// tcp_plugin_harness.cpp - TCP Protocol.dll's NPIM entry points on the kit's
// Winsock, over the loopback interface (P5-A; netcode.md, "Mac Winsock HLE").
//
//   ee_tcp_plugin_harness [--wire FILE]
//
// sxlrt233.dll, Low-Level Engine.dll and TCP Protocol.dll are mapped by the
// kit's loader at their game.toml bases and run from their translations
// (linked archives), as the M1 harness runs Default.dll. The plugin is then
// driven the way the engine's NEProtocol drives it [static: netcode.md §2,
// the NPIM signatures in analysis/netcode/tcpproto_named.c]:
//
//   1. NPIMActivate (WSAStartup 2.0, a probe socket, the key table), and the
//      Sierra lookup failing at once (game.toml [network] offline_hosts);
//   2. NPIMEnumerateNetworkAdapters and NPIMGetLocalAddress on the host's
//      adapters, then on three fake adapters, where index 2 and 3 take the
//      path that frees an interior pointer (netcode.md §3.4);
//   3. NPIMConnect (UDP) to 127.0.0.1 and NPIMSend of a 0x22 connection
//      request, caught by a plain socket on 127.0.0.1:33334 - the bytes on
//      the wire, written to --wire for tools/lan/eelan.py to decode;
//   4. NPIMPublish (UDP 33334, TCP 33335; private, so no EEService search),
//      the same request again, the server's event signalled by
//      WSAEventSelect, NPIMReceiveFrom decrypting it and registering the
//      client; NPIMSendTo of a 0x23 reply and NPIMReceive on the client;
//   5. NPIMConnectForXfer / NPIMAcceptForXfer on TCP 33335, NPIMIsAlive;
//      NPIMCloseConnection and NPIMDeactivate.
//
// Every step is checked; the exit status is the number of failures.
#include "dx/dx.h"
#include "platform/os.h"
#include "runtime/guest.h"
#include "runtime/imports.h"
#include "runtime/loader.h"
#include "runtime/memory.h"
#include "runtime/net_adapters.h"
#include "runtime/win32.h"

#include <csetjmp>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace {

int g_failures = 0;
void expect(bool ok, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
void expect(bool ok, const char *fmt, ...) {
    char msg[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);
    printf("%s %s\n", ok ? "  ok  " : "  FAIL", msg);
    if (!ok)
        ++g_failures;
}

const uint16_t GAME_PORT = 33334; // the plugin's UDP port (htons 0x8236)
const uint32_t LOOPBACK = 0x7f000001u;

X86 *g_c = nullptr;
LoaderModule *g_engine = nullptr, *g_tcp = nullptr;

uint32_t fn(LoaderModule *m, const char *name) {
    uint32_t a = loader_module_export(*m, name);
    if (!a)
        printf("  missing export %s!%s\n", m->name.c_str(), name);
    return a;
}
uint32_t call(uint32_t f, std::vector<uint32_t> args = {}) {
    return guest_call(g_c, f, args.data(), (int)args.size());
}
uint32_t import(const char *dll, const char *name, std::vector<uint32_t> args) {
    return call(imports_resolve(dll, name), args);
}
uint32_t cstr(const std::string &s) {
    uint32_t a = heap_alloc((uint32_t)s.size() + 1, true);
    gm_put_str(a, s.c_str(), (uint32_t)s.size() + 1);
    return a;
}
// The engine's string classes, built with its own constructors (thiscall).
uint32_t ustring(const char *text) {
    uint32_t obj = heap_alloc(64, true);
    g_c->r[R_ECX] = obj;
    call(fn(g_engine, "??0UString@@QAE@PBD@Z"), {cstr(text)});
    return obj;
}
uint32_t uwstring(const char *text) {
    uint32_t obj = heap_alloc(64, true);
    g_c->r[R_ECX] = obj;
    call(fn(g_engine, "??0UWideString@@QAE@PBD@Z"), {cstr(text)});
    return obj;
}
// A UWideString's text: {vtable, char *data, ...}. Despite the name it holds
// MBCS bytes (netcode.md §2).
std::string uw_text(uint32_t obj) {
    return obj && rd32(obj + 4) ? gm_str(rd32(obj + 4), 256) : std::string();
}
uint32_t dword(uint32_t value = 0) {
    uint32_t a = heap_alloc(16, true);
    wr32(a, value);
    return a;
}
uint32_t event() {
    return import("KERNEL32.dll", "CreateEventA", {0, 0, 0, 0});
}
uint32_t wait(uint32_t ev, uint32_t ms) {
    return import("KERNEL32.dll", "WaitForSingleObject", {ev, ms});
}

// An NE message as the engine exports it: [key][type][for][orig] + body.
// UWideString on the wire: u32 strlen + 2, the bytes, two NULs.
void put_uws(std::vector<uint8_t> &m, const char *s) {
    uint32_t n = (uint32_t)strlen(s) + 2;
    for (int i = 0; i < 4; ++i)
        m.push_back((uint8_t)(n >> (8 * i)));
    m.insert(m.end(), s, s + strlen(s));
    m.push_back(0);
    m.push_back(0);
}
void put_u32(std::vector<uint8_t> &m, uint32_t v) {
    for (int i = 0; i < 4; ++i)
        m.push_back((uint8_t)(v >> (8 * i)));
}
// NEMClientConnectionRequest (0x22): behindFirewall, version, player name,
// data checksum - the values both peers sent in the session-2 captures.
std::vector<uint8_t> connection_request() {
    std::vector<uint8_t> m = {0, 0x22, 0xff, 0xff, 0};
    put_uws(m, "v2.00.2974");
    put_uws(m, "MacHarness");
    put_u32(m, 0x0e998b9eu);
    return m;
}
// NEMClientConnectionRequestResponse (0x23): result 1, 1, slot 0, CT 0, version.
std::vector<uint8_t> connection_response() {
    std::vector<uint8_t> m = {0, 0x23, 0x00, 0xfe};
    put_u32(m, 1);
    m.push_back(1);
    m.push_back(0);
    put_u32(m, 0);
    put_uws(m, "v2.00.2974");
    return m;
}
uint32_t guest_bytes(const std::vector<uint8_t> &m, uint32_t room = 0x240) {
    uint32_t a = heap_alloc(room, true);
    memcpy(g_mem + a, m.data(), m.size());
    return a;
}
std::string hex(const uint8_t *p, size_t n) {
    std::string s;
    char b[3];
    for (size_t i = 0; i < n; ++i) {
        snprintf(b, sizeof b, "%02x", p[i]);
        s += b;
    }
    return s;
}

OsNetInterface fake(const char *name, unsigned index, uint32_t ip) {
    OsNetInterface f;
    memset(&f, 0, sizeof f);
    snprintf(f.name, sizeof f.name, "%s", name);
    f.index = index;
    f.ip = ip;
    f.netmask = 0xffffff00u;
    f.broadcast = (ip | 0xffu);
    f.up = f.running = f.can_broadcast = 1;
    f.mac_len = 6;
    f.mtu = 1500;
    return f;
}

void run(const char *wire_path) {
    uint32_t valid = fn(g_tcp, "PIMIsValid");
    expect((call(valid) & 0xff) == 0, "PIMIsValid is 0 before NPIMActivate");
    expect(call(fn(g_tcp, "NPIMActivate")) == 0,
           "NPIMActivate: WSAStartup(2.0) and a probe socket");
    expect((call(valid) & 0xff) == 1, "PIMIsValid is 1 after it");
    // The dead Sierra server the Multiplayer screen looks up: game.toml's
    // [network] offline_hosts fails it at once, without a resolver.
    uint64_t t0 = os_monotonic_ns();
    uint32_t he = import("WS2_32.dll", "gethostbyname", {cstr("empireearth.m1.sierra.com")});
    double ms = (double)(os_monotonic_ns() - t0) / 1e6;
    uint32_t err = import("WS2_32.dll", "WSAGetLastError", {});
    expect(he == 0 && err == 11001 && ms < 50,
           "gethostbyname(\"empireearth.m1.sierra.com\"): WSAHOST_NOT_FOUND in %.2f ms", ms);

    // Adapters: the host's.
    std::vector<NetAdapter> host = net_adapters();
    uint32_t list = heap_alloc(16, true);
    uint32_t n = call(fn(g_tcp, "NPIMEnumerateNetworkAdapters"), {list});
    expect(n == host.size(), "NPIMEnumerateNetworkAdapters: %u adapter(s), as net_adapters()", n);
    uint32_t first = rd32(list + 4);
    if (n && first)
        printf("        adapter 1: \"%s\"\n", uw_text(first).c_str());
    uint32_t local = fn(g_tcp, "NPIMGetLocalAddress");
    if (!host.empty()) {
        std::string want = net_dotted(host[0].addresses[0].ip);
        std::string got = uw_text(call(local, {1}));
        expect(got == want, "NPIMGetLocalAddress(1) is the first adapter's address (%s)",
               got.c_str());
    }
    // Three fake adapters: index 2 and 3 walk the GetAdaptersInfo list and
    // free a pointer into the middle of the buffer, which the kit's heap
    // logs and ignores.
    OsNetInterface fakes[3] = {fake("en0", 4, 0x0a630001u), fake("en1", 5, 0x0a640001u),
                               fake("feth7", 9, 0x0a930001u)};
    net_set_interfaces_for_test(fakes, 3, 4, 0);
    const char *want[3] = {"10.99.0.1", "10.100.0.1", "10.147.0.1"};
    for (uint32_t i = 1; i <= 3; ++i) {
        std::string got = uw_text(call(local, {i}));
        expect(got == want[i - 1], "fake adapters: NPIMGetLocalAddress(%u) = %s%s", i, got.c_str(),
               i >= 2 ? " (the interior free survived)" : "");
    }
    net_set_interfaces_for_test(nullptr, -1, 0, 0);

    // The client: NPIMConnect over UDP to 127.0.0.1, and one connection
    // request caught on the wire.
    int capture = os_net_socket(OS_NET_DGRAM);
    expect(capture >= 0 && os_net_bind(capture, LOOPBACK, GAME_PORT) == 0,
           "a plain socket on 127.0.0.1:%u catches what the client sends", GAME_PORT);
    uint32_t host_name = ustring("127.0.0.1"), service = ustring("EEService");
    uint32_t game = uwstring("TestGame");
    uint32_t client_event = dword(event()), client = dword();
    uint32_t r = call(fn(g_tcp, "NPIMConnect"),
                      {host_name, service, game, 2 /* UDP */, client_event, client, 0});
    expect(r == 0 && rd32(client) != 0, "NPIMConnect(UDP, 127.0.0.1) -> %u", r);
    std::vector<uint8_t> plain = connection_request();
    uint32_t msg = guest_bytes(plain);
    r = call(fn(g_tcp, "NPIMSend"), {client, msg, (uint32_t)plain.size()});
    expect(r == 0, "NPIMSend of a %zu-byte connection request -> %u", plain.size(), r);
    uint8_t wire[2048];
    OsNetDatagram info;
    OsNetPoll p = {capture, OS_NET_POLL_IN, 0};
    int64_t got =
        os_net_poll(&p, 1, 2000) == 1 ? os_net_recvfrom(capture, wire, sizeof wire, 0, &info) : -1;
    bool obfuscated = got == (int64_t)plain.size() && wire[0] >= 1 && wire[0] <= 254 &&
                      memcmp(wire + 1, plain.data() + 1, plain.size() - 1) != 0;
    expect(obfuscated, "on the wire: %lld bytes, key index %u, RC4 over the rest", (long long)got,
           got > 0 ? wire[0] : 0);
    expect(info.from_ip == LOOPBACK && info.from_port != 0, "from 127.0.0.1:%u", info.from_port);
    printf("WIRE %s\nPLAIN %s\n", hex(wire, got > 0 ? (size_t)got : 0).c_str(),
           hex(plain.data(), plain.size()).c_str());
    if (wire_path) {
        FILE *f = fopen(wire_path, "w");
        if (f) {
            fprintf(f, "%s\n%s\n", hex(wire, got > 0 ? (size_t)got : 0).c_str(),
                    hex(plain.data(), plain.size()).c_str());
            fclose(f);
        }
    }
    os_net_close(capture);

    // The server: NPIMPublish on UDP 33334 and TCP 33335.
    uint32_t data_event = dword(event()), xfer_event = dword(event());
    uint32_t accept_event = dword(event()), server = dword();
    std::vector<uint32_t> publish = {service,
                                     game,
                                     2 /* UDP */,
                                     data_event,
                                     xfer_event,
                                     accept_event,
                                     server,
                                     1 /* private: no search */,
                                     0 /* not advertised */,
                                     0 /* no adapter */};
    r = call(fn(g_tcp, "NPIMPublish"), publish);
    expect(r == 0 && rd32(server) != 0, "NPIMPublish(UDP %u, TCP %u) -> %u", GAME_PORT,
           GAME_PORT + 1, r);
    call(fn(g_tcp, "NPIMAllowNewConnections"), {server, 1});
    plain = connection_request();
    msg = guest_bytes(plain);
    expect(call(fn(g_tcp, "NPIMSend"), {client, msg, (uint32_t)plain.size()}) == 0,
           "the client sends its request again");
    expect(wait(rd32(data_event), 2000) == 0, "the server's event is signalled (WSAEventSelect)");
    uint32_t buf = heap_alloc(0x240, true), len = dword(), slot = dword(0xffffffffu);
    r = call(fn(g_tcp, "NPIMReceiveFrom"), {server, 0x200, buf, len, slot});
    expect(r == 0 && rd32(len) == plain.size() &&
               memcmp(g_mem + buf + 1, plain.data() + 1, plain.size() - 1) == 0,
           "NPIMReceiveFrom decrypts it (%u bytes) -> %u", rd32(len), r);
    expect(rd32(slot) == 0, "and registers the client in slot %u", rd32(slot));
    expect(wait(rd32(data_event), 0) == 0x102, "the event is quiet with nothing queued");
    // The reply.
    std::vector<uint8_t> reply = connection_response();
    uint32_t out = guest_bytes(reply);
    r = call(fn(g_tcp, "NPIMSendTo"), {server, 0, out, (uint32_t)reply.size()});
    expect(r == 0, "NPIMSendTo(slot 0) a connection response -> %u", r);
    expect(wait(rd32(client_event), 2000) == 0, "the client's event is signalled");
    uint32_t back = heap_alloc(0x240, true), back_len = dword();
    r = call(fn(g_tcp, "NPIMReceive"), {client, 0x240, back, back_len});
    expect(r == 0 && rd32(back_len) == reply.size() &&
               memcmp(g_mem + back + 1, reply.data() + 1, reply.size() - 1) == 0,
           "NPIMReceive on the client decrypts it (%u bytes) -> %u", rd32(back_len), r);

    // File transfer: the client connects to TCP 33335, the server accepts it
    // for the client it knows.
    uint32_t cx_event = dword(event()), cx = dword(), sx = dword();
    r = call(fn(g_tcp, "NPIMConnectForXfer"), {client, 1, cx_event, cx});
    expect(r == 0 && rd32(cx) != 0, "NPIMConnectForXfer (a blocking connect to %u) -> %u",
           GAME_PORT + 1, r);
    expect(wait(rd32(accept_event), 2000) == 0, "FD_ACCEPT signals the server's accept event");
    r = call(fn(g_tcp, "NPIMAcceptForXfer"), {server, sx});
    expect(r == 0 && rd32(sx) != 0, "NPIMAcceptForXfer matches it to client 0 -> %u", r);
    expect((call(fn(g_tcp, "NPIMIsAlive"), {cx}) & 0xff) == 1,
           "NPIMIsAlive on the transfer connection (recv MSG_PEEK)");

    expect(call(fn(g_tcp, "NPIMCloseConnection"), {client}) == 0, "NPIMCloseConnection");
    expect(call(fn(g_tcp, "NPIMDeactivate")) == 0, "NPIMDeactivate: WSACleanup");
}

} // namespace

int main(int argc, char **argv) {
    setvbuf(stdout, nullptr, _IOLBF, 0);
    const char *wire_path = nullptr;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--wire") && i + 1 < argc)
            wire_path = argv[++i];
        else {
            fprintf(stderr, "usage: %s [--wire FILE]\n", argv[0]);
            return 2;
        }
    }
    if (!loader_load(nullptr)) {
        fprintf(stderr, "loader_load failed: %s\n", loader_error());
        return 3;
    }
    dx_register_shims();
    g_c = loader_context();
    LoaderModule *sx = loader_module_named("sxlrt233.dll");
    g_engine = loader_module_named("Low-Level Engine.dll");
    g_tcp = loader_module_named("TCP Protocol.dll");
    if (!sx || !g_engine || !g_tcp) {
        fprintf(stderr, "the loader did not map the three modules\n");
        return 3;
    }
    for (const char *name : {"sxlrt233.dll", "Low-Level Engine.dll", "TCP Protocol.dll"})
        expect(recomp_module_named(name) != nullptr, "%s is translated and registered", name);
    printf("TCP Protocol.dll at %08x (prefers %08x, %u relocations)\n", g_tcp->base,
           g_tcp->preferred_base, g_tcp->relocations);
    sched_set_guest_thread(true);
    int rc = 0;
    if (setjmp(*process_exit_jmp()) == 0) {
        expect(loader_attach_static(g_c), "sxlrt233 and the engine attach");
        expect(loader_module_load(g_c, g_tcp) == 0,
               "LoadLibrary(\"TCP Protocol.dll\") attaches it");
        run(wire_path);
    } else {
        printf("guest process exited with code %u\n", process_exit_code());
        ++g_failures;
        rc = 4;
    }
    sched_set_guest_thread(false);
    printf("TCP plugin harness: %s (%d failures)\n", g_failures ? "FAILED" : "all checks passed",
           g_failures);
    return rc ? rc : g_failures;
}
