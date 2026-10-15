/* simparity.c - the single-player checksum-stream oracle (P4-C), a mod plugin.
 *
 * Calls the game's own world checksum, EEPhysics::WorldChecksum at EXE
 * 0x545ebf (thiscall, (char dump, uint flags), RET 8), once after every
 * physics tick (PT), and writes the stream to RECOMP_EE_CHECKSUM_TRACE. The
 * Windows twin is windows/frida/checksum_trace.js; both write the same rows,
 * and tools/simparity/compare.py compares them. Addresses, fields and why the
 * call has no effect on the simulation: docs/reference/simparity.md.
 *
 * Loaded only when asked for: app/ee_standins.cpp points RECOMP_MODS_DIR at
 * build/recomp/ee-mods when RECOMP_EE_CHECKSUM_TRACE is set. Without that
 * variable the plugin installs nothing.
 *
 * Environment:
 *   RECOMP_EE_CHECKSUM_TRACE=FILE      the stream (rows below)
 *   RECOMP_EE_SIMPARITY_SEED=N         force the game seed (EEMStartLoading
 *                                      +0x14: the map seed and the world RNG)
 *   RECOMP_EE_SIMPARITY_NO_REPORTS=1   no EEMReportStatistics: its timer is
 *                                      wall-clock (EXE 0x547365)
 *   RECOMP_EE_CHECKSUM_FLAGS=N         flags of the second checksum column
 *                                      (default 0x1, the network checksum's;
 *                                      2 adds each unit's physics fields, but
 *                                      only some classes may take it: see
 *                                      simparity.md)
 *   RECOMP_EE_CHECKSUM_UNITS=P[,P..]   per-unit rows after these PTs
 *   RECOMP_EE_CHECKSUM_UNITS_EVERY=N   ... and after every Nth PT
 *   RECOMP_EE_CHECKSUM_OOSDUMP=P       after PT P, the game's own OOS dump
 *                                      (WorldChecksum with dump = 1)
 *   RECOMP_EE_CHECKSUM_MAX_PT=N        stop writing rows after PT N
 *   RECOMP_EE_SIMPARITY_LOCKSTEP=1     the server sends a CT only once the
 *                                      simulation has run every PT it was
 *                                      sent (waits at most 60 s), so a message
 *                                      the simulation sends during CT n always
 *                                      runs in CT n+1, and a pause asked for
 *                                      meanwhile applies from CT n+1
 *                                      (simparity.md §4.3)
 *   RECOMP_EE_SIMPARITY_NO_CIV_TIMER=1 no civilization timer (EXE 0x54778a):
 *                                      no EEMCivTimerExpired, so no "Choose
 *                                      Civilization" screen and no pause
 *
 * Rows (CSV, one per line; '#' lines are comments):
 *   G,seed_orig,seed,humans                  a game starts (EEMStartLoading)
 *   C,ct,total_pt,pts,ptps,dt_bits,speed_bits a communications tick's PTs arrive
 *   T,pt,ct,cs,cs2,rng,inputs,units           after each PT
 *   S,pt,ct,type,slot                         a message sent to the server
 *   U,pt,i,vtable,id,x,y,hp,cs,cs2            one unit of the checksum list
 *   W,ct,pt,dump,flags,cs                     the game's own checksum call (a
 *                                             multiplayer CT; the host's value
 *                                             never goes on the wire)
 */
#include "pop_mod_api.h"

#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

POP_MOD_DECLARE_ABI();

/* Empire Earth.exe (GOG 2.00, fixed base 0x400000). */
#define VA_WORLD_PTR 0x00919774u     /* EEPhysics * (RTSPhysics subclass) */
#define VA_COMM_PTR 0x0091984cu      /* NESynchronousCommunications *: EEServer in SP */
#define VA_UNIT_LIST 0x00918318u     /* head of the checksummed object list */
#define VA_INPUTS 0x00919b68u        /* broadcast user-input messages executed */
#define VA_GAME_SPEED 0x009183c0u    /* float game-speed multiplier */
#define VA_CHECKSUM 0x00545ebfu      /* EEPhysics::WorldChecksum(char dump, uint flags) */
#define VA_PHYSICS_TICK 0x00546c63u  /* EEPhysics vtable slot 8: one PT */
#define VA_START_LOADING 0x004bee57u /* EEMStartLoading execute: the seed */
#define VA_REPORT_STATS 0x00547365u  /* the wall-clock statistics report timer */
#define VA_CIV_TIMER 0x0054778au     /* the civilization timer (EEMCivTimerExpired) */
#define VA_SEND_INPUT 0x00683bc8u    /* thunk: NESynchronousServer::ProcessLocalUserInput */
#define VA_CT_GATE 0x004c3703u       /* EEServer vtable +0x28, asked once per running CT */
#define VA_CT_GATE_RET 0x10054c6bu   /* ... from NESynchronousServer::SendCommunicationsTick */
#define VA_IAT_SLEEP 0x0082318cu     /* kernel32 Sleep */
#define SRV_NEXT_CT 0x190u           /* NESynchronousServer: the next CT to send */
#define W_PT_LEFT 0xb8u              /* PTs left of the current CT */

#define W_RNG 0x18u      /* URandom state (mRandomNumberGenerator) */
#define W_PTPS 0x58u     /* PTs per second (uint) */
#define W_TOTAL_PT 0x80u /* PTs scheduled by all CTs so far */
#define W_DT 0x98u       /* game milliseconds per PT (float) */
#define W_PT 0xb4u       /* PTs executed */
#define COMM_CT 0x10u    /* CTs processed by the simulation */

#define MAX_UNIT_PTS 64

static FILE *g_out;
static uint32_t g_flags2 = 0x1;
static uint32_t g_max_pt = 0xffffffffu;
static uint32_t g_unit_pts[MAX_UNIT_PTS];
static uint32_t g_unit_npts;
static uint32_t g_units_every;
static uint32_t g_oosdump_pt = 0xffffffffu;
static int g_have_seed;
static uint32_t g_seed;
static uint32_t g_last_pt = 0xffffffffu, g_last_total = 0xffffffffu;
static int g_done;
static uint32_t g_lock_waits, g_lock_timeouts;

static uint32_t rd(const PopModApi *api, uint32_t a) {
    uint32_t v = 0;
    if (api->guest_read_u32(api, a, &v) != POP_OK)
        return 0;
    return v;
}

static uint32_t env_u32(const char *name, uint32_t def) {
    const char *s = getenv(name);
    return s && *s ? (uint32_t)strtoul(s, NULL, 0) : def;
}

static int g_own_call; /* the tracer's own checksum calls are not W rows */
static uint32_t g_game_dump, g_game_flags;

static uint32_t checksum(const PopModApi *api, uint32_t world, uint32_t dump, uint32_t flags) {
    uint32_t args[2] = {dump, flags}, eax = 0;
    g_own_call = 1;
    PopModStatus st = api->guest_call(api, VA_CHECKSUM, world, args, 2, &eax);
    g_own_call = 0;
    return st == POP_OK ? eax : 0;
}

/* The game's own calls of the checksum (multiplayer: once per CT from CT 40,
 * in the simulation thread's message pump): its arguments on entry, its value
 * on return. */
static void before_game_checksum(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv,
                                 void *user) {
    (void)inv;
    (void)user;
    if (g_own_call)
        return;
    g_game_dump = rd(api, cpu->esp + 4) & 0xff;
    g_game_flags = rd(api, cpu->esp + 8);
}

static void after_game_checksum(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv,
                                void *user) {
    (void)inv;
    (void)user;
    if (g_own_call)
        return;
    uint32_t world = rd(api, VA_WORLD_PTR), comm = rd(api, VA_COMM_PTR);
    fprintf(g_out, "W,%u,%u,%u,%u,%08x\n", comm ? rd(api, comm + COMM_CT) : 0,
            world ? rd(api, world + W_PT) : 0, g_game_dump, g_game_flags, cpu->eax);
}

/* The checksum's object list: node = *(VA_UNIT_LIST), next = **(node + 8). */
static uint32_t next_unit(const PopModApi *api, uint32_t unit) {
    uint32_t link = rd(api, unit + 8);
    return link ? rd(api, link) : 0;
}

static uint32_t unit_checksum(const PopModApi *api, uint32_t unit, uint32_t flags) {
    uint32_t vt = rd(api, unit), fn = vt ? rd(api, vt + 0x4c) : 0, eax = 0;
    if (!fn || api->guest_call(api, fn, unit, &flags, 1, &eax) != POP_OK)
        return 0;
    return eax;
}

static int units_wanted(uint32_t pt) {
    if (g_units_every && pt % g_units_every == 0)
        return 1;
    for (uint32_t i = 0; i < g_unit_npts; ++i)
        if (g_unit_pts[i] == pt)
            return 1;
    return 0;
}

static void write_units(const PopModApi *api, uint32_t pt) {
    uint32_t i = 0;
    for (uint32_t u = rd(api, VA_UNIT_LIST); u && i < 100000; u = next_unit(api, u), ++i)
        fprintf(g_out, "U,%u,%u,%08x,%u,%d,%d,%d,%08x,%08x\n", pt, i, rd(api, u), rd(api, u + 0x24),
                (int)rd(api, u + 0x1c), (int)rd(api, u + 0x20), (int)rd(api, u + 0x3c),
                unit_checksum(api, u, 1), g_flags2 == 1 ? 0 : unit_checksum(api, u, g_flags2));
}

/* After each call of the PT entry: a row when a PT ran. */
static void after_tick(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)cpu;
    (void)inv;
    (void)user;
    uint32_t world = rd(api, VA_WORLD_PTR), comm = rd(api, VA_COMM_PTR);
    if (!world || g_done)
        return;
    uint32_t pt = rd(api, world + W_PT), total = rd(api, world + W_TOTAL_PT);
    uint32_t ct = comm ? rd(api, comm + COMM_CT) : 0;
    if (g_last_total == 0xffffffffu && comm)
        fprintf(g_out,
                "# rate: options max=%u min=%u share=%u; server max=%u min=%u factor=%08x "
                "interval=%u/%u pts=%u ptps=%u\n",
                rd(api, 0x00919408u), rd(api, 0x0091940cu), rd(api, 0x00919424u),
                rd(api, comm + 0x1b4), rd(api, comm + 0x1b8), rd(api, comm + 0x170),
                rd(api, comm + 0x198), rd(api, comm + 0x19c), rd(api, comm + 0x1f8) >> 8 & 0xff,
                rd(api, comm + 0x1f8) >> 24 & 0xff);
    if (total != g_last_total) {
        uint32_t speed = rd(api, VA_GAME_SPEED);
        fprintf(g_out, "C,%u,%u,%d,%u,%08x,%08x\n", ct, total,
                g_last_total == 0xffffffffu ? (int)total : (int)(total - g_last_total),
                rd(api, world + W_PTPS), rd(api, world + W_DT), speed);
        g_last_total = total;
    }
    if (pt == g_last_pt)
        return;
    if (pt < g_last_pt && g_last_pt != 0xffffffffu)
        fprintf(g_out, "# pt went back from %u to %u: a new game\n", g_last_pt, pt);
    g_last_pt = pt;
    uint32_t units = 0;
    for (uint32_t u = rd(api, VA_UNIT_LIST); u && units < 100000; u = next_unit(api, u))
        ++units;
    uint32_t cs = checksum(api, world, 0, 1);
    uint32_t cs2 = g_flags2 == 1 ? cs : checksum(api, world, 0, g_flags2);
    fprintf(g_out, "T,%u,%u,%08x,%08x,%08x,%u,%u\n", pt, ct, cs, cs2, rd(api, world + W_RNG),
            rd(api, VA_INPUTS), units);
    if (units_wanted(pt))
        write_units(api, pt);
    if (pt == g_oosdump_pt) {
        checksum(api, world, 1, 1);
        fprintf(g_out, "# OOS dump written after PT %u (the game's OOS *.log)\n", pt);
    }
    if (pt >= g_max_pt) {
        fprintf(g_out, "# end after PT %u\n", pt);
        fflush(g_out);
        g_done = 1;
    }
}

/* EEMStartLoading execute (ECX = the message): its seed at +0x14 becomes the
 * world RNG state (EEPhysics+0x18) and the map seed (*(0x91977c)+0x54). */
static void before_start_loading(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv,
                                 void *user) {
    (void)inv;
    (void)user;
    uint32_t orig = rd(api, cpu->ecx + 0x14);
    uint8_t humans = 0;
    api->guest_read_u8(api, cpu->ecx + 0x10, &humans);
    if (g_have_seed)
        api->guest_write_u32(api, cpu->ecx + 0x14, g_seed);
    fprintf(g_out, "G,%u,%u,%u\n", orig, g_have_seed ? g_seed : orig, humans);
    g_last_pt = g_last_total = 0xffffffffu;
    g_done = 0;
}

/* The statistics report or the civilization timer: skipped (a plain RET; the
 * functions take no stack arguments). */
static void skip_report(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)inv;
    (void)user;
    api->hook_return(api, cpu, cpu->eax, 0);
}

/* Every message the EXE hands the server (thiscall: [esp+4] message, [esp+8]
 * slot). The type is the message's byte +8. */
static void before_send(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)inv;
    (void)user;
    uint32_t world = rd(api, VA_WORLD_PTR), comm = rd(api, VA_COMM_PTR);
    uint32_t msg = rd(api, cpu->esp + 4), slot = rd(api, cpu->esp + 8) & 0xff;
    uint8_t type = 0;
    if (msg)
        api->guest_read_u8(api, msg + 8, &type);
    fprintf(g_out, "S,%u,%u,%02x,%02x\n", world ? rd(api, world + W_PT) : 0,
            comm ? rd(api, comm + COMM_CT) : 0, type, slot);
}

/* Lockstep (on request): on the server's send thread, before it builds a CT
 * (only the call from SendCommunicationsTick), wait until the simulation has
 * started every CT sent so far and run all of their PTs. Waiting is the
 * guest's own Sleep(1), which hands the baton on. */
static void before_ct_gate(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv,
                           void *user) {
    (void)inv;
    (void)user;
    uint32_t sleep_fn = rd(api, VA_IAT_SLEEP), one = 1, eax = 0, n = 0;
    for (;;) {
        uint32_t world = rd(api, VA_WORLD_PTR), comm = rd(api, VA_COMM_PTR);
        if (!world || !comm)
            return;
        if (rd(api, comm + COMM_CT) >= rd(api, cpu->ecx + SRV_NEXT_CT) &&
            rd(api, world + W_PT_LEFT) == 0)
            break;
        if (++n > 60000) {
            ++g_lock_timeouts;
            fprintf(g_out, "# lockstep: gave up waiting at CT %u (sim CT %u, PTs left %u)\n",
                    rd(api, cpu->ecx + SRV_NEXT_CT), rd(api, comm + COMM_CT),
                    rd(api, world + W_PT_LEFT));
            return;
        }
        if (!sleep_fn || api->guest_call(api, sleep_fn, 0, &one, 1, &eax) != POP_OK)
            return;
    }
    if (n)
        ++g_lock_waits;
    if (n >= 1000)
        fprintf(g_out, "# lockstep: waited %u ms before CT %u\n", n, rd(api, cpu->ecx + SRV_NEXT_CT));
}

/* No game view: the snapshot the kit takes for Populous's entity table means
 * nothing here. hook_install_ex is a size-gated v1 tail. */
static PopModStatus install_at(const PopModApi *api, uint32_t addr, uint32_t return_pc, PopHookFn fn,
                               int32_t mode) {
    uint32_t id = 0;
    if (api->size >= offsetof(PopModApi, hook_install_ex) + sizeof(api->hook_install_ex) &&
        api->hook_install_ex)
        return api->hook_install_ex(api, addr, return_pc, fn, mode, POP_HOOK_NO_GAME_VIEW, NULL,
                                    &id);
    if (return_pc)
        return POP_E_ABI;
    return api->hook_install(api, addr, fn, mode, NULL, &id);
}

static PopModStatus install(const PopModApi *api, uint32_t addr, PopHookFn fn, int32_t mode) {
    return install_at(api, addr, 0, fn, mode);
}

PopModStatus pop_mod_init(const PopModApi *api) {
    const char *path = getenv("RECOMP_EE_CHECKSUM_TRACE");
    if (!path || !*path)
        return POP_OK; /* not asked for: install nothing */
    g_out = fopen(path, "w");
    if (!g_out) {
        api->log(api, "simparity: cannot open RECOMP_EE_CHECKSUM_TRACE");
        return POP_E_INVAL;
    }
    setvbuf(g_out, NULL, _IOLBF, 0);
    g_flags2 = env_u32("RECOMP_EE_CHECKSUM_FLAGS", 0x1);
    g_max_pt = env_u32("RECOMP_EE_CHECKSUM_MAX_PT", 0xffffffffu);
    g_units_every = env_u32("RECOMP_EE_CHECKSUM_UNITS_EVERY", 0);
    g_oosdump_pt = env_u32("RECOMP_EE_CHECKSUM_OOSDUMP", 0xffffffffu);
    const char *pts = getenv("RECOMP_EE_CHECKSUM_UNITS");
    for (const char *p = pts; p && *p && g_unit_npts < MAX_UNIT_PTS;) {
        char *end = NULL;
        unsigned long v = strtoul(p, &end, 0);
        if (end == p)
            break;
        g_unit_pts[g_unit_npts++] = (uint32_t)v;
        p = *end == ',' ? end + 1 : end;
    }
    const char *seed = getenv("RECOMP_EE_SIMPARITY_SEED");
    if (seed && *seed) {
        g_have_seed = 1;
        g_seed = (uint32_t)strtoul(seed, NULL, 0);
    }
    const char *no_reports = getenv("RECOMP_EE_SIMPARITY_NO_REPORTS");
    int skip = no_reports && *no_reports && strcmp(no_reports, "0") != 0;
    const char *ls = getenv("RECOMP_EE_SIMPARITY_LOCKSTEP");
    int lockstep = ls && *ls && strcmp(ls, "0") != 0;
    const char *nc = getenv("RECOMP_EE_SIMPARITY_NO_CIV_TIMER");
    int no_civ = nc && *nc && strcmp(nc, "0") != 0;
    fprintf(g_out,
            "# ee-simparity/1 mac flags2=0x%x seed=%s no_reports=%d lockstep=%d no_civ_timer=%d\n",
            g_flags2, g_have_seed ? seed : "game", skip, lockstep, no_civ);

    PopModStatus st = install(api, VA_PHYSICS_TICK, after_tick, POP_HOOK_AFTER);
    if (st == POP_OK)
        st = install(api, VA_START_LOADING, before_start_loading, POP_HOOK_BEFORE);
    if (st == POP_OK)
        st = install(api, VA_SEND_INPUT, before_send, POP_HOOK_BEFORE);
    if (st == POP_OK)
        st = install(api, VA_CHECKSUM, before_game_checksum, POP_HOOK_BEFORE);
    if (st == POP_OK)
        st = install(api, VA_CHECKSUM, after_game_checksum, POP_HOOK_AFTER);
    if (st == POP_OK && skip)
        st = install(api, VA_REPORT_STATS, skip_report, POP_HOOK_BEFORE);
    if (st == POP_OK && no_civ)
        st = install(api, VA_CIV_TIMER, skip_report, POP_HOOK_BEFORE);
    if (st == POP_OK && lockstep)
        st = install_at(api, VA_CT_GATE, VA_CT_GATE_RET, before_ct_gate, POP_HOOK_BEFORE);
    if (st != POP_OK) {
        fprintf(g_out, "# hook install failed: %d\n", (int)st);
        fclose(g_out);
        g_out = NULL;
        return st;
    }
    api->log(api, "simparity: tracing the world checksum");
    return POP_OK;
}

PopModStatus pop_mod_exit(void) {
    if (g_out) {
        fprintf(g_out, "# exit (lockstep waits %u, timeouts %u)\n", g_lock_waits, g_lock_timeouts);
        fclose(g_out);
        g_out = NULL;
    }
    return POP_OK;
}
