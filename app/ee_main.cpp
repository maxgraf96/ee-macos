// ee_main.cpp - Empire Earth on recomp-kit: the executable and its eight DLLs,
// translated, booted from the PE entry point (WinMainCRTStartup).
//
//   ee_boot [--game DIR] [--user DIR] [--seconds S] [--trace FILE] [--no-strict]
//           [--play-movies] [--frames DIR [--frame-every N]]
//           [--script FILE [--script-out DIR]] [--lan-pcap FILE]
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
//   - Scripted input (--script, the kit's host/input_script.h): the steps a
//     player takes through the menus, delivered through the kit's input state
//     and message queue as the windowed host delivers a real device's events,
//     with frame dumps as PNG. tests/menus/ holds the scenarios.
//   - --lan-pcap: every UDP datagram and every TCP connection's bytes the
//     game sends or receives through Winsock, as the game sees them (its own
//     buffers, before the kit's sockets), written as a raw-IP pcap with
//     wall-clock times that tools/lan/eelan.py decodes - the LAN lobby's
//     adverts on UDP 8585, its room on TCP 33336 and the game's datagrams on
//     UDP 33334 without a packet capture's privileges. The file is flushed
//     per packet, so it can be decoded while the game runs.
//   - A boot report: the milestones reached, the threads the game started and
//     the x87 control word each ran at, and every import that was called with
//     no implementation behind it. --trace writes every import call.
//
// Exit status: 0 when the guest ended the run itself or the deadline closed it
// after the main menu's message loop was reached; 3 when it ended abnormally;
// 4/5/6 from the watchdog and the fault handler (kit boot.cpp); 7 when a
// --script failed or did not finish.
#include "boot.h"
#include "input.h"
#include "input_script_host.h"

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

// app/ee_standins.cpp: whether RECOMP_REGISTRY is its default (main's --user moves it).
extern "C" bool ee_standins_registry_defaulted();

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

// --- --lan-pcap -------------------------------------------------------------------
// The game's own traffic as raw IPv4 (LINKTYPE_RAW): every UDP datagram, and
// the bytes of every TCP connection (the lobby room on 33336, the transfer
// port) as data segments with running sequence numbers - no handshake, no
// acknowledgements, a FIN when the game closes the socket. A socket's address
// is what it was bound to (0.0.0.0 for INADDR_ANY); a connected socket that
// was never bound gets its handle as its port, so two connections stay two
// streams. The IP header's checksum is right (readers check it); the UDP and
// TCP checksums are 0.
FILE *g_pcap = nullptr;
using Endpoint = std::pair<uint32_t, uint16_t>;  // (ip, port), network order
std::map<uint32_t, Endpoint> g_bound;            // socket -> its address
std::map<uint32_t, Endpoint> g_peer;             // TCP socket -> the other end
std::map<uint32_t, std::pair<uint32_t, uint32_t>> g_seq; // TCP socket -> (sent, received) bytes
struct PendingRecv {
    uint32_t socket = 0, buf = 0, len = 0, from = 0, flags = 0;
    bool tcp = false; // recv/send rather than recvfrom
    bool send = false;
};
std::map<uint32_t, PendingRecv> g_recv; // by guest thread: a recvfrom/recv/send/accept in flight

void pcap_open(const char *path) {
    if (!(g_pcap = fopen(path, "wb")))
        return;
    const uint32_t header[6] = {0xa1b2c3d4u, 0x00040002u, 0, 0, 65535, 101 /* LINKTYPE_RAW */};
    fwrite(header, 1, sizeof header, g_pcap);
    fflush(g_pcap);
}
// One IPv4 packet: the 20-byte IP header, then `l4` (the UDP or TCP header),
// then the data. `src`/`dst` are a sockaddr_in's address words.
void pcap_ip(uint8_t proto, uint32_t src_ip, uint32_t dst_ip, const uint8_t *l4, uint32_t l4_len,
             const uint8_t *data, uint32_t len) {
    if (!g_pcap || len > 65000)
        return;
    uint8_t h[20] = {0x45, 0};
    const uint16_t total = uint16_t(20 + l4_len + len);
    h[2] = uint8_t(total >> 8);
    h[3] = uint8_t(total);
    h[8] = 64;
    h[9] = proto;
    memcpy(h + 12, &src_ip, 4);
    memcpy(h + 16, &dst_ip, 4);
    uint32_t sum = 0;
    for (int i = 0; i < 20; i += 2)
        sum += uint32_t(h[i]) << 8 | h[i + 1];
    while (sum >> 16)
        sum = (sum & 0xffff) + (sum >> 16);
    h[10] = uint8_t(~sum >> 8);
    h[11] = uint8_t(~sum);
    // The wall clock, so the capture lines up with the other machine's.
    const uint64_t us = os_wall_time_us();
    const uint32_t rec[4] = {uint32_t(us / 1000000u), uint32_t(us % 1000000u), uint32_t(total),
                             uint32_t(total)};
    fwrite(rec, 1, sizeof rec, g_pcap);
    fwrite(h, 1, sizeof h, g_pcap);
    fwrite(l4, 1, l4_len, g_pcap);
    if (len)
        fwrite(data, 1, len, g_pcap);
    fflush(g_pcap);
}
// Ports are a sockaddr_in's port words, in network order.
void pcap_udp(uint32_t src_ip, uint16_t src_port, uint32_t dst_ip, uint16_t dst_port,
              const uint8_t *data, uint32_t len) {
    uint8_t u[8] = {0};
    memcpy(u, &src_port, 2);
    memcpy(u + 2, &dst_port, 2);
    const uint16_t udp_len = uint16_t(8 + len);
    u[4] = uint8_t(udp_len >> 8);
    u[5] = uint8_t(udp_len);
    pcap_ip(17, src_ip, dst_ip, u, sizeof u, data, len);
}
void pcap_tcp(const Endpoint &src, const Endpoint &dst, uint32_t seq, uint8_t flags,
              const uint8_t *data, uint32_t len) {
    uint8_t t[20] = {0};
    memcpy(t, &src.second, 2);
    memcpy(t + 2, &dst.second, 2);
    for (int i = 0; i < 4; ++i)
        t[4 + i] = uint8_t(seq >> (24 - 8 * i));
    t[12] = 5 << 4; // 20-byte header
    t[13] = flags;
    t[14] = t[15] = 0xff; // window
    pcap_ip(6, src.first, dst.first, t, sizeof t, data, len);
}
// A TCP socket's own end: its bound address, or its handle as a port.
Endpoint tcp_self(uint32_t socket) {
    auto it = g_bound.find(socket);
    if (it != g_bound.end() && it->second.second)
        return it->second;
    const uint16_t port = uint16_t((socket & 0xff) << 8 | (socket >> 8 & 0xff));
    return {it != g_bound.end() ? it->second.first : 0, port};
}
// TCP data the game sent (`out`) or received on a connected socket.
void pcap_tcp_data(uint32_t socket, bool out, const uint8_t *data, uint32_t len) {
    auto peer = g_peer.find(socket);
    if (peer == g_peer.end())
        return;
    auto &seq = g_seq[socket];
    const Endpoint self = tcp_self(socket);
    if (out) {
        pcap_tcp(self, peer->second, 1 + seq.first, 0x18 /* PSH ACK */, data, len);
        seq.first += len;
    } else {
        pcap_tcp(peer->second, self, 1 + seq.second, 0x18, data, len);
        seq.second += len;
    }
}
Endpoint sockaddr_at(uint32_t a) {
    Endpoint e{0, 0};
    if (a && gm_valid(a, 8)) {
        memcpy(&e.second, g_mem + a + 2, 2);
        memcpy(&e.first, g_mem + a + 4, 4);
    }
    return e;
}
void pcap_call(const char *name, const uint32_t *args, uint32_t argc, uint32_t tid) {
    if (!strcmp(name, "bind") && argc >= 3 && gm_valid(args[1], 8)) {
        g_bound[args[0]] = sockaddr_at(args[1]);
    } else if (!strcmp(name, "closesocket") && argc >= 1) {
        if (g_peer.count(args[0])) {
            const auto &seq = g_seq[args[0]];
            pcap_tcp(tcp_self(args[0]), g_peer[args[0]], 1 + seq.first, 0x11 /* FIN ACK */,
                     nullptr, 0);
        }
        g_bound.erase(args[0]);
        g_peer.erase(args[0]);
        g_seq.erase(args[0]);
    } else if (!strcmp(name, "connect") && argc >= 3 && gm_valid(args[1], 8)) {
        g_peer[args[0]] = sockaddr_at(args[1]);
        g_seq.erase(args[0]);
    } else if (!strcmp(name, "accept") && argc >= 3) {
        PendingRecv p;
        p.socket = args[0];
        p.from = args[1];
        g_recv[tid] = p;
    } else if ((!strcmp(name, "recv") || !strcmp(name, "send")) && argc >= 4) {
        PendingRecv p;
        p.socket = args[0];
        p.buf = args[1];
        p.len = args[2];
        p.flags = args[3];
        p.tcp = true;
        p.send = name[0] == 's';
        g_recv[tid] = p;
    } else if (!strcmp(name, "sendto") && argc >= 6 && args[4] && gm_valid(args[4], 8) &&
               gm_valid(args[1], args[2])) {
        uint16_t port;
        uint32_t ip;
        memcpy(&port, g_mem + args[4] + 2, 2);
        memcpy(&ip, g_mem + args[4] + 4, 4);
        const auto self = g_bound.count(args[0]) ? g_bound[args[0]]
                                                 : std::pair<uint32_t, uint16_t>{0, 0};
        pcap_udp(self.first, self.second, ip, port, g_mem + args[1], args[2]);
    } else if (!strcmp(name, "recvfrom") && argc >= 6) {
        g_recv[tid] = {args[0], args[1], args[2], args[4]};
    }
}
void pcap_return(const char *name, uint32_t eax, uint32_t tid) {
    const bool accepted = !strcmp(name, "accept");
    const bool stream = !strcmp(name, "recv") || !strcmp(name, "send");
    if (strcmp(name, "recvfrom") && !accepted && !stream)
        return;
    auto it = g_recv.find(tid);
    if (it == g_recv.end())
        return;
    const PendingRecv r = it->second;
    g_recv.erase(it);
    if (accepted) {
        // The new connection: its other end from the call's address, its own
        // the listening socket's.
        if (eax == 0xffffffffu)
            return;
        g_peer[eax] = sockaddr_at(r.from);
        if (g_bound.count(r.socket))
            g_bound[eax] = g_bound[r.socket];
        g_seq.erase(eax);
        return;
    }
    if (stream) {
        // A peek leaves the bytes queued: they are recorded when read.
        if (r.tcp && eax != 0xffffffffu && eax && eax <= r.len && !(r.flags & 2 /* MSG_PEEK */) &&
            gm_valid(r.buf, eax))
            pcap_tcp_data(r.socket, r.send, g_mem + r.buf, eax);
        return;
    }
    if (eax == 0xffffffffu || !eax || eax > r.len || !r.from || !gm_valid(r.from, 8) ||
        !gm_valid(r.buf, eax))
        return;
    uint16_t port;
    uint32_t ip;
    memcpy(&port, g_mem + r.from + 2, 2);
    memcpy(&ip, g_mem + r.from + 4, 4);
    const auto self =
        g_bound.count(r.socket) ? g_bound[r.socket] : std::pair<uint32_t, uint16_t>{0, 0};
    pcap_udp(ip, port, self.first, self.second, g_mem + r.buf, eax);
}

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
        if (g_pcap)
            pcap_call(name, args, argc, tid);
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
    if (g_pcap) {
        std::lock_guard<std::mutex> hold(g_lock);
        pcap_return(after_bang(desc), eax, tid);
    }
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
// windows): counted, handed to the --script (dumps, picture waits), and with
// --frames every Nth one written as a PPM - the frames to compare with
// legion's references.
extern "C" void host_display_present_window(const uint32_t *argb, int w, int h) {
    const uint64_t n = ++g_presents;
    iscript::host_pixels(argb, w, h);
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
// The keyboard and mouse DirectInput reads are the kit's input state
// (host/input.cpp), fed by the kit's headless input path (input_script_host):
// Escape while an intro movie plays with --skip-movies (the default), held for
// 100 ms of every second - what a player does on Windows to reach the menu in
// seconds rather than two minutes (legion.md §6) - and whatever a --script does.
namespace {

iscript::HeadlessSink *g_sink = nullptr;
bool g_escape_down = false;
bool g_script = false;
bool g_script_closed = false;

void skip_movies() {
    const bool want = g_skip_movies && g_movies_open > 0 && (boot_millis() % 1000) < 100;
    if (want != g_escape_down) {
        g_escape_down = want;
        g_sink->key(0x01, want); // DIK_ESCAPE
    }
}

// --- the host ----------------------------------------------------------------
// The deadline: WM_CLOSE to the main window, as closing it does; the game
// then shuts down through its own exit path.
void tick() {
    if (!g_sink)
        g_sink = new iscript::HeadlessSink();
    skip_movies();
    // The display's clock for GDI windows: while the game presents nothing (the
    // Multiplayer lobby is up), the kit repeats what the screen shows a few
    // times a second, so a script's dumps and waits see it.
    boot_present_windows();
    // A script starts at the main menu, the screen every scenario starts from.
    if (g_script && !g_movies_open && g_reached[kMenuLoop] > 0) {
        iscript::host_tick(*g_sink, g_presents.load());
        iscript::Runner *r = iscript::host_runner();
        if (r->failed() && !g_script_closed && !boot_close_requested()) {
            g_script_closed = true;
            boot_request_close("input script failed");
        }
    }
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
    if (iscript::Runner *r = iscript::host_runner())
        r->report(out);
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
        "  --frames DIR write presented frames there as PPM (every --frame-every N, default 60)\n"
        "  --script FILE scripted input (kit host/input_script.h); --script-out DIR for its\n"
        "               frames (default FILE without its extension, plus -frames)\n"
        "  --lan-pcap FILE the game's UDP and TCP traffic as a raw-IP pcap (tools/lan/eelan.py)\n",
        argv0);
    return 2;
}

} // namespace

int main(int argc, char **argv) {
    setvbuf(stdout, nullptr, _IOLBF, 0);
    std::string game, user = user_dir_default(), trace, frames, frame_every = "60";
    std::string script, script_out, lan_pcap;
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
        else if (!strcmp(argv[i], "--script") && i + 1 < argc)
            script = argv[++i];
        else if (!strcmp(argv[i], "--script-out") && i + 1 < argc)
            script_out = argv[++i];
        else if (!strcmp(argv[i], "--lan-pcap") && i + 1 < argc)
            lan_pcap = argv[++i];
        else
            return usage(argv[0]);
    }
    // The player's data. Set before anything computes the host layout: the
    // overlay's writable tier is RECOMP_PROFILE_DIR, the registry RECOMP_REGISTRY.
    os_mkdir(user.c_str());
    setenv("RECOMP_PROFILE_DIR", user.c_str(), 1);
    // ee_standins.cpp's constructor ran first and defaulted RECOMP_REGISTRY to
    // the default user directory's; only a caller's own RECOMP_REGISTRY wins
    // over the user directory (without this, --user DIR read and rewrote
    // ~/Library/Application Support/EmpireEarth/registry.json).
    if (!getenv("RECOMP_REGISTRY") || ee_standins_registry_defaulted())
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
    if (!script.empty()) {
        std::string error;
        if (!iscript::host_start(script, script_out, &error)) {
            fprintf(stderr, "[ee] %s\n", error.c_str());
            return 2;
        }
        g_script = true;
    }
    if (!lan_pcap.empty()) {
        pcap_open(lan_pcap.c_str());
        if (!g_pcap)
            fprintf(stderr, "[ee] cannot write %s\n", lan_pcap.c_str());
    }
    if (!trace.empty() && !(g_trace = fopen(trace.c_str(), "w")))
        fprintf(stderr, "[ee] cannot write %s\n", trace.c_str());
    // Line by line: a trace is read most when the run died, and a fault
    // ends the process without flushing a buffer.
    if (g_trace)
        setvbuf(g_trace, nullptr, _IOLBF, 1 << 16);

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
    if (g_pcap)
        fclose(g_pcap);
    if (boot_abnormal_exit() && !(boot_close_requested() && g_reached[kMenuLoop] > 0)) {
        fprintf(stderr, "[ee] the run did not end normally (%s)\n", boot_stop_reason());
        return 3;
    }
    if (iscript::Runner *r = iscript::host_runner(); r && !r->finished()) {
        fprintf(stderr, "[ee] the input script %s\n",
                r->failed() ? ("failed: " + r->failure()).c_str() : "did not finish");
        return 7;
    }
    return 0;
}
