#!/usr/bin/env bash
# web/vendor/mpeg4dec.js 를 굽는다 — FFmpeg 의 mpeg4 디코더 하나만 wasm 으로.
#
#   tools/build_mpeg4dec.sh [작업폴더]      (기본: /tmp/mpeg4dec-build)
#
# 필요한 것: git, make, 인터넷(첫 1회 — emsdk 와 FFmpeg 소스를 받는다).
# 결과물은 저장소에 같이 넣는다 — 페이지는 CDN 없이 돌아야 한다(대회 현장).
#
# ★SINGLE_FILE★ wasm 을 base64 로 js 안에 묻는다. 그래야 index.html 을 파일로
# 열어도(file://) 돈다 — 따로 둔 .wasm 은 file:// 에서 fetch 가 막힌다.
# ★LGPL★ mpeg4 디코더는 LGPL 이다. --enable-gpl 을 켜지 않는다.
set -euo pipefail

EMSDK_VER=6.0.11
FFMPEG_TAG=n7.1
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
WORK="${1:-/tmp/mpeg4dec-build}"
mkdir -p "$WORK"
cd "$WORK"

[ -d emsdk ] || git clone --depth 1 https://github.com/emscripten-core/emsdk.git
(cd emsdk && ./emsdk install "$EMSDK_VER" && ./emsdk activate "$EMSDK_VER") >/dev/null
# shellcheck disable=SC1091
source emsdk/emsdk_env.sh >/dev/null

[ -d ffmpeg ] || git clone --depth 1 --branch "$FFMPEG_TAG" https://github.com/FFmpeg/FFmpeg.git ffmpeg
cd ffmpeg
if [ ! -f libavcodec/libavcodec.a ]; then
  emconfigure ./configure \
    --cc=emcc --cxx=em++ --ar=emar --ranlib=emranlib --nm=emnm \
    --target-os=none --arch=x86_32 --enable-cross-compile \
    --disable-x86asm --disable-inline-asm --disable-stripping \
    --disable-programs --disable-doc --disable-debug --disable-autodetect \
    --disable-everything --disable-network --disable-pthreads \
    --disable-avdevice --disable-avformat --disable-avfilter \
    --disable-swresample --disable-swscale --disable-postproc \
    --enable-decoder=mpeg4 \
    --extra-cflags="-O3"
  emmake make -j"$(nproc)"
fi

mkdir -p "$ROOT/web/vendor"
emcc -O3 -I. "$HERE/mpeg4dec/mpeg4dec.c" libavcodec/libavcodec.a libavutil/libavutil.a \
  -s MODULARIZE=1 -s EXPORT_NAME=Mpeg4Dec -s SINGLE_FILE=1 \
  -s ALLOW_MEMORY_GROWTH=1 -s ENVIRONMENT=web,worker,node \
  -s EXPORTED_FUNCTIONS=_malloc,_free \
  -s EXPORTED_RUNTIME_METHODS=HEAPU8 \
  -o "$ROOT/web/vendor/mpeg4dec.js"

ls -l "$ROOT/web/vendor/mpeg4dec.js"
