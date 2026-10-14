// m1_harness.cpp - load Empire Earth's three M1 modules through the kit and
// check that their code runs.
//
//   ee_m1_harness --bundles DIR [--check] [--rmv NAME] [--imports FILE]
//   ee_m1_harness_static        [--check] [--rmv NAME] [--imports FILE]
//
// sxlrt233.dll, Low-Level Engine.dll and Default.dll are mapped by the kit's
// loader from the original files at their game.toml bases, relocated there
// from their .reloc tables as the translator relocated them; their
// translations come either from
// freestanding bundles this program dlopen()s (ee_m1_harness, the M1 build:
// tools/recomp/bundle.py) or from the generated archives linked in
// (ee_m1_harness_static). Neither links the executable's translation: the
// runtime's own dispatch (kit runtime/dispatch.cpp) serves the modules.
//
// The checks (--check makes any failure the exit status):
//   - each module's translation registered (bundles: once per dlopen);
//   - the modules sit at their configured bases, relocated where they prefer
//     another;
//   - the loader attaches them as Windows does: the executable's static
//     imports in dependency order (sxlrt233, then the engine), then
//     Default.dll by LoadLibrary - every DllMain returning TRUE;
//   - Default.dll's PIM exports answer as the real DLL does on Windows:
//     PIMGetName -> a UString holding "Default", PIMGetType -> 'pamr'
//     (0x726d6170), PIMIsValid -> 1.
// --rmv NAME additionally runs Default.dll's .rmv reader on a script and
// prints an FNV-1a digest of the values it writes into a synthetic context
// (offsets and values, heap pointers left out): a determinism smoke, the two
// builds must agree. The context the EXE builds is M1 work (captures).
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

void crt_pending_register(); // crt_pending.cpp: what the kit's msvcrt lacks yet
// um_vectors.cpp: ADR-002 criterion 2, the engine's maths against the recording
int um_run(X86 *c, const LoaderModule &engine, const char *in, const char *out);

namespace {

// game.toml [modules.aux.<key>] (the base map), in the order Windows runs
// their DllMains.
struct Module {
    const char *key, *name;
    uint32_t base;
};
const Module kModules[] = {
    {"a_sxlrt233", "sxlrt233.dll", 0x10200000u},
    {"b_engine", "Low-Level Engine.dll", 0x10000000u},
    {"c_default", "Default.dll", 0x10300000u},
};

int g_failures = 0;

void expect(bool ok, const char *what) {
    printf("%s %s\n", ok ? "  ok  " : "  FAIL", what);
    if (!ok)
        ++g_failures;
}

// EE_M1_TRACE_IMPORTS=1: every import call with its first arguments and its
// result, on stderr - what a module asked of the runtime just before a fault.
bool trace_call(const char *desc, const uint32_t *args, uint32_t argc, uint32_t *) {
    fprintf(stderr, "[import] %s(", desc);
    for (uint32_t i = 0; i < argc && i < 4; ++i)
        fprintf(stderr, "%s%08x", i ? ", " : "", args[i]);
    fprintf(stderr, ")\n");
    return true;
}
void trace_return(const char *desc, uint32_t eax) {
    fprintf(stderr, "[import] %s -> %08x\n", desc, eax);
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

// EERMPIMSetRandomMapValues (Default.dll+0x70ee -> +0xc268) up to and including
// its .rmv read, replayed call by call [static: +0xc285..+0xc2b3]: five
// set-up calls that write the defaults, then the reader (+0xde77, cdecl
// (RandomMapContext *ctx, const UString &script)), which opens
// <ctx+0x20>/<script>.rmv, follows its #includes and stores each keyword's
// value. What the function does next derives the map from those values and
// from fields the EXE fills, which a synthetic context does not have (it
// faults), so the smoke stops there. The context gets the two objects this
// path uses, built with the engine's own constructors (thiscall: ECX = this):
// the random generator Default.dll draws from at ctx+0x10 (URandom(seed)) and
// the scripts directory at ctx+0x20 (FSFileSpec(UString)) [inferred from
// Default.dll's call sites].
const uint32_t kReadScriptRva = 0xde77;
struct SetUpCall {
    uint32_t rva;
    bool takes_ctx; // cdecl (ctx) rather than no arguments
};
const SetUpCall kSetUp[] = {{0x1439b, false}, {0xc6d0, false}, {0xd40c, true}, {0xdc2c, false},
                            {0xff4d, true}};
const uint32_t kSetUpThisRva = 0x1a7d8, kSetUpMethodRva = 0x40d2; // thiscall, no arguments

void run_rmv(X86 *c, LoaderModule &engine, LoaderModule &def, const char *script) {
    uint32_t ustring_ctor = loader_module_export(engine, "??0UString@@QAE@PBD@Z");
    uint32_t spec_ctor = loader_module_export(engine, "??0FSFileSpec@@QAE@ABVUString@@@Z");
    uint32_t random_ctor = loader_module_export(engine, "??0URandom@@QAE@K@Z");
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
    c->r[R_ECX] = ctx + 0x10;
    guest_call(c, random_ctor, 12345u);
    uint32_t dir = ustring("C:\\Empire Earth\\Data\\Random Map Scripts\\");
    c->r[R_ECX] = ctx + 0x20;
    guest_call(c, spec_ctor, dir);
    uint32_t name = ustring(script);
    for (const SetUpCall &call : kSetUp) {
        if (call.takes_ctx)
            guest_call(c, def.base + call.rva, ctx);
        else
            guest_call(c, def.base + call.rva);
    }
    c->r[R_ECX] = def.base + kSetUpThisRva;
    guest_call(c, def.base + kSetUpMethodRva);
    uint32_t r = guest_call(c, def.base + kReadScriptRva, ctx, name);
    // Values only: a dword pointing into the heap is an allocation address,
    // which depends on the allocator, not on the script.
    uint32_t values = 0, pointers = 0, h = 2166136261u;
    for (uint32_t off = 0x40; off < 0x2000; off += 4) {
        uint32_t v = rd32(ctx + off);
        if (!v)
            continue;
        if (v >= GUEST_HEAP_BASE && v < GUEST_SIZE) {
            ++pointers;
            continue;
        }
        ++values;
        for (uint32_t k = 0; k < 8; ++k)
            h = (h ^ ((k < 4 ? off : v) >> (8 * (k & 3)) & 0xffu)) * 16777619u;
        if (getenv("EE_M1_DUMP"))
            printf("    ctx+%03x = %08x\n", off, v);
    }
    printf("  read %s.rmv -> %u; %u values (and %u heap pointers) in ctx[0x40,0x2000), digest %08x\n",
           script, r, values, pointers, h);
    expect(values > 0, "the script filled the random-map context");
}

} // namespace

int main(int argc, char **argv) {
    setvbuf(stdout, nullptr, _IOLBF, 0); // a guest fault ends the process: keep what was said
    const char *bundles = getenv("EE_M1_BUNDLES"), *rmv = nullptr, *imports_file = nullptr;
    const char *um_in = nullptr, *um_out = nullptr;
    bool check = false;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--bundles") && i + 1 < argc)
            bundles = argv[++i];
        else if (!strcmp(argv[i], "--rmv") && i + 1 < argc)
            rmv = argv[++i];
        else if (!strcmp(argv[i], "--imports") && i + 1 < argc)
            imports_file = argv[++i];
        else if (!strcmp(argv[i], "--um") && i + 1 < argc)
            um_in = argv[++i];
        else if (!strcmp(argv[i], "--um-out") && i + 1 < argc)
            um_out = argv[++i];
        else if (!strcmp(argv[i], "--check"))
            check = true;
        else {
            fprintf(stderr,
                    "usage: %s [--bundles DIR] [--check] [--rmv NAME] [--imports FILE] "
                    "[--um VECTORS --um-out RESULTS]\n",
                    argv[0]);
            return 2;
        }
    }
    // Before loader_load registers the kit's tables, so every shim the kit
    // implements replaces ours.
    crt_pending_register();
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
    if (getenv("EE_M1_TRACE_IMPORTS")) {
        imports_set_call_observer(trace_call);
        imports_set_return_observer(trace_return);
    }
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
        printf("module %-22s base %08x (prefers %08x, %u relocations) size %08x entry %08x\n",
               lm->name.c_str(), lm->base, lm->preferred_base, lm->relocations, lm->size, lm->entry);
        char what[128];
        snprintf(what, sizeof what, "%s at its configured base %08x", m.name, m.base);
        expect(lm->base == m.base && (lm->base == lm->preferred_base || lm->relocations > 0), what);
        mods.push_back(lm);
    }

    sched_set_guest_thread(true);
    int rc = 0;
    if (setjmp(*process_exit_jmp()) == 0) {
        // As Windows starts the game: the executable's static imports
        // (sxlrt233, then the engine, which imports it), then Default.dll,
        // which the executable LoadLibrary()s. The EXE's own entry is not run.
        bool attached = loader_attach_static(c);
        if (!attached)
            printf("  loader_attach_static: %s\n", loader_error());
        expect(attached, "the executable's static imports attach");
        uint32_t err = loader_module_load(c, mods[2]);
        expect(err == 0, "LoadLibrary(\"Default.dll\") attaches it");
        for (LoaderModule *lm : mods)
            printf("  %-22s attached %d, DllMain #%u, x87 CW %04x\n", lm->name.c_str(), lm->attached,
                   lm->attach_seq, c->fpu_cw);
        expect(mods[0]->attached && mods[1]->attached && mods[2]->attached &&
                   mods[0]->attach_seq < mods[1]->attach_seq &&
                   mods[1]->attach_seq < mods[2]->attach_seq,
               "DllMains ran in Windows' order: sxlrt233, engine, Default.dll");
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
        if (um_in) {
            int n = um_run(c, *mods[1], um_in, um_out ? um_out : "um-port.jsonl");
            printf("  um: %d call vectors -> %s\n", n, um_out ? um_out : "um-port.jsonl");
            expect(n > 0, "the call vectors ran");
        }
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
