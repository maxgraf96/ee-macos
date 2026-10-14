// pending_dmusic.cpp - TEMPORARY inert DirectMusic until Phase 4's
// (docs/reference/audio.md §2, docs/PLAN.md §7 Phase 4).
//
// The engine's SEConductor::InitializeConductor (engine+0x582e6) creates the
// loader, the composer and the performance with CoCreateInstance and checks
// every HRESULT with a VERIFY that survived into the release build: a failure
// prints "Verification failure" and calls abort() (engine+0x1c43e). So
// "DirectMusic is unavailable" is not a path the game has - a Windows without
// it would not boot either - and the boot needs DirectMusic that exists and
// plays nothing:
//
//   CLSID_DirectMusicLoader      IDirectMusicLoader (DX7)
//   CLSID_DirectMusicComposer    IDirectMusicComposer
//   CLSID_DirectMusicPerformance IDirectMusicPerformance (DX6.1), whose Init
//                                hands out an IDirectMusic with one default
//                                port (output class, DLS), IKsControl on it,
//   and the segments, styles and segment states a score load asks for.
//
// Every call succeeds and touches only the out-parameters audio.md lists;
// notifications never fire (no engine code registers a callback, §2.6), so
// GetNotificationPMsg answers S_FALSE. Served through the kit's
// com_register_guest_class (dx/com.h). Delete this file when Phase 4 lands.
#include "dx/com.h"
#include "runtime/guest.h"
#include "runtime/imports.h"
#include "runtime/memory.h"

#include <cstdio>
#include <cstring>
#include <map>
#include <set>
#include <string>

namespace {

const uint32_t S_OK_ = 0, S_FALSE_ = 1, E_NOINTERFACE_ = 0x80004002u, E_POINTER_ = 0x80004003u;

void said(const char *what) {
    static std::set<std::string> seen;
    if (seen.insert(what).second)
        fprintf(stderr, "[ee pending dmusic] %s\n", what);
}

struct Method {
    const char *name;
    uint8_t argc; // including this
    void (*fn)(X86 *);
};

// One guest object: a vtable pointer and a reference count the inert
// interfaces never act on.
std::map<uint32_t, uint32_t> g_kind; // object -> its vtable
uint32_t object_of(uint32_t vtable) {
    uint32_t self = heap_alloc(16, true);
    wr32(self, vtable);
    wr32(self + 4, 1);
    g_kind[self] = vtable;
    return self;
}
uint32_t vtable(const char *iface, const Method *m, size_t n) {
    static std::map<std::string, uint32_t> built;
    auto it = built.find(iface);
    if (it != built.end())
        return it->second;
    uint32_t vt = heap_alloc(4 * (uint32_t)n, true);
    for (size_t i = 0; i < n; ++i) {
        std::string name = std::string(iface) + "::" + m[i].name;
        wr32(vt + 4 * (uint32_t)i,
             imports_alloc_trampoline("DMUSIC.dll", strdup(name.c_str()), m[i].fn, m[i].argc));
    }
    built[iface] = vt;
    return vt;
}
#define VT(iface, table) vtable(iface, table, sizeof table / sizeof *table)

void ok(X86 *c) {
    set_eax(c, S_OK_);
}
void s_false(X86 *c) {
    set_eax(c, S_FALSE_);
}
void add_ref(X86 *c) {
    uint32_t self = arg(c, 0);
    wr32(self + 4, rd32(self + 4) + 1);
    set_eax(c, rd32(self + 4));
}
void release(X86 *c) {
    uint32_t self = arg(c, 0);
    uint32_t n = rd32(self + 4);
    wr32(self + 4, n ? n - 1 : 0);
    set_eax(c, n ? n - 1 : 0);
}
// Writes zero to the out-parameter at argument `i` and succeeds.
template <int I> void out_zero(X86 *c) {
    if (arg(c, I))
        wr32(arg(c, I), 0);
    set_eax(c, S_OK_);
}

uint32_t new_ks_control();
uint32_t new_style();
uint32_t new_segment();
uint32_t new_segment_state();

const uint8_t IID_IKsControl[16] = {0x85, 0x46, 0xf5, 0x28, 0xfd, 0x06, 0xd2, 0x11,
                                    0xb2, 0x7a, 0x00, 0xa0, 0xc9, 0x22, 0x31, 0x96};

// QueryInterface: the object itself for anything but IKsControl, which a port
// answers with a control object.
void query_interface(X86 *c) {
    uint32_t self = arg(c, 0), iid = arg(c, 1), out = arg(c, 2);
    if (!out) {
        set_eax(c, E_POINTER_);
        return;
    }
    if (iid && gm_valid(iid, 16) && !memcmp(g_mem + iid, IID_IKsControl, 16)) {
        wr32(out, new_ks_control());
        set_eax(c, S_OK_);
        return;
    }
    wr32(self + 4, rd32(self + 4) + 1);
    wr32(out, self);
    set_eax(c, S_OK_);
}
#define IUNKNOWN {"QueryInterface", 3, query_interface}, {"AddRef", 1, add_ref}, {"Release", 1, release}

// --- IKsControl --------------------------------------------------------------
// KsProperty(property, length, data, data_length, bytes_returned): a GET of
// the port's effects or volume reads back zeros.
void ks_property(X86 *c) {
    if (arg(c, 3) && arg(c, 4))
        memset(g_mem + arg(c, 3), 0, arg(c, 4));
    if (arg(c, 5))
        wr32(arg(c, 5), arg(c, 4));
    set_eax(c, S_OK_);
}
const Method kKs[] = {IUNKNOWN, {"KsProperty", 6, ks_property}, {"KsMethod", 6, ok}, {"KsEvent", 6, ok}};
uint32_t new_ks_control() {
    return object_of(VT("IKsControl", kKs));
}

// --- IDirectMusicPort ----------------------------------------------------------
// DMUS_PORTCAPS (0x134 bytes): the Microsoft Software Synthesizer as DX7 had it.
void put_port_caps(uint32_t p) {
    uint32_t size = rd32(p) ? rd32(p) : 0x134;
    memset(g_mem + p, 0, size);
    wr32(p, size);
    wr32(p + 4, 0x1 | 0x4 | 0x40 | 0x100); // DLS, SOFTWARESYNTH, DIRECTSOUND, SHAREABLE
    wr32(p + 8, 0x58c2b4d0);               // guidPort: GUID_DMUS_PROP... any fixed value
    wr32(p + 24, 1);                       // DMUS_PC_OUTPUTCLASS
    wr32(p + 28, 2);                       // DMUS_PORT_USER_MODE_SYNTH
    wr32(p + 36, 1000);                    // dwMaxChannelGroups
    wr32(p + 40, 32);                      // dwMaxVoices
    wr32(p + 44, 2);
    wr32(p + 48, 1); // DMUS_EFFECT_REVERB
    gm_put_wstr(p + 52, "Inert software synthesizer", 128);
}
void port_get_caps(X86 *c) {
    if (arg(c, 1))
        put_port_caps(arg(c, 1));
    set_eax(c, S_OK_);
}
void port_get_groups(X86 *c) {
    if (arg(c, 1))
        wr32(arg(c, 1), 8);
    set_eax(c, S_OK_);
}
const Method kPort[] = {
    IUNKNOWN,
    {"PlayBuffer", 2, ok},
    {"SetReadNotificationHandle", 2, ok},
    {"Read", 2, s_false},
    {"DownloadInstrument", 5, out_zero<2>},
    {"UnloadInstrument", 2, ok},
    {"GetLatencyClock", 2, out_zero<1>},
    {"GetRunningStats", 2, ok},
    {"Compact", 1, ok},
    {"GetCaps", 2, port_get_caps},
    {"DeviceIoControl", 8, ok},
    {"SetNumChannelGroups", 2, ok},
    {"GetNumChannelGroups", 2, port_get_groups},
    {"Activate", 2, ok},
    {"SetChannelPriority", 4, ok},
    {"GetChannelPriority", 4, out_zero<3>},
    {"SetDirectSound", 3, ok},
    {"GetFormat", 4, ok},
};

// --- IDirectMusic --------------------------------------------------------------
void dm_enum_port(X86 *c) {
    if (arg(c, 1) != 0) { // one port, index 0
        set_eax(c, S_FALSE_);
        return;
    }
    if (arg(c, 2))
        put_port_caps(arg(c, 2));
    set_eax(c, S_OK_);
}
void dm_create_port(X86 *c) {
    said("IDirectMusic::CreatePort: an inert software synthesizer");
    if (!arg(c, 3)) {
        set_eax(c, E_POINTER_);
        return;
    }
    wr32(arg(c, 3), object_of(VT("IDirectMusicPort", kPort)));
    set_eax(c, S_OK_);
}
void dm_default_port(X86 *c) {
    if (arg(c, 1)) {
        memset(g_mem + arg(c, 1), 0, 16);
        wr32(arg(c, 1), 0x58c2b4d0);
    }
    set_eax(c, S_OK_);
}
const Method kDM[] = {
    IUNKNOWN,
    {"EnumPort", 3, dm_enum_port},
    {"CreateMusicBuffer", 4, out_zero<2>},
    {"CreatePort", 5, dm_create_port},
    {"EnumMasterClock", 3, s_false},
    {"GetMasterClock", 3, out_zero<2>},
    {"SetMasterClock", 2, ok},
    {"Activate", 2, ok},
    {"GetDefaultPort", 2, dm_default_port},
    {"SetDirectSound", 3, ok},
};

// --- IDirectMusicStyle, IDirectMusicSegment, IDirectMusicSegmentState ------------
const Method kStyle[] = {
    IUNKNOWN,
    {"GetBand", 3, out_zero<2>},
    {"EnumBand", 3, s_false},
    {"GetDefaultBand", 2, out_zero<1>},
    {"EnumMotif", 3, s_false}, // no EE style has motifs (audio.md §2.3)
    {"GetMotif", 3, out_zero<2>},
    {"GetDefaultChordMap", 2, out_zero<1>},
    {"EnumChordMap", 3, s_false},
    {"GetChordMap", 3, out_zero<2>},
    {"GetTimeSignature", 2, ok},
    {"GetEmbellishmentLength", 5, ok},
    {"GetTempo", 2, ok},
};
uint32_t new_style() {
    return object_of(VT("IDirectMusicStyle", kStyle));
}
// GetParam(guid, group, index, time, next, param): GUID_IDirectMusicStyle asks
// for the style, which is an inert one; anything else reads back zero.
const uint8_t GUID_IDirectMusicStyle[16] = {0xa1, 0x28, 0xac, 0xd2, 0x9b, 0xb3, 0xd1, 0x11,
                                            0x87, 0x04, 0x00, 0x60, 0x08, 0x93, 0xb1, 0xbd};
void seg_get_param(X86 *c) {
    uint32_t guid = arg(c, 1), next = arg(c, 5), param = arg(c, 6);
    if (next)
        wr32(next, 0);
    if (param && guid && gm_valid(guid, 16) && !memcmp(g_mem + guid, GUID_IDirectMusicStyle, 16))
        wr32(param, new_style());
    else if (param)
        wr32(param, 0);
    set_eax(c, S_OK_);
}
void seg_get_repeats(X86 *c) {
    if (arg(c, 1))
        wr32(arg(c, 1), rd32(arg(c, 0) + 8));
    set_eax(c, S_OK_);
}
void seg_set_repeats(X86 *c) {
    wr32(arg(c, 0) + 8, arg(c, 1));
    set_eax(c, S_OK_);
}
const Method kSegment[] = {
    IUNKNOWN,
    {"GetLength", 2, out_zero<1>},
    {"SetLength", 2, ok},
    {"GetRepeats", 2, seg_get_repeats},
    {"SetRepeats", 2, seg_set_repeats},
    {"GetDefaultResolution", 2, out_zero<1>},
    {"SetDefaultResolution", 2, ok},
    {"GetTrack", 5, out_zero<4>},
    {"GetTrackGroup", 3, out_zero<2>},
    {"InsertTrack", 3, ok},
    {"RemoveTrack", 2, ok},
    {"InitPlay", 4, out_zero<1>},
    {"GetGraph", 2, out_zero<1>},
    {"SetGraph", 2, ok},
    {"AddNotificationType", 2, ok},
    {"RemoveNotificationType", 2, ok},
    {"GetParam", 7, seg_get_param},
    {"SetParam", 6, ok},
    {"Clone", 4, out_zero<3>},
    {"SetStartPoint", 2, ok},
    {"GetStartPoint", 2, out_zero<1>},
    {"SetLoopPoints", 3, ok},
    {"GetLoopPoints", 3, ok},
    {"SetPChannelsUsed", 3, ok},
};
uint32_t new_segment() {
    return object_of(VT("IDirectMusicSegment", kSegment));
}
void state_get_segment(X86 *c) {
    if (arg(c, 1))
        wr32(arg(c, 1), new_segment());
    set_eax(c, S_OK_);
}
const Method kState[] = {
    IUNKNOWN,
    {"GetRepeats", 2, out_zero<1>},
    {"GetSegment", 2, state_get_segment},
    {"GetStartTime", 2, out_zero<1>},
    {"GetSeek", 2, out_zero<1>},
    {"GetStartPoint", 2, out_zero<1>},
};
uint32_t new_segment_state() {
    return object_of(VT("IDirectMusicSegmentState", kState));
}

// --- IDirectMusicLoader ----------------------------------------------------------
// GetObject(desc, riid, ppv): every object a score load asks for is an inert
// segment (the engine asks for CLSID_DirectMusicSegment only).
void loader_get_object(X86 *c) {
    said("IDirectMusicLoader::GetObject: an inert segment");
    if (!arg(c, 3)) {
        set_eax(c, E_POINTER_);
        return;
    }
    wr32(arg(c, 3), new_segment());
    set_eax(c, S_OK_);
}
const Method kLoader[] = {
    IUNKNOWN,
    {"GetObject", 4, loader_get_object},
    {"SetObject", 2, ok},
    {"SetSearchDirectory", 4, ok},
    {"ScanDirectory", 4, ok},
    {"CacheObject", 2, ok},
    {"ReleaseObject", 2, ok},
    {"ClearCache", 2, ok},
    {"EnableCache", 3, ok},
    {"EnumObject", 4, s_false},
};

// --- IDirectMusicComposer -----------------------------------------------------------
// AutoTransition(perf, to_seg, command, flags, chordmap, pp_trans_seg,
// pp_to_seg_state, pp_trans_seg_state): nothing to transition.
void composer_auto_transition(X86 *c) {
    for (int i = 6; i <= 8; ++i)
        if (arg(c, i))
            wr32(arg(c, i), 0);
    set_eax(c, S_OK_);
}
const Method kComposer[] = {
    IUNKNOWN,
    {"ComposeSegmentFromTemplate", 6, out_zero<5>},
    {"ComposeSegmentFromShape", 9, out_zero<8>},
    {"ComposeTransition", 8, out_zero<7>},
    {"AutoTransition", 9, composer_auto_transition},
    {"ComposeTemplateFromShape", 7, out_zero<6>},
    {"ChangeChordMap", 4, ok},
};

// --- IDirectMusicPerformance (DX6.1) ---------------------------------------------------
// Init(ppDirectMusic, pDirectSound, hWnd): an IDirectMusic when asked for one.
void perf_init(X86 *c) {
    said("IDirectMusicPerformance::Init: inert DirectMusic");
    if (arg(c, 1) && !rd32(arg(c, 1)))
        wr32(arg(c, 1), object_of(VT("IDirectMusic", kDM)));
    set_eax(c, S_OK_);
}
// PlaySegment(seg, flags, i64 start, pp_state): 6 dwords with the 64-bit time.
void perf_play_segment(X86 *c) {
    if (arg(c, 5))
        wr32(arg(c, 5), new_segment_state());
    set_eax(c, S_OK_);
}
void perf_get_segment_state(X86 *c) {
    if (arg(c, 1))
        wr32(arg(c, 1), 0);
    set_eax(c, 0x88781200u | 0x7f); // DMUS_E_NOT_FOUND: nothing is playing
}
// GetTime(prtNow, pmtNow): reference time in 100 ns, music time in ticks.
void perf_get_time(X86 *c) {
    if (arg(c, 1)) {
        wr32(arg(c, 1), 0);
        wr32(arg(c, 1) + 4, 0);
    }
    if (arg(c, 2))
        wr32(arg(c, 2), 0);
    set_eax(c, S_OK_);
}
void perf_get_notification(X86 *c) {
    if (arg(c, 1))
        wr32(arg(c, 1), 0);
    set_eax(c, S_FALSE_);
}
void perf_alloc_pmsg(X86 *c) {
    if (arg(c, 2))
        wr32(arg(c, 2), heap_alloc(arg(c, 1) ? arg(c, 1) : 4, true));
    set_eax(c, S_OK_);
}
void perf_get_global(X86 *c) {
    if (arg(c, 2) && arg(c, 3))
        memset(g_mem + arg(c, 2), 0, arg(c, 3));
    set_eax(c, S_OK_);
}
const Method kPerformance[] = {
    IUNKNOWN,
    {"Init", 4, perf_init},
    {"PlaySegment", 6, perf_play_segment},
    {"Stop", 5, ok},
    {"GetSegmentState", 3, perf_get_segment_state},
    {"SetPrepareTime", 2, ok},
    {"GetPrepareTime", 2, out_zero<1>},
    {"SetBumperLength", 2, ok},
    {"GetBumperLength", 2, out_zero<1>},
    {"SendPMsg", 2, ok},
    {"MusicToReferenceTime", 3, ok},
    {"ReferenceToMusicTime", 4, ok},
    {"IsPlaying", 3, s_false},
    {"GetTime", 3, perf_get_time},
    {"AllocPMsg", 3, perf_alloc_pmsg},
    {"FreePMsg", 2, ok},
    {"GetGraph", 2, out_zero<1>},
    {"SetGraph", 2, ok},
    {"SetNotificationHandle", 4, ok},
    {"GetNotificationPMsg", 2, perf_get_notification},
    {"AddNotificationType", 2, ok},
    {"RemoveNotificationType", 2, ok},
    {"AddPort", 2, ok},
    {"RemovePort", 2, ok},
    {"AssignPChannelBlock", 4, ok},
    {"AssignPChannel", 5, ok},
    {"PChannelInfo", 5, ok},
    {"DownloadInstrument", 9, out_zero<3>},
    {"Invalidate", 3, ok},
    {"GetParam", 7, ok},
    {"SetParam", 6, ok},
    {"GetGlobalParam", 4, perf_get_global},
    {"SetGlobalParam", 4, ok},
    {"GetLatencyTime", 2, ok},
    {"GetQueueTime", 2, ok},
    {"AdjustTime", 3, ok},
    {"CloseDown", 1, ok},
    {"GetResolvedTime", 5, ok},
    {"MIDIToMusic", 6, ok},
    {"MusicToMIDI", 6, ok},
    {"TimeToRhythm", 7, ok},
    {"RhythmToTime", 7, ok},
};

uint32_t create_loader(X86 *, uint32_t, uint32_t *out) {
    *out = object_of(VT("IDirectMusicLoader", kLoader));
    return S_OK_;
}
uint32_t create_composer(X86 *, uint32_t, uint32_t *out) {
    *out = object_of(VT("IDirectMusicComposer", kComposer));
    return S_OK_;
}
uint32_t create_performance(X86 *, uint32_t, uint32_t *out) {
    *out = object_of(VT("IDirectMusicPerformance", kPerformance));
    return S_OK_;
}
uint32_t create_segment(X86 *, uint32_t, uint32_t *out) {
    *out = new_segment();
    return S_OK_;
}

// {D2AC28xx-B39B-11D1-8704-00600893B1BD}
void clsid(uint8_t out[16], uint8_t low) {
    const uint8_t base[16] = {0x00, 0x28, 0xac, 0xd2, 0x9b, 0xb3, 0xd1, 0x11,
                              0x87, 0x04, 0x00, 0x60, 0x08, 0x93, 0xb1, 0xbd};
    memcpy(out, base, 16);
    out[0] = low;
}

} // namespace

void ee_pending_dmusic_register() {
    uint8_t id[16];
    clsid(id, 0x92);
    com_register_guest_class(id, "DirectMusicLoader (inert)", create_loader);
    clsid(id, 0x90);
    com_register_guest_class(id, "DirectMusicComposer (inert)", create_composer);
    clsid(id, 0x81);
    com_register_guest_class(id, "DirectMusicPerformance (inert)", create_performance);
    clsid(id, 0x82);
    com_register_guest_class(id, "DirectMusicSegment (inert)", create_segment);
    (void)E_NOINTERFACE_;
}
