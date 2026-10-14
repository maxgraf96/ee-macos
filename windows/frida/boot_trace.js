// Ordered boot trace (mailbox 0010 Part 2): every call that game code (EXE, engine, plugins) makes
// to an imported Win32 / C runtime / COM-creation function, from spawn until the script stops:
// sequence number, time, thread id and role, caller module+RVA, function, decoded arguments and
// the return value. After PARAMS.repeat calls of one function from one call site only a count is
// kept. CreateFontA / CreateFontIndirectA are decoded in full (0011).
//
// How: each imported function is hooked once with a native (CModule) listener. It checks the
// return address against the game modules' ranges and a per-(function, call site) counter, and
// calls into JS only for game calls under the limit, so the system's and drivers' own traffic on
// the same functions never reaches JS. Imports are taken from the PE import directories of the
// game modules as they load (ordinal imports included) and resolved with GetProcAddress.
// Records go to trace/NNNNN.json via run.py's file messages; snapshot() has the counts.
//
// Run at spawn:  run.py --spawn --scripts modules,boot_trace --seconds 120 --out ...\boot-trace
// PARAMS: repeat (50), skip ([names] not hooked), chunk (records per file, 2000), modules (game modules).
'use strict';

if (Process.pointerSize !== 4) throw new Error('boot_trace.js: 32-bit targets only');
const P = Object.assign({ repeat: 50, chunk: 2000, skip: [] }, globalThis.PARAMS || {});
const GAME = (P.modules || ['empire earth.exe', 'low-level engine.dll', 'sxlrt233.dll', 'language.dll', 'dx7hrdisplay.dll',
  'dx7hrtnldisplay.dll', 'default.dll', 'tcp protocol.dll', 'miles sound system mixer.dll']).map(s => s.toLowerCase());
// Synchronisation and TLS primitives are not hooked: they run inside Frida's own runtime too, and
// their rates are known from iat_census (legion.md 5.5).
const NOHOOK = new Set(['EnterCriticalSection', 'LeaveCriticalSection', 'TryEnterCriticalSection',
  'InitializeCriticalSection', 'DeleteCriticalSection', 'TlsGetValue', 'TlsSetValue', 'GetLastError',
  'SetLastError', 'InterlockedIncrement', 'InterlockedDecrement', 'InterlockedExchange',
  'InterlockedCompareExchange', 'GetCurrentThreadId'].concat(P.skip));
const t0 = Date.now();

// ---- argument signatures: s ANSI string, w wide string, x hex, d int, b bool, h handle, p pointer,
//      g GUID*, f LOGFONTA*, r RECT*, n string of length in the next arg; return code after ':'
const SIG = {
  CreateFileA: 'sxxpxxh:h', CreateFileW: 'wxxpxxh:h', ReadFile: 'hpdpp:b', WriteFile: 'hpdpp:b', CloseHandle: 'h:b',
  SetFilePointer: 'hdpx:x', GetFileSize: 'hp:x', DeleteFileA: 's:b', GetFileAttributesA: 's:x', FindFirstFileA: 'sp:h',
  FindNextFileA: 'hp:b', FindClose: 'h:b', CreateDirectoryA: 'sp:b', GetCurrentDirectoryA: 'dp:d', SetCurrentDirectoryA: 's:b',
  GetModuleFileNameA: 'hpd:d', GetModuleHandleA: 's:h', LoadLibraryA: 's:h', LoadLibraryExA: 'shx:h', FreeLibrary: 'h:b',
  GetProcAddress: 'hs:p', GetVersionExA: 'p:b', GetSystemDirectoryA: 'pd:d', GetWindowsDirectoryA: 'pd:d',
  GetEnvironmentVariableA: 'spd:d', GetPrivateProfileStringA: 'sssp ds:d', GetDriveTypeA: 's:d', GetVolumeInformationA: 'spdpppp d:b',
  GetDiskFreeSpaceA: 'spppp:b', CreateThread: 'pdppxp:h', CreateEventA: 'pbbs:h', CreateMutexA: 'pbs:h', CreateSemaphoreA: 'pdds:h',
  WaitForSingleObject: 'hd:x', WaitForMultipleObjects: 'dpbd:x', SetEvent: 'h:b', ResetEvent: 'h:b', Sleep: 'd:', SleepEx: 'db:d',
  SetThreadPriority: 'hd:b', GetTickCount: ':d', QueryPerformanceCounter: 'p:b', QueryPerformanceFrequency: 'p:b',
  GlobalAlloc: 'xd:h', GlobalLock: 'h:p', GlobalUnlock: 'h:b', GlobalFree: 'h:h', VirtualAlloc: 'pdxx:p', VirtualFree: 'pdx:b',
  HeapAlloc: 'hxd:p', HeapFree: 'hxp:b', MultiByteToWideChar: 'dxsdpd:d', WideCharToMultiByte: 'dxwdpdpp:d',
  OutputDebugStringA: 's:', GetUserDefaultLCID: ':x', GetACP: ':d', GetLocaleInfoA: 'xxpd:d', SetErrorMode: 'x:x',
  RegOpenKeyExA: 'hsxxp:x', RegCreateKeyExA: 'hsxsxxppp:x', RegQueryValueExA: 'hsppppx:x', RegSetValueExA: 'hsxxpd:x',
  RegCloseKey: 'h:x', RegDeleteValueA: 'hs:x', RegDeleteKeyA: 'hs:x',
  RegisterClassA: 'p:x', RegisterClassExA: 'p:x', CreateWindowExA: 'xssxddddhhhp:h', DestroyWindow: 'h:b', ShowWindow: 'hd:b',
  UpdateWindow: 'h:b', SetWindowPos: 'hhddddx:b', MoveWindow: 'hddddb:b', SetWindowTextA: 'hs:b', SetWindowLongA: 'hdx:x',
  GetWindowLongA: 'hd:x', SetForegroundWindow: 'h:b', SetFocus: 'h:h', SetActiveWindow: 'h:h', ShowCursor: 'b:d',
  SetCursor: 'h:h', LoadCursorA: 'hx:h', LoadIconA: 'hx:h', LoadImageA: 'hxdddx:h', LoadStringA: 'hdpd:d', ClipCursor: 'r:b',
  GetSystemMetrics: 'd:d', SystemParametersInfoA: 'xxpx:b', MessageBoxA: 'hssx:d', PeekMessageA: 'phxxx:b',
  GetMessageA: 'phxx:b', PostMessageA: 'hxxx:b', SendMessageA: 'hxxx:x', PostThreadMessageA: 'dxxx:b', PostQuitMessage: 'd:',
  DefWindowProcA: 'hxxx:x', GetClientRect: 'hp:b', GetWindowRect: 'hp:b', AdjustWindowRect: 'rxb:b', GetDC: 'h:h',
  ReleaseDC: 'hh:d', GetDeviceCaps: 'hd:d', EnumDisplaySettingsA: 'sdp:b', ChangeDisplaySettingsA: 'px:d', SetTimer: 'hxdp:x',
  KillTimer: 'hx:b', MapVirtualKeyA: 'xx:x', GetKeyboardLayout: 'd:x', GetAsyncKeyState: 'd:x', GetKeyState: 'd:x',
  CreateFontA: 'ddddddddddddds:h', CreateFontIndirectA: 'f:h', SelectObject: 'hh:h', DeleteObject: 'h:b', SetTextColor: 'hx:x',
  SetBkMode: 'hd:d', SetBkColor: 'hx:x', TextOutA: 'hddnd:b', ExtTextOutA: 'hddxrndp:b', GetTextExtentPoint32A: 'hndp:b',
  GetTextMetricsA: 'hp:b', GetTextFaceA: 'hdp:d', CreateCompatibleDC: 'h:h', CreateDIBSection: 'hpxpxx:h', BitBlt: 'hddddhddx:b',
  GetStockObject: 'd:h', CreateSolidBrush: 'x:h', FillRect: 'hrh:d', GetCharWidthA: 'hxxp:b', GetCharABCWidthsA: 'hxxp:b',
  timeGetTime: ':d', timeBeginPeriod: 'd:x', timeEndPeriod: 'd:x', timeSetEvent: 'ddppx:x', timeKillEvent: 'x:x',
  mciSendCommandA: 'xxxp:x', waveOutGetNumDevs: ':d', joyGetNumDevs: ':d', joyGetDevCapsA: 'dpd:x',
  DirectDrawCreateEx: 'gpgp:x', DirectDrawCreate: 'gpp:x', DirectDrawEnumerateExA: 'ppx:x', DirectInputCreateA: 'hxpp:x',
  DirectInputCreateEx: 'hxgpp:x', DirectSoundCreate: 'gpp:x', CoInitialize: 'p:x', CoInitializeEx: 'px:x', CoUninitialize: ':',
  CoCreateInstance: 'gpxgp:x', WSAStartup: 'xp:d', socket: 'ddd:h', bind: 'hpd:d', connect: 'hpd:d', listen: 'hd:d',
  setsockopt: 'hddpd:d', ioctlsocket: 'hxp:d', gethostname: 'pd:d', gethostbyname: 's:p', inet_addr: 's:x',
  GetAdaptersInfo: 'pp:x',
  fopen: 'ss:p', fclose: 'p:d', fread: 'pddp:d', fwrite: 'pddp:d', fseek: 'pdd:d', ftell: 'p:d', fgets: 'pdp:s', fprintf: 'ps:d',
  _open: 'sxx:d', _close: 'd:d', _read: 'dpd:d', _lseek: 'ddd:d', _access: 'sd:d', _stat: 'sp:d', _findfirst: 'sp:d',
  _findnext: 'dp:d', _mkdir: 's:d', remove: 's:d', rename: 'ss:d', getenv: 's:s', _getcwd: 'pd:s', atoi: 's:d', atof: 's:x',
  strtol: 'spd:d', sscanf: 'ss:d', sprintf: 'ps:d', _snprintf: 'pds:d', vsprintf: 'psp:d', srand: 'd:', rand: ':d', time: 'p:d',
  _controlfp: 'xx:x', setlocale: 'ds:s', _beginthreadex: 'pdppdp:h', _beginthread: 'pdp:h', exit: 'd:',
  'mbstowcs': 'psd:d', 'wcstombs': 'pwd:d',
};

// ---- shared native state (TinyCC: no writable globals, so everything goes through pointers)
const NR = 32, NT = 1 << 16;
const SH_LOCK = 0, SH_ENABLED = 4, SH_LIMIT = 8, SH_NR = 12, SH_LO = 16, SH_HI = SH_LO + 4 * NR, SH_TAB = SH_HI + 4 * NR;
const shared = Memory.alloc(SH_TAB + 16 * NT);
shared.add(SH_ENABLED).writeU32(1);
shared.add(SH_LIMIT).writeU32(P.repeat);

const jsEnter = new NativeCallback((fn, esp, tid, caller) => onCall(fn, esp, tid, caller), 'uint32', ['uint32', 'uint32', 'uint32', 'uint32']);
const jsLeave = new NativeCallback((seq, eax, edx) => onReturn(seq, eax, edx), 'void', ['uint32', 'uint32', 'uint32']);
const cm = new CModule(`
#include <gum/guminterceptor.h>
typedef unsigned int u32;
typedef struct { u32 fn, caller, count, pad; } Ent;
extern u32 js_enter(u32 fn, u32 esp, u32 tid, u32 caller);
extern void js_leave(u32 seq, u32 eax, u32 edx);
static int xchg(volatile int *p, int v) { __asm__ __volatile__("xchgl %0, (%1)" : "=r"(v) : "r"(p), "0"(v) : "memory"); return v; }
/* function data: [0] shared state, [1] function index */
void onEnter(GumInvocationContext *ic) {
  u32 *fd = (u32 *) gum_invocation_context_get_listener_function_data(ic);
  unsigned char *sh = (unsigned char *) fd[0];
  u32 *inv = (u32 *) gum_invocation_context_get_listener_invocation_data(ic, 4);
  u32 ra = (u32) gum_invocation_context_get_return_address(ic), n = *(u32 *)(sh + ${SH_NR}), i, h, c = 0;
  Ent *tab = (Ent *)(sh + ${SH_TAB}), *e;
  inv[0] = 0;
  if (!*(u32 *)(sh + ${SH_ENABLED})) return;
  for (i = 0; i < n; i++) if (ra >= ((u32 *)(sh + ${SH_LO}))[i] && ra < ((u32 *)(sh + ${SH_HI}))[i]) break;
  if (i == n) return;
  h = ((fd[1] * 2654435761u) ^ (ra * 40503u)) & ${NT - 1};
  while (xchg((volatile int *) sh, 1)) { }
  for (i = 0; i < ${NT}; i++, h = (h + 1) & ${NT - 1}) {
    e = &tab[h];
    if (e->count == 0) { e->fn = fd[1]; e->caller = ra; e->count = 1; c = 1; break; }
    if (e->fn == fd[1] && e->caller == ra) { c = ++e->count; break; }
  }
  xchg((volatile int *) sh, 0);
  if (c == 0 || c > *(u32 *)(sh + ${SH_LIMIT})) return;
  inv[0] = js_enter(fd[1], (u32) ic->cpu_context->esp, (u32) gum_invocation_context_get_thread_id(ic), ra);
}
void onLeave(GumInvocationContext *ic) {
  u32 *inv = (u32 *) gum_invocation_context_get_listener_invocation_data(ic, 4);
  if (inv[0]) js_leave(inv[0], (u32) ic->cpu_context->eax, (u32) ic->cpu_context->edx);
}
`, { js_enter: jsEnter, js_leave: jsLeave });

// ---- modules, imports, hooks
const k32 = Process.getModuleByName('kernel32.dll');
const GetProcAddress = new NativeFunction(k32.getExportByName('GetProcAddress'), 'pointer', ['pointer', 'pointer'], 'stdcall');
const fns = [];                 // index -> { dll, name, addr, sig }
const byAddr = new Map();       // target address string -> index
const pendingImports = [];      // { dll, name|ordinal } not resolvable yet
const seenModules = new Set();
let ranges = [];

function refreshRanges() {
  ranges = Process.enumerateModules().filter(m => GAME.indexOf(m.name.toLowerCase()) >= 0)
    .map(m => ({ name: m.name, base: m.base, lo: m.base.toUInt32(), hi: m.base.toUInt32() + m.size }));
  shared.add(SH_NR).writeU32(0);
  ranges.slice(0, NR).forEach((r, i) => { shared.add(SH_LO + 4 * i).writeU32(r.lo); shared.add(SH_HI + 4 * i).writeU32(r.hi); });
  shared.add(SH_NR).writeU32(Math.min(ranges.length, NR));
}

function where(a) {
  for (const r of ranges) if (a >= r.lo && a < r.hi) return r.name + '+0x' + (a - r.lo).toString(16);
  const m = Process.findModuleByAddress(ptr(a));
  return m ? m.name + '+0x' + ptr(a).sub(m.base).toString(16) : '0x' + (a >>> 0).toString(16);
}

// Import directory walk (as in iat_census.js: Frida's enumerateImports misses ordinal imports).
function importsOf(m) {
  const b = m.base, out = [];
  const nt = b.add(b.add(0x3c).readU32());
  const rva = nt.add(0x80).readU32();
  if (!rva) return out;
  for (let d = b.add(rva); d.add(12).readU32() !== 0; d = d.add(20)) {
    const dll = b.add(d.add(12).readU32()).readCString().toLowerCase();
    if (GAME.indexOf(dll) >= 0) continue;
    const ilt = d.readU32() || d.add(16).readU32();
    for (let t = b.add(ilt); t.readU32() !== 0; t = t.add(4)) {
      const v = t.readU32();
      if (v & 0x80000000) out.push({ dll, ord: v & 0xffff });
      else out.push({ dll, name: b.add(v + 2).readCString() });
    }
  }
  return out;
}

function hookImport(imp) {
  const name = imp.name || ('#' + imp.ord);
  if (imp.name && NOHOOK.has(imp.name)) return true;
  const m = Process.findModuleByName(imp.dll);
  if (!m) return false;
  const a = GetProcAddress(m.base, imp.name ? Memory.allocAnsiString(imp.name) : ptr(imp.ord));
  if (a.isNull()) return true;
  const key = a.toString();
  if (byAddr.has(key)) return true;
  const rng = Process.findRangeByAddress(a);
  if (!rng || rng.protection.indexOf('x') < 0) return true;   // data import (_iob, ...)
  const idx = fns.length;
  fns.push({ dll: imp.dll, name, addr: key, sig: SIG[imp.name] || null });
  byAddr.set(key, idx);
  const fd = Memory.alloc(8);
  fd.writePointer(shared);
  fd.add(4).writeU32(idx);
  fns[idx].fd = fd;
  try {
    Interceptor.attach(a, { onEnter: cm.onEnter, onLeave: cm.onLeave }, fd);
  } catch (e) {
    fns[idx].error = e.message;
  }
  return true;
}

function sweep() {
  refreshRanges();
  for (const m of Process.enumerateModules()) {
    const n = m.name.toLowerCase();
    if (GAME.indexOf(n) < 0 || seenModules.has(n + m.base)) continue;
    seenModules.add(n + m.base);
    if (n === 'language.dll') continue;   // resource module, no imports
    try { importsOf(m).forEach(imp => { if (!hookImport(imp)) pendingImports.push(imp); }); } catch (e) { log({ note: 'imports of ' + m.name + ': ' + e.message }); }
  }
  for (let i = pendingImports.length - 1; i >= 0; i--) if (hookImport(pendingImports[i])) pendingImports.splice(i, 1);
}

// ---- thread roles (TSThread routine, see code_census.js)
const ROLES = { 'Empire Earth.exe+0x1532fb': 'sim', 'Empire Earth.exe+0x10fe16': 'render', 'Empire Earth.exe+0xce947': 'asset',
  'Empire Earth.exe+0x1110fe': 'input', 'Empire Earth.exe+0x1270bf': 'music-intensity' };
const roles = new Map();
const ntdll = Process.getModuleByName('ntdll.dll');
const OpenThread = new NativeFunction(k32.getExportByName('OpenThread'), 'pointer', ['uint32', 'int', 'uint32'], 'stdcall');
const CloseHandle = new NativeFunction(k32.getExportByName('CloseHandle'), 'int', ['pointer'], 'stdcall');
const NtQIT = new NativeFunction(ntdll.getExportByName('NtQueryInformationThread'), 'uint32', ['pointer', 'uint32', 'pointer', 'uint32', 'pointer'], 'stdcall');
const startBuf = Memory.alloc(4);
// Threads not started through TSThread: name them by their Win32StartAddress (EXE entry = main).
function roleOf(tid) {
  let r = roles.get(tid);
  if (r !== undefined) return r;
  r = 'other';
  const h = OpenThread(0x40, 0, tid);
  if (!h.isNull()) {
    if (NtQIT(h, 9, startBuf, 4, NULL) === 0) {
      const s = where(startBuf.readU32());
      r = s.startsWith('Empire Earth.exe+') ? 'main' : 'start ' + s;
    }
    CloseHandle(h);
  }
  roles.set(tid, r);
  return r;
}
let roleHooked = false;
function hookRoles() {
  if (roleHooked) return;
  const eng = Process.findModuleByName('Low-Level Engine.dll');
  const a = eng && eng.findExportByName('?WIN32ThreadMain@TSThread@@CGKPAX@Z');
  if (!a) return;
  roleHooked = true;
  Interceptor.attach(a, { onEnter(args) {
    const r = where(args[0].add(0x28).readPointer().toUInt32());
    roles.set(this.threadId, ROLES[r] || ('TSThread ' + r));
  } });
}

// ---- decoding
function str(p, wide, max) {
  if (p.isNull()) return null;
  try { return wide ? p.readUtf16String(max || 260) : p.readAnsiString(max || 260); } catch (e) { return '<bad ' + p + '>'; }
}
function guid(p) {
  if (p.isNull()) return null;
  try {
    const b = new Uint8Array(p.readByteArray(16));
    const h = i => b[i].toString(16).padStart(2, '0');
    return [3, 2, 1, 0].map(h).join('') + '-' + [5, 4].map(h).join('') + '-' + [7, 6].map(h).join('') + '-' +
      [8, 9].map(h).join('') + '-' + [10, 11, 12, 13, 14, 15].map(h).join('');
  } catch (e) { return '<bad ' + p + '>'; }
}
function logfont(p) {
  if (p.isNull()) return null;
  try {
    const r = n => p.add(n).readS32();
    return { height: r(0), width: r(4), escapement: r(8), orientation: r(12), weight: r(16), italic: p.add(20).readU8(),
      underline: p.add(21).readU8(), strikeout: p.add(22).readU8(), charset: p.add(23).readU8(), out_precision: p.add(24).readU8(),
      clip_precision: p.add(25).readU8(), quality: p.add(26).readU8(), pitch_family: p.add(27).readU8(), face: p.add(28).readAnsiString(32) };
  } catch (e) { return '<bad ' + p + '>'; }
}
function rect(p) {
  if (p.isNull()) return null;
  try { return [p.readS32(), p.add(4).readS32(), p.add(8).readS32(), p.add(12).readS32()]; } catch (e) { return '<bad ' + p + '>'; }
}
const hex = v => '0x' + (v >>> 0).toString(16);
function decode(sig, esp) {
  const codes = sig.split(':')[0].replace(/ /g, '');
  const args = [];
  for (let i = 0; i < codes.length; i++) {
    const raw = ptr(esp).add(4 + 4 * i).readU32();
    const p = ptr(raw);
    switch (codes[i]) {
      case 's': args.push(str(p, false)); break;
      case 'w': args.push(str(p, true)); break;
      case 'n': { const len = ptr(esp).add(8 + 4 * i).readS32(); args.push(len > 0 && len < 4096 ? str(p, false, len) : hex(raw)); break; }
      case 'd': args.push(raw | 0); break;
      case 'b': args.push(raw !== 0); break;
      case 'g': args.push(guid(p)); break;
      case 'f': args.push(logfont(p)); break;
      case 'r': args.push(rect(p)); break;
      default: args.push(hex(raw));
    }
  }
  return args;
}

// ---- records
let seq = 0, buf = [], fileNo = 0;
const open = new Map();          // seq -> record awaiting its return
function flush() {
  if (!buf.length) return;
  send({ type: 'file', path: 'trace/' + String(fileNo++).padStart(5, '0') + '.json', json: buf });
  buf = [];
}
function log(rec) { rec.seq = ++seq; rec.t_ms = Date.now() - t0; buf.push(rec); if (buf.length >= P.chunk) flush(); return rec; }
function onCall(fn, esp, tid, caller) {
  const f = fns[fn];
  const rec = log({ tid, role: roleOf(tid), caller: where(caller), fn: f.dll.replace(/\.dll$/, '') + '!' + f.name });
  try { rec.args = f.sig ? decode(f.sig, esp) : [0, 1, 2, 3].map(i => hex(ptr(esp).add(4 + 4 * i).readU32())); }
  catch (e) { rec.args_error = e.message; }
  if (f.name === 'DirectDrawCreateEx' || f.name === 'CoCreateInstance' || f.name === 'DirectInputCreateEx' ||
      f.name === 'DirectInputCreateA' || f.name === 'DirectSoundCreate' || f.name === 'DirectDrawCreate') {
    const pos = { DirectDrawCreateEx: 1, CoCreateInstance: 4, DirectInputCreateEx: 3, DirectInputCreateA: 2, DirectSoundCreate: 1, DirectDrawCreate: 1 }[f.name];
    rec.out_ptr = ptr(esp).add(4 + 4 * pos).readPointer();
  }
  open.set(rec.seq, rec);
  return rec.seq;
}
function onReturn(s, eax, edx) {
  const rec = open.get(s);
  if (!rec) return;
  open.delete(s);
  const rcode = (SIG[rec.fn.split('!')[1]] || ':x').split(':')[1];
  rec.ret = rcode === 's' ? str(ptr(eax), false) : (rcode === 'd' ? (eax | 0) : (rcode === 'b' ? eax !== 0 : (rcode === '' ? undefined : hex(eax))));
  if (rec.out_ptr) {
    try { rec.out = hex(rec.out_ptr.readU32()); } catch (e) { }
    delete rec.out_ptr;
  }
}

function snapshot() {
  flush();
  const counts = [];
  const tab = shared.add(SH_TAB);
  for (let i = 0; i < NT; i++) {
    const c = tab.add(16 * i).add(8).readU32();
    if (!c) continue;
    const f = fns[tab.add(16 * i).readU32()];
    counts.push({ fn: f.dll.replace(/\.dll$/, '') + '!' + f.name, caller: where(tab.add(16 * i).add(4).readU32()), count: c });
  }
  counts.sort((a, b) => b.count - a.count);
  return { records: seq, files: fileNo, functions_hooked: fns.length, hook_errors: fns.filter(f => f.error).map(f => f.name + ': ' + f.error),
           pending_imports: pendingImports.map(i => i.dll + '!' + (i.name || '#' + i.ord)), threads: [...roles].map(([t, r]) => ({ tid: t, role: r })),
           call_sites: counts.length, counts, elapsed_ms: Date.now() - t0 };
}

function stop() {
  shared.add(SH_ENABLED).writeU32(0);
  flush();
  return {};
}

sweep();
hookRoles();
Process.attachModuleObserver({ onAdded(m) { if (GAME.indexOf(m.name.toLowerCase()) >= 0) { sweep(); hookRoles(); } } });
setInterval(() => { sweep(); hookRoles(); }, 500);
rpc.exports = { snapshot, stop };
