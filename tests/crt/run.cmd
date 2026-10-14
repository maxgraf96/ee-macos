@echo off
rem Build and run the msvcrt vectors on Windows (x86 MSVC; run from an
rem "x86 Native Tools Command Prompt" or after vcvars32.bat).
rem   run.cmd vectors.tsv results.tsv
rem A vector that kills the process is recorded as CRASH and the run resumes.
setlocal
if "%~2"=="" (
  echo usage: run.cmd vectors.tsv results.tsv
  exit /b 2
)
cl /nologo /O1 /arch:IA32 /W3 "%~dp0crtvec.c" /Fe:"%~dp0crtvec.exe" /Fo:"%TEMP%\crtvec.obj" || exit /b 1
"%~dp0crtvec.exe" "%~1" "%~2"
set /a tries=0
:again
if %errorlevel%==0 goto done
set /a tries+=1
if %tries% gtr 200 (
  echo giving up after %tries% resumes
  exit /b 1
)
"%~dp0crtvec.exe" "%~1" "%~2" --resume
goto again
:done
echo done: %~2
endlocal
