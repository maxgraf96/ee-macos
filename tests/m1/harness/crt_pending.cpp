// crt_pending.cpp - stand-ins for the msvcrt.dll functions the kit does not
// implement yet, and nothing else: the x87 control word (_controlfp,
// _control87, __control87_2; workstream C) and exception handling
// (_CxxThrowException, __CxxFrameHandler, __CxxLongjmpUnwind,
// _except_handler3, _setjmp3, longjmp; workstream E). Delete each one as the
// kit gains it.
//
// Registered before loader_load runs imports_init, so a shim the kit has
// always replaces the one here (imports_register: a later non-null
// registration wins). None of these is exact; the M1 path is not expected to
// reach the exception ones, so those stop the run by name if it does.
#include "runtime/guest.h"
#include "runtime/imports.h"

#include <cstdio>
#include <cstdlib>

namespace {

// msvcrt's default control word as _controlfp reports it: 53-bit precision,
// round to nearest, every exception masked. State is left alone.
const uint32_t kDefaultControl = 0x0009001fu;

void control_word(X86 *c) {
    static bool said = false;
    if (!said) {
        fprintf(stderr, "[crt_pending] msvcrt control-word call answered with %08x, unchanged\n",
                kDefaultControl);
        said = true;
    }
    set_eax(c, kDefaultControl);
}
// __control87_2(new, mask, unsigned *x86_cw, unsigned *sse2_cw): 1 = success.
void control87_2(X86 *c) {
    if (arg(c, 2))
        wr32(arg(c, 2), kDefaultControl);
    if (arg(c, 3))
        wr32(arg(c, 3), kDefaultControl);
    set_eax(c, 1);
}
// An exception would need workstream E's runtime; say where, and stop.
[[noreturn]] void unsupported(X86 *c, const char *what) {
    fprintf(stderr, "[crt_pending] %s called (return address %08x): MSVC exception handling is not "
                    "implemented yet\n",
            what, rd32(c->r[R_ESP]));
    exit(5);
}
void throw_exception(X86 *c) { unsupported(c, "_CxxThrowException"); }
void long_jump(X86 *c) { unsupported(c, "longjmp"); }
void unwind(X86 *c) { unsupported(c, "__CxxLongjmpUnwind"); }
// Exception handlers asked to handle something: decline (ExceptionContinueSearch).
void decline(X86 *c) { set_eax(c, 1); }
// _setjmp3 as setjmp's first return; a later longjmp stops the run above.
void set_jump(X86 *c) { set_eax(c, 0); }

const ImportShim kPending[] = {
    {"MSVCRT.dll", "_controlfp", ARGC_CDECL, control_word},
    {"MSVCRT.dll", "_control87", ARGC_CDECL, control_word},
    {"MSVCRT.dll", "__control87_2", ARGC_CDECL, control87_2},
    {"MSVCRT.dll", "_CxxThrowException", 2, throw_exception},
    {"MSVCRT.dll", "__CxxFrameHandler", ARGC_CDECL, decline},
    {"MSVCRT.dll", "__CxxLongjmpUnwind", 1, unwind},
    {"MSVCRT.dll", "_except_handler3", ARGC_CDECL, decline},
    {"MSVCRT.dll", "_setjmp3", ARGC_CDECL, set_jump},
    {"MSVCRT.dll", "longjmp", ARGC_CDECL, long_jump},
};

} // namespace

void crt_pending_register() {
    imports_register(kPending, sizeof kPending / sizeof kPending[0]);
}
