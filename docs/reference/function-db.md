# Function database (Phase 1)

Status: first complete build, 2026-10-14. Owner: agent func-db.
Files: `db/functions/<module>.json` (tracked, 14 MB in total), tools in [`tools/ghidra/`](../../tools/ghidra/).

The function DB is the authoritative list of what is code in the nine canonical GOG modules (PLAN §2.4):
- where every function starts and ends;
- what it is called (where any name exists);
- which library it comes from;
- how it can be reached (direct calls, vtables, EH tables, data pointers);
- what it does to the x87 FPU and to exception handling.

It replaces section flags and the kit's heuristic pointer scan as the input to translation (ADR-001 gap 2).

Labels: **[measured]** = output of the tools here, run on this Mac against the canonical files; **[static]** = read from the binaries by hand; **[inferred]** = reasoning. Addresses are given as module + RVA, with the preferred VA in brackets. Every number in this file is [measured] unless marked otherwise.

---

## 1. Headline numbers

| Module | Entries | Ghidra's own first pass | `.text` classified | Unknown bytes |
|---|---|---|---|---|
| Empire Earth.exe | 37,459 | 20,642 | **99.996 %** of 4,333,568 B | 0.004 % |
| Low-Level Engine.dll | 5,218 | 4,899 | **99.990 %** | 0.010 % |
| sxlrt233.dll | 3,350 | 2,970 | **99.999 %** | 0.001 % |
| Default.dll | 834 | 622 | **100.000 %** | 0 |
| DX7HRDisplay.dll | 673 | 515 | 99.896 % | 0 (0.104 % orphan code, see §8) |
| DX7HRTnLDisplay.dll | 715 | 540 | 99.899 % | 0 (0.101 % orphan code) |
| TCP Protocol.dll | 180 | 148 | **100.000 %** | 0 |
| Miles Sound System Mixer.dll | 123 | 95 | **100.000 %** | 0 |
| Language.dll | 0 | 0 | no code section (resources only) | — |

- **PLAN Phase 1 target (≥ 99.5 % of `.text` bytes) is met for every module.**
- "Classified" means the byte is in a function body, is data inside the code section (jump/index tables, constants), or is padding (`CC`/`90`/zero runs, and the zero-filled tail past the raw data).
- "Entries" are all entry points: functions plus import thunks plus MSVC EH pieces (stubs, unwind funclets, catch blocks and their continuations, SEH filters and handlers). Each is a separate entry the runtime can be asked to call. The EXE has 25,602 ordinary functions among its 37,459 entries (§3.3).
- **RTTI vtables:** every slot of every recovered vtable resolves to a function start in every module: EXE 23,800 / 23,800 slots, 6,654 / 6,654 distinct targets (§4).
- **Kit check:** the unmodified recomp-kit translator accepts the DB-driven EXE listings with no `--allow-unmodelled` or `--allow-table-gaps`. The spike needed both (§7.3).

---

## 2. How it is built

### 2.1 Pipeline

`tools/ghidra/regen.py` runs everything. Per module:

1. **`ghidra_run.py analyze`** (PyGhidra, Ghidra 12.1.4)
   - Imports the canonical file (hash-checked against `common.py`) at its **preferred image base** (0x400000 for the EXE, 0x10000000 for every DLL). At run time the DLLs are relocated, so DB addresses are RVAs plus the preferred base.
   - Applies `setup_program.py`, then runs full auto-analysis.
   - Repairs disassembly conflicts (`repair.py`) and re-analyses until clean.
   - Saves the project under `analysis/func-db/ghidra/<slug>/`.
2. **Seeding loop**, which converges in 2–6 rounds:
   - `ghidra_run.py export` writes `analysis/func-db/facts/<slug>.json.gz`.
   - `build_db.py --seeds-only` lists every **evidence-backed** start that Ghidra lacks (§2.3).
   - `ghidra_run.py seed` disassembles them, creates the functions, completes under-resolved jump tables, re-runs auto-analysis and repairs.
3. **`build_db.py`** decodes every function body with Capstone and computes the attributes (§3). `tagging.py` tags, recovers classes, checks DRM reachability and writes the DB.
4. **`build_db.py --summary`** writes `analysis/func-db/work/summary.json` and the tables in this file.

### 2.2 EXE-specific setup (`setup_program.py`)

The EXE needs two fixes before analysis:
- **Executable flags.** Every section is `0xc0000040` (initialised data, R/W, no execute) [static]. In the Ghidra program only, `.text` and `.cms_t` are marked executable. The file on disk is untouched, so no `mark_code.py` copy is needed for analysis.
- **Header overlap.** `SizeOfHeaders` is 0x1000, but `.text`'s raw data starts at file offset 0x600 [static]. Ghidra's `Headers` block therefore held a copy of `.text`'s first 0xA00 bytes at 0x400600–0x400fff, where the spike got 11 phantom functions. The block is cut to 0x400000–0x4005ff.

All modules use Ghidra's default analyzers, with these settings:
- Function ID **on** (the `vsOlder_x86` FID database);
- RTTI and PE exception-handling analyzers **on**;
- Aggressive Instruction Finder **off**: we add code only with evidence.

The options in force are recorded in `analysis/func-db/ghidra/<slug>/analyze.json`.

### 2.3 Evidence classes (what may become a function)

| Evidence | Meaning | Use |
|---|---|---|
| `vt` | slot of an RTTI-recovered vtable (`rtti.py`) | strong |
| `unwind`, `catch`, `catch_cont` | `FuncInfo` (magic 0x19930520) unwind actions, catch handlers, and the continuation each catch funclet returns (`MOV EAX, offset; RET`) | strong |
| `eh_stub` | the `MOV EAX, FuncInfo; JMP __CxxFrameHandler` stub a C++ EH frame pushes | strong |
| `seh_filter`, `seh_handler` | `_except_handler3` scope-table entries | strong |
| `call` | target of a direct `CALL rel32` from non-DRM code | strong |
| `export`, `entry` | export table, PE entry point | strong |
| `reloc` | (DLLs) a relocated dword in a data section names the address | needs Ghidra-decoded code or validation |
| `dp` | (EXE, no relocations) a 4-aligned dword in `.rdata`/`.data`/`.idata` names the address, outside Ghidra-defined strings | needs validation |
| `imm` | a `PUSH`/`MOV` immediate in non-DRM code names the address (callbacks, `atexit` handlers, thread procs) | needs validation |

**Validation** (`build_db.py validate_code`):
- the target starts at a function boundary: a section start, after padding or `RET`/`RET n`/`JMP`, or where another body ends;
- it does not start with padding;
- it decodes to a `RET`/`JMP` with none of the opcodes MSVC 6 never emits (`IN`, `LES`, `AAM`, segment overrides, …);
- it does not look like table data.

References *from* the DRM sections never count as evidence for game code. Without this rule the DRM's own pointers into `.text` (0x401001, 0x40110c, 0x7ff353, …) created a misaligned function and broke two real ones.

**Repairs** (`repair.py`, `seed_functions.py`). Ghidra sometimes lays down a code unit where a later correct flow needs to go:
- relocation-table pointer data inside not-yet-decoded `MOV [abs], imm32` instructions (DX7 plugins, Default.dll, the mixer);
- targets of DRM references and of numeric constants (EXE).

Each Error bookmark `conflicting data/instruction at B` is cleared when B has only data references, then re-disassembled and the function body re-derived. Every other plain data unit that the instruction at the flow target overlaps is cleared too, and the straight-line flow is followed past further such units. One `MOV [abs], imm32` holds two relocated dwords, and clearing only B left Default.dll+0x6c9a's initializer tail (+0x6caa, 20 bytes) as data, with no new bookmark; the DB was regenerated for Default.dll (Phase 2 workstream A). One jump table (EXE+0x3c985f [0x7c985f], `CMP EDI,3; JA; JMP [EDI*4+0x7c9b1c]`) was only half resolved by Ghidra. Its two missing cases are added as computed-jump flows.

### 2.4 Code vs data in the EXE

Coverage after convergence, `.text` = 4,333,568 B:

| Class | Bytes | % |
|---|---|---|
| function bodies | 4,239,044 | 97.819 |
| data in `.text` (jump tables, byte index tables, Ghidra-defined constants) | 13,208 | 0.305 |
| padding (`CC`, `90`, zero runs ≥ 4, zero-filled tail) | 81,126 | 1.872 |
| orphan instructions (decoded, in no function) | 14 | 0.000 |
| unknown | 176 | 0.004 |

Exact per-section numbers are in each DB's `stats.coverage`. The remaining unknown bytes are 76 runs of 1–4 bytes between functions; `stats.coverage.<section>.largest_unknown` lists them. For comparison, `.cms_t` (DRM, not translated) is 99.11 % classified.
- **Switch tables:** all 233 `JMP [reg*4+table]` sites in the EXE are resolved: 232 by Ghidra and 1 completed by the DB.
  - The three that stopped the spike's translator are resolved: EXE+0x55aa9 [0x455aa9], the stack-reloaded index, 9 entries; EXE+0x326b58 [0x726b58], byte-indexed, 6 targets; and the DRM one at 0x94e17a.
  - Each function's tables are in its `switch` field, and the table bytes in `data_in_code`.
- **QA** (`stats.qa` in `analysis/func-db/work/stats-<slug>.json`):
  - 0 function entries inside another function's instruction;
  - 0 overlapping bodies;
  - 0 functions outside `.cms_t` containing opcodes MSVC 6 never emits, or undecodable bytes.
  - In the engine this check found 3.1 KB of 16-byte records with relocated GUID pointers in `.text` (Low-Level Engine.dll+0x819b0). They are probably the DirectInput `c_dfDIKeyboard`/`c_dfDIMouse` data formats linked from `dinput.lib` [inferred]. A relocation-only seed had made them a "function"; they are now data, because unknown runs dense in relocation slots are classified as tables.

---

## 3. Schema (`neoee-funcdb/1`)

One JSON document per module. Top-level arrays hold one record per line, so diffs are line-based. Addresses are hex strings; `rva` is relative to the image base and `va` uses the preferred base. **No bytes, no code, no strings** from the game: only addresses, sizes, names (export/import/RTTI/FID/community), demangled signatures, flags and counts.

### 3.1 Top level

| Key | Content |
|---|---|
| `schema` | `"neoee-funcdb/1"` |
| `module` | `name`, `sha256`, `image_base` (preferred), `size_of_image`, `entry_rva`, `size_of_headers`, `sections[]` (`name`, `rva`, `vsize`, `raw_size`, `flags`, `code`: our classification, not the flags) |
| `generator` | tool and Ghidra version |
| `stats` | `functions`, `kinds`, `tags`, `coverage` (per code section: bytes, class counts, %, largest unknown runs), `classes`, `vtables`, `vtable_slots`, `vtable_slot_coverage`, `x87_functions`, `eh`, `indirect_targets`, `seeded`, `switch_tables`, `community` |
| `imports[]` | `slot_rva` (IAT slot), `dll`, `name`, `demangled`. Function records refer to imports by index. |
| `exports[]` | `ordinal`, `name`, `rva`, `demangled` |
| `classes[]` | RTTI classes (§4). Function records refer to classes by index. |
| `data_in_code[]` | `[rva, size, type]`: data inside code sections (Ghidra data items and our tables) |
| `drm_reachability` | EXE only: the evidence in §6 |
| `functions[]` | one record per entry point, sorted by address |

### 3.2 Function record

Fields are omitted when empty, false or zero, which keeps the EXE at 9.2 MB.

| Field | Meaning |
|---|---|
| `rva`, `va`, `size` | entry and body size in bytes (sum of chunks) |
| `chunks` | `[[rva_start, rva_end), …]` when the body is not one contiguous range starting at the entry |
| `sec` | section of the entry |
| `name`, `name_src` | best name and where it came from: `export` (demangled), `import_thunk` (demangled import), `fid` (Ghidra Function ID), `community` (`db/community-symbols.json`), `ghidra` (Ghidra's EH analyzer names, e.g. `Unwind@…` in sxlrt233) |
| `mangled`, `sig`, `cc`, `cc_src` | MSVC-mangled name, full demangled signature, calling convention (`demangled` from the mangled name, or `ghidra`) |
| `kind` | `function`, `thunk` (single `JMP rel32`), `import_thunk` (`JMP [IAT]`), `eh_stub`, `eh_unwind`, `eh_catch`, `eh_continuation`, `seh_filter`, `seh_handler` |
| `parent` | for EH pieces: the function whose frame they belong to |
| `tag`, `tag_ev` | library/DRM tag and the evidence for it (§5) |
| `insns` | instruction count |
| `x87` | `{class: count}`: `ldst` (FLD/FST/FSTP), `int_load` (FILD/FBLD), `int_store` (FIST/FISTP/FBSTP: depend on RC), `arith`, `sign` (FCHS/FABS), `sqrt`, `transcendental` (FSIN/FCOS/FSINCOS/FPTAN/FPATAN/F2XM1/FYL2X/FYL2XP1), `misc_math` (FPREM/FSCALE/FXTRACT/FRNDINT), `compare`, `status_word` (FNSTSW), `const` (FLD1/FLDZ), `const_ext` (FLDPI and friends: 64-bit-significand constants), `control_word` (FLDCW/FNSTCW), `env`, `stack`, `cmov`, `wait` |
| `x87_m80`, `simd` | count of 80-bit memory operands; count of MMX/SSE/CPUID instructions |
| `eh` | frames the function establishes: `cxx` (`__CxxFrameHandler` via a FuncInfo stub), `seh3` (`_except_handler3`), `seh_other` |
| `throws` | `_CxxThrowException` call sites |
| `callers`, `call_sites`, `callees` | distinct direct callers (calls and tail jumps), direct call sites, distinct direct callees |
| `imports` | indices into `imports[]` the function calls (directly or through a thunk) |
| `icalls`, `vcalls` | indirect calls through a register or a global pointer / through `[reg+disp]` (virtual-call shape) |
| `xref`, `indirect` | evidence that the entry is reached indirectly: `vt`, `dp`, `reloc`, `imm`, `export`, `entry`, `unwind`, `catch`, `catch_cont`, `eh_stub`, `seh_*` with counts. `indirect` = any of them. |
| `vt_slots`, `vt_slots_total`, `class` | `[class index, slot, (vptr offset)]` for up to 8 vtable slots naming the function; the owning class (the class among those whose vtables hold it that is a base of all the others) |
| `vt_writes` | class indices whose vtable the function stores into an object (constructor/destructor evidence) |
| `noreturn`, `ret_pop` | Ghidra no-return flag; bytes popped by `RET n` (a list if several values) |
| `ecx_in`, `edx_in` | ECX/EDX read before written in the entry block (`__thiscall`/`__fastcall` evidence) [inferred] |
| `alt_entries` | `{evidence: count}` for interior addresses that data or EH tables name (alternate entries) |
| `alt_vas` | the same interior addresses: `{va: [evidence]}`. The kit seeds each as an alternate entry |
| `switch` | `[[table_rva, entries, resolved_by], …]` |
| `community` | `[{name, status, source}]` from `db/community-symbols.json`, with provenance |
| `seeded` | created by our seeding, not by Ghidra's own analysis |

### 3.3 Entry kinds

| Kind | EXE | engine | sxlrt233 | Default | DX7HR | DX7HRTnL | TCP | Miles mixer |
|---|---|---|---|---|---|---|---|---|
| function | 25,602 | 5,180 | 1,773 | 824 | 658 | 700 | 169 | 112 |
| thunk | 82 | 16 | — | 2 | — | — | — | 1 |
| import_thunk | 521 | 22 | 34 | 8 | 15 | 15 | 11 | 10 |
| eh_stub | 2,395 | — | 374 | — | — | — | — | — |
| eh_unwind | 8,777 | — | 1,166 | — | — | — | — | — |
| eh_catch / eh_continuation | 47 / 25 | — | — | — | — | — | — | — |
| seh_filter / seh_handler | 3 / 7 | — | 0 / 3 | — | — | — | — | — |

---

## 4. Classes recovered (RTTI)

Ghidra's RTTI analyzer finds **nothing** in the EXE [measured]: there are no relocations and every section is writable. `rtti.py` therefore parses MSVC 6 RTTI straight from the image:
- TypeDescriptors (`.?AV…@@`, sharing one `type_info` vftable pointer);
- CompleteObjectLocators;
- ClassHierarchyDescriptors and BaseClassDescriptors;
- vtables, found as the dword after each `&COL`. A vtable's slots run until a non-code dword or the next COL.

Names are demangled with Ghidra's Microsoft demangler.

| Module | Classes (TDs) | with vtables | vtables | slots | distinct targets | targets that are function starts |
|---|---|---|---|---|---|---|
| Empire Earth.exe | 1,879 | 1,829 | 1,842 | 23,800 | 6,654 | **6,654** (2,791 of them seeded; Ghidra alone found 3,863) |
| Low-Level Engine.dll | 143 | 142 | 142 | 2,076 | 680 | **680** (108 seeded) |
| sxlrt233.dll | 74 | 74 | 74 | 345 | 237 | **237** |
| DX7HRDisplay.dll | 23 | 16 | 16 | 199 | 192 | **192** |
| DX7HRTnLDisplay.dll | 24 | 17 | 17 | 281 | 196 | **196** |
| Default.dll | 14 | 13 | 13 | 68 | 37 | **37** |
| TCP Protocol.dll | 7 | 7 | 7 | 13 | 10 | **10** |
| Miles Sound System Mixer.dll | 9 | 6 | 6 | 12 | 9 | **9** |

Notes:
- **Agreement with com-inventory.** com-inventory counted 6,669 EXE methods reachable only via vtables, 2,853 of them missing from the spike export. We find 6,654 distinct targets, 2,791 of which Ghidra did not create by itself. The 15-target difference is presumably in how vtable ends are detected: ours stop at the next COL or the first non-code dword, and we checked that the 145 vtables followed by float constants then a COL end correctly.
- **Engine cross-check.** All 124 exported `??_7…@@6B@` vftables in the engine are among our 142 RTTI vtables. 18 more have no export.
- **EXE class families:**
  - `EE*` game classes, about 1,160;
  - `WONAPI::` 583;
  - `RTS*` 19 (the SSSI RTS framework);
  - engine-template instantiations (`UI*`, `UMemoryPool<…>`, `UListNode<…>`);
  - `std::exception`, `logic_error`, `length_error`, `out_of_range`;
  - `type_info`.
- **Hierarchy.** 1,619 EXE classes have base-class lists (`bases[]` with `mdisp/pdisp/vdisp`). 13 use multiple or virtual inheritance.
- **Vtable slots.** Each class's `vtables[]` lists every slot target (RVA). 6,654 EXE functions carry an owning `class`, and 3,111 store a vtable pointer (`vt_writes`, constructor/destructor evidence).
- **Not run:** Ghidra's `RecoverClassesFromRTTIScript`. It depends on the RTTI analyzer, which fails on the EXE, and the DB already holds the hierarchy and slot map. Running it later on the engine for struct layouts is optional.

---

## 5. Library and DRM tagging

### 5.1 Rules (`tagging.py`, first match wins; evidence in `tag_ev`)

1. **Anchors.**
   - `section`: everything in `.cms_t` is `drm`.
   - `module`: everything in sxlrt233 is `lib:dinkumware`, except the static-CRT helpers below.
   - `crt_*` shapes: EH vector constructor/destructor iterators, `__unwindfilter` (`CMP …,0xE06D7363`), `_setdefaultprecision` (`_controlfp(0x10000, 0x30000)`, EXE+0x283914 [0x683914]; legion's start-up `_controlfp` return site EXE+0x283923 lies inside it), `_chkstk`.
   - `jmp_iat`: import thunks are `lib:import`.
   - `fid`: a Function ID match with a runtime-style name (`_…` or `` `…' ``) → `lib:crt-static`. Others, such as the FID match `CRect::CRect` at EXE+0x334520, which is almost certainly a coincidental four-store constructor, keep the name but stay `game`.
   - `entry`: the module entry is `lib:crt-static`.
   - `name_std`: `std::` names are `lib:dinkumware`.
   - `vtable` and `vtable_write`: functions in vtables of `WONAPI::`, `std::`, `type_info` or `CTraceClipboardData` classes, and functions that store only such vtables.
   - `string`: references to strings of one family that make up at least half of the strings the function names, directly or through a pointer table. Families:
     - zlib 1.1.3 messages;
     - libpng messages;
     - IJG libjpeg 6b messages;
     - PKWARE DCL banner;
     - MFC `AfxDumpStack`;
     - WON status strings (`WS_*`);
     - Dinkumware `vector<T> too long`, `invalid map/set<T> iterator`, …;
     - DRM `SIntf*`/`CmdLineExt`/Petite.
   - `constants`: IJG fixed-point IDCT and YCbCr constants; zlib Adler-32 `65521`/`5552`.
2. **`region`**: gaps of up to 24 functions between two anchors of the same C library are filled. Object files are linked contiguously.
3. **`callers`**: an untagged function whose direct callers all carry one tag gets it. This never propagates from `game`, `lib:import`, `lib:crt-static`, `lib:dinkumware` or `drm`: the CRT calls `WinMain`, templates call game destructors, and the DRM calls the original entry point.
4. **`neighbors`**: sandwiched within 12 functions between two functions with the same propagating tag, and not called from code with another tag.
5. **`parent`**: EH stubs and funclets take their parent's tag. Everything else is `game`.

### 5.2 Breakdown

| Module | game | lib:won | lib:dinkumware | drm | lib:libpng | lib:zlib | lib:jpeg | lib:pkware-dcl | lib:mfc | lib:crt-static | lib:import |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Empire Earth.exe: entries | 21,162 | 13,800 | 1,226 | 572 | 118 | 20 | 11 | — | 15 | 17 | 518 |
| Empire Earth.exe: bytes | 2,882,093 (65.3 %) | **1,046,262 (23.7 %)** | 246,836 (5.6 %) | 174,058 (3.9 %) | 41,457 | 14,440 | 1,842 | — | 2,060 | 946 | 3,108 |
| Low-Level Engine.dll | 5,119 | — | 33 | — | — | — | 32 | 5 | — | 7 | 22 |
| sxlrt233.dll | — | — | 3,347 | — | — | — | — | — | — | 3 | — |
| plugins (Default, DX7HR, DX7HRTnL, TCP, Miles) | 808 / 647 / 689 / 158 / 102 | — | 15 / 8 / 8 / 8 / 8 | — | — | — | — | — | — | 3 each | 8 / 15 / 15 / 11 / 10 |

What this tells us:
- **WON** is about a quarter of the EXE's code (5,045 functions plus 8,755 EH pieces).
  - It sits in EXE+0x270000–0x3f0000, mixed with EE's own lobby code.
  - EE's lobby code (`EEWonNet*`, lobby UI) is tagged `game`, although it is dead for LAN too.
  - WON also owns the only game-side `FSIN`/`FCOS` (EXE+0x3baae0 [0x7baae0], a WON GUI drawing routine): not simulation code.
- **The image libraries** (libpng, zlib 1.1.3, libjpeg 6b) sit together at EXE+0x3f1000–0x401a00, interleaved with WON functions.
  - They are probably used only by WON's GUI image classes, i.e. lobby-only [inferred: link position and caller propagation; not proven].
  - The engine has its own libjpeg (image decoding [inferred]) and the PKWARE DCL 1.11 that `.ssa` archives use.
- **MFC** is only `AfxDumpStack` plus `CTraceClipboardData` (10 functions, EXE+0x2a2320 region): debug helpers compiled in.
- **lib:crt-static** is small because everything links msvcrt.dll dynamically. The EXE's static CRT is:
  - `WinMainCRTStartup` (EXE+0x283798);
  - `_setdefaultprecision`;
  - the EH vector iterators;
  - `_chkstk`;
  - `__allmul`/`__allshr` (FID);
  - `__unwindfilter`, plus a few start-up helpers found by their msvcrt imports.
- **Lower bounds.** C-library tags (zlib, libpng, jpeg, pkware-dcl) are lower bounds. Library code reached only through function-pointer tables (IJG's method tables) is tagged where a constant or region rule catches it, otherwise `game`.

---

## 6. DRM (`.cms_t`/`.cms_d`): reachability verdict

**Verdict: unreachable [static]. Nothing outside the DRM sections can transfer control into them.** The evidence is in `drm_reachability` in `empire-earth.exe.json`:

| Check | Result |
|---|---|
| PE entry point | EXE+0x283798 [0x683798] in `.text`: the CRT `WinMainCRTStartup`. No TLS callbacks, no load-config. |
| Direct calls/jumps from non-DRM code into DRM | 0 |
| Instruction operands of non-DRM code naming a DRM address | 1: EXE+0x2968c1 `MOV ECX, 0x9a90e7` is a size constant (`SUB ECX,[ESI+8]; CMP ECX,EBP`), not an address |
| Aligned dwords in `.rdata`/`.data`/`.idata` naming a DRM address | 6. Each is coincidental: X11 colour-table RGB values (`0x9932cc` darkorchid, `0x98fb98`, `0x9acd32`), import hint/name bytes (`…\0 95 00`), a 16-bit lookup table. 0 in `.text` data. (`.rsrc` bitmaps excluded.) |
| Ghidra references into DRM from outside | only the PE section table (`Headers`) and the two coincidences above |

**What the DRM residue is [static]:**
- **Code.** `.cms_t` holds 572 functions (174 KB): the protected retail build's entry stub, with its own statically linked MSVC CRT (`__ftol`, `__except_handler3`, `memset`; 33 FID hits).
  - Six small DRM routines name `.text` range bounds (0x401000–0x4020ab, 0x403000–0x404121, 0x432101–0x432202, 0x442103–0x442204), presumably the regions it decrypted or checked [inferred].
  - The main routine (EXE+0x552080 [0x952080], 6.2 KB) ends with a computed call to the original entry point **0x683798** (EXE+0x5534ec). GOG's PE header now points there directly.
- **Data.** `.cms_d` holds encoded strings, `SIntf32.dll`/`SIntf16.dll`/`CmdLineExt.dll` names, and two Petite-packed PE images (SafeDisc helper DLLs).
- **Imports.** The DRM calls its APIs through a frozen IAT in `.idata` (0x9f3c98–0x9f582c). It holds **absolute Windows XP-era addresses** (`kernel32` 0x77e4xxxx, `user32` 0x77d1xxxx, `advapi32` 0x77daxxxx). The table is a dump of the running protected game: no import descriptor binds those slots, so the code would crash on any modern Windows if it ever ran [inferred from [static]].
- **`version.dll`** (`GetFileVersionInfoA`, `GetFileVersionInfoSizeA`, `VerQueryValueA`) is the one GOG import descriptor that lives in `.idata`, and **only DRM code calls it** [measured, DB `imports_used_only_by_drm`]. The HLE layer can stub it.

Consequences:
- Translation must exclude `.cms_t`/`.cms_d` (the kit exporter does: `excluded_functions`, `excluded_ranges`).
- DRM pointers into `.text` must never be used as code evidence, which the DB enforces (§2.3).
- On legion the census script in §9 can confirm at run time that no block in `.cms_t` ever executes.

---

## 7. Kit integration (ADR-001 gap 2)

### 7.1 How the kit consumes listings (fork at `eb32cc4`)

`kit/tools/recomp/translate.py` reads:
- `functions.tsv`: address, name, bytes, …;
- one `functions/<addr>.asm` per row, with lines `%08x  <Ghidra instruction text>`. These are parsed by `LINE_RE`, `translate.py:635`, and loaded by `load_functions`, `:3981`.

It then grows the set with its own discovery loop (`:4923–5060`):
- direct branch targets;
- `__initterm` tables;
- `PUSH`/`MOV` immediates;
- a byte-wise **data-pointer scan** (`Image.code_pointers`, `:1246`) gated by `function_alignment`.

Code is classified by **section flags** (`Image.__init__ :874`, `is_exec :919`). Jump tables are decoded from the listing (`decode_jumptable :2187`), and undecodable ones are fatal without `--allow-table-gaps` (`:5591`).

### 7.2 What we provide: `ghidra_run.py kit` (`kit_export.py`)

`ghidra_run.py kit <slug> db/functions/<module>.json <outdir>` writes, from the saved Ghidra project, exactly the DB's non-DRM entries:
- `functions.tsv` and `functions/<addr>.asm` in the kit's format. Optional `--decompile` adds `.c` pseudocode.
- Function names: the DB's best name (`name`), else `fn_<va>`. They name the translated functions in the kit's `symbols.json` and profiler.
- `funcdb.json` (schema `recomp-funcdb/1`, the kit's `[translate] function_db` input), a sidecar that carries what the kit needs from the DB beyond the listings:
  - `code_ranges`;
  - `excluded_ranges` (DRM sections, headers);
  - `entry_points` with kinds and evidence;
  - `alternate_entries` (the `alt_vas` addresses with their evidence);
  - `data_in_code`;
  - `jump_tables`;
  - `excluded_functions`.

The output must stay in an ignored directory (`analysis/`), because listings are derived from game code.

### 7.3 Test with the unmodified kit [measured]

Setup:
- EXE listings from the DB;
- the spike's `mark_code` copy of the EXE, so that the kit's section-flag test passes;
- `function_alignment = 16`.

Command: `translate.py --game analysis/func-db/kit-test/exe --out gen --report report.json`, **with no allowance flags**.

| | Spike (Ghidra default listings) | DB listings |
|---|---|---|
| translated functions | 36,849 / 36,863 | **36,888 / 36,888** (36,887 DB entries + the padding block) |
| failures | 0 | 0 |
| jump-table gaps / undecoded sites | 3 (needed `--allow-table-gaps`) | **0 / 0** |
| unmodelled instructions (data decoded as code) | 145 (needed `--allow-unmodelled`) | **0** |
| recovery errors | 6 | **0** |
| entries invented by the data-pointer scan | 13,343 | **0** (+22 from immediates) |
| blocks recovered from the PE | 16,036 | **1** (`0x7eab2a`: 6 bytes of NOP padding before a function, which a stray EXE dword points at) |
| alternate entries from data pointers | n/a | 8,295 (interior instruction boundaries that random EXE data happens to hit; harmless noise) |
| time | 71.5 s | 37.5 s |

### 7.4 Kit changes needed so that the DB is authoritative

**Status: implemented** on the kit branch `feat/game-repo` (Phase 2 workstream A): `translate.py` `FunctionDB`, `[translate] function_db`, `[modules.aux.<key>] function_db`. Items 1–6 below are done as described; item 7 waits for gap 5. Measured results and the base map: [build.md](build.md).

These are small and local to `translate.py`/`game_config.py`, and the order matters:
1. **`[translate] function_db = "db/functions/<module>.json"`** (or the `funcdb.json` sidecar) in `game_config.load` (`game_config.py:143`).
2. **Code classification.** In `Image.__init__` (`translate.py:869–877`), take `exec_ranges` from the DB's `code_ranges` minus `excluded_ranges` instead of `Characteristics & 0x20000000`. This removes `tools/mark_code.py` and the header filter.
3. **Turn off the speculative scan when a DB is given.** Skip `Image.code_pointers` and the immediate/relocation candidate passes in the `scanned_pointers` block (`:4923–5060`). Seed `EXTRA_ENTRY_POINTS` (`:4838`) from the DB's `entry_points` and `alternate_entries` instead.
   - Keep branch following and `__initterm` (cheap and exact).
   - Report, rather than adopt, any address the DB lacks; a non-zero count is a DB bug.
   - This removes the 8,295 noise alternate entries and makes the image's entry set exactly the DB's.
4. **Tables.** Add `data_in_code` ranges to `tr.table_ranges` (`:4891`, `:5404`) so no scan or recovery reads them as code. Optionally let `decode_jumptable` (`:2187`) take the DB's `switch` entry count when its own dataflow fails.
5. **Exclusions.** Never translate `excluded_functions` (DRM). The kit's run-time discovery (`RECOMP_DISCOVERY`) plus a strict trap (gap 3) then catches anything the DB missed.
6. **DLLs.** With the scan off, `function_alignment` no longer matters. The spike's two silent misses at alignment 4 cannot recur.
7. **Later:** feed `kind`/`parent` into the MSVC EH runtime (gap 5), so funclets are registered as EH entries rather than ordinary functions.

Estimated effort: 2–3 days, inside gap 2's 1–2 week budget [inferred].

---

## 8. Other findings

- **x87 [measured, static instruction census].**
  - No game module executes `FLDCW`/`FNSTCW`, transcendental instructions (beyond WON's one `FSIN`/`FCOS` routine), `FLDPI`-class constants or 80-bit memory operands. In the EXE all of these exist only in `.cms_t` (the DRM's CRT: `__ftol`, `__math_exit`, …).
  - No MMX/SSE/CPUID anywhere.
  - **Precision and rounding changes therefore only happen through `msvcrt!_controlfp`**, from CRT start-up and the engine's `UMSetFloatingPointPrecision`. Legion's rare `0x0e7f` main-thread sample (PLAN §2.3) must come from a system DLL or msvcrt, not from game code.
  - x87 users: EXE 2,525 functions, of which 364 use `FIST(P)` (RC-dependent) and 1,003 use `FNSTSW` compares; engine 736; Default.dll 76; display plugins 76/77.
- **C++ EH.**
  - EXE: 2,395 functions with `__CxxFrameHandler` frames, 8,777 unwind funclets, 47 catch blocks (25 continuations), and 131 `_CxxThrowException` call sites in 83 functions.
  - EXE SEH: 6 `_except_handler3` frames. Four are static CRT (`WinMainCRTStartup` and three EH-vector iterators); two are in the AfxDumpStack debug-helper code (EXE+0x2a2920, EXE+0x2a3b30).
  - sxlrt233: 374 C++ frames, no throw sites (it throws through msvcp60).
  - The engine, Default.dll and every plugin have **no** EH frames.
- **Indirect targets.** EXE: 20,824 entries have indirect-call evidence; 20,190 have no direct caller at all.
- **Community symbols.** 78 of 80 EXE entries match a DB function start and are imported with provenance. The two misses are interior labels: `RenderThread_Loop` EXE+0x10fe42 and a `WM_ACTIVATEAPP` handler EXE+0x262656. Engine 46/46, DX7 plugins 3/3 and 6/6, TCP 2/2.
- **Display plugins.** 0.10 % of `.text` is still "orphan" code that Ghidra decoded but no evidence makes a function: probably dead tails after no-return calls. It is listed in `stats.coverage`.

---

## 9. Regenerate

```sh
# once: venv with PyGhidra (wheels ship inside Ghidra), pefile, capstone
uv venv --python 3.12 analysis/func-db/.venv
D=/opt/homebrew/Cellar/ghidra/12.1.4/libexec/Ghidra/Features/PyGhidra/pypkg/dist
uv pip install --python analysis/func-db/.venv/bin/python -r tools/requirements.txt \
    $D/jpype1-1.5.2-cp312-cp312-macosx_10_9_universal2.whl $D/pyghidra-3.1.0-py3-none-any.whl \
    $D/packaging-25.0-py3-none-any.whl

# everything (about 20 min on an M4 Max, mostly the EXE's seeding rounds; about 0.5 GB in analysis/func-db/)
GHIDRA_MAXMEM=8G analysis/func-db/.venv/bin/python tools/ghidra/regen.py

# rebuild the DB from existing facts only (seconds)
analysis/func-db/.venv/bin/python -I tools/ghidra/regen.py --db-only

# kit listings for one module (ignored output)
analysis/func-db/.venv/bin/python tools/ghidra/ghidra_run.py kit exe db/functions/empire-earth.exe.json analysis/func-db/kit/exe
```

Environment: `EE_GAME_DIR` overrides the game folder (default `original/gog/Empire Earth`); `GHIDRA_HOME` and `JAVA_HOME` override the Homebrew paths.

Reproducibility [measured]: two complete regenerations from scratch produced identical function sets in all nine modules (same entries, sizes, chunks and kinds). The only differences were two tags, caused by an intended rule change between the runs.

**On legion (optional, not run yet).** `tools/ghidra/legion/code_census.js` follows every thread with Frida Stalker (non-game modules excluded) and records each executed basic block as module, RVA and length. `check_census.py` checks the blocks against the DB:
- every block lies in a DB function;
- none lies in `.cms_t`;
- it reports how many functions of each tag ran.

```sh
# from this Mac (legion is reserved for the user's sessions; run when convenient)
scp tools/ghidra/legion/code_census.js legion:C:/EEPort/frida/
ssh legion "C:\EEPort\tools\venv\Scripts\python.exe C:\EEPort\frida\run.py --scripts code_census --seconds 600 --out C:\EEPort\captures\census"
#   (attach at the main menu, then start a skirmish within the 10 minutes; expect a slow first minute)
scp -r legion:C:/EEPort/captures/census analysis/func-db/census
python3 -I tools/ghidra/legion/check_census.py analysis/func-db/census --json analysis/func-db/census/report.json
```

---

## 10. Known limitations

- **Names.** Most EXE functions have no name (36,824 of 37,459): there are no symbols, and RTTI gives classes, not method names. Named are 521 import thunks, 36 FID matches and 78 community symbols. Classes and vtable slots give structure (`class`, `vt_slots`, `vt_writes`), but no synthetic names are invented.
- **Calling conventions** are known for named functions (demangled or Ghidra) only. For the rest, `ret_pop` and `ecx_in`/`edx_in` are heuristics.
- **Tags.**
  - C-library tags are lower bounds (§5.2).
  - `lib:won` includes code that WON's classes reach through caller propagation, but EE's own lobby code stays `game`.
  - The `neighbors` and `callers` rules are heuristic, and `tag_ev` says which rule fired.
- **Indirect calls** are counted, not resolved: no per-site target sets. The COM-slot inventory belongs to com-inventory.
- **Thread roles (sim/render), from the PLAN Phase 1 bullet, are not in the DB yet.** Static reachability from the thread entry points needs virtual-call resolution to be meaningful. The legion census, run per thread, is the better source.
- **Language.dll** has no code; its DB is a stub.
- **Toolchain pinning.** The DB depends on Ghidra 12.1.4's analysis. A different Ghidra version may find a different first pass; the seeding converges to the same evidence-backed set in practice, but this has not been tested across versions.
