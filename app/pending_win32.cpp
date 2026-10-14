// pending_win32.cpp - TEMPORARY stand-ins for user32/gdi32/dinput/imm32 gaps
// that block the headless boot. They belong to P3-C (docs/phase3.md); each is
// listed in docs/reference/boot.md §5 for its owner.
//
// Registered before loader_load runs imports_init, so a shim the kit has
// always replaces the one here (imports_register: a later non-null
// registration wins). Delete each one as the kit gains it. They do what the
// boot needs and say so once; none claims more.
#include "runtime/gdi_image.h"
#include "runtime/guest.h"
#include "runtime/imports.h"
#include "runtime/memory.h"
#include "runtime/resources.h"

#include "runtime/user32_internal.h"
#include "runtime/win32.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <map>
#include <set>
#include <string>
#include <vector>

void ee_flush_host_posts(); // ee_main.cpp

namespace {

void said(const char *what) {
    static std::set<std::string> seen;
    if (seen.insert(what).second)
        fprintf(stderr, "[ee pending] %s (temporary stand-in, app/pending_win32.cpp)\n", what);
}

// user32!LoadStringA(hInstance, id, buffer, cch): the module's RT_STRING entry
// (kit resources.h, module-aware), narrowed to the ANSI code page the way
// Windows-1252 maps the first 256 code points; NUL-terminated, truncated to
// cch - 1. Returns the characters copied, 0 when there is no such string.
// The kit's LoadStringW reads only the executable's table.
void LoadStringA(X86 *c) {
    said("user32!LoadStringA");
    uint32_t module = arg(c, 0), id = arg(c, 1), out = arg(c, 2), cap = arg(c, 3);
    std::u16string text;
    if (!out || !cap || !resource_string_in(module, id, &text)) {
        if (out && cap)
            wr8(out, 0);
        set_eax(c, 0);
        return;
    }
    uint32_t n = (uint32_t)text.size() < cap - 1 ? (uint32_t)text.size() : cap - 1;
    for (uint32_t i = 0; i < n; ++i)
        wr8(out + i, text[i] < 0x100 ? (uint8_t)text[i] : '?');
    wr8(out + n, 0);
    set_eax(c, n);
}

// user32!LoadBitmapA(hInstance, name): the module's RT_BITMAP as a GDI bitmap
// (kit gdi_decode_image). The splash screen's thread (EXE 0x5344fb) ends at
// once without it, and the main thread waits for that splash forever.
void LoadBitmapA(X86 *c) {
    said("user32!LoadBitmapA");
    uint32_t module = arg(c, 0), name = arg(c, 1);
    if (name > 0xffff) { // a string name: the reader takes UTF-16
        set_eax(c, 0);
        return;
    }
    uint32_t bytes = 0;
    uint32_t p = resource_data_in(module, resource_find_in(module, 2, name), &bytes);
    GdiImage image;
    set_eax(c, p && gdi_decode_image(p, bytes, &image) ? gdi_image_bitmap(image) : 0);
}

// Thread messages. The kit's user32 has one message queue for the process and
// no PostThreadMessageA; Empire Earth's threads talk to each other with
// PostThreadMessageA (the main thread posts 0x404 to a worker and retries with
// Sleep(1) until the post succeeds; a worker posts 0x418 back), so without it
// two threads spin for ever. Windows gives each thread its own queue: a thread
// message (hwnd NULL) is retrieved only by PeekMessage on its own thread, with
// a NULL or -1 window filter. These queues are kept here and consulted before
// the kit's PeekMessageA runs; everything else is the kit's.
struct ThreadMsg {
    uint32_t message, wparam, lparam, time;
};
std::map<uint32_t, std::deque<ThreadMsg>> g_thread_queues;
void (*g_kit_peek)(X86 *) = nullptr;

void PostThreadMessageA(X86 *c) {
    said("user32!PostThreadMessageA (per-thread queues)");
    uint32_t tid = arg(c, 0);
    if (!tid) {
        set_last_error(1444); // ERROR_INVALID_THREAD_ID
        set_eax(c, 0);
        return;
    }
    g_thread_queues[tid].push_back({arg(c, 1), arg(c, 2), arg(c, 3), host_millis()});
    set_eax(c, 1);
}
bool filter_passes(uint32_t message, uint32_t lo, uint32_t hi) {
    if (message == 0x0012 || (!lo && !hi))
        return true;
    return message >= lo && (!hi || message <= hi);
}
// The kit's queue is the process's: a worker's PeekMessage loop takes the main
// window's messages (WM_SIZE, the activation, WM_CLOSE) and the splash
// thread's WM_QUIT reaches the main loop. Until it keeps a queue per thread,
// the messages and pending paints of windows another thread created are set
// aside while the kit's PeekMessageA runs on this one, and put back after.
struct Aside {
    std::deque<user32::Msg> msgs;
    std::vector<uint32_t> paints;
};
Aside set_aside(uint32_t tid) {
    Aside a;
    std::deque<user32::Msg> keep;
    for (const user32::Msg &m : user32::queue()) {
        user32::Window *w = m.hwnd ? user32::find_window(m.hwnd) : nullptr;
        (w && w->thread && w->thread != tid ? a.msgs : keep).push_back(m);
    }
    user32::queue().swap(keep);
    for (auto &kv : user32::windows())
        if (kv.second.thread && kv.second.thread != tid && kv.second.update_pending) {
            kv.second.update_pending = false;
            a.paints.push_back(kv.first);
        }
    return a;
}
void put_back(Aside &a) {
    for (auto it = a.msgs.rbegin(); it != a.msgs.rend(); ++it)
        user32::queue().push_front(*it);
    for (uint32_t hwnd : a.paints)
        if (user32::Window *w = user32::find_window(hwnd))
            w->update_pending = true;
}

// PostQuitMessage(code): WM_QUIT for the calling thread only.
void PostQuitMessage(X86 *c) {
    g_thread_queues[guest_current_thread_id()].push_back({0x0012, arg(c, 0), 0, host_millis()});
}

// PeekMessageA(lpMsg, hWnd, min, max, flags): this thread's thread messages
// first, then the kit's queue, holding back other threads' windows.
void PeekMessageA(X86 *c) {
    ee_flush_host_posts();
    uint32_t p = arg(c, 0), hwnd = arg(c, 1), lo = arg(c, 2), hi = arg(c, 3), flags = arg(c, 4);
    if (!hwnd || hwnd == 0xffffffffu) {
        auto it = g_thread_queues.find(guest_current_thread_id());
        if (it != g_thread_queues.end())
            for (auto m = it->second.begin(); m != it->second.end(); ++m) {
                if (!filter_passes(m->message, lo, hi))
                    continue;
                if (p) { // MSG {hwnd, message, wParam, lParam, time, pt}
                    wr32(p, 0);
                    wr32(p + 4, m->message);
                    wr32(p + 8, m->wparam);
                    wr32(p + 12, m->lparam);
                    wr32(p + 16, m->time);
                    wr32(p + 20, 0);
                    wr32(p + 24, 0);
                }
                if (flags & 1) // PM_REMOVE
                    it->second.erase(m);
                set_eax(c, 1);
                return;
            }
        if (hwnd == 0xffffffffu) { // thread messages only
            set_eax(c, 0);
            return;
        }
    }
    if (g_kit_peek) {
        Aside aside = set_aside(guest_current_thread_id());
        g_kit_peek(c);
        put_back(aside);
    } else {
        set_eax(c, 0);
    }
    static const bool debug = getenv("EE_MSG_DEBUG") != nullptr;
    if (debug && c->r[R_EAX] && p && rd32(p + 4) != 0x0f)
        fprintf(stderr,
                "[ee msg] thread %u took hwnd %08x msg %04x wp %08x lp %08x (filter %08x, %s)\n",
                guest_current_thread_id(), rd32(p), rd32(p + 4), rd32(p + 8), rd32(p + 12), hwnd,
                (flags & 1) ? "remove" : "peek");
}

// --- GDI text, ANSI over the kit's wide functions --------------------------------
uint32_t call_kit(X86 *c, const char *dll, const char *name, std::vector<uint32_t> args) {
    uint32_t fn = imports_resolve(dll, name);
    return fn ? guest_call(c, fn, args.data(), (int)args.size()) : 0;
}
// An ANSI string as UTF-16 in a temporary guest block (code points < 256 map
// to themselves, as Windows-1252 does for everything the game draws).
uint32_t wide_copy(uint32_t s, uint32_t n) {
    uint32_t w = heap_alloc(2 * (n + 1), true);
    for (uint32_t i = 0; w && i < n; ++i)
        wr16(w + 2 * i, rd8(s + i));
    return w;
}
// gdi32!CreateFontA(14 arguments): a LOGFONTW for the kit's CreateFontIndirectW.
void CreateFontA(X86 *c) {
    said("gdi32!CreateFontA -> the kit's CreateFontIndirectW");
    uint32_t lf = heap_alloc(92, true);
    for (int i = 0; i < 5; ++i) // height, width, escapement, orientation, weight
        wr32(lf + 4 * i, arg(c, i));
    for (int i = 0; i < 8; ++i) // italic .. pitch and family: bytes
        wr8(lf + 20 + i, (uint8_t)arg(c, 5 + i));
    if (uint32_t face = arg(c, 13)) {
        std::string f = gm_str(face, 32);
        for (size_t i = 0; i < f.size() && i < 31; ++i)
            wr16(lf + 28 + 2 * (uint32_t)i, (uint8_t)f[i]);
    }
    set_eax(c, call_kit(c, "GDI32.dll", "CreateFontIndirectW", {lf}));
    heap_free(lf);
}
// gdi32!GetTextExtentPoint32A(hdc, s, n, size): the kit's GetTextExtentPointA.
void GetTextExtentPoint32A(X86 *c) {
    said("gdi32!GetTextExtentPoint32A -> the kit's GetTextExtentPointA");
    set_eax(c, call_kit(c, "GDI32.dll", "GetTextExtentPointA",
                        {arg(c, 0), arg(c, 1), arg(c, 2), arg(c, 3)}));
}
// gdi32!ExtTextOutA(hdc, x, y, options, rect, s, n, dx): the kit's ExtTextOutW.
void ExtTextOutA(X86 *c) {
    said("gdi32!ExtTextOutA -> the kit's ExtTextOutW");
    uint32_t n = arg(c, 6), w = arg(c, 5) ? wide_copy(arg(c, 5), n) : 0;
    set_eax(c, call_kit(c, "GDI32.dll", "ExtTextOutW",
                        {arg(c, 0), arg(c, 1), arg(c, 2), arg(c, 3), arg(c, 4), w, n, arg(c, 7)}));
    if (w)
        heap_free(w);
}

// --- hooks -----------------------------------------------------------------------
// user32!SetWindowsHookExA(id, proc, hmod, thread). The executable asks for a
// WH_CALLWNDPROC hook on every thread (thread 0) with no module, which Windows
// refuses with ERROR_HOOK_NEEDS_HMOD [inferred from the documented rule]; a
// thread hook would be accepted and never called here.
void SetWindowsHookExA(X86 *c) {
    said("user32!SetWindowsHookExA (thread hooks accepted, never called)");
    if (!arg(c, 2) && !arg(c, 3)) {
        set_last_error(1428); // ERROR_HOOK_NEEDS_HMOD
        set_eax(c, 0);
        return;
    }
    static uint32_t next = 0x00070004;
    set_eax(c, next);
    next += 4;
}
void UnhookWindowsHookEx(X86 *c) {
    set_eax(c, arg(c, 0) ? 1 : 0);
}
void CallNextHookEx(X86 *c) {
    set_eax(c, 0);
}

const ImportShim kPending[] = {
    {"GDI32.dll", "CreateFontA", 14, CreateFontA},
    {"GDI32.dll", "GetTextExtentPoint32A", 4, GetTextExtentPoint32A},
    {"GDI32.dll", "ExtTextOutA", 8, ExtTextOutA},
    {"USER32.dll", "SetWindowsHookExA", 4, SetWindowsHookExA},
    {"USER32.dll", "UnhookWindowsHookEx", 1, UnhookWindowsHookEx},
    {"USER32.dll", "CallNextHookEx", 4, CallNextHookEx},
    {"USER32.dll", "LoadStringA", 4, LoadStringA},
    {"USER32.dll", "LoadBitmapA", 2, LoadBitmapA},
    {"USER32.dll", "PostThreadMessageA", 4, PostThreadMessageA},
};
const ImportShim kOverKit[] = {
    {"USER32.dll", "PeekMessageA", 5, PeekMessageA},
    {"USER32.dll", "PostQuitMessage", 1, PostQuitMessage},
};

void find_kit_peek(const ImportShim &s, void *) {
    if (!strcmp(s.dll, "USER32.dll") && !strcmp(s.name, "PeekMessageA") && s.fn != PeekMessageA)
        g_kit_peek = s.fn;
}

} // namespace

void ee_pending_win32_register() {
    imports_register(kPending, sizeof kPending / sizeof kPending[0]);
}
// After imports_init: wraps the kit's PeekMessageA.
void ee_pending_win32_over_kit() {
    imports_for_each_registered(find_kit_peek, nullptr);
    if (g_kit_peek)
        imports_register(kOverKit, sizeof kOverKit / sizeof kOverKit[0]);
    else
        fprintf(stderr, "[ee pending] the kit's PeekMessageA was not found; no thread messages\n");
}
