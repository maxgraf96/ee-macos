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
 *                                      3 adds each unit's physics model: the
 *                                      float position bits, update count and
 *                                      target; 0 when a unit has no model)
 *   RECOMP_EE_CHECKSUM_UNITS=P[,P..]   per-unit rows after these PTs
 *   RECOMP_EE_CHECKSUM_UNITS_EVERY=N   ... and after every Nth PT
 *   RECOMP_EE_CHECKSUM_OOSDUMP=P       after PT P, the game's own OOS dump
 *                                      (WorldChecksum with dump = 1)
 *   RECOMP_EE_CHECKSUM_MAX_PT=N        stop writing rows after PT N
 *   RECOMP_EE_CHECKSUM_DRAWS=A-B       R rows for PTs A..B: every call of an
 *                                      EXE function that calls URandom, with
 *                                      its caller and the world RNG around it
 *   RECOMP_EE_CHECKSUM_TRACE_FNS=F,..  with DRAWS: "# call" lines for these EXE
 *                                      functions too (caller, ECX, three stack
 *                                      arguments, the world RNG around it)
 *   RECOMP_EE_CHECKSUM_MEM=P,A,L[;..]  after PT P, "# mem" lines: L bytes at the
 *                                      object whose pointer is at A (hex; e.g.
 *                                      91837c, player 2 of the player table)
 *   RECOMP_EE_SIMPARITY_AI_SCHED=1     "# sched" lines: every time a computer
 *                                      player schedules its next strategy
 *                                      review (EXE 0x43dcf4)
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
 *   R,pt,fn,caller,rng_in,rng_out             a call of a function that draws
 *                                             random numbers, in the PT window
 *                                             of RECOMP_EE_CHECKSUM_DRAWS
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
#define VA_CIV_EXPIRED 0x004b4f9cu   /* EEMCivTimerExpired execute (simulation thread) */
#define VA_CIV_SCREEN 0x00662f64u    /* the UI opens "Choose Civilization": PauseGame */
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

static _Thread_local int g_own_call; /* the tracer's own checksum calls are not W rows */
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

/* Flag 2 dereferences each unit's physics model ([unit+0x30]) without a null
 * check (EEComplexUnit 0x5ec507): only ask for it when every unit has one. */
static int units_have_models(const PopModApi *api) {
    uint32_t i = 0;
    for (uint32_t u = rd(api, VA_UNIT_LIST); u && i < 100000; u = next_unit(api, u), ++i)
        if (!rd(api, u + 0x30))
            return 0;
    return 1;
}

#define MAX_MEM 16
static struct {
    uint32_t pt, ptr_at, len;
} g_mem[MAX_MEM];
static uint32_t g_nmem;

/* "# mem PT ptr_at+off: 8 words" lines: the object behind a pointer, for
 * diffing hidden state between platforms (heap pointers differ, values do
 * not). */
static void write_mem(const PopModApi *api, uint32_t pt) {
    for (uint32_t i = 0; i < g_nmem; ++i) {
        if (g_mem[i].pt != pt)
            continue;
        uint32_t base = rd(api, g_mem[i].ptr_at);
        for (uint32_t off = 0; base && off < g_mem[i].len; off += 32) {
            fprintf(g_out, "# mem %u %08x+%04x:", pt, g_mem[i].ptr_at, off);
            for (uint32_t w = 0; w < 32 && off + w < g_mem[i].len; w += 4)
                fprintf(g_out, " %08x", rd(api, base + off + w));
            fprintf(g_out, "\n");
        }
    }
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
    for (uint32_t u = rd(api, VA_UNIT_LIST); u && i < 100000; u = next_unit(api, u), ++i) {
        int model = rd(api, u + 0x30) != 0 || !(g_flags2 & 2);
        fprintf(g_out, "U,%u,%u,%08x,%u,%d,%d,%d,%08x,%08x\n", pt, i, rd(api, u), rd(api, u + 0x24),
                (int)rd(api, u + 0x1c), (int)rd(api, u + 0x20), (int)rd(api, u + 0x3c),
                unit_checksum(api, u, 1),
                g_flags2 == 1 || !model ? 0 : unit_checksum(api, u, g_flags2));
    }
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
    int models = g_flags2 & 2 ? units_have_models(api) : 1;
    uint32_t cs2 = g_flags2 == 1 ? cs : models ? checksum(api, world, 0, g_flags2) : 0;
    fprintf(g_out, "T,%u,%u,%08x,%08x,%08x,%u,%u\n", pt, ct, cs, cs2, rd(api, world + W_RNG),
            rd(api, VA_INPUTS), units);
    if (units_wanted(pt))
        write_units(api, pt);
    if (g_nmem)
        write_mem(api, pt);
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

/* The civilization screen's pause (on request, with lockstep): the UI thread
 * opens the screen and calls NESynchronousServer::PauseGame, which applies
 * from the server's next CT. Hold the UI there until the simulation has run
 * its current CT to the end (at most 2 s), so the pause lands at the same PT
 * whatever the UI's speed. Both ends are logged as comments. */
static void civ_note(const PopModApi *api, const char *what) {
    uint32_t world = rd(api, VA_WORLD_PTR), comm = rd(api, VA_COMM_PTR);
    if (!world || !comm)
        return;
    fprintf(g_out, "# civ: %s at PT %u, PTs left %u, sim CT %u, server next CT %u\n", what,
            rd(api, world + W_PT), rd(api, world + W_PT_LEFT), rd(api, comm + COMM_CT),
            rd(api, comm + SRV_NEXT_CT));
}

static void before_civ_expired(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv,
                               void *user) {
    (void)inv;
    (void)user;
    uint8_t warning = 0;
    api->guest_read_u8(api, cpu->ecx + 0x10, &warning);
    civ_note(api, warning ? "timer warning runs" : "timer expiry runs");
}

static void before_civ_screen(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv,
                              void *user) {
    (void)cpu;
    (void)inv;
    (void)user;
    civ_note(api, "screen opens");
    uint32_t sleep_fn = rd(api, VA_IAT_SLEEP), one = 1, eax = 0, n = 0;
    for (; n < 2000; ++n) {
        uint32_t world = rd(api, VA_WORLD_PTR);
        if (!world || rd(api, world + W_PT_LEFT) == 0)
            break;
        if (!sleep_fn || api->guest_call(api, sleep_fn, 0, &one, 1, &eax) != POP_OK)
            break;
    }
    if (n)
        fprintf(g_out, "# civ: held the screen %u ms%s\n", n, n >= 2000 ? " (gave up)" : "");
    civ_note(api, "screen pauses");
}

static void after_civ_screen(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv,
                             void *user) {
    (void)cpu;
    (void)inv;
    (void)user;
    civ_note(api, "screen open");
}

/* The EXE functions that call URandom::Generate, URandom::NextInSeries,
 * URandomIntegers::Generate or U2DSparseArrayPointContainer::GetRandomPoint
 * (their import slots 0x8233b0, 0x823300, 0x82370c, 0x823380). */
static const uint32_t k_draw_fns[] = {
    0x0040a0c0u, 0x004115b4u, 0x00443d6cu, 0x004446a5u, 0x00444fbdu, 0x00447342u, 0x0044f807u,
    0x0044f23eu, 0x0045b064u, 0x0045a2dau, 0x0045ccc3u, 0x0045af8cu, 0x0045f4f5u, 0x00463340u,
    0x0046e0b9u, 0x0046f4ffu, 0x00474e85u, 0x00474cc8u, 0x004769f7u, 0x00483d97u, 0x0049400du,
    0x00495033u, 0x0049352au, 0x0049ca4au, 0x0049f5efu, 0x004aa0afu, 0x004ab812u, 0x004adb20u,
    0x004aef93u, 0x004d42fau, 0x004f6838u, 0x004f8c62u, 0x004f8c48u, 0x004f8c9cu, 0x00512fb0u,
    0x0051a7eau, 0x0051a9a3u, 0x0051a9feu, 0x0051c7bdu, 0x0051a9bdu, 0x00523cf6u, 0x00524554u,
    0x0052599bu, 0x00523739u, 0x005272beu, 0x005274a4u, 0x00547b6bu, 0x005531a8u, 0x00546c63u,
    0x005531c2u, 0x00593a23u, 0x00598b7au, 0x005ecd26u, 0x005ef9ffu, 0x005fbba0u, 0x005fff1bu,
    0x006083b2u, 0x00611946u, 0x006136fcu, 0x0061ad41u, 0x0063323fu, 0x00630e9cu, 0x0066b921u,
    0x0066d402u, 0x0066f0d5u, 0x0066ea64u
};
#define N_DRAW_FNS (sizeof k_draw_fns / sizeof k_draw_fns[0])
static uint32_t g_draws_from = 1, g_draws_to;
/* Per thread: a guest thread can hand the baton on inside a hooked call. */
static _Thread_local struct {
    uint32_t fn, caller, rng;
} g_draw_stack[64];
static _Thread_local uint32_t g_draw_depth;

/* The PT being run: EEPhysics+0xb4 counts the PTs done. */
static int draws_window(const PopModApi *api) {
    uint32_t world = rd(api, VA_WORLD_PTR);
    uint32_t pt = world ? rd(api, world + W_PT) + 1 : 0;
    return world && pt >= g_draws_from && pt <= g_draws_to;
}

static void before_draw_fn(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)inv;
    if (g_draw_depth < 64) {
        uint32_t world = rd(api, VA_WORLD_PTR);
        g_draw_stack[g_draw_depth].fn = (uint32_t)(uintptr_t)user;
        g_draw_stack[g_draw_depth].caller = draws_window(api) ? rd(api, cpu->esp) : 0;
        g_draw_stack[g_draw_depth].rng = world ? rd(api, world + W_RNG) : 0;
    }
    ++g_draw_depth;
}

static void after_draw_fn(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)cpu;
    (void)inv;
    (void)user;
    if (!g_draw_depth)
        return;
    --g_draw_depth;
    if (g_draw_depth >= 64 || !g_draw_stack[g_draw_depth].caller)
        return;
    uint32_t world = rd(api, VA_WORLD_PTR);
    fprintf(g_out, "R,%u,%08x,%08x,%08x,%08x\n", world ? rd(api, world + W_PT) + 1 : 0,
            g_draw_stack[g_draw_depth].fn, g_draw_stack[g_draw_depth].caller,
            g_draw_stack[g_draw_depth].rng, world ? rd(api, world + W_RNG) : 0);
}

/* Extra functions to trace in the draw window: comment lines, so the row
 * format stays the Windows twin's. */
static _Thread_local struct {
    uint32_t fn, caller, ecx, a0, a1, a2, rng;
} g_call_stack[64];
static _Thread_local uint32_t g_call_depth;

static void before_trace_fn(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv,
                            void *user) {
    (void)inv;
    if (g_call_depth < 64) {
        uint32_t world = rd(api, VA_WORLD_PTR);
        int on = draws_window(api);
        g_call_stack[g_call_depth].fn = (uint32_t)(uintptr_t)user;
        g_call_stack[g_call_depth].caller = on ? rd(api, cpu->esp) : 0;
        g_call_stack[g_call_depth].ecx = cpu->ecx;
        g_call_stack[g_call_depth].a0 = on ? rd(api, cpu->esp + 4) : 0;
        g_call_stack[g_call_depth].a1 = on ? rd(api, cpu->esp + 8) : 0;
        g_call_stack[g_call_depth].a2 = on ? rd(api, cpu->esp + 12) : 0;
        g_call_stack[g_call_depth].rng = world ? rd(api, world + W_RNG) : 0;
        if (on)
            fprintf(g_out, "# call> %08x from %08x ecx %08x args %08x %08x %08x\n",
                    g_call_stack[g_call_depth].fn, g_call_stack[g_call_depth].caller, cpu->ecx,
                    g_call_stack[g_call_depth].a0, g_call_stack[g_call_depth].a1,
                    g_call_stack[g_call_depth].a2);
        if (on && g_call_depth == 0) {
            /* A heuristic backtrace: the words on the stack that point into
             * the EXE's code (return addresses, and some stale ones). */
            fprintf(g_out, "# stack:");
            for (uint32_t a = cpu->esp, n = 0; a < cpu->esp + 0x1000 && n < 40; a += 4) {
                uint32_t w = rd(api, a);
                if (w >= 0x00401000u && w < 0x00823000u) {
                    fprintf(g_out, " %08x@+%x", w, a - cpu->esp);
                    ++n;
                }
            }
            fprintf(g_out, "\n");
        }
    }
    ++g_call_depth;
}

static void after_trace_fn(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv,
                           void *user) {
    (void)inv;
    (void)user;
    if (!g_call_depth)
        return;
    --g_call_depth;
    if (g_call_depth >= 64 || !g_call_stack[g_call_depth].caller)
        return;
    uint32_t world = rd(api, VA_WORLD_PTR);
    fprintf(g_out, "# call< %08x eax %08x rng %08x -> %08x (PT %u)\n", g_call_stack[g_call_depth].fn,
            cpu->eax, g_call_stack[g_call_depth].rng, world ? rd(api, world + W_RNG) : 0,
            world ? rd(api, world + W_PT) + 1 : 0);
}

/* A computer player schedules its next strategy review (0x43dcf4, thiscall
 * (uint now)): next = (int)(float)(now + [this+0xc5c] * 1000). */
static _Thread_local uint32_t g_sched_now;

static void before_ai_sched(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv,
                            void *user) {
    (void)inv;
    (void)user;
    g_sched_now = rd(api, cpu->esp + 4);
}

static void after_ai_sched(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv,
                           void *user) {
    (void)inv;
    (void)user;
    uint32_t world = rd(api, VA_WORLD_PTR);
    fprintf(g_out, "# sched: PT %u player %u now %u interval %08x next %u\n",
            world ? rd(api, world + W_PT) + 1 : 0, rd(api, cpu->ecx + 0x45c), g_sched_now,
            rd(api, cpu->ecx + 0xc5c), rd(api, cpu->ecx + 0xedc));
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
    const char *mem = getenv("RECOMP_EE_CHECKSUM_MEM");
    for (const char *p = mem; p && *p && g_nmem < MAX_MEM;) {
        char *end = NULL;
        uint32_t pt = (uint32_t)strtoul(p, &end, 0);
        if (*end != ',')
            break;
        uint32_t at = (uint32_t)strtoul(end + 1, &end, 16);
        if (*end != ',')
            break;
        uint32_t len = (uint32_t)strtoul(end + 1, &end, 0);
        g_mem[g_nmem].pt = pt;
        g_mem[g_nmem].ptr_at = at;
        g_mem[g_nmem].len = len;
        ++g_nmem;
        p = *end == ';' ? end + 1 : end;
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
    if (st == POP_OK && lockstep)
        st = install(api, VA_CIV_EXPIRED, before_civ_expired, POP_HOOK_BEFORE);
    if (st == POP_OK && lockstep)
        st = install(api, VA_CIV_SCREEN, before_civ_screen, POP_HOOK_BEFORE);
    if (st == POP_OK && lockstep)
        st = install(api, VA_CIV_SCREEN, after_civ_screen, POP_HOOK_AFTER);
    const char *sched = getenv("RECOMP_EE_SIMPARITY_AI_SCHED");
    if (st == POP_OK && sched && *sched && strcmp(sched, "0") != 0) {
        st = install(api, 0x0043dcf4u, before_ai_sched, POP_HOOK_BEFORE);
        if (st == POP_OK)
            st = install(api, 0x0043dcf4u, after_ai_sched, POP_HOOK_AFTER);
    }
    const char *draws = getenv("RECOMP_EE_CHECKSUM_DRAWS");
    if (st == POP_OK && draws && *draws) {
        char *end = NULL;
        g_draws_from = (uint32_t)strtoul(draws, &end, 0);
        g_draws_to = end && *end == '-' ? (uint32_t)strtoul(end + 1, NULL, 0) : g_draws_from;
        for (uint32_t i = 0; st == POP_OK && i < N_DRAW_FNS; ++i) {
            uint32_t id = 0;
            void *fn = (void *)(uintptr_t)k_draw_fns[i];
            st = api->hook_install_ex(api, k_draw_fns[i], 0, before_draw_fn, POP_HOOK_BEFORE,
                                      POP_HOOK_NO_GAME_VIEW, fn, &id);
            if (st == POP_OK)
                st = api->hook_install_ex(api, k_draw_fns[i], 0, after_draw_fn, POP_HOOK_AFTER,
                                          POP_HOOK_NO_GAME_VIEW, fn, &id);
        }
        fprintf(g_out, "# draws: R rows for PTs %u-%u\n", g_draws_from, g_draws_to);
        const char *fns = getenv("RECOMP_EE_CHECKSUM_TRACE_FNS");
        for (const char *p = fns; st == POP_OK && p && *p;) {
            char *end = NULL;
            uint32_t addr = (uint32_t)strtoul(p, &end, 16);
            if (end == p)
                break;
            uint32_t id = 0;
            void *fn = (void *)(uintptr_t)addr;
            st = api->hook_install_ex(api, addr, 0, before_trace_fn, POP_HOOK_BEFORE,
                                      POP_HOOK_NO_GAME_VIEW, fn, &id);
            if (st == POP_OK)
                st = api->hook_install_ex(api, addr, 0, after_trace_fn, POP_HOOK_AFTER,
                                          POP_HOOK_NO_GAME_VIEW, fn, &id);
            if (st != POP_OK)
                fprintf(g_out, "# trace: cannot hook %08x (%d)\n", addr, (int)st);
            st = POP_OK; /* a diagnostic: never fatal */
            p = *end == ',' ? end + 1 : end;
        }
    }
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
