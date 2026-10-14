/* crtvec.c - run the msvcrt behaviour vectors against Windows' own msvcrt.dll.
 *
 * Build (x86 Native Tools prompt, MSVC; the /arch:IA32 keeps the harness's own
 * floating point on the x87 and away from the values under test):
 *     cl /nologo /O1 /arch:IA32 /W3 crtvec.c
 * Run:
 *     crtvec.exe vectors.tsv results.tsv [--resume]
 *
 * Every function under test is called through GetProcAddress on the system
 * msvcrt.dll (C:\Windows\SysWOW64\msvcrt.dll for a 32-bit process), never
 * through the compiler's own C runtime, which is a different library. Values
 * cross as bit patterns: doubles are passed to variadic functions as two
 * dwords, so a signalling NaN is never loaded into an x87 register on the way.
 *
 * A call that raises an exception is reported as EXC. A call that triggers
 * msvcrt's invalid-parameter handler has "IPH" appended. If a call kills the
 * process outright (a fast-fail), run again with --resume: the vector after
 * the last one written is recorded as CRASH and the run continues after it.
 * run.cmd does that in a loop.
 *
 * Output: one line per vector, "id<TAB>op<TAB>field...", formats in README.md.
 * This file contains no game data and no Microsoft code.
 */
#define _CRT_SECURE_NO_WARNINGS
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned __int64 u64;

static HMODULE g_crt;
static FILE *g_out;
static volatile int g_iph;

static void *sym(const char *name) {
    void *p = (void *)GetProcAddress(g_crt, name);
    if (!p) {
        fprintf(stderr, "msvcrt.dll has no %s\n", name);
        exit(2);
    }
    return p;
}

/* ---------------------------------------------------------------- output */
static char g_line[1 << 16];
static size_t g_len;

static void put_raw(const char *s) {
    size_t n = strlen(s);
    if (g_len + n + 2 < sizeof g_line) {
        memcpy(g_line + g_len, s, n);
        g_len += n;
    }
}
static void field(const char *s) {
    put_raw("\t");
    put_raw(s);
}
static void field_hex(const void *p, size_t n) {
    static const char digits[] = "0123456789abcdef";
    char buf[2 * 4096 + 1];
    size_t i;
    if (n > 4096)
        n = 4096;
    for (i = 0; i < n; ++i) {
        buf[2 * i] = digits[((const u8 *)p)[i] >> 4];
        buf[2 * i + 1] = digits[((const u8 *)p)[i] & 15];
    }
    buf[2 * n] = 0;
    field(buf);
}
static void field_str(const char *s) {
    if (!s)
        field("NULL");
    else
        field_hex(s, strlen(s));
}
static void field_int(__int64 v) {
    char buf[32];
    sprintf(buf, "%I64d", v);
    field(buf);
}
static void field_u32x(u32 v, int width) {
    char buf[16];
    sprintf(buf, "%0*x", width, v);
    field(buf);
}
static void field_bits64(const void *p) {
    u64 v;
    char buf[24];
    memcpy(&v, p, 8);
    sprintf(buf, "%016I64x", v);
    field(buf);
}
static void field_bits80(const u8 *p) {
    char buf[24];
    int i;
    for (i = 0; i < 10; ++i)
        sprintf(buf + 2 * i, "%02x", p[9 - i]);
    field(buf);
}

/* ---------------------------------------------------------------- input */
static int unhex(const char *s, u8 *out, int cap) {
    int n = 0;
    while (s[0] && s[1] && n < cap) {
        int hi = s[0] <= '9' ? s[0] - '0' : (s[0] | 32) - 'a' + 10;
        int lo = s[1] <= '9' ? s[1] - '0' : (s[1] | 32) - 'a' + 10;
        out[n++] = (u8)(hi << 4 | lo);
        s += 2;
    }
    out[n] = 0;
    return n;
}
static u64 hex64(const char *s) {
    return _strtoui64(s, NULL, 16);
}

/* ---------------------------------------------------------------- msvcrt */
typedef int(__cdecl *pf_rand)(void);
typedef void(__cdecl *pf_srand)(unsigned);
typedef double(__cdecl *pf_strtod)(const char *, char **);
typedef double(__cdecl *pf_atof)(const char *);
typedef int(__cdecl *pf_sscanf)(const char *, const char *, ...);
typedef int(__cdecl *pf_sprintf)(char *, const char *, ...);
typedef int(__cdecl *pf_snprintf)(char *, size_t, const char *, ...);
typedef long(__cdecl *pf_strtol)(const char *, char **, int);
typedef unsigned long(__cdecl *pf_strtoul)(const char *, char **, int);
typedef int(__cdecl *pf_atoi)(const char *);
typedef __int64(__cdecl *pf_atoi64)(const char *);
typedef char *(__cdecl *pf_itoa)(int, char *, int);
typedef int *(__cdecl *pf_errno)(void);
typedef char *(__cdecl *pf_setlocale)(int, const char *);
typedef char *(__cdecl *pf_localeconv)(void);
typedef int(__cdecl *pf_int_int)(int);
typedef unsigned short(__cdecl *pf_w_w)(unsigned short);
typedef int(__cdecl *pf_iswctype)(unsigned short, unsigned short);
typedef size_t(__cdecl *pf_mbstowcs)(unsigned short *, const char *, size_t);
typedef size_t(__cdecl *pf_wcstombs)(char *, const unsigned short *, size_t);
typedef unsigned char *(__cdecl *pf_mbscase)(unsigned char *);
typedef int(__cdecl *pf_cmp2)(const void *, const void *);
typedef int(__cdecl *pf_cmp3)(const void *, const void *, size_t);
typedef char *(__cdecl *pf_asctime)(const int *);
typedef size_t(__cdecl *pf_strftime)(char *, size_t, const char *, const int *);
typedef int *(__cdecl *pf_gmtime)(const long *);
typedef void *(__cdecl *pf_malloc)(size_t);
typedef size_t(__cdecl *pf_msize)(void *);
typedef void(__cdecl *pf_free)(void *);
typedef void(__cdecl *pf_splitpath)(const char *, char *, char *, char *, char *);
typedef const char *(__fastcall *pf_tiname)(void *self, void *unused); /* __thiscall stand-in */
typedef void *(__cdecl *pf_fopen)(const char *, const char *);
typedef char *(__cdecl *pf_fgets)(char *, int, void *);
typedef long(__cdecl *pf_ftell)(void *);
typedef size_t(__cdecl *pf_fread)(void *, size_t, size_t, void *);
typedef int(__cdecl *pf_fseek)(void *, long, int);
typedef int(__cdecl *pf_fgetc)(void *);
typedef int(__cdecl *pf_ungetc)(int, void *);
typedef void(__cdecl *pf_rewind)(void *);
typedef int(__cdecl *pf_fclose)(void *);
typedef void(__cdecl *pf_iph)(const unsigned short *, const unsigned short *,
                              const unsigned short *, unsigned, uintptr_t);
typedef pf_iph(__cdecl *pf_set_iph)(pf_iph);

static pf_errno p_errno;
static pf_setlocale p_setlocale;

static void __cdecl on_invalid_parameter(const unsigned short *e, const unsigned short *f,
                                         const unsigned short *file, unsigned line,
                                         uintptr_t reserved) {
    (void)e;
    (void)f;
    (void)file;
    (void)line;
    (void)reserved;
    g_iph = 1;
}

static void set_locale(const char *name) {
    p_setlocale(0, name);
}
static void clear_errno(void) {
    *p_errno() = 0;
}
static int get_errno(void) {
    return *p_errno();
}

/* x87 calls. Operands are copied as integers so no load quietens a NaN. */
static void call_d_d(void *fn, u16 cw, const u8 *x, u8 *r64, u8 *r80) {
    u16 old;
    u32 x0, x1;
    memcpy(&x0, x, 4);
    memcpy(&x1, x + 4, 4);
    __asm {
        fnstcw old
        fnclex
        fldcw cw
        sub esp, 8
        mov eax, x0
        mov [esp], eax
        mov eax, x1
        mov [esp+4], eax
        call fn
        add esp, 8
        mov eax, r80
        fld st(0)
        fstp tbyte ptr [eax]
        mov eax, r64
        fstp qword ptr [eax]
        fldcw old
    }
}
static void call_dd_d(void *fn, u16 cw, const u8 *x, const u8 *y, u8 *r64, u8 *r80) {
    u16 old;
    u32 x0, x1, y0, y1;
    memcpy(&x0, x, 4);
    memcpy(&x1, x + 4, 4);
    memcpy(&y0, y, 4);
    memcpy(&y1, y + 4, 4);
    __asm {
        fnstcw old
        fnclex
        fldcw cw
        sub esp, 16
        mov eax, x0
        mov [esp], eax
        mov eax, x1
        mov [esp+4], eax
        mov eax, y0
        mov [esp+8], eax
        mov eax, y1
        mov [esp+12], eax
        call fn
        add esp, 16
        mov eax, r80
        fld st(0)
        fstp tbyte ptr [eax]
        mov eax, r64
        fstp qword ptr [eax]
        fldcw old
    }
}
static void call_di_d(void *fn, u16 cw, const u8 *x, int e, u8 *r64, u8 *r80) {
    u16 old;
    u32 x0, x1;
    memcpy(&x0, x, 4);
    memcpy(&x1, x + 4, 4);
    __asm {
        fnstcw old
        fnclex
        fldcw cw
        sub esp, 12
        mov eax, x0
        mov [esp], eax
        mov eax, x1
        mov [esp+4], eax
        mov eax, e
        mov [esp+8], eax
        call fn
        add esp, 12
        mov eax, r80
        fld st(0)
        fstp tbyte ptr [eax]
        mov eax, r64
        fstp qword ptr [eax]
        fldcw old
    }
}
static int call_d_i(void *fn, u16 cw, const u8 *x) {
    u16 old;
    u32 x0, x1;
    int r;
    memcpy(&x0, x, 4);
    memcpy(&x1, x + 4, 4);
    __asm {
        fnstcw old
        fldcw cw
        sub esp, 8
        mov eax, x0
        mov [esp], eax
        mov eax, x1
        mov [esp+4], eax
        call fn
        add esp, 8
        mov r, eax
        fldcw old
    }
    return r;
}
/* _CI* intrinsics: operands on the x87 stack, the first argument deepest. */
static void call_ci(void *fn, int two, u16 cw, const u8 *a, const u8 *b, u8 *r64, u8 *r80) {
    u16 old;
    __asm {
        fnstcw old
        fnclex
        fldcw cw
        mov eax, a
        fld qword ptr [eax]
        mov ecx, two
        test ecx, ecx
        jz one
        mov eax, b
        fld qword ptr [eax]
    one:
        call fn
        mov eax, r80
        fld st(0)
        fstp tbyte ptr [eax]
        mov eax, r64
        fstp qword ptr [eax]
        fldcw old
    }
}
static u64 call_ftol(void *fn, u16 cw, const u8 *x) {
    u16 old;
    u32 lo, hi;
    __asm {
        fnstcw old
        fldcw cw
        mov eax, x
        fld qword ptr [eax]
        call fn
        mov lo, eax
        mov hi, edx
        fldcw old
    }
    return (u64)hi << 32 | lo;
}

/* ---------------------------------------------------------------- threads */
static pf_rand p_rand_for_thread;
static int g_thread_vals[64];
static int g_thread_n;
static DWORD WINAPI rand_thread(LPVOID unused) {
    int i;
    (void)unused;
    for (i = 0; i < g_thread_n; ++i)
        g_thread_vals[i] = p_rand_for_thread();
    return 0;
}

/* ---------------------------------------------------------------- the ops */
#define MAXF 32
static char *g_f[MAXF];
static int g_nf;

static void run_vector(void) {
    const char *op = g_f[1];
    static u8 s1[8192], s2[8192], s3[8192];
    static char buf[8192];
    if (!strcmp(op, "rand")) {
        pf_srand p_srand = (pf_srand)sym("srand");
        pf_rand p_rand = (pf_rand)sym("rand");
        int i, n = atoi(g_f[3]);
        p_srand((unsigned)_strtoui64(g_f[2], NULL, 10));
        for (i = 0; i < n; ++i)
            field_int(p_rand());
    } else if (!strcmp(op, "rand_thread")) {
        HANDLE h;
        int i;
        p_rand_for_thread = (pf_rand)sym("rand");
        g_thread_n = atoi(g_f[2]);
        h = CreateThread(NULL, 0, rand_thread, NULL, 0, NULL);
        WaitForSingleObject(h, INFINITE);
        CloseHandle(h);
        for (i = 0; i < g_thread_n; ++i)
            field_int(g_thread_vals[i]);
    } else if (!strcmp(op, "strtod") || !strcmp(op, "atof")) {
        double r;
        char *end = NULL;
        set_locale(g_f[2]);
        unhex(g_f[3], s1, sizeof s1 - 1);
        clear_errno();
        if (op[0] == 's')
            r = ((pf_strtod)sym("strtod"))((const char *)s1, &end);
        else
            r = ((pf_atof)sym("atof"))((const char *)s1);
        field_bits64(&r);
        if (op[0] == 's')
            field_int(end - (char *)s1);
        field_int(get_errno());
    } else if (!strcmp(op, "sscanf_f") || !strcmp(op, "sscanf_lf") || !strcmp(op, "sscanf_d")) {
        pf_sscanf p = (pf_sscanf)sym("sscanf");
        int ret, n = -1;
        u32 v32 = 0xcccccccc;
        u64 v64 = 0xccccccccccccccccull;
        if (op[7] != 'd')
            set_locale(g_f[2]);
        unhex(g_f[op[7] == 'd' ? 2 : 3], s1, sizeof s1 - 1);
        if (!strcmp(op, "sscanf_f")) {
            ret = p((const char *)s1, "%f%n", &v32, &n);
            field_int(ret);
            field_u32x(v32, 8);
        } else if (!strcmp(op, "sscanf_lf")) {
            ret = p((const char *)s1, "%lf%n", &v64, &n);
            field_int(ret);
            field_bits64(&v64);
        } else {
            ret = p((const char *)s1, "%d%n", &v32, &n);
            field_int(ret);
            field_int((int)v32);
        }
        field_int(n);
    } else if (!strcmp(op, "sprintf_d")) {
        u64 bits = hex64(g_f[4]);
        int ret;
        set_locale(g_f[2]);
        unhex(g_f[3], s1, sizeof s1 - 1);
        memset(buf, 0, sizeof buf);
        ret = ((pf_sprintf)sym("sprintf"))(buf, (const char *)s1, (u32)bits, (u32)(bits >> 32));
        field_str(buf);
        field_int(ret);
    } else if (!strcmp(op, "sprintf_i")) {
        u32 v = (u32)_strtoui64(g_f[3], NULL, 10);
        int ret;
        unhex(g_f[2], s1, sizeof s1 - 1);
        memset(buf, 0, sizeof buf);
        ret = ((pf_sprintf)sym("sprintf"))(buf, (const char *)s1, v);
        field_str(buf);
        field_int(ret);
    } else if (!strcmp(op, "sprintf_i64")) {
        u64 v = hex64(g_f[3]);
        int ret;
        unhex(g_f[2], s1, sizeof s1 - 1);
        memset(buf, 0, sizeof buf);
        ret = ((pf_sprintf)sym("sprintf"))(buf, (const char *)s1, (u32)v, (u32)(v >> 32));
        field_str(buf);
        field_int(ret);
    } else if (!strcmp(op, "sprintf_s")) {
        int ret;
        unhex(g_f[2], s1, sizeof s1 - 1);
        unhex(g_f[3], s2, sizeof s2 - 1);
        memset(buf, 0, sizeof buf);
        ret = ((pf_sprintf)sym("sprintf"))(buf, (const char *)s1, (const char *)s2);
        field_str(buf);
        field_int(ret);
    } else if (!strcmp(op, "snprintf")) {
        int count = atoi(g_f[2]), ret;
        unhex(g_f[3], s1, sizeof s1 - 1);
        unhex(g_f[4], s2, sizeof s2 - 1);
        memset(buf, 0xcc, 64);
        ret = ((pf_snprintf)sym("_snprintf"))(buf, (size_t)count, (const char *)s1,
                                              (const char *)s2);
        field_int(ret);
        field_hex(buf, (size_t)count + 4);
    } else if (!strcmp(op, "strtol") || !strcmp(op, "strtoul")) {
        char *end = NULL;
        int base = atoi(g_f[3]);
        unhex(g_f[2], s1, sizeof s1 - 1);
        clear_errno();
        if (!strcmp(op, "strtol"))
            field_int(((pf_strtol)sym("strtol"))((const char *)s1, &end, base));
        else
            field_int((__int64)((pf_strtoul)sym("strtoul"))((const char *)s1, &end, base));
        field_int(end - (char *)s1);
        field_int(get_errno());
    } else if (!strcmp(op, "atoi")) {
        unhex(g_f[2], s1, sizeof s1 - 1);
        field_int(((pf_atoi)sym("atoi"))((const char *)s1));
    } else if (!strcmp(op, "atoi64")) {
        unhex(g_f[2], s1, sizeof s1 - 1);
        field_int(((pf_atoi64)sym("_atoi64"))((const char *)s1));
    } else if (!strcmp(op, "itoa")) {
        memset(buf, 0, 64);
        ((pf_itoa)sym("_itoa"))(atoi(g_f[2]), buf, atoi(g_f[3]));
        field_str(buf);
    } else if (!strcmp(op, "math")) {
        u8 x[8], y[8], r64[8], r80[10];
        u16 cw = (u16)strtoul(g_f[3], NULL, 16);
        u64 xb = hex64(g_f[4]);
        void *fn = sym(g_f[2]);
        memcpy(x, &xb, 8);
        clear_errno();
        if (!strcmp(g_f[2], "pow")) {
            u64 yb = hex64(g_f[5]);
            memcpy(y, &yb, 8);
            call_dd_d(fn, cw, x, y, r64, r80);
        } else if (!strcmp(g_f[2], "ldexp")) {
            call_di_d(fn, cw, x, atoi(g_f[5]), r64, r80);
        } else if (!strcmp(g_f[2], "_finite")) {
            int r = call_d_i(fn, cw, x);
            memset(r64, 0, 8);
            memcpy(r64, &r, 4);
            memset(r80, 0, 10);
        } else {
            call_d_d(fn, cw, x, r64, r80);
        }
        field_bits64(r64);
        field_bits80(r80);
        field_int(get_errno());
    } else if (!strcmp(op, "ci")) {
        u8 a[8], b[8], r64[8], r80[10];
        u16 cw = (u16)strtoul(g_f[3], NULL, 16);
        u64 ab = hex64(g_f[4]), bb = hex64(g_f[5]);
        int two = !strcmp(g_f[2], "_CIpow") || !strcmp(g_f[2], "_CIatan2");
        memcpy(a, &ab, 8);
        memcpy(b, &bb, 8);
        call_ci(sym(g_f[2]), two, cw, a, b, r64, r80);
        field_bits64(r64);
        field_bits80(r80);
    } else if (!strcmp(op, "ftol")) {
        u8 x[8];
        u64 xb = hex64(g_f[3]), r;
        memcpy(x, &xb, 8);
        r = call_ftol(sym("_ftol"), (u16)strtoul(g_f[2], NULL, 16), x);
        field_bits64(&r);
    } else if (!strcmp(op, "ctype")) {
        static const char *names[] = {"isalnum", "isalpha", "isdigit", "isspace", "isxdigit",
                                      "tolower", "toupper"};
        int c = atoi(g_f[3]), i;
        set_locale(g_f[2]);
        for (i = 0; i < 7; ++i) {
            int r = ((pf_int_int)sym(names[i]))(c);
            if (i < 5)
                field_u32x((u32)r, 1);
            else
                field_int(r);
        }
    } else if (!strcmp(op, "wctype")) {
        unsigned short w = (unsigned short)atoi(g_f[3]);
        set_locale(g_f[2]);
        field_u32x(((pf_w_w)sym("towupper"))(w), 4);
        field_u32x(((pf_w_w)sym("towlower"))(w), 4);
        field_u32x((u32)((pf_iswctype)sym("iswctype"))(w, 0x1ff), 1);
    } else if (!strcmp(op, "mbconv")) {
        unsigned short w[4] = {0xcccc, 0xcccc, 0xcccc, 0xcccc};
        char in[2], back[4] = {(char)0xcc, (char)0xcc, (char)0xcc, (char)0xcc};
        size_t r;
        set_locale(g_f[2]);
        in[0] = (char)atoi(g_f[3]);
        in[1] = 0;
        r = ((pf_mbstowcs)sym("mbstowcs"))(w, in, 4);
        field_int((__int64)(int)r);
        field_u32x(w[0], 4);
        r = ((pf_wcstombs)sym("wcstombs"))(back, w, 4);
        field_int((__int64)(int)r);
        field_u32x((u8)back[0], 2);
    } else if (!strcmp(op, "mbcase")) {
        int n;
        set_locale(g_f[2]);
        n = unhex(g_f[3], s1, sizeof s1 - 1);
        memcpy(s2, s1, (size_t)n + 1);
        ((pf_mbscase)sym("_mbsupr"))(s2);
        field_hex(s2, (size_t)n);
        memcpy(s2, s1, (size_t)n + 1);
        ((pf_mbscase)sym("_mbslwr"))(s2);
        field_hex(s2, (size_t)n);
    } else if (!strcmp(op, "cmp")) {
        const char *fn = g_f[2];
        int na, nb, r, n = atoi(g_f[6]);
        set_locale(g_f[3]);
        na = unhex(g_f[4], s1, sizeof s1 - 1);
        nb = unhex(g_f[5], s2, sizeof s2 - 1);
        if (!strcmp(fn, "wcscmp")) {
            unsigned short wa[256], wb[256];
            int i;
            for (i = 0; i <= na && i < 255; ++i)
                wa[i] = s1[i];
            for (i = 0; i <= nb && i < 255; ++i)
                wb[i] = s2[i];
            r = ((pf_cmp2)sym(fn))(wa, wb);
        } else if (!strcmp(fn, "strcmp") || !strcmp(fn, "_stricmp") || !strcmp(fn, "_mbscmp") ||
                   !strcmp(fn, "_mbsicmp")) {
            r = ((pf_cmp2)sym(fn))(s1, s2);
        } else {
            r = ((pf_cmp3)sym(fn))(s1, s2, (size_t)n);
        }
        field_int(r);
    } else if (!strcmp(op, "setlocale")) {
        char *r, *lc;
        int i;
        set_locale("C");
        unhex(g_f[3], s1, sizeof s1 - 1);
        r = p_setlocale(atoi(g_f[2]), (const char *)s1);
        field_str(r);
        field_str(p_setlocale(0, NULL));
        lc = ((pf_localeconv)sym("localeconv"))();
        for (i = 0; i < 10; ++i)
            field_str(*(char **)(lc + 4 * i));
        for (i = 0; i < 8; ++i)
            field_int((u8)lc[40 + i]);
    } else if (!strcmp(op, "asctime")) {
        int tm[9], i;
        for (i = 0; i < 9; ++i)
            tm[i] = atoi(g_f[2 + i]);
        field_str(((pf_asctime)sym("asctime"))(tm));
    } else if (!strcmp(op, "strftime")) {
        int tm[9], i;
        size_t r, max = (size_t)atoi(g_f[4]);
        set_locale(g_f[2]);
        unhex(g_f[3], s1, sizeof s1 - 1);
        for (i = 0; i < 9; ++i)
            tm[i] = atoi(g_f[5 + i]);
        memset(buf, 0, 512);
        r = ((pf_strftime)sym("strftime"))(buf, max, (const char *)s1, tm);
        field_int((__int64)r);
        field_str(r ? buf : "");
    } else if (!strcmp(op, "gmtime")) {
        long t = atol(g_f[2]);
        int *tm = ((pf_gmtime)sym("gmtime"))(&t), i;
        if (!tm)
            field("NULL");
        else
            for (i = 0; i < 9; ++i)
                field_int(tm[i]);
    } else if (!strcmp(op, "msize")) {
        void *p = ((pf_malloc)sym("malloc"))((size_t)atoi(g_f[2]));
        field_int(p ? (__int64)((pf_msize)sym("_msize"))(p) : -1);
        field_int((__int64)((uintptr_t)p & 15));
        ((pf_free)sym("free"))(p);
    } else if (!strcmp(op, "splitpath")) {
        static char dr[1024], di[1024], fn[1024], ex[1024];
        unhex(g_f[2], s1, sizeof s1 - 1);
        memset(dr, 0, sizeof dr);
        memset(di, 0, sizeof di);
        memset(fn, 0, sizeof fn);
        memset(ex, 0, sizeof ex);
        ((pf_splitpath)sym("_splitpath"))((const char *)s1, dr, di, fn, ex);
        field_str(dr);
        field_str(di);
        field_str(fn);
        field_str(ex);
    } else if (!strcmp(op, "typename")) {
        struct {
            void *vftable;
            char *data;
            char name[512];
        } ti;
        ti.vftable = (void *)&ti; /* any non-null value; name() does not use it */
        ti.data = NULL;
        unhex(g_f[2], (u8 *)ti.name, sizeof ti.name - 1);
        field_str(((pf_tiname)sym("?name@type_info@@QBEPBDXZ"))(&ti, NULL));
    } else if (!strcmp(op, "textfile")) {
        char path[MAX_PATH], line[16];
        int n, i;
        HANDLE h;
        DWORD wrote;
        void *f;
        pf_ftell p_ftell = (pf_ftell)sym("ftell");
        pf_fgetc p_fgetc = (pf_fgetc)sym("fgetc");
        unhex(g_f[2], s1, sizeof s1 - 1);
        n = unhex(g_f[3], s2, sizeof s2 - 1);
        GetTempPathA(MAX_PATH - 20, path);
        strcat(path, "crtvec.tmp");
        h = CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
        WriteFile(h, s2, (DWORD)n, &wrote, NULL);
        CloseHandle(h);
        f = ((pf_fopen)sym("fopen"))(path, (const char *)s1);
        if (!f) {
            field("NULL");
        } else {
            for (i = 0; i < 64; ++i) {
                char *r = ((pf_fgets)sym("fgets"))(line, (int)sizeof line, f);
                field_str(r ? line : NULL);
                field_int(p_ftell(f));
                if (!r)
                    break;
            }
            field_int((((int *)f)[3] & 0x10) != 0); /* _IOEOF, as feof reads it */
            ((pf_rewind)sym("rewind"))(f);
            memset(s3, 0, 256);
            field_int((__int64)((pf_fread)sym("fread"))(s3, 1, 200, f));
            field_str((const char *)s3); /* up to the first NUL */
            field_int(p_ftell(f));
            field_int(((pf_fseek)sym("fseek"))(f, 3, 0));
            field_int(p_fgetc(f));
            field_int(((pf_ungetc)sym("ungetc"))('Q', f));
            field_int(p_ftell(f));
            field_int(p_fgetc(f));
            field_int(p_fgetc(f));
            ((pf_fclose)sym("fclose"))(f);
        }
        DeleteFileA(path);
    } else {
        field("UNKNOWN_OP");
    }
}

/* ---------------------------------------------------------------- main */
static int split(char *line) {
    char *p = line;
    g_nf = 0;
    while (g_nf < MAXF) {
        g_f[g_nf++] = p;
        p = strchr(p, '\t');
        if (!p)
            break;
        *p++ = 0;
    }
    return g_nf;
}

static long last_done_id(const char *results) {
    FILE *f = fopen(results, "rb");
    char line[1 << 16];
    long last = -1;
    if (!f)
        return -1;
    while (fgets(line, sizeof line, f))
        if (line[0] >= '0' && line[0] <= '9' && strchr(line, '\n'))
            last = atol(line);
    fclose(f);
    return last;
}

int main(int argc, char **argv) {
    FILE *in;
    static char line[1 << 17];
    long skip_through = -1, crashed = -1;
    pf_set_iph set_iph;
    if (argc < 3) {
        fprintf(stderr, "usage: crtvec vectors.tsv results.tsv [--resume]\n");
        return 2;
    }
    g_crt = LoadLibraryA("msvcrt.dll");
    if (!g_crt) {
        fprintf(stderr, "cannot load msvcrt.dll\n");
        return 2;
    }
    p_errno = (pf_errno)sym("_errno");
    p_setlocale = (pf_setlocale)sym("setlocale");
    set_iph = (pf_set_iph)GetProcAddress(g_crt, "_set_invalid_parameter_handler");
    if (set_iph)
        set_iph(on_invalid_parameter);
    if (argc > 3 && !strcmp(argv[3], "--resume")) {
        skip_through = last_done_id(argv[2]);
        crashed = skip_through + 1;
        g_out = fopen(argv[2], "ab");
    } else {
        g_out = fopen(argv[2], "wb");
    }
    in = fopen(argv[1], "rb");
    if (!in || !g_out) {
        fprintf(stderr, "cannot open %s or %s\n", argv[1], argv[2]);
        return 2;
    }
    if (skip_through < 0 && crashed >= 0)
        crashed = 0;
    while (fgets(line, sizeof line, in)) {
        long id;
        size_t n = strlen(line);
        while (n && (line[n - 1] == '\n' || line[n - 1] == '\r'))
            line[--n] = 0;
        if (!n)
            continue;
        if (split(line) < 2)
            continue;
        id = atol(g_f[0]);
        if (id <= skip_through)
            continue;
        g_len = 0;
        put_raw(g_f[0]);
        field(g_f[1]);
        if (id == crashed) {
            field("CRASH");
        } else {
            g_iph = 0;
            __try {
                set_locale("C"); /* every vector starts in the C locale */
                run_vector();
            } __except (EXCEPTION_EXECUTE_HANDLER) {
                g_len = 0;
                put_raw(g_f[0]);
                field(g_f[1]);
                field("EXC");
                field_u32x(GetExceptionCode(), 8);
            }
            if (g_iph)
                field("IPH");
        }
        g_line[g_len++] = '\n';
        fwrite(g_line, 1, g_len, g_out);
        fflush(g_out);
    }
    set_locale("C");
    fclose(g_out);
    fclose(in);
    return 0;
}
