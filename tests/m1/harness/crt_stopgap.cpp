// crt_stopgap.cpp - TEMPORARY: the msvcrt.dll subset the M1 modules' CRT
// start-up and .rmv parser call, until the kit's msvcrt (Phase 2 gap 4,
// branch feat/crt) lands on `ee`. Delete this file then.
//
// Ported from the Phase 0 spike (spikes/ee-recomp/harness/msvcrt_spike.cpp).
// Registered before the loader registers the kit's own tables, so wherever
// the kit implements an import, its shim replaces the one here
// (imports_register: later registrations win). None of this is exact: host
// strtof for sscanf("%f"), host printf formatting, single-byte _mbs*.
// EE_M1_NO_CRT_STOPGAP=1 runs without it.
#include "runtime/guest.h"
#include "runtime/imports.h"
#include "runtime/memory.h"
#include "runtime/win32.h"
#include <map>
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>
#include <algorithm>
#include <cstring>
#include <cctype>
#include <cstdlib>

namespace {

// cdecl void _initterm(PVFV *begin, PVFV *end): call each non-null pointer.
void crt_initterm(X86 *c) {
    uint32_t p = arg(c, 0), end = arg(c, 1);
    for (; p < end; p += 4) {
        uint32_t fn = rd32(p);
        if (fn)
            guest_call(c, fn);
    }
}
void crt_malloc(X86 *c) { set_eax(c, heap_alloc(arg(c, 0) ? arg(c, 0) : 1)); }
void crt_calloc(X86 *c) { set_eax(c, heap_alloc(arg(c, 0) * arg(c, 1) ? arg(c, 0) * arg(c, 1) : 1, true)); }
void crt_realloc(X86 *c) {
    uint32_t p = arg(c, 0), n = arg(c, 1);
    set_eax(c, p ? heap_realloc(p, n) : heap_alloc(n ? n : 1));
}
void crt_free(X86 *c) {
    if (arg(c, 0))
        heap_free(arg(c, 0));
}
// _onexit/__dllonexit/atexit: record and succeed; process exit never runs them here.
std::vector<uint32_t> g_onexit;
void crt_onexit(X86 *c) {
    g_onexit.push_back(arg(c, 0));
    set_eax(c, arg(c, 0));
}
void crt_atexit(X86 *c) {
    g_onexit.push_back(arg(c, 0));
    set_eax(c, 0);
}
void crt_noop(X86 *c) { set_eax(c, 0); }
void crt_one(X86 *c) { set_eax(c, 1); }
// type_info::~type_info is __thiscall with ECX = this and no stack args.
void crt_typeinfo_dtor(X86 *c) { (void)c; }
// double sqrt(double): cdecl, returns in ST(0). Host sqrt is correctly rounded,
// as is x87 FSQRT, so for doubles this one is exact; sin/pow/etc. are not.
void crt_sqrt(X86 *c) {
    double x;
    uint32_t a = c->r[R_ESP] + 4;
    x = rdf64(a);
    fpush(c, std::sqrt(x));
}
void crt_strlen(X86 *c) { set_eax(c, (uint32_t)gm_str(arg(c, 0)).size()); }
void crt_strcmp(X86 *c) {
    std::string a = gm_str(arg(c, 0)), b = gm_str(arg(c, 1));
    int r = a.compare(b);
    set_eax(c, (uint32_t)(r < 0 ? -1 : r > 0 ? 1 : 0));
}
void crt_strcpy(X86 *c) {
    std::string s = gm_str(arg(c, 1));
    memcpy(g_mem + arg(c, 0), s.c_str(), s.size() + 1);
    set_eax(c, arg(c, 0));
}
void crt_memmove(X86 *c) {
    memmove(g_mem + arg(c, 0), g_mem + arg(c, 1), arg(c, 2));
    set_eax(c, arg(c, 0));
}
void crt_memcmp(X86 *c) { set_eax(c, (uint32_t)memcmp(g_mem + arg(c, 0), g_mem + arg(c, 1), arg(c, 2))); }
std::string guest_format(const std::string &fmt, uint32_t ap);
void crt_printf(X86 *c) {
    std::string f = guest_format(gm_str(arg(c, 0)), c->r[R_ESP] + 8);
    fprintf(stdout, "[guest printf] %s", f.c_str());
    if (f.empty() || f.back() != '\n')
        fputc('\n', stdout);
    set_eax(c, (uint32_t)f.size());
}


// ---- stdio subset: fopen/fgets/fclose on host FILE*, keyed by a guest token.
std::map<uint32_t, FILE *> g_files;
void crt_fopen(X86 *c) {
    std::string guest = gm_str(arg(c, 0)), mode = gm_str(arg(c, 1));
    std::string host = win32_host_path(guest, mode.find_first_of("wa+") != std::string::npos);
    FILE *f = host.empty() ? nullptr : fopen(host.c_str(), mode.c_str());
    printf("[msvcrt] fopen(\"%s\", \"%s\") -> %s%s\n", guest.c_str(), mode.c_str(),
           host.empty() ? "(unresolved)" : host.c_str(), f ? "" : " FAILED");
    if (!f) {
        set_eax(c, 0);
        return;
    }
    uint32_t token = heap_alloc(32, true);
    g_files[token] = f;
    set_eax(c, token);
}
void crt_fgets(X86 *c) {
    uint32_t buf = arg(c, 0), n = arg(c, 1), token = arg(c, 2);
    auto it = g_files.find(token);
    std::vector<char> tmp(n ? n : 1);
    if (it == g_files.end() || !fgets(tmp.data(), (int)tmp.size(), it->second)) {
        set_eax(c, 0);
        return;
    }
    memcpy(g_mem + buf, tmp.data(), strlen(tmp.data()) + 1);
    set_eax(c, buf);
}
void crt_fclose(X86 *c) {
    auto it = g_files.find(arg(c, 0));
    if (it != g_files.end()) {
        fclose(it->second);
        g_files.erase(it);
        heap_free(arg(c, 0));
    }
    set_eax(c, 0);
}
// sscanf: Default.dll only ever passes "%d" or "%f" with one pointer.
// NOTE: host strtof is correctly rounded; MSVC6's _fltin is not guaranteed to
// be, so "%f" here is not proven bit-identical to msvcrt.
void crt_sscanf(X86 *c) {
    std::string str = gm_str(arg(c, 0)), fmt = gm_str(arg(c, 1));
    uint32_t out = arg(c, 2);
    int n = 0;
    if (fmt == "%d") {
        int v;
        n = sscanf(str.c_str(), "%d", &v);
        if (n == 1)
            wr32(out, (uint32_t)v);
    } else if (fmt == "%f") {
        float v;
        n = sscanf(str.c_str(), "%f", &v);
        if (n == 1)
            wrf32(out, v);
    } else {
        fprintf(stderr, "[msvcrt] sscanf format \"%s\" not supported by the stopgap\n", fmt.c_str());
    }
    set_eax(c, (uint32_t)n);
}
// printf-family formatting from a guest va_list (dword slots; doubles take two).
std::string guest_format(const std::string &fmt, uint32_t ap) {
    std::string out;
    for (size_t i = 0; i < fmt.size(); ++i) {
        if (fmt[i] != '%') {
            out += fmt[i];
            continue;
        }
        size_t j = i + 1;
        while (j < fmt.size() && strchr("-+ #0123456789.lhI", fmt[j]))
            ++j;
        if (j >= fmt.size())
            break;
        std::string spec = fmt.substr(i, j - i + 1);
        char conv = fmt[j], buf[512];
        if (conv == '%') {
            out += '%';
        } else if (strchr("eEfgG", conv)) {
            double d = rdf64(ap);
            ap += 8;
            snprintf(buf, sizeof buf, spec.c_str(), d);
            out += buf;
        } else if (conv == 's') {
            std::string a = gm_str(rd32(ap));
            ap += 4;
            snprintf(buf, sizeof buf, spec.c_str(), a.c_str());
            out += buf;
        } else {
            uint32_t v = rd32(ap);
            ap += 4;
            std::string s2 = spec;
            s2.erase(std::remove(s2.begin(), s2.end(), 'l'), s2.end());
            snprintf(buf, sizeof buf, s2.c_str(), v);
            out += buf;
        }
        i = j;
    }
    return out;
}
void crt_sprintf(X86 *c) {
    std::string s = guest_format(gm_str(arg(c, 1)), c->r[R_ESP] + 12);
    memcpy(g_mem + arg(c, 0), s.c_str(), s.size() + 1);
    set_eax(c, (uint32_t)s.size());
}
void crt_vsprintf(X86 *c) {
    std::string s = guest_format(gm_str(arg(c, 1)), arg(c, 2));
    memcpy(g_mem + arg(c, 0), s.c_str(), s.size() + 1);
    set_eax(c, (uint32_t)s.size());
}

// ---- string subset (code page 1252 is single-byte, so _mbs* == str* here).
int cmp_n(const std::string &a, const std::string &b, size_t n, bool icase) {
    for (size_t i = 0; i < n; ++i) {
        int x = i < a.size() ? (unsigned char)a[i] : 0, y = i < b.size() ? (unsigned char)b[i] : 0;
        if (icase) {
            x = tolower(x);
            y = tolower(y);
        }
        if (x != y)
            return x < y ? -1 : 1;
        if (!x)
            return 0;
    }
    return 0;
}
void crt_strncmp(X86 *c) { set_eax(c, (uint32_t)cmp_n(gm_str(arg(c, 0)), gm_str(arg(c, 1)), arg(c, 2), false)); }
void crt_strnicmp(X86 *c) { set_eax(c, (uint32_t)cmp_n(gm_str(arg(c, 0)), gm_str(arg(c, 1)), arg(c, 2), true)); }
void crt_stricmp(X86 *c) { set_eax(c, (uint32_t)cmp_n(gm_str(arg(c, 0)), gm_str(arg(c, 1)), ~0u, true)); }
void crt_strcmp2(X86 *c) { set_eax(c, (uint32_t)cmp_n(gm_str(arg(c, 0)), gm_str(arg(c, 1)), ~0u, false)); }
void crt_strcat(X86 *c) {
    std::string a = gm_str(arg(c, 0)), b = gm_str(arg(c, 1));
    memcpy(g_mem + arg(c, 0) + a.size(), b.c_str(), b.size() + 1);
    set_eax(c, arg(c, 0));
}
void crt_strrchr(X86 *c) {
    std::string a = gm_str(arg(c, 0));
    size_t at = a.rfind((char)arg(c, 1));
    set_eax(c, at == std::string::npos ? 0 : arg(c, 0) + (uint32_t)at);
}
void crt_strchr(X86 *c) {
    std::string a = gm_str(arg(c, 0));
    size_t at = a.find((char)arg(c, 1));
    set_eax(c, at == std::string::npos ? 0 : arg(c, 0) + (uint32_t)at);
}
void crt_strstr(X86 *c) {
    std::string a = gm_str(arg(c, 0)), b = gm_str(arg(c, 1));
    size_t at = a.find(b);
    set_eax(c, at == std::string::npos ? 0 : arg(c, 0) + (uint32_t)at);
}
void crt_atoi(X86 *c) { set_eax(c, (uint32_t)atoi(gm_str(arg(c, 0)).c_str())); }
void crt_toupper(X86 *c) { set_eax(c, (uint32_t)toupper((int)arg(c, 0))); }
void crt_tolower(X86 *c) { set_eax(c, (uint32_t)tolower((int)arg(c, 0))); }
void crt_mbslen(X86 *c) { crt_strlen(c); }
void crt_mbsinc(X86 *c) { set_eax(c, arg(c, 0) + 1); }
void crt_mbsninc(X86 *c) { set_eax(c, arg(c, 0) + arg(c, 1)); }
void crt_ismbblead(X86 *c) { set_eax(c, 0); }
void crt_mbsnbcnt(X86 *c) {
    uint32_t n = (uint32_t)gm_str(arg(c, 0)).size();
    set_eax(c, n < arg(c, 1) ? n : arg(c, 1));
}
void crt_mbsncpy(X86 *c) {
    std::string b = gm_str(arg(c, 1));
    uint32_t n = arg(c, 2);
    for (uint32_t i = 0; i < n; ++i)
        wr8(arg(c, 0) + i, i < b.size() ? (uint8_t)b[i] : 0);
    set_eax(c, arg(c, 0));
}
void crt_mbsnbcat(X86 *c) {
    std::string a = gm_str(arg(c, 0)), b = gm_str(arg(c, 1));
    b = b.substr(0, arg(c, 2));
    memcpy(g_mem + arg(c, 0) + a.size(), b.c_str(), b.size() + 1);
    set_eax(c, arg(c, 0));
}

const ImportShim shims[] = {
    {"MSVCRT.dll", "_initterm", ARGC_CDECL, crt_initterm},
    {"MSVCRT.dll", "malloc", ARGC_CDECL, crt_malloc},
    {"MSVCRT.dll", "calloc", ARGC_CDECL, crt_calloc},
    {"MSVCRT.dll", "realloc", ARGC_CDECL, crt_realloc},
    {"MSVCRT.dll", "free", ARGC_CDECL, crt_free},
    {"MSVCRT.dll", "??2@YAPAXI@Z", ARGC_CDECL, crt_malloc},
    {"MSVCRT.dll", "??3@YAXPAX@Z", ARGC_CDECL, crt_free},
    {"MSVCRT.dll", "_onexit", ARGC_CDECL, crt_onexit},
    {"MSVCRT.dll", "__dllonexit", ARGC_CDECL, crt_onexit},
    {"MSVCRT.dll", "atexit", ARGC_CDECL, crt_atexit},
    {"MSVCRT.dll", "??1type_info@@UAE@XZ", 0, crt_typeinfo_dtor},
    {"MSVCRT.dll", "sqrt", ARGC_CDECL, crt_sqrt},
    {"MSVCRT.dll", "strlen", ARGC_CDECL, crt_strlen},
    {"MSVCRT.dll", "strcmp", ARGC_CDECL, crt_strcmp},
    {"MSVCRT.dll", "strcpy", ARGC_CDECL, crt_strcpy},
    {"MSVCRT.dll", "memmove", ARGC_CDECL, crt_memmove},
    {"MSVCRT.dll", "memcmp", ARGC_CDECL, crt_memcmp},
    {"MSVCRT.dll", "printf", ARGC_CDECL, crt_printf},
    {"MSVCRT.dll", "_purecall", ARGC_CDECL, crt_noop},
    {"MSVCRT.dll", "fopen", ARGC_CDECL, crt_fopen},
    {"MSVCRT.dll", "strncmp", ARGC_CDECL, crt_strncmp},
    {"MSVCRT.dll", "_strnicmp", ARGC_CDECL, crt_strnicmp},
    {"MSVCRT.dll", "_stricmp", ARGC_CDECL, crt_stricmp},
    {"MSVCRT.dll", "_mbscmp", ARGC_CDECL, crt_strcmp2},
    {"MSVCRT.dll", "_mbsicmp", ARGC_CDECL, crt_stricmp},
    {"MSVCRT.dll", "_mbsnbcmp", ARGC_CDECL, crt_strncmp},
    {"MSVCRT.dll", "_mbsncmp", ARGC_CDECL, crt_strncmp},
    {"MSVCRT.dll", "_mbsnbicmp", ARGC_CDECL, crt_strnicmp},
    {"MSVCRT.dll", "strcat", ARGC_CDECL, crt_strcat},
    {"MSVCRT.dll", "strrchr", ARGC_CDECL, crt_strrchr},
    {"MSVCRT.dll", "_mbschr", ARGC_CDECL, crt_strchr},
    {"MSVCRT.dll", "strstr", ARGC_CDECL, crt_strstr},
    {"MSVCRT.dll", "atoi", ARGC_CDECL, crt_atoi},
    {"MSVCRT.dll", "toupper", ARGC_CDECL, crt_toupper},
    {"MSVCRT.dll", "tolower", ARGC_CDECL, crt_tolower},
    {"MSVCRT.dll", "_mbslen", ARGC_CDECL, crt_mbslen},
    {"MSVCRT.dll", "_mbsinc", ARGC_CDECL, crt_mbsinc},
    {"MSVCRT.dll", "_mbsninc", ARGC_CDECL, crt_mbsninc},
    {"MSVCRT.dll", "_ismbblead", ARGC_CDECL, crt_ismbblead},
    {"MSVCRT.dll", "_mbsnbcnt", ARGC_CDECL, crt_mbsnbcnt},
    {"MSVCRT.dll", "_mbsnccnt", ARGC_CDECL, crt_mbsnbcnt},
    {"MSVCRT.dll", "_mbsncpy", ARGC_CDECL, crt_mbsncpy},
    {"MSVCRT.dll", "_mbsnbcat", ARGC_CDECL, crt_mbsnbcat},
    {"MSVCRT.dll", "sprintf", ARGC_CDECL, crt_sprintf},
    {"MSVCRT.dll", "fgets", ARGC_CDECL, crt_fgets},
    {"MSVCRT.dll", "fclose", ARGC_CDECL, crt_fclose},
    {"MSVCRT.dll", "sscanf", ARGC_CDECL, crt_sscanf},
    {"MSVCRT.dll", "vsprintf", ARGC_CDECL, crt_vsprintf},
    // Missing from the kit's kernel32 table; without an argc the stdcall
    // argument is never popped (imports.cpp "unknown stdcall argument count").
    {"KERNEL32.dll", "DisableThreadLibraryCalls", 1, crt_one},
};

} // namespace

void crt_stopgap_register() {
    imports_register(shims, sizeof shims / sizeof shims[0]);
    // A data import: MSVCRT's _adjust_fdiv is an int the Pentium FDIV check sets.
    imports_register_data("MSVCRT.dll", "_adjust_fdiv", 4);
}
