/* M1 random-map replay driver: Windows platform (32-bit; MSVC x86 or MinGW i686).
 *
 *   rmg_replay.exe [--game DIR] [--diff-dir DIR] [-v] PACK.rrp...
 *
 * For each pack the parent process starts a suspended child (this same program with --child),
 * checks that every address range the call needs is free in the child, reserves those ranges
 * before the child's loader runs (so its heap, its DLLs and the system DLLs it loads later stay
 * out of them), and resumes it. If a range is taken it retries with a new child (ASLR moves the
 * child's own stack and heap). The child maps sxlrt233.dll, Low-Level Engine.dll and Default.dll
 * itself at the captured bases, binds their imports (msvcrt allocators, fopen/fgets/fclose and
 * kernel32 OutputDebugStringA to the replay shims; everything else to the real DLLs), runs their
 * DllMains, then hands over to the portable core (rmg_replay_core.c), which restores the memory,
 * calls the export on the captured stack and compares the result.
 *
 * Exit status: 0 all packs passed, 1 a mismatch, 2 an error (setup, address conflict, crash).
 * Build: build_win32.bat (MSVC) or README.md (MinGW / clang-cl). Must be /LARGEADDRESSAWARE:
 * the game is, so captured addresses can lie above 2 GB.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "rmg_replay.h"

#ifndef DBG_PRINTEXCEPTION_C
#define DBG_PRINTEXCEPTION_C 0x40010006L
#endif
#ifdef _MSC_VER
#pragma warning(disable : 4054 4055 4152 4996) /* function/data pointer casts, CRT deprecations */
#endif

static char g_game_dir[MAX_PATH] = "C:\\EEPort\\game\\Empire Earth";
static int g_verbose;

/* ---- import shims ----------------------------------------------------------------------- */

typedef void *(__cdecl *alloc1_t)(size_t);
typedef void(__cdecl *free_t)(void *);
static alloc1_t real_new, real_newa, real_malloc;
static void *(__cdecl *real_calloc)(size_t, size_t);
static void *(__cdecl *real_realloc)(void *, size_t);
static free_t real_free, real_delete, real_deletea;
static FILE *(__cdecl *real_fopen)(const char *, const char *);
static char *(__cdecl *real_fgets)(char *, int, FILE *);
static int(__cdecl *real_fclose)(FILE *);
static unsigned g_dbg_lines;

#define G(p) ((uint32_t)(uintptr_t)(p))
#define H(a) ((void *)(uintptr_t)(a))

/* Outside the replayed call (DllMains, set-up) every shim forwards to the real msvcrt function.
   A private heap does not work there: the DLLs' CRT start-up mallocs its atexit table through the
   shim, and msvcrt's own __dllonexit later grows it with its internal _msize/realloc on msvcrt's
   heap, which corrupted the heap or faulted in ntdll (Default.dll, most runs on legion). */
static void *__cdecl sh_new(size_t n) { return rr_active() ? H(rr_shim_alloc(RR_OP_NEW, (uint32_t)n, 0)) : real_new(n); }
static void *__cdecl sh_newa(size_t n) { return rr_active() ? H(rr_shim_alloc(RR_OP_NEWA, (uint32_t)n, 0)) : real_newa(n); }
static void *__cdecl sh_malloc(size_t n) { return rr_active() ? H(rr_shim_alloc(RR_OP_MALLOC, (uint32_t)n, 0)) : real_malloc(n); }
static void *__cdecl sh_calloc(size_t c, size_t n)
{
    return rr_active() ? H(rr_shim_alloc(RR_OP_CALLOC, (uint32_t)c, (uint32_t)n)) : real_calloc(c, n);
}
static void *__cdecl sh_realloc(void *p, size_t n)
{
    return rr_active() ? H(rr_shim_alloc(RR_OP_REALLOC, G(p), (uint32_t)n)) : real_realloc(p, n);
}
static void __cdecl sh_delete(void *p) { if (rr_active()) rr_shim_free(RR_OP_DELETE, G(p)); else real_delete(p); }
static void __cdecl sh_deletea(void *p) { if (rr_active()) rr_shim_free(RR_OP_DELETEA, G(p)); else real_deletea(p); }
static void __cdecl sh_free(void *p) { if (rr_active()) rr_shim_free(RR_OP_FREE, G(p)); else real_free(p); }
static FILE *__cdecl sh_fopen(const char *path, const char *mode)
{
    return rr_active() ? (FILE *)H(rr_shim_fopen(G(path), G(mode))) : real_fopen(path, mode);
}
static char *__cdecl sh_fgets(char *b, int n, FILE *f)
{
    return rr_active() ? (char *)H(rr_shim_fgets(G(b), n, G(f))) : real_fgets(b, n, f);
}
static int __cdecl sh_fclose(FILE *f) { return rr_active() ? rr_shim_fclose(G(f)) : real_fclose(f); }
/* ESDebugOut ends in OutputDebugStringA, which raises an exception internally; keep that off the
   captured stack (no SEH frames there are valid for the OS). */
static void WINAPI sh_ods(LPCSTR s)
{
    g_dbg_lines++;
    if (g_verbose > 1 && s) printf("  [debug] %s", s);
}

static const struct { const char *dll, *name; void *fn; } k_shims[] = {
    { "msvcrt.dll", "??2@YAPAXI@Z", (void *)sh_new }, { "msvcrt.dll", "??_U@YAPAXI@Z", (void *)sh_newa },
    { "msvcrt.dll", "malloc", (void *)sh_malloc }, { "msvcrt.dll", "calloc", (void *)sh_calloc },
    { "msvcrt.dll", "realloc", (void *)sh_realloc }, { "msvcrt.dll", "??3@YAXPAX@Z", (void *)sh_delete },
    { "msvcrt.dll", "??_V@YAXPAX@Z", (void *)sh_deletea }, { "msvcrt.dll", "free", (void *)sh_free },
    { "msvcrt.dll", "fopen", (void *)sh_fopen }, { "msvcrt.dll", "fgets", (void *)sh_fgets },
    { "msvcrt.dll", "fclose", (void *)sh_fclose }, { "kernel32.dll", "OutputDebugStringA", (void *)sh_ods },
};

static void *shim_for(const char *dll, const char *name)
{
    size_t i;
    for (i = 0; i < sizeof k_shims / sizeof k_shims[0]; i++)
        if (!_stricmp(dll, k_shims[i].dll) && !strcmp(name, k_shims[i].name)) return k_shims[i].fn;
    return NULL;
}

/* ---- PE mapping ------------------------------------------------------------------------- */

typedef struct mod {
    char name[32];
    uint8_t *img;
    IMAGE_NT_HEADERS32 *nt;
} mod_t;
static mod_t g_mod[3];
static int g_nmod;

static void die(int code, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stdout, fmt, ap);
    va_end(ap);
    fputc('\n', stdout);
    fflush(stdout);
    TerminateProcess(GetCurrentProcess(), (UINT)code);
}

static mod_t *own_module(const char *dll)
{
    int i;
    for (i = 0; i < g_nmod; i++) if (!_stricmp(dll, g_mod[i].name)) return &g_mod[i];
    return NULL;
}

static void *own_export(mod_t *m, const char *name, WORD ord)
{
    IMAGE_DATA_DIRECTORY dd = m->nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_EXPORT];
    IMAGE_EXPORT_DIRECTORY *ed;
    DWORD *funcs, *names, idx, rva;
    WORD *ords;
    if (!dd.Size) return NULL;
    ed = (IMAGE_EXPORT_DIRECTORY *)(m->img + dd.VirtualAddress);
    funcs = (DWORD *)(m->img + ed->AddressOfFunctions);
    names = (DWORD *)(m->img + ed->AddressOfNames);
    ords = (WORD *)(m->img + ed->AddressOfNameOrdinals);
    if (name) {
        DWORD lo = 0, hi = ed->NumberOfNames;
        idx = (DWORD)-1;
        while (lo < hi) {
            DWORD mid = (lo + hi) / 2;
            int c = strcmp(name, (const char *)(m->img + names[mid]));
            if (!c) { idx = ords[mid]; break; }
            if (c < 0) hi = mid; else lo = mid + 1;
        }
        if (idx == (DWORD)-1) return NULL;
    } else {
        idx = ord - ed->Base;
    }
    if (idx >= ed->NumberOfFunctions || !(rva = funcs[idx])) return NULL;
    if (rva >= dd.VirtualAddress && rva < dd.VirtualAddress + dd.Size) return NULL;   /* forwarder: unused here */
    return m->img + rva;
}

static uint8_t *read_file(const char *path, DWORD *len)
{
    HANDLE f = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    uint8_t *buf;
    DWORD got = 0;
    if (f == INVALID_HANDLE_VALUE) return NULL;
    *len = GetFileSize(f, NULL);
    buf = (uint8_t *)malloc(*len);
    if (!buf || !ReadFile(f, buf, *len, &got, NULL) || got != *len) { free(buf); buf = NULL; }
    CloseHandle(f);
    return buf;
}

static int commit(uint32_t addr, uint32_t size)
{
    uint32_t lo, hi;
    if (VirtualAlloc(H(addr), size, MEM_COMMIT, PAGE_EXECUTE_READWRITE)) return 0;
    /* not reserved by a parent (--child run by hand): reserve the 64 KB-aligned enclosing range */
    lo = addr & ~0xffffu;
    hi = (uint32_t)(((uint64_t)addr + size + 0xffff) & ~(uint64_t)0xffff);
    if (VirtualAlloc(H(lo), hi - lo, MEM_RESERVE, PAGE_NOACCESS) && VirtualAlloc(H(addr), size, MEM_COMMIT, PAGE_EXECUTE_READWRITE)) return 0;
    return -1;
}

static int map_module(mod_t *m, const rr_module_rec *rec)
{
    char path[MAX_PATH * 2];
    DWORD len = 0, i;
    uint8_t *file, *img = (uint8_t *)H(rec->base);
    IMAGE_DOS_HEADER *dos;
    IMAGE_NT_HEADERS32 *fnt;
    IMAGE_SECTION_HEADER *sec;
    uint32_t delta;

    memcpy(m->name, rec->name, sizeof m->name);
    m->name[sizeof m->name - 1] = 0;
    snprintf(path, sizeof path, "%s\\%s", g_game_dir, m->name);
    file = read_file(path, &len);
    if (!file) die(2, "ERROR: cannot read %s", path);
    dos = (IMAGE_DOS_HEADER *)file;
    fnt = (IMAGE_NT_HEADERS32 *)(file + dos->e_lfanew);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE || fnt->Signature != IMAGE_NT_SIGNATURE || fnt->FileHeader.Machine != IMAGE_FILE_MACHINE_I386)
        die(2, "ERROR: %s is not a 32-bit PE", path);
    if (fnt->FileHeader.TimeDateStamp != rec->timestamp || fnt->OptionalHeader.SizeOfImage != rec->size_of_image)
        die(2, "ERROR: %s does not match the capture (timestamp %08lx/%08x, size %#lx/%#x)", path,
            (unsigned long)fnt->FileHeader.TimeDateStamp, rec->timestamp, (unsigned long)fnt->OptionalHeader.SizeOfImage, rec->size_of_image);
    if (commit(rec->base, rec->size_of_image)) die(2, "ERROR: cannot commit %s at %#x", m->name, rec->base);
    memcpy(img, file, fnt->OptionalHeader.SizeOfHeaders);
    sec = IMAGE_FIRST_SECTION(fnt);
    for (i = 0; i < fnt->FileHeader.NumberOfSections; i++) {
        DWORD raw = sec[i].SizeOfRawData, vs = sec[i].Misc.VirtualSize;
        memset(img + sec[i].VirtualAddress, 0, vs > raw ? vs : raw);
        if (raw) memcpy(img + sec[i].VirtualAddress, file + sec[i].PointerToRawData, vs && raw > vs ? vs : raw);
    }
    m->img = img;
    m->nt = (IMAGE_NT_HEADERS32 *)(img + dos->e_lfanew);   /* dos points into file: read it before free */
    free(file);
    delta = rec->base - m->nt->OptionalHeader.ImageBase;
    if (delta) {
        IMAGE_DATA_DIRECTORY dd = m->nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_BASERELOC];
        uint8_t *p = img + dd.VirtualAddress, *end = p + dd.Size;
        while (p < end) {
            IMAGE_BASE_RELOCATION *b = (IMAGE_BASE_RELOCATION *)p;
            WORD *e = (WORD *)(b + 1);
            DWORD n = (b->SizeOfBlock - sizeof *b) / 2, k;
            if (!b->SizeOfBlock) break;
            for (k = 0; k < n; k++) {
                int type = e[k] >> 12;
                if (type == IMAGE_REL_BASED_HIGHLOW) *(DWORD *)(img + b->VirtualAddress + (e[k] & 0xfff)) += delta;
                else if (type != IMAGE_REL_BASED_ABSOLUTE) die(2, "ERROR: %s: relocation type %d", m->name, type);
            }
            p += b->SizeOfBlock;
        }
    }
    return 0;
}

static void bind_imports(mod_t *m)
{
    IMAGE_DATA_DIRECTORY dd = m->nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    IMAGE_IMPORT_DESCRIPTOR *d;
    if (!dd.Size) return;
    for (d = (IMAGE_IMPORT_DESCRIPTOR *)(m->img + dd.VirtualAddress); d->Name; d++) {
        const char *dll = (const char *)(m->img + d->Name);
        mod_t *own = own_module(dll);
        HMODULE h = own ? NULL : LoadLibraryA(dll);
        IMAGE_THUNK_DATA32 *ilt = (IMAGE_THUNK_DATA32 *)(m->img + (d->OriginalFirstThunk ? d->OriginalFirstThunk : d->FirstThunk));
        IMAGE_THUNK_DATA32 *iat = (IMAGE_THUNK_DATA32 *)(m->img + d->FirstThunk);
        if (!own && !h) die(2, "ERROR: %s imports %s, which does not load (%lu)", m->name, dll, (unsigned long)GetLastError());
        for (; ilt->u1.AddressOfData; ilt++, iat++) {
            const char *name = NULL;
            WORD ord = 0;
            void *addr = NULL;
            if (IMAGE_SNAP_BY_ORDINAL32(ilt->u1.Ordinal)) ord = (WORD)IMAGE_ORDINAL32(ilt->u1.Ordinal);
            else name = (const char *)((IMAGE_IMPORT_BY_NAME *)(m->img + ilt->u1.AddressOfData))->Name;
            if (name) addr = shim_for(dll, name);
            if (!addr) addr = own ? own_export(own, name, ord) : (void *)GetProcAddress(h, name ? name : (LPCSTR)(uintptr_t)ord);
            if (!addr) die(2, "ERROR: %s: unresolved import %s!%s#%u", m->name, dll, name ? name : "", ord);
            iat->u1.Function = G(addr);
        }
    }
}

/* Route a normally loaded DLL's msvcrt allocator imports to the shims (msvcp60.dll). */
static void patch_iat(HMODULE hm)
{
    uint8_t *img = (uint8_t *)hm;
    IMAGE_NT_HEADERS32 *nt;
    IMAGE_DATA_DIRECTORY dd;
    IMAGE_IMPORT_DESCRIPTOR *d;
    if (!hm) return;
    nt = (IMAGE_NT_HEADERS32 *)(img + ((IMAGE_DOS_HEADER *)img)->e_lfanew);
    dd = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    for (d = (IMAGE_IMPORT_DESCRIPTOR *)(img + dd.VirtualAddress); dd.Size && d->Name; d++) {
        const char *dll = (const char *)(img + d->Name);
        IMAGE_THUNK_DATA32 *ilt, *iat;
        if (_stricmp(dll, "msvcrt.dll") || !d->OriginalFirstThunk) continue;
        ilt = (IMAGE_THUNK_DATA32 *)(img + d->OriginalFirstThunk);
        iat = (IMAGE_THUNK_DATA32 *)(img + d->FirstThunk);
        for (; ilt->u1.AddressOfData; ilt++, iat++) {
            const char *name;
            void *fn;
            DWORD old;
            if (IMAGE_SNAP_BY_ORDINAL32(ilt->u1.Ordinal)) continue;
            name = (const char *)((IMAGE_IMPORT_BY_NAME *)(img + ilt->u1.AddressOfData))->Name;
            fn = shim_for(dll, name);
            if (!fn || fn == (void *)sh_fopen || fn == (void *)sh_fgets || fn == (void *)sh_fclose) continue;
            VirtualProtect(iat, 4, PAGE_READWRITE, &old);
            iat->u1.Function = G(fn);
            VirtualProtect(iat, 4, old, &old);
        }
    }
}

/* ---- platform callbacks ---------------------------------------------------------------- */

static int w_map(void *u, uint32_t addr, uint32_t size) { (void)u; return commit(addr, size); }
static int w_write(void *u, uint32_t addr, const void *src, uint32_t n) { (void)u; memcpy(H(addr), src, n); return 0; }
static int w_read(void *u, uint32_t addr, void *dst, uint32_t n) { (void)u; memcpy(dst, H(addr), n); return 0; }

static int w_load_modules(void *u, const rr_module_rec *mods, uint32_t n)
{
    HMODULE crt = LoadLibraryA("msvcrt.dll");
    uint32_t i;
    (void)u;
    if (!crt) die(2, "ERROR: msvcrt.dll does not load");
    real_new = (alloc1_t)GetProcAddress(crt, "??2@YAPAXI@Z");
    real_newa = (alloc1_t)GetProcAddress(crt, "??_U@YAPAXI@Z");
    real_malloc = (alloc1_t)GetProcAddress(crt, "malloc");
    real_calloc = (void *(__cdecl *)(size_t, size_t))GetProcAddress(crt, "calloc");
    real_realloc = (void *(__cdecl *)(void *, size_t))GetProcAddress(crt, "realloc");
    real_free = (free_t)GetProcAddress(crt, "free");
    real_delete = (free_t)GetProcAddress(crt, "??3@YAXPAX@Z");
    real_deletea = (free_t)GetProcAddress(crt, "??_V@YAXPAX@Z");
    real_fopen = (FILE * (__cdecl *)(const char *, const char *))GetProcAddress(crt, "fopen");
    real_fgets = (char *(__cdecl *)(char *, int, FILE *))GetProcAddress(crt, "fgets");
    real_fclose = (int(__cdecl *)(FILE *))GetProcAddress(crt, "fclose");
    g_nmod = (int)n;
    for (i = 0; i < n; i++) map_module(&g_mod[i], &mods[i]);
    for (i = 0; i < n; i++) bind_imports(&g_mod[i]);
    patch_iat(GetModuleHandleA("msvcp60.dll"));
    for (i = 0; i < n; i++) {   /* dependency order: sxlrt233, engine, Default (as in MODS) */
        BOOL(WINAPI * entry)(HINSTANCE, DWORD, LPVOID) =
            (BOOL(WINAPI *)(HINSTANCE, DWORD, LPVOID))(g_mod[i].img + g_mod[i].nt->OptionalHeader.AddressOfEntryPoint);
        if (!entry((HINSTANCE)g_mod[i].img, DLL_PROCESS_ATTACH, NULL)) die(2, "ERROR: DllMain of %s failed", g_mod[i].name);
        if (g_verbose) printf("  %s mapped at %#x, DllMain ok\n", g_mod[i].name, G(g_mod[i].img));
    }
    return 0;
}

static uint32_t w_export(void *u, const char *module, const char *name)
{
    mod_t *m = own_module(module);
    (void)u;
    return m ? G(own_export(m, name, 0)) : 0;
}

/* Stack switch: the export runs with ESP == captured ESP; CALL pushes our return address there. */
static volatile DWORD s_saved_esp, s_target, s_fn, s_eax;
static volatile WORD s_cw_in, s_cw_out, s_cw_host;
static PVOID volatile s_stack_base, s_stack_limit;

/* Not inlined: a real call tells the compiler that every caller-saved register (XMM included)
   is gone afterwards; the game code and the shims it calls use them freely. */
#ifdef _MSC_VER
__declspec(noinline)
#else
__attribute__((noinline))
#endif
static void call_switched(void)
{
#ifdef _MSC_VER
    __asm {
        pushad
        mov  s_saved_esp, esp
        fnstcw s_cw_host
        fninit
        fldcw s_cw_in
        mov  eax, s_fn
        mov  esp, s_target
        call eax
        mov  s_eax, eax
        fnstcw s_cw_out
        fninit
        fldcw s_cw_host
        mov  esp, s_saved_esp
        popad
    }
#else
    __asm__ __volatile__(
        "pushal\n\t"
        "movl %%esp, %0\n\t"
        "fnstcw %1\n\t"
        "fninit\n\t"
        "fldcw %2\n\t"
        "movl %3, %%eax\n\t"
        "movl %4, %%esp\n\t"
        "call *%%eax\n\t"
        "movl %%eax, %5\n\t"
        "fnstcw %6\n\t"
        "fninit\n\t"
        "fldcw %1\n\t"
        "movl %0, %%esp\n\t"
        "popal\n\t"
        : "+m"(s_saved_esp), "+m"(s_cw_host), "+m"(s_cw_in), "+m"(s_fn), "+m"(s_target), "+m"(s_eax), "+m"(s_cw_out)
        :
        : "memory", "cc", "xmm0", "xmm1", "xmm2", "xmm3", "xmm4", "xmm5", "xmm6", "xmm7");
#endif
}

static int w_call(void *u, uint32_t fn, uint32_t esp, uint16_t cw_in, uint32_t *eax, uint16_t *cw_out)
{
    NT_TIB *tib = (NT_TIB *)NtCurrentTeb();
    (void)u;
    s_stack_base = tib->StackBase; s_stack_limit = tib->StackLimit;
    s_fn = fn; s_target = esp + 4; s_cw_in = cw_in;
    /* the TEB's stack bounds follow ESP onto the captured stack for the duration of the call */
    tib->StackBase = H((esp + RR_PAGE) & ~(RR_PAGE - 1));
    tib->StackLimit = H((esp - RR_STACK_BELOW) & ~(RR_PAGE - 1));
    call_switched();
    tib = (NT_TIB *)NtCurrentTeb();
    tib->StackBase = s_stack_base; tib->StackLimit = s_stack_limit;
    *eax = s_eax; *cw_out = s_cw_out;
    return 0;
}

static void w_fatal(void *u, int code, const char *msg) { (void)u; die(code, "%s %s", code == 1 ? "MISMATCH" : "ERROR", msg); }
static void w_log(void *u, const char *msg) { (void)u; printf("%s\n", msg); fflush(stdout); }

static const char *where(uint32_t a, char *buf, size_t n)
{
    int i;
    for (i = 0; i < g_nmod; i++)
        if (g_mod[i].img && a >= G(g_mod[i].img) && a < G(g_mod[i].img) + g_mod[i].nt->OptionalHeader.SizeOfImage) {
            snprintf(buf, n, "%s+%#x", g_mod[i].name, a - G(g_mod[i].img));
            return buf;
        }
    {   /* a system module (ntdll, msvcrt, ...) or the driver itself */
        HMODULE h;
        char path[MAX_PATH], *base;
        if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                               (LPCSTR)H(a), &h) && GetModuleFileNameA(h, path, sizeof path)) {
            base = strrchr(path, '\\');
            snprintf(buf, n, "%s+%#x", base ? base + 1 : path, a - G(h));
            return buf;
        }
    }
    snprintf(buf, n, "%#x", a);
    return buf;
}

/* Return-address candidates on the faulting stack: values that point into a known module. */
static void print_stack(const CONTEXT *c)
{
    const uint32_t *sp = (const uint32_t *)H(c->Esp);
    MEMORY_BASIC_INFORMATION mbi;
    char w[96];
    int i, found = 0;
    if (!VirtualQuery(sp, &mbi, sizeof mbi) || mbi.State != MEM_COMMIT) return;
    for (i = 0; i < 1024 && found < 16 && (const uint8_t *)(sp + i + 1) <= (const uint8_t *)mbi.BaseAddress + mbi.RegionSize; i++) {
        where(sp[i], w, sizeof w);
        if (!sp[i] || (w[0] == '0' && w[1] == 'x')) continue;
        printf("  [esp+%#x] %s\n", (unsigned)(i * 4), w);
        found++;
    }
}

static LONG CALLBACK on_exception(EXCEPTION_POINTERS *ep)
{
    DWORD code = ep->ExceptionRecord->ExceptionCode;
    char w[96];
    if (code == 0x406D1388 /* thread naming */ || code == DBG_PRINTEXCEPTION_C) return EXCEPTION_CONTINUE_SEARCH;
    if (g_verbose) print_stack(ep->ContextRecord);
    if (code == 0xE06D7363) die(2, "ERROR: C++ exception thrown at %s during the replay", where(G(ep->ExceptionRecord->ExceptionAddress), w, sizeof w));
    if (code == EXCEPTION_ACCESS_VIOLATION)
        die(2, "ERROR: access violation at %s (%s %#x)", where(ep->ContextRecord->Eip, w, sizeof w),
            ep->ExceptionRecord->ExceptionInformation[0] ? "writing" : "reading", (unsigned)ep->ExceptionRecord->ExceptionInformation[1]);
    die(2, "ERROR: exception %#lx at %s", (unsigned long)code, where(ep->ContextRecord->Eip, w, sizeof w));
    return EXCEPTION_CONTINUE_SEARCH;
}

/* ---- parent / child -------------------------------------------------------------------- */

static int reserve_ranges(const rr_pack *pk, rr_range **out, uint32_t *n)
{
    uint32_t i, m = 0;
    rr_range *r;
    if (rr_needed_ranges(pk, &r, n)) return -1;
    for (i = 0; i < *n; i++) {     /* 64 KB allocation granularity, then merge again */
        r[i].lo &= ~0xffffu;
        r[i].hi = (uint32_t)(((uint64_t)r[i].hi + 0xffff) & ~(uint64_t)0xffff);
        if (m && r[i].lo <= r[m - 1].hi) { if (r[i].hi > r[m - 1].hi) r[m - 1].hi = r[i].hi; }
        else r[m++] = r[i];
    }
    *out = r; *n = m;
    return 0;
}

static int run_child(const char *pack_path, const char *diff_dir)
{
    rr_pack pk;
    rr_options opt;
    rr_platform pf = { NULL, w_map, w_write, w_read, w_load_modules, w_export, w_call, w_fatal, w_log };
    char err[256];
    rr_range *r;
    uint32_t n, i;
    AddVectoredExceptionHandler(1, on_exception);
    if (rr_pack_load(&pk, pack_path, err, sizeof err)) die(2, "ERROR: %s", err);
    /* Commit the reserved ranges up front so nothing else lands there. */
    if (!reserve_ranges(&pk, &r, &n)) {
        for (i = 0; i < n; i++) if (commit(r[i].lo, r[i].hi - r[i].lo)) die(2, "ERROR: cannot commit %#x-%#x", r[i].lo, r[i].hi);
        free(r);
    }
    memset(&opt, 0, sizeof opt);
    opt.verbose = g_verbose;
    opt.diff_dir = diff_dir;
    i = (uint32_t)rr_replay(&pf, &pk, &opt);
    if (g_verbose) printf("  %u OutputDebugStringA lines during the replay (%u in the capture)\n", g_dbg_lines, pk.ndbgs);
    return (int)i;
}

static int run_parent(const char *self, const char *pack_path, const char *child_args)
{
    rr_pack pk;
    char err[256], cmd[4096];
    rr_range *r;
    uint32_t n, i;
    int attempt;
    if (rr_pack_load(&pk, pack_path, err, sizeof err)) { printf("ERROR %s\n", err); return 2; }
    if (reserve_ranges(&pk, &r, &n)) { printf("ERROR out of memory\n"); return 2; }
    rr_pack_free(&pk);
    snprintf(cmd, sizeof cmd, "\"%s\" --child %s \"%s\"", self, child_args, pack_path);
    for (attempt = 0; attempt < 10; attempt++) {
        STARTUPINFOA si;
        PROCESS_INFORMATION pi;
        DWORD code = 2;
        uint32_t clash = 0, clash_at = 0;
        memset(&si, 0, sizeof si);
        si.cb = sizeof si;
        if (!CreateProcessA(NULL, cmd, NULL, NULL, TRUE, CREATE_SUSPENDED, NULL, NULL, &si, &pi)) {
            printf("ERROR CreateProcess failed (%lu)\n", (unsigned long)GetLastError());
            free(r);
            return 2;
        }
        for (i = 0; i < n && !clash; i++) {
            uint32_t a = r[i].lo;
            while (a < r[i].hi) {
                MEMORY_BASIC_INFORMATION mbi;
                if (!VirtualQueryEx(pi.hProcess, H(a), &mbi, sizeof mbi)) { clash = 1; clash_at = a; break; }
                if (mbi.State != MEM_FREE) { clash = mbi.Type ? mbi.Type : 1; clash_at = a; break; }
                a = G(mbi.BaseAddress) + (uint32_t)mbi.RegionSize;
                if (!a) break;
            }
        }
        for (i = 0; i < n && !clash; i++)
            if (G(VirtualAllocEx(pi.hProcess, H(r[i].lo), r[i].hi - r[i].lo, MEM_RESERVE, PAGE_NOACCESS)) != r[i].lo) { clash = 1; clash_at = r[i].lo; }
        if (clash) {
            if (g_verbose) printf("  address %#x is taken in the child (type %#x); retrying\n", clash_at, clash);
            TerminateProcess(pi.hProcess, 99);
            WaitForSingleObject(pi.hProcess, INFINITE);
            CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
            continue;
        }
        ResumeThread(pi.hThread);
        WaitForSingleObject(pi.hProcess, INFINITE);
        GetExitCodeProcess(pi.hProcess, &code);
        CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
        free(r);
        return code == 0 ? 0 : code == 1 ? 1 : 2;
    }
    free(r);
    printf("ERROR %s: the needed address ranges stayed taken in 10 child processes\n", pack_path);
    return 2;
}

int main(int argc, char **argv)
{
    char self[MAX_PATH], child_args[1024] = "";
    const char *diff_dir = NULL;
    int i, child = 0, worst = 0, npacks = 0, npass = 0;
    for (i = 1; i < argc && argv[i][0] == '-'; i++) {
        if (!strcmp(argv[i], "--child")) child = 1;
        else if (!strcmp(argv[i], "-v")) g_verbose++;
        else if (!strcmp(argv[i], "--game") && i + 1 < argc) snprintf(g_game_dir, sizeof g_game_dir, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--diff-dir") && i + 1 < argc) diff_dir = argv[++i];
        else { printf("usage: %s [--game DIR] [--diff-dir DIR] [-v] PACK.rrp...\n", argv[0]); return 2; }
    }
    if (i >= argc) { printf("usage: %s [--game DIR] [--diff-dir DIR] [-v] PACK.rrp...\n", argv[0]); return 2; }
    if (diff_dir) CreateDirectoryA(diff_dir, NULL);   /* may already exist */
    if (child) return run_child(argv[i], diff_dir);
    GetModuleFileNameA(NULL, self, sizeof self);
    snprintf(child_args, sizeof child_args, "--game \"%s\"%s%s%s%s%s", g_game_dir, diff_dir ? " --diff-dir \"" : "",
             diff_dir ? diff_dir : "", diff_dir ? "\"" : "", g_verbose ? " -v" : "", g_verbose > 1 ? " -v" : "");
    for (; i < argc; i++) {
        int rc = run_parent(self, argv[i], child_args);
        npacks++;
        if (rc == 0) npass++;
        if (rc > worst) worst = rc;
        fflush(stdout);
    }
    printf("%d/%d packs passed\n", npass, npacks);
    return worst;
}
