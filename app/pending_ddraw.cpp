// pending_ddraw.cpp - TEMPORARY null DirectDraw 7 / Direct3D 7 for the headless
// boot, until P3-B's ddraw/d3d shims land in the kit (docs/phase3.md). The
// kit's DirectDrawCreateEx answers DDERR_UNSUPPORTED today, so the display
// plugins' probe (DX7HRDisplay PIMIsValid -> GERasterizer::Activate) finds no
// screen, no rasterizer registers, and the executable dereferences a null
// rasterizer (EXE 0x5393ae). This file stands in with one primary display
// and one HAL/TnL HAL device that draw nothing:
//
//   IDirectDraw7, IDirect3D7, IDirectDrawSurface7 (system-memory pixels, flip
//   chains, Lock/Unlock, GetDC through a GDI DIB section), IDirect3DDevice7,
//   IDirect3DVertexBuffer7, IDirectDrawClipper, IDirectDrawGammaControl.
//
// Registered before the kit's tables, so the kit's own DirectDrawCreateEx wins
// the moment it is non-null... which it already is: these replace the kit's
// DDRAW.dll factory entry points explicitly (ee_pending_ddraw_register runs
// after imports_init, see ee_main.cpp). Delete this file when P3-B lands.
// Every method no code below handles says so once and returns DD_OK.
#include "runtime/guest.h"
#include "runtime/imports.h"
#include "runtime/memory.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace {

const uint32_t DD_OK = 0, E_NOINTERFACE = 0x80004002u, DDERR_INVALIDPARAMS = 0x80070057u;
const uint32_t DDERR_NOTFOUND = 0x887600ffu, DDERR_OUTOFMEMORY = 0x8007000eu;
const uint32_t DDERR_UNSUPPORTED = 0x80004001u;

// Display: the modes a desktop display of 2001 offers, at 16 and 32 bits.
const uint32_t kModes[][2] = {{640, 480},   {800, 600},   {1024, 768}, {1152, 864},
                              {1280, 960},  {1280, 1024}, {1600, 1200}, {1920, 1080}};
uint32_t g_mode_w = 1024, g_mode_h = 768, g_mode_bpp = 32;

void note(const char *what) {
    static std::set<std::string> seen;
    if (seen.insert(what).second)
        fprintf(stderr, "[ee pending ddraw] %s\n", what);
}

// --- objects -------------------------------------------------------------------
enum Kind { K_DD, K_D3D, K_SURFACE, K_DEVICE, K_VB, K_CLIPPER, K_GAMMA, K_PALETTE };
struct Obj {
    Kind kind;
    uint32_t self = 0, refs = 1;
    // surfaces
    uint32_t w = 0, h = 0, bpp = 0, pitch = 0, caps = 0, caps2 = 0, pixels = 0, pf_flags = 0x40;
    uint32_t masks[4] = {0, 0, 0, 0};
    std::vector<uint32_t> attached; // surfaces attached (back buffer, z-buffer)
    uint32_t dc = 0, dib = 0, old_bitmap = 0;
    // vertex buffers
    uint32_t vb_bytes = 0, vb_fvf = 0, vb_count = 0;
    uint32_t parent = 0; // the IDirectDraw7 a surface or D3D came from
};
std::map<uint32_t, Obj> g_objs;
std::map<Kind, uint32_t> g_vtables;

Obj *get(uint32_t self) {
    auto it = g_objs.find(self);
    return it == g_objs.end() ? nullptr : &it->second;
}

struct Method {
    const char *name;
    uint8_t argc; // including this
    void (*fn)(X86 *);
};

uint32_t vtable_for(Kind k, const char *iface, const Method *m, size_t n) {
    auto it = g_vtables.find(k);
    if (it != g_vtables.end())
        return it->second;
    uint32_t vt = heap_alloc(4 * (uint32_t)n, true);
    for (size_t i = 0; i < n; ++i) {
        std::string name = std::string(iface) + "::" + m[i].name;
        wr32(vt + 4 * (uint32_t)i,
             imports_alloc_trampoline("DDRAW.dll", strdup(name.c_str()), m[i].fn, m[i].argc));
    }
    g_vtables[k] = vt;
    return vt;
}

uint32_t make(Kind k, uint32_t vtable) {
    uint32_t self = heap_alloc(16, true);
    wr32(self, vtable);
    Obj o;
    o.kind = k;
    o.self = self;
    g_objs[self] = o;
    return self;
}

void ret(X86 *c, uint32_t hr) {
    set_eax(c, hr);
}

// A method no handler below answers: DD_OK, said once by name.
#define NOTE_OK(iface, name)                                                                       \
    [](X86 *c) {                                                                                    \
        note(iface "::" name " (null: DD_OK)");                                                    \
        ret(c, DD_OK);                                                                             \
    }

// --- IUnknown ------------------------------------------------------------------
uint32_t new_d3d(uint32_t dd);
uint32_t new_gamma();
const uint8_t IID_IDirect3D7[16] = {0x77, 0x9e, 0x04, 0xf5, 0x61, 0x48, 0xd2, 0x11,
                                    0xa4, 0x07, 0x00, 0xa0, 0xc9, 0x06, 0x29, 0xa8};
const uint8_t IID_IDirectDraw7[16] = {0xc0, 0x5e, 0xe6, 0x15, 0x9c, 0x3b, 0xd2, 0x11,
                                      0xb9, 0x2f, 0x00, 0x60, 0x97, 0x97, 0xea, 0x5b};
const uint8_t IID_IDirectDrawGammaControl[16] = {0x3e, 0x1c, 0xc1, 0x69, 0x6b, 0xb4, 0xd1, 0x11,
                                                 0xad, 0x7a, 0x00, 0xc0, 0x4f, 0xc2, 0x9b, 0x4e};
const uint8_t IID_IDirectDrawSurface7[16] = {0x80, 0x5a, 0x67, 0x06, 0x9b, 0x3b, 0xd2, 0x11,
                                             0xb9, 0x2f, 0x00, 0x60, 0x97, 0x97, 0xea, 0x5b};

bool iid_is(uint32_t iid, const uint8_t *want) {
    return iid && gm_valid(iid, 16) && !memcmp(g_mem + iid, want, 16);
}

void QueryInterface(X86 *c) {
    uint32_t self = arg(c, 0), iid = arg(c, 1), out = arg(c, 2);
    Obj *o = get(self);
    if (!o || !out) {
        ret(c, DDERR_INVALIDPARAMS);
        return;
    }
    wr32(out, 0);
    if (o->kind == K_DD && iid_is(iid, IID_IDirect3D7)) {
        wr32(out, new_d3d(self));
        ret(c, DD_OK);
        return;
    }
    if ((o->kind == K_DD && iid_is(iid, IID_IDirectDraw7)) ||
        (o->kind == K_SURFACE && iid_is(iid, IID_IDirectDrawSurface7))) {
        ++o->refs;
        wr32(out, self);
        ret(c, DD_OK);
        return;
    }
    if (o->kind == K_SURFACE && iid_is(iid, IID_IDirectDrawGammaControl)) {
        wr32(out, new_gamma());
        ret(c, DD_OK);
        return;
    }
    char buf[96];
    snprintf(buf, sizeof buf, "QueryInterface(kind %d, iid %08x-...): E_NOINTERFACE", o->kind,
             iid && gm_valid(iid, 4) ? rd32(iid) : 0);
    note(buf);
    ret(c, E_NOINTERFACE);
}
void AddRef(X86 *c) {
    Obj *o = get(arg(c, 0));
    ret(c, o ? ++o->refs : 0);
}
void Release(X86 *c) {
    Obj *o = get(arg(c, 0));
    if (!o) {
        ret(c, 0);
        return;
    }
    // Objects are never freed: a null device keeps everything it handed out.
    ret(c, o->refs ? --o->refs : 0);
}

// --- pixel formats -------------------------------------------------------------
// DDPIXELFORMAT (32 bytes) of an RGB surface at `bpp`.
void put_pf(uint32_t p, uint32_t bpp) {
    memset(g_mem + p, 0, 32);
    wr32(p, 32);
    wr32(p + 4, 0x40); // DDPF_RGB
    wr32(p + 12, bpp);
    if (bpp == 16) {
        wr32(p + 16, 0xf800);
        wr32(p + 20, 0x07e0);
        wr32(p + 24, 0x001f);
    } else {
        wr32(p + 16, 0x00ff0000);
        wr32(p + 20, 0x0000ff00);
        wr32(p + 24, 0x000000ff);
    }
}

// DDSURFACEDESC2 (124 bytes) of a surface.
void put_desc(uint32_t d, const Obj &s) {
    memset(g_mem + d, 0, 124);
    wr32(d, 124);
    wr32(d + 4, 0x1 | 0x2 | 0x4 | 0x8 | 0x1000); // CAPS HEIGHT WIDTH PITCH PIXELFORMAT
    wr32(d + 8, s.h);
    wr32(d + 12, s.w);
    wr32(d + 16, s.pitch);
    wr32(d + 36, s.pixels);
    if (s.caps & 0x20000) { // DDSCAPS_ZBUFFER
        memset(g_mem + d + 72, 0, 32);
        wr32(d + 72, 32);
        wr32(d + 76, 0x400); // DDPF_ZBUFFER
        wr32(d + 84, s.bpp);
        wr32(d + 92, s.bpp == 16 ? 0xffff : 0xffffff);
    } else {
        put_pf(d + 72, s.bpp);
        if (s.pf_flags != 0x40) { // a texture format with alpha
            wr32(d + 76, s.pf_flags);
            for (int i = 0; i < 4; ++i)
                wr32(d + 88 + 4 * i, s.masks[i]);
        }
    }
    wr32(d + 104, s.caps);
    wr32(d + 108, s.caps2);
}

// --- IDirectDrawSurface7 ---------------------------------------------------------
uint32_t surface_vtable();
uint32_t new_surface(uint32_t dd, uint32_t w, uint32_t h, uint32_t bpp, uint32_t caps) {
    uint32_t self = make(K_SURFACE, surface_vtable());
    Obj &s = g_objs[self];
    s.parent = dd;
    s.w = w ? w : 1;
    s.h = h ? h : 1;
    s.bpp = bpp;
    s.caps = caps;
    s.pitch = ((s.w * bpp / 8) + 3) & ~3u;
    s.pixels = heap_alloc(s.pitch * s.h, true, 16);
    return self;
}

void S_GetSurfaceDesc(X86 *c) {
    Obj *s = get(arg(c, 0));
    uint32_t d = arg(c, 1);
    if (!s || !d) {
        ret(c, DDERR_INVALIDPARAMS);
        return;
    }
    put_desc(d, *s);
    ret(c, DD_OK);
}
void S_Lock(X86 *c) {
    Obj *s = get(arg(c, 0));
    uint32_t rect = arg(c, 1), d = arg(c, 2);
    if (!s || !d) {
        ret(c, DDERR_INVALIDPARAMS);
        return;
    }
    put_desc(d, *s);
    if (rect) {
        uint32_t x = rd32(rect), y = rd32(rect + 4);
        wr32(d + 36, s->pixels + y * s->pitch + x * (s->bpp / 8));
    }
    ret(c, DD_OK);
}
void S_GetAttachedSurface(X86 *c) {
    Obj *s = get(arg(c, 0));
    uint32_t caps = arg(c, 1), out = arg(c, 2);
    if (!s || !caps || !out) {
        ret(c, DDERR_INVALIDPARAMS);
        return;
    }
    uint32_t want = rd32(caps);
    if (getenv("EE_DDRAW_DEBUG"))
        fprintf(stderr, "[ee pending ddraw] GetAttachedSurface(%08x, caps %08x %08x): %zu attached\n",
                arg(c, 0), want, rd32(caps + 4), s->attached.size());
    for (uint32_t a : s->attached) {
        Obj *t = get(a);
        if (t && (t->caps & want) == want) {
            ++t->refs;
            wr32(out, a);
            ret(c, DD_OK);
            return;
        }
    }
    wr32(out, 0);
    ret(c, DDERR_NOTFOUND);
}
void S_AddAttachedSurface(X86 *c) {
    Obj *s = get(arg(c, 0));
    if (s && get(arg(c, 1))) {
        s->attached.push_back(arg(c, 1));
        ++get(arg(c, 1))->refs;
    }
    ret(c, DD_OK);
}
void S_DeleteAttachedSurface(X86 *c) {
    Obj *s = get(arg(c, 0));
    if (s) {
        uint32_t which = arg(c, 2);
        for (size_t i = 0; i < s->attached.size(); ++i)
            if (!which || s->attached[i] == which) {
                s->attached.erase(s->attached.begin() + (long)i);
                break;
            }
    }
    ret(c, DD_OK);
}
void S_GetPixelFormat(X86 *c) {
    Obj *s = get(arg(c, 0));
    uint32_t p = arg(c, 1);
    if (!s || !p) {
        ret(c, DDERR_INVALIDPARAMS);
        return;
    }
    uint32_t tmp = heap_alloc(124, true);
    put_desc(tmp, *s);
    memcpy(g_mem + p, g_mem + tmp + 72, 32);
    heap_free(tmp);
    ret(c, DD_OK);
}
void S_GetCaps(X86 *c) {
    Obj *s = get(arg(c, 0));
    uint32_t p = arg(c, 1);
    if (!s || !p) {
        ret(c, DDERR_INVALIDPARAMS);
        return;
    }
    memset(g_mem + p, 0, 16);
    wr32(p, s->caps);
    wr32(p + 4, s->caps2);
    ret(c, DD_OK);
}
void S_GetDDInterface(X86 *c) {
    Obj *s = get(arg(c, 0));
    if (s && arg(c, 1))
        wr32(arg(c, 1), s->parent);
    ret(c, s ? DD_OK : DDERR_INVALIDPARAMS);
}
void S_IsLost(X86 *c) {
    ret(c, DD_OK);
}
// A DC on the surface: a GDI memory DC with a DIB section of the surface's size
// selected, through the kit's own GDI (what is drawn stays in the DIB; the
// null device presents nothing anyway).
uint32_t gdi(X86 *c, const char *name, std::vector<uint32_t> args) {
    uint32_t fn = imports_resolve("GDI32.dll", name);
    if (!fn)
        return 0;
    return guest_call(c, fn, args.data(), (int)args.size());
}
void S_GetDC(X86 *c) {
    Obj *s = get(arg(c, 0));
    uint32_t out = arg(c, 1);
    if (!s || !out) {
        ret(c, DDERR_INVALIDPARAMS);
        return;
    }
    if (!s->dc) {
        s->dc = gdi(c, "CreateCompatibleDC", {0});
        uint32_t bmi = heap_alloc(40 + 12, true);
        wr32(bmi, 40);
        wr32(bmi + 4, s->w);
        wr32(bmi + 8, (uint32_t) - (int32_t)s->h);
        wr16(bmi + 12, 1);
        wr16(bmi + 14, (uint16_t)s->bpp);
        uint32_t bits = heap_alloc(4, true);
        s->dib = gdi(c, "CreateDIBSection", {s->dc, bmi, 0, bits, 0, 0});
        if (s->dib)
            s->old_bitmap = gdi(c, "SelectObject", {s->dc, s->dib});
        heap_free(bits);
        heap_free(bmi);
    }
    wr32(out, s->dc);
    ret(c, s->dc ? DD_OK : DDERR_UNSUPPORTED);
}
void S_ReleaseDC(X86 *c) {
    ret(c, DD_OK);
}
void S_GetColorKey(X86 *c) {
    if (arg(c, 2))
        memset(g_mem + arg(c, 2), 0, 8);
    ret(c, DD_OK);
}
void S_GetUniquenessValue(X86 *c) {
    if (arg(c, 1))
        wr32(arg(c, 1), 1);
    ret(c, DD_OK);
}
void S_GetPriority(X86 *c) {
    if (arg(c, 1))
        wr32(arg(c, 1), 0);
    ret(c, DD_OK);
}

const Method kSurface[] = {
    {"QueryInterface", 3, QueryInterface},
    {"AddRef", 1, AddRef},
    {"Release", 1, Release},
    {"AddAttachedSurface", 2, S_AddAttachedSurface},
    {"AddOverlayDirtyRect", 2, NOTE_OK("IDirectDrawSurface7", "AddOverlayDirtyRect")},
    {"Blt", 6, [](X86 *c) { ret(c, DD_OK); }},
    {"BltBatch", 4, NOTE_OK("IDirectDrawSurface7", "BltBatch")},
    {"BltFast", 6, [](X86 *c) { ret(c, DD_OK); }},
    {"DeleteAttachedSurface", 3, S_DeleteAttachedSurface},
    {"EnumAttachedSurfaces", 3, NOTE_OK("IDirectDrawSurface7", "EnumAttachedSurfaces")},
    {"EnumOverlayZOrders", 4, NOTE_OK("IDirectDrawSurface7", "EnumOverlayZOrders")},
    {"Flip", 3, [](X86 *c) { ret(c, DD_OK); }},
    {"GetAttachedSurface", 3, S_GetAttachedSurface},
    {"GetBltStatus", 2, [](X86 *c) { ret(c, DD_OK); }},
    {"GetCaps", 2, S_GetCaps},
    {"GetClipper", 2, NOTE_OK("IDirectDrawSurface7", "GetClipper")},
    {"GetColorKey", 3, S_GetColorKey},
    {"GetDC", 2, S_GetDC},
    {"GetFlipStatus", 2, [](X86 *c) { ret(c, DD_OK); }},
    {"GetOverlayPosition", 3, NOTE_OK("IDirectDrawSurface7", "GetOverlayPosition")},
    {"GetPalette", 2, NOTE_OK("IDirectDrawSurface7", "GetPalette")},
    {"GetPixelFormat", 2, S_GetPixelFormat},
    {"GetSurfaceDesc", 2, S_GetSurfaceDesc},
    {"Initialize", 3, NOTE_OK("IDirectDrawSurface7", "Initialize")},
    {"IsLost", 1, S_IsLost},
    {"Lock", 5, S_Lock},
    {"ReleaseDC", 2, S_ReleaseDC},
    {"Restore", 1, [](X86 *c) { ret(c, DD_OK); }},
    {"SetClipper", 2, [](X86 *c) { ret(c, DD_OK); }},
    {"SetColorKey", 3, [](X86 *c) { ret(c, DD_OK); }},
    {"SetOverlayPosition", 3, NOTE_OK("IDirectDrawSurface7", "SetOverlayPosition")},
    {"SetPalette", 2, NOTE_OK("IDirectDrawSurface7", "SetPalette")},
    {"Unlock", 2, [](X86 *c) { ret(c, DD_OK); }},
    {"UpdateOverlay", 6, NOTE_OK("IDirectDrawSurface7", "UpdateOverlay")},
    {"UpdateOverlayDisplay", 2, NOTE_OK("IDirectDrawSurface7", "UpdateOverlayDisplay")},
    {"UpdateOverlayZOrder", 3, NOTE_OK("IDirectDrawSurface7", "UpdateOverlayZOrder")},
    {"GetDDInterface", 2, S_GetDDInterface},
    {"PageLock", 2, [](X86 *c) { ret(c, DD_OK); }},
    {"PageUnlock", 2, [](X86 *c) { ret(c, DD_OK); }},
    {"SetSurfaceDesc", 3, NOTE_OK("IDirectDrawSurface7", "SetSurfaceDesc")},
    {"SetPrivateData", 5, NOTE_OK("IDirectDrawSurface7", "SetPrivateData")},
    {"GetPrivateData", 4, NOTE_OK("IDirectDrawSurface7", "GetPrivateData")},
    {"FreePrivateData", 2, NOTE_OK("IDirectDrawSurface7", "FreePrivateData")},
    {"GetUniquenessValue", 2, S_GetUniquenessValue},
    {"ChangeUniquenessValue", 1, [](X86 *c) { ret(c, DD_OK); }},
    {"SetPriority", 2, [](X86 *c) { ret(c, DD_OK); }},
    {"GetPriority", 2, S_GetPriority},
    {"SetLOD", 2, [](X86 *c) { ret(c, DD_OK); }},
    {"GetLOD", 2, S_GetPriority},
};
uint32_t surface_vtable() {
    return vtable_for(K_SURFACE, "IDirectDrawSurface7", kSurface, sizeof kSurface / sizeof *kSurface);
}

// --- IDirectDrawGammaControl, IDirectDrawClipper ------------------------------------
const Method kGamma[] = {
    {"QueryInterface", 3, QueryInterface},
    {"AddRef", 1, AddRef},
    {"Release", 1, Release},
    {"GetGammaRamp", 3,
     [](X86 *c) {
         uint32_t r = arg(c, 2);
         if (r)
             for (uint32_t i = 0; i < 3 * 256; ++i)
                 wr16(r + 2 * i, (uint16_t)((i % 256) * 257));
         ret(c, DD_OK);
     }},
    {"SetGammaRamp", 3, [](X86 *c) { ret(c, DD_OK); }},
};
uint32_t new_gamma() {
    return make(K_GAMMA, vtable_for(K_GAMMA, "IDirectDrawGammaControl", kGamma,
                                    sizeof kGamma / sizeof *kGamma));
}
const Method kClipper[] = {
    {"QueryInterface", 3, QueryInterface},
    {"AddRef", 1, AddRef},
    {"Release", 1, Release},
    {"GetClipList", 4, NOTE_OK("IDirectDrawClipper", "GetClipList")},
    {"GetHWnd", 2, NOTE_OK("IDirectDrawClipper", "GetHWnd")},
    {"Initialize", 3, NOTE_OK("IDirectDrawClipper", "Initialize")},
    {"IsClipListChanged", 2, NOTE_OK("IDirectDrawClipper", "IsClipListChanged")},
    {"SetClipList", 3, NOTE_OK("IDirectDrawClipper", "SetClipList")},
    {"SetHWnd", 3, [](X86 *c) { ret(c, DD_OK); }},
};

// --- IDirect3DVertexBuffer7 --------------------------------------------------------
const Method kVB[] = {
    {"QueryInterface", 3, QueryInterface},
    {"AddRef", 1, AddRef},
    {"Release", 1, Release},
    {"Lock", 4,
     [](X86 *c) {
         Obj *v = get(arg(c, 0));
         if (!v) {
             ret(c, DDERR_INVALIDPARAMS);
             return;
         }
         if (arg(c, 2))
             wr32(arg(c, 2), v->pixels);
         if (arg(c, 3))
             wr32(arg(c, 3), v->vb_bytes);
         ret(c, DD_OK);
     }},
    {"Unlock", 1, [](X86 *c) { ret(c, DD_OK); }},
    {"ProcessVertices", 8, NOTE_OK("IDirect3DVertexBuffer7", "ProcessVertices")},
    {"GetVertexBufferDesc", 2,
     [](X86 *c) {
         Obj *v = get(arg(c, 0));
         uint32_t d = arg(c, 1);
         if (v && d) {
             wr32(d, 16);
             wr32(d + 4, 0);
             wr32(d + 8, v->vb_fvf);
             wr32(d + 12, v->vb_count);
         }
         ret(c, DD_OK);
     }},
    {"Optimize", 3, [](X86 *c) { ret(c, DD_OK); }},
    {"ProcessVerticesStrided", 8, NOTE_OK("IDirect3DVertexBuffer7", "ProcessVerticesStrided")},
};
// The vertex size of an FVF, for the buffer's bytes.
uint32_t fvf_size(uint32_t fvf) {
    uint32_t n = 0;
    switch (fvf & 0xe) {
    case 0x2: n = 12; break; // XYZ
    case 0x4: n = 16; break; // XYZRHW
    default: n = 12 + 4 * (((fvf & 0xe) - 4) / 2); break;
    }
    if (fvf & 0x10) n += 12; // NORMAL
    if (fvf & 0x20) n += 4;  // RESERVED1
    if (fvf & 0x40) n += 4;  // DIFFUSE
    if (fvf & 0x80) n += 4;  // SPECULAR
    n += 8 * ((fvf >> 8) & 0xf);
    return n;
}

// --- IDirect3DDevice7 ----------------------------------------------------------------
// D3DDEVICEDESC7 (236 bytes): a T&L HAL with everything a 2001 card had.
void put_device_desc(uint32_t d, bool tnl) {
    memset(g_mem + d, 0, 236);
    uint32_t devcaps = 0x1 | 0x2 | 0x10 | 0x20 | 0x40 | 0x80 | 0x100 | 0x200 | 0x400 | 0x800 |
                       0x1000 | 0x4000 | 0x8000 | 0x10000 | 0x80000;
    if (tnl)
        devcaps |= 0x10000 | 0x20000; // HWTRANSFORMANDLIGHT, CANBLTSYSTONONLOCAL
    wr32(d, devcaps);
    for (uint32_t prim = d + 4; prim <= d + 60; prim += 56) {
        wr32(prim, 56);
        for (uint32_t k = 4; k < 48; k += 4)
            wr32(prim + k, 0x00ffffffu); // every misc/raster/compare/blend/shade/texture cap
        wr32(prim + 48, 32);
        wr32(prim + 52, 32);
    }
    wr32(d + 116, 0x400 | 0x100); // DDBD_16 | DDBD_32 render depths
    wr32(d + 120, 0x400 | 0x200 | 0x100); // z depths 16/24/32
    wr32(d + 124, 1);
    wr32(d + 128, 1);
    wr32(d + 132, 2048);
    wr32(d + 136, 2048);
    wr32(d + 140, 8192);
    wr32(d + 144, 2048);
    wr32(d + 148, 16);
    float gb[4] = {-4096.f, -4096.f, 4096.f, 4096.f};
    memcpy(g_mem + d + 152, gb, 16);
    wr32(d + 172, 0xff);       // stencil caps
    wr32(d + 176, 0x00080008); // FVF caps: 8 texture coordinate sets
    wr32(d + 180, 0x03ffffff); // texture op caps
    wr16(d + 184, 8);          // max texture blend stages
    wr16(d + 186, 8);          // max simultaneous textures
    wr32(d + 188, 8);          // max active lights
    float maxw = 1e10f;
    memcpy(g_mem + d + 192, &maxw, 4);
    // deviceGUID is filled by the caller
    wr16(d + 212, 6);
    wr16(d + 214, 0);
    wr32(d + 216, tnl ? 0x3fu : 0);
}
const uint8_t IID_IDirect3DHALDevice[16] = {0xe0, 0x3d, 0xe6, 0x84, 0xaa, 0x46, 0xcf, 0x11,
                                            0x81, 0x6f, 0x00, 0x00, 0xc0, 0x20, 0x15, 0x6e};
const uint8_t IID_IDirect3DTnLHalDevice[16] = {0x78, 0x9e, 0x04, 0xf5, 0x61, 0x48, 0xd2, 0x11,
                                               0xa4, 0x07, 0x00, 0xa0, 0xc9, 0x06, 0x29, 0xa8};

void D_GetCaps(X86 *c) {
    if (arg(c, 1)) {
        put_device_desc(arg(c, 1), true);
        memcpy(g_mem + arg(c, 1) + 196, IID_IDirect3DTnLHalDevice, 16);
    }
    ret(c, DD_OK);
}
// The texture formats a card offered: 16-bit 565/1555/4444 and 32-bit 8888/888.
void D_EnumTextureFormats(X86 *c) {
    uint32_t cb = arg(c, 1), ctx = arg(c, 2);
    struct F {
        uint32_t flags, bits, r, g, b, a;
    } formats[] = {{0x40, 16, 0xf800, 0x07e0, 0x001f, 0},
                   {0x41, 16, 0x7c00, 0x03e0, 0x001f, 0x8000},
                   {0x41, 16, 0x0f00, 0x00f0, 0x000f, 0xf000},
                   {0x41, 32, 0x00ff0000, 0x0000ff00, 0x000000ff, 0xff000000},
                   {0x40, 32, 0x00ff0000, 0x0000ff00, 0x000000ff, 0}};
    uint32_t pf = heap_alloc(32, true);
    for (const F &f : formats) {
        memset(g_mem + pf, 0, 32);
        wr32(pf, 32);
        wr32(pf + 4, f.flags);
        wr32(pf + 12, f.bits);
        wr32(pf + 16, f.r);
        wr32(pf + 20, f.g);
        wr32(pf + 24, f.b);
        wr32(pf + 28, f.a);
        if (!guest_call(c, cb, pf, ctx))
            break;
    }
    heap_free(pf);
    ret(c, DD_OK);
}
void D_GetDirect3D(X86 *c) {
    Obj *d = get(arg(c, 0));
    if (arg(c, 1))
        wr32(arg(c, 1), d ? d->parent : 0);
    ret(c, DD_OK);
}
void D_GetRenderTarget(X86 *c) {
    Obj *d = get(arg(c, 0));
    if (arg(c, 1))
        wr32(arg(c, 1), d && !d->attached.empty() ? d->attached[0] : 0);
    ret(c, DD_OK);
}
void D_SetRenderTarget(X86 *c) {
    Obj *d = get(arg(c, 0));
    if (d) {
        d->attached.clear();
        d->attached.push_back(arg(c, 1));
    }
    ret(c, DD_OK);
}
void D_GetRenderState(X86 *c) {
    if (arg(c, 2))
        wr32(arg(c, 2), 0);
    ret(c, DD_OK);
}
void D_GetViewport(X86 *c) {
    if (arg(c, 1)) {
        uint32_t v = arg(c, 1);
        memset(g_mem + v, 0, 24);
        wr32(v + 8, g_mode_w);
        wr32(v + 12, g_mode_h);
        float one = 1.f;
        memcpy(g_mem + v + 20, &one, 4);
    }
    ret(c, DD_OK);
}
void D_GetTransform(X86 *c) {
    if (arg(c, 2)) {
        memset(g_mem + arg(c, 2), 0, 64);
        float one = 1.f;
        for (int i = 0; i < 4; ++i)
            memcpy(g_mem + arg(c, 2) + 20 * i, &one, 4);
    }
    ret(c, DD_OK);
}
void D_ValidateDevice(X86 *c) {
    if (arg(c, 1))
        wr32(arg(c, 1), 1);
    ret(c, DD_OK);
}
void D_GetTexture(X86 *c) {
    if (arg(c, 2))
        wr32(arg(c, 2), 0);
    ret(c, DD_OK);
}
void D_GetTextureStageState(X86 *c) {
    if (arg(c, 3))
        wr32(arg(c, 3), 0);
    ret(c, DD_OK);
}
void D_CreateStateBlock(X86 *c) {
    static uint32_t next = 1;
    if (arg(c, 2))
        wr32(arg(c, 2), next++);
    ret(c, DD_OK);
}
void D_GetLightEnable(X86 *c) {
    if (arg(c, 2))
        wr32(arg(c, 2), 0);
    ret(c, DD_OK);
}
void ok(X86 *c) {
    ret(c, DD_OK);
}

const Method kDevice[] = {
    {"QueryInterface", 3, QueryInterface},
    {"AddRef", 1, AddRef},
    {"Release", 1, Release},
    {"GetCaps", 2, D_GetCaps},
    {"EnumTextureFormats", 3, D_EnumTextureFormats},
    {"BeginScene", 1, ok},
    {"EndScene", 1, ok},
    {"GetDirect3D", 2, D_GetDirect3D},
    {"SetRenderTarget", 3, D_SetRenderTarget},
    {"GetRenderTarget", 2, D_GetRenderTarget},
    {"Clear", 7, ok},
    {"SetTransform", 3, ok},
    {"GetTransform", 3, D_GetTransform},
    {"SetViewport", 2, ok},
    {"MultiplyTransform", 3, ok},
    {"GetViewport", 2, D_GetViewport},
    {"SetMaterial", 2, ok},
    {"GetMaterial", 2, NOTE_OK("IDirect3DDevice7", "GetMaterial")},
    {"SetLight", 3, ok},
    {"GetLight", 3, NOTE_OK("IDirect3DDevice7", "GetLight")},
    {"SetRenderState", 3, ok},
    {"GetRenderState", 3, D_GetRenderState},
    {"BeginStateBlock", 1, ok},
    {"EndStateBlock", 2, D_CreateStateBlock},
    {"PreLoad", 2, ok},
    {"DrawPrimitive", 6, ok},
    {"DrawIndexedPrimitive", 8, ok},
    {"SetClipStatus", 2, ok},
    {"GetClipStatus", 2, NOTE_OK("IDirect3DDevice7", "GetClipStatus")},
    {"DrawPrimitiveStrided", 6, ok},
    {"DrawIndexedPrimitiveStrided", 8, ok},
    {"DrawPrimitiveVB", 6, ok},
    {"DrawIndexedPrimitiveVB", 8, ok},
    {"ComputeSphereVisibility", 6, NOTE_OK("IDirect3DDevice7", "ComputeSphereVisibility")},
    {"GetTexture", 3, D_GetTexture},
    {"SetTexture", 3, ok},
    {"GetTextureStageState", 4, D_GetTextureStageState},
    {"SetTextureStageState", 4, ok},
    {"ValidateDevice", 2, D_ValidateDevice},
    {"ApplyStateBlock", 2, ok},
    {"CaptureStateBlock", 2, ok},
    {"DeleteStateBlock", 2, ok},
    {"CreateStateBlock", 3, D_CreateStateBlock},
    {"Load", 6, ok},
    {"LightEnable", 3, ok},
    {"GetLightEnable", 3, D_GetLightEnable},
    {"SetClipPlane", 3, ok},
    {"GetClipPlane", 3, NOTE_OK("IDirect3DDevice7", "GetClipPlane")},
    {"GetInfo", 4, NOTE_OK("IDirect3DDevice7", "GetInfo")},
};

// --- IDirect3D7 ----------------------------------------------------------------------
void D3_EnumDevices(X86 *c) {
    uint32_t cb = arg(c, 1), ctx = arg(c, 2);
    uint32_t desc = heap_alloc(236, true), names = heap_alloc(128, true);
    struct Dev {
        const char *desc, *name;
        const uint8_t *guid;
        bool tnl;
    } devs[] = {{"Direct3D HAL", "Direct3D HAL", IID_IDirect3DHALDevice, false},
                {"Direct3D T&L HAL", "Direct3D T&L HAL", IID_IDirect3DTnLHalDevice, true}};
    for (const Dev &d : devs) {
        put_device_desc(desc, d.tnl);
        memcpy(g_mem + desc + 196, d.guid, 16);
        gm_put_str(names, d.desc, 64);
        gm_put_str(names + 64, d.name, 64);
        if (!guest_call(c, cb, names, names + 64, desc, ctx))
            break;
    }
    heap_free(names);
    heap_free(desc);
    ret(c, DD_OK);
}
uint32_t device_vtable() {
    return vtable_for(K_DEVICE, "IDirect3DDevice7", kDevice, sizeof kDevice / sizeof *kDevice);
}
void D3_CreateDevice(X86 *c) {
    uint32_t surface = arg(c, 2), out = arg(c, 3);
    if (!out) {
        ret(c, DDERR_INVALIDPARAMS);
        return;
    }
    uint32_t dev = make(K_DEVICE, device_vtable());
    g_objs[dev].parent = arg(c, 0);
    g_objs[dev].attached.push_back(surface);
    wr32(out, dev);
    ret(c, DD_OK);
}
void D3_CreateVertexBuffer(X86 *c) {
    uint32_t desc = arg(c, 1), out = arg(c, 2);
    if (!desc || !out) {
        ret(c, DDERR_INVALIDPARAMS);
        return;
    }
    uint32_t vb = make(K_VB, vtable_for(K_VB, "IDirect3DVertexBuffer7", kVB, sizeof kVB / sizeof *kVB));
    Obj &v = g_objs[vb];
    v.vb_fvf = rd32(desc + 8);
    v.vb_count = rd32(desc + 12);
    v.vb_bytes = fvf_size(v.vb_fvf) * v.vb_count;
    v.pixels = heap_alloc(v.vb_bytes ? v.vb_bytes : 16, true, 16);
    if (!v.pixels) {
        ret(c, DDERR_OUTOFMEMORY);
        return;
    }
    wr32(out, vb);
    ret(c, DD_OK);
}
void D3_EnumZBufferFormats(X86 *c) {
    uint32_t cb = arg(c, 2), ctx = arg(c, 3);
    uint32_t pf = heap_alloc(32, true);
    const uint32_t depths[][2] = {{16, 0xffff}, {24, 0xffffff}, {32, 0xffffff}};
    for (const auto &d : depths) {
        memset(g_mem + pf, 0, 32);
        wr32(pf, 32);
        wr32(pf + 4, 0x400); // DDPF_ZBUFFER
        wr32(pf + 12, d[0]);
        wr32(pf + 20, d[1]);
        if (!guest_call(c, cb, pf, ctx))
            break;
    }
    heap_free(pf);
    ret(c, DD_OK);
}
const Method kD3D[] = {
    {"QueryInterface", 3, QueryInterface},
    {"AddRef", 1, AddRef},
    {"Release", 1, Release},
    {"EnumDevices", 3, D3_EnumDevices},
    {"CreateDevice", 4, D3_CreateDevice},
    {"CreateVertexBuffer", 4, D3_CreateVertexBuffer},
    {"EnumZBufferFormats", 4, D3_EnumZBufferFormats},
    {"EvictManagedTextures", 1, ok},
};
uint32_t new_d3d(uint32_t dd) {
    uint32_t d3d = make(K_D3D, vtable_for(K_D3D, "IDirect3D7", kD3D, sizeof kD3D / sizeof *kD3D));
    g_objs[d3d].parent = dd;
    return d3d;
}

// --- IDirectDraw7 ----------------------------------------------------------------------
// DDCAPS (DX7: 380 bytes): a 3D card with 64 MB of video memory.
void put_ddcaps(uint32_t p, uint32_t size) {
    if (!p)
        return;
    memset(g_mem + p, 0, size);
    wr32(p, size);
    wr32(p + 4, 0x1 | 0x40 | 0x200 | 0x400 | 0x40000 | 0x80000000u); // 3D BLT BLTSTRETCH ...
    wr32(p + 8, 0x200 | 0x80000); // CAPS2: CANRENDERWINDOWED, WIDESURFACES
    wr32(p + 60, 64u << 20);      // dwVidMemTotal
    wr32(p + 64, 60u << 20);      // dwVidMemFree
    // ddsCaps.dwCaps (offset 0x84 in DDCAPS_DX7): everything a 3D card offers
    wr32(p + 0x84, 0x00004000 | 0x1000 | 0x2000 | 0x20000 | 0x8 | 0x10 | 0x200 | 0x4000000);
}
void DD_GetCaps(X86 *c) {
    uint32_t drv = arg(c, 1), hel = arg(c, 2);
    if (drv)
        put_ddcaps(drv, rd32(drv) ? rd32(drv) : 380);
    if (hel)
        put_ddcaps(hel, rd32(hel) ? rd32(hel) : 380);
    ret(c, DD_OK);
}
void DD_GetDisplayMode(X86 *c) {
    uint32_t d = arg(c, 1);
    if (!d) {
        ret(c, DDERR_INVALIDPARAMS);
        return;
    }
    Obj tmp;
    tmp.kind = K_SURFACE;
    tmp.w = g_mode_w;
    tmp.h = g_mode_h;
    tmp.bpp = g_mode_bpp;
    tmp.pitch = g_mode_w * g_mode_bpp / 8;
    put_desc(d, tmp);
    wr32(d + 4, rd32(d + 4) | 0x40000); // DDSD_REFRESHRATE
    wr32(d + 24, 60);
    ret(c, DD_OK);
}
void DD_EnumDisplayModes(X86 *c) {
    uint32_t ctx = arg(c, 3), cb = arg(c, 4);
    uint32_t d = heap_alloc(124, true);
    for (uint32_t bpp : {16u, 32u})
        for (const auto &m : kModes) {
            Obj tmp;
            tmp.kind = K_SURFACE;
            tmp.w = m[0];
            tmp.h = m[1];
            tmp.bpp = bpp;
            tmp.pitch = m[0] * bpp / 8;
            put_desc(d, tmp);
            wr32(d + 4, rd32(d + 4) | 0x40000);
            wr32(d + 24, 60);
            if (!guest_call(c, cb, d, ctx)) {
                heap_free(d);
                ret(c, DD_OK);
                return;
            }
        }
    heap_free(d);
    ret(c, DD_OK);
}
void DD_SetDisplayMode(X86 *c) {
    g_mode_w = arg(c, 1);
    g_mode_h = arg(c, 2);
    g_mode_bpp = arg(c, 3) ? arg(c, 3) : g_mode_bpp;
    printf("[ee pending ddraw] SetDisplayMode %ux%u %ubpp\n", g_mode_w, g_mode_h, g_mode_bpp);
    ret(c, DD_OK);
}
void DD_CreateSurface(X86 *c) {
    uint32_t dd = arg(c, 0), d = arg(c, 1), out = arg(c, 2);
    if (!d || !out) {
        ret(c, DDERR_INVALIDPARAMS);
        return;
    }
    uint32_t flags = rd32(d + 4), caps = rd32(d + 104);
    if (getenv("EE_DDRAW_DEBUG"))
        fprintf(stderr, "[ee pending ddraw] CreateSurface flags %08x caps %08x %ux%u back %u mips %u\n",
                flags, caps, rd32(d + 12), rd32(d + 8), rd32(d + 20), rd32(d + 24));
    uint32_t w = (flags & 0x4) ? rd32(d + 12) : g_mode_w;
    uint32_t h = (flags & 0x2) ? rd32(d + 8) : g_mode_h;
    uint32_t bpp = g_mode_bpp;
    uint32_t pf_flags = 0x40, masks[4] = {0, 0, 0, 0};
    if (flags & 0x1000) { // DDSD_PIXELFORMAT
        uint32_t pf = d + 72;
        bpp = rd32(pf + 12) ? rd32(pf + 12) : bpp;
        pf_flags = rd32(pf + 4);
        for (int i = 0; i < 4; ++i)
            masks[i] = rd32(pf + 16 + 4 * i);
    }
    if (caps & 0x200) { // DDSCAPS_PRIMARYSURFACE
        w = g_mode_w;
        h = g_mode_h;
        bpp = g_mode_bpp;
    }
    if ((caps & 0x20000) && (flags & 0x1000)) // a z-buffer: its depth from the format
        bpp = rd32(d + 84);
    uint32_t s = new_surface(dd, w, h, bpp, caps | 0x800 /*LOCALVIDMEM*/ | 0x4000 /*VIDEOMEMORY*/);
    if (!g_objs[s].pixels) {
        ret(c, DDERR_OUTOFMEMORY);
        return;
    }
    g_objs[s].caps2 = rd32(d + 108);
    g_objs[s].pf_flags = pf_flags;
    memcpy(g_objs[s].masks, masks, sizeof masks);
    if ((caps & 0x200) && (flags & 0x20) && rd32(d + 20)) { // a flip chain with back buffers
        g_objs[s].caps |= 0x20 | 0x10 | 0x8; // FRONTBUFFER | FLIP | COMPLEX, as Windows reports
        uint32_t prev = s;
        for (uint32_t i = 0; i < rd32(d + 20); ++i) {
            // BACKBUFFER | FLIP | COMPLEX: found by either cap, as on Windows.
            uint32_t b = new_surface(dd, w, h, bpp, 0x4 | 0x10 | 0x8 | 0x800 | 0x4000 | (caps & 0x2000));
            g_objs[prev].attached.push_back(b);
            prev = b;
        }
    }
    // A mipmap chain: each level attached to the one above it, at half the
    // size, found with GetAttachedSurface(DDSCAPS_MIPMAP).
    if ((flags & 0x20000) && (caps & 0x400000) && rd32(d + 24) > 1) {
        uint32_t prev = s, lw = w, lh = h;
        for (uint32_t i = 1; i < rd32(d + 24); ++i) {
            lw = lw > 1 ? lw / 2 : 1;
            lh = lh > 1 ? lh / 2 : 1;
            uint32_t m = new_surface(dd, lw, lh, bpp, (caps & ~0x200u) | 0x800 | 0x4000);
            g_objs[m].caps2 = rd32(d + 108) | 0x10000; // DDSCAPS2_MIPMAPSUBLEVEL
            g_objs[m].pf_flags = pf_flags;
            memcpy(g_objs[m].masks, masks, sizeof masks);
            g_objs[prev].attached.push_back(m);
            prev = m;
        }
    }
    wr32(out, s);
    ret(c, DD_OK);
}
void DD_GetAvailableVidMem(X86 *c) {
    if (arg(c, 2))
        wr32(arg(c, 2), 64u << 20);
    if (arg(c, 3))
        wr32(arg(c, 3), 60u << 20);
    ret(c, DD_OK);
}
void DD_GetDeviceIdentifier(X86 *c) {
    uint32_t p = arg(c, 1);
    if (p) {
        memset(g_mem + p, 0, 1068); // DDDEVICEIDENTIFIER2
        gm_put_str(p, "display", 512);
        gm_put_str(p + 512, "Null display (Empire Earth headless boot)", 512);
    }
    ret(c, DD_OK);
}
void DD_GetMonitorFrequency(X86 *c) {
    if (arg(c, 1))
        wr32(arg(c, 1), 60);
    ret(c, DD_OK);
}
void DD_GetFourCCCodes(X86 *c) {
    if (arg(c, 1))
        wr32(arg(c, 1), 0);
    ret(c, DD_OK);
}
void DD_CreateClipper(X86 *c) {
    if (arg(c, 2))
        wr32(arg(c, 2), make(K_CLIPPER, vtable_for(K_CLIPPER, "IDirectDrawClipper", kClipper,
                                                   sizeof kClipper / sizeof *kClipper)));
    ret(c, DD_OK);
}
void DD_GetVerticalBlankStatus(X86 *c) {
    if (arg(c, 1))
        wr32(arg(c, 1), 1);
    ret(c, DD_OK);
}
void DD_GetScanLine(X86 *c) {
    if (arg(c, 1))
        wr32(arg(c, 1), 0);
    ret(c, DD_OK);
}
void DD_GetGDISurface(X86 *c) {
    if (arg(c, 1))
        wr32(arg(c, 1), 0);
    ret(c, DDERR_NOTFOUND);
}

const Method kDD[] = {
    {"QueryInterface", 3, QueryInterface},
    {"AddRef", 1, AddRef},
    {"Release", 1, Release},
    {"Compact", 1, ok},
    {"CreateClipper", 4, DD_CreateClipper},
    {"CreatePalette", 5, NOTE_OK("IDirectDraw7", "CreatePalette")},
    {"CreateSurface", 4, DD_CreateSurface},
    {"DuplicateSurface", 3, NOTE_OK("IDirectDraw7", "DuplicateSurface")},
    {"EnumDisplayModes", 5, DD_EnumDisplayModes},
    {"EnumSurfaces", 5, NOTE_OK("IDirectDraw7", "EnumSurfaces")},
    {"FlipToGDISurface", 1, ok},
    {"GetCaps", 3, DD_GetCaps},
    {"GetDisplayMode", 2, DD_GetDisplayMode},
    {"GetFourCCCodes", 3, DD_GetFourCCCodes},
    {"GetGDISurface", 2, DD_GetGDISurface},
    {"GetMonitorFrequency", 2, DD_GetMonitorFrequency},
    {"GetScanLine", 2, DD_GetScanLine},
    {"GetVerticalBlankStatus", 2, DD_GetVerticalBlankStatus},
    {"Initialize", 2, ok},
    {"RestoreDisplayMode", 1, ok},
    {"SetCooperativeLevel", 3, ok},
    {"SetDisplayMode", 6, DD_SetDisplayMode},
    {"WaitForVerticalBlank", 3, ok},
    {"GetAvailableVidMem", 4, DD_GetAvailableVidMem},
    {"GetSurfaceFromDC", 3, NOTE_OK("IDirectDraw7", "GetSurfaceFromDC")},
    {"RestoreAllSurfaces", 1, ok},
    {"TestCooperativeLevel", 1, ok},
    {"GetDeviceIdentifier", 3, DD_GetDeviceIdentifier},
    {"StartModeTest", 4, NOTE_OK("IDirectDraw7", "StartModeTest")},
    {"EvaluateMode", 3, NOTE_OK("IDirectDraw7", "EvaluateMode")},
};

// DDRAW.dll!DirectDrawCreateEx(guid, lplpDD, iid, outer): IID_IDirectDraw7 only.
void DirectDrawCreateEx(X86 *c) {
    uint32_t out = arg(c, 1), iid = arg(c, 2);
    if (!out || !iid_is(iid, IID_IDirectDraw7)) {
        ret(c, DDERR_INVALIDPARAMS);
        return;
    }
    note("DirectDrawCreateEx: the null display (app/pending_ddraw.cpp)");
    wr32(out, make(K_DD, vtable_for(K_DD, "IDirectDraw7", kDD, sizeof kDD / sizeof *kDD)));
    ret(c, DD_OK);
}

// binkw32!BinkDDSurfaceType(surface): the kit's asks its own COM objects, and
// these surfaces are not: answer for them in Bink's numbering (BINKSURFACE32 3,
// 555 9, 565 10), and 0 for anything else, as the kit does.
void BinkDDSurfaceType(X86 *c) {
    Obj *s = get(arg(c, 0));
    uint32_t type = 0;
    if (s && s->kind == K_SURFACE) {
        uint32_t r = s->masks[0] ? s->masks[0] : (s->bpp == 16 ? 0xf800u : 0xff0000u);
        if (s->bpp == 32)
            type = 3;
        else if (s->bpp == 16)
            type = r == 0x7c00 ? 9 : 10;
    }
    set_eax(c, type);
}

const ImportShim kPending[] = {
    {"DDRAW.dll", "DirectDrawCreateEx", 4, DirectDrawCreateEx},
    {"binkw32.dll", "_BinkDDSurfaceType@4", 1, BinkDDSurfaceType},
};

} // namespace

// After imports_init: the kit's DirectDrawCreateEx is registered (and refuses
// IID_IDirectDraw7); a later non-null registration replaces it.
void ee_pending_ddraw_register() {
    imports_register(kPending, sizeof kPending / sizeof kPending[0]);
}
