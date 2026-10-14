// rmg_mac.h - what the Mac replay (rmg_replay_mac.cpp) and the Mac recorder
// (rmg_record_mac.cpp) share: the three M1 modules through the kit, import
// overrides that wrap the kit's own shims, and a guest call at a given ESP.
// docs/reference/m1-replay.md describes both programs.
#pragma once

#include "runtime/guest.h"
#include "runtime/imports.h"
#include "runtime/loader.h"

#include <stdint.h>
#include <string>
#include <vector>

// game.toml's base map for the M1 modules, in the order Windows runs their
// DllMains (docs/reference/build.md section 3).
struct RmgModule {
    const char *key, *name;
    uint32_t base;
};
extern const RmgModule kRmgModules[3];

// The translations: dlopen()s the three bundles from `dir` (bundle build) or
// checks that the linked archives registered (linked build). False with *err.
bool rmg_load_translations(const char *dir, std::string *err);
// loader_load (which runs mem_init and its reset hooks first), then the three
// modules at their configured bases. False with *err.
bool rmg_load_modules(LoaderModule *mods[3], std::string *err);
// Runs the DllMains as Windows does: the executable's static imports
// (sxlrt233, then the engine), then Default.dll by LoadLibrary.
bool rmg_attach(X86 *c, LoaderModule *mods[3], std::string *err);

// An import override: `fn` replaces dll!name; *orig receives the kit's shim it
// replaced (nullptr when the kit has none), for pass-through.
struct RmgOverride {
    const char *dll, *name;
    void (*fn)(X86 *);
    void (**orig)(X86 *);
};
// After loader_load (the kit's tables are registered by then): records each
// original and registers the overrides, which also re-points the trampolines
// already bound.
void rmg_install_overrides(const RmgOverride *o, size_t n);
// Calls a kit shim the way imports_dispatch does, from inside another shim.
static inline void rmg_pass(X86 *c, void (*orig)(X86 *)) {
    if (orig)
        orig(c);
    else
        set_eax(c, 0);
}

// Runs guest code at `fn` with ESP == esp at its first instruction: [esp] gets
// the kit's return sentinel, the arguments are whatever the caller left at
// [esp+4]..., the general registers are zero, the x87 is reset (FNINIT) and
// then loaded with cw_in. The TEB's stack bounds follow ESP for the duration
// (as the Windows replay sets them). Returns EAX; *cw_out gets the control
// word afterwards. ESP and the TEB are restored. *exited is set when the guest
// ended the process instead of returning.
uint32_t rmg_call_at(X86 *c, uint32_t fn, uint32_t esp, uint16_t cw_in, uint16_t *cw_out,
                     bool *exited);

// Whether [lo, hi) collides with what the kit keeps at fixed guest addresses
// and a replay must not overwrite: the PEB/TEB/TLS pages, the import
// trampolines, the executable's image, the null page. The kit's stack is not
// among them: nothing lives on it while a replayed call runs on its own.
// Returns the name of what is there, or nullptr.
const char *rmg_kit_region(uint32_t lo, uint32_t hi);

// PE header fields of a mapped module, read from guest memory.
uint32_t rmg_pe_timestamp(const LoaderModule &m);
uint32_t rmg_pe_size_of_image(const LoaderModule &m);
// The module's .data section (RVA and virtual size), or false.
bool rmg_data_section(const LoaderModule &m, uint32_t *rva, uint32_t *size);
