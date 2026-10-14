// rmg_mac_common.cpp - the M1 modules through the kit, import overrides and a
// guest call at a given ESP, shared by the Mac replay and the Mac recorder.
// See rmg_mac.h.
#include "rmg_mac.h"

#include "dx/dx.h"
#include "runtime/memory.h"
#include "runtime/win32.h"

#include <csetjmp>
#include <cstdio>
#include <cstring>
#include <strings.h>
#ifdef EE_M1_BUNDLES
#include <dlfcn.h>
#endif

const RmgModule kRmgModules[3] = {
    {"a_sxlrt233", "sxlrt233.dll", 0x10200000u},
    {"b_engine", "Low-Level Engine.dll", 0x10000000u},
    {"c_default", "Default.dll", 0x10300000u},
};

bool rmg_load_translations(const char *dir, std::string *err) {
#ifdef EE_M1_BUNDLES
    if (!dir) {
        *err = "pass --bundles DIR (or EE_M1_BUNDLES): the directory bundle.py wrote";
        return false;
    }
    for (const RmgModule &m : kRmgModules) {
        std::string path = std::string(dir) + "/" + m.key + ".bundle";
        uint32_t before = recomp_module_count();
        if (!dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL)) {
            *err = std::string("dlopen(") + path + "): " + dlerror();
            return false;
        }
        if (recomp_module_count() != before + 1 || !recomp_module_named(m.name)) {
            *err = path + " did not register " + m.name;
            return false;
        }
    }
#else
    (void)dir;
    for (const RmgModule &m : kRmgModules)
        if (!recomp_module_named(m.name)) {
            *err = std::string("the linked translation of ") + m.name + " did not register";
            return false;
        }
#endif
    return true;
}

bool rmg_load_modules(LoaderModule *mods[3], std::string *err) {
    if (!loader_load(nullptr)) {
        *err = std::string("loader_load: ") + loader_error();
        return false;
    }
    dx_register_shims();
    for (int i = 0; i < 3; ++i) {
        mods[i] = loader_module_named(kRmgModules[i].name);
        if (!mods[i] || mods[i]->base != kRmgModules[i].base) {
            *err = std::string(kRmgModules[i].name) + " is not mapped at its game.toml base";
            return false;
        }
    }
    return true;
}

bool rmg_attach(X86 *c, LoaderModule *mods[3], std::string *err) {
    bool ok = false;
    if (setjmp(*process_exit_jmp()) == 0) {
        if (!loader_attach_static(c))
            *err = std::string("loader_attach_static: ") + loader_error();
        else if (uint32_t e = loader_module_load(c, mods[2]))
            *err = "LoadLibrary(\"Default.dll\") failed with " + std::to_string(e);
        else if (!(mods[0]->attached && mods[1]->attached && mods[2]->attached))
            *err = "a DllMain did not attach";
        else
            ok = true;
    } else {
        *err = "the guest ended the process during a DllMain (code " +
               std::to_string(process_exit_code()) + ")";
    }
    return ok;
}

namespace {
struct Seek {
    const char *dll, *name;
    void (*found)(X86 *);
};
void seek_original(const ImportShim &s, void *user) {
    Seek *k = static_cast<Seek *>(user);
    if (s.fn && !strcasecmp(s.dll, k->dll) && !strcmp(s.name, k->name))
        k->found = s.fn;
}
} // namespace

void rmg_install_overrides(const RmgOverride *o, size_t n) {
    std::vector<ImportShim> table;
    for (size_t i = 0; i < n; ++i) {
        Seek k{o[i].dll, o[i].name, nullptr};
        imports_for_each_registered(seek_original, &k);
        // Installing twice must not make the override its own original.
        if (k.found != o[i].fn)
            *o[i].orig = k.found;
        table.push_back(ImportShim{o[i].dll, o[i].name, ARGC_CDECL, o[i].fn});
    }
    // OutputDebugStringA is stdcall (one argument); everything else here is
    // msvcrt's, which is cdecl.
    for (ImportShim &s : table)
        if (!strcasecmp(s.dll, "kernel32.dll"))
            s.argc_stdcall = imports_infer_argc(s.dll, s.name);
    imports_register(table.data(), table.size());
}

uint32_t rmg_call_at(X86 *c, uint32_t fn, uint32_t esp, uint16_t cw_in, uint16_t *cw_out,
                     bool *exited) {
    const uint32_t saved_esp = c->r[R_ESP], teb = c->fs_base;
    const uint32_t saved_base = rd32(teb + 4), saved_limit = rd32(teb + 8);
    for (int r = 0; r < 8; ++r)
        if (r != R_ESP)
            c->r[r] = 0;
    c->eflags_df = 0;
    x87_finit(c);
    x87_set_cw(c, cw_in);
    wr32(teb + 4, (esp + 0x1000u) & ~0xfffu);
    wr32(teb + 8, (esp - 0x100000u) & ~0xfffu);
    // guest_call pushes its sentinel below the ESP it finds: start one slot up.
    c->r[R_ESP] = esp + 4;
    volatile uint32_t eax = 0; // set between setjmp and a longjmp past it
    *exited = false;
    if (setjmp(*process_exit_jmp()) == 0)
        eax = guest_call(c, fn);
    else
        *exited = true;
    *cw_out = c->fpu_cw;
    wr32(teb + 4, saved_base);
    wr32(teb + 8, saved_limit);
    c->r[R_ESP] = saved_esp;
    return eax;
}

const char *rmg_kit_region(uint32_t lo, uint32_t hi) {
    struct Region {
        uint32_t lo, hi;
        const char *what;
    } const regions[] = {
        {0, GUEST_NULL_LIMIT, "the null page"},
        {TEB_BASE - 0x1000u, TLS_BASE + 0x1000u, "the kit's PEB/TEB/TLS pages"},
        {GUEST_SHIM_BASE, GUEST_SHIM_END, "the kit's import trampolines"},
        {loader_image_base(), loader_image_limit(), "Empire Earth.exe's image"},
    };
    for (const Region &r : regions)
        if (lo < r.hi && r.lo < hi)
            return r.what;
    return nullptr;
}

uint32_t rmg_pe_timestamp(const LoaderModule &m) {
    return rd32(m.base + rd32(m.base + 0x3c) + 8);
}

uint32_t rmg_pe_size_of_image(const LoaderModule &m) {
    return rd32(m.base + rd32(m.base + 0x3c) + 24 + 56);
}

bool rmg_data_section(const LoaderModule &m, uint32_t *rva, uint32_t *size) {
    for (const SectionInfo &s : m.sections)
        if (s.name == ".data") {
            *rva = s.va - m.base;
            *size = s.vsize;
            return true;
        }
    return false;
}
