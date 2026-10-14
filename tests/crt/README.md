# msvcrt.dll behaviour vectors

The kit's msvcrt/msvcp60 HLE (workstream D, `kit/runtime/msvcrt*.cpp`, `msvcp60.cpp`) has to
reproduce what Windows' **own `msvcrt.dll`** does, not what the C standard allows: cross-play
needs the same parsed numbers, the same formatted text and the same `rand()` sequence on both
sides. These files measure that on a real Windows machine (legion) and compare it with the HLE.

| File | Role |
|---|---|
| `gen_vectors.py` | Writes the vector file (seeded; ~24k vectors, ~41k with game numbers). |
| `extract_game_numbers.py` | Collects the numeric tokens the game parses (`.rmv` map scripts on disk; `.tai` scripts and the db converter log inside `data.ssa`). Output is game-derived and goes to `analysis/crt/` (git-ignored). |
| `crtvec.c` | Windows harness: loads the **system** `msvcrt.dll` with `LoadLibrary` and calls every function through `GetProcAddress`, so MSVC's own UCRT never takes part. 32-bit, MSVC x86. |
| `run.cmd` | Builds `crtvec.exe` and runs it, resuming past any vector that kills the process. |
| `compare.py` | Diffs the Windows results with the HLE's and writes a Markdown report. |
| `build_wine.sh` | Builds `crtvec.exe` on the Mac with LLVM for a dry run under Wine (no SDK needed). |

The HLE side runs the same file with the kit's test binary:
`kit/build/recomp/crt_tests --vectors vectors.tsv results-mac.tsv`.

## Running it

**Mac (generate):**
```sh
python3 -I tests/crt/extract_game_numbers.py --game-dir "original/gog/Empire Earth" \
    --dcl-lib analysis/audio/build/libdcl.dylib -o analysis/crt/game-numbers.tsv
python3 -I tests/crt/gen_vectors.py --seed 1 --game-numbers analysis/crt/game-numbers.tsv \
    -o build/crt/vectors.tsv
```
Without `--game-numbers` the file holds no game data (`build/crt/vectors-nogame.tsv`).

**Windows (legion operator):** copy `crtvec.c`, `run.cmd` and `vectors.tsv` to one folder
(e.g. `C:\EEPort\crt\`). In an **x86** Native Tools Command Prompt (or after `vcvars32.bat`;
the compiler must be the 32-bit `cl.exe`, `Hostx64\x86` or `Hostx86\x86`):
```bat
cd C:\EEPort\crt
run.cmd vectors.tsv results-win.tsv
powershell -c "(Get-Item C:\Windows\SysWOW64\msvcrt.dll).VersionInfo.FileVersion" > msvcrt-version.txt
powershell -c "Get-Culture; Get-WinSystemLocale" > locale.txt
```
Return `results-win.tsv`, `msvcrt-version.txt` and `locale.txt`. A run takes seconds. Do not
run it during a live LAN session (it changes nothing persistent, but it is a CPU burst). It
writes and deletes one file, `%TEMP%\crtvec.tmp`.

**Mac (compare):**
```sh
kit/build/recomp/crt_tests --vectors build/crt/vectors.tsv build/crt/results-mac.tsv
python3 -I tests/crt/compare.py build/crt/vectors.tsv build/crt/results-win.tsv \
    build/crt/results-mac.tsv --report build/crt/report.md
```
Both sides must use the same `vectors.tsv`.

## Format

One vector per line: `id<TAB>op<TAB>field...`; one result per line: `id<TAB>op<TAB>out...`.
Text is hex-encoded (bytes; tabs, newlines, NULs and high bytes survive), doubles are their
16-hex-digit IEEE bits, 80-bit values 20 hex digits (sign/exponent first), integers decimal.
`NULL` stands for a null pointer result. Every vector starts in the `"C"` locale; a `loc`
field is passed to `setlocale(LC_ALL, loc)` first.

| op | inputs | outputs |
|---|---|---|
| `rand` | seed, n | n values after `srand(seed)` |
| `rand_thread` | n | n values of `rand()` in a fresh thread (no `srand`) |
| `strtod` | loc, text | bits, characters consumed, errno |
| `atof` | loc, text | bits, errno |
| `sscanf_f` / `sscanf_lf` | loc, text | return, float bits (8 hex) / double bits, `%n` |
| `sscanf_d` | text | return, int, `%n` |
| `sprintf_d` | loc, format, double bits | text, return (the double is passed as two dwords) |
| `sprintf_i` / `sprintf_i64` / `sprintf_s` | format, dword / qword hex / text | text, return |
| `snprintf` | count, format, text | return, the first count+4 bytes of a 0xCC-filled buffer |
| `strtol` / `strtoul` | text, base | value, consumed, errno |
| `atoi` / `atoi64` / `itoa` | text / text / value, radix | value / value / text |
| `math` | fn, control word, x bits, y bits or int | double result, 80-bit ST(0), errno (`_finite`: the int) |
| `ci` | `_CI*` fn, control word, a, b | double result, 80-bit ST(0); operands pushed a then b |
| `ftol` | control word, x bits | EDX:EAX |
| `ctype` | loc, int c | isalnum, isalpha, isdigit, isspace, isxdigit (hex), tolower, toupper |
| `wctype` | loc, wchar | towupper, towlower, `iswctype(w, 0x1ff)` |
| `mbconv` | loc, byte | `mbstowcs` count and wchar, `wcstombs` count and byte |
| `mbcase` | loc, text | `_mbsupr` and `_mbslwr` of a copy |
| `cmp` | fn, loc, a, b, n | return value of strcmp, strncmp, memcmp, _stricmp, _strnicmp, _mbscmp, _mbsicmp, _mbsnbcmp, _mbsnbicmp, _mbsncmp or wcscmp (bytes widened) |
| `setlocale` | category, name | result, the LC_ALL string after it, localeconv's ten strings and eight chars |
| `asctime` | struct tm (9 ints) | text |
| `strftime` | loc, format, max, struct tm | return, text |
| `gmtime` | time_t | struct tm or `NULL` |
| `msize` | n | `_msize(malloc(n))`, address mod 16 |
| `splitpath` | path | drive, dir, fname, ext |
| `typename` | decorated name | `type_info::name()` of a type_info built around it |
| `textfile` | fopen mode, file bytes | `fgets(16)`+`ftell` until NULL, `_IOEOF`, `fread(200)` after `rewind` and its text and `ftell`, then `fseek(3)`, `fgetc`, `ungetc('Q')`, `ftell`, `fgetc`, `fgetc` |

The harness marks a vector `EXC <code>` when it raised, appends `IPH` when msvcrt's
invalid-parameter handler ran, and writes `CRASH` for one that ended the process.

## How differences are judged

`compare.py` sorts every vector into a class:

- **strict**: must be identical. Everything a program can observe from the deterministic part
  of the C runtime: decimal parsing and formatting, `rand`, the integer conversions, string
  comparison results, case mapping, locales, time formatting, file positions, `sqrt` and
  `_CIsqrt` (the simulation calls `sqrt` about 2,900 times a second), `fabs`, `ldexp`,
  `_ftol`, `_msize`, type names.
- **truthy**: the ctype predicates only matter as zero/non-zero; their raw bits are counted
  as informational.
- **informational**: `log`, `sin`, `pow` and the `_CI*` transcendentals (none feeds the
  simulation, ADR-002), every 80-bit field and every result at control word 0x037F (PC=64:
  the HLE keeps doubles; the x87 layer's extended path owns that), `setlocale("")` (the
  Windows user's own locale) and `strftime("%Z")`.

## What the vectors settle

Each of these is implemented from documentation and the behaviour msvcrt is known for; the
run confirms or corrects it ([inferred] until measured).

- **printf floats:** 17 significant digits, correctly rounded, then zeros; a second
  half-up rounding on the digit string at the requested precision (`%.0f` of 2.5 is `3`,
  `%.2f` of 0.125 is `0.13`); three-digit exponents; `1.#INF00`, `-1.#IND00`, `1.#QNAN0`,
  `1.#SNAN0`, and their rounded forms (`%.2f` of infinity is `1.#J`, `%.1f` is `1.$`);
  `-0.000000`; LC_NUMERIC's separator. If the 17-digit step turns out not to be correctly
  rounded, `kPrintfDigits` and `double_digits` in `msvcrt_format.cpp` are where it changes.
- **strtod/atof/sscanf:** correctly rounded; 'd'/'D' exponents in strtod/atof but not scanf;
  no `inf`/`nan`/hex spellings; an exponent marker without digits is not consumed by strtod
  but is by scanf; `%f` rounds once, straight to float (the vectors include decimals just
  above a float midpoint that round to the midpoint as a double); overflow/underflow errno.
  Long hard-case strings test whether msvcrt reads every digit.
- **Integers:** `atoi`/`_atoi64` wrap on overflow (no saturation) while `strtol`/`strtoul`
  saturate with ERANGE; `%d` in scanf wraps.
- **rand:** `seed*214013+2531011`, bits 16..30, per thread, a new thread starts at 1.
- **Comparisons:** `strcmp`/`memcmp`/`wcscmp`/`_mbscmp` return -1/0/1; `strncmp`,
  `_stricmp`, `_strnicmp` return the byte difference; the `_mbs*` forms follow LC_CTYPE
  like the `str*` forms in a single-byte code page.
- **Locale tables:** the full ctype and case tables for `"C"` and `"English"` (bytes
  -128..255, wide 0..0x2ff and samples), aliases accepted by setlocale, localeconv fields.
- **Maths:** `sqrt` at control words 0x027F, 0x007F (PC=24, including the double-rounding
  cases) and 0x037F; `sqrt(-1)` bits; the `_CI*` operand order; `_ftol` limits.
- **Everything else:** `_msize(malloc(0)) == 1`, `asctime` zero-pads the day, `%c`/`%x`/`%X`
  in both locales, `_splitpath` with over-long parts, `type_info::name` formatting of
  templates and back-references, text-mode `fgets`/`fread`/`ftell` with CR, CR LF and
  Ctrl-Z, `ungetc` and `ftell`.

## A dry run under Wine

`crtvec.c` also builds with clang-cl and lld-link against import libraries made from .def
files, and runs under Wine 11 against Wine's own msvcrt (2026-10-14, 41,096 vectors, no
crash, no exception). Wine is a reimplementation, so this checked the harness, not msvcrt;
but where Wine's choices reflect how msvcrt works they were adopted (SNaN arguments come
back quiet, as an FLD would leave them; `strtoul` negates after saturating; Ctrl-Z in a
text stream discards the rest of the chunk read; `sqrt(NaN)` sets EDOM). The remaining
disagreements are the questions the legion run answers:

| Question | HLE (as built) | Wine |
|---|---|---|
| printf digits beyond the 17th, and the second rounding (`%.1e` of 0.995) | zeros; `1.0e+000` | exact; `9.9e-001` |
| printf of a signalling NaN | `1.#SNAN0` | `1.#QNAN0` |
| `%.0g`, `%#g`, `%#.0f`, `%#.0e`, `%-12.3e` of infinity | `1`, `1.#INF0`, `1.`, `1.e+000`, `1.#IOe+000  ` | `1.#INF`, `1.#INF`, `1`, `1e+000`, `1.#IO   e+000` |
| `%5%` | `%` | nothing |
| scanf `%f` on `1.5d2`, `1e`, `.`, `-`, `" "` | `d` not an exponent; `1e` is 1; 0; 0; EOF | `d` is; EOF; EOF; EOF; 0 |
| `_atoi64` overflow | wraps | saturates |
| `_msize(malloc(0))`, malloc address mod 16 | 1, 0 | 0, 8 |
| `_splitpath` with a part over 255 characters | truncates | all parts empty |
| `ldexp` underflowing to zero | ERANGE | no errno |
| `sqrt` at PC=24 on an exact tie | even (x87 FSQRT) | odd (not an x87 FSQRT) |
| `setlocale(…, "british")` | English_United Kingdom | refused |
| ctype functions on values outside -128..255, `tolower(EOF)` | unchanged, EOF | low byte |

## Legal hygiene

No game bytes and no Microsoft code here. `vectors.tsv` built with `--game-numbers` holds
numeric tokens from the user's own game files and stays in `build/`; the results hold only
outputs of the C runtime for those inputs.
