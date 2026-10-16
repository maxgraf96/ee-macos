// ee_sched.cpp - Empire Earth's threads by name in the kit's scheduler trace
// (RECOMP_SCHED_TRACE, kit runtime/sched_trace.h), and the simulation's
// progress in every window report. Linked into every host that boots the game
// (app/CMakeLists.txt, with the stand-ins); installed from a static
// constructor and consulted only while the trace is on.
//
// Every thread the game starts goes through the engine's TSThread trampoline
// (engine+0x5f5d0), whose parameter block holds the body at +0x28
// (docs/reference/boot.md, "Threads"). The bodies' roles are legion's
// (docs/reference/legion.md §5.4); a body not listed is named by its
// module+offset.
//
// The report line: PTs executed (EEPhysics+0xb4) and their rate over the
// window, PTs scheduled (+0x80), the PT rate the server chose (+0x58) and the
// game's own measured PT time (+0xd8, +0xdc), EEPhysics at [0x919774]
// (docs/reference/simparity.md §3, §4.1).
#include "runtime/guest.h"
#include "runtime/loader.h"
#include "runtime/sched_trace.h"

#include <cstdio>
#include <cstring>
#include <string>

namespace {

const uint32_t kTsThreadTrampoline = 0x1005f5d0;
const uint32_t kPhysics = 0x919774;

struct Role {
    uint32_t body;
    const char *name;
};
const Role kRoles[] = {
    {0x005344fb, "splash"},
    {0x10057f9d, "dmusic EventHandler"},
    {0x1005ba83, "Miles Scavenger"},
    {0x10502835, "Bink movie"},
    {0x005270bf, "music intensity"},
    {0x005110fe, "input (ISMouse)"},
    {0x0050fe16, "render"},
    {0x004ce947, "texture loader"},
    {0x005532fb, "simulation"},
    {0x100526f0, "net client Main"},
    {0x10052d3f, "net client Receive"},
};

std::string where(uint32_t a) {
    char buf[96];
    const LoaderModule *m = loader_module_containing(a);
    if (m)
        snprintf(buf, sizeof buf, "%s+%x", m->base == loader_image_base() ? "EXE" : m->name.c_str(),
                 a - m->base);
    else
        snprintf(buf, sizeof buf, "%08x", a);
    return buf;
}

const char *name_thread(uint32_t start, uint32_t param) {
    static std::string out;
    uint32_t body = start;
    if (start == kTsThreadTrampoline && param && gm_valid(param + 0x28, 4))
        body = rd32(param + 0x28);
    for (const Role &r : kRoles)
        if (r.body == body)
            return r.name;
    out = (body == start ? "start " : "TSThread ") + where(body);
    return out.c_str();
}

double g_prev_t = 0.0;
uint32_t g_prev_pt = 0;

void report(FILE *out, double now) {
    uint32_t ph = gm_valid(kPhysics, 4) ? rd32(kPhysics) : 0;
    if (!ph || !gm_valid(ph, 0x100)) {
        fprintf(out, "# ee: no game\n");
        g_prev_t = 0.0;
        return;
    }
    const uint32_t pt = rd32(ph + 0xb4);
    float d8, dc;
    uint32_t r8 = rd32(ph + 0xd8), rc = rd32(ph + 0xdc);
    memcpy(&d8, &r8, 4);
    memcpy(&dc, &rc, 4);
    double rate = g_prev_t > 0.0 && now > g_prev_t && pt >= g_prev_pt
                      ? (pt - g_prev_pt) / (now - g_prev_t)
                      : 0.0;
    fprintf(out, "# ee: PT %u (%.1f PT/s over the window), scheduled %u, PT rate %u/s, "
                 "measured PT time +0xd8 %08x (%g) +0xdc %08x (%g)\n",
            pt, rate, rd32(ph + 0x80), rd32(ph + 0x58), r8, (double)d8, rc, (double)dc);
    g_prev_t = now;
    g_prev_pt = pt;
}

__attribute__((constructor)) void ee_sched_init() {
    sched_set_thread_namer(name_thread);
    sched_set_trace_reporter(report);
}

} // namespace
