/* inputprobe.c - keyboard, hotkey, sound and frame-rate probe (P7-C), a mod plugin.
 *
 * Writes one CSV row per event to RECOMP_EE_INPUT_PROBE, each stamped with
 * host milliseconds since the plugin started, so a run can say how often the
 * main thread runs the UI, how far one arrow-key step moves the camera, which
 * sounds a key started and which window messages it made. The Windows twin is
 * windows/frida/input_probe.js (legion request 0080); both count the same
 * EXE functions. docs/reference/performance.md, "Input and frame rate".
 *
 * Loaded only when asked for: app/ee_standins.cpp points RECOMP_MODS_DIR at
 * build/recomp/ee-mods when RECOMP_EE_INPUT_PROBE (or the simparity trace)
 * is set. Without that variable the plugin installs nothing. It only reads
 * guest memory: it changes nothing in the game.
 *
 * Rows (Empire Earth.exe, GOG, fixed base 0x400000):
 *   U,ms                    the main thread's input + UI step (0x661889: keyboard
 *                           and mouse Refresh, then UIManager::UpdateUI)
 *   S,ms,dir,x,y            EEUIEKeyboardScroll fire (0x5c9070): one camera step;
 *                           dir 0 up, 1 down, 2 left, 3 right; the camera's
 *                           look-at point (0x918d48/0x918d4c) before the step
 *   Z,ms                    EEUIEHotKeyZoom fire (0x5c913c)
 *   A,ms                    EEUIESelectClosestAircraft fire (0x5d9090: A, B, D, F)
 *   N,ms,id,caller          a sound looked up by id (0x405a58, before every
 *                           SESoundManager::Play of a game sound)
 *   M,ms,msg,wparam,lparam  a keyboard or IME message reaching the game window
 *                           (WndProc 0x6622dd)
 *   R,ms,x,y                a rendered frame (RTSGraphics::Render, 0x51015d) and
 *                           the camera's look-at point it starts from: how far
 *                           the camera moved per frame (tools/input/evenness.py)
 *   P,ms,pt                 once a second: the physics ticks the world has run
 *                           (EEPhysics at 0x919774, +0xb4), the game's own clock
 */
#include "pop_mod_api.h"

#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

POP_MOD_DECLARE_ABI();

#define VA_UI_STEP 0x00661889u
#define VA_KEY_SCROLL 0x005c9070u
#define VA_KEY_ZOOM 0x005c913cu
#define VA_AIRCRAFT 0x005d9090u
#define VA_SOUND_BY_ID 0x00405a58u
#define VA_WNDPROC 0x006622ddu
#define VA_RENDER 0x0051015du
#define VA_CAMERA_X 0x00918d48u  /* the camera (0x918cd0) +0x78/+0x7c: the look-at point */
#define VA_WORLD_PTR 0x00919774u /* EEPhysics * */
#define W_PT 0xb4u               /* PTs executed */

static FILE *g_out;
static double g_t0;

static double now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}
static double ms(void) {
    return now_ms() - g_t0;
}
static uint32_t rd(const PopModApi *api, uint32_t a) {
    uint32_t v = 0;
    if (api->guest_read_u32(api, a, &v) != POP_OK)
        return 0;
    return v;
}
static float rdf(const PopModApi *api, uint32_t a) {
    union {
        uint32_t u;
        float f;
    } v;
    v.u = rd(api, a);
    return v.f;
}

static double g_last_pt_row = -1000;

static void on_ui(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)cpu, (void)inv, (void)user;
    const double t = ms();
    fprintf(g_out, "U,%.1f\n", t);
    if (t - g_last_pt_row >= 1000) {
        const uint32_t world = rd(api, VA_WORLD_PTR);
        if (world) {
            fprintf(g_out, "P,%.1f,%u\n", t, rd(api, world + W_PT));
            g_last_pt_row = t;
        }
    }
}
static void on_scroll(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)inv, (void)user;
    fprintf(g_out, "S,%.1f,%u,%.5f,%.5f\n", ms(), rd(api, cpu->ecx + 0xc), rdf(api, VA_CAMERA_X),
            rdf(api, VA_CAMERA_X + 4));
}
static void on_zoom(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)api, (void)cpu, (void)inv, (void)user;
    fprintf(g_out, "Z,%.1f\n", ms());
}
static void on_aircraft(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)api, (void)cpu, (void)inv, (void)user;
    fprintf(g_out, "A,%.1f\n", ms());
}
static void on_sound(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)inv, (void)user;
    fprintf(g_out, "N,%.1f,%x,%08x\n", ms(), rd(api, cpu->esp + 4), rd(api, cpu->esp));
}
static void on_wndproc(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)inv, (void)user;
    const uint32_t msg = rd(api, cpu->esp + 8);
    /* WM_KEYDOWN..WM_SYSDEADCHAR, the IME composition messages, WM_IME_* */
    if ((msg >= 0x100 && msg <= 0x109) || (msg >= 0x10d && msg <= 0x10f) ||
        (msg >= 0x281 && msg <= 0x291))
        fprintf(g_out, "M,%.1f,%x,%x,%08x\n", ms(), msg, rd(api, cpu->esp + 12),
                rd(api, cpu->esp + 16));
}
static void on_render(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)cpu, (void)inv, (void)user;
    fprintf(g_out, "R,%.1f,%.5f,%.5f\n", ms(), rdf(api, VA_CAMERA_X), rdf(api, VA_CAMERA_X + 4));
}

/* No game view: the snapshot the kit takes for Populous's entity table means
 * nothing here. hook_install_ex is a size-gated v1 tail. */
static PopModStatus install(const PopModApi *api, uint32_t addr, PopHookFn fn) {
    uint32_t id = 0;
    if (api->size >= offsetof(PopModApi, hook_install_ex) + sizeof(api->hook_install_ex) &&
        api->hook_install_ex)
        return api->hook_install_ex(api, addr, 0, fn, POP_HOOK_BEFORE, POP_HOOK_NO_GAME_VIEW, NULL,
                                    &id);
    return api->hook_install(api, addr, fn, POP_HOOK_BEFORE, NULL, &id);
}

PopModStatus pop_mod_init(const PopModApi *api) {
    const char *path = getenv("RECOMP_EE_INPUT_PROBE");
    if (!path || !*path)
        return POP_OK;
    g_out = fopen(path, "w");
    if (!g_out)
        return POP_E_STATE;
    setvbuf(g_out, NULL, _IOFBF, 1 << 16);
    g_t0 = now_ms();
    fprintf(g_out, "# ee-inputprobe/1\n");
    static const struct {
        uint32_t addr;
        PopHookFn fn;
    } hooks[] = {{VA_UI_STEP, on_ui},        {VA_KEY_SCROLL, on_scroll}, {VA_KEY_ZOOM, on_zoom},
                 {VA_AIRCRAFT, on_aircraft}, {VA_SOUND_BY_ID, on_sound}, {VA_WNDPROC, on_wndproc},
                 {VA_RENDER, on_render}};
    for (size_t i = 0; i < sizeof hooks / sizeof hooks[0]; ++i) {
        PopModStatus st = install(api, hooks[i].addr, hooks[i].fn);
        if (st != POP_OK)
            fprintf(g_out, "# cannot hook %08x (%d)\n", hooks[i].addr, (int)st);
    }
    api->log(api, "inputprobe: writing keyboard, sound and frame events");
    return POP_OK;
}

PopModStatus pop_mod_exit(void) {
    if (g_out) {
        fprintf(g_out, "# exit at %.1f ms\n", ms());
        fclose(g_out);
        g_out = NULL;
    }
    return POP_OK;
}
