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
//
// Nothing else is a stand-in now: Miles (mss32) and DirectMusic are the kit's
// (Phase 4), and so is destroying a thread's windows when it ends (P3-E).
// docs/reference/boot.md §5 has the history.
#include "platform/os.h"

#include <cstdlib>
#include <string>

namespace {

bool g_registry_defaulted = false;

__attribute__((constructor)) void ee_standins_init() {
    if (!getenv("RECOMP_PROFILE_DIR")) {
        char dir[4096];
        if (os_user_data_dir("EmpireEarth", dir, sizeof dir) == 0) {
            os_mkdir(dir);
            setenv("RECOMP_PROFILE_DIR", dir, 1);
        }
    }
    if (!getenv("RECOMP_REGISTRY") && getenv("RECOMP_PROFILE_DIR")) {
        setenv("RECOMP_REGISTRY",
               (std::string(getenv("RECOMP_PROFILE_DIR")) + "/registry.json").c_str(), 1);
        g_registry_defaulted = true;
    }
}

} // namespace

// Whether RECOMP_REGISTRY is this file's default rather than the caller's: a
// host with a user directory of its own (ee_boot --user) moves it there, since
// this constructor ran before the host's main() read its arguments.
extern "C" bool ee_standins_registry_defaulted() {
    return g_registry_defaulted;
}
