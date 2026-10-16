// ee_input.cpp - Empire Earth's held-key camera keys at Windows' speed.
//
// The arrow keys scroll the camera, and '[' and ']' zoom it, for as long as
// they are held. Each is a hotkey with a continuous event (RegisterInGameHotkeys
// EXE 0x64c8b0): EEUIEKeyboardScroll (EXE 0x5c9070) moves the camera one
// fixed step, (zoom + 0.5) * (scroll speed + 0.5) * 0.05 in the camera's own
// units, and EEUIEHotKeyZoom (0x5c913c) zooms one fixed step. Neither looks
// at the clock. UIHotKey::FireEvents (engine) calls them on every pass of the
// main thread's loop (0x6603c4 -> 0x661889: the keyboard and mouse Refresh,
// then UIManager::UpdateUI), and that loop ends each pass in Sleep(1), so on
// Windows the camera moves as many steps a second as the loop makes passes:
// kWindowsPassesPerSecond, measured on legion (docs/reference/performance.md,
// "Input and frame rate"). Here the main thread shares the cooperative baton
// with the render thread and makes far fewer passes, so a held arrow key
// crawled. The edge of the screen scrolls by elapsed time (0x4050fc) and was
// never slow.
//
// So each step of a held key is scaled by the time since that key's previous
// step, counted in Windows passes: a key held for a second moves the camera as
// far as on Windows however often the loop runs here, and a loop that runs as
// often as Windows' scales by one. The first step of a press is one step, as
// on Windows. The scale goes on the arguments of the call that applies the
// step, at that one call site (the camera at 0x918cd0: 0x51e2e6 dx, dy from
// 0x5c9131; 0x51f2f3 zoom from 0x5c9258), so nothing else that moves the
// camera is touched. The camera is local to this machine: no simulation state
// and nothing on the network (docs/reference/simparity.md).
//
// RECOMP_EE_ORIGINAL_KEY_SCROLL=1 keeps one fixed step per pass.
#include "host/boot.h"
#include "mods/mods_internal.h"
#include "runtime/guest.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace {

constexpr uint32_t kUiStep = 0x00661889;     // one pass of the main loop: Refresh, then the UI
constexpr uint32_t kKeyScroll = 0x005c9070;  // EEUIEKeyboardScroll::Fire, ECX+0xc the direction
constexpr uint32_t kKeyZoom = 0x005c913c;    // EEUIEHotKeyZoom::Fire, ECX+0xc out (0) or in (1)
constexpr uint32_t kCameraMove = 0x0051e2e6; // camera (thiscall): move by (float, float), RET 8
constexpr uint32_t kCameraMoveFromKey = 0x005c9136; // ... its return address in kKeyScroll
constexpr uint32_t kCameraZoom = 0x0051f2f3;        // camera (thiscall): zoom to (float), RET 4
constexpr uint32_t kCameraZoomFromKey = 0x005c925d; // ... its return address in kKeyZoom
constexpr uint32_t kZoomState = 0x00918db0;         // camera +0xe0: the zoom, 0 (in) to 1 (out)

// The main loop's passes a second on Windows, in game with nothing selected
// (legion 0080).
constexpr double kWindowsPassesPerSecond = 485.0;
// A pass this much later than the last one is a stall, not a held key's
// motion: at most this much of it moves the camera.
constexpr double kLongestStepMs = 100.0;

struct HeldKey {
    uint64_t pass = 0; // the pass of the key's last step
    double ms = 0;     // and when it was
};
HeldKey g_scroll[4], g_zoom[2];
uint64_t g_pass = 0;
double g_scroll_scale = 1, g_zoom_scale = 1;

double now_ms() {
    using namespace std::chrono;
    return duration<double, std::milli>(steady_clock::now().time_since_epoch()).count();
}

// How many Windows passes this step stands for.
double scale_for(HeldKey &key) {
    const double now = now_ms();
    const bool held = key.pass && key.pass + 1 == g_pass;
    const double dt = std::min(now - key.ms, kLongestStepMs);
    key.pass = g_pass;
    key.ms = now;
    return held && dt > 0 ? dt * kWindowsPassesPerSecond / 1000.0 : 1.0;
}

float rdf(uint32_t a) {
    const uint32_t v = rd32(a);
    float f;
    memcpy(&f, &v, sizeof f);
    return f;
}
void wrf(uint32_t a, float f) {
    uint32_t v;
    memcpy(&v, &f, sizeof v);
    wr32(a, v);
}

void on_pass(const PopModApi *, pop_cpu_v1 *, PopHookInvocation *, void *) {
    ++g_pass;
}
void on_key_scroll(const PopModApi *, pop_cpu_v1 *cpu, PopHookInvocation *, void *) {
    g_scroll_scale = scale_for(g_scroll[rd32(cpu->ecx + 0xc) & 3]);
}
void on_key_zoom(const PopModApi *, pop_cpu_v1 *cpu, PopHookInvocation *, void *) {
    g_zoom_scale = scale_for(g_zoom[rd32(cpu->ecx + 0xc) ? 1 : 0]);
}
// [ESP] the return address, then the arguments.
void on_camera_move(const PopModApi *, pop_cpu_v1 *cpu, PopHookInvocation *, void *) {
    const float s = float(g_scroll_scale);
    wrf(cpu->esp + 4, rdf(cpu->esp + 4) * s);
    wrf(cpu->esp + 8, rdf(cpu->esp + 8) * s);
    g_scroll_scale = 1;
}
void on_camera_zoom(const PopModApi *, pop_cpu_v1 *cpu, PopHookInvocation *, void *) {
    const float now = rdf(kZoomState), to = rdf(cpu->esp + 4);
    wrf(cpu->esp + 4, std::clamp(now + (to - now) * float(g_zoom_scale), 0.0f, 1.0f));
    g_zoom_scale = 1;
}

void install() {
    const char *original = getenv("RECOMP_EE_ORIGINAL_KEY_SCROLL");
    if (original && *original && strcmp(original, "0") != 0) {
        printf("[ee] keys: the arrow keys and [ ] move the camera one step per main-loop pass\n");
        return;
    }
    uint32_t id = 0;
    struct {
        uint32_t addr, return_pc;
        PopHookFn fn;
    } const hooks[] = {{kUiStep, 0, on_pass},
                       {kKeyScroll, 0, on_key_scroll},
                       {kKeyZoom, 0, on_key_zoom},
                       {kCameraMove, kCameraMoveFromKey, on_camera_move},
                       {kCameraZoom, kCameraZoomFromKey, on_camera_zoom}};
    for (const auto &h : hooks) {
        const PopModStatus st =
            h.return_pc ? mods_hook_install_at_callsite(MODS_OWNER_RUNTIME, h.addr, h.return_pc,
                                                        h.fn, POP_HOOK_BEFORE, nullptr, &id)
                        : mods_hook_install_ex(MODS_OWNER_RUNTIME, h.addr, 0, h.fn, POP_HOOK_BEFORE,
                                               POP_HOOK_NO_GAME_VIEW, nullptr, &id);
        if (st != POP_OK) {
            fprintf(stderr,
                    "[ee] keys: cannot hook %08x (%d); held keys move the camera one step "
                    "per pass\n",
                    h.addr, int(st));
            return;
        }
    }
    mods_registry_pump_preentry();
    printf("[ee] keys: held arrow keys and [ ] move the camera at Windows' %.0f steps a second\n",
           kWindowsPassesPerSecond);
}

__attribute__((constructor)) void ee_input_init() {
    boot_add_load_step(install);
}

} // namespace
