// COM census: DirectDraw 7 / Direct3D 7 (GOG DDRAW.dll wrapper) and DirectInput (system dinput.dll),
// as called by the game. Static inventory and expected values: docs/reference/graphics.md.
//
// How it works
//  - Entry points: DDRAW!DirectDrawCreateEx and DINPUT!DirectInputCreateA are hooked when their
//    modules appear (spawn mode). When attached to a running game, the live interfaces are found
//    through engine/plugin globals instead (PARAMS.discover, on by default):
//      GERasterizer::sActiveRasterizer -> DX7Rasterizer: +0x6c vector<ctx*>; ctx +0x18 IDirect3D7,
//      +0x1c IDirect3DDevice7, +0x24 IDirect3DVertexBuffer7, +0x20 DX7Screen (+0x30 IDirectDraw7);
//      +0x20 vector<state*>; state +0x2ac / +0x2b0 front / back GESurface (+0x48 IDirectDrawSurface7);
//      engine RVA 0x128b94 IDirectInputA*, gISMouse +0x44 and gISKeyboard +0x18 IDirectInputDeviceA*.
//  - Every vtable seen for a known interface gets each slot redirected to a 46-byte stub
//    (same technique as iat_census.js): cmp byte [enabled],0 / je direct / pushfd / pushad /
//    lea eax,[esp+36] / push eax / mov eax,fs:[0x24] / push eax / push id / call record /
//    add esp,12 / popad / popfd / direct: jmp [orig]. record() (CModule) bumps a counter keyed by
//    (slot id, thread, caller module, up to three selected argument values) and adds one argument
//    (vertex or index count) to a 64-bit sum. Caller module = module of the return address, so
//    the GOG wrapper's own internal calls show up as caller "other" and can be filtered out.
//  - Child interfaces are followed from DirectDrawCreateEx, QueryInterface, CreateSurface,
//    GetAttachedSurface, CreateClipper, CreatePalette, CreateDevice, CreateVertexBuffer and
//    IDirectInputA::CreateDevice, via JS listeners on those (rare) methods, which also decode
//    their structures: surface descriptions, VB descriptions, IIDs and GUIDs, device identifier,
//    caps, enumeration results (display modes, texture/Z formats, D3D devices), DirectInput data
//    formats, properties and buffered-read results.
//  - Samplers (JS, detach after PARAMS.sample_limit calls): SetTransform (matrix class),
//    SetMaterial, SetLight, SetViewport, surface Lock results, Blt (source/destination kinds).
// stop() disables the stubs and restores every patched vtable entry (run.py calls it, then sleeps
// 1 s before unloading the script). Stubs live in VirtualAlloc'd memory that is never freed.
//
// PARAMS: discover (bool, default true), all_callers (bool: decode wrapper-internal calls too),
//         sample_limit (default 2000), decode_limit (events per method, default 40),
//         max_events (default 4000).
'use strict';

const P = Object.assign({ discover: true, all_callers: false, sample_limit: 2000, decode_limit: 40, max_events: 4000 },
  globalThis.PARAMS || {});

// ---------------------------------------------------------------------------------------------
// Interface tables (slot order from the DirectX 7 SDK headers; IUnknown = slots 0-2)
const IUNK = ['QueryInterface', 'AddRef', 'Release'];
const IFACES = {
  IDirectDraw7: IUNK.concat(['Compact', 'CreateClipper', 'CreatePalette', 'CreateSurface', 'DuplicateSurface',
    'EnumDisplayModes', 'EnumSurfaces', 'FlipToGDISurface', 'GetCaps', 'GetDisplayMode', 'GetFourCCCodes',
    'GetGDISurface', 'GetMonitorFrequency', 'GetScanLine', 'GetVerticalBlankStatus', 'Initialize',
    'RestoreDisplayMode', 'SetCooperativeLevel', 'SetDisplayMode', 'WaitForVerticalBlank', 'GetAvailableVidMem',
    'GetSurfaceFromDC', 'RestoreAllSurfaces', 'TestCooperativeLevel', 'GetDeviceIdentifier', 'StartModeTest',
    'EvaluateMode']),
  IDirectDrawSurface7: IUNK.concat(['AddAttachedSurface', 'AddOverlayDirtyRect', 'Blt', 'BltBatch', 'BltFast',
    'DeleteAttachedSurface', 'EnumAttachedSurfaces', 'EnumOverlayZOrders', 'Flip', 'GetAttachedSurface',
    'GetBltStatus', 'GetCaps', 'GetClipper', 'GetColorKey', 'GetDC', 'GetFlipStatus', 'GetOverlayPosition',
    'GetPalette', 'GetPixelFormat', 'GetSurfaceDesc', 'Initialize', 'IsLost', 'Lock', 'ReleaseDC', 'Restore',
    'SetClipper', 'SetColorKey', 'SetOverlayPosition', 'SetPalette', 'Unlock', 'UpdateOverlay',
    'UpdateOverlayDisplay', 'UpdateOverlayZOrder', 'GetDDInterface', 'PageLock', 'PageUnlock', 'SetSurfaceDesc',
    'SetPrivateData', 'GetPrivateData', 'FreePrivateData', 'GetUniquenessValue', 'ChangeUniquenessValue',
    'SetPriority', 'GetPriority', 'SetLOD', 'GetLOD']),
  IDirectDrawClipper: IUNK.concat(['GetClipList', 'GetHWnd', 'Initialize', 'IsClipListChanged', 'SetClipList', 'SetHWnd']),
  IDirectDrawPalette: IUNK.concat(['GetCaps', 'GetEntries', 'Initialize', 'SetEntries']),
  IDirectDrawGammaControl: IUNK.concat(['GetGammaRamp', 'SetGammaRamp']),
  IDirect3D7: IUNK.concat(['EnumDevices', 'CreateDevice', 'CreateVertexBuffer', 'EnumZBufferFormats', 'EvictManagedTextures']),
  IDirect3DDevice7: IUNK.concat(['GetCaps', 'EnumTextureFormats', 'BeginScene', 'EndScene', 'GetDirect3D',
    'SetRenderTarget', 'GetRenderTarget', 'Clear', 'SetTransform', 'GetTransform', 'SetViewport',
    'MultiplyTransform', 'GetViewport', 'SetMaterial', 'GetMaterial', 'SetLight', 'GetLight', 'SetRenderState',
    'GetRenderState', 'BeginStateBlock', 'EndStateBlock', 'PreLoad', 'DrawPrimitive', 'DrawIndexedPrimitive',
    'SetClipStatus', 'GetClipStatus', 'DrawPrimitiveStrided', 'DrawIndexedPrimitiveStrided', 'DrawPrimitiveVB',
    'DrawIndexedPrimitiveVB', 'ComputeSphereVisibility', 'GetTexture', 'SetTexture', 'GetTextureStageState',
    'SetTextureStageState', 'ValidateDevice', 'ApplyStateBlock', 'CaptureStateBlock', 'DeleteStateBlock',
    'CreateStateBlock', 'Load', 'LightEnable', 'GetLightEnable', 'SetClipPlane', 'GetClipPlane', 'GetInfo']),
  IDirect3DVertexBuffer7: IUNK.concat(['Lock', 'Unlock', 'ProcessVertices', 'GetVertexBufferDesc', 'Optimize',
    'ProcessVerticesStrided']),
  IDirectInputA: IUNK.concat(['CreateDevice', 'EnumDevices', 'GetDeviceStatus', 'RunControlPanel', 'Initialize']),
  IDirectInputDeviceA: IUNK.concat(['GetCapabilities', 'EnumObjects', 'GetProperty', 'SetProperty', 'Acquire',
    'Unacquire', 'GetDeviceState', 'GetDeviceData', 'SetDataFormat', 'SetEventNotification', 'SetCooperativeLevel',
    'GetObjectInfo', 'GetDeviceInfo', 'RunControlPanel', 'Initialize']),
};

// Which arguments the native counter keeps. Parameter index k counts after `this` (k = 1 is the
// first real parameter); mode 'b' records (value != 0) instead of the value. sum = parameter added
// to the 64-bit sum. fl bit 1: SetRenderState float states (FOGSTART/END/DENSITY, ...) -> value
// squashed; bit 2: SetTextureStageState float types (bump-env, LOD bias) -> value squashed.
const ARGS = {
  'IDirectDraw7::SetCooperativeLevel': { a: 2 },
  'IDirectDraw7::SetDisplayMode': { a: 1, b: 2, c: 3 },
  'IDirectDraw7::EnumDisplayModes': { a: 1, b: [2, 'b'] },
  'IDirectDraw7::CreateClipper': { a: 1 },
  'IDirectDraw7::CreatePalette': { a: 1 },
  'IDirectDraw7::WaitForVerticalBlank': { a: 1 },
  'IDirectDraw7::GetDeviceIdentifier': { a: 2 },
  'IDirectDraw7::EnumSurfaces': { a: 1 },
  'IDirectDrawSurface7::Blt': { a: 4, b: [2, 'b'], c: [1, 'b'] },
  'IDirectDrawSurface7::BltFast': { a: 5, b: [4, 'b'] },
  'IDirectDrawSurface7::Flip': { a: 2, b: [1, 'b'] },
  'IDirectDrawSurface7::Lock': { a: 3, b: [1, 'b'] },
  'IDirectDrawSurface7::Unlock': { a: [1, 'b'] },
  'IDirectDrawSurface7::SetColorKey': { a: 1 },
  'IDirectDrawSurface7::GetColorKey': { a: 1 },
  'IDirectDrawSurface7::DeleteAttachedSurface': { a: 1 },
  'IDirectDrawSurface7::SetClipper': { a: [1, 'b'] },
  'IDirectDrawSurface7::SetPalette': { a: [1, 'b'] },
  'IDirectDrawSurface7::GetBltStatus': { a: 1 },
  'IDirectDrawSurface7::GetFlipStatus': { a: 1 },
  'IDirectDrawSurface7::SetPriority': { a: 1 },
  'IDirectDrawSurface7::SetLOD': { a: 1 },
  'IDirectDrawSurface7::PageLock': { a: 1 },
  'IDirectDrawPalette::SetEntries': { a: 1, b: 2, c: 3 },
  'IDirectDrawGammaControl::SetGammaRamp': { a: 1 },
  'IDirect3DDevice7::SetRenderState': { a: 1, b: 2, fl: 1 },
  'IDirect3DDevice7::GetRenderState': { a: 1 },
  'IDirect3DDevice7::SetTextureStageState': { a: 1, b: 2, c: 3, fl: 2 },
  'IDirect3DDevice7::GetTextureStageState': { a: 1, b: 2 },
  'IDirect3DDevice7::SetTexture': { a: 1, b: [2, 'b'] },
  'IDirect3DDevice7::GetTexture': { a: 1 },
  'IDirect3DDevice7::SetTransform': { a: 1 },
  'IDirect3DDevice7::GetTransform': { a: 1 },
  'IDirect3DDevice7::MultiplyTransform': { a: 1 },
  'IDirect3DDevice7::SetLight': { a: 1 },
  'IDirect3DDevice7::GetLight': { a: 1 },
  'IDirect3DDevice7::LightEnable': { a: 1, b: 2 },
  'IDirect3DDevice7::GetLightEnable': { a: 1 },
  'IDirect3DDevice7::Clear': { a: 3, b: 1, c: 4 },
  'IDirect3DDevice7::SetRenderTarget': { a: 2, b: [1, 'b'] },
  'IDirect3DDevice7::DrawPrimitive': { a: 1, b: 2, c: 5, sum: 4 },
  'IDirect3DDevice7::DrawIndexedPrimitive': { a: 1, b: 2, c: 7, sum: 6 },
  'IDirect3DDevice7::DrawPrimitiveStrided': { a: 1, b: 2, c: 5, sum: 4 },
  'IDirect3DDevice7::DrawIndexedPrimitiveStrided': { a: 1, b: 2, c: 7, sum: 6 },
  'IDirect3DDevice7::DrawPrimitiveVB': { a: 1, b: 5, sum: 4 },
  'IDirect3DDevice7::DrawIndexedPrimitiveVB': { a: 1, b: 7, sum: 6 },
  'IDirect3DDevice7::ComputeSphereVisibility': { b: 2, sum: 2 },
  'IDirect3DDevice7::ApplyStateBlock': { a: 1 },
  'IDirect3DDevice7::CaptureStateBlock': { a: 1 },
  'IDirect3DDevice7::DeleteStateBlock': { a: 1 },
  'IDirect3DDevice7::CreateStateBlock': { a: 1 },
  'IDirect3DDevice7::SetClipPlane': { a: 1 },
  'IDirect3DDevice7::GetInfo': { a: 1, c: 3 },
  'IDirect3DDevice7::PreLoad': { a: [1, 'b'] },
  'IDirect3DVertexBuffer7::Lock': { a: 1 },
  'IDirect3DVertexBuffer7::ProcessVertices': { a: 1, sum: 3 },
  'IDirect3DVertexBuffer7::Optimize': { a: 2 },
  'IDirectInputA::EnumDevices': { a: 1, b: 4 },
  'IDirectInputA::Initialize': { a: 2 },
  'IDirectInputDeviceA::GetProperty': { a: 1 },
  'IDirectInputDeviceA::SetProperty': { a: 1 },
  'IDirectInputDeviceA::GetDeviceState': { a: 1 },
  'IDirectInputDeviceA::GetDeviceData': { a: 1, b: 4, c: [2, 'b'] },
  'IDirectInputDeviceA::SetEventNotification': { a: [1, 'b'] },
  'IDirectInputDeviceA::SetCooperativeLevel': { a: 2 },
  'IDirectInputDeviceA::GetObjectInfo': { a: 2, b: 3 },
  'IDirectInputDeviceA::EnumObjects': { a: 3 },
  'IDirectInputDeviceA::Initialize': { a: 2 },
};

// Names used to label rows in the snapshot.
const RS = { 1: 'TEXTUREHANDLE', 2: 'ANTIALIAS', 3: 'TEXTUREADDRESS', 4: 'TEXTUREPERSPECTIVE', 5: 'WRAPU', 6: 'WRAPV',
  7: 'ZENABLE', 8: 'FILLMODE', 9: 'SHADEMODE', 10: 'LINEPATTERN', 14: 'ZWRITEENABLE', 15: 'ALPHATESTENABLE',
  16: 'LASTPIXEL', 17: 'TEXTUREMAG', 18: 'TEXTUREMIN', 19: 'SRCBLEND', 20: 'DESTBLEND', 21: 'TEXTUREMAPBLEND',
  22: 'CULLMODE', 23: 'ZFUNC', 24: 'ALPHAREF', 25: 'ALPHAFUNC', 26: 'DITHERENABLE', 27: 'ALPHABLENDENABLE',
  28: 'FOGENABLE', 29: 'SPECULARENABLE', 30: 'ZVISIBLE', 33: 'STIPPLEDALPHA', 34: 'FOGCOLOR', 35: 'FOGTABLEMODE',
  36: 'FOGSTART', 37: 'FOGEND', 38: 'FOGDENSITY', 40: 'EDGEANTIALIAS', 41: 'COLORKEYENABLE', 43: 'BORDERCOLOR',
  44: 'TEXTUREADDRESSU', 45: 'TEXTUREADDRESSV', 46: 'MIPMAPLODBIAS', 47: 'ZBIAS', 48: 'RANGEFOGENABLE',
  49: 'ANISOTROPY', 52: 'STENCILENABLE', 53: 'STENCILFAIL', 54: 'STENCILZFAIL', 55: 'STENCILPASS',
  56: 'STENCILFUNC', 57: 'STENCILREF', 58: 'STENCILMASK', 59: 'STENCILWRITEMASK', 60: 'TEXTUREFACTOR',
  128: 'WRAP0', 129: 'WRAP1', 136: 'CLIPPING', 137: 'LIGHTING', 138: 'EXTENTS', 139: 'AMBIENT',
  140: 'FOGVERTEXMODE', 141: 'COLORVERTEX', 142: 'LOCALVIEWER', 143: 'NORMALIZENORMALS',
  144: 'COLORKEYBLENDENABLE', 145: 'DIFFUSEMATERIALSOURCE', 146: 'SPECULARMATERIALSOURCE',
  147: 'AMBIENTMATERIALSOURCE', 148: 'EMISSIVEMATERIALSOURCE', 151: 'VERTEXBLEND', 152: 'CLIPPLANEENABLE' };
const TSS = { 1: 'COLOROP', 2: 'COLORARG1', 3: 'COLORARG2', 4: 'ALPHAOP', 5: 'ALPHAARG1', 6: 'ALPHAARG2',
  7: 'BUMPENVMAT00', 8: 'BUMPENVMAT01', 9: 'BUMPENVMAT10', 10: 'BUMPENVMAT11', 11: 'TEXCOORDINDEX',
  12: 'ADDRESS', 13: 'ADDRESSU', 14: 'ADDRESSV', 15: 'BORDERCOLOR', 16: 'MAGFILTER', 17: 'MINFILTER',
  18: 'MIPFILTER', 19: 'MIPMAPLODBIAS', 20: 'MAXMIPLEVEL', 21: 'MAXANISOTROPY', 22: 'BUMPENVLSCALE',
  23: 'BUMPENVLOFFSET', 24: 'TEXTURETRANSFORMFLAGS' };
const PRIM = { 1: 'POINTLIST', 2: 'LINELIST', 3: 'LINESTRIP', 4: 'TRIANGLELIST', 5: 'TRIANGLESTRIP', 6: 'TRIANGLEFAN' };
const XFORM = { 1: 'WORLD', 2: 'VIEW', 3: 'PROJECTION', 0x100: 'WORLD1' };
const DIPROP = { 1: 'BUFFERSIZE', 2: 'AXISMODE', 3: 'GRANULARITY', 4: 'RANGE', 5: 'DEADZONE', 6: 'SATURATION',
  7: 'FFGAIN', 8: 'FFLOAD', 9: 'AUTOCENTER', 10: 'CALIBRATIONMODE' };
const GUIDS = {
  '15e65ec0-3b9c-11d2-b92f-00609797ea5b': 'IID_IDirectDraw7',
  '06675a80-3b9b-11d2-b92f-00609797ea5b': 'IID_IDirectDrawSurface7',
  '69c11c3e-b46b-11d1-ad7a-00c04fc29b4e': 'IID_IDirectDrawGammaControl',
  '4b9f0ee0-0d7e-11d0-9b06-00a0c903a3b8': 'IID_IDirectDrawColorControl',
  '6c14db85-a733-11ce-a521-0020af0be560': 'IID_IDirectDrawClipper',
  '6c14db84-a733-11ce-a521-0020af0be560': 'IID_IDirectDrawPalette',
  'f5049e77-4861-11d2-a407-00a0c90629a8': 'IID_IDirect3D7',
  'f5049e79-4861-11d2-a407-00a0c90629a8': 'IID_IDirect3DDevice7',
  'f5049e7d-4861-11d2-a407-00a0c90629a8': 'IID_IDirect3DVertexBuffer7',
  '84e63de0-46aa-11cf-816f-0000c020156e': 'IID_IDirect3DHALDevice',
  'f5049e78-4861-11d2-a407-00a0c90629a8': 'IID_IDirect3DTnLHalDevice',
  'a4665c60-2673-11cf-a31a-00aa00b93356': 'IID_IDirect3DRGBDevice',
  '50936643-13e9-11d1-89aa-00a0c9054129': 'IID_IDirect3DRefDevice',
  '6f1d2b60-d5a0-11cf-bfc7-444553540000': 'GUID_SysMouse',
  '6f1d2b61-d5a0-11cf-bfc7-444553540000': 'GUID_SysKeyboard',
  '89521360-aa8a-11cf-bfc7-444553540000': 'IID_IDirectInputA',
  '5944e662-aa8a-11cf-bfc7-444553540000': 'IID_IDirectInput2A',
  '5944e680-c92e-11cf-bfc7-444553540000': 'IID_IDirectInputDeviceA',
  '5944e682-c92e-11cf-bfc7-444553540000': 'IID_IDirectInputDevice2A',
};
const IID_TYPE = { IID_IDirectDraw7: 'IDirectDraw7', IID_IDirectDrawSurface7: 'IDirectDrawSurface7',
  IID_IDirectDrawGammaControl: 'IDirectDrawGammaControl', IID_IDirectDrawClipper: 'IDirectDrawClipper',
  IID_IDirectDrawPalette: 'IDirectDrawPalette', IID_IDirect3D7: 'IDirect3D7', IID_IDirect3DDevice7: 'IDirect3DDevice7',
  IID_IDirect3DVertexBuffer7: 'IDirect3DVertexBuffer7', IID_IDirectInputA: 'IDirectInputA',
  IID_IDirectInputDeviceA: 'IDirectInputDeviceA' };
const PIXFMT = { // flags/bpp/r/g/b/a/fourcc -> name
  '41/32/ff0000/ff00/ff/ff000000/0': 'A8R8G8B8', '40/32/ff0000/ff00/ff/0/0': 'X8R8G8B8',
  '40/16/f800/7e0/1f/0/0': 'R5G6B5', '40/16/7c00/3e0/1f/0/0': 'X1R5G5B5', '41/16/7c00/3e0/1f/8000/0': 'A1R5G5B5',
  '41/16/f00/f0/f/f000/0': 'A4R4G4B4', '41/16/f800/7c0/3e/1/0': 'R5G5B5A1', '40/16/f800/7c0/3e/0/0': 'R5G5B5X1',
  '4/0/0/0/0/0/31545844': 'DXT1', '400/16/0/ffff/0/0/0': 'Z16', '400/24/0/ffffff/0/0/0': 'Z24',
  '400/32/0/ffffffff/0/0/0': 'Z32' };

// ---------------------------------------------------------------------------------------------
const hex = v => '0x' + (v >>> 0).toString(16);
const t0 = Date.now();
const events = [];
function emit(e) { e.ms = Date.now() - t0; if (events.length < P.max_events) events.push(e); send({ type: 'event', event: e }); }
const decodeCounts = new Map();
function canDecode(key) { const n = decodeCounts.get(key) || 0; decodeCounts.set(key, n + 1); return n < P.decode_limit; }
const aggs = new Map();     // aggregate name -> Map(signature -> {count, first...})
function agg(name, sig, extra) {
  let m = aggs.get(name); if (!m) { m = new Map(); aggs.set(name, m); }
  const r = m.get(sig);
  if (r) { r.count++; return; }
  if (m.size >= 400) { const o = m.get('(other)') || { count: 0 }; o.count++; m.set('(other)', o); return; }
  m.set(sig, Object.assign({ count: 1, firstMs: Date.now() - t0 }, extra || {}));
}

const whereCache = new Map();
function where(addr) {
  const k = addr.toString();
  let r = whereCache.get(k);
  if (r !== undefined) return r;
  const m = Process.findModuleByAddress(addr);
  r = m ? m.name + '+0x' + addr.sub(m.base).toString(16) : k;
  whereCache.set(k, r);
  return r;
}
function guidAt(p) {
  if (p.isNull()) return 'NULL';
  const b = new Uint8Array(p.readByteArray(16));
  const h = i => b[i].toString(16).padStart(2, '0');
  const s = h(3) + h(2) + h(1) + h(0) + '-' + h(5) + h(4) + '-' + h(7) + h(6) + '-' + h(8) + h(9) + '-' +
    h(10) + h(11) + h(12) + h(13) + h(14) + h(15);
  return GUIDS[s] || '{' + s + '}';
}
function pixfmt(p) { // DDPIXELFORMAT
  const k = [p.add(4).readU32(), p.add(12).readU32(), p.add(16).readU32(), p.add(20).readU32(), p.add(24).readU32(),
    p.add(28).readU32(), p.add(8).readU32()];
  const key = [k[0], k[1], k[2], k[3], k[4], k[5], k[6]].map((v, i) => i === 1 ? String(v) : v.toString(16)).join('/');
  return PIXFMT[key] || 'pf(' + key + ')';
}
function surfdesc(p) { // DDSURFACEDESC2
  const f = p.add(4).readU32();
  const d = { flags: hex(f), caps: hex(p.add(0x68).readU32()), caps2: hex(p.add(0x6c).readU32()) };
  if (f & 0x4) d.w = p.add(0xc).readU32();
  if (f & 0x2) d.h = p.add(8).readU32();
  if (f & 0x8) d.pitch = p.add(0x10).readS32();
  if (f & 0x20) d.backbuffers = p.add(0x14).readU32();
  if (f & 0x20000) d.mips = p.add(0x18).readU32();
  if (f & 0x40000) d.refresh = p.add(0x18).readU32();
  if (f & 0x1000) d.pf = pixfmt(p.add(0x48));
  if (f & 0x10000) d.ckSrcBlt = hex(p.add(0x40).readU32()) + '-' + hex(p.add(0x44).readU32());
  if (f & 0x4000) d.ckDestBlt = hex(p.add(0x30).readU32()) + '-' + hex(p.add(0x34).readU32());
  return d;
}
const sig = o => Object.keys(o).map(k => k + '=' + o[k]).join(' ');
const surfKind = new Map();   // IDirectDrawSurface7* -> short description
function kindOf(p) { return p.isNull() ? 'NULL' : (surfKind.get(p.toString()) || '?'); }
function capsKind(d) {
  const c = parseInt(d.caps, 16);
  const n = [];
  if (c & 0x200) n.push('primary'); if (c & 0x4) n.push('back'); if (c & 0x20000) n.push('z');
  if (c & 0x1000) n.push('tex'); if (c & 0x400000) n.push('mip'); if (c & 0x40) n.push('offscreen');
  if (c & 0x800) n.push('sysmem'); if (c & 0x2000) n.push('3d');
  return n.join('+') + (d.w ? ' ' + d.w + 'x' + d.h : '') + (d.pf ? ' ' + d.pf : '');
}

// ---------------------------------------------------------------------------------------------
// Native counter + stubs (iat_census.js technique)
const k32 = Process.getModuleByName('kernel32.dll');
const VirtualAlloc = new NativeFunction(k32.getExportByName('VirtualAlloc'), 'pointer',
  ['pointer', 'uint', 'uint32', 'uint32'], 'stdcall');
const NB = 1 << 15;          // hash table entries, 40 bytes each
const MAXSLOT = 2048;
const STUB = 48;
const NRANGE = 16;
const mem = VirtualAlloc(NULL, 0x1000 + NB * 40 + MAXSLOT * (8 + 4 + STUB), 0x3000, 0x40);   // RWX
if (mem.isNull()) throw new Error('VirtualAlloc failed');
const enabled = mem;
const lockv = mem.add(16);
const droppedp = mem.add(32);
const ranges = mem.add(0x100);                  // NRANGE x {lo, hi}
const table = mem.add(0x1000);
const cfg = table.add(NB * 40);                 // MAXSLOT x 8 bytes
const origs = cfg.add(MAXSLOT * 8);             // MAXSLOT x 4
const stubs = origs.add(MAXSLOT * 4);

const cm = new CModule(`
typedef struct { unsigned int id, tid, mod, a, b, c, count, sumlo, sumhi, pad; } Rec;
typedef struct { unsigned char ai, am, bi, bm, ci, cm, si, fl; } Cfg;
typedef struct { unsigned int lo, hi; } Range;
extern Rec table[];
extern Cfg cfg[];
extern Range ranges[];
extern volatile int lockv;
extern volatile unsigned int dropped;
#define NB ${NB}
#define NRANGE ${NRANGE}
static int xchg(volatile int *p, int v) {
  __asm__ __volatile__("xchgl %0, (%1)" : "=r"(v) : "r"(p), "0"(v) : "memory");
  return v;
}
static unsigned int arg(unsigned int *sp, unsigned int i, unsigned int m) {
  unsigned int v;
  if (i == 0) return 0;
  v = sp[1 + i];
  return m ? (v != 0) : v;
}
void record(unsigned int id, unsigned int tid, unsigned int *sp) {
  Cfg *c = &cfg[id];
  unsigned int ret = sp[0], mod = 0, i, a, b, cc, s = 0, h, probes = 0;
  Rec *r;
  for (i = 0; i < NRANGE; i++) {
    if (ranges[i].hi == 0) continue;
    if (ret >= ranges[i].lo && ret < ranges[i].hi) { mod = i + 1; break; }
  }
  a = arg(sp, c->ai, c->am); b = arg(sp, c->bi, c->bm); cc = arg(sp, c->ci, c->cm);
  if (c->si) s = sp[1 + c->si];
  if ((c->fl & 1) && (a == 36 || a == 37 || a == 38 || a == 46 || a == 154 || a == 155)) b = 0xf10a7;
  if ((c->fl & 2) && ((b >= 7 && b <= 10) || b == 19 || b == 22 || b == 23)) cc = 0xf10a7;
  h = (id * 2654435761u) ^ (tid * 40503u) ^ (mod * 97u) ^ (a * 2246822519u) ^ (b * 3266489917u) ^ (cc * 668265263u);
  h = (h ^ (h >> 15)) & (NB - 1);
  while (xchg(&lockv, 1)) { }
  for (;;) {
    r = &table[h];
    if (r->count == 0) { r->id = id; r->tid = tid; r->mod = mod; r->a = a; r->b = b; r->c = cc; r->count = 1;
                         r->sumlo = s; r->sumhi = 0; break; }
    if (r->id == id && r->tid == tid && r->mod == mod && r->a == a && r->b == b && r->c == cc) {
      r->count++; r->sumlo += s; if (r->sumlo < s) r->sumhi++; break;
    }
    h = (h + 1) & (NB - 1);
    if (++probes > 1024) { dropped++; break; }
  }
  lockv = 0;
}
`, { table, cfg, ranges, lockv, dropped: droppedp });

// Caller modules (index = position + 1; 0 = anything else, e.g. the GOG wrapper itself).
const GAME_MODS = ['empire earth.exe', 'low-level engine.dll', 'dx7hrdisplay.dll', 'dx7hrtnldisplay.dll',
  'binkw32.dll', 'sxlrt233.dll', 'miles sound system mixer.dll', 'mss32.dll', 'default.dll', 'tcp protocol.dll'];
function refreshRanges() {
  for (let i = 0; i < NRANGE; i++) { ranges.add(i * 8).writeU32(0); ranges.add(i * 8 + 4).writeU32(0); }
  for (const m of Process.enumerateModules()) {
    const i = GAME_MODS.indexOf(m.name.toLowerCase());
    if (i < 0) continue;
    ranges.add(i * 8).writeU32(m.base.toUInt32());
    ranges.add(i * 8 + 4).writeU32(m.base.add(m.size).toUInt32());
  }
}
refreshRanges();
function isGameCaller(ret) {
  if (P.all_callers) return true;
  const m = Process.findModuleByAddress(ret);
  return m !== null && GAME_MODS.indexOf(m.name.toLowerCase()) >= 0;
}

const slots = [];            // id -> {iface, method, slot, vtbl, orig}
const vtbls = new Map();     // vtable address -> {iface, n, firstObj}
const patched = [];          // [slot address, original pointer]

function writeStub(id, target) {
  const s = stubs.add(id * STUB);
  const o = origs.add(id * 4);
  o.writePointer(target);
  const b = [];
  const u32 = v => { v = ptr(v).toUInt32(); b.push(v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff); };
  b.push(0x80, 0x3d); u32(enabled); b.push(0x00);        // cmp byte [enabled], 0
  b.push(0x74, 0x1f);                                      // je direct
  b.push(0x9c, 0x60);                                      // pushfd; pushad
  b.push(0x8d, 0x44, 0x24, 0x24);                          // lea eax, [esp+36] (caller's esp: ret, this, args)
  b.push(0x50);                                            // push eax
  b.push(0x64, 0xa1, 0x24, 0x00, 0x00, 0x00);              // mov eax, fs:[0x24] (thread id)
  b.push(0x50);                                            // push eax
  b.push(0x68); u32(id);                                   // push id
  b.push(0xb8); u32(cm.record);                            // mov eax, record
  b.push(0xff, 0xd0);                                      // call eax
  b.push(0x83, 0xc4, 0x0c);                                // add esp, 12
  b.push(0x61, 0x9d);                                      // popad; popfd
  b.push(0xff, 0x25); u32(o);                              // direct: jmp [orig]
  if (b.length !== 46) throw new Error('stub size ' + b.length);
  s.writeByteArray(b);
  return s;
}
function writeCfg(id, key) {
  const a = ARGS[key] || {};
  const one = v => v === undefined ? [0, 0] : (Array.isArray(v) ? [v[0], v[1] === 'b' ? 1 : 0] : [v, 0]);
  const x = one(a.a), y = one(a.b), z = one(a.c);
  cfg.add(id * 8).writeByteArray([x[0], x[1], y[0], y[1], z[0], z[1], a.sum || 0, a.fl || 0]);
}

function hookIface(obj, type, how) {
  if (obj.isNull() || !IFACES[type]) return;
  let vt;
  try { vt = obj.readPointer(); } catch (e) { emit({ kind: 'hook-error', type, error: e.message }); return; }
  const key = vt.toString();
  const known = vtbls.get(key);
  if (known) {
    if (known.iface !== type) agg('vtable-aliases', known.iface + ' also seen as ' + type);
    return;
  }
  const names = IFACES[type];
  vtbls.set(key, { iface: type, n: names.length, how, firstObj: obj.toString() });
  const r = Process.findRangeByAddress(vt);
  const prot = r ? r.protection : 'r--';
  try {
    Memory.protect(vt, names.length * 4, prot.replace(/^r./, 'rw'));
    for (let i = 0; i < names.length; i++) {
      if (slots.length >= MAXSLOT) { emit({ kind: 'hook-error', type, error: 'MAXSLOT' }); break; }
      const sa = vt.add(i * 4);
      const orig = sa.readPointer();
      const id = slots.length;
      slots.push({ iface: type, method: names[i], slot: i, vtbl: where(vt), impl: where(orig), orig });
      writeCfg(id, type + '::' + names[i]);
      const stub = writeStub(id, orig);
      sa.writePointer(stub);
      patched.push([sa, orig]);
      attachDecoder(type, names[i], orig);
    }
  } finally {
    Memory.protect(vt, names.length * 4, prot);
  }
  emit({ kind: 'hooked', type, vtbl: where(vt), obj: obj.toString(), how });
}

// ---------------------------------------------------------------------------------------------
// JS decoders on rare methods. Keyed by implementation address so shared implementations are
// attached once; the decoder for the first interface type that reaches it wins.
const decoded = new Set();
const samplers = [];
function outPtr(p) { try { return p.readPointer(); } catch (e) { return NULL; } }
function onGame(fn) { return function (args) { this.game = isGameCaller(this.returnAddress); if (this.game) fn.call(this, args); }; }
function leaveGame(fn) { return function (ret) { if (this.game) fn.call(this, ret); }; }
function sampler(orig, name, onEnter, onLeave) {
  let n = 0;
  const l = Interceptor.attach(orig, {
    onEnter(args) {
      this.go = isGameCaller(this.returnAddress) && n < P.sample_limit;
      if (!this.go) return;
      if (++n === P.sample_limit) emit({ kind: 'sampler-done', name });
      onEnter.call(this, args);
    },
    onLeave(ret) { if (this.go && onLeave) onLeave.call(this, ret); },
  });
  samplers.push(l);
}
// Enumeration callbacks: the game's callback is wrapped so every enumerated item is recorded
// (no code patching inside the display plugin, which may be unloaded and reloaded).
const cbWrappers = new Map();
function wrapCallback(args, idx, types, record) {
  const orig = args[idx];
  if (orig.isNull()) return;
  const key = orig.toString() + '|' + types.length;
  let w = cbWrappers.get(key);
  if (!w) {
    const f = new NativeFunction(orig, 'int', types, 'stdcall');
    w = new NativeCallback(function () {
      const r = f.apply(null, arguments);
      try { record(arguments, r); } catch (e) { agg('callback-errors', e.message); }
      return r;
    }, 'int', types, 'stdcall');
    cbWrappers.set(key, w);
  }
  args[idx] = w;
}

const DECODERS = {
  'IDirectDraw7::QueryInterface': qiDecoder('IDirectDraw7'),
  'IDirectDrawSurface7::QueryInterface': qiDecoder('IDirectDrawSurface7'),
  'IDirect3D7::QueryInterface': qiDecoder('IDirect3D7'),
  'IDirect3DDevice7::QueryInterface': qiDecoder('IDirect3DDevice7'),
  'IDirectDraw7::CreateSurface': orig => Interceptor.attach(orig, {
    onEnter: function (args) {
      this.desc = surfdesc(args[1]); this.pp = args[2]; this.caller = where(this.returnAddress);
      this.game = isGameCaller(this.returnAddress);
    },
    onLeave(ret) {
      const hr = ret.toUInt32();
      const s = outPtr(this.pp);
      if (hr === 0 && !s.isNull()) { surfKind.set(s.toString(), capsKind(this.desc)); hookIface(s, 'IDirectDrawSurface7', 'CreateSurface'); }
      if (this.game) agg('CreateSurface', sig(this.desc) + ' hr=' + hex(hr), { caller: this.caller });
    },
  }),
  'IDirectDraw7::CreateClipper': childDecoder(2, 'IDirectDrawClipper'),
  'IDirectDraw7::CreatePalette': childDecoder(3, 'IDirectDrawPalette'),
  'IDirectDraw7::SetCooperativeLevel': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) { this.f = args[2].toUInt32(); this.hwnd = args[1]; }),
    onLeave: leaveGame(function (ret) {
      emit({ kind: 'SetCooperativeLevel', tid: this.threadId, flags: hex(this.f), hwnd: this.hwnd.toString(),
             hr: hex(ret.toUInt32()), caller: where(this.returnAddress) });
    }),
  }),
  'IDirectDraw7::SetDisplayMode': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) { this.m = [1, 2, 3, 4, 5].map(i => args[i].toUInt32()); }),
    onLeave: leaveGame(function (ret) {
      emit({ kind: 'SetDisplayMode', tid: this.threadId, w: this.m[0], h: this.m[1], bpp: this.m[2], refresh: this.m[3],
             flags: hex(this.m[4]), hr: hex(ret.toUInt32()) });
    }),
  }),
  'IDirectDraw7::EnumDisplayModes': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) {
      agg('EnumDisplayModes-calls', 'flags=' + hex(args[1].toUInt32()) + ' desc=' + !args[2].isNull());
      wrapCallback(args, 4, ['pointer', 'pointer'], (a, r) => {
        const d = surfdesc(a[0]);
        agg('EnumDisplayModes-modes', d.w + 'x' + d.h + ' ' + d.pf + ' @' + (d.refresh || 0) + ' ret=' + r);
      });
    }),
  }),
  'IDirectDraw7::GetCaps': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) { this.p = args[1]; }),
    onLeave: leaveGame(function (ret) {
      if (this.p.isNull()) return;
      const p = this.p;
      agg('IDirectDraw7::GetCaps', 'size=' + hex(p.readU32()) + ' caps=' + hex(p.add(4).readU32()) + ' caps2=' +
        hex(p.add(8).readU32()) + ' ckey=' + hex(p.add(12).readU32()) + ' fx=' + hex(p.add(16).readU32()) +
        ' hr=' + hex(ret.toUInt32()));
    }),
  }),
  'IDirectDraw7::GetDisplayMode': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) { this.p = args[1]; }),
    onLeave: leaveGame(function (ret) { agg('IDirectDraw7::GetDisplayMode', sig(surfdesc(this.p)) + ' hr=' + hex(ret.toUInt32())); }),
  }),
  'IDirectDraw7::GetAvailableVidMem': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) { this.c = args[1].isNull() ? 0 : args[1].readU32(); this.t = args[2]; this.f = args[3]; }),
    onLeave: leaveGame(function (ret) {
      agg('GetAvailableVidMem', 'caps=' + hex(this.c) + ' total=' + (this.t.isNull() ? '-' : this.t.readU32()) +
        ' free=' + (this.f.isNull() ? '-' : this.f.readU32()) + ' hr=' + hex(ret.toUInt32()));
    }),
  }),
  'IDirectDraw7::GetDeviceIdentifier': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) { this.p = args[1]; this.f = args[2].toUInt32(); }),
    onLeave: leaveGame(function (ret) {
      const p = this.p;
      agg('GetDeviceIdentifier', 'flags=' + hex(this.f) + ' desc="' + p.add(0x200).readAnsiString() + '" vendor=' +
        hex(p.add(0x408).readU32()) + ' device=' + hex(p.add(0x40c).readU32()) + ' hr=' + hex(ret.toUInt32()));
    }),
  }),
  'IDirectDrawSurface7::GetAttachedSurface': orig => Interceptor.attach(orig, {
    onEnter(args) { this.caps = args[1].readU32(); this.pp = args[2]; this.self = args[0]; this.game = isGameCaller(this.returnAddress); },
    onLeave(ret) {
      const s = outPtr(this.pp);
      if (ret.toUInt32() === 0 && !s.isNull()) {
        if (!surfKind.has(s.toString())) surfKind.set(s.toString(), 'attached(' + hex(this.caps) + ') of ' + kindOf(this.self));
        hookIface(s, 'IDirectDrawSurface7', 'GetAttachedSurface');
      }
      if (this.game) agg('GetAttachedSurface', 'caps=' + hex(this.caps) + ' of ' + kindOf(this.self) + ' hr=' + hex(ret.toUInt32()));
    },
  }),
  'IDirectDrawSurface7::AddAttachedSurface': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) { agg('AddAttachedSurface', kindOf(args[1]) + ' -> ' + kindOf(args[0])); }),
  }),
  'IDirectDrawSurface7::SetColorKey': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) {
      const k = args[2].isNull() ? 'NULL' : hex(args[2].readU32()) + '-' + hex(args[2].add(4).readU32());
      agg('SetColorKey', 'flags=' + hex(args[1].toUInt32()) + ' key=' + k + ' on ' + kindOf(args[0]));
    }),
  }),
  'IDirectDrawSurface7::Blt': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) {
      const f = args[4].toUInt32();
      let fx = '';
      if ((f & 0x400) && !args[5].isNull()) fx = ' fill=' + hex(args[5].add(0x50).readU32());
      const rect = p => p.isNull() ? 'full' : [0, 4, 8, 12].map(o => p.add(o).readS32()).join(',');
      agg('Blt', kindOf(args[2]) + ' -> ' + kindOf(args[0]) + ' flags=' + hex(f) + fx +
        ' stretch=' + (args[1].isNull() || args[3].isNull() ? '?' : String(rectSize(args[1]) !== rectSize(args[3]))));
      if (canDecode('Blt-rects')) emit({ kind: 'Blt', dst: kindOf(args[0]), dstRect: rect(args[1]), src: kindOf(args[2]),
        srcRect: rect(args[3]), flags: hex(f), tid: this.threadId, caller: where(this.returnAddress) });
    }),
  }),
  'IDirectDrawSurface7::BltFast': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) { agg('BltFast', kindOf(args[3]) + ' -> ' + kindOf(args[0]) + ' flags=' + hex(args[5].toUInt32())); }),
  }),
  'IDirectDrawSurface7::Flip': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) { agg('Flip', kindOf(args[0]) + ' target=' + kindOf(args[1]) + ' flags=' + hex(args[2].toUInt32())); }),
  }),
  'IDirectDrawSurface7::Lock': orig => sampler(orig, 'Surface Lock', function (args) {
    this.self = args[0]; this.p = args[2]; this.f = args[3].toUInt32(); this.rect = !args[1].isNull();
  }, function (ret) {
    const d = surfdesc(this.p);
    agg('Surface Lock', kindOf(this.self) + ' flags=' + hex(this.f) + ' rect=' + this.rect + ' -> pitch=' + d.pitch +
      ' ' + d.w + 'x' + d.h + ' ' + d.pf + ' hr=' + hex(ret.toUInt32()));
  }),
  'IDirectDrawSurface7::GetDC': orig => sampler(orig, 'Surface GetDC', function (args) { this.self = args[0]; }, function (ret) {
    agg('Surface GetDC', kindOf(this.self) + ' hr=' + hex(ret.toUInt32()) + ' tid=' + this.threadId);
  }),
  'IDirect3D7::CreateDevice': orig => Interceptor.attach(orig, {
    onEnter(args) { this.iid = guidAt(args[1]); this.rt = args[2]; this.pp = args[3]; this.game = isGameCaller(this.returnAddress); },
    onLeave(ret) {
      const d = outPtr(this.pp);
      if (ret.toUInt32() === 0 && !d.isNull()) hookIface(d, 'IDirect3DDevice7', 'CreateDevice');
      if (this.game) emit({ kind: 'IDirect3D7::CreateDevice', tid: this.threadId, device: this.iid, target: kindOf(this.rt),
                            hr: hex(ret.toUInt32()), caller: where(this.returnAddress) });
    },
  }),
  'IDirect3D7::CreateVertexBuffer': orig => Interceptor.attach(orig, {
    onEnter(args) {
      const p = args[1];
      this.d = 'size=' + p.readU32() + ' caps=' + hex(p.add(4).readU32()) + ' fvf=' + hex(p.add(8).readU32()) +
        ' n=' + p.add(12).readU32() + ' flags=' + hex(args[3].toUInt32());
      this.pp = args[2]; this.game = isGameCaller(this.returnAddress);
    },
    onLeave(ret) {
      const v = outPtr(this.pp);
      if (ret.toUInt32() === 0 && !v.isNull()) hookIface(v, 'IDirect3DVertexBuffer7', 'CreateVertexBuffer');
      if (this.game) agg('CreateVertexBuffer', this.d + ' hr=' + hex(ret.toUInt32()), { caller: where(this.returnAddress) });
    },
  }),
  'IDirect3D7::EnumDevices': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) {
      wrapCallback(args, 1, ['pointer', 'pointer', 'pointer', 'pointer'], (a, r) => {
        const d = a[2];
        agg('EnumDevices', '"' + a[1].readAnsiString() + '" ' + guidAt(d.add(0xc4)) + ' devcaps=' + hex(d.readU32()) +
          ' renderbits=' + hex(d.add(0x74).readU32()) + ' zbits=' + hex(d.add(0x78).readU32()) +
          ' fvfcaps=' + hex(d.add(0xb0).readU32()) + ' stages=' + d.add(0xb8).readU16() + ' lights=' + d.add(0xbc).readU32() +
          ' ret=' + r);
      });
    }),
  }),
  'IDirect3D7::EnumZBufferFormats': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) {
      const dev = guidAt(args[1]);
      wrapCallback(args, 2, ['pointer', 'pointer'], (a, r) => agg('EnumZBufferFormats', dev + ' ' + pixfmt(a[0]) + ' ret=' + r));
    }),
  }),
  'IDirect3DDevice7::EnumTextureFormats': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) {
      wrapCallback(args, 1, ['pointer', 'pointer'], (a, r) => agg('EnumTextureFormats', pixfmt(a[0]) + ' ret=' + r));
    }),
  }),
  'IDirect3DDevice7::GetCaps': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) { this.p = args[1]; }),
    onLeave: leaveGame(function (ret) {
      const d = this.p;
      agg('IDirect3DDevice7::GetCaps', 'devcaps=' + hex(d.readU32()) + ' renderbits=' + hex(d.add(0x74).readU32()) +
        ' texopcaps=' + hex(d.add(0xb4).readU32()) + ' stages=' + d.add(0xb8).readU16() + ' hr=' + hex(ret.toUInt32()));
    }),
  }),
  'IDirect3DDevice7::SetTransform': orig => sampler(orig, 'SetTransform', function (args) {
    const t = args[1].toUInt32();
    const m = args[2];
    const f = i => m.add(i * 4).readFloat();
    let cls;
    const ident = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15].every(i => f(i) === ((i % 5 === 0) ? 1 : 0));
    if (ident) cls = 'identity';
    else if (f(11) === 1 && f(15) === 0) cls = 'perspective';
    else if (f(3) === 0 && f(7) === 0 && f(11) === 0 && f(15) === 1) cls = (t === 3 ? 'orthographic' : 'affine');
    else cls = 'other';
    agg('SetTransform', (XFORM[t] || t) + ' ' + cls);
    if (t === 3 && canDecode('projection-' + cls)) emit({ kind: 'projection', cls, m: Array.from({ length: 16 }, (_, i) => f(i)) });
  }),
  'IDirect3DDevice7::SetViewport': orig => sampler(orig, 'SetViewport', function (args) {
    const v = args[1];
    agg('SetViewport', [0, 4, 8, 12].map(o => v.add(o).readU32()).join(',') + ' z=' + v.add(16).readFloat() + '..' + v.add(20).readFloat());
  }),
  'IDirect3DDevice7::SetMaterial': orig => sampler(orig, 'SetMaterial', function (args) {
    const m = args[1];
    const c = o => [0, 4, 8, 12].map(i => m.add(o + i).readFloat().toFixed(3)).join(',');
    agg('SetMaterial', 'diffuse=' + c(0) + ' ambient=' + c(16) + ' specular=' + c(32) + ' emissive=' + c(48) +
      ' power=' + m.add(64).readFloat());
  }),
  'IDirect3DDevice7::SetLight': orig => sampler(orig, 'SetLight', function (args) {
    const l = args[2];
    const t = l.readU32();
    agg('SetLight', 'index=' + args[1].toUInt32() + ' type=' + ({ 1: 'POINT', 2: 'SPOT', 3: 'DIRECTIONAL' }[t] || t) +
      ' range=' + l.add(0x4c).readFloat() + ' att=' + [0x54, 0x58, 0x5c].map(o => l.add(o).readFloat()).join(','));
  }),
  'IDirectInputA::CreateDevice': orig => Interceptor.attach(orig, {
    onEnter(args) { this.g = guidAt(args[1]); this.pp = args[2]; this.game = isGameCaller(this.returnAddress); },
    onLeave(ret) {
      const d = outPtr(this.pp);
      if (ret.toUInt32() === 0 && !d.isNull()) { diName.set(d.toString(), this.g); hookIface(d, 'IDirectInputDeviceA', 'CreateDevice'); }
      if (this.game) emit({ kind: 'IDirectInputA::CreateDevice', guid: this.g, hr: hex(ret.toUInt32()), tid: this.threadId,
                            caller: where(this.returnAddress) });
    },
  }),
  'IDirectInputDeviceA::SetDataFormat': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) {
      const f = args[1];
      agg('DI SetDataFormat', diOf(args[0]) + ' size=' + f.readU32() + ' objsize=' + f.add(4).readU32() + ' flags=' +
        hex(f.add(8).readU32()) + ' datasize=' + f.add(12).readU32() + ' nobjs=' + f.add(16).readU32());
    }),
  }),
  'IDirectInputDeviceA::SetCooperativeLevel': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) { this.d = diOf(args[0]); this.f = args[2].toUInt32(); }),
    onLeave: leaveGame(function (ret) {
      emit({ kind: 'DI SetCooperativeLevel', dev: this.d, flags: hex(this.f), hr: hex(ret.toUInt32()), tid: this.threadId,
             caller: where(this.returnAddress) });
    }),
  }),
  'IDirectInputDeviceA::SetProperty': diPropDecoder('DI SetProperty', false),
  'IDirectInputDeviceA::GetProperty': diPropDecoder('DI GetProperty', true),
  'IDirectInputDeviceA::GetObjectInfo': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) { this.d = diOf(args[0]); this.k = 'obj=' + hex(args[2].toUInt32()) + ' how=' + args[3].toUInt32() + ' size=' + hex(args[1].readU32()); }),
    onLeave: leaveGame(function (ret) { agg('DI GetObjectInfo', this.d + ' ' + this.k + ' hr=' + hex(ret.toUInt32())); }),
  }),
  'IDirectInputDeviceA::Acquire': diHrDecoder('DI Acquire'),
  'IDirectInputDeviceA::Unacquire': diHrDecoder('DI Unacquire'),
  'IDirectInputDeviceA::GetDeviceState': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) { this.d = diOf(args[0]); this.cb = args[1].toUInt32(); }),
    onLeave: leaveGame(function (ret) { agg('DI GetDeviceState', this.d + ' cb=' + this.cb + ' hr=' + hex(ret.toUInt32()) + ' tid=' + this.threadId); }),
  }),
  'IDirectInputDeviceA::GetDeviceData': orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) {
      this.d = diOf(args[0]); this.cb = args[1].toUInt32(); this.buf = !args[2].isNull(); this.pn = args[3];
      this.nin = args[3].readU32(); this.f = args[4].toUInt32();
    }),
    onLeave: leaveGame(function (ret) {
      const n = this.pn.readU32();
      const bucket = n === 0 ? '0' : n === 1 ? '1' : n <= 4 ? '2-4' : n <= 16 ? '5-16' : n <= 64 ? '17-64' : n < 256 ? '65-255' : '256+';
      agg('DI GetDeviceData', this.d + ' cb=' + this.cb + ' buf=' + this.buf + ' in=' + (this.nin === 0xffffffff ? 'INFINITE' : this.nin) +
        ' flags=' + hex(this.f) + ' out=' + bucket + ' hr=' + hex(ret.toUInt32()) + ' tid=' + this.threadId);
    }),
  }),
};
function rectSize(p) { return (p.add(8).readS32() - p.readS32()) + 'x' + (p.add(12).readS32() - p.add(4).readS32()); }
function qiDecoder(from) {
  return orig => Interceptor.attach(orig, {
    onEnter(args) { this.iid = guidAt(args[1]); this.pp = args[2]; this.game = isGameCaller(this.returnAddress); },
    onLeave(ret) {
      const o = outPtr(this.pp);
      const type = IID_TYPE[this.iid];
      if (ret.toUInt32() === 0 && type) hookIface(o, type, from + '::QueryInterface');
      if (this.game) agg('QueryInterface', from + ' -> ' + this.iid + ' hr=' + hex(ret.toUInt32()), { caller: where(this.returnAddress) });
    },
  });
}
function childDecoder(outIdx, type) {
  return orig => Interceptor.attach(orig, {
    onEnter(args) { this.pp = args[outIdx]; this.f = args[1].toUInt32(); this.game = isGameCaller(this.returnAddress); },
    onLeave(ret) {
      const o = outPtr(this.pp);
      if (ret.toUInt32() === 0) hookIface(o, type, 'create');
      if (this.game) agg('Create ' + type, 'flags=' + hex(this.f) + ' hr=' + hex(ret.toUInt32()), { caller: where(this.returnAddress) });
    },
  });
}
const diName = new Map();
function diOf(p) { return diName.get(p.toString()) || p.toString(); }
function diPropDecoder(name, isGet) {
  return orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) {
      this.d = diOf(args[0]);
      const g = args[1].toUInt32();
      this.prop = g < 0x10000 ? (DIPROP[g] || g) : guidAt(args[1]);
      this.h = args[2];
      this.hdr = 'size=' + this.h.readU32() + ' hdr=' + this.h.add(4).readU32() + ' obj=' + hex(this.h.add(8).readU32()) + ' how=' + this.h.add(12).readU32();
      if (!isGet) this.v = this.h.add(16).readU32();
    }),
    onLeave: leaveGame(function (ret) {
      const v = isGet ? this.h.add(16).readU32() : this.v;
      agg(name, this.d + ' ' + this.prop + ' ' + this.hdr + ' data=' + v + ' hr=' + hex(ret.toUInt32()));
    }),
  });
}
function diHrDecoder(name) {
  return orig => Interceptor.attach(orig, {
    onEnter: onGame(function (args) { this.d = diOf(args[0]); }),
    onLeave: leaveGame(function (ret) { agg(name, this.d + ' hr=' + hex(ret.toUInt32()) + ' tid=' + this.threadId); }),
  });
}
function attachDecoder(type, method, orig) {
  const key = type + '::' + method;
  const d = DECODERS[key];
  if (!d) return;
  const k = orig.toString() + '|' + method;
  if (decoded.has(k)) return;
  decoded.add(k);
  try { d(orig); } catch (e) { emit({ kind: 'decoder-error', key, impl: where(orig), error: e.message }); }
}

// ---------------------------------------------------------------------------------------------
// Entry points
const hookedExports = new Set();
function hookDDraw(m) {
  for (const name of ['DirectDrawEnumerateA', 'DirectDrawEnumerateExA', 'DirectDrawCreate']) {
    const e = m.findExportByName(name);
    if (!e || hookedExports.has(e.toString())) continue;
    hookedExports.add(e.toString());
    Interceptor.attach(e, {
      // The plugin's enumeration callback creates DirectDraw and the whole screen inside it, so it is
      // not wrapped (deep JS re-entrancy); the devices it sees show up as DirectDrawCreateEx events.
      onEnter(args) { this.caller = where(this.returnAddress); this.flags = name === 'DirectDrawEnumerateExA' ? hex(args[2].toUInt32()) : ''; },
      onLeave(ret) { emit({ kind: name, tid: this.threadId, flags: this.flags, hr: hex(ret.toUInt32()), caller: this.caller }); },
    });
  }
  const a = m.findExportByName('DirectDrawCreateEx');
  if (!a || hookedExports.has(a.toString())) return;
  hookedExports.add(a.toString());
  Interceptor.attach(a, {
    onEnter(args) { this.guid = args[0].isNull() ? 'NULL' : guidAt(args[0]); this.pp = args[1]; this.iid = guidAt(args[2]); this.caller = where(this.returnAddress); },
    onLeave(ret) {
      emit({ kind: 'DirectDrawCreateEx', tid: this.threadId, guid: this.guid, iid: this.iid, hr: hex(ret.toUInt32()), caller: this.caller });
      if (ret.toUInt32() === 0) hookIface(outPtr(this.pp), IID_TYPE[this.iid] || 'IDirectDraw7', 'DirectDrawCreateEx');
    },
  });
  emit({ kind: 'hooked-export', what: 'DirectDrawCreateEx', module: m.path });
}
function hookDInput(m) {
  for (const name of ['DirectInputCreateA', 'DirectInputCreateEx']) {
    const a = m.findExportByName(name);
    if (!a || hookedExports.has(a.toString())) continue;
    hookedExports.add(a.toString());
    Interceptor.attach(a, {
      onEnter(args) {
        this.ver = args[1].toUInt32();
        this.pp = name === 'DirectInputCreateEx' ? args[3] : args[2];
        this.iid = name === 'DirectInputCreateEx' ? guidAt(args[2]) : 'IID_IDirectInputA';
        this.caller = where(this.returnAddress);
      },
      onLeave(ret) {
        emit({ kind: name, tid: this.threadId, version: hex(this.ver), iid: this.iid, hr: hex(ret.toUInt32()), caller: this.caller });
        if (ret.toUInt32() === 0 && this.iid === 'IID_IDirectInputA') hookIface(outPtr(this.pp), 'IDirectInputA', name);
      },
    });
  }
}

// Attach mode: find the live interfaces through the engine's and the plugin's globals.
function discover() {
  const eng = Process.findModuleByName('Low-Level Engine.dll');
  if (!eng) return;
  const found = [];
  const tryHook = (p, type, how) => { try { if (!p.isNull()) { hookIface(p, type, how); found.push(how); } } catch (e) { emit({ kind: 'discover-error', how, error: e.message }); } };
  try {
    const sar = eng.findExportByName('?sActiveRasterizer@GERasterizer@@1PAV1@A');
    const r = sar ? sar.readPointer() : NULL;
    if (!r.isNull()) {
      const b = r.add(0x6c).readPointer(), e = r.add(0x70).readPointer();
      for (let p = b, i = 0; !b.isNull() && p.compare(e) < 0 && i < 8; p = p.add(4), i++) {
        const ctx = p.readPointer();
        if (ctx.isNull()) continue;
        tryHook(ctx.add(0x20).readPointer().add(0x30).readPointer(), 'IDirectDraw7', 'discover:DX7Screen+0x30');
        tryHook(ctx.add(0x18).readPointer(), 'IDirect3D7', 'discover:ctx+0x18');
        tryHook(ctx.add(0x1c).readPointer(), 'IDirect3DDevice7', 'discover:ctx+0x1c');
        tryHook(ctx.add(0x24).readPointer(), 'IDirect3DVertexBuffer7', 'discover:ctx+0x24');
      }
      const sb = r.add(0x20).readPointer(), se = r.add(0x24).readPointer();
      for (let p = sb, i = 0; !sb.isNull() && p.compare(se) < 0 && i < 8; p = p.add(4), i++) {
        const st = p.readPointer();
        if (st.isNull()) continue;
        for (const [off, kind] of [[0x2ac, 'front'], [0x2b0, 'back']]) {
          const gs = st.add(off).readPointer();
          if (gs.isNull()) continue;
          const s = gs.add(0x48).readPointer();
          if (!surfKind.has(s.toString())) surfKind.set(s.toString(), kind + '(discovered)');
          tryHook(s, 'IDirectDrawSurface7', 'discover:state+' + hex(off));
        }
      }
    }
  } catch (e) { emit({ kind: 'discover-error', what: 'rasterizer', error: e.message }); }
  try {
    tryHook(eng.base.add(0x128b94).readPointer(), 'IDirectInputA', 'discover:engine+0x128b94');
    const ms = eng.findExportByName('?gISMouse@@3PAVISMouse@@A');
    const ks = eng.findExportByName('?gISKeyboard@@3PAVISKeyboard@@A');
    if (ms && !ms.readPointer().isNull()) { const d = ms.readPointer().add(0x44).readPointer(); diName.set(d.toString(), 'GUID_SysMouse'); tryHook(d, 'IDirectInputDeviceA', 'discover:gISMouse+0x44'); }
    if (ks && !ks.readPointer().isNull()) { const d = ks.readPointer().add(0x18).readPointer(); diName.set(d.toString(), 'GUID_SysKeyboard'); tryHook(d, 'IDirectInputDeviceA', 'discover:gISKeyboard+0x18'); }
  } catch (e) { emit({ kind: 'discover-error', what: 'dinput', error: e.message }); }
  emit({ kind: 'discovered', found });
}

const seen = new Set();
function onModule(m) {
  const n = m.name.toLowerCase();
  if (GAME_MODS.indexOf(n) >= 0) refreshRanges();
  if (seen.has(m.path.toLowerCase())) return;
  try {
    if (n === 'ddraw.dll') { seen.add(m.path.toLowerCase()); hookDDraw(m); }
    else if (n === 'dinput.dll') { seen.add(m.path.toLowerCase()); hookDInput(m); }
  } catch (e) { emit({ kind: 'hook-error', module: m.name, error: e.message }); }
}
Process.enumerateModules().forEach(onModule);
Process.attachModuleObserver({ onAdded: onModule, onRemoved(m) { if (GAME_MODS.indexOf(m.name.toLowerCase()) >= 0) refreshRanges(); } });
if (P.discover) discover();
enabled.writeU8(1);
const startedAt = Date.now();
emit({ kind: 'loaded', tid: Process.getCurrentThreadId() });

// ---------------------------------------------------------------------------------------------
function snapshot() {
  const rows = [];
  const buf = new Uint32Array(table.readByteArray(NB * 40));
  for (let i = 0; i < NB; i++) {
    const o = i * 10;
    const count = buf[o + 6];
    if (!count) continue;
    const s = slots[buf[o]];
    const r = { iface: s.iface, method: s.method, caller: buf[o + 2] ? GAME_MODS[buf[o + 2] - 1] : 'other',
                tid: buf[o + 1], count };
    const a = ARGS[s.iface + '::' + s.method] || {};
    if (a.a !== undefined) r.a = buf[o + 3];
    if (a.b !== undefined) r.b = buf[o + 4];
    if (a.c !== undefined) r.c = buf[o + 5];
    if (a.sum) r.sum = buf[o + 8] * 4294967296 + buf[o + 7];
    if (s.method === 'SetRenderState' || s.method === 'GetRenderState') r.a_name = RS[r.a] || String(r.a);
    if (s.method.indexOf('TextureStageState') >= 0) r.b_name = TSS[r.b] || String(r.b);
    if (s.method.indexOf('Primitive') >= 0) r.a_name = PRIM[r.a] || String(r.a);
    if (s.method.indexOf('Transform') >= 0) r.a_name = XFORM[r.a] || String(r.a);
    if (s.iface === 'IDirectInputDeviceA' && s.method.indexOf('Property') >= 0) r.a_name = DIPROP[r.a] || String(r.a);
    for (const k of ['a', 'b', 'c']) if (r[k] === 0xf10a7) r[k] = 'float';
    rows.push(r);
  }
  rows.sort((x, y) => y.count - x.count);
  const aggOut = {};
  for (const [k, m] of aggs) aggOut[k] = Array.from(m, ([sigv, v]) => Object.assign({ sig: sigv }, v)).sort((x, y) => y.count - x.count);
  return {
    elapsedMs: Date.now() - startedAt, dropped: droppedp.readU32(),
    vtables: Array.from(vtbls, ([k, v]) => Object.assign({ vtbl: where(ptr(k)) }, v)),
    slots: slots.map(s => ({ iface: s.iface, method: s.method, slot: s.slot, vtbl: s.vtbl, impl: s.impl })),
    rows, aggregates: aggOut, events,
  };
}
function stop() {
  enabled.writeU8(0);
  let n = 0;
  for (const [sa, orig] of patched) {
    const r = Process.findRangeByAddress(sa);
    const prot = r ? r.protection : 'r--';
    try { Memory.protect(sa, 4, prot.replace(/^r./, 'rw')); sa.writePointer(orig); Memory.protect(sa, 4, prot); n++; } catch (e) { }
  }
  for (const l of samplers) { try { l.detach(); } catch (e) { } }
  return n;
}
rpc.exports = { snapshot, stop };
