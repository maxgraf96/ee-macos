# 002: Exact x87: design of the fork's floating-point layer
Status: accepted (design; the acceptance criteria below gate the Phase 2 implementation)
Date: 2026-10-14

This is the exact-x87 design note that ADR-001 makes a Phase 2 gate (gap 6), and that PLAN §5.2 asks for.

Claims are labelled:
- **[measured]**: a run on this Mac (M4 Max) or on `legion`;
- **[static]**: read from the GOG binaries (capstone over Ghidra-listed function bodies, plus a linear sweep for anything Ghidra missed);
- **[inferred]**: reasoning or literature, not yet checked.

Addresses are given as module + RVA. `EXE` means `Empire Earth.exe` (base 0x400000) and `LLE` means `Low-Level Engine.dll` (preferred base 0x10000000).

## Context

### Requirement
Lockstep LAN play between Mac and unmodified Windows GOG peers needs every value that feeds the simulation to be bit-identical with real x87 hardware (PLAN D3, §5.2).

`legion` measured these facts (docs/reference/legion.md §5.4 and §6):
- Every game thread runs at control word (CW) 0x027F: PC = 53 bits, RC = nearest, all exceptions masked.
- The render thread runs at 0x007F (PC = 24 bits). The game sets this itself through `UMSetFloatingPointPrecision`.
- The CW never changes in game. The simulation runs on the TSThread whose start code contains EXE+0x15332b, at about 41 ticks/s.
- Simulation maths per second: msvcrt `sqrt` about 2.9k, `UMArcTan2` 1.5k, `UMArcCos` 630, `UMCos` 130, and some `fabs`.

### What the code actually does
The census tool is `tests/x87/census.py`. Its outputs are in `analysis/sync-math/census/`, which is git-ignored.

#### 1. Engine maths entry points [static]
All of them are cdecl and take `float` arguments. They return the result in ST(0).

| Export (LLE RVA) | What it computes | Result in ST(0) | CRT calls | Call sites |
|---|---|---|---|---|
| `UMCos` (0x7df28) | Adds or subtracts 2π (a float constant) until the angle is in [−π, π]. Then takes the absolute value and computes `i = FISTP(float(|x|·5215.189f))` with round-half-even. Returns `cos_table[i]`: 16,385 floats at RVA 0xd9130. | an exact float | none | EXE 42, Default 4 |
| `UMArcCos` (0x7dcc4) | **0 < x < 0.9995f:** `acos_table[FISTP(float(x·32768f))]`, 32,760 floats at RVA 0xe9134.<br>**0.9995f ≤ x < 1:** `t = sqrt((double)(1−x²))/x`, then t − t³/3 + t⁵/5 − t⁷/7. The intermediates are partly rounded to float, and the constants are floats.<br>**x ≥ 1:** 0.<br>**x == 0:** π/2 (float).<br>**x < 0:** π − UMArcCos(−max(x, −1)).<br>**NaN:** π, because unordered compares follow the SAHF flags. | table: a float; series: 53-bit | `sqrt` | EXE 30 |
| `UMArcTan2(y, x)` (0x7debb) | **x == 0:** ±π/2 by the sign of y.<br>**Otherwise:** `sign(y) · UMArcCos(float(x / sqrt((double)(y²+x²))))`. | as UMArcCos | `sqrt` | EXE 67, Default 1 |
| `UMArcTan` (0x7dda4) | **0 < x < 1:** the same odd series.<br>**\|x\| > 1:** ±π/2 ∓ UMArcTan(1/x), guarded by `_finite`.<br>**x == 1:** π/4. | 53-bit or float | `_finite` | EXE 2 |
| `UMTan` (0x7e08b) | `UMCos(π/2 − x) / UMCos(x)`. If cos is 0, returns ±FLT_MAX. | 53-bit | none | EXE 6 |
| `UMQuadraticEquation` (0x7dfd0) | `(±sqrt(b²−4ac) − b) / float(2a)`, stored as floats. The a == 0 case is handled. | bool in AL | `sqrt` | EXE 1 |
| `UMFastMath::Sqrt` (0x194e) | `sqrt((double)f)`, left at 53 bits. `BuildSqrtTable` (0x883e6) is an empty `RET` folded with 36 other symbols. | 53-bit | `sqrt` | (via engine) |
| `UMSet/GetFloatingPoint{Precision,RoundingMode}` (0x7e067, 0x7e079, 0x7df8d, 0x7dfbe) | `_controlfp(p, _MCW_PC)`; `_controlfp(r, _MCW_RC)`. The getters map `_controlfp(0,0)` to `_PC_24/53/64` and to `& _MCW_RC`. | n/a | `_controlfp` | EXE 23 / 18 / 18 / 18 |

Points that follow from the table:
- **No `UMSin`, `UMArcSin` or `UMSqrt` exists** [static].
- **No UM function uses a transcendental instruction.** They use lookup tables, float-rounded polynomials and msvcrt `sqrt` [static].
- The tables are static `.data` that the user's own DLL ships; they look like 6-decimal source literals. Nothing writes to them [static].
- **`UMCos` never terminates for NaN, ±inf, or |x| ≥ 2⁵⁶.** For |x| ≥ 2²⁰ it effectively hangs: the loop takes |x|/2π steps [static, by reading the loop]. The game only passes small angles; the harness refuses such inputs.
- **Every EXE call to `UMSet*` passes `_PC_53` (0x10000) or `_RC_NEAR` (0)**, with one exception: EXE+0x13529d saves the precision before a `TSThread::Post` and restores the saved value afterwards [static]. The calls sit inside "if `Get()` is not the expected value, set it" guards in the thread start functions: EXE+0x1532fb (sim), +0xce947, +0x1110fe, +0x135ca7, +0x2603c4 and others.
- **msvcrt `sqrt` follows the caller's precision control.** It is not in Microsoft's list of SSE2-implemented CRT functions (atan, ceil, exp, floor, log, log10, modf, pow), so it runs as x87 `FSQRT` [inferred from the MS docs below; checked by the legion vectors]. At PC = 53 that is the IEEE double square root.

#### 2. Control-word writes and float→int conversion [static]
**No `FLDCW` exists in game code.** That covers EXE `.text`, LLE and Default.dll, both in the Ghidra listing and in a linear sweep.

The only CW writes are:
- the CRT start-up call `_controlfp(_PC_53, _MCW_PC)`, from the EXE call site at EXE+0x283914;
- the four `_controlfp` wrappers in LLE (above).

All 8 `FLDCW` sites in the EXE belong to the GOG/DRM residue section `.cms_t`: a static CRT with `__ftol`, `_math_exit` and pow/exp helpers. PLAN §2.3 already calls `.cms_t` residue, and func-db should confirm it is unreachable.

There are three ways the game converts float to int:

| Mechanism | EXE | LLE | Default.dll | Semantics |
|---|---|---|---|---|
| Inline `FISTP m32` under the ambient RC, typically `FSTP m32 t; FLD m32 t; FISTP m32` | 1,065 sites in 363 functions | 182 | 83 | **Round half to even.** This is not C truncation. |
| Call to a truncating helper (EXE+0x13ba92, LLE+0x6207c, Default+0x15ece). It does `FLD ST0; FISTP m64` (nearest), `FILD m64`, then corrects with the float-stored difference. It never touches the CW and returns EDX:EAX. | 527 calls in 173 functions | 36 | 4 | trunc to int64. Out of range or NaN gives 0x8000000000000000 [measured, Unicorn, 94 vectors]. Callers that use only EAX wrap modulo 2³². |
| msvcrt `_ftol` | 0 call sites; not imported | 0 | 0 | n/a |

**The 0x0E7F samples come from outside game code.** legion saw the main thread at CW 0x0E7F (PC53 plus truncate) in 2 of 17,339 samples. No game function produces that [static]. It is the classic CW-switching `_ftol` pattern found in system, driver or Frida code on that thread [inferred]. sxlrt233 imports `_ftol`, but only `std::basic_string` and exception helpers of sxlrt233 are imported by the game.

#### 3. x87 instruction census [static]
Coverage is the Ghidra listing, about 90% of `.text` instructions, plus a linear sweep for special instructions outside the listed functions.

| | EXE | LLE | Default.dll | sxlrt233 |
|---|---|---|---|---|
| functions / instructions | 20,827 / 1.37 M | 4,899 / 206 k | 622 / 30 k | 2,970 / 207 k |
| x87 instructions (functions) | 62,787 in `.text` + 629 in `.cms_t` (2,398) | 17,112 (736) | 2,311 (75) | 3,877 (188) |
| top classes | `fstp m32` 18.0k, `fld m32` 12.7k, `fldz` 6.8k, `fnstsw` 3.5k, `fild m32` 2.4k, `fcomp m32` 2.3k | same shape | same shape | m64-heavy |
| **FLD constants other than FLD1/FLDZ** | **0** (1 `FLDPI` in `.cms_t`) | 0 | 0 | 0 |
| **FLD m80 / FSTP m80** | **0 / 0** (5 + 5 unlisted in `.cms_t`) | 0 / 0 | 0 / 0 | 0 / 0 |
| **transcendental instructions** | **0 in game logic.** Two `FSIN` and two `FCOS` sit in a GDI drawing method at EXE+0x3baae0 that Ghidra did not list; it uses `SelectObject`, and its vtable neighbour calls `Ellipse`. `.cms_t` has `F2XM1`, `FSCALE` and `FRNDINT`. | 0 | 0 | 0 (uses msvcrt `_CI*`) |
| `FSQRT` | 0 (1 in `.cms_t`) | 0 | 0 | 0 |
| `FILD m64` | 674. Of these, 646 are the u32 idiom (`mov [t+4],0`), which gives ≤ 32 bits and is exact. The other 30 are the ftol helper's re-load (≤ 53 bits), QPC/time reads and UI. | 266 (235 u32; the rest are `GETerrainMesh::UpdateVisibleTerrain` render code and `TMGetMicroTime`) | 85 (82 u32, plus 3 that the heuristic missed) | 0 |
| compare idioms | `FNSTSW AX; SAHF` 3,399; `TEST AH,0x41` 30; `TEST AH,1` 29 | `SAHF` 845 | `SAHF` 170 | `TEST AH` 286 |
| P6 forms (`FCOMI`, `FUCOM*`, `FCMOV`) | 0 | 0 | 0 | 0 |

**Every source of extended precision is absent from game code** [static]: no 64-bit constants, no 80-bit loads or spills, and no transcendental results.

The only way a value with more than 53 significant bits could enter is `FILD m64`. In this game it loads u32 values, times, or values that a 53-bit `FISTP` produced.

At PC = 53 every arithmetic result is rounded to 53 bits. **The only thing a register value has beyond a `double` is the 15-bit exponent range.** That matters only for overflow past DBL_MAX and for results below DBL_MIN. With float-sized game data these need long multiplication chains [inferred]. They are not provably absent, so the design guards them.

Values kept on the stack across operations are therefore harmless at PC = 53. MSVC6 never spills x87 registers as 80-bit values here: there are no `FSTP m80` sites.

**Status-word reads use only C0, C2 and C3** (`SAHF`, `TEST AH,0x41/0x01`) [static]. Exception flags are never inspected by game code, so their exact emulation is not sync-relevant.

**msvcrt maths imports and their callers** [static]. Only `sqrt` is sim-hot.

| Import | Where | Sim relevance |
|---|---|---|
| `sqrt` | EXE 28 sites, LLE 18, Default 7, DX7HRDisplay | hot on the sim thread (2.9k/s). Exact (IEEE) at PC = 53. |
| `fabs` | EXE 5 sites | exact |
| `pow` | EXE+0x11da2d: `pow(1−|x|, k)` shapes a pitch that is passed to `GETransformation::SetOrientationYPR(float…)` | probably visual. It was not called in the 60 s in-game census. The result is narrowed to float before use. |
| `_CIpow` | EXE+0x3eafe0 and +0x3ee920. libpng gamma tables: they test the transform flags and use `sCAL` strings | none (image loading) |
| `atof`, `sscanf "%f"` | EXE+0x120a17 (3-token command parse); EXE+0xdb2df and EXE+0x1f8e8a (number after `_` in asset names, ×π/180) | load-time parsing of short decimal strings [inferred] |
| `strtod` | libpng `sCAL` | none |
| `log` | `SEConductor::SetVolume` (LLE) | none (music) |
| `sin` | DX7HRDisplay | render only |
| `rand`, `srand` | 6 sites each | not float. msvcrt keeps the LCG state **per thread**, so the HLE must too. This is flagged for netcode. |

sxlrt233's `_CIsin`, `_CIcos`, `_CIpow`, `_CIatan2` and the other `_CI*` calls are unreachable from the game: the game imports only `basic_string` and exception helpers from it [static].

### How the existing models behave
The vectors come from `tests/x87/gen_vectors.py --n 200 --seed 7`: 127,383 vectors over 6 control words. Of these, 125,623 have a reference value (others are ops the reference does not model). The suites are arithmetic, conversions, constants, compares, exact ops and transcendentals. Everything below is [measured].

**The kit's double model** (`runtime/x86.h` and `translate.py`, reproduced in `model_run.py --model kit`):
- **Bit-exact on game-shaped work:**
  - 0 differences in 13,577 PC53/RN vectors with double, float, game-like, tie and integer operands;
  - 0 differences in 7,700 PC24 vectors with float operands.
- **Wrong in 28,417 of 88,408 of the other vectors.** The causes:
  - 64-bit inputs and constants: `FLDPI; FMUL m64` differs in 25.5%, which reproduces ADR-001's 26%;
  - `FLD m80` rounds the value;
  - `FILD m64` above 2⁵³: 10–14% of random int64 values;
  - exponent-range escapes: 66–92% of range vectors;
  - NaN handling: every NaN result becomes the indefinite NaN, so payloads are lost, and `wrf80` drops the sign;
  - RC ≠ nearest for arithmetic: the host always rounds to nearest, so about 50% are wrong;
  - PC = 64: 54–88%;
  - PC = 24 overflow and underflow: the float cast loses the 15-bit exponent;
  - transcendentals come from the host libm.
- Threads start at CW 0x037F instead of 0x027F.

**Unicorn 2.1.4** agrees with our SDM model (`model_run.py --model exact`) on every arithmetic, conversion and compare vector. That includes ties, range escapes and extended denormals at PC < 64. It differs in three ways:
- it does not quiet a signalling NaN on `FLD m32/m64` or on `FST`;
- `FLDxx` ignores RC;
- transcendentals are computed in double.

**Rosetta 2** was run as x86-64 Python with `x87_runner.py --backend native` (on Apple's emulation, not real x87 hardware):
- **0 differences from the SDM model** outside transcendentals, across 125,623 vectors (and again across the 184,622 of the `--n 300` legion suite). That includes SNaN quieting, `FLDxx` under all RCs, the NaN-choice rules and denormals.
- `FSIN`, `FCOS`, `FPTAN` and `FSINCOS` equal the correctly rounded `f(x·π/P66)` within 1 ulp, and exactly in 77–96% of cases depending on the input class. P66 is π to 66 bits, the x87's reduction constant. Rosetta therefore emulates the x87 argument reduction.
- `FPATAN`, `F2XM1` and `FYL2X(P1)` are correctly rounded in 97–100% of cases.

This agreement makes the SDM model the specification to implement. It also means the existing Wine/Rosetta build is a sound extra oracle for arithmetic.

**UM\* functions under the double model** (`um_model.py`, a re-statement of the code above using doubles) equal Unicorn's x87 execution of the user's own LLE on all 22,076 PC53 call vectors of the `--n 2000` suite (4,476 at `--n 400`) [measured]. That covers ties at the table index, ±0, ±1, NaN and the near-1 series.

**00xJS** (knowledge only; no licence, nothing copied) relies on the same fact: at PC = 53 x87 equals SSE2 double. Its offline generator turns hot EXE and LLE functions into SSE2 "recipes" with these limits:
- constants are FLD1/FLDZ only;
- `FILD m64` is assumed below 2⁵³;
- `FISTP` becomes `cvtsd2si` (round to nearest, and it also yields 0x80000000 on overflow).

At run time, calls are dispatched by the caller's CW: PC53 runs the double code, and PC24 runs float code only when every operand is a float. A shadow mode checks results against the original. It has no range guard. This agrees with our census: the game-code subset it handles is exactly the subset where the double model is exact.

### Literature
These are the facts behind the transcendental policy [inferred from sources]:
- **Only accuracy bounds are specified.** Intel's SDM and AMD's manual give an error bound below 1 ulp for x87 transcendentals, not bit patterns. `FSIN`, `FCOS` and `FPTAN` reduce their argument with a 66-bit π.
- **Results have changed between CPU generations.** They changed with the Pentium, and between the AMD K6 and later AMD parts, which were made compatible with Intel. They are believed identical on current Intel and AMD CPUs, but no one has published a verification.
- **Microsoft's 32-bit CRT has SSE2 code for some functions.** It uses SSE2 implementations of `atan`, `ceil`, `exp`, `floor`, `log`, `log10`, `modf` and `pow` when the CPU has SSE2 and all exceptions are masked. Those results "might give slightly different answers" from the x87 versions. This is a CRT-algorithm question; it does not depend on the CPU vendor.

## Options considered

**A. SoftFloat extF80 for every x87 operation.** Registers are 80-bit and every op calls Berkeley SoftFloat 3e (BSD-3).
- *For:* exact by construction for every PC, RC, range and NaN rule.
- *Against:*
  - Every op becomes an out-of-line call. A minimal 80-bit multiply or add with no special cases already takes 2.1 ns against 1.3 ns for the double path [measured, microbenchmark]. A full SoftFloat op is likely 5–15 ns [inferred].
  - It blocks the later optimisation of keeping the x87 stack in C locals (00xJS's xmm-stack idea).

**B. Keep the kit's double model.** It is exact for the game-shaped cases measured, but it has the known failure modes listed above. That is not "exact" by construction.

**C. Hybrid (chosen).** Use a double fast path where it is provably equal to x87, and SoftFloat everywhere else:
- a per-thread mode word;
- a check of every result's exponent;
- side registers for extended values.

**D. A layer outside the fork.** ADR-001 allows replacing the x87 layer. This note shows it is unnecessary: the kit's register file, tags and helpers stay, and the changes are local to `x86.h` and the x87 emitters in `translate.py`.

## Decision

Adopt **C**, inside the fork. Specification: `model_run.py --model exact` (the SDM reading confirmed by Rosetta), to be confirmed on `legion` hardware.

### Register representation
Per thread:
- `double st[8]` (as today);
- `uint8 kind[8]`, where 0 means the value is exactly a double and 1 means extended;
- `extF80 stx[8]`;
- `uint8 ext_live`, a count of registers with kind 1.

The kit's `st_bits` and `st_exact` FILD shortcut goes away: an integer of up to 2⁵³ is an exact double, and a bigger one becomes extended.

### Mode word
`fp_mode` is recomputed on every CW write: `FLDCW`, `FLDENV`, `FRSTOR`, `FNINIT`, `FNSAVE` and the `_controlfp` HLE.

| fp_mode | Condition | Arithmetic path |
|---|---|---|
| 0 | PC = 53, RC = nearest, `ext_live == 0` | double fast path |
| 1 | PC = 24, RC = nearest, `ext_live == 0` (the render thread) | double, then round to 24 bits |
| 2 | anything else | SoftFloat with exact PC and RC |

Each x87 arithmetic op tests `fp_mode` once. The branch is predictable per thread.

### Fast-path guard (mode 0)
- **`*` and `/`:** compute `r = a op b`. If the exponent field of `r` is 0 or 0x7ff, take the slow fix-up.
  - A zero with a zero operand returns at once.
  - Underflow, overflow and NaN are recomputed in extF80 at PC53/RN.
  - If the result is not a double (a range escape), the destination becomes kind = extended.
- **`+` and `−`:** only 0x7ff (overflow or NaN) needs the fix-up. Sums in the subnormal range are exact.
- **`FSQRT`:** cannot leave the double range; negative inputs and NaN go to the fix-up.

### PC = 24 (mode 1)
- The result is the double result rounded to 24 bits *inside a double*, ties to even, keeping the 15-bit exponent range. It is not the float cast.
- **Double-rounding check:** if the 29 discarded bits are exactly a midpoint, compute the exact residual (`fma`, TwoSum or a remainder) and round accordingly.
- Results outside the double range take the slow path.

PC24 exactness is not sync-relevant: it is render only. It keeps one semantics source and makes PC24 differential tests meaningful.

### Loads, constants and stores
- **`FLD m32`, `FLD m64`:** exact. An SNaN loaded from m64 is quietened (one bit test).
- **`FILD m16`, `FILD m32`:** double. **`FILD m64`:** double if |v| ≤ 2⁵³, otherwise extended.
- **`FLD1`, `FLDZ`:** double.
- **`FLDPI`, `FLDL2E`, `FLDL2T`, `FLDLG2`, `FLDLN2`:** extended. The exact 64-bit constant is rounded per RC: `FLDPI` gives …c235 at nearest and up, and …c234 at down and chop.
- **`FLD m80`:** double if exactly representable, otherwise extended.
- **`FST m32`:** `(float)` of the register in mode 0/1, which is a single rounding of the register value, as on hardware. Directed RC and extended values go through SoftFloat.
- **`FST m64`:** identity for double registers; SoftFloat for extended ones.
- **`FST m80`:** exact encoding.
- **`FIST` / `FISTP`:**
  - fast path at RC = nearest is `fcvtns` into 64 bits, then a range check;
  - out of range or NaN gives the integer indefinite: 0x8000, 0x80000000 or 0x8000000000000000;
  - other RCs are explicit;
  - this keeps the round-half-even meaning of the game's 1,330 inline sites.

### Compares and NaN rules
- **Compares:** unchanged from the kit; extended operands go through SoftFloat compare. C0, C2 and C3 must be exact. Other flags are best effort, since game code never reads them [static].
- **NaN rules (slow path):**
  - an invalid operation gives the negative QNaN indefinite;
  - a single NaN operand is quietened and returned;
  - with an SNaN and a QNaN, the QNaN wins;
  - otherwise the larger significand wins.

  The tie rule (equal significands) is still open: legion decides.

### Host floating-point environment
- On every runtime thread assert FPCR FZ = 0, DN = 0, RMode = RN and AH = 0.
- Compile generated C with `-ffp-contract=off` and without any fast-math or finite-math flags.
- CI greps generated objects for `fmadd/fmsub/fnmadd/fnmsub`. The spike bundles contain 0 today [measured].

### Control word and `_controlfp`
- Per thread, initialised to **0x027F**, the Windows thread default (legion; ADR-001 gap 3). `FNINIT` sets 0x037F, as hardware does.
- `_controlfp`, `_control87` and `__control87_2` are an HLE: a pure mapping to and from the x87 bits with no system calls.
  - Examples: 0x027F ↔ 0x9001F and 0x007F ↔ 0xA001F (measured on legion).
  - The bits include `_EM_DENORMAL` 0x80000 and `_IC_AFFINE` 0x40000 ↔ CW bit 12.
- Cost: well under a microsecond at about 4.2k calls/s.

### Transcendental policy
1. **x87 instructions** (`FSIN`, `FCOS`, `FSINCOS`, `FPTAN`, `FPATAN`, `F2XM1`, `FYL2X`, `FYL2XP1`):
   - none occur in game logic;
   - implement them in C on top of SoftFloat as correctly rounded `f(x·π/P66)` (and `C2` with an unchanged operand for |x| ≥ 2⁶³), which matches Rosetta within 1 ulp;
   - mark them "not bit-exact". Debug builds log any execution on the sim thread.
2. **msvcrt functions:**
   - `sqrt` and `_CIsqrt`: the IEEE square root, rounded at the **caller's PC**, like the x87 path.
   - `fabs`: exact.
   - `pow`, `_CIpow`, `exp`, `log`, `atan`, `floor`, `ceil`: correctly rounded implementations (CORE-MATH, MIT). On Windows these use MS's SSE2 code, which is CPU-vendor-independent but not guaranteed correctly rounded.
     - They are off the sim path [static]. The one possibly sim-adjacent caller, EXE+0x11da2d, narrows to float. A 1-ulp double difference then changes the float with probability of about 2⁻²⁹ per call [inferred].
   - `sin` and `cos`: CORE-MATH (render only).
   - Legion vectors measure all of these.
3. **Decimal parsing** (`atof`, `strtod`, `sscanf "%f"`): use a correctly rounded conversion. The game's inputs are short decimal strings, where msvcrt is very likely exact [inferred]. Legion vectors include hard midpoint strings to measure where msvcrt differs.
4. **Intel versus AMD** [inferred]:
   - The cross-vendor risk is confined to x87 transcendental instructions and msvcrt's x87-path `sin` and `cos`. None of them feeds EE's simulation, which explains why Windows↔Windows play works across vendors.
   - msvcrt's SSE2 functions run the same IEEE operations on any vendor.
   - To settle it, run `x87_runner.py` on one AMD Windows PC.

### Performance budget [measured microbenchmark; rates inferred]
| Path | ns per x87 op (M4 Max, ops on a context-resident register file) |
|---|---|
| kit double (today) | 1.27 |
| double + mode test + exponent guard (C, mode 0) | 1.26 (the guard is free next to the memory dependency) |
| minimal software 80-bit multiply/add (lower bound for A) | 2.11 |

The sim-thread rate is estimated at about 10⁷ x87 ops/s in a late-game battle: about 1,000 units × about 250 ops × 41 ticks. At that rate:
- C costs about 13 ms/s, about 1.3% of a core;
- A would cost about 50–150 ms/s.

Library calls are negligible: `sqrt` at 2.9k/s and UM\* at 2.3k/s cost microseconds per second. The slow path runs only on special results.

### Conformance suite (tests/x87/)
| Layer | Mac | legion / other x86 | Compare |
|---|---|---|---|
| Instructions | `gen_vectors.py` produces seeded suites: arith, conv, consts, compare, exact, transc, idiom. Models: `model_run.py --model exact\|kit`, `x87_runner.py --backend unicorn`, Rosetta via `arch -x86_64 python x87_runner.py --backend native`. | `x87_runner.py --backend native` (64-bit Python, `ctypes`, RWX page; no game needed). Also runs on any x86-64 Linux, Windows or Intel Mac, e.g. an AMD friend's PC. | `compare.py` by op × cw × tag. Values and C0/C2/C3 are compared; exception flags only with `--sw-mask`. |
| Game functions | `gen_vectors.py --suite um`. `um_unicorn.py` runs the user's LLE and EXE in Unicorn with msvcrt stand-ins. `um_model.py` is the double model of UM\*. | `windows/frida/ummath_vectors.js` calls the real functions in the running game, with the same stub bytes as `x87ops.build_call_stub` [measured: identical for all 14 signatures]. | `compare.py --by fn,cw` |
| Translated code (Phase 2) | Instruction vectors assembled into a tiny module, run through the fork's translator and executed natively. Call vectors run against the translated LLE and EXE bundles (`ee_harness`). | n/a | same files |
| Game | per-tick checksum streams; `Debug OOS Handling` "OUT OF SYNCH DUMP" text (positions and transforms printed `%6.6f`); M1 byte-identical random maps | LAN sessions | PLAN §6 |

### Hardware validation (2026-10-14, legion: Intel Core Ultra 9 275HX)
Run with the procedure in "Running on legion" below. Results are in `build/x87/*.md`, which is git-ignored and can be regenerated.
- **Instruction vectors: 187,182 run on hardware.** Against the **exact model** (`model_run.py --model exact`), every non-transcendental suite (arithmetic, ties, range, specials, memory loads and stores, integer conversions) matches **bit for bit at every tested control word**: `0x027F`, `0x007F`, `0x037F`, `0x0E7F`, `0x067F`. The only differences are transcendental instructions.
  - With the π/66-bit reduction model (`--pi66`), those are 660 cases, all within 1 ulp. Hardware transcendentals are not correctly rounded, so we model them as "informational" (criterion 6).
- **The kit's double model vs hardware** differs only where the game never goes: 64-bit-precision inputs, range escapes, specials, PC64, and non-nearest rounding. Game-shaped arithmetic at `0x027F`/`0x007F` has 0 differences.
- **Game maths called inside the real game** (Frida, 52,608 vectors, 0 errors): at the simulation's `0x027F`, `UMArcTan2`, `UMArcCos`, `UMArcTan`, `UMCos`, `UMTan`, `UMFastMath::Sqrt`, `UMQuadraticEquation` and msvcrt `sqrt` equal the double model with **0 differences** in about 24,000 calls.
  - The msvcrt `pow`/`atan2`/`sin`/… suites and the PC24 call rows aren't modelled by `um_model.py` yet. They are off the simulation path; they move to Phase 2 with the msvcrt HLE.
- **Full-size corpus (2026-10-14, run by the legion operator agent, mailbox 0002):** 1,085,508 vectors (seed 2, 38 tags, 58 ops) over control words `0x027F`, `0x007F`, `0x037F`, `0x067F`, `0x0A7F` and `0x0E7F`. It ran in 7 s on legion with 0 skips, and a second run was byte-identical.
  - Exact model vs hardware: **966,532 non-transcendental vectors, 0 differences.** Of the 104,416 transcendental vectors, 37,546 differ (informational).
  - The corpus, `insn-2-legion.jsonl.gz` (sha256 `752d4214…cd7bad`), is the Phase 2 reference for criterion 1. It is kept on legion (`C:\EEPort\x87\`) and in the git-ignored `build/x87/big/`, and is regenerable from seed 2.
- **Conclusion:** the design holds. The reference model is right about real hardware, and the double fast path is exact for everything the simulation does. Criteria 1–2 are scaled to the full vector counts when the translated code exists (Phase 2).

### Hardware validation, round 2 (2026-10-15, legion, same CPU)
- **Implementation request set:** 17,864 instruction vectors, run with 0 skips. It covers:
  - the four forms the seed corpora never used (`fsubr_m64`, `fcom_m64`, `fild_m16`, `fild_m32`);
  - the fast path's edges: products rounding up to DBL_MIN, PC=24 sums landing on a 24-bit midpoint, quotients beside DBL_MIN.
- **One rule corrected.** With ST(0) a QNaN and an m64 operand an SNaN, hardware returns ST(0)'s QNaN, payload and all. The model and the kit had quietened the memory operand first and picked by significand, which gave 18 differences, all `fsubr_m64` across the six CWs. The rule is SDM table 4-7: an SNaN and a QNaN give the QNaN, whichever operand holds it.
  - The kit is fixed on `feat/x87-nan`; m32 operands keep their signalling bit too. `model_run.py` is fixed here.
  - Both now give 0 differences on the set [measured]. The two big corpora stay at 0 non-transcendental differences (169,766 and 981,092 vectors) [measured].
- **Criterion 2 at scale:** 131,208 call vectors recorded inside the game (`um-3`), run through the translated modules (`tests/x87/run_um.py --with-exe`).
  - **113,180 required vectors equal the recording bit for bit**, at 0x027f and 0x007f: UMArcCos, UMArcTan, UMArcTan2, UMCos, UMTan, UMQuadraticEquation, UMFastMath::Sqrt, msvcrt `sqrt`/`_CIsqrt`/`_ftol`, and the executable's ftol helper [measured].
  - Informational: msvcrt `pow`/`_CIpow`/`sin`/`_CIsin`/`log` still differ, because they use the host libm. `atof` and `sscanf "%f"` now match (0 of 5,028).

### Acceptance criteria
1. **Instruction vectors.** All non-transcendental suites, at least 10⁶ vectors over CWs 0x027F, 0x007F, 0x037F, 0x0E7F, 0x067F and 0x0A7F, plus the game idioms. The port's translated x87 must equal legion **bit for bit**: register values, memory outputs and C0–C3.
2. **Call vectors.** At least 10⁵ vectors at PC53 and PC24 for UM\*, `UMFastMath::Sqrt`, msvcrt `sqrt` and the three truncating ftol helpers. The translated modules must equal legion's Frida results bit for bit, including ST(0) as 80 bits.
3. **Census in CI** (`census.py` over func-db's function list): no extended-precision source, `FLDCW` or transcendental instruction in game code beyond the ones listed here. A new hit needs review.
4. **Sim-thread counters in a 30-minute 8-AI skirmish:** `ext_live` is never above 0 and no mode-2 arithmetic runs on the sim thread. Slow fix-ups are logged by kind; zero results are expected.
5. **M1:** Default.dll random maps are byte-identical with legion for 50 seeds or scripts. Checksum streams match in LAN tests (PLAN §6.3–6.5).
6. **Informational:** transcendental instructions are within 1 ulp of legion; msvcrt `pow`, `atan`, `atof` and the others: differences are listed and reviewed.

### Effort
Estimated at **about 3.5–4.5 focused weeks**, against ADR-001's 4–6 for gap 6. That is inside the 6-week gate, so the fork's layer is kept.

| Work | Weeks |
|---|---|
| mode word, guards, side registers and SoftFloat 3e (vendored, freestanding) in `x86.h`, plus emitter changes for arithmetic, loads, stores, FIST and constants | 1.5–2 |
| PC24 exact rounding | 0.5 |
| CW 0x027F, `_controlfp` HLE, FPCR asserts, compile flags, FMA check | 0.5 |
| msvcrt maths and parsing HLE subset (`sqrt`, `_CIsqrt`, `fabs`, `pow`, `_CIpow`, `_ftol`, `atof`, `strtod`, `sscanf %f`, per-thread `rand`), overlapping gap 4 | 0.5 |
| suite integration with translated code, legion runs, CI | 1 |
| (optional) x87 transcendental instructions with π/P66 reduction | 0.5 |

The saving over ADR-001 comes from the census: there are no 64-bit sources or transcendentals in game code, so the 80-bit path is a rarely taken fallback, not the main path.

## Consequences
- **The fast path stays a plain double expression.** Later work can keep x87 slots in C locals inside a function, as 00xJS does with xmm registers, without changing semantics, provided the guard and `fp_mode` checks are kept.
- **Every x87 emitter in `translate.py` changes**, roughly 40 cases. `x86.h` grows by a few hundred lines plus vendored SoftFloat (about 6k lines of C, BSD-3, compatible with the freestanding bundle mode).
- **Exception flags stay best effort.** If a future module reads PE, UE or OE, mode 2 already models them.
- **Wine/Rosetta is a valid arithmetic oracle**, which helps the Mac↔Wine LAN tests (PLAN §6.5). Its msvcrt is Wine's, so transcendentals differ; this is sim-irrelevant.
- **Open questions** (to settle before acceptance):
  1. What legion hardware says about SNaN quieting, `FLDxx` under RC, the NaN tie rule, the denormal quantum at PC < 64, msvcrt `sqrt` at PC24 (x87 path, or SSE2 ignoring PC), msvcrt `sqrt(−1)` bits, `atof` and `sscanf` on midpoint strings, and UM\* (expected equal to Unicorn and the double model).
  2. Whether an AMD CPU gives the same results as legion for transcendentals (run the instruction suite on one AMD box).
  3. Whether the `pow` curve (EXE+0x11da2d), the `sscanf "%f"` sites and `atof` run on the sim thread and with what inputs. Check with `iat_census` during a LAN session and a scenario load.
  4. The `rand`/`srand` callers and threads (netcode/func-db).
  5. Confirmation (func-db) that `.cms_t` is unreachable.
  6. The ~10% of `.text` outside Ghidra functions. The linear sweep found only the GDI `FSIN`/`FCOS`; re-run the census on func-db's ≥ 99.5% function list.

## Evidence
- **Census:** `tests/x87/census.py`, giving `analysis/sync-math/census/{Empire Earth.exe,Low-Level Engine.dll,Default.dll,sxlrt233.dll}.{md,json}` (git-ignored).
  - Inputs: the GOG modules and the spike's Ghidra exports (`spikes/ee-recomp/{,exe-scale/}analysis/decompiled/`).
  - The linear sweep for unlisted special instructions was a scratch script; its results are quoted above.
- **Vectors and models:** `tests/x87/{x87ops,gen_vectors,x87_runner,model_run,compare,um_unicorn,um_model}.py`. Mac runs are in `analysis/sync-math/x87/`:
  - `cmp200-exact-kit.md`
  - `cmp200-unicorn-exact.md`
  - `cmp200-rosetta-exact.md`
  - `cmp-um.md`
- **Frida harness:** `windows/frida/ummath_vectors.js`. A Node mock check confirmed that its stub bytes equal the Python stubs.
- **Microbenchmark:** 200 M iterations, `clang -O2`, M4 Max, macOS 15.5.
- **Sources:**
  - [Intel: x87 FSIN/FCOS/FSINCOS/FPTAN vs the mathematical functions](https://intel.com/content/www/us/en/developer/articles/technical/the-difference-between-x87-instructions-and-mathematical-functions.html)
  - [Intel forum: x87 FSIN/FCOS accuracy, 66-bit π](https://community.intel.com/t5/Intel-oneAPI-Math-Kernel-Library/x87-FSIN-FCOS-mediocre-accuracy/td-p/818625)
  - [B. Dawson, Floating-Point Determinism](https://randomascii.wordpress.com/2013/07/16/floating-point-determinism/)
  - [B. Dawson, Intel Underestimates Error Bounds by 1.3 quintillion](https://randomascii.wordpress.com/2014/10/09/intel-underestimates-error-bounds-by-1-3-quintillion/)
  - [Microsoft: `_set_SSE2_enable`](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/set-sse2-enable?view=msvc-170)
  - [rsemu issue on x87 transcendental bit patterns](https://github.com/KarpelesLab/rsemu/issues/38)

### Running on legion (do not run during a live LAN session)
From the Mac. `$PY` is a venv Python with `tools/requirements.txt` plus `unicorn` and `mpmath`.

**1. Generate the vectors** (seeded; they contain no game bytes).
```sh
mkdir -p build/x87
$PY -I tests/x87/gen_vectors.py --n 300 --seed 1 -o build/x87/insn.jsonl
$PY -I tests/x87/gen_vectors.py --suite um --n 2000 --seed 1 -o build/x87/um.jsonl
```

**2. Copy the runner, vectors and Frida script to legion.**
```sh
ssh legion "if not exist C:\EEPort\x87 mkdir C:\EEPort\x87"
scp tests/x87/x87ops.py tests/x87/x87_runner.py build/x87/insn.jsonl build/x87/um.jsonl legion:C:/EEPort/x87/
scp windows/frida/ummath_vectors.js legion:C:/EEPort/frida/
```

**3. Run the raw instructions on the Intel Core Ultra 9.** The game does not need to be running.
```sh
ssh legion 'C:\EEPort\tools\venv\Scripts\python.exe -I C:\EEPort\x87\x87_runner.py --backend native C:\EEPort\x87\insn.jsonl -o C:\EEPort\x87\insn-legion.jsonl'
```

**4. Run the game-function vectors in the running game.** The main menu is enough. `L` is the helper from `windows/README.md`.
```sh
L start
ssh legion 'C:\EEPort\tools\venv\Scripts\python.exe C:\EEPort\frida\run.py --scripts ummath_vectors --seconds 180 --out C:\EEPort\captures\ummath --param in=C:\EEPort\x87\um.jsonl --param out=C:\EEPort\x87\um-legion.jsonl'
L stop
```

**5. Fetch the results and compare.**
```sh
scp legion:C:/EEPort/x87/insn-legion.jsonl legion:C:/EEPort/x87/um-legion.jsonl legion:C:/EEPort/captures/ummath/ummath_vectors.json build/x87/
for m in exact kit; do $PY -I tests/x87/model_run.py --model $m build/x87/insn.jsonl -o build/x87/insn-$m.jsonl; done
$PY -I tests/x87/model_run.py --model exact --pi66 build/x87/insn.jsonl -o build/x87/insn-pi66.jsonl
$PY -I tests/x87/x87_runner.py --backend unicorn build/x87/insn.jsonl -o build/x87/insn-unicorn.jsonl
$PY -I tests/x87/compare.py build/x87/insn-legion.jsonl build/x87/insn-exact.jsonl --vectors build/x87/insn.jsonl --by tag,cw --md build/x87/legion-vs-exact.md
$PY -I tests/x87/compare.py build/x87/insn-legion.jsonl build/x87/insn-pi66.jsonl --vectors build/x87/insn.jsonl --by op,tag --md build/x87/legion-vs-pi66.md
$PY -I tests/x87/compare.py build/x87/insn-legion.jsonl build/x87/insn-kit.jsonl --vectors build/x87/insn.jsonl --by tag,cw --md build/x87/legion-vs-kit.md
$PY -I tests/x87/um_unicorn.py build/x87/um.jsonl -o build/x87/um-unicorn.jsonl
$PY -I tests/x87/um_model.py build/x87/um.jsonl -o build/x87/um-double.jsonl
$PY -I tests/x87/compare.py build/x87/um-legion.jsonl build/x87/um-unicorn.jsonl --vectors build/x87/um.jsonl --by fn,cw --md build/x87/um-legion-vs-unicorn.md
$PY -I tests/x87/compare.py build/x87/um-legion.jsonl build/x87/um-double.jsonl --vectors build/x87/um.jsonl --by fn,cw
```

**Other machines.** On an AMD friend's PC, any 64-bit Python 3 can run the instruction suite. Copy `x87ops.py`, `x87_runner.py` and `insn.jsonl` to one folder, then run:
```
py -3 x87_runner.py --backend native insn.jsonl -o insn-amd.jsonl
```
