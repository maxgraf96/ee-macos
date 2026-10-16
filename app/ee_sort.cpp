// ee_sort.cpp - Empire Earth's sort by angle, kept inside its array.
//
// The executable's std::sort instantiation at 0x63a355 orders pointers to
// objects by their angle around a centre object, for a geometry routine of
// the simulation (0x630466, reached from an object's virtual method). The
// angle is 0x62d5a4: the engine's UMArcTan2 of the two positions' difference,
// wrapped into [0, 2*pi), returned as a float in ST(0). The comparator is
// `angle(a) < angle(b)`, and as compiled the first call's result is spilled
// to a float while the second stays in ST(0) at the thread's 53-bit
// precision:
//
//     less(a, b) = angle(b) > (double)(float)angle(a)
//
// That is not irreflexive: when angle(a) rounds down to float, a < a, and
// two objects at the same angle each compare less than the other. MSVC's
// introsort has unguarded loops that rely on the pivot, or the first
// element, to stop them: the partition's two scans and the insertion of each
// element after the first 16. With such ties they run off the array, read
// whatever lies beyond it as an object pointer and dereference it. On
// 2026-10-16 that ended a 29-minute single-player game: a guest memory fault
// in the partition (0x63b8d2) on the simulation thread, 64553b5a + 0x20.
//
// The arithmetic is Windows' bit for bit (docs/decisions/002-exact-x87.md),
// so Windows takes the same path, and what saves or crashes it is whatever
// happens to lie next to the array in its heap. The hook therefore leaves
// every sort that stays inside its array to the original:
//
// 1. it computes each element's angle once, with the game's own function,
//    which only reads the two positions (UMArcTan2 is pure);
// 2. it replays the algorithm on indices (app/ee_sort_sim.h) and notes
//    whether any loop would read outside the array;
// 3. if none would, the original translation runs untouched: the result is
//    the game's own, and a lockstep game with Windows players stays in step;
// 4. if one would, it writes the elements ordered by their float angle (ties
//    kept in order) and returns, and says so in the log.
//
// A sort it cannot replay - a heap-sort fallback, an angle not exactly a
// double, a rounding mode other than nearest - runs the original as before.
//
// RECOMP_EE_SORT_CHECK=1 also compares each original result with the
// replay's prediction and prints the counts when the process exits: the
// replay is trusted only to predict, and this is how that is checked.
//
// RECOMP_EE_SORT_SELFTEST=1 (implies the check): once, at the simulation
// thread's first world update (0x547aad), the sort is called through its
// entry on objects made for the test, around a centre at (1000, 1000):
//   - 40 at distinct angles: the original runs and orders them;
//   - 40 stacked at one point whose angle rounds down to float: the original
//     would run off the array, so the hook sorts them inside it and keeps
//     their order;
//   - 12 of those: at 16 or fewer the original's insertion sort is guarded,
//     and it runs.
// Each case prints "[ee] sort self-test: ... ok" or "FAILED".
#include "ee_sort_sim.h"

#include "host/boot.h"
#include "runtime/guest.h"
#include "runtime/memory.h"
#include "runtime/x86.h"

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace {

constexpr uint32_t kSort = 0x0063a355;  // std::sort(first, last, by_angle), cdecl
constexpr uint32_t kAngle = 0x0062d5a4; // float angle(centre, object), cdecl
constexpr uint32_t kMaxElements = 1u << 16;
constexpr uint32_t kWorldUpdate = 0x00547aad; // the self-test's moment: every object's update

int32_t g_index = -1;
bool g_check = false;
std::atomic<uint64_t> g_sorts{0}, g_kept{0}, g_heap{0}, g_unreplayed{0}, g_checked{0}, g_differ{0};

// What a guest call may change in the registers: the hook puts them back, so
// the original starts from exactly the state it was called with.
struct Saved {
    uint32_t r[8];
    uint32_t cf, zf, sf, of, pf, af, df, misc;
    double st[8];
    X87Ext stx[8];
    uint8_t st_kind[8];
    uint8_t ext_live;
    uint32_t fpu_top;
    uint16_t fpu_cw, fpu_sw, fpu_tag;

    explicit Saved(const X86 *c) {
        memcpy(r, c->r, sizeof r);
        cf = c->eflags_cf, zf = c->eflags_zf, sf = c->eflags_sf, of = c->eflags_of;
        pf = c->eflags_pf, af = c->eflags_af, df = c->eflags_df, misc = c->eflags_misc;
        memcpy(st, c->st, sizeof st);
        memcpy(stx, c->stx, sizeof stx);
        memcpy(st_kind, c->st_kind, sizeof st_kind);
        ext_live = c->ext_live, fpu_top = c->fpu_top;
        fpu_cw = c->fpu_cw, fpu_sw = c->fpu_sw, fpu_tag = c->fpu_tag;
    }
    void restore(X86 *c) const {
        memcpy(c->r, r, sizeof r);
        c->eflags_cf = cf, c->eflags_zf = zf, c->eflags_sf = sf, c->eflags_of = of;
        c->eflags_pf = pf, c->eflags_af = af, c->eflags_df = df, c->eflags_misc = misc;
        memcpy(c->st, st, sizeof st);
        memcpy(c->stx, stx, sizeof stx);
        memcpy(c->st_kind, st_kind, sizeof st_kind);
        c->ext_live = ext_live, c->fpu_top = fpu_top;
        c->fpu_cw = fpu_cw, c->fpu_sw = fpu_sw, c->fpu_tag = fpu_tag;
    }
};

void run_original(X86 *c) {
    recomp_base_ptrs[g_index](c);
}

// [ESP] the return address, then first, last and the comparator (the centre
// object's pointer, by value).
void sort_hook(X86 *c, uint32_t) {
    g_sorts.fetch_add(1, std::memory_order_relaxed);
    const uint32_t esp = c->r[R_ESP];
    const uint32_t first = rd32(esp + 4), last = rd32(esp + 8), centre = rd32(esp + 12);
    const uint32_t bytes = last - first;
    if (last < first || bytes % 4 || bytes / 4 > kMaxElements || (bytes && !gm_valid(first, bytes)) ||
        (c->fpu_cw & 0x0c00u)) {
        g_unreplayed.fetch_add(1, std::memory_order_relaxed);
        return run_original(c);
    }
    const size_t n = bytes / 4;
    std::vector<uint32_t> element(n);
    std::vector<double> angle(n);
    for (size_t i = 0; i < n; ++i)
        element[i] = rd32(first + 4 * uint32_t(i));
    {
        const Saved saved(c);
        bool plain = true;
        for (size_t i = 0; i < n && plain; ++i) {
            guest_call(c, kAngle, centre, element[i]);
            plain = x87_st0_plain(c);
            angle[i] = c->st[c->fpu_top & 7u];
            saved.restore(c);
        }
        if (!plain) {
            g_unreplayed.fetch_add(1, std::memory_order_relaxed);
            return run_original(c);
        }
    }

    ee_sort::SortSimulation replay(angle);
    switch (replay.run()) {
    case ee_sort::SortSimulation::kInBounds:
        run_original(c);
        if (g_check) {
            bool same = true;
            for (size_t i = 0; i < n && same; ++i)
                same = rd32(first + 4 * uint32_t(i)) == element[size_t(replay.order()[i])];
            g_checked.fetch_add(1, std::memory_order_relaxed);
            if (!same && g_differ.fetch_add(1, std::memory_order_relaxed) < 5)
                fprintf(stderr, "[ee] sort: the replay of a %zu-element sort predicted another order\n", n);
        }
        return;
    case ee_sort::SortSimulation::kHeap:
        g_heap.fetch_add(1, std::memory_order_relaxed);
        return run_original(c);
    case ee_sort::SortSimulation::kEscapes:
        break;
    }

    // The original would read outside the array: the elements by their float
    // angle instead, then the RET (cdecl: the caller pops the arguments).
    const std::vector<int> order = ee_sort::bounded_order(angle);
    for (size_t i = 0; i < n; ++i)
        wr32(first + 4 * uint32_t(i), element[size_t(order[i])]);
    const uint64_t kept = g_kept.fetch_add(1, std::memory_order_relaxed);
    if (kept < 5 || (kept & (kept + 1)) == 0) {
        fprintf(stderr,
                "[ee] sort: a %zu-element sort by angle (called from %08x) would have run off its "
                "array; sorted inside it (%llu so far)\n",
                n, rd32(esp), (unsigned long long)kept + 1);
        fflush(stderr);
    }
    c->eip = rd32(esp);
    c->r[R_ESP] = esp + 4;
}

// ------------------------------------------------------------- self-test --

std::atomic<bool> g_selftest_ran{false};

uint32_t make_object(int32_t x, int32_t y) {
    const uint32_t o = heap_alloc(64, true);
    wr32(o + 0x1c, uint32_t(x));
    wr32(o + 0x20, uint32_t(y));
    return o;
}

double angle_of(X86 *c, uint32_t centre, uint32_t object) {
    const Saved saved(c);
    guest_call(c, kAngle, centre, object);
    const double a = c->st[c->fpu_top & 7u];
    saved.restore(c);
    return a;
}

bool sort_case(X86 *c, const char *name, uint32_t centre, const std::vector<uint32_t> &objects,
               bool kept_inside) {
    const uint32_t n = uint32_t(objects.size());
    const uint32_t array = heap_alloc(4 * n + 4);
    for (uint32_t i = 0; i < n; ++i)
        wr32(array + 4 * i, objects[i]);
    const uint64_t sorts = g_sorts, kept = g_kept, differ = g_differ;
    {
        const Saved saved(c);
        guest_call(c, kSort, array, array + 4 * n, centre);
        saved.restore(c);
    }
    std::vector<uint32_t> out(n);
    for (uint32_t i = 0; i < n; ++i)
        out[i] = rd32(array + 4 * i);
    heap_free(array);
    bool ok = g_sorts == sorts + 1 && (g_kept == kept + 1) == kept_inside && g_differ == differ;
    std::vector<uint32_t> a = objects, b = out;
    std::sort(a.begin(), a.end());
    std::sort(b.begin(), b.end());
    ok = ok && a == b;
    if (kept_inside)
        ok = ok && out == objects; // all at one angle: their order kept
    else if (n > ee_sort::kSortMax)
        for (uint32_t i = 1; i < n && ok; ++i)
            ok = float(angle_of(c, centre, out[i - 1])) <= float(angle_of(c, centre, out[i]));
    fprintf(stderr, "[ee] sort self-test: %s (%u objects): %s\n", name, n, ok ? "ok" : "FAILED");
    return ok;
}

void selftest(X86 *c) {
    const int32_t cx = 1000, cy = 1000;
    const uint32_t centre = make_object(cx, cy);
    std::vector<uint32_t> spread;
    for (int32_t i = 1; i <= 40; ++i)
        spread.push_back(make_object(cx + (i * 7919) % 201 - 100, cy + (i * 104729) % 199 - 99 + (i % 2)));
    // a point whose angle rounds down to float: each of its copies compares
    // less than every other
    uint32_t probe = make_object(0, 0);
    int32_t px = 0, py = 0;
    for (int32_t dy = -1; dy >= -60 && !px; --dy)
        for (int32_t dx = 1; dx <= 60; ++dx) {
            wr32(probe + 0x1c, uint32_t(cx + dx));
            wr32(probe + 0x20, uint32_t(cy + dy));
            const double a = angle_of(c, centre, probe);
            if (double(float(a)) < a) {
                px = cx + dx, py = cy + dy;
                break;
            }
        }
    heap_free(probe);
    bool ok = sort_case(c, "distinct angles", centre, spread, false);
    if (!px) {
        fprintf(stderr, "[ee] sort self-test: no point whose angle rounds down: FAILED\n");
        ok = false;
    } else {
        std::vector<uint32_t> stacked;
        for (int i = 0; i < 40; ++i)
            stacked.push_back(make_object(px, py));
        ok = sort_case(c, "stacked, angle rounding down", centre, stacked, true) && ok;
        stacked.resize(12);
        ok = sort_case(c, "12 stacked", centre, stacked, false) && ok;
        for (uint32_t o : stacked)
            heap_free(o);
    }
    for (uint32_t o : spread)
        heap_free(o);
    heap_free(centre);
    fprintf(stderr, "[ee] sort self-test: %s\n", ok ? "passed" : "FAILED");
    fflush(stderr);
}

void world_update_hook(X86 *c, uint32_t index) {
    if (!g_selftest_ran.exchange(true)) {
        selftest(c);
        __atomic_store_n(&recomp_hooked[index], (uint8_t)0, __ATOMIC_RELEASE);
    }
    recomp_base_ptrs[index](c);
}

void report() {
    fprintf(stderr,
            "[ee] sort: %llu sorts by angle, %llu kept inside their array, %llu heap sorts, %llu not "
            "replayed; %llu checked against the original, %llu different\n",
            (unsigned long long)g_sorts.load(), (unsigned long long)g_kept.load(),
            (unsigned long long)g_heap.load(), (unsigned long long)g_unreplayed.load(),
            (unsigned long long)g_checked.load(), (unsigned long long)g_differ.load());
}

// The self-test's moment, with or without the hook (RECOMP_EE_ORIGINAL_SORT=1
// shows what the original does with the stacked objects).
void install_selftest() {
    const char *test = getenv("RECOMP_EE_SORT_SELFTEST");
    if (!test || !*test || strcmp(test, "0") == 0)
        return;
    const int32_t update = recomp_index_of(kWorldUpdate);
    if (update < 0 || __atomic_load_n(&recomp_hooked[update], __ATOMIC_ACQUIRE)) {
        fprintf(stderr, "[ee] sort self-test: no slot at %08x: FAILED\n", kWorldUpdate);
        return;
    }
    __atomic_store_n(&recomp_hook_ptrs[update], (RecompHookFn)world_update_hook, __ATOMIC_RELEASE);
    __atomic_store_n(&recomp_hooked[update], (uint8_t)1, __ATOMIC_RELEASE);
}

void install() {
    const char *off = getenv("RECOMP_EE_ORIGINAL_SORT");
    if (off && *off && strcmp(off, "0") != 0) {
        printf("[ee] sort: the sort by angle (%08x) runs as the original\n", kSort);
        install_selftest();
        return;
    }
    const int32_t index = recomp_index_of(kSort);
    if (index < 0) {
        fprintf(stderr, "[ee] sort: no translated %08x; the sort by angle runs as the original\n", kSort);
        return;
    }
    if (__atomic_load_n(&recomp_hooked[index], __ATOMIC_ACQUIRE)) {
        fprintf(stderr, "[ee] sort: %08x's slot is taken; the sort by angle runs as the original\n", kSort);
        return;
    }
    const char *check = getenv("RECOMP_EE_SORT_CHECK");
    const char *test = getenv("RECOMP_EE_SORT_SELFTEST");
    const bool selftest = test && *test && strcmp(test, "0") != 0;
    g_check = selftest || (check && *check && strcmp(check, "0") != 0);
    if (g_check)
        atexit(report);
    g_index = index;
    __atomic_store_n(&recomp_hook_ptrs[index], (RecompHookFn)sort_hook, __ATOMIC_RELEASE);
    __atomic_store_n(&recomp_hooked[index], (uint8_t)1, __ATOMIC_RELEASE);
    printf("[ee] sort: the sort by angle (%08x) is kept inside its array%s\n", kSort,
           g_check ? "; each result is checked against the replay" : "");
    install_selftest();
}

__attribute__((constructor)) void ee_sort_init() {
    boot_add_load_step(install);
}

} // namespace
