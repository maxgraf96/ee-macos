// ee_main.cpp - Empire Earth on recomp-kit: the executable and its eight DLLs,
// translated, booted from the PE entry point (WinMainCRTStartup).
//
//   ee_boot [--game DIR] [--user DIR] [--seconds S] [--trace FILE] [--no-strict]
//           [--play-movies] [--frames DIR [--frame-every N]]
//
// The boot itself is the kit's (host/boot.cpp): mem_init, the loader (the
// executable plus every [modules.aux.*] DLL at its base-map address), the
// shims, the heartbeat on the guest's clock, the watchdog and the fault
// handler. What is Empire Earth's lives here:
//
//   - Where things are. The game directory is the GOG folder (read-only to
//     us); the player's data - the emulated registry, and everything the game
//     writes (Users\, saves, logs, its HTTP cache) - goes to the user
//     directory, ~/Library/Application Support/EmpireEarth by default. The
//     kit's file overlay (mods/overlay.cpp) puts the user directory over the
//     game directory: reads prefer it, every write lands in it, a game file
//     opened for writing is copied up first. The registry is
//     <user>/registry.json, seeded by tools/boot/seed_user.py.
//   - Strict mode on by default (RECOMP_STRICT=1): an undeliverable call, a
//     call through an import of unknown argument count, or a divide error stops
//     the run with a diagnostic.
//   - Headless: DirectDraw 7 / Direct3D 7 are the kit's (game.toml [runtime]
//     ddraw7, P3-B) drawing with the software reference renderer
//     (RECOMP_D3D7_SOFTWARE=1 unless set), and --frames writes presented
//     frames; sound and input are null. Escape is pressed through the intro
//     movies (--play-movies turns that off), as a player does to reach the
//     menu in seconds.
//   - What every Empire Earth host adds to the kit, the user directory and
//     the temporary stand-ins, is ee_standins.cpp's (shared with the app).
//   - A boot report: the milestones reached, the threads the game started and
//     the x87 control word each ran at, and every import that was called with
//     no implementation behind it. --trace writes every import call.
//
// Exit status: 0 when the guest ended the run itself or the deadline closed it
// after the main menu's message loop was reached; 3 when it ended abnormally;
// 4/5/6 from the watchdog and the fault handler (kit boot.cpp).
#include "boot.h"

#include "../dx/host_api.h"
#include "../runtime/guest.h"
#include "../runtime/imports.h"
#include "../runtime/loader.h"
#include "../runtime/memory.h"
#include "../runtime/win32.h"
#include "../platform/os.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <atomic>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <vector>

namespace {

double g_seconds = 120.0;
FILE *g_trace = nullptr;
bool g_skip_movies = true;
std::string g_frames_dir;
uint32_t g_frame_every = 60;
std::atomic<uint64_t> g_presents{0};
int g_movies_open = 0; // BinkOpen without its BinkClose yet

// --- milestones ---------------------------------------------------------------
// In the order a Windows boot reaches them (docs/reference/boot.md §2).
enum Milestone {
    kLoaded,     // every module mapped, imports bound
    kCrtStarted, // the executable's CRT asked for its command line
    kRegistry,   // HKCU\Software\SSSI\Empire Earth opened
    kPluginScan, // the engine LoadLibrary'd a plugin from the game folder
    kWindow,     // a window exists (the splash screen's, first)
    kThreads,    // the game started a thread of its own
    kDirectDraw, // a display plugin created DirectDraw 7
    kMovies,     // the first intro movie opened
    kMenuMode,   // a display mode was set after the last movie closed (the menu's)
    kMenuFrames, // a thread other than the main one presented 60 frames since
    kMenuLoop,   // ...and the main thread pumped 100 messages: the main menu's loop
    kMilestoneCount
};
const char *const kMilestoneNames[kMilestoneCount] = {
    "modules loaded",  "CRT started",     "registry read",      "plugin scan",
    "window created",  "threads started", "DirectDraw created", "intro movies",
    "menu video mode", "menu frames",     "main menu loop"};
double g_reached[kMilestoneCount];
int g_last = -1;

void reach(Milestone m, const char *detail = nullptr) {
    if (g_reached[m] > 0)
        return;
    g_reached[m] = boot_elapsed() > 0 ? boot_elapsed() : 1e-6;
    if ((int)m > g_last)
        g_last = m;
    printf("[ee] milestone %d (%s) at %.2fs%s%s\n", (int)m, kMilestoneNames[m], boot_elapsed(),
           detail ? ": " : "", detail ? detail : "");
    fflush(stdout);
}

// --- what the guest asked for ---------------------------------------------------
std::mutex g_lock; // the observer runs under the baton; the report on any thread
// A thread the game started. The engine's TSThread starts every one of them at
// one trampoline (engine+0x5f5d0: CoInitialize, param->fn(param->arg),
// CoUninitialize, ExitThread), so the body is read from the parameter block:
// the function at +0x28 [static].
const uint32_t kTsThreadTrampoline = 0x1005f5d0;
struct ThreadInfo {
    uint32_t start = 0, param = 0, body = 0, creator = 0, out = 0;
    uint32_t calls = 0, flips = 0, peeks = 0;
    uint16_t first_cw = 0, last_cw = 0;
    bool seen = false;
};
std::map<uint32_t, ThreadInfo> g_thread_info; // by guest thread id
std::map<uint32_t, ThreadInfo> g_creating;    // creator tid -> the CreateThread in flight
std::vector<std::string> g_libraries;
uint32_t g_main_tid = 0xffffffffu; // the thread that ran the CRT start-up
uint32_t g_render_tid = 0;
double g_last_movie_close = -1;
uint32_t g_menu_flips = 0, g_menu_peeks = 0;

const char *after_bang(const char *desc) {
    const char *b = strchr(desc, '!');
    return b ? b + 1 : desc;
}

bool observe_call(const char *desc, const uint32_t *args, uint32_t argc, uint32_t *) {
    const char *name = after_bang(desc);
    const uint32_t tid = guest_current_thread_id();
    if (g_trace) {
        fprintf(g_trace, "%u %s", tid, desc);
        for (uint32_t i = 0; i < argc; ++i)
            fprintf(g_trace, " %08x", args[i]);
        // The string a path- or name-taking call names, for reading the trace.
        static const struct {
            const char *name;
            uint32_t arg;
        } kStrings[] = {{"CreateFileA", 0},        {"GetFileAttributesA", 0}, {"FindFirstFileA", 0},
                        {"LoadLibraryA", 0},       {"LoadLibraryExA", 0},     {"RegOpenKeyExA", 1},
                        {"RegCreateKeyExA", 1},    {"RegQueryValueExA", 1},   {"RegSetValueExA", 1},
                        {"CreateDirectoryA", 0},   {"DeleteFileA", 0},        {"GetProcAddress", 1},
                        {"OutputDebugStringA", 0}, {"CreateMutexA", 2},       {"CreateEventA", 3}};
        for (const auto &s : kStrings)
            if (!strcmp(name, s.name) && s.arg < argc && args[s.arg] > 0xffff &&
                gm_valid(args[s.arg], 1))
                fprintf(g_trace, "  \"%s\"", gm_str(args[s.arg], 260).c_str());
        fputc('\n', g_trace);
    }
    {
        std::lock_guard<std::mutex> hold(g_lock);
        ThreadInfo &ti = g_thread_info[tid];
        uint16_t cw = guest_current_context()->fpu_cw;
        if (!ti.seen) {
            ti.seen = true;
            ti.first_cw = cw;
        }
        ti.last_cw = cw;
        ++ti.calls;
    }
    if (!strcmp(name, "GetCommandLineA") || !strcmp(name, "__getmainargs") ||
        !strcmp(name, "__p___initenv")) {
        if (g_main_tid == 0xffffffffu)
            g_main_tid = tid;
        reach(kCrtStarted);
    } else if ((!strcmp(name, "RegOpenKeyExA") || !strcmp(name, "RegCreateKeyExA")) && argc >= 2) {
        std::string sub = gm_str(args[1], 260);
        if (strstr(sub.c_str(), "SSSI"))
            reach(kRegistry, sub.c_str());
    } else if ((!strcmp(name, "LoadLibraryA") || !strcmp(name, "LoadLibraryExA")) && argc >= 1) {
        std::string lib = gm_str(args[0], 260);
        {
            std::lock_guard<std::mutex> hold(g_lock);
            g_libraries.push_back(lib);
        }
        if (strchr(lib.c_str(), '\\'))
            reach(kPluginScan, lib.c_str());
    } else if (!strcmp(name, "CreateWindowExA")) {
        reach(kWindow);
    } else if (!strcmp(name, "DirectDrawCreateEx") || !strcmp(name, "DirectDrawCreate")) {
        reach(kDirectDraw);
    } else if (!strcmp(name, "CreateThread") && argc >= 6) {
        ThreadInfo ti;
        ti.start = args[2];
        ti.param = args[3];
        ti.out = args[5];
        ti.creator = tid;
        ti.body = ti.start == kTsThreadTrampoline && gm_valid(ti.param + 0x28, 4)
                      ? rd32(ti.param + 0x28)
                      : ti.start;
        std::lock_guard<std::mutex> hold(g_lock);
        g_creating[tid] = ti;
    } else if (!strcmp(name, "_BinkOpen@8")) {
        ++g_movies_open;
        reach(kMovies);
    } else if (!strcmp(name, "_BinkClose@4")) {
        if (g_movies_open && !--g_movies_open)
            g_last_movie_close = boot_elapsed();
    } else if (!strcmp(name, "IDirectDraw7::SetDisplayMode") && argc >= 4 &&
               g_last_movie_close >= 0 && !g_movies_open) {
        char buf[48];
        snprintf(buf, sizeof buf, "%ux%u %u bpp", args[1], args[2], args[3]);
        reach(kMenuMode, buf);
    } else if (!strcmp(name, "IDirectDrawSurface7::Flip") && g_reached[kMenuMode] > 0 &&
               !g_movies_open && tid != g_main_tid) {
        if (++g_menu_flips == 60) {
            g_render_tid = tid;
            char buf[48];
            snprintf(buf, sizeof buf, "thread %u", tid);
            reach(kMenuFrames, buf);
        }
    } else if (!strcmp(name, "PeekMessageA") && tid == g_main_tid && g_reached[kMenuFrames] > 0) {
        if (++g_menu_peeks == 100)
            reach(kMenuLoop);
    }
    return true;
}

// The trace's results, and the thread a CreateThread made: its id is in the
// caller's out-parameter once the call returns.
ImportReturnObserver g_next_return_observer = nullptr; // the kit's (dx failure reports)
void observe_return(const char *desc, uint32_t eax) {
    if (g_next_return_observer)
        g_next_return_observer(desc, eax);
    const uint32_t tid = guest_current_thread_id();
    if (g_trace)
        fprintf(g_trace, "%u   -> %08x %s\n", tid, eax, after_bang(desc));
    if (!strcmp(after_bang(desc), "CreateThread")) {
        std::lock_guard<std::mutex> hold(g_lock);
        auto it = g_creating.find(tid);
        if (it == g_creating.end())
            return;
        ThreadInfo ti = it->second;
        g_creating.erase(it);
        if (!eax || !ti.out || !gm_valid(ti.out, 4))
            return;
        uint32_t new_tid = rd32(ti.out);
        ThreadInfo &slot = g_thread_info[new_tid];
        slot.start = ti.start;
        slot.param = ti.param;
        slot.body = ti.body;
        slot.creator = ti.creator;
        char buf[64];
        snprintf(buf, sizeof buf, "thread %u, body %08x", new_tid, ti.body);
        reach(kThreads, buf);
    }
}

std::string where(uint32_t a) {
    char buf[96];
    if (const LoaderModule *m = loader_module_containing(a))
        snprintf(buf, sizeof buf, "%s+%x", m->base == 0x00400000 ? "EXE" : m->name.c_str(),
                 a - m->base);
    else
        snprintf(buf, sizeof buf, "%08x", a);
    return buf;
}

} // namespace

// --- presentation ---------------------------------------------------------------------
// What the kit's DirectDraw 7 presents through its software renderer (and GDI
// windows): counted, and with --frames every Nth one written as a PPM - the
// frames to compare with legion's references.
extern "C" void host_display_present_window(const uint32_t *argb, int w, int h) {
    const uint64_t n = ++g_presents;
    if (g_frames_dir.empty() || !argb || w <= 0 || h <= 0 || n % g_frame_every)
        return;
    char path[1024];
    snprintf(path, sizeof path, "%s/frame-%06llu.ppm", g_frames_dir.c_str(), (unsigned long long)n);
    FILE *f = fopen(path, "wb");
    if (!f)
        return;
    fprintf(f, "P6\n%d %d\n255\n", w, h);
    std::vector<uint8_t> row(size_t(w) * 3);
    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w; ++x) {
            const uint32_t p = argb[size_t(y) * size_t(w) + size_t(x)];
            row[3 * size_t(x)] = uint8_t(p >> 16);
            row[3 * size_t(x) + 1] = uint8_t(p >> 8);
            row[3 * size_t(x) + 2] = uint8_t(p);
        }
        fwrite(row.data(), 1, row.size(), f);
    }
    fclose(f);
}

// --- input -----------------------------------------------------------------------
// The keyboard and mouse DirectInput reads (dx/dinput.cpp): nothing is pressed,
// except Escape while an intro movie plays with --skip-movies (the default),
// held for 100 ms of every second - what a player does on Windows to reach the
// menu in seconds rather than two minutes (legion.md §6).
extern "C" void host_input_state(HostInputState *out) {
    if (!out)
        return;
    memset(out, 0, sizeof *out);
    if (g_skip_movies && g_movies_open > 0 && (boot_millis() % 1000) < 100)
        out->keys[0x01] = 0x80; // DIK_ESCAPE
}

namespace {

// --- the host ----------------------------------------------------------------
// The deadline: WM_CLOSE to the main window, as closing it does; the game
// then shuts down through its own exit path.
void tick() {
    if (g_seconds > 0 && boot_elapsed() > g_seconds && !boot_close_requested()) {
        printf("[ee] %.0fs deadline: posting WM_CLOSE to %08x\n", g_seconds, host_main_window());
        boot_request_close("deadline");
    }
}

void report(FILE *out, bool abnormal) {
    std::lock_guard<std::mutex> hold(g_lock);
    fprintf(out, "\n== Empire Earth boot report%s ==\n", abnormal ? " (abnormal end)" : "");
    fprintf(out, "stop: %s after %.1fs\n", boot_stop_reason(), boot_elapsed());
    fprintf(out, "frames presented: %llu; application active: %s\n",
            (unsigned long long)g_presents.load(), win32_app_active() ? "yes" : "no");
    boot_print_exit_code(out);
    for (int m = 0; m < kMilestoneCount; ++m)
        if (g_reached[m] > 0)
            fprintf(out, "  milestone %d %-22s %7.2fs\n", m, kMilestoneNames[m], g_reached[m]);
        else
            fprintf(out, "  milestone %d %-22s    not reached\n", m, kMilestoneNames[m]);
    std::map<std::string, int> libraries;
    for (const std::string &l : g_libraries)
        ++libraries[l];
    fprintf(out, "LoadLibrary calls: %zu, of %zu libraries\n", g_libraries.size(),
            libraries.size());
    for (const auto &kv : libraries)
        fprintf(out, "  %2dx %s\n", kv.second, kv.first.c_str());
    fprintf(out, "guest threads (x87 control word at the first and the latest import call):\n");
    for (const auto &kv : g_thread_info) {
        const ThreadInfo &ti = kv.second;
        std::string role = kv.first == g_main_tid     ? "main"
                           : kv.first == g_render_tid ? "render"
                                                      : "";
        fprintf(out, "  thread %-3u %-28s cw %04x -> %04x  %8u calls%s%s\n", kv.first,
                ti.body ? where(ti.body).c_str() : "(process entry)", ti.first_cw, ti.last_cw,
                ti.calls, role.empty() ? "" : "  ", role.c_str());
    }
    boot_print_undeliverable(out);
    imports_dump_coverage(out);
    imports_dump_report(out);
    fflush(out);
}

std::string user_dir_default() {
    char buf[4096];
    if (os_user_data_dir("EmpireEarth", buf, sizeof buf) == 0)
        return buf;
    return "profile";
}

int usage(const char *argv0) {
    fprintf(
        stderr,
        "usage: %s [--game DIR] [--user DIR] [--seconds S] [--trace FILE] [--no-strict]\n"
        "  --game DIR   the GOG game folder holding Empire Earth.exe (default: the\n"
        "               developer copy game.toml names)\n"
        "  --user DIR   the player's data: registry.json and everything the game writes\n"
        "               (default ~/Library/Application Support/EmpireEarth)\n"
        "  --seconds S  post WM_CLOSE after S seconds (default 120; 0 = never)\n"
        "  --trace FILE every import call: thread, DLL!name, stdcall arguments\n"
        "  --play-movies do not press Escape through the intro movies\n"
        "  --frames DIR write presented frames there as PPM (every --frame-every N, default 60)\n",
        argv0);
    return 2;
}

} // namespace

int main(int argc, char **argv) {
    setvbuf(stdout, nullptr, _IOLBF, 0);
    std::string game, user = user_dir_default(), trace, frames, frame_every = "60";
    bool strict = true;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--game") && i + 1 < argc)
            game = argv[++i];
        else if (!strcmp(argv[i], "--user") && i + 1 < argc)
            user = argv[++i];
        else if (!strcmp(argv[i], "--seconds") && i + 1 < argc)
            g_seconds = strtod(argv[++i], nullptr);
        else if (!strcmp(argv[i], "--trace") && i + 1 < argc)
            trace = argv[++i];
        else if (!strcmp(argv[i], "--no-strict"))
            strict = false;
        else if (!strcmp(argv[i], "--play-movies"))
            g_skip_movies = false;
        else if (!strcmp(argv[i], "--frames") && i + 1 < argc)
            frames = argv[++i];
        else if (!strcmp(argv[i], "--frame-every") && i + 1 < argc)
            frame_every = argv[++i];
        else
            return usage(argv[0]);
    }
    // The player's data. Set before anything computes the host layout: the
    // overlay's writable tier is RECOMP_PROFILE_DIR, the registry RECOMP_REGISTRY.
    os_mkdir(user.c_str());
    setenv("RECOMP_PROFILE_DIR", user.c_str(), 1);
    if (!getenv("RECOMP_REGISTRY"))
        setenv("RECOMP_REGISTRY", (user + "/registry.json").c_str(), 1);
    if (!game.empty())
        setenv("RECOMP_EXE", (game + "/Empire Earth.exe").c_str(), 1);
    if (!getenv("RECOMP_STRICT"))
        setenv("RECOMP_STRICT", strict ? "1" : "0", 1);
    // No GPU here: the kit's DirectDraw 7 draws with its reference renderer.
    if (!getenv("RECOMP_D3D7_SOFTWARE"))
        setenv("RECOMP_D3D7_SOFTWARE", "1", 1);
    if (!frames.empty()) {
        os_mkdir(frames.c_str());
        g_frames_dir = frames;
        g_frame_every = (uint32_t)strtoul(frame_every.c_str(), nullptr, 10);
        if (!g_frame_every)
            g_frame_every = 1;
    }
    {
        OsStat st;
        if (os_stat(getenv("RECOMP_REGISTRY"), &st) != 0)
            fprintf(stderr,
                    "[ee] no registry at %s: the game starts as a fresh install would. "
                    "Seed it with tools/boot/seed_user.py\n",
                    getenv("RECOMP_REGISTRY"));
    }
    if (!trace.empty() && !(g_trace = fopen(trace.c_str(), "w")))
        fprintf(stderr, "[ee] cannot write %s\n", trace.c_str());

    BootOptions opts;
    opts.name = "EmpireEarth";
    // The application has the foreground for the whole run, as a player's
    // focused window does: the kit's user32 then activates each window the
    // game shows from inside ShowWindow, as Windows does (host_app_state).
    opts.activate = false;
    opts.tick = tick;
    opts.report = report;
    opts.deadline_seconds = g_seconds;
    opts.deadline_grace = 30.0;
    opts.close_unwind_grace = 15.0;
    if (!boot_load(opts)) {
        fprintf(stderr, "[ee] loading failed: %s\n", loader_error());
        return 2;
    }
    reach(kLoaded);
    printf("[ee] %s; user data %s; strict %s\n", loader_exe_path().c_str(), user.c_str(),
           getenv("RECOMP_STRICT"));
    for (uint32_t i = 0; const LoaderModule *m = loader_module(i); ++i)
        printf("[ee]   %-30s %08x..%08x (prefers %08x, %u relocations)\n", m->name.c_str(), m->base,
               m->base + m->size, m->preferred_base, m->relocations);
    imports_set_call_observer(observe_call);
    g_next_return_observer = imports_set_return_observer_get();
    imports_set_return_observer(observe_return);

    host_app_state(true, false);
    boot_run();

    imports_set_call_observer(nullptr);
    boot_report_lock();
    report(stdout, false);
    boot_report_unlock();
    if (g_trace)
        fclose(g_trace);
    if (boot_abnormal_exit() && !(boot_close_requested() && g_reached[kMenuLoop] > 0)) {
        fprintf(stderr, "[ee] the run did not end normally (%s)\n", boot_stop_reason());
        return 3;
    }
    return 0;
}
