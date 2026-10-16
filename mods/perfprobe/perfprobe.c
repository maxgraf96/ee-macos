/* perfprobe.c - the performance probe (P7-B), a mod plugin.
 *
 * The Mac twin of windows/frida/perf_probe.js's S rows: every
 * RECOMP_EE_PERF_MS milliseconds (default 250) a host thread writes the
 * physics ticks (PTs) the game has executed so far, so a run's simulation
 * rate (PT/s) can be set beside Windows'. It installs no hook and calls no
 * guest code: the counter is one aligned 32-bit read of EEPhysics+0xb4
 * (EEPhysics at [0x919774]), which needs no baton. Nothing in the game
 * changes. docs/reference/performance.md.
 *
 * Environment:
 *   RECOMP_EE_PERF_TRACE=FILE   the rows below
 *   RECOMP_EE_PERF_MS=N         the sampling interval (default 250)
 *
 * Rows (CSV; '#' lines are comments):
 *   S,t_ms,epoch_ms,pts,ptps,game_ms
 *       t_ms: host monotonic ms since the plugin loaded; epoch_ms: the wall
 *       clock; pts: PTs executed (EEPhysics+0xb4), -1 with no game; ptps: the
 *       PT rate the game runs at (+0x58); game_ms: the game time (+0x94, a
 *       float), so game seconds per wall second say whether it keeps up
 */
#include "pop_mod_api.h"

#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

POP_MOD_DECLARE_ABI();

#define VA_WORLD_PTR 0x00919774u /* EEPhysics * (RTSPhysics subclass) */
#define W_PT 0xb4u               /* PTs executed */
#define W_PTPS 0x58u             /* PTs per second (uint) */
#define W_GAME_TIME 0x94u        /* game time, ms (float) */

static FILE *g_out;
static const PopModApi *g_api;
static pthread_t g_thread;
static volatile int g_stop;
static unsigned g_ms = 250;

static double mono_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

static long long epoch_ms(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (long long)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

static void sample(FILE *f, double t, long long epoch) {
    uint32_t world = 0, pt = 0, ptps = 0, game = 0;
    if (g_api->guest_read_u32(g_api, VA_WORLD_PTR, &world) != POP_OK || !world ||
        g_api->guest_read_u32(g_api, world + W_PT, &pt) != POP_OK) {
        fprintf(f, "S,%.3f,%lld,-1,0,0\n", t, epoch);
        return;
    }
    g_api->guest_read_u32(g_api, world + W_PTPS, &ptps);
    g_api->guest_read_u32(g_api, world + W_GAME_TIME, &game);
    float game_ms;
    memcpy(&game_ms, &game, 4);
    fprintf(f, "S,%.3f,%lld,%u,%u,%.1f\n", t, epoch, pt, ptps, (double)game_ms);
}

static void *sampler(void *arg) {
    (void)arg;
    const double t0 = mono_ms();
    while (!g_stop) {
        sample(g_out, mono_ms() - t0, epoch_ms());
        fflush(g_out);
        usleep(g_ms * 1000u);
    }
    return NULL;
}

PopModStatus pop_mod_init(const PopModApi *api) {
    const char *path = getenv("RECOMP_EE_PERF_TRACE");
    if (!path || !*path)
        return POP_OK; /* not asked for: nothing runs */
    const char *ms = getenv("RECOMP_EE_PERF_MS");
    if (ms && *ms && atoi(ms) > 0)
        g_ms = (unsigned)atoi(ms);
    g_out = fopen(path, "w");
    if (!g_out) {
        api->log(api, "perfprobe: cannot open RECOMP_EE_PERF_TRACE");
        return POP_E_INVAL;
    }
    fprintf(g_out, "# perfprobe: every %u ms\n", g_ms);
    g_api = api;
    if (pthread_create(&g_thread, NULL, sampler, NULL) != 0) {
        fclose(g_out);
        g_out = NULL;
        return POP_E_INVAL;
    }
    api->log(api, "perfprobe: sampling the PT counter");
    return POP_OK;
}

PopModStatus pop_mod_exit(void) {
    if (g_out) {
        g_stop = 1;
        pthread_join(g_thread, NULL);
        fclose(g_out);
        g_out = NULL;
    }
    return POP_OK;
}
