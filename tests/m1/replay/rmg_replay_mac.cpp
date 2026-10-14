// rmg_replay_mac.cpp - the M1 replay on the Mac: rmg_replay_core.c behind an
// rr_platform over the kit, with the translated sxlrt233.dll, Low-Level
// Engine.dll and Default.dll at their game.toml bases. The Windows twin is
// rmg_replay_win32.c; docs/reference/m1-replay.md is the manual.
//
//   ee_m1_replay [--bundles DIR] [--diff-dir DIR] [--results FILE] [--log-dir DIR]
//                [-v] [--lenient] PACK.rrp...
//   ee_m1_replay [--bundles DIR] --record OUT [...]          (rmg_record_mac.cpp)
//
// ee_m1_replay dlopen()s the freestanding bundles (--bundles, or EE_M1_BUNDLES);
// ee_m1_replay_static has the translations linked in.
//
// One child process per pack (this program with --child), as on Windows: each
// replay needs a fresh guest arena whose heap keeps out of that pack's
// address ranges, and a guest fault or a strict-mode stop ends only that
// child. The child
//   1. reads the pack and the address ranges it needs (rr_needed_ranges);
//   2. has the kit's heap give those ranges up as the arena is created
//      (heap_exclude from a mem_init reset hook), so nothing the DllMains or
//      the kit allocate lands where recorded memory goes;
//   3. maps the modules (loader_load), checks them against the pack's MODS
//      (base, SizeOfImage, TimeDateStamp), routes msvcrt's allocators,
//      fopen/fgets/fclose and kernel32's OutputDebugStringA to the replay
//      shims (pass-through to the kit's own until the call starts) and runs
//      the DllMains in Windows' order;
//   4. lets the core restore every input region (mem_open: at exactly its
//      address, above guest_size included), call the export at the captured
//      ESP and CW, and compare every output byte.
// Strict mode (RECOMP_STRICT=1) is on unless --lenient: a call the
// translation does not cover, an import of unknown argument count or a
// divide error stops the replay instead of being answered with a made-up
// result. Exit status: 0 every pack passed, 1 a mismatch, 2 an error.
#include "rmg_mac.h"
extern "C" {
#include "rmg_replay.h"
}

#include "platform/os.h"
#include "runtime/memory.h"
#include "runtime/win32.h"

#include <algorithm>
#include <cerrno>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <string>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

int rmg_record_main(int argc, char **argv, const char *bundles); // rmg_record_mac.cpp

namespace {

// ---- the child: one pack ---------------------------------------------------------------------

const char *g_bundles = nullptr;
int g_verbose = 0;
LoaderModule *g_mods[3];
rr_range *g_ranges = nullptr;
uint32_t g_nranges = 0;
bool g_exclude_failed = false;
uint32_t g_exclude_lo = 0, g_exclude_hi = 0;
unsigned g_dbg_lines = 0;
int g_result_fd = -1; // where the outcome goes, for the parent
const rr_file_header *g_hdr = nullptr;
std::string g_detail; // the line that best says what happened

// One line for the parent: status, then what happened. Raw write(2): this
// also runs from the fault handler.
void write_result(const char *status, const char *detail) {
    if (g_result_fd < 0)
        return;
    char line[1024];
    int n = snprintf(line, sizeof line, "%s\t%s\n", status, detail ? detail : "");
    if (n > (int)sizeof line - 1)
        n = (int)sizeof line - 1;
    if (n > 0) {
        ssize_t written = write(g_result_fd, line, (size_t)n);
        (void)written; // nothing better to do from a fault handler
    }
    close(g_result_fd);
    g_result_fd = -1;
}

// SIGABRT (a strict-mode stop, an abort in the runtime) or a host fault the
// guest arena did not claim. The kit has printed its diagnostic already.
void on_fault(const char *what) {
    char msg[256];
    snprintf(msg, sizeof msg,
             "%s: a strict-mode stop or a fault in the runtime (the log has the kit's "
             "diagnostic)",
             what);
    write_result("ERROR", msg);
    os_exit_immediately(2);
}

// mem_init has reset the arena: take every range the pack needs from the heap
// before anything allocates.
void exclude_needed() {
    for (uint32_t i = 0; i < g_nranges; ++i)
        if (!heap_exclude(g_ranges[i].lo, g_ranges[i].hi - g_ranges[i].lo) && !g_exclude_failed) {
            g_exclude_failed = true;
            g_exclude_lo = g_ranges[i].lo;
            g_exclude_hi = g_ranges[i].hi;
        }
}

// A setup error of the platform's own: printed, and kept as the detail for the
// parent unless an earlier one already is (the core then adds its own, less
// specific line).
void platform_error(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void platform_error(const char *fmt, ...) {
    char msg[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);
    printf("ERROR %s\n", msg);
    fflush(stdout);
    if (g_detail.compare(0, 6, "ERROR ") != 0)
        g_detail = std::string("ERROR ") + msg;
}

// Reports and ends the child: the platform's fatal, also used by the overrides.
[[noreturn]] void fatal_now(int code, const char *msg) {
    printf("%s seq=%u %.48s: %s\n", code == 1 ? "MISMATCH" : "ERROR", g_hdr ? g_hdr->seq : 0,
           g_hdr ? g_hdr->export_name : "?", msg);
    fflush(stdout);
    write_result(code == 1 ? "MISMATCH" : "ERROR", msg);
    os_exit_immediately(code);
    abort();
}

// ---- import overrides --------------------------------------------------------------------------

void (*o_new)(X86 *), (*o_newa)(X86 *), (*o_malloc)(X86 *), (*o_calloc)(X86 *), (*o_realloc)(X86 *),
    (*o_delete)(X86 *), (*o_deletea)(X86 *), (*o_free)(X86 *), (*o_fopen)(X86 *), (*o_fgets)(X86 *),
    (*o_fclose)(X86 *), (*o_ods)(X86 *), (*o_printf)(X86 *);

void sh_new(X86 *c) {
    if (rr_active())
        set_eax(c, rr_shim_alloc(RR_OP_NEW, arg(c, 0), 0));
    else
        rmg_pass(c, o_new);
}
void sh_newa(X86 *c) {
    if (rr_active())
        set_eax(c, rr_shim_alloc(RR_OP_NEWA, arg(c, 0), 0));
    else
        rmg_pass(c, o_newa ? o_newa : o_new);
}
void sh_malloc(X86 *c) {
    if (rr_active())
        set_eax(c, rr_shim_alloc(RR_OP_MALLOC, arg(c, 0), 0));
    else
        rmg_pass(c, o_malloc);
}
void sh_calloc(X86 *c) {
    if (rr_active())
        set_eax(c, rr_shim_alloc(RR_OP_CALLOC, arg(c, 0), arg(c, 1)));
    else
        rmg_pass(c, o_calloc);
}
void sh_realloc(X86 *c) {
    if (rr_active())
        set_eax(c, rr_shim_alloc(RR_OP_REALLOC, arg(c, 0), arg(c, 1)));
    else
        rmg_pass(c, o_realloc);
}
void sh_delete(X86 *c) {
    if (rr_active())
        rr_shim_free(RR_OP_DELETE, arg(c, 0));
    else
        rmg_pass(c, o_delete);
}
void sh_deletea(X86 *c) {
    if (rr_active())
        rr_shim_free(RR_OP_DELETEA, arg(c, 0));
    else
        rmg_pass(c, o_deletea ? o_deletea : o_delete);
}
void sh_free(X86 *c) {
    if (rr_active())
        rr_shim_free(RR_OP_FREE, arg(c, 0));
    else
        rmg_pass(c, o_free);
}
void sh_fopen(X86 *c) {
    if (rr_active())
        set_eax(c, rr_shim_fopen(arg(c, 0), arg(c, 1)));
    else
        rmg_pass(c, o_fopen);
}
void sh_fgets(X86 *c) {
    if (rr_active())
        set_eax(c, rr_shim_fgets(arg(c, 0), (int32_t)arg(c, 1), arg(c, 2)));
    else
        rmg_pass(c, o_fgets);
}
void sh_fclose(X86 *c) {
    if (rr_active())
        set_eax(c, (uint32_t)rr_shim_fclose(arg(c, 0)));
    else
        rmg_pass(c, o_fclose);
}
// ESDebugOut's lines: counted during the call, as the Windows replay does.
void sh_ods(X86 *c) {
    if (rr_active()) {
        ++g_dbg_lines;
        if (g_verbose > 1)
            printf("  [debug] %s", gm_str(arg(c, 0), 512).c_str());
    } else {
        rmg_pass(c, o_ods);
    }
}
// The kit's printf, its result included, with its text kept out of the
// report unless -v -v (Default.dll prints stage timings).
void sh_printf(X86 *c) {
    if (!rr_active() || g_verbose > 1) {
        rmg_pass(c, o_printf);
        return;
    }
    fflush(stdout);
    int saved = dup(1), null_fd = open("/dev/null", O_WRONLY);
    if (saved >= 0 && null_fd >= 0)
        dup2(null_fd, 1);
    rmg_pass(c, o_printf);
    fflush(stdout);
    if (saved >= 0 && null_fd >= 0)
        dup2(saved, 1);
    if (null_fd >= 0)
        close(null_fd);
    if (saved >= 0)
        close(saved);
}

// __dllonexit during the call (A03 GenerateResources: a function-local static constructed on
// first use registers its destructor through Default.dll's static CRT). The atexit table is a
// restored heap block the kit's heap does not own, so the kit's own __dllonexit (mem_size,
// mem_realloc) cannot grow it; the core appends in place while the captured block has room,
// as the Windows replay does (rr_shim_dllonexit in rmg_replay_core.c).
void (*o_dllonexit)(X86 *);
void sh_dllonexit(X86 *c) {
    if (rr_active())
        set_eax(c, rr_shim_dllonexit(arg(c, 0), arg(c, 1), arg(c, 2)));
    else
        rmg_pass(c, o_dllonexit);
}

// msvcp60's _Getctype copies the classification table into a block it gets
// from msvcrt's malloc (256 shorts), which the capture records (msvcp60.dll
// is among the callers it follows). The kit's msvcp60 allocates the table
// itself, so during the call the table moves into the block the shim hands
// out, in the recorded order.
void (*o_getctype)(X86 *);
void sh_getctype(X86 *c) {
    rmg_pass(c, o_getctype);
    if (!rr_active())
        return;
    const uint32_t out = c->r[R_EAX], kit_table = rd32(out + 8);
    const uint32_t table = rr_shim_alloc(RR_OP_MALLOC, 512, 0);
    if (table && kit_table)
        memcpy(g_mem + table, g_mem + kit_table, 512);
    wr32(out + 8, table);
    wr32(out + 12, table ? 1 : 0);
}

// Stream functions the capture does not record (it flags them as warnings):
// the replay cannot reproduce them, so reaching one during the call is an
// error rather than a call into the kit with a FILE* that was never opened.
void (*o_stream[24])(X86 *);
const char *const kStreamNames[] = {"fread",  "fscanf", "fgetc",   "getc",    "feof",    "fseek",
                                    "ftell",  "fwrite", "fputs",   "fputc",   "fprintf", "ungetc",
                                    "rewind", "fflush", "fgetpos", "fsetpos", "setvbuf", "_getw",
                                    "_putw",  "fgetwc", "fputwc",  "ungetwc", "_fsopen", "freopen"};
template <int I> void sh_stream(X86 *c) {
    if (!rr_active()) {
        rmg_pass(c, o_stream[I]);
        return;
    }
    char msg[200];
    snprintf(msg, sizeof msg,
             "the call reached msvcrt %s, which the replay cannot reproduce (captures record "
             "fopen/fgets/fclose only)",
             kStreamNames[I]);
    fatal_now(2, msg);
}

template <int... I> struct StreamTable {
    static void add(std::vector<RmgOverride> &v) {
        (v.push_back(RmgOverride{"msvcrt.dll", kStreamNames[I], sh_stream<I>, &o_stream[I]}), ...);
    }
};

void install_replay_overrides() {
    std::vector<RmgOverride> v = {
        {"msvcrt.dll", "??2@YAPAXI@Z", sh_new, &o_new},
        {"msvcrt.dll", "??_U@YAPAXI@Z", sh_newa, &o_newa},
        {"msvcrt.dll", "malloc", sh_malloc, &o_malloc},
        {"msvcrt.dll", "calloc", sh_calloc, &o_calloc},
        {"msvcrt.dll", "realloc", sh_realloc, &o_realloc},
        {"msvcrt.dll", "??3@YAXPAX@Z", sh_delete, &o_delete},
        {"msvcrt.dll", "??_V@YAXPAX@Z", sh_deletea, &o_deletea},
        {"msvcrt.dll", "free", sh_free, &o_free},
        {"msvcrt.dll", "fopen", sh_fopen, &o_fopen},
        {"msvcrt.dll", "fgets", sh_fgets, &o_fgets},
        {"msvcrt.dll", "fclose", sh_fclose, &o_fclose},
        {"msvcrt.dll", "printf", sh_printf, &o_printf},
        {"msvcrt.dll", "__dllonexit", sh_dllonexit, &o_dllonexit},
        {"kernel32.dll", "OutputDebugStringA", sh_ods, &o_ods},
        {"msvcp60.dll", "_Getctype", sh_getctype, &o_getctype},
    };
    StreamTable<0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22,
                23>::add(v);
    rmg_install_overrides(v.data(), v.size());
}

// ---- rr_platform -------------------------------------------------------------------------------

int p_map(void *, uint32_t addr, uint32_t size) {
    const uint64_t hi = (uint64_t)addr + size;
    if (const char *what = rmg_kit_region(addr, (uint32_t)std::min<uint64_t>(hi, 0xffffffffu))) {
        platform_error("address conflict: %08x-%08llx, which the pack needs, is %s", addr,
                       (unsigned long long)hi, what);
        return -1;
    }
    if (!heap_exclude(addr, size)) {
        platform_error("address conflict: the kit's heap holds a live block in %08x-%08llx", addr,
                       (unsigned long long)hi);
        return -1;
    }
    if (!mem_open(addr, size)) {
        platform_error("cannot open guest %08x-%08llx", addr, (unsigned long long)hi);
        return -1;
    }
    return 0;
}

int p_write(void *, uint32_t addr, const void *src, uint32_t n) {
    if (!n)
        return 0;
    if (!gm_valid(addr, n))
        return -1;
    memcpy(g_mem + addr, src, n);
    return 0;
}

int p_read(void *, uint32_t addr, void *dst, uint32_t n) {
    if (!n)
        return 0;
    if (!gm_valid(addr, n)) {
        memset(dst, 0, n);
        return -1;
    }
    memcpy(dst, g_mem + addr, n);
    return 0;
}

int p_load_modules(void *, const rr_module_rec *mods, uint32_t n) {
    std::string err;
    if (!rmg_load_modules(g_mods, &err)) {
        platform_error("%s", err.c_str());
        return -1;
    }
    if (n != 3) {
        platform_error("the pack lists %u modules, not sxlrt233, Low-Level Engine, Default", n);
        return -1;
    }
    for (uint32_t i = 0; i < 3; ++i) {
        const LoaderModule &m = *g_mods[i];
        char name[33];
        memcpy(name, mods[i].name, 32);
        name[32] = 0;
        const uint32_t ts = rmg_pe_timestamp(m), soi = rmg_pe_size_of_image(m);
        if (strcasecmp(name, kRmgModules[i].name) || mods[i].base != m.base ||
            mods[i].size_of_image != soi) {
            platform_error("the capture's %s is at %08x (SizeOfImage %#x); the translated %s is "
                           "at %08x (%#x). Captures must come from DLLs rebased to the game.toml "
                           "map (docs/reference/build.md section 3)",
                           name, mods[i].base, mods[i].size_of_image, m.name.c_str(), m.base, soi);
            return -1;
        }
        // A rebasing tool may restamp the copy it writes; the code is the same.
        if (mods[i].timestamp != ts)
            printf("  note: %s TimeDateStamp %08x in the capture, %08x in the original file\n",
                   name, mods[i].timestamp, ts);
        uint32_t rva = 0, size = 0;
        if (rmg_data_section(m, &rva, &size) &&
            (rva != mods[i].data_rva || size != mods[i].data_size))
            printf("  note: %s .data is %#x+%#x here, %#x+%#x in the capture\n", name, rva, size,
                   mods[i].data_rva, mods[i].data_size);
    }
    for (uint32_t i = 0; i < g_nranges; ++i)
        if (const char *what = rmg_kit_region(g_ranges[i].lo, g_ranges[i].hi)) {
            platform_error("address conflict: %08x-%08x, which the pack needs, is %s",
                           g_ranges[i].lo, g_ranges[i].hi, what);
            return -1;
        }
    if (g_exclude_failed) {
        platform_error("address conflict: the kit's heap holds a block in %08x-%08x", g_exclude_lo,
                       g_exclude_hi);
        return -1;
    }
    install_replay_overrides();
    X86 *c = loader_context();
    sched_set_guest_thread(true);
    if (!rmg_attach(c, g_mods, &err)) {
        platform_error("%s", err.c_str());
        return -1;
    }
    if (g_verbose)
        for (LoaderModule *m : g_mods)
            printf("  %-22s at %08x, DllMain #%u ok\n", m->name.c_str(), m->base, m->attach_seq);
    return 0;
}

uint32_t p_export(void *, const char *module, const char *name) {
    LoaderModule *m = loader_module_named(module);
    return m ? loader_module_export(*m, name) : 0;
}

int p_call(void *, uint32_t fn, uint32_t esp, uint16_t cw_in, uint32_t *eax, uint16_t *cw_out) {
    bool exited = false;
    *eax = rmg_call_at(loader_context(), fn, esp, cw_in, cw_out, &exited);
    if (exited) {
        platform_error("the guest called ExitProcess(%u) during the call", process_exit_code());
        return -1;
    }
    return 0;
}

void p_fatal(void *, int code, const char *msg) {
    fatal_now(code, msg);
}

// The core's report lines: printed, and the telling one kept for the parent.
void p_log(void *, const char *msg) {
    printf("%s\n", msg);
    fflush(stdout);
    if (!strncmp(msg, "ERROR", 5)) {
        if (g_detail.compare(0, 6, "ERROR ") != 0)
            g_detail = msg;
    } else if (!strncmp(msg, "PASS ", 5) ||
               (!strncmp(msg, "MISMATCH ", 9) && g_detail.compare(0, 9, "MISMATCH ") != 0)) {
        g_detail = msg;
    } else if (!strncmp(msg, "FAIL ", 5))
        g_detail = std::string(msg) + (g_detail.empty() ? "" : " | " + g_detail);
}

int run_child(const char *pack_path, const char *diff_dir, const char *result_path, bool lenient) {
    setvbuf(stdout, nullptr, _IOLBF, 0);
    if (result_path)
        g_result_fd = open(result_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (!lenient)
        os_setenv("RECOMP_STRICT", "1");
    os_install_fault_handlers(on_fault);
    std::string err;
    if (!rmg_load_translations(g_bundles, &err)) {
        platform_error("%s", err.c_str());
        write_result("ERROR", err.c_str());
        return 2;
    }
    rr_pack pk;
    char perr[256];
    if (rr_pack_load(&pk, pack_path, perr, sizeof perr)) {
        platform_error("%s", perr);
        write_result("ERROR", perr);
        return 2;
    }
    g_hdr = pk.hdr;
    if (rr_needed_ranges(&pk, &g_ranges, &g_nranges)) {
        write_result("ERROR", "out of memory");
        return 2;
    }
    mem_add_reset_hook(exclude_needed);
    rr_platform pf = {nullptr,  p_map,  p_write, p_read, p_load_modules,
                      p_export, p_call, p_fatal, p_log};
    rr_options opt;
    memset(&opt, 0, sizeof opt);
    opt.verbose = g_verbose;
    opt.diff_dir = diff_dir;
    int rc = rr_replay(&pf, &pk, &opt);
    if (g_verbose)
        printf("  %u OutputDebugStringA lines during the replay (%u in the capture)\n", g_dbg_lines,
               pk.ndbgs);
    write_result(rc == 0 ? "PASS" : rc == 1 ? "MISMATCH" : "ERROR", g_detail.c_str());
    fflush(stdout);
    return rc;
}

// ---- the parent: one child per pack ------------------------------------------------------------

std::string base_name(const std::string &p) {
    size_t s = p.find_last_of('/');
    return s == std::string::npos ? p : p.substr(s + 1);
}

std::string read_file(const std::string &path) {
    std::string out;
    if (FILE *f = fopen(path.c_str(), "rb")) {
        char buf[4096];
        size_t n;
        while ((n = fread(buf, 1, sizeof buf, f)) > 0)
            out.append(buf, n);
        fclose(f);
    }
    return out;
}

// What the parent can say about a pack without loading it: its header.
bool read_header(const char *path, rr_file_header *h) {
    FILE *f = fopen(path, "rb");
    if (!f)
        return false;
    bool ok = fread(h, sizeof *h, 1, f) == 1 && !memcmp(h->magic, RR_MAGIC, 8);
    fclose(f);
    return ok;
}

void print_tail(const std::string &log, int lines) {
    std::vector<std::string> v;
    size_t start = 0;
    while (start < log.size()) {
        size_t end = log.find('\n', start);
        if (end == std::string::npos)
            end = log.size();
        if (end > start)
            v.push_back(log.substr(start, end - start));
        start = end + 1;
    }
    size_t from = v.size() > (size_t)lines ? v.size() - lines : 0;
    for (size_t i = from; i < v.size(); ++i)
        printf("  | %s\n", v[i].c_str());
}

std::string tsv_clean(std::string s) {
    for (char &ch : s)
        if (ch == '\t' || ch == '\n' || ch == '\r')
            ch = ' ';
    return s;
}

int run_parent(const char *self, const std::vector<const char *> &packs, const char *diff_dir,
               const char *results, const char *log_dir_arg, bool lenient) {
    std::string log_dir = log_dir_arg ? log_dir_arg : "";
    bool temp_logs = !log_dir_arg;
    if (temp_logs) {
        char tmpl[512];
        snprintf(tmpl, sizeof tmpl, "%s/ee-m1-replay-%d", os_temp_dir(), (int)getpid());
        log_dir = tmpl;
    }
    mkdir(log_dir.c_str(), 0755);
    if (diff_dir)
        mkdir(diff_dir, 0755);
    FILE *tsv = nullptr;
    if (results) {
        tsv = fopen(results, "a");
        if (!tsv) {
            platform_error("cannot open %s", results);
            return 2;
        }
        fseek(tsv, 0, SEEK_END);
        if (ftell(tsv) == 0)
            fputs("pack\tstatus\tseq\tgen\tattempt\texport\texit\tdetail\n", tsv);
    }
    int worst = 0, npass = 0;
    for (size_t index = 0; index < packs.size(); ++index) {
        const char *pack = packs[index];
        const std::string name = base_name(pack);
        // Logs are per pack and per position: two cases may hold packs of one name.
        const std::string stem = log_dir + "/" + std::to_string(index) + "-" + name;
        const std::string log = stem + ".log", res = stem + ".result";
        remove(res.c_str());
        std::vector<std::string> args = {self, "--log", log, "--result", res};
        if (g_bundles)
            args.insert(args.end(), {"--bundles", g_bundles});
        if (diff_dir)
            args.insert(args.end(), {"--diff-dir", diff_dir});
        for (int i = 0; i < g_verbose; ++i)
            args.push_back("-v");
        if (lenient)
            args.push_back("--lenient");
        args.insert(args.end(), {"--child", pack});
        std::vector<const char *> argv;
        for (const std::string &a : args)
            argv.push_back(a.c_str());
        argv.push_back(nullptr);
        fflush(stdout);
        int64_t pid = 0;
        int code = -1;
        if (os_spawn(argv.data(), &pid) != 0 || os_wait(pid, &code) != 0)
            code = -1;
        std::string result = read_file(res), status, detail;
        size_t tab = result.find('\t');
        if (tab != std::string::npos) {
            status = result.substr(0, tab);
            detail = result.substr(tab + 1);
            while (!detail.empty() && detail.back() == '\n')
                detail.pop_back();
        }
        int rc = code == 0 ? 0 : code == 1 ? 1 : 2;
        if (status.empty()) {
            status = rc == 1 ? "MISMATCH" : rc == 0 ? "PASS" : "ERROR";
            char why[160];
            if (code == 5)
                snprintf(why, sizeof why, "guest memory fault (the log has the guest address)");
            else if (code > 128)
                snprintf(why, sizeof why, "the child died of signal %d", code - 128);
            else
                snprintf(why, sizeof why, "the child exited with %d and no result", code);
            detail = why;
            platform_error("%s: %s", name.c_str(), why);
        }
        if (rc) {
            std::string text = read_file(log);
            if (!text.empty()) {
                printf("  %s, last lines of %s:\n", name.c_str(), log.c_str());
                print_tail(text, 12);
            }
        } else if (temp_logs) {
            remove(log.c_str());
        }
        remove(res.c_str());
        if (tsv) {
            rr_file_header h;
            memset(&h, 0, sizeof h);
            bool hv = read_header(pack, &h);
            fprintf(tsv, "%s\t%s\t%d\t%d\t%d\t%.48s\t%d\t%s\n", pack, status.c_str(),
                    hv ? (int)h.seq : -1, hv ? (int)h.gen : -1, hv ? (int)h.attempt : -1,
                    hv ? h.export_name : "?", code, tsv_clean(detail).c_str());
            fflush(tsv);
        }
        if (rc == 0)
            ++npass;
        if (rc > worst)
            worst = rc;
    }
    if (tsv)
        fclose(tsv);
    if (temp_logs)
        rmdir(log_dir.c_str());
    printf("%d/%zu packs passed\n", npass, packs.size());
    return worst;
}

void usage(const char *self) {
    fprintf(stderr,
            "usage: %s [--bundles DIR] [--diff-dir DIR] [--results FILE] [--log-dir DIR] [-v] "
            "[--lenient] PACK.rrp...\n"
            "       %s [--bundles DIR] --record OUT [--rmv NAME] [--seed N] [--case NAME]\n",
            self, self);
}

} // namespace

int main(int argc, char **argv) {
    const char *diff_dir = nullptr, *results = nullptr, *log_dir = nullptr, *log = nullptr,
               *result = nullptr, *child_pack = nullptr;
    bool lenient = false, record = false;
    g_bundles = getenv("EE_M1_BUNDLES");
    std::vector<const char *> packs;
    std::vector<char *> rest = {argv[0]};
    for (int i = 1; i < argc; ++i) {
        const char *a = argv[i];
        if (!strcmp(a, "--bundles") && i + 1 < argc)
            g_bundles = argv[++i];
        else if (!strcmp(a, "--diff-dir") && i + 1 < argc)
            diff_dir = argv[++i];
        else if (!strcmp(a, "--results") && i + 1 < argc)
            results = argv[++i];
        else if (!strcmp(a, "--log-dir") && i + 1 < argc)
            log_dir = argv[++i];
        else if (!strcmp(a, "--log") && i + 1 < argc)
            log = argv[++i];
        else if (!strcmp(a, "--result") && i + 1 < argc)
            result = argv[++i];
        else if (!strcmp(a, "--child") && i + 1 < argc)
            child_pack = argv[++i];
        else if (!strcmp(a, "-v"))
            ++g_verbose;
        else if (!strcmp(a, "--lenient"))
            lenient = true;
        else if (!strcmp(a, "--record"))
            record = true, rest.push_back(argv[i]);
        else if (record)
            rest.push_back(argv[i]);
        else if (a[0] == '-') {
            usage(argv[0]);
            return 2;
        } else
            packs.push_back(a);
    }
    if (record)
        return rmg_record_main((int)rest.size(), rest.data(), g_bundles);
    if (child_pack) {
        if (log && !freopen(log, "w", stderr))
            return 2;
        return run_child(child_pack, diff_dir, result, lenient);
    }
    if (packs.empty()) {
        usage(argv[0]);
        return 2;
    }
    char self[1024];
    if (os_exe_path(self, sizeof self) != 0)
        snprintf(self, sizeof self, "%s", argv[0]);
    return run_parent(self, packs, diff_dir, results, log_dir, lenient);
}
