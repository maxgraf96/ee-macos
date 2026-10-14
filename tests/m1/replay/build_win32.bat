@echo off
rem Build rmg_replay.exe (32-bit) with MSVC. Run from an "x86 Native Tools Command Prompt for VS"
rem (VS 2022 Build Tools, C++ workload; or: "%VCINSTALLDIR%Auxiliary\Build\vcvarsall.bat" x86).
rem /LARGEADDRESSAWARE: the game is LAA, so captured addresses can lie above 2 GB.
rem setargv.obj: expand *.rrp wildcards on the command line.
rem /DYNAMICBASE: lets the parent retry when the child's own stack or heap lands on a needed range.
setlocal
cd /d "%~dp0"
cl /nologo /O2 /W3 /MT /D_CRT_SECURE_NO_WARNINGS rmg_replay_win32.c rmg_replay_core.c setargv.obj /Fe:rmg_replay.exe ^
   /link /LARGEADDRESSAWARE /DYNAMICBASE
if errorlevel 1 exit /b 1
echo built %~dp0rmg_replay.exe
