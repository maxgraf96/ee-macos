// rmg_record_mac.cpp - a capture made on the Mac: one EERMPIMSetRandomMapValues
// call through the translated modules, on a synthetic context, recorded in
// rmg_capture.js's format (ee-rmg-capture/1, docs/reference/rmg.md section 6).
// rmg_pack.py packs it and the Mac replay replays it, which tests the packer,
// the replay and the comparison end to end with no Windows involved
// (tests/m1/replay/run_replay.py; docs/reference/m1-replay.md).
//
//   ee_m1_replay --record OUT [--rmv NAME] [--scripts DIR] [--heap-at ADDR]
//                [--seed N] [--size-class K] [--players N] [--epoch E] [--case NAME]
//
// What it records is what the Frida capture records, by the same rules:
//   - a heap index of every block the modules get from msvcrt's allocators,
//     from load on (address -> size);
//   - at entry, the roots (the 0x3d0-byte context, Default.dll's .data, the
//     UString argument) and every live block a dword of a root or of a block
//     already taken points into, recursively; the stack window
//     [ESP-0x8000, ESP+0x200), restored but not scanned; the x87 words;
//   - during the call, every allocator call with its result and the new
//     block's first contents, and fopen/fgets/fclose with their data;
//   - at exit, EAX, the x87 words and the same closure without the stack.
// The context is the one a game would build only in part: the two random
// generators, the scripts' directory, map size class, starting epoch, player
// count and grid dimensions (rmg.md section 4.2); every list is empty.
#include "rmg_mac.h"

#include "runtime/memory.h"
#include "runtime/win32.h"

#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <strings.h>
#include <map>
#include <set>
#include <string>
#include <sys/stat.h>
#include <vector>

namespace {

// ---- SHA-256 (FIPS 180-4), for the content-addressed blobs -------------------------------------

struct Sha256 {
    uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                     0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    uint8_t buf[64];
    uint64_t len = 0;
    static uint32_t ror(uint32_t x, int n) {
        return (x >> n) | (x << (32 - n));
    }
    void block(const uint8_t *p) {
        static const uint32_t k[64] = {
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
            0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
            0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
            0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
            0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
            0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
            0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
            0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
            0xc67178f2};
        uint32_t w[64];
        for (int i = 0; i < 16; ++i)
            w[i] = (uint32_t)p[4 * i] << 24 | (uint32_t)p[4 * i + 1] << 16 |
                   (uint32_t)p[4 * i + 2] << 8 | p[4 * i + 3];
        for (int i = 16; i < 64; ++i) {
            uint32_t s0 = ror(w[i - 15], 7) ^ ror(w[i - 15], 18) ^ (w[i - 15] >> 3);
            uint32_t s1 = ror(w[i - 2], 17) ^ ror(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
        for (int i = 0; i < 64; ++i) {
            uint32_t t1 =
                hh + (ror(e, 6) ^ ror(e, 11) ^ ror(e, 25)) + ((e & f) ^ (~e & g)) + k[i] + w[i];
            uint32_t t2 = (ror(a, 2) ^ ror(a, 13) ^ ror(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
            hh = g, g = f, f = e, e = d + t1, d = c, c = b, b = a, a = t1 + t2;
        }
        h[0] += a, h[1] += b, h[2] += c, h[3] += d, h[4] += e, h[5] += f, h[6] += g, h[7] += hh;
    }
    void update(const uint8_t *p, size_t n) {
        while (n) {
            size_t used = len % 64, take = std::min<size_t>(64 - used, n);
            memcpy(buf + used, p, take);
            len += take, p += take, n -= take;
            if (len % 64 == 0)
                block(buf);
        }
    }
    std::string hex() {
        uint64_t bits = len * 8;
        uint8_t pad = 0x80, zero = 0;
        update(&pad, 1);
        while (len % 64 != 56)
            update(&zero, 1);
        for (int i = 7; i >= 0; --i) {
            uint8_t b = (uint8_t)(bits >> (8 * i));
            update(&b, 1);
        }
        char out[65];
        for (int i = 0; i < 8; ++i)
            snprintf(out + 8 * i, 9, "%08x", h[i]);
        return out;
    }
};

// ---- output: blobs and JSON --------------------------------------------------------------------

const uint32_t kChunk = 4096;
std::string g_out;
std::set<std::string> g_blobs;

void make_dirs(const std::string &path) {
    for (size_t i = 1; i <= path.size(); ++i)
        if (i == path.size() || path[i] == '/')
            mkdir(path.substr(0, i).c_str(), 0755);
}

std::string store(const uint8_t *p, size_t n) {
    Sha256 s;
    s.update(p, n);
    std::string h = s.hex();
    if (g_blobs.insert(h).second) {
        std::string dir = g_out + "/blobs/" + h.substr(0, 2);
        make_dirs(dir);
        if (FILE *f = fopen((dir + "/" + h + ".bin").c_str(), "wb")) {
            fwrite(p, 1, n, f);
            fclose(f);
        }
    }
    return h;
}

// Guest bytes as a JSON list of chunk hashes.
std::string chunks(uint32_t addr, uint32_t size) {
    std::string out = "[";
    for (uint32_t off = 0; off < size; off += kChunk) {
        uint32_t n = std::min(kChunk, size - off);
        out += (off ? ", \"" : "\"") + store(g_mem + addr + off, n) + "\"";
    }
    return out + "]";
}

std::string hx(uint32_t v) {
    char b[16];
    snprintf(b, sizeof b, "0x%x", v);
    return b;
}

std::string jstr(const std::string &s) {
    std::string o = "\"";
    for (unsigned char ch : s) {
        if (ch == '"' || ch == '\\')
            o += '\\', o += (char)ch;
        else if (ch < 0x20 || ch >= 0x7f) {
            char b[8];
            snprintf(b, sizeof b, "\\u%04x", ch);
            o += b;
        } else
            o += (char)ch;
    }
    return o + "\"";
}

std::string hex_bytes(const uint8_t *p, size_t n) {
    std::string o;
    char b[3];
    for (size_t i = 0; i < n; ++i) {
        snprintf(b, sizeof b, "%02x", p[i]);
        o += b;
    }
    return o;
}

// ---- the heap index and the recording overrides -------------------------------------------------

std::map<uint32_t, uint32_t> g_live; // address -> size, every live block from msvcrt's allocators
bool g_recording = false;
std::vector<std::string> g_allocs, g_files, g_debug, g_printf, g_warnings;

void (*o_new)(X86 *), (*o_malloc)(X86 *), (*o_calloc)(X86 *), (*o_realloc)(X86 *),
    (*o_delete)(X86 *), (*o_free)(X86 *), (*o_fopen)(X86 *), (*o_fgets)(X86 *), (*o_fclose)(X86 *),
    (*o_ods)(X86 *), (*o_printf)(X86 *);

// An allocation as rmg_capture.js records it: arguments, result, size and,
// for a new block, its contents as the allocator handed it over.
void allocated(const char *op, uint32_t a, uint32_t b, uint32_t ret, uint32_t size) {
    if (ret)
        g_live[ret] = size;
    if (!g_recording)
        return;
    std::string ev = "{\"op\": \"" + std::string(op) + "\", \"a\": \"" + hx(a) + "\", \"b\": \"" +
                     hx(b) + "\", \"ret\": \"" + hx(ret) + "\", \"size\": " + std::to_string(size) +
                     ", \"caller\": \"mac\"";
    if (ret && size)
        ev += ", \"chunks\": " + chunks(ret, size);
    g_allocs.push_back(ev + "}");
}
void freed(const char *op, uint32_t p) {
    g_live.erase(p);
    if (g_recording)
        g_allocs.push_back("{\"op\": \"" + std::string(op) + "\", \"a\": \"" + hx(p) +
                           "\", \"caller\": \"mac\"}");
}

// --heap-at ADDR: once the DllMains have run, the modules' blocks come from a
// heap of the recorder's own at ADDR (above guest_size, say, where Windows
// puts a big game's heap) instead of the kit's, so a replay has to open
// memory there (mem_open) and the kit's string functions have to read it. A
// bump allocator: freed blocks are not reused, and every block starts out
// filled with 0xcd.
uint32_t g_far_base = 0, g_far_next = 0;
const uint32_t kFarSpan = 0x04000000u;

bool far_heap() {
    return g_far_base != 0;
}
bool far_owns(uint32_t p) {
    return p >= g_far_base && p < g_far_base + kFarSpan;
}
uint32_t far_alloc(uint32_t size, bool zero) {
    const uint32_t a = (g_far_next + 15u) & ~15u;
    if (size > kFarSpan || a + size > g_far_base + kFarSpan)
        return 0;
    g_far_next = a + std::max<uint32_t>(size, 1);
    memset(g_mem + a, zero ? 0 : 0xcd, size);
    return a;
}

void rec_new(X86 *c) {
    uint32_t n = arg(c, 0);
    if (far_heap())
        set_eax(c, far_alloc(n, false));
    else
        rmg_pass(c, o_new);
    allocated("new", n, 0, c->r[R_EAX], n);
}
void rec_malloc(X86 *c) {
    uint32_t n = arg(c, 0);
    if (far_heap())
        set_eax(c, far_alloc(n, false));
    else
        rmg_pass(c, o_malloc);
    allocated("malloc", n, 0, c->r[R_EAX], n);
}
void rec_calloc(X86 *c) {
    uint32_t n = arg(c, 0), s = arg(c, 1);
    if (far_heap())
        set_eax(c, far_alloc(n * s, true));
    else
        rmg_pass(c, o_calloc);
    allocated("calloc", n, s, c->r[R_EAX], n * s);
}
void rec_realloc(X86 *c) {
    uint32_t p = arg(c, 0), n = arg(c, 1);
    if (far_heap() && (!p || far_owns(p))) {
        uint32_t r = 0;
        if (n) {
            r = far_alloc(n, false);
            auto it = g_live.find(p);
            if (r && p && it != g_live.end())
                memcpy(g_mem + r, g_mem + p, std::min(it->second, n));
        }
        set_eax(c, r);
    } else {
        rmg_pass(c, o_realloc);
    }
    uint32_t r = c->r[R_EAX];
    if (p && (r || n == 0))
        g_live.erase(p);
    allocated("realloc", p, n, r, n);
}
void rec_delete(X86 *c) {
    uint32_t p = arg(c, 0);
    freed("delete", p);
    if (!far_owns(p))
        rmg_pass(c, o_delete);
}
void rec_free(X86 *c) {
    uint32_t p = arg(c, 0);
    freed("free", p);
    if (!far_owns(p))
        rmg_pass(c, o_free);
}
void rec_fopen(X86 *c) {
    const std::string path = gm_str(arg(c, 0), 1024), mode = gm_str(arg(c, 1), 32);
    rmg_pass(c, o_fopen);
    if (g_recording)
        g_files.push_back("{\"op\": \"fopen\", \"path\": " + jstr(path) + ", \"path_hex\": \"" +
                          hex_bytes((const uint8_t *)path.data(), path.size()) +
                          "\", \"mode\": " + jstr(mode) + ", \"ret\": \"" + hx(c->r[R_EAX]) +
                          "\", \"caller\": \"mac\"}");
}
void rec_fgets(X86 *c) {
    const uint32_t buf = arg(c, 0), fp = arg(c, 2);
    const int32_t n = (int32_t)arg(c, 1);
    rmg_pass(c, o_fgets);
    if (!g_recording)
        return;
    std::string ev = "{\"op\": \"fgets\", \"fp\": \"" + hx(fp) + "\", \"n\": " + std::to_string(n) +
                     ", \"ret\": \"" + hx(c->r[R_EAX]) + "\"";
    if (c->r[R_EAX] && n > 0) {
        uint32_t len = 0;
        while ((int32_t)len < n - 1 && g_mem[buf + len])
            ++len;
        ev += ", \"data_hex\": \"" + hex_bytes(g_mem + buf, len + 1) + "\"";
    }
    g_files.push_back(ev + "}");
}
void rec_fclose(X86 *c) {
    const uint32_t fp = arg(c, 0);
    rmg_pass(c, o_fclose);
    if (g_recording)
        g_files.push_back("{\"op\": \"fclose\", \"fp\": \"" + hx(fp) + "\", \"ret\": \"" +
                          hx(c->r[R_EAX]) + "\"}");
}
void rec_ods(X86 *c) {
    if (g_recording)
        g_debug.push_back(jstr(gm_str(arg(c, 0), 1024)));
    else
        rmg_pass(c, o_ods);
}
void rec_printf(X86 *c) {
    if (g_recording)
        g_printf.push_back(jstr(gm_str(arg(c, 0), 1024)));
    rmg_pass(c, o_printf);
}

void install_record_overrides() {
    // new[] and delete[] are imported by none of the three modules.
    const RmgOverride v[] = {
        {"msvcrt.dll", "??2@YAPAXI@Z", rec_new, &o_new},
        {"msvcrt.dll", "malloc", rec_malloc, &o_malloc},
        {"msvcrt.dll", "calloc", rec_calloc, &o_calloc},
        {"msvcrt.dll", "realloc", rec_realloc, &o_realloc},
        {"msvcrt.dll", "??3@YAXPAX@Z", rec_delete, &o_delete},
        {"msvcrt.dll", "free", rec_free, &o_free},
        {"msvcrt.dll", "fopen", rec_fopen, &o_fopen},
        {"msvcrt.dll", "fgets", rec_fgets, &o_fgets},
        {"msvcrt.dll", "fclose", rec_fclose, &o_fclose},
        {"msvcrt.dll", "printf", rec_printf, &o_printf},
        {"kernel32.dll", "OutputDebugStringA", rec_ods, &o_ods},
    };
    rmg_install_overrides(v, sizeof v / sizeof v[0]);
}

// ---- the closure --------------------------------------------------------------------------------

struct Region {
    std::string kind, module, from;
    uint32_t addr, size;
    bool scan, restore;
    bool has_via = false;
    uint32_t via = 0;
};

struct Captured {
    std::vector<Region> regions;
    uint64_t bytes = 0;
    uint32_t foreign = 0;
    std::vector<std::string> foreign_sample;
};

// rmg_capture.js's captureState: every aligned dword of a scanned region that
// points into a live block (zero-sized blocks count one byte) pulls in the
// whole block, scanned in turn. A value inside the kit's heap that no live
// block holds is "foreign": counted and sampled, never followed.
Captured capture_state(const std::vector<Region> &roots) {
    Captured out;
    std::vector<std::pair<uint32_t, uint32_t>> idx; // [start, end), overlaps dropped
    for (const auto &kv : g_live) {
        uint32_t e = kv.first + std::max<uint32_t>(1, kv.second);
        if (!idx.empty() && kv.first < idx.back().second)
            continue;
        idx.push_back({kv.first, e});
    }
    std::set<uint32_t> seen;
    std::vector<size_t> work;
    auto add = [&](const Region &r) {
        out.regions.push_back(r);
        if (r.scan)
            work.push_back(out.regions.size() - 1);
    };
    for (const Region &r : roots)
        add(r);
    while (!work.empty()) {
        const Region r = out.regions[work.back()];
        work.pop_back();
        for (uint32_t off = 0; off + 4 <= r.size; off += 4) {
            const uint32_t v = rd32(r.addr + off);
            auto it = std::upper_bound(idx.begin(), idx.end(), std::make_pair(v, 0xffffffffu));
            if (it != idx.begin() && v < std::prev(it)->second) {
                --it;
                if (seen.insert(it->first).second) {
                    Region b;
                    b.kind = "heap";
                    b.addr = it->first;
                    b.size = it->second - it->first;
                    b.scan = b.restore = true;
                    b.has_via = true;
                    b.via = r.addr + off;
                    b.from = r.kind;
                    add(b);
                }
            } else if (v >= HEAP_BASE && v < HEAP_LIMIT) {
                ++out.foreign;
                if (out.foreign_sample.size() < 64)
                    out.foreign_sample.push_back("[\"" + hx(r.addr + off) + "\", \"" + hx(v) +
                                                 "\", \"" + r.kind + "\"]");
            }
        }
    }
    for (const Region &r : out.regions)
        out.bytes += r.size;
    return out;
}

std::string regions_json(const Captured &c) {
    std::string o = "[";
    for (size_t i = 0; i < c.regions.size(); ++i) {
        const Region &r = c.regions[i];
        o += i ? ",\n    " : "\n    ";
        o += "{\"kind\": \"" + r.kind + "\", \"addr\": \"" + hx(r.addr) +
             "\", \"size\": " + std::to_string(r.size) +
             ", \"restore\": " + (r.restore ? "true" : "false") +
             ", \"scan\": " + (r.scan ? "true" : "false");
        if (!r.module.empty())
            o += ", \"module\": " + jstr(r.module);
        if (r.has_via)
            o += ", \"via\": \"" + hx(r.via) + "\", \"from\": \"" + r.from + "\"";
        o += ", \"chunks\": " + chunks(r.addr, r.size) + "}";
    }
    o += "]";
    std::string s = "\"regions\": " + o + ", \"bytes\": " + std::to_string(c.bytes) +
                    ", \"tracked_blocks\": " + std::to_string(g_live.size()) +
                    ", \"foreign\": {\"count\": " + std::to_string(c.foreign) + ", \"sample\": [";
    for (size_t i = 0; i < c.foreign_sample.size(); ++i)
        s += (i ? ", " : "") + c.foreign_sample[i];
    return s + "]}";
}

std::string fpu_json(const X86 *c) {
    return "{\"cw\": \"" + hx(c->fpu_cw) + "\", \"sw\": \"" + hx(c->fpu_sw) + "\", \"tw\": \"" +
           hx(c->fpu_tag) + "\"}";
}

std::string summary_json(uint32_t ctx) {
    char b[512];
    snprintf(b, sizeof b,
             "{\"rng0\": [\"0x%x\", \"0x%x\"], \"seed\": %u, \"map_size_class\": %u, "
             "\"start_epoch\": %u, \"player_count_minus_1\": %u, \"dims\": [%u, %u, %u, %u], "
             "\"filter\": %u, \"shallows\": %u}",
             rd32(ctx), rd32(ctx + 0x10), rd32(ctx + 0x3b0), rd32(ctx + 0x250), rd32(ctx + 0x254),
             rd32(ctx + 0x258), rd32(ctx + 0x37c), rd32(ctx + 0x380), rd32(ctx + 0x384),
             rd32(ctx + 0x388), rd8(ctx + 0x3cd), rd8(ctx + 0x3cf));
    return b;
}

// ---- the synthetic context and the call ---------------------------------------------------------

const uint32_t kCtxSize = 0x3d0;

struct Options {
    const char *out = nullptr, *script = "Continental", *case_name = "mac-roundtrip";
    const char *scripts = nullptr; // a host directory of scripts, seen as kScriptsGuest
    uint32_t seed = 1037, size_class = 1, players = 2, epoch = 0;
};

// --scripts DIR: the guest reads its scripts from C:\Empire Earth\MacRoundTrip\,
// which this resolver maps onto DIR (the kit's file seam, runtime/win32.h).
// The real scripts stay where they are; nothing is written to the game.
const char *const kScriptsGuest = "C:\\Empire Earth\\MacRoundTrip\\";
std::string g_scripts_host;
int resolve_scripts(const char *relative, int op, char *out, size_t out_len) {
    static const char prefix[] = "macroundtrip/";
    if (op != WIN32_FILE_READ || strncasecmp(relative, prefix, sizeof prefix - 1))
        return 0;
    snprintf(out, out_len, "%s/%s", g_scripts_host.c_str(), relative + sizeof prefix - 1);
    return 1;
}

// The world widths behind each $MapSize class (rmg.md 8.1: Tiny 42 ... Gigantic 220 tiles).
const uint32_t kTiles[6] = {42, 58, 75, 105, 160, 220};

int record(const Options &o, LoaderModule *mods[3]) {
    X86 *c = loader_context();
    LoaderModule &engine = *mods[1], &def = *mods[2];
    auto exp = [&](LoaderModule &m, const char *name) {
        uint32_t a = loader_module_export(m, name);
        if (!a)
            fprintf(stderr, "record: %s has no export %s\n", m.name.c_str(), name);
        return a;
    };
    const uint32_t ustring_ctor = exp(engine, "??0UString@@QAE@PBD@Z"),
                   spec_ctor = exp(engine, "??0FSFileSpec@@QAE@ABVUString@@@Z"),
                   random_ctor = exp(engine, "??0URandom@@QAE@K@Z"),
                   integers_ctor = exp(engine, "??0URandomIntegers@@QAE@XZ"),
                   srmv = exp(def, "EERMPIMSetRandomMapValues");
    if (!ustring_ctor || !spec_ctor || !random_ctor || !integers_ctor || !srmv)
        return 2;
    // Host-side helpers for the set-up: tracked like the guest's own blocks.
    auto block = [&](uint32_t size) {
        uint32_t a = far_heap() ? far_alloc(size, true) : heap_alloc(size, true);
        g_live[a] = size;
        return a;
    };
    auto ustring_at = [&](uint32_t obj, const char *text) {
        uint32_t s = block((uint32_t)strlen(text) + 1);
        gm_put_str(s, text, (uint32_t)strlen(text) + 1);
        c->r[R_ECX] = obj;
        guest_call(c, ustring_ctor, s);
    };
    // EERandomMap is 0x410 bytes of heap with the context at +4 (rmg.md 4.1).
    const uint32_t object = block(0x410), ctx = object + 4;
    c->r[R_ECX] = ctx;
    guest_call(c, integers_ctor);
    wr32(ctx, o.seed); // the EXE writes the seed over the state
    c->r[R_ECX] = ctx + 0x10;
    guest_call(c, random_ctor, o.seed);
    const uint32_t dir = block(16);
    if (o.scripts) {
        g_scripts_host = o.scripts;
        win32_set_file_ops(resolve_scripts, nullptr);
    }
    ustring_at(dir, o.scripts ? kScriptsGuest : "C:\\Empire Earth\\Data\\Random Map Scripts\\");
    c->r[R_ECX] = ctx + 0x20;
    guest_call(c, spec_ctor, dir);
    const uint32_t tiles = kTiles[o.size_class < 6 ? o.size_class : 1];
    wr32(ctx + 0x250, o.size_class);
    wr32(ctx + 0x254, o.epoch);
    wr32(ctx + 0x258, o.players ? o.players - 1 : 0);
    wr32(ctx + 0x37c, 2 * tiles);
    wr32(ctx + 0x384, 2 * tiles);
    wr32(ctx + 0x3b0, o.seed);

    // The call's frame, below the loader's stack pointer as the EXE's would be:
    // its local UString (the script name) above the arguments.
    const uint32_t frame = (c->r[R_ESP] - 0x1000u) & ~0xfu;
    const uint32_t name = frame + 0x40, esp = frame - 0x20;
    ustring_at(name, o.script);
    wr32(esp + 4, ctx);
    wr32(esp + 8, name);
    wr32(esp, GUEST_RETURN_SENTINEL); // [esp]: the caller's return address

    uint32_t data_rva = 0, data_size = 0, eng_rva = 0, eng_size = 0;
    rmg_data_section(def, &data_rva, &data_size);
    rmg_data_section(engine, &eng_rva, &eng_size);
    std::vector<Region> roots = {
        {"ctx", "", "", ctx, kCtxSize, true, true},
        {"module_data", "Default.dll", "", def.base + data_rva, data_size, true, true},
        {"arg", "", "", name, 16, true, true},
    };
    std::vector<Region> entry_roots = roots;
    entry_roots.push_back({"stack", "", "", esp - 0x8000u, 0x8000u + 0x200u, false, true});
    entry_roots.push_back({"module_data_diag", "Low-Level Engine.dll", "", engine.base + eng_rva,
                           eng_size, false, false});

    const uint16_t cw_in = c->fpu_cw;
    const std::string fpu_in = fpu_json(c), sum_in = summary_json(ctx);
    const Captured in = capture_state(entry_roots);
    const std::string in_json = regions_json(in);

    g_recording = true;
    uint16_t cw_out = 0;
    bool exited = false;
    const uint32_t eax = rmg_call_at(c, srmv, esp, cw_in, &cw_out, &exited);
    g_recording = false;
    if (exited) {
        fprintf(stderr, "record: the guest ended the process during the call\n");
        return 2;
    }
    const Captured out = capture_state(roots);
    printf("record: EERMPIMSetRandomMapValues(ctx %08x, \"%s\") -> %u; %zu regions in (%llu "
           "bytes), %zu allocator calls, %zu file calls, %zu regions out (%llu bytes)\n",
           ctx, o.script, eax, in.regions.size(), (unsigned long long)in.bytes, g_allocs.size(),
           g_files.size(), out.regions.size(), (unsigned long long)out.bytes);

    std::string mods_json = "[";
    for (int i = 0; i < 3; ++i) {
        const LoaderModule &m = *mods[i];
        uint32_t rva = 0, size = 0;
        rmg_data_section(m, &rva, &size);
        mods_json += std::string(i ? ", " : "") + "{\"name\": " + jstr(kRmgModules[i].name) +
                     ", \"base\": \"" + hx(m.base) + "\", \"size\": " + std::to_string(m.size) +
                     ", \"sizeOfImage\": \"" + hx(rmg_pe_size_of_image(m)) +
                     "\", \"timestamp\": \"" + hx(rmg_pe_timestamp(m)) + "\", \"preferred\": \"" +
                     hx(m.preferred_base) + "\", \"data\": {\"rva\": \"" + hx(rva) +
                     "\", \"size\": " + std::to_string(size) + "}, \"path\": " + jstr(m.path) + "}";
    }
    mods_json += "]";
    auto list = [](const std::vector<std::string> &v) {
        std::string s = "[";
        for (size_t i = 0; i < v.size(); ++i)
            s += (i ? ",\n    " : "\n    ") + v[i];
        return s + "]";
    };
    const std::string args = "[\"" + hx(ctx) + "\", \"" + hx(name) + "\"]";
    std::string call = "{\"schema\": \"ee-rmg-capture/1\", \"case\": " + jstr(o.case_name) +
                       ", \"platform\": \"mac-kit\",\n \"seq\": 0, \"gen\": 0, \"attempt\": 0, "
                       "\"export\": \"EERMPIMSetRandomMapValues\", \"tid\": 1, \"esp\": \"" +
                       hx(esp) + "\", \"args\": " + args + ",\n \"modules\": " + mods_json +
                       ",\n \"entry\": {\"esp\": \"" + hx(esp) + "\", \"args\": " + args +
                       ", \"fpu\": " + fpu_in + ", \"summary\": " + sum_in +
                       ", \"script\": " + jstr(o.script) + ", " + in_json +
                       "},\n \"allocs\": " + list(g_allocs) + ",\n \"files\": " + list(g_files) +
                       ",\n \"printf\": " + list(g_printf) + ",\n \"debug\": " + list(g_debug) +
                       ",\n \"warnings\": " + list(g_warnings) + ",\n \"exit\": {\"eax\": \"" +
                       hx(eax) + "\", \"fpu\": " + fpu_json(c) +
                       ", \"summary\": " + summary_json(ctx) + ", " + regions_json(out) + "}}\n";
    make_dirs(g_out + "/calls");
    FILE *f = fopen((g_out + "/calls/000000.json").c_str(), "w");
    if (!f) {
        fprintf(stderr, "record: cannot write %s/calls/000000.json\n", g_out.c_str());
        return 2;
    }
    fputs(call.c_str(), f);
    fclose(f);
    if ((f = fopen((g_out + "/rmg_capture.json").c_str(), "w"))) {
        fprintf(f,
                "{\"schema\": \"ee-rmg-capture/1\", \"case\": %s, \"platform\": \"mac-kit\", "
                "\"calls\": 1, \"blobs\": %zu, \"warnings\": []}\n",
                jstr(o.case_name).c_str(), g_blobs.size());
        fclose(f);
    }
    printf("record: wrote %s (1 call, %zu blobs)\n", g_out.c_str(), g_blobs.size());
    return eax == 0 ? 0 : 1;
}

} // namespace

int rmg_record_main(int argc, char **argv, const char *bundles) {
    setvbuf(stdout, nullptr, _IOLBF, 0);
    Options o;
    for (int i = 1; i < argc; ++i) {
        const char *a = argv[i];
        auto num = [&](uint32_t *v) {
            if (i + 1 < argc)
                *v = (uint32_t)strtoul(argv[++i], nullptr, 0);
        };
        if (!strcmp(a, "--record") && i + 1 < argc)
            o.out = argv[++i];
        else if (!strcmp(a, "--rmv") && i + 1 < argc)
            o.script = argv[++i];
        else if (!strcmp(a, "--case") && i + 1 < argc)
            o.case_name = argv[++i];
        else if (!strcmp(a, "--scripts") && i + 1 < argc)
            o.scripts = argv[++i];
        else if (!strcmp(a, "--heap-at"))
            num(&g_far_base);
        else if (!strcmp(a, "--seed"))
            num(&o.seed);
        else if (!strcmp(a, "--size-class"))
            num(&o.size_class);
        else if (!strcmp(a, "--players"))
            num(&o.players);
        else if (!strcmp(a, "--epoch"))
            num(&o.epoch);
        else {
            fprintf(stderr, "record: unknown option %s\n", a);
            return 2;
        }
    }
    if (!o.out) {
        fprintf(stderr, "record: --record OUT names the capture directory\n");
        return 2;
    }
    g_out = o.out;
    std::string err;
    LoaderModule *mods[3];
    if (!rmg_load_translations(bundles, &err) || !rmg_load_modules(mods, &err)) {
        fprintf(stderr, "record: %s\n", err.c_str());
        return 2;
    }
    install_record_overrides();
    sched_set_guest_thread(true);
    // The far heap starts after the DllMains: their blocks stay in the kit's
    // heap, whose CRT bookkeeping (the onexit tables) must be able to
    // reallocate them, and a replay then needs memory on both sides of
    // guest_size.
    const uint32_t far_base = g_far_base & ~0xffffu;
    g_far_base = 0;
    if (!rmg_attach(loader_context(), mods, &err)) {
        fprintf(stderr, "record: %s\n", err.c_str());
        return 2;
    }
    if (far_base) {
        if (rmg_kit_region(far_base, far_base + kFarSpan) || !heap_exclude(far_base, kFarSpan) ||
            !mem_open(far_base, kFarSpan)) {
            fprintf(stderr, "record: cannot put a heap at %08x\n", far_base);
            return 2;
        }
        g_far_base = g_far_next = far_base;
    }
    int rc = record(o, mods);
    sched_set_guest_thread(false);
    return rc;
}
