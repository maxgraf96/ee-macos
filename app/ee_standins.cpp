// ee_standins.cpp - what every host that boots Empire Earth adds to the kit:
// the windowed app (the kit's recomp_app, build/EmpireEarth.app) and the
// headless ee_boot alike. Installed from a static constructor, so the kit's
// own app picks it up by being linked with this file (app/CMakeLists.txt),
// before main() and before the kit registers its own shims - each stand-in
// yields to a kit shim the moment one exists (imports_register: a later
// non-null registration wins; a null one never replaces).
//
//   - The player's data: RECOMP_PROFILE_DIR (the file overlay's writable tier)
//     and RECOMP_REGISTRY default to ~/Library/Application Support/EmpireEarth
//     and its registry.json, seeded by tools/boot/seed_user.py. A developer
//     build would otherwise keep both under build/recomp/.
//   - TEMPORARY, Phase 4 (audio): the Miles calls the sound plugin makes that
//     the kit lacks (pending_mss32.cpp) and an inert DirectMusic
//     (pending_dmusic.cpp). The engine aborts on either missing.
//   - TEMPORARY, P3-C (user32): a thread's windows are destroyed when it ends,
//     as on Windows. The splash thread (EXE 0x5344fb) returns without
//     DestroyWindow; left alive, its window is the one host_main_window()
//     names, and so the one activated and sent WM_CLOSE.
//
// docs/reference/boot.md §5 lists each with its owner.
#include "platform/os.h"
#include "runtime/guest.h"
#include "runtime/imports.h"
#include "runtime/user32_internal.h"
#include "runtime/win32.h"

#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

void ee_pending_mss32_register();  // pending_mss32.cpp
void ee_pending_dmusic_register(); // pending_dmusic.cpp

namespace {

void destroy_thread_windows() {
    const uint32_t tid = guest_current_thread_id();
    std::vector<uint32_t> mine;
    for (const auto &kv : user32::windows())
        if (kv.second.thread == tid && !kv.second.parent)
            mine.push_back(kv.first);
    const uint32_t destroy = mine.empty() ? 0 : imports_resolve("USER32.dll", "DestroyWindow");
    for (uint32_t hwnd : mine)
        if (destroy && user32::find_window(hwnd)) {
            fprintf(stderr, "[ee] thread %u ended: destroying its window %08x\n", tid, hwnd);
            guest_call(guest_current_context(), destroy, hwnd);
        }
}

__attribute__((constructor)) void ee_standins_init() {
    if (!getenv("RECOMP_PROFILE_DIR")) {
        char dir[4096];
        if (os_user_data_dir("EmpireEarth", dir, sizeof dir) == 0) {
            os_mkdir(dir);
            setenv("RECOMP_PROFILE_DIR", dir, 1);
        }
    }
    if (!getenv("RECOMP_REGISTRY") && getenv("RECOMP_PROFILE_DIR"))
        setenv("RECOMP_REGISTRY",
               (std::string(getenv("RECOMP_PROFILE_DIR")) + "/registry.json").c_str(), 1);
    ee_pending_mss32_register();
    ee_pending_dmusic_register();
    sched_set_thread_exit_observer(destroy_thread_windows);
}

} // namespace
