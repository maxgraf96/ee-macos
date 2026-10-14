// um_vectors.cpp - ADR-002 criterion 2: the engine's maths through the
// translated modules, against the calls recorded inside the real game.
//
//   ee_m1_harness_static --um build/x87/um.jsonl --um-out build/x87/um-port.jsonl
//
// Each line of the input is a call vector (tests/x87/x87ops.py, "kind":
// "call"): a function, its signature, a control word and arguments. The
// recording (windows/frida/ummath_vectors.js) ran each through a small x86
// stub; this replays the stub's steps on the guest thread:
//
//   FNSTCW saved; FLDCW cw; FNCLEX; FNSTSW (before)
//   push the arguments (cdecl), or FLD m64 each of them (the _CI* and _ftol
//     convention); CALL fn
//   record EAX and EDX; FSTP m80 ST(0) if the function returns there
//   FNSTCW (after); FNSTSW (after); FNINIT; FLDCW saved
//
// and writes the same result fields (st0 as 80 bits, eax, edx, out-buffers,
// the control and status words after, the change of TOP), so
// tests/x87/compare.py compares the two files. Engine exports are called in
// the translated Low-Level Engine.dll; msvcrt functions are the kit's own;
// the executable's helpers ("Empire Earth.exe", "+0x<rva>") only in
// ee_um_harness, which links the executable's translation - elsewhere those
// vectors are reported as not modelled.
#include "runtime/guest.h"
#include "runtime/imports.h"
#include "runtime/loader.h"
#include "runtime/memory.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

namespace {

// The value of "key" in a one-line JSON object whose values are strings or
// arrays of strings: the string, or the array's elements joined by '\n'.
// Enough for the vector format, which has nothing else.
bool json_field(const std::string &line, const char *key, std::string &out) {
    std::string pat = std::string("\"") + key + "\": ";
    size_t p = line.find(pat);
    if (p == std::string::npos)
        return false;
    p += pat.size();
    out.clear();
    auto read_string = [&](size_t &i) {
        std::string s;
        for (++i; i < line.size() && line[i] != '"'; ++i) {
            if (line[i] == '\\' && i + 1 < line.size()) {
                ++i;
                if (line[i] == 'u' && i + 4 < line.size()) {
                    s += (char)strtol(line.substr(i + 1, 4).c_str(), nullptr, 16);
                    i += 4;
                    continue;
                }
            }
            s += line[i];
        }
        ++i;
        return s;
    };
    if (line[p] == '"') {
        out = read_string(p);
        return true;
    }
    if (line[p] == '[') {
        bool first = true;
        for (++p; p < line.size() && line[p] != ']';) {
            if (line[p] == '"') {
                if (!first)
                    out += '\n';
                out += read_string(p);
                first = false;
            } else {
                ++p;
            }
        }
        return true;
    }
    return false;
}

std::vector<std::string> split_lines(const std::string &s) {
    std::vector<std::string> v;
    size_t a = 0;
    if (s.empty())
        return v;
    for (;;) {
        size_t b = s.find('\n', a);
        v.push_back(s.substr(a, b - a));
        if (b == std::string::npos)
            return v;
        a = b + 1;
    }
}

// "f32:3f800000" and the like: the bit pattern; a bare number is decimal.
uint64_t typed_bits(const std::string &tv) {
    size_t colon = tv.find(':');
    if (colon == std::string::npos)
        return (uint64_t)strtoll(tv.c_str(), nullptr, 10);
    return strtoull(tv.c_str() + colon + 1, nullptr, 16);
}

std::string hex_bytes(uint32_t a, uint32_t n) {
    std::string s;
    char b[3];
    for (uint32_t i = 0; i < n; ++i) {
        snprintf(b, sizeof b, "%02x", rd8(a + i));
        s += b;
    }
    return s;
}

} // namespace

// Runs every call vector in `in` on the guest thread `c` and writes the
// results to `out`. Returns the number of vectors run.
int um_run(X86 *c, const LoaderModule &engine, const char *in, const char *out) {
    std::ifstream src(in);
    FILE *dst = fopen(out, "w");
    if (!src || !dst) {
        fprintf(stderr, "um: cannot open %s or %s\n", in, out);
        return -1;
    }
    fprintf(dst, "{\"meta\": {\"backend\": \"ee-macos translated modules, kit x87\"}}\n");
    const uint32_t scratch = heap_alloc(0x400, true, 16);
    std::string line;
    int n = 0;
    while (std::getline(src, line)) {
        std::string id, fn, mod, sym, sig, cw_s, args_s;
        if (!json_field(line, "id", id) || !json_field(line, "fn", fn) ||
            !json_field(line, "sig", sig) || !json_field(line, "cw", cw_s))
            continue;
        json_field(line, "mod", mod);
        json_field(line, "sym", sym);
        json_field(line, "args", args_s);
        std::vector<std::string> args = split_lines(args_s);
        uint32_t addr = 0;
        if (mod == "Low-Level Engine.dll")
            addr = loader_module_export(engine, sym.c_str());
        else if (mod == "msvcrt.dll")
            addr = imports_resolve("msvcrt.dll", sym.c_str());
#ifdef EE_UM_EXE
        else if (mod == "Empire Earth.exe" && sym.rfind("+0x", 0) == 0)
            addr = loader_image_base() + (uint32_t)strtoul(sym.c_str() + 3, nullptr, 16);
#endif
        char conv = sig[0], ret = sig[2];
        std::string types = sig.substr(4);
        if (!addr || conv == 't') {
            fprintf(dst, "{\"id\": \"%s\", \"error\": \"not modelled\"}\n", id.c_str());
            continue;
        }
        // Arguments: dwords for the stack, buffers and strings in guest memory.
        std::vector<uint32_t> words;
        std::vector<uint32_t> bufs;
        std::vector<double> x87_args;
        uint32_t next = scratch + 0x40;
        for (size_t k = 0; k < types.size() && k < args.size(); ++k) {
            const std::string &a = args[k];
            uint64_t bits = typed_bits(a);
            switch (types[k]) {
            case 'f':
            case 'i':
                words.push_back((uint32_t)bits);
                break;
            case 'd':
                if (conv == 'i') {
                    double d;
                    memcpy(&d, &bits, 8);
                    x87_args.push_back(d);
                } else {
                    words.push_back((uint32_t)bits);
                    words.push_back((uint32_t)(bits >> 32));
                }
                break;
            case 'p':
            case 'P': {
                uint32_t b = next;
                next += 16;
                for (uint32_t i = 0; i < 16; ++i)
                    wr8(b + i, 0);
                if (a.rfind("hex:", 0) == 0)
                    for (size_t i = 0; 4 + 2 * i + 1 < a.size() && i < 16; ++i)
                        wr8(b + (uint32_t)i, (uint8_t)strtol(a.substr(4 + 2 * i, 2).c_str(), nullptr, 16));
                bufs.push_back(b);
                words.push_back(b);
                break;
            }
            case 's': {
                uint32_t s = next;
                std::string text = a.rfind("str:", 0) == 0 ? a.substr(4) : a;
                for (size_t i = 0; i < text.size() && i < 63; ++i)
                    wr8(s + (uint32_t)i, (uint8_t)text[i]);
                wr8(s + (uint32_t)std::min<size_t>(text.size(), 63), 0);
                next += 64;
                words.push_back(s);
                break;
            }
            }
        }
        // The stub.
        uint16_t saved = c->fpu_cw;
        x87_set_cw(c, (uint16_t)strtoul(cw_s.c_str(), nullptr, 16));
        c->fpu_sw &= (uint16_t)~0x80ffu;
        uint16_t sw_before = fstsw(c);
        for (double d : x87_args)
            x87_fld_f64(c, d);
        uint32_t eax = guest_call(c, addr, words.data(), (int)words.size());
        uint32_t edx = c->r[R_EDX];
        std::string st0 = "null";
        if (ret == 'f') {
            x87_fst_f80(c, scratch);
            fdrop(c);
            char b[32];
            snprintf(b, sizeof b, "\"x80:%04x%016llx\"", rd16(scratch + 8),
                     (unsigned long long)rd64(scratch));
            st0 = b;
        }
        uint16_t cw_after = c->fpu_cw, sw_after = fstsw(c);
        x87_finit(c);
        x87_set_cw(c, saved);
        unsigned top_delta = ((unsigned)(sw_after >> 11) - (unsigned)(sw_before >> 11)) & 7u;
        char eax_s[24] = "null", edx_s[24] = "null";
        if (ret == 'i' || ret == 'l' || ret == 'b')
            snprintf(eax_s, sizeof eax_s, "\"i32:%08x\"", ret == 'b' ? eax & 0xffu : eax);
        if (ret == 'l')
            snprintf(edx_s, sizeof edx_s, "\"i32:%08x\"", edx);
        std::string bufs_s;
        for (size_t i = 0; i < bufs.size(); ++i)
            bufs_s += (i ? ", \"hex:" : "\"hex:") + hex_bytes(bufs[i], 16) + "\"";
        fprintf(dst,
                "{\"id\": \"%s\", \"st0\": %s, \"eax\": %s, \"edx\": %s, \"bufs\": [%s], "
                "\"cw_after\": \"%04x\", \"sw_after\": \"%04x\", \"top_delta\": %u}\n",
                id.c_str(), st0.c_str(), eax_s, edx_s, bufs_s.c_str(), cw_after, sw_after, top_delta);
        ++n;
    }
    fclose(dst);
    return n;
}
