// eh_harness.cpp - Empire Earth's own C++ exceptions through the kit's MSVC
// exception handling (kit runtime/msvc_eh.cpp), on the executable's
// translation.
//
//   ee_eh_harness --bundles DIR [--check] [--unhandled]
//
// Loads sxlrt233.dll, Low-Level Engine.dll and the executable's translation
// (freestanding bundles: a_sxlrt233, b_engine, main), attaches the
// executable's static imports as Windows does, then runs WON code that throws
// and catches. The pieces [static, from the function DB and the code]:
//   EXE+0x29ef10  ReadBuffer::ReadBuffer(const void *data, unsigned size),
//                 thiscall: {offset, data, size}
//   EXE+0x29ef50  ReadBuffer::ReadBytes(unsigned n), thiscall: throws a
//                 WONAPI::ReadBufferException (ThrowInfo EXE+0x469dd8: 24
//                 bytes, destructor EXE+0x29f060, catchable as itself and as
//                 std::exception) when fewer than n bytes remain. A C++
//                 frame of its own with no try block (frame-pointer-less
//                 prologue): its unwind map runs on the way out.
//   EXE+0x29f270  ReadShort (ReadBytes(2)); EXE+0x29f250 ReadBool (a byte, as 0/1).
//   EXE+0x3944b0  a message's Unpack(ReadBuffer &), thiscall:
//                   try { w = rb.ReadShort(); b = rb.ReadBool(); this->vfn1(rb); }
//                   catch (WONAPI::ReadBufferException &) { return false; }
//                 catch block EXE+0x39450b returns continuation EXE+0x394511.
//   EXE+0x2048ab  a `RET 4` (a no-op virtual with one argument).
// --unhandled: ReadShort with nothing to read and nothing to catch: the
// process must end in terminate() (exit code 3), naming the exception type.
#include "dx/dx.h"
#include "runtime/guest.h"
#include "runtime/imports.h"
#include "runtime/loader.h"
#include "runtime/memory.h"
#include "runtime/msvcrt_internal.h"
#include "runtime/win32.h"

#include <csetjmp>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <dlfcn.h>

void crt_pending_register(); // tests/m1/harness/crt_pending.cpp

namespace {

const uint32_t kReadBufferCtor = 0x0069ef10, kReadShort = 0x0069f270;
const uint32_t kUnpack = 0x007944b0, kRet4 = 0x006048ab;
const char *const kBundles[] = {"a_sxlrt233", "b_engine", "main"};

int g_failures = 0;
void expect(bool ok, const char *what) {
    printf("%s %s\n", ok ? "  ok  " : "  FAIL", what);
    if (!ok)
        ++g_failures;
}

// The exception object's message lives on the CRT heap: count what the
// guest allocates and frees through msvcrt while a throw is caught.
int g_allocs = 0, g_frees = 0;
bool count_heap(const char *desc, const uint32_t *, uint32_t, uint32_t *) {
    const char *name = strchr(desc, '!');
    name = name ? name + 1 : desc;
    if (!strcmp(name, "??2@YAPAXI@Z") || !strcmp(name, "malloc"))
        ++g_allocs;
    else if (!strcmp(name, "??3@YAXPAX@Z") || !strcmp(name, "free"))
        ++g_frees;
    return true;
}

uint32_t read_buffer(X86 *c, const void *bytes, uint32_t n) {
    uint32_t data = heap_alloc(n ? n : 1, true);
    memcpy(gm_ptr(data), bytes, n);
    uint32_t rb = heap_alloc(16, true);
    c->r[R_ECX] = rb;
    guest_call(c, kReadBufferCtor, data, n);
    return rb;
}

// One Unpack call, as a caller would make it; the callee-saved registers and
// the chain head must come back unchanged whether or not it threw.
uint32_t unpack(X86 *c, uint32_t self, uint32_t rb, bool *preserved) {
    uint32_t ebp = c->r[R_EBP] = 0x0e0ebeb0, ebx = c->r[R_EBX] = 0x0b0b0b0b;
    uint32_t esi = c->r[R_ESI] = 0x05050505, edi = c->r[R_EDI] = 0x0d0d0d0d;
    uint32_t head = rd32(c->fs_base);
    c->r[R_ECX] = self;
    uint32_t r = guest_call(c, kUnpack, rb) & 0xffu;
    *preserved = c->r[R_EBP] == ebp && c->r[R_EBX] == ebx && c->r[R_ESI] == esi &&
                 c->r[R_EDI] == edi && rd32(c->fs_base) == head;
    return r;
}

void run(X86 *c, bool unhandled) {
    if (unhandled) {
        uint32_t rb = read_buffer(c, "\x01", 1);
        c->r[R_ECX] = rb;
        guest_call(c, kReadShort);
        expect(false, "an uncaught ReadBufferException ends the process");
        return;
    }
    // A message object: a vtable whose slot 1 is a no-op `RET 4`.
    uint32_t vtable = heap_alloc(16, true);
    wr32(vtable + 4, kRet4);
    uint32_t self = heap_alloc(64, true);
    wr32(self, vtable);
    bool preserved = false;

    // Enough bytes: no exception, the fields are read, the buffer advanced.
    uint32_t rb = read_buffer(c, "\x34\x12\x56", 3);
    unpack(c, self, rb, &preserved);
    printf("  Unpack(3 bytes): field +8 %04x, +a %02x, buffer offset %u\n", rd16(self + 8),
           rd8(self + 0xa), rd32(rb));
    expect(rd16(self + 8) == 0x1234 && rd8(self + 0xa) == 1 && rd32(rb) == 3,
           "Unpack of a 3-byte buffer reads a short and a bool, no exception");
    expect(preserved, "  ...and returns with EBP, EBX, ESI, EDI and FS:[0] intact");

    // One byte: ReadShort throws from two frames down, Unpack catches it by
    // reference, its continuation returns false. The exception object's
    // destructor frees what its constructor allocated.
    wr32(self + 8, 0xffffffffu);
    crt::HeapCounters before = crt::heap_counters();
    rb = read_buffer(c, "\x01", 1);
    g_allocs = g_frees = 0;
    imports_set_call_observer(count_heap);
    uint32_t r = unpack(c, self, rb, &preserved);
    imports_set_call_observer(nullptr);
    crt::HeapCounters after = crt::heap_counters();
    printf("  Unpack(1 byte) -> %u; field +8 %08x; live CRT blocks %llu -> %llu\n", r,
           rd32(self + 8), (unsigned long long)before.live_blocks,
           (unsigned long long)after.live_blocks);
    expect(r == 0, "Unpack of a 1-byte buffer catches ReadBufferException and returns false");
    expect(rd32(self + 8) == 0xffffffffu, "  ...before ReadShort could store anything");
    expect(preserved, "  ...and returns with EBP, EBX, ESI, EDI and FS:[0] intact");
    printf("  during it: %d CRT allocations, %d frees\n", g_allocs, g_frees);
    expect(g_allocs > 0 && g_frees == g_allocs && after.live_blocks == before.live_blocks &&
               after.bad_frees == before.bad_frees,
           "  ...and the exception's destructor released what its constructor allocated");

    // Many times over: every catch resumes the same frame, nothing accumulates.
    before = crt::heap_counters();
    int caught = 0, intact = 0;
    for (int i = 0; i < 2000; ++i) {
        uint32_t b = read_buffer(c, "\x01", 1);
        caught += unpack(c, self, b, &preserved) == 0;
        intact += preserved;
        heap_free(rd32(b + 4));
        heap_free(b);
    }
    after = crt::heap_counters();
    printf("  2000 throws: %d caught, %d with registers intact; live CRT blocks %llu -> %llu\n",
           caught, intact, (unsigned long long)before.live_blocks,
           (unsigned long long)after.live_blocks);
    expect(caught == 2000 && intact == 2000, "2000 throws and catches in a row");
    expect(after.live_blocks == before.live_blocks, "  ...with no CRT allocation left behind");
}

} // namespace

int main(int argc, char **argv) {
    setvbuf(stdout, nullptr, _IOLBF, 0);
    const char *bundles = getenv("EE_M1_BUNDLES");
    bool check = false, unhandled = false;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--bundles") && i + 1 < argc)
            bundles = argv[++i];
        else if (!strcmp(argv[i], "--check"))
            check = true;
        else if (!strcmp(argv[i], "--unhandled"))
            unhandled = true;
        else {
            fprintf(stderr, "usage: %s --bundles DIR [--check] [--unhandled]\n", argv[0]);
            return 2;
        }
    }
    if (!bundles) {
        fprintf(stderr, "pass --bundles DIR (or EE_M1_BUNDLES): the directory bundle.py wrote\n");
        return 2;
    }
    crt_pending_register();
    for (const char *key : kBundles) {
        std::string path = std::string(bundles) + "/" + key + ".bundle";
        if (!dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL)) {
            fprintf(stderr, "dlopen(%s): %s\n", path.c_str(), dlerror());
            return 3;
        }
    }
    if (!loader_load(nullptr)) {
        fprintf(stderr, "loader_load failed: %s\n", loader_error());
        return 3;
    }
    dx_register_shims();
    X86 *c = loader_context();
    expect(recomp_module_named("Empire Earth.exe") != nullptr, "the executable's translation registered");
    sched_set_guest_thread(true);
    int rc = 0;
    if (setjmp(*process_exit_jmp()) == 0) {
        bool attached = loader_attach_static(c);
        if (!attached)
            printf("  loader_attach_static: %s\n", loader_error());
        expect(attached, "the executable's static imports attach");
        run(c, unhandled);
    } else {
        printf("guest process exited with code %u\n", process_exit_code());
        rc = unhandled && process_exit_code() == 3 ? 3 : 4;
        if (!unhandled)
            ++g_failures;
    }
    sched_set_guest_thread(false);
    printf("EH harness: %s\n", g_failures ? "FAILED" : "all checks passed");
    if (rc)
        return rc;
    return check && g_failures ? 1 : 0;
}
