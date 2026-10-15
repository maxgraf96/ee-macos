#!/bin/bash
# Build innoextract for the app: one self-contained arm64 binary that links
# only libSystem, libc++ and libz, for macOS 13.0 and later. The first-run
# importer runs it on the player's GOG offline installer
# (setup_empire_earth_gold_*.exe [+ *-1.bin]); docs/reference/firstrun.md §3.
#
#     tools/firstrun/innoextract/build.sh [--source upstream|pr210] [--out DIR] [--jobs N] [--keep-work]
#
# Every source is pinned by SHA-256 and downloaded from its upstream:
#   innoextract  --source upstream (default): master 6e9e34e (2025-02-06). 1.9
#                reads Inno Setup only to 6.0.5; master to 6.4.0, with GOG
#                Galaxy fixes. Not 6.4.2+ (measured: 6.4.3, 6.7.3, 7.1.0 fail).
#                --source pr210: dscho/innoextract 376a13e, the open upstream
#                pull request #210 (2026-07-29) on top of master, adding Inno
#                Setup 6.4.2 to 7.0.2. For a GOG installer built with a newer
#                Inno Setup than 6.4.0.                     zlib licence
#   Boost 1.87.0 (iostreams, filesystem, date_time, system,
#                program_options; static)                   Boost Software Licence 1.0
#   xz 5.8.4     (liblzma only; static)                      0BSD (liblzma)
#   bzip2 1.0.8  (static)                                    bzip2 licence (BSD-style)
#   zlib         the system's /usr/lib/libz.1.dylib (every macOS has it)
# Charset conversion is innoextract's built-in one (WITH_CONV=builtin): no
# libiconv. Boost's CMake package names bzip2 as a plain "-lbz2"; the
# -L$PREFIX/lib in front makes that our static libbz2.a, not the SDK's dylib.
#
# Output (default $EE_BUILD_CACHE/innoextract/<id>, EE_BUILD_CACHE defaulting
# to /Users/max/Code/ee-build-cache or ../ee-build-cache beside the repo):
#   bin/innoextract      stripped, ad-hoc signed
#   licenses/            the four licence texts the app must carry
#   build.json           pins, compiler, the binary's SHA-256
# Downloads are cached in $EE_BUILD_CACHE/downloads. Needs the Xcode Command
# Line Tools (clang, the SDK) and CMake: a developer build, never a player's.
set -euo pipefail

SOURCE=upstream
BOOST_VERSION=1.87.0
BOOST_SHA256=3abd7a51118a5dd74673b25e0a3f0a4ab1752d8d618f4b8cea84a603aeecc680
XZ_VERSION=5.8.4
XZ_SHA256=4ce24038fd4221e0d13bc1a2de7a4db56e90b92b3bf75321f6c14be73f65de4b
BZIP2_VERSION=1.0.8
BZIP2_SHA256=ab5a03176ee106d3f0fa90e381da478ddae405918153cca248e682cd0c4a2269
MIN_MACOS=13.0

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
if [ -z "${EE_BUILD_CACHE:-}" ]; then
    if [ -d /Users/max/Code/ee-build-cache ]; then EE_BUILD_CACHE=/Users/max/Code/ee-build-cache
    else EE_BUILD_CACHE="$(dirname "$REPO")/ee-build-cache"; fi
fi
OUT=
JOBS="$(sysctl -n hw.ncpu)"
KEEP=0
while [ $# -gt 0 ]; do
    case "$1" in
        --source) SOURCE="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --jobs) JOBS="$2"; shift 2 ;;
        --keep-work) KEEP=1; shift ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "build.sh: unknown option $1" >&2; exit 2 ;;
    esac
done
case "$SOURCE" in
    upstream) INNOEXTRACT_REPO=dscharrer/innoextract
              INNOEXTRACT_COMMIT=6e9e34ed0876014fdb46e684103ef8c3605e382e
              INNOEXTRACT_SHA256=4d885b27e452dcb137a9d97736b19478447f172bcfed1aa98d80138e9a4c08ae ;;
    pr210)    INNOEXTRACT_REPO=dscho/innoextract
              INNOEXTRACT_COMMIT=376a13e7c41cc5528b6088d0dd16ec1b323a8d37
              INNOEXTRACT_SHA256=d780adf8d9111b61100adbe7f136f2eb94af889e3add0123a51560134516588d ;;
    *) echo "build.sh: --source upstream or pr210" >&2; exit 2 ;;
esac
ID="innoextract-${INNOEXTRACT_COMMIT:0:7}-boost${BOOST_VERSION}-xz${XZ_VERSION}-bz${BZIP2_VERSION}-macos${MIN_MACOS}"
[ -n "$OUT" ] || OUT="$EE_BUILD_CACHE/innoextract/$ID"
DL="$EE_BUILD_CACHE/downloads"
# $CRASHEXIT_DYLIB, if set, is injected into the binaries this script built
# when it runs them (a crash-to-exit shim; DYLD_INSERT_LIBRARIES itself does
# not survive /bin/bash).
RUN=(env)
if [ -n "${CRASHEXIT_DYLIB:-}" ]; then RUN=(env DYLD_INSERT_LIBRARIES="$CRASHEXIT_DYLIB"); fi
WORK="$OUT.work"
PREFIX="$WORK/prefix"
mkdir -p "$DL"
rm -rf "$WORK"
mkdir -p "$WORK/src" "$PREFIX/include" "$PREFIX/lib"

fetch() {  # url sha256 file
    local url="$1" sum="$2" file="$DL/$3"
    if [ -f "$file" ] && [ "$(shasum -a 256 "$file" | cut -d' ' -f1)" = "$sum" ]; then return; fi
    echo "build.sh: downloading $url"
    curl -fL --retry 3 -o "$file.part" "$url"
    local got; got="$(shasum -a 256 "$file.part" | cut -d' ' -f1)"
    if [ "$got" != "$sum" ]; then
        echo "build.sh: $url has SHA-256 $got, expected $sum" >&2; rm -f "$file.part"; exit 1
    fi
    mv "$file.part" "$file"
}

fetch "https://github.com/$INNOEXTRACT_REPO/archive/$INNOEXTRACT_COMMIT.tar.gz" \
      "$INNOEXTRACT_SHA256" "innoextract-$INNOEXTRACT_COMMIT.tar.gz"
fetch "https://github.com/boostorg/boost/releases/download/boost-$BOOST_VERSION/boost-$BOOST_VERSION-b2-nodocs.tar.xz" \
      "$BOOST_SHA256" "boost-$BOOST_VERSION-b2-nodocs.tar.xz"
fetch "https://github.com/tukaani-project/xz/releases/download/v$XZ_VERSION/xz-$XZ_VERSION.tar.xz" \
      "$XZ_SHA256" "xz-$XZ_VERSION.tar.xz"
fetch "https://sourceware.org/pub/bzip2/bzip2-$BZIP2_VERSION.tar.gz" \
      "$BZIP2_SHA256" "bzip2-$BZIP2_VERSION.tar.gz"

SDK="$(xcrun --sdk macosx --show-sdk-path)"
CC="$(xcrun --sdk macosx -f clang)"
CXX="$(xcrun --sdk macosx -f clang++)"
ARCHFLAGS="-arch arm64 -mmacosx-version-min=$MIN_MACOS -isysroot $SDK"
export MACOSX_DEPLOYMENT_TARGET="$MIN_MACOS"
# Nothing from Homebrew or a user's environment may leak into the build.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$(dirname "$(command -v cmake)")"
unset CPATH C_INCLUDE_PATH CPLUS_INCLUDE_PATH LIBRARY_PATH PKG_CONFIG_PATH CFLAGS CXXFLAGS LDFLAGS || true

cd "$WORK/src"
tar xzf "$DL/innoextract-$INNOEXTRACT_COMMIT.tar.gz"
tar xJf "$DL/boost-$BOOST_VERSION-b2-nodocs.tar.xz"
tar xJf "$DL/xz-$XZ_VERSION.tar.xz"
tar xzf "$DL/bzip2-$BZIP2_VERSION.tar.gz"

echo "build.sh: bzip2 $BZIP2_VERSION"
(
    cd "bzip2-$BZIP2_VERSION"
    for f in blocksort huffman crctable randtable compress decompress bzlib; do
        "$CC" $ARCHFLAGS -O2 -D_FILE_OFFSET_BITS=64 -c "$f.c" -o "$f.o"
    done
    ar rcs "$PREFIX/lib/libbz2.a" blocksort.o huffman.o crctable.o randtable.o compress.o decompress.o bzlib.o
    cp bzlib.h "$PREFIX/include/"
)

echo "build.sh: liblzma (xz $XZ_VERSION)"
(
    cd "xz-$XZ_VERSION"
    CC="$CC" CFLAGS="$ARCHFLAGS -O2" ./configure --prefix="$PREFIX" --host=aarch64-apple-darwin \
        --disable-shared --enable-static --disable-xz --disable-xzdec --disable-lzmadec \
        --disable-lzmainfo --disable-lzma-links --disable-scripts --disable-doc --disable-nls \
        > "$WORK/xz-configure.log"
    make -j"$JOBS" > "$WORK/xz-make.log"
    make install > "$WORK/xz-install.log"
)

echo "build.sh: Boost $BOOST_VERSION"
(
    cd "boost-$BOOST_VERSION"
    ./bootstrap.sh --with-toolset=clang \
        --with-libraries=iostreams,filesystem,date_time,system,program_options > "$WORK/boost-bootstrap.log"
    ./b2 -j"$JOBS" -q --prefix="$PREFIX" --layout=system toolset=clang \
        variant=release link=static runtime-link=shared threading=multi \
        architecture=arm address-model=64 \
        cflags="$ARCHFLAGS" cxxflags="$ARCHFLAGS -std=c++17" linkflags="$ARCHFLAGS" \
        -sNO_LZMA=1 -sNO_ZSTD=1 \
        -sBZIP2_INCLUDE="$PREFIX/include" -sBZIP2_LIBPATH="$PREFIX/lib" \
        -sZLIB_INCLUDE="$SDK/usr/include" -sZLIB_LIBPATH="$SDK/usr/lib" \
        install > "$WORK/boost-b2.log"
)

echo "build.sh: innoextract ${INNOEXTRACT_COMMIT:0:12}"
cmake -S "$WORK/src/innoextract-$INNOEXTRACT_COMMIT" -B "$WORK/innoextract-build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" \
    -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_DEPLOYMENT_TARGET="$MIN_MACOS" -DCMAKE_OSX_SYSROOT="$SDK" \
    -DCMAKE_PREFIX_PATH="$PREFIX" -DBoost_ROOT="$PREFIX" \
    -DCMAKE_EXE_LINKER_FLAGS="-L$PREFIX/lib" \
    -DCMAKE_IGNORE_PREFIX_PATH="/opt/homebrew;/usr/local;/opt/local" \
    -DCMAKE_FIND_USE_SYSTEM_ENVIRONMENT_PATH=OFF -DCMAKE_MAKE_PROGRAM="$(command -v make)" \
    -DLZMA_INCLUDE_DIR="$PREFIX/include" -DLZMA_LIBRARY="$PREFIX/lib/liblzma.a" \
    -DBZIP2_INCLUDE_DIR="$PREFIX/include" -DBZIP2_LIBRARIES="$PREFIX/lib/libbz2.a" \
    -DBZIP2_LIBRARY_RELEASE="$PREFIX/lib/libbz2.a" \
    -DUSE_STATIC_LIBS=OFF -DBoost_USE_STATIC_LIBS=ON -DLZMA_USE_STATIC_LIBS=ON \
    -DBZip2_USE_STATIC_LIBS=ON -DZLIB_USE_STATIC_LIBS=OFF \
    -DWITH_CONV=builtin -DUSE_LTO=OFF -DUSE_LD=default -DBUILD_TESTS=ON -DSTRICT_USE=ON \
    > "$WORK/innoextract-cmake.log"
cmake --build "$WORK/innoextract-build" -j"$JOBS" --target innoextract unittest > "$WORK/innoextract-build.log"

BIN="$WORK/innoextract-build/innoextract"
TESTS="$WORK/innoextract-build/unittest"

# Self-contained: only libSystem, libc++ and libz.
LINKED="$(otool -L "$BIN" | tail -n +2 | awk '{print $1}')"
for lib in $LINKED; do
    case "$lib" in
        /usr/lib/libSystem.B.dylib|/usr/lib/libc++.1.dylib|/usr/lib/libz.1.dylib) ;;
        *) echo "build.sh: innoextract links $lib, which a player's Mac may not have" >&2; exit 1 ;;
    esac
done
if [ "$(lipo -archs "$BIN")" != "arm64" ]; then echo "build.sh: not arm64-only" >&2; exit 1; fi
MINOS="$(otool -l "$BIN" | awk '/LC_BUILD_VERSION/{f=1} f&&/minos/{print $2; exit}')"
if [ "$MINOS" != "$MIN_MACOS" ]; then echo "build.sh: minos $MINOS, expected $MIN_MACOS" >&2; exit 1; fi

echo "build.sh: self-test"
"${RUN[@]}" "$TESTS" > "$WORK/innoextract-tests.log" 2>&1 || { tail -20 "$WORK/innoextract-tests.log" >&2; exit 1; }

rm -rf "$OUT"
mkdir -p "$OUT/bin" "$OUT/licenses"
cp "$BIN" "$OUT/bin/innoextract"
strip -x "$OUT/bin/innoextract"
codesign -s - -f "$OUT/bin/innoextract" 2> /dev/null
"${RUN[@]}" "$OUT/bin/innoextract" --version > /dev/null
cp "$WORK/src/innoextract-$INNOEXTRACT_COMMIT/LICENSE" "$OUT/licenses/innoextract-LICENSE.txt"
cp "$WORK/src/boost-$BOOST_VERSION/LICENSE_1_0.txt" "$OUT/licenses/boost-LICENSE_1_0.txt"
cp "$WORK/src/xz-$XZ_VERSION/COPYING" "$OUT/licenses/xz-COPYING.txt"
cp "$WORK/src/xz-$XZ_VERSION/COPYING.0BSD" "$OUT/licenses/xz-COPYING.0BSD.txt"
cp "$WORK/src/bzip2-$BZIP2_VERSION/LICENSE" "$OUT/licenses/bzip2-LICENSE.txt"

SUM="$(shasum -a 256 "$OUT/bin/innoextract" | cut -d' ' -f1)"
cat > "$OUT/build.json" <<EOF
{
 "id": "$ID",
 "binary_sha256": "$SUM",
 "binary_bytes": $(stat -f %z "$OUT/bin/innoextract"),
 "version": "$("${RUN[@]}" "$OUT/bin/innoextract" --version | head -1)",
 "min_macos": "$MIN_MACOS",
 "compiler": "$("$CC" --version | head -1)",
 "sdk": "$(xcrun --sdk macosx --show-sdk-version)",
 "sources": {
  "innoextract": {"repo": "$INNOEXTRACT_REPO", "commit": "$INNOEXTRACT_COMMIT", "sha256": "$INNOEXTRACT_SHA256"},
  "boost": {"version": "$BOOST_VERSION", "sha256": "$BOOST_SHA256"},
  "xz": {"version": "$XZ_VERSION", "sha256": "$XZ_SHA256"},
  "bzip2": {"version": "$BZIP2_VERSION", "sha256": "$BZIP2_SHA256"}
 },
 "links": [$(echo "$LINKED" | sed 's/.*/"&"/' | paste -sd, -)]
}
EOF
[ "$KEEP" = 1 ] || rm -rf "$WORK"
echo "build.sh: $OUT/bin/innoextract ($SUM)"
