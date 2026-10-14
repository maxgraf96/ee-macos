// m1_harness.cpp - load Empire Earth's three M1 modules through the kit and
// check that their code runs.
//
//   ee_m1_harness --bundles DIR [--check] [--rmv NAME] [--imports FILE]
//   ee_m1_harness_static        [--check] [--rmv NAME] [--imports FILE]
//
// sxlrt233.dll, Low-Level Engine.dll and Default.dll are mapped by the kit's
// loader at their game.toml bases; their translations come either from
// freestanding bundles this program dlopen()s (ee_m1_harness, the M1 build:
// tools/recomp/bundle.py) or from the generated archives linked in
// (ee_m1_harness_static). Neither links the executable's translation: the
// runtime's own dispatch (kit runtime/dispatch.cpp) serves the modules.
//
// The checks (--check makes any failure the exit status):
//   - each module's translation registered (bundles: once per dlopen);
//   - the DllMains run in dependency order (sxlrt233, engine, Default.dll)
//     and each returns TRUE;
//   - Default.dll's PIM exports answer as the real DLL does on Windows:
//     PIMGetName -> a UString holding "Default", PIMGetType -> 'pamr'
//     (0x726d6170), PIMIsValid -> 1.
// --rmv NAME additionally runs EERMPIMSetRandomMapValues on a script and
// prints an FNV-1a digest of the context it fills (a determinism smoke: the
// two builds must agree). The random-map context the EXE builds is M1 work;
// this one is synthetic.
#include "dx/dx.h"
#include "runtime/guest.h"
#include "runtime/imports.h"
#include "runtime/loader.h"
#include "runtime/memory.h"
#include "runtime/win32.h"

#include <csetjmp>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>
#ifdef EE_M1_BUNDLES
#include <dlfcn.h>
#endif

void crt_stopgap_register(); // crt_stopgap.cpp (TEMPORARY)

namespace {

// game.toml [modules.aux.<key>], in the order Windows runs their DllMains.
struct Module {
    const char *key, *name;
};
const Module kModules[] = {
    {"a_sxlrt233", "sxlrt233.dll"},
    {"b_engine", "Low-Level Engine.dll"},
    {"c_default", "Default.dll"},
};

int g_failures = 0;

void expect(bool ok, const char *what) {
    printf("%s %s\n", ok ? "  ok  " : "  FAIL", what);
    if (!ok)
        ++g_failures;
}

uint32_t fnv1a(uint32_t addr, uint32_t len) {
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < len; ++i)
        h = (h ^ rd8(addr + i)) * 16777619u;
    return h;
}

#ifdef EE_M1_BUNDLES
// dlopen each module's bundle; its table registers itself from a constructor.
bool load_bundles(const char *dir) {
    for (const Module &m : kModules) {
        std::string path = std::string(dir) + "/" + m.key + ".bundle";
        uint32_t before = recomp_module_count();
        void *handle = dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL);
        if (!handle) {
            fprintf(stderr, "dlopen(%s): %s\n", path.c_str(), dlerror());
            return false;
        }
        const RecompModule *r = recomp_module_named(m.name);
        char what[160];
        snprintf(what, sizeof what, "%s.bundle registers %s (%u entries)", m.key, m.name,
                 r ? r->func_count : 0);
        expect(recomp_module_count() == before + 1 && r != nullptr, what);
    }
    return true;
}
#endif

uint32_t dll_main(X86 *c, const LoaderModule &m) {
    uint16_t cw = c->fpu_cw;
    uint32_t r = guest_call(c, m.entry, m.base, 1u /* DLL_PROCESS_ATTACH */, 0u);
    printf("  %-22s DllMain(%08x, 1, 0) at %08x -> %u (x87 CW %04x -> %04x)\n", m.name.c_str(),
           m.base, m.entry, r, cw, c->fpu_cw);
    return r;
}

// EERMPIMSetRandomMapValues(RandomMapContext *ctx, const UString &script):
// ctx+0x20 is an FSFileSpec naming the scripts directory (spike, from the
// disassembly of Default.dll+0x70ee). Both objects are built with the
// engine's exported constructors (thiscall: ECX = this).
void run_rmv(X86 *c, LoaderModule &engine, LoaderModule &def, const char *script) {
    uint32_t ustring_ctor = loader_module_export(engine, "??0UString@@QAE@PBD@Z");
    uint32_t spec_ctor = loader_module_export(engine, "??0FSFileSpec@@QAE@ABVUString@@@Z");
    auto cstr = [&](const char *t) {
        uint32_t a = heap_alloc((uint32_t)strlen(t) + 1);
        gm_put_str(a, t, (uint32_t)strlen(t) + 1);
        return a;
    };
    auto ustring = [&](const char *t) {
        uint32_t obj = heap_alloc(64, true);
        c->r[R_ECX] = obj;
        guest_call(c, ustring_ctor, cstr(t));
        return obj;
    };
    uint32_t ctx = heap_alloc(0x2000, true);
    uint32_t dir = ustring("C:\\Empire Earth\\Data\\Random Map Scripts\\");
    c->r[R_ECX] = ctx + 0x20;
    guest_call(c, spec_ctor, dir);
    uint32_t name = ustring(script);
    uint32_t set_values = loader_module_export(def, "EERMPIMSetRandomMapValues");
    uint32_t r = guest_call(c, set_values, ctx, name);
    uint32_t nonzero = 0;
    for (uint32_t off = 0x40; off < 0x400; off += 4)
        nonzero += rd32(ctx + off) != 0;
    printf("  EERMPIMSetRandomMapValues(ctx, \"%s\") -> %u; %u non-zero dwords in ctx[0x40,0x400), "
           "digest %08x\n",
           script, r, nonzero, fnv1a(ctx + 0x40, 0x3c0));
    expect(nonzero > 0, "the script filled the random-map context");
}

} // namespace

int main(int argc, char **argv) {
    const char *bundles = getenv("EE_M1_BUNDLES"), *rmv = nullptr, *imports_file = nullptr;
    bool check = false;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--bundles") && i + 1 < argc)
            bundles = argv[++i];
        else if (!strcmp(argv[i], "--rmv") && i + 1 < argc)
            rmv = argv[++i];
        else if (!strcmp(argv[i], "--imports") && i + 1 < argc)
            imports_file = argv[++i];
        else if (!strcmp(argv[i], "--check"))
            check = true;
        else {
            fprintf(stderr, "usage: %s [--bundles DIR] [--check] [--rmv NAME] [--imports FILE]\n",
                    argv[0]);
            return 2;
        }
    }
    // TEMPORARY: msvcrt until the kit has it (feat/crt). Registered before the
    // loader's own tables, so every shim the kit implements replaces ours.
    if (!getenv("EE_M1_NO_CRT_STOPGAP"))
        crt_stopgap_register();
#ifdef EE_M1_BUNDLES
    printf("translations: freestanding bundles from %s\n", bundles ? bundles : "(unset)");
    if (!bundles) {
        fprintf(stderr, "pass --bundles DIR (or EE_M1_BUNDLES): the directory bundle.py wrote\n");
        return 2;
    }
    if (!load_bundles(bundles))
        return 3;
#else
    (void)bundles;
    printf("translations: linked archives\n");
    for (const Module &m : kModules)
        expect(recomp_module_named(m.name) != nullptr, m.key);
#endif
    if (!loader_load(nullptr)) {
        fprintf(stderr, "loader_load failed: %s\n", loader_error());
        return 3;
    }
    dx_register_shims();
    X86 *c = loader_context();
    printf("main image %s at %08x; %u IAT slots patched\n", loader_exe_path().c_str(),
           loader_image_base(), loader_iat_patched());
    std::vector<LoaderModule *> mods;
    for (const Module &m : kModules) {
        LoaderModule *lm = loader_module_named(m.name);
        if (!lm) {
            fprintf(stderr, "the loader did not map %s\n", m.name);
            return 3;
        }
        printf("module %-22s base %08x size %08x entry %08x\n", lm->name.c_str(), lm->base,
               lm->size, lm->entry);
        mods.push_back(lm);
    }

    sched_set_guest_thread(true);
    int rc = 0;
    if (setjmp(*process_exit_jmp()) == 0) {
        // Static imports first, as Windows does: sxlrt233 has no EE
        // dependency, the engine imports it, Default.dll (which the EXE
        // LoadLibrary()s) imports both.
        for (LoaderModule *lm : mods) {
            lm->attached = true;
            lm->load_refs = 1;
            char what[96];
            snprintf(what, sizeof what, "%s DllMain returns TRUE", lm->name.c_str());
            expect(dll_main(c, *lm) == 1, what);
        }
        LoaderModule &def = *mods[2];
        uint32_t name = guest_call(c, loader_module_export(def, "PIMGetName"));
        uint32_t type = guest_call(c, loader_module_export(def, "PIMGetType"));
        uint32_t valid = guest_call(c, loader_module_export(def, "PIMIsValid")) & 0xffu;
        // A UString (engine class): {vtable, char *data, length, capacity}.
        std::string text = name ? gm_str(rd32(name + 4), 64) : std::string();
        printf("  PIMGetName -> %08x {vtable %08x, \"%s\"}; PIMGetType -> %08x; PIMIsValid -> %u\n",
               name, name ? rd32(name) : 0, text.c_str(), type, valid);
        expect(text == "Default", "PIMGetName is \"Default\"");
        expect(type == 0x726d6170u, "PIMGetType is 'pamr' (0x726d6170)");
        expect(valid == 1, "PIMIsValid is 1");
        if (rmv)
            run_rmv(c, *mods[1], def, rmv);
    } else {
        printf("guest process exited with code %u\n", process_exit_code());
        ++g_failures;
        rc = 4;
    }
    sched_set_guest_thread(false);

    if (imports_file) {
        // "dll<TAB>name" lines: allocate each so the coverage dump lists it.
        std::ifstream in(imports_file);
        std::string line;
        while (std::getline(in, line)) {
            size_t tab = line.find('\t');
            if (tab != std::string::npos)
                imports_alloc_trampoline(line.substr(0, tab).c_str(), line.substr(tab + 1).c_str(),
                                         nullptr, ARGC_UNKNOWN);
        }
        imports_dump_coverage(stdout);
    }
    printf("M1 harness: %s\n", g_failures ? "FAILED" : "all checks passed");
    if (rc)
        return rc;
    return check && g_failures ? 1 : 0;
}
