// ee_assertions.cpp - Empire Earth's failed assertions, reported by name.
//
// The game's assertion is the engine's ESVerify, exported by
// Low-Level Engine.dll as ?ESVerify@@YAXPBDK0@Z and imported by the
// executable:
//
//   void ESVerify(const char *file, unsigned long line, const char *expr);
//
// Called only for an expression that failed. The first time, it formats
// "<file> (<line>) : Verification failure (<expr>)" into a buffer on its own
// stack and calls abort() - nothing else: no message box, no log file, no
// debugger output (engine 0x1001c43e [static]). On Windows the player sees
// msvcrt's "abnormal program termination"; here the run's log had only
// "msvcrt: abort() from 1001c47e", the return address inside ESVerify. So
// every host that boots the game - the windowed app, the player host of the
// shareable app, ee_boot - reports it before the engine's code runs:
//
//   [ee] ESVerify failed: <file>:<line>: <expr>
//   [ee]   called from EXE+2c3a1 EXE+2b0f0 ...
//
// on stderr (the app's game.log), followed by the engine's own abort(). The
// chain is ESVerify's return address, then the return addresses along the
// caller's EBP chain, as module+offset (docs/reference/packaging.md §9).
//
// How: a load step (kit host/boot.h, boot_add_load_step), run once the
// modules and their translated code are in, points the engine module's own
// hook slot for ESVerify at report() - the slot every translated call site
// and every indirect call into the module consults (kit runtime/x86.h,
// RecompModule) - and report() then runs the engine's code. Nothing is
// changed until an assertion fails; a run where none does pays one flag test
// per ESVerify call, and there are none. The mods module's hooks reach only
// the executable's table, so this one is set directly; it refuses a slot
// someone else has set.
#include "host/boot.h"
#include "runtime/guest.h"
#include "runtime/loader.h"
#include "runtime/win32.h"
#include "runtime/x86.h"

#include <cstdio>
#include <cctype>
#include <string>

namespace {

const char kEngine[] = "Low-Level Engine.dll";
const char kESVerify[] = "?ESVerify@@YAXPBDK0@Z";

const RecompModule *g_engine = nullptr; // the module whose slot is set

// "EXE+2c3a1", "Low-Level Engine.dll+1c47e", or the bare address.
std::string where(uint32_t a) {
    char buf[96];
    const LoaderModule *m = loader_module_containing(a);
    if (m)
        snprintf(buf, sizeof buf, "%s+%x", m->base == loader_image_base() ? "EXE" : m->name.c_str(),
                 a - m->base);
    else if (a >= loader_image_base() && a < loader_image_limit())
        snprintf(buf, sizeof buf, "EXE+%x", a - loader_image_base());
    else
        snprintf(buf, sizeof buf, "%08x", a);
    return buf;
}

std::string guest_text(uint32_t a) {
    if (!a || !gm_valid(a, 1))
        return "(null)";
    return gm_str(a, 512);
}

// ESVerify's entry: [ESP] is the return address, the arguments follow.
void report(X86 *c, uint32_t index) {
    {
        const uint32_t esp = c->r[R_ESP];
        const bool readable = gm_valid(esp, 16);
        const uint32_t ret = readable ? rd32(esp) : 0, file = readable ? rd32(esp + 4) : 0,
                       line = readable ? rd32(esp + 8) : 0, expr = readable ? rd32(esp + 12) : 0;
        std::string chain = where(ret);
        for (uint32_t r : win32_return_chain(c->r[R_EBP], 7))
            chain += " " + where(r);
        fprintf(stderr, "[ee] ESVerify failed: %s:%u: %s\n[ee]   called from %s\n",
                guest_text(file).c_str(), line, guest_text(expr).c_str(), chain.c_str());
        fflush(stderr);
    }
    // The engine's own ESVerify: the message into its buffer, then abort().
    // Nothing of this frame is alive across it (abort ends the process).
    g_engine->base_ptrs[index](c);
}

// The loader keeps a module's name as the import that named it, in whatever
// case that was.
bool same_name(const std::string &a, const char *b) {
    size_t i = 0;
    for (; i < a.size() && b[i]; ++i)
        if (tolower((unsigned char)a[i]) != tolower((unsigned char)b[i]))
            return false;
    return i == a.size() && !b[i];
}

const LoaderModule *engine_module() {
    for (uint32_t i = 0; const LoaderModule *m = loader_module(i); ++i)
        if (same_name(m->name, kEngine))
            return m;
    return nullptr;
}

void install() {
    const LoaderModule *engine = engine_module();
    const uint32_t addr = engine ? loader_module_export(*engine, kESVerify) : 0;
    const RecompModule *m = addr ? recomp_module_containing(addr) : nullptr;
    const int32_t index = m ? recomp_module_lookup(addr) : -1;
    if (index < 0) {
        fprintf(stderr, "[ee] assertions: no translated ESVerify in %s; failures are not named\n",
                kEngine);
        return;
    }
    if (__atomic_load_n(&m->hooked[index], __ATOMIC_ACQUIRE)) {
        fprintf(stderr, "[ee] assertions: ESVerify's slot is taken; failures are not named\n");
        return;
    }
    g_engine = m;
    __atomic_store_n(&m->hook_ptrs[index], (RecompHookFn)report, __ATOMIC_RELEASE);
    __atomic_store_n(&m->hooked[index], (uint8_t)1, __ATOMIC_RELEASE);
    printf("[ee] assertions: a failed ESVerify (%s) is reported\n", where(addr).c_str());
}

__attribute__((constructor)) void ee_assertions_init() {
    boot_add_load_step(install);
}

} // namespace
