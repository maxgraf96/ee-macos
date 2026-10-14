// pending_mss32.cpp - TEMPORARY stand-ins for the Miles Sound System 6 calls
// "Miles Sound System Mixer.dll" makes that the kit's mss32 (dx/mss32.cpp)
// lacks. Audio is Phase 4 (docs/PLAN.md §7); until then the boot needs the
// mixer's initialisation to succeed, because the engine aborts when its sound
// plugin cannot open a driver (Low-Level Engine.dll+0x1c47e, abort()).
//
// AIL_open_digital_driver opens the kit's own driver through its
// AIL_waveOutOpen, so samples allocated on it are the kit's; everything else
// here answers the way a silent Miles does and says so once. Registered before
// the kit's tables: a kit implementation, when one exists, replaces these.
#include "runtime/guest.h"
#include "runtime/imports.h"
#include "runtime/memory.h"
#include "runtime/win32.h"
#include "platform/os.h"

#include <cstdio>
#include <set>
#include <string>

namespace {

void said(const char *what) {
    static std::set<std::string> seen;
    if (seen.insert(what).second)
        fprintf(stderr, "[ee pending mss32] %s (temporary stand-in, app/pending_mss32.cpp)\n",
                what);
}

uint32_t g_empty_string = 0, g_redist = 0;
uint32_t empty_string() {
    if (!g_empty_string)
        g_empty_string = heap_alloc(4, true);
    return g_empty_string;
}

// AIL_open_digital_driver(frequency, bits, channels, flags) -> HDIGDRIVER.
void open_digital_driver(X86 *c) {
    said("mss32!AIL_open_digital_driver -> the kit's AIL_waveOutOpen");
    uint32_t rate = arg(c, 0), bits = arg(c, 1), channels = arg(c, 2);
    uint32_t fn = imports_resolve("mss32.dll", "_AIL_waveOutOpen@16");
    uint32_t out = heap_alloc(8, true), fmt = heap_alloc(16, true);
    wr16(fmt, 1); // WAVE_FORMAT_PCM
    wr16(fmt + 2, (uint16_t)(channels ? channels : 2));
    wr32(fmt + 4, rate ? rate : 22050);
    wr16(fmt + 12, (uint16_t)((channels ? channels : 2) * (bits ? bits : 16) / 8));
    wr32(fmt + 8, rd32(fmt + 4) * rd16(fmt + 12));
    wr16(fmt + 14, (uint16_t)(bits ? bits : 16));
    uint32_t err = fn ? guest_call(c, fn, out, 0, 0xffffffffu, fmt) : 1;
    uint32_t driver = err ? 0 : rd32(out);
    heap_free(fmt);
    heap_free(out);
    set_eax(c, driver);
}
void close_digital_driver(X86 *c) {
    if (uint32_t fn = imports_resolve("mss32.dll", "_AIL_waveOutClose@4"))
        guest_call(c, fn, arg(c, 0));
    set_eax(c, 0);
}
// AIL_set_redist_directory(dir) -> the previous one (a string).
void set_redist_directory(X86 *c) {
    said("mss32!AIL_set_redist_directory (no providers are loaded)");
    uint32_t prev = g_redist ? g_redist : empty_string();
    g_redist = arg(c, 0);
    set_eax(c, prev);
}
void last_error(X86 *c) {
    set_eax(c, empty_string());
}
void ret0(X86 *c) {
    set_eax(c, 0);
}
void ret1(X86 *c) {
    set_eax(c, 1);
}
// AIL_get_DirectSound_info(sample, LPDIRECTSOUND *, LPDIRECTSOUNDBUFFER *):
// the DirectSound object Miles plays through. The mixer asks it for its
// hardware voice count (IDirectSound::GetCaps), so it is a real one: the
// kit's (DSOUND.dll!DirectSoundCreate), made once. No buffer.
uint32_t g_dsound = 0;
void get_directsound_info(X86 *c) {
    said("mss32!AIL_get_DirectSound_info -> the kit's DirectSoundCreate");
    if (!g_dsound) {
        uint32_t fn = imports_resolve("DSOUND.dll", "DirectSoundCreate");
        uint32_t out = heap_alloc(4, true);
        if (fn && !guest_call(c, fn, 0, out, 0))
            g_dsound = rd32(out);
        heap_free(out);
    }
    if (arg(c, 1))
        wr32(arg(c, 1), g_dsound);
    if (arg(c, 2))
        wr32(arg(c, 2), 0);
    set_eax(c, 0);
}
void mem_alloc_lock(X86 *c) {
    set_eax(c, heap_alloc(arg(c, 0) ? arg(c, 0) : 1, false));
}
// AIL_file_size(filename) -> bytes, -1 when missing.
void file_size(X86 *c) {
    std::string path = win32_host_path_op(gm_str(arg(c, 0)), WIN32_FILE_READ);
    OsStat st{};
    set_eax(c, !path.empty() && os_stat(path.c_str(), &st) == 0 && st.is_regular ? (uint32_t)st.size
                                                                                 : 0xffffffffu);
}
// AIL_sample_ms_position(sample, int *total, int *current).
void sample_ms_position(X86 *c) {
    said("mss32!AIL_sample_ms_position (silent: 0 ms)");
    if (arg(c, 1))
        wr32(arg(c, 1), 0);
    if (arg(c, 2))
        wr32(arg(c, 2), 0);
    set_eax(c, 0);
}
void sample_playback_rate(X86 *c) {
    set_eax(c, 22050);
}

// AIL_set_preference / AIL_get_preference with Miles 6's defaults. The kit's
// table starts at zero, and the mixer divides by DIG_DS_FRAGMENT_SIZE
// (Miles Sound System Mixer.dll+0x194f): 8 ms fragments, 96 of them, 8 mixed
// ahead, as Miles 6 ships them.
int32_t g_prefs[64] = {};
bool g_prefs_seeded = false;
void seed_prefs() {
    if (g_prefs_seeded)
        return;
    g_prefs_seeded = true;
    g_prefs[34] = 8;  // DIG_DS_FRAGMENT_SIZE (ms)
    g_prefs[35] = 96; // DIG_DS_FRAGMENT_CNT
    g_prefs[42] = 8;  // DIG_DS_MIX_FRAGMENT_CNT
}
void set_preference(X86 *c) {
    seed_prefs();
    uint32_t i = arg(c, 0);
    set_eax(c, i < 64 ? (uint32_t)g_prefs[i] : 0);
    if (i < 64)
        g_prefs[i] = (int32_t)arg(c, 1);
}
void get_preference(X86 *c) {
    seed_prefs();
    set_eax(c, arg(c, 0) < 64 ? (uint32_t)g_prefs[arg(c, 0)] : 0);
}

#define AIL(name, bytes, fn) {"mss32.dll", "_AIL_" #name "@" #bytes, (bytes) / 4, fn}
const ImportShim kPending[] = {
    AIL(open_digital_driver, 16, open_digital_driver),
    AIL(close_digital_driver, 4, close_digital_driver),
    AIL(set_redist_directory, 4, set_redist_directory),
    AIL(last_error, 0, last_error),
    AIL(digital_CPU_percent, 4, ret0),
    AIL(get_DirectSound_info, 12, get_directsound_info),
    AIL(set_DirectSound_HWND, 8, ret0),
    AIL(get_timer_highest_delay, 0, ret0),
    AIL(mem_alloc_lock, 4, mem_alloc_lock),
    AIL(file_size, 4, file_size),
    AIL(sample_granularity, 4, ret1),
    AIL(sample_ms_position, 12, sample_ms_position),
    AIL(sample_playback_rate, 4, sample_playback_rate),
    AIL(sample_position, 4, ret0),
    AIL(serve, 0, ret0),
    AIL(set_sample_playback_rate, 8, ret0),
    AIL(set_sample_position, 8, ret0),
};

const ImportShim kOverKit[] = {
    AIL(set_preference, 8, set_preference),
    AIL(get_preference, 4, get_preference),
};

} // namespace

void ee_pending_mss32_register() {
    imports_register(kPending, sizeof kPending / sizeof kPending[0]);
}
// After the kit's tables (boot_load): these replace the kit's own.
void ee_pending_mss32_over_kit() {
    imports_register(kOverKit, sizeof kOverKit / sizeof kOverKit[0]);
}
