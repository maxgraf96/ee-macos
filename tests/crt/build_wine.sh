#!/bin/sh
# Build crtvec.exe on a Mac with LLVM (clang-cl, lld-link, llvm-dlltool) for a dry run
# under Wine. No Windows SDK is needed: the few declarations crtvec.c uses are written
# here, the program links against msvcrt.dll itself, and a tiny entry point stands in
# for the C runtime's start-up. The legion run uses MSVC and run.cmd instead.
#
#   tests/crt/build_wine.sh build/crt/wine
#   (cd build/crt/wine && WINEDEBUG=-all wine crtvec.exe ../vectors.tsv ../results-wine.tsv)
set -eu
OUT=${1:-build/crt/wine}
LLVM=${LLVM:-/opt/homebrew/opt/llvm/bin}
SRC=$(cd "$(dirname "$0")" && pwd)/crtvec.c
mkdir -p "$OUT/inc"
cd "$OUT"
cat > inc/windows.h <<'EOF'
#define NULL ((void *)0)
typedef unsigned long DWORD; typedef void *HANDLE; typedef void *HMODULE; typedef void *LPVOID;
typedef int BOOL; typedef int(__stdcall *FARPROC)(void);
#define WINAPI __stdcall
#define MAX_PATH 260
#define GENERIC_WRITE 0x40000000
#define CREATE_ALWAYS 2
#define INFINITE 0xffffffff
#define EXCEPTION_EXECUTE_HANDLER 1
typedef DWORD(WINAPI *LPTHREAD_START_ROUTINE)(LPVOID);
__declspec(dllimport) HMODULE __stdcall LoadLibraryA(const char *);
__declspec(dllimport) FARPROC __stdcall GetProcAddress(HMODULE, const char *);
__declspec(dllimport) HANDLE __stdcall CreateThread(void *, unsigned, LPTHREAD_START_ROUTINE, LPVOID, DWORD, DWORD *);
__declspec(dllimport) DWORD __stdcall WaitForSingleObject(HANDLE, DWORD);
__declspec(dllimport) BOOL __stdcall CloseHandle(HANDLE);
__declspec(dllimport) DWORD __stdcall GetTempPathA(DWORD, char *);
__declspec(dllimport) HANDLE __stdcall CreateFileA(const char *, DWORD, DWORD, void *, DWORD, DWORD, HANDLE);
__declspec(dllimport) BOOL __stdcall WriteFile(HANDLE, const void *, DWORD, DWORD *, void *);
__declspec(dllimport) BOOL __stdcall DeleteFileA(const char *);
unsigned long __cdecl _exception_code(void);
#define GetExceptionCode _exception_code
EOF
cat > inc/stdio.h <<'EOF'
#define NULL ((void *)0)
typedef unsigned int size_t;
typedef struct { char *p; int cnt; char *base; int flag; int file; int cb; int bs; char *tmp; } FILE;
__declspec(dllimport) FILE *fopen(const char *, const char *);
__declspec(dllimport) int fclose(FILE *);
__declspec(dllimport) char *fgets(char *, int, FILE *);
__declspec(dllimport) size_t fwrite(const void *, size_t, size_t, FILE *);
__declspec(dllimport) int fflush(FILE *);
__declspec(dllimport) int fprintf(FILE *, const char *, ...);
__declspec(dllimport) int sprintf(char *, const char *, ...);
__declspec(dllimport) extern FILE _iob[];
#define stderr (&_iob[2])
EOF
cat > inc/stdlib.h <<'EOF'
typedef unsigned int size_t; typedef unsigned int uintptr_t;
__declspec(dllimport) void exit(int);
__declspec(dllimport) int atoi(const char *);
__declspec(dllimport) long atol(const char *);
__declspec(dllimport) unsigned long strtoul(const char *, char **, int);
__declspec(dllimport) unsigned __int64 _strtoui64(const char *, char **, int);
EOF
cat > inc/string.h <<'EOF'
typedef unsigned int size_t;
__declspec(dllimport) void *memcpy(void *, const void *, size_t);
__declspec(dllimport) void *memset(void *, int, size_t);
__declspec(dllimport) size_t strlen(const char *);
__declspec(dllimport) int strcmp(const char *, const char *);
__declspec(dllimport) char *strchr(const char *, int);
__declspec(dllimport) char *strcat(char *, const char *);
EOF
cat > crt0.c <<'EOF'
typedef struct { int newmode; } startinfo;
__declspec(dllimport) int __getmainargs(int *, char ***, char ***, int, startinfo *);
__declspec(dllimport) void exit(int);
int main(int, char **);
void mainCRTStartup(void) {
    int argc; char **argv, **env; startinfo si = {0};
    __getmainargs(&argc, &argv, &env, 0, &si);
    exit(main(argc, argv));
}
int _fltused = 0;
EOF
printf 'LIBRARY kernel32.dll\nEXPORTS\nLoadLibraryA@4\nGetProcAddress@8\nCreateThread@24\nWaitForSingleObject@8\nCloseHandle@4\nGetTempPathA@8\nCreateFileA@28\nWriteFile@20\nDeleteFileA@4\n' > kernel32.def
printf 'LIBRARY msvcrt.dll\nEXPORTS\nfopen\nfclose\nfgets\nfwrite\nfflush\nfprintf\nsprintf\n_iob DATA\nexit\natoi\natol\nstrtoul\n_strtoui64\nmemcpy\nmemset\nstrlen\nstrcmp\nstrchr\nstrcat\n__getmainargs\n_except_handler3\n_exception_code\n' > msvcrt.def
printf 'LIBRARY ntdll.dll\nEXPORTS\n_chkstk\n' > ntdll.def
"$LLVM/llvm-dlltool" -m i386 -k -d kernel32.def -l kernel32.lib
"$LLVM/llvm-dlltool" -m i386 -d msvcrt.def -l msvcrt.lib
"$LLVM/llvm-dlltool" -m i386 -d ntdll.def -l ntdll.lib
"$LLVM/clang-cl" --target=i686-pc-windows-msvc /c /O1 /GS- -fms-extensions /X -Iinc -Wno-macro-redefined /Tc "$SRC" /Focrtvec.obj
"$LLVM/clang-cl" --target=i686-pc-windows-msvc /c /O1 /GS- /X /Tc crt0.c /Focrt0.obj
"${LLD_LINK:-lld-link}" /nologo /entry:mainCRTStartup /subsystem:console /nodefaultlib /safeseh:no \
    crtvec.obj crt0.obj kernel32.lib msvcrt.lib ntdll.lib /out:crtvec.exe
echo "$OUT/crtvec.exe"
