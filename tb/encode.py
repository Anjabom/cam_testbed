"""브라우저에서 ★재생되는★ 영상 쓰기 — OpenCV 기본 코덱은 안 된다.

cv2.VideoWriter 의 기본값 `mp4v` 는 MPEG-4 Part 2 다. 파일 자체는 멀쩡하고
VLC·mpv 로는 잘 열리지만 브라우저는 재생하지 못한다 — Chrome/Firefox 가
mp4 컨테이너에서 받아 주는 코덱은 H.264(avc1)·AV1 뿐이다. 웹앱의 <video>
가 아무 오류 없이 검은 화면으로만 있던 원인이 정확히 이것이다.

그런데 pip 로 받은 opencv 에는 H.264 ★인코더★가 없다(libx264 가 GPL 이라
빼고 빌드한다). 확인해 보면 avc1/H264/X264 는 전부 열리지 않는다. 그래서
아래 순서로 내려간다:

  1. 시스템 ffmpeg 에 raw 프레임을 파이프 → H.264/mp4   ← 제일 좋다(작고 빠름)
  2. cv2 로 VP9 또는 VP8 → webm                        ← ffmpeg 이 없을 때
  3. cv2 로 mp4v → mp4                                 ← 마지막 수단(브라우저 X)

★호출한 쪽은 writer.path 를 다시 읽어야 한다★ — 2번으로 떨어지면 확장자가
webm 으로 바뀐다.

이미 만들어 둔 mp4v 파일은 web_path() 가 처음 요청될 때 한 번만 H.264 로
변환해 옆에 캐시해 둔다 — 예전 런을 다시 돌리지 않아도 웹앱에서 보인다.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

import cv2
import numpy as np

# 브라우저가 mp4 안에서 재생할 수 있는 코덱 태그
_WEB_TAGS = {"avc1", "h264", "av01"}
_WEB_EXT = {".webm"}          # 우리가 webm 에 쓰는 것은 VP8/VP9 뿐이다

_probe_cache: dict = {}
_probe_lock = threading.Lock()
_xcode_lock = threading.Lock()


# ── ffmpeg 유무 ────────────────────────────────────────────────────────
def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _has_encoder(name: str) -> bool:
    """ffmpeg 이 그 인코더로 빌드돼 있는가. 배포판마다 다르다."""
    exe = _ffmpeg()
    if not exe:
        return False
    key = ("enc", name)
    with _probe_lock:
        if key in _probe_cache:
            return _probe_cache[key]
    try:
        out = subprocess.run([exe, "-hide_banner", "-encoders"],
                             capture_output=True, text=True, timeout=15).stdout
        ok = any(line.split()[1:2] == [name] for line in out.splitlines())
    except (OSError, subprocess.SubprocessError):
        ok = False
    with _probe_lock:
        _probe_cache[key] = ok
    return ok


#  ★GPU 인코더가 있으면 그것을 쓴다★ 실측(RTX 4060 Laptop · 1920×1080):
#  libx264 veryfast 는 실시간의 2~3배, h264_nvenc 는 ★14배★ 다(30초 구간 2.2초).
#  10분짜리 2.2GB 주행영상이 40초에 끝나느냐 5분이 걸리느냐의 차이라, 변환을
#  「나중에 해야 하는 일」이 아니라 「그 자리에서 해도 되는 일」로 만든다.
#  ⚠️ NVENC 은 드라이버·GPU 점유 상황에 따라 실행 시점에 실패할 수 있다 —
#     그래서 고르기만 하고, 실제로 실패하면 부르는 쪽이 libx264 로 내려간다.
def h264_args(gpu: bool = True):
    """→ (인코더 이름, ffmpeg 인자 리스트). 화질은 둘이 비슷하게 맞춰 두었다."""
    if gpu and _has_encoder("h264_nvenc"):
        return "h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "26"]
    return "libx264", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]


def capability() -> str:
    """지금 이 머신이 쓸 수 있는 최선. doctor 가 사람에게 보여 준다."""
    if _has_encoder("libx264"):
        return "ffmpeg/H.264"
    if _has_encoder("libvpx-vp9"):
        return "ffmpeg/VP9"
    if _cv2_tag_ok("vp09") or _cv2_tag_ok("VP80"):
        return "cv2/webm"
    return "cv2/mp4v(브라우저 재생 불가)"


def _cv2_tag_ok(tag: str) -> bool:
    key = ("cv2", tag)
    with _probe_lock:
        if key in _probe_cache:
            return _probe_cache[key]
    import tempfile
    ext = ".webm" if tag in ("vp09", "VP80") else ".mp4"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
        p = Path(f.name)
    try:
        w = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*tag), 10, (64, 48))
        ok = w.isOpened()
        if ok:
            w.write(np.zeros((48, 64, 3), np.uint8))
        w.release()
    except cv2.error:
        ok = False
    finally:
        p.unlink(missing_ok=True)
    with _probe_lock:
        _probe_cache[key] = ok
    return ok


# ── 코덱 판별 ──────────────────────────────────────────────────────────
def fourcc_of(path) -> str:
    p = Path(path)
    cap = cv2.VideoCapture(str(p))
    v = int(cap.get(cv2.CAP_PROP_FOURCC))
    cap.release()
    return "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00 ").strip()


def is_web_playable(path) -> bool:
    """이 파일을 <video> 태그가 재생할 수 있는가."""
    p = Path(path)
    if not p.is_file():
        return False
    if p.suffix.lower() in _WEB_EXT:
        return True
    st = p.stat()
    key = (str(p), st.st_mtime_ns, st.st_size)
    with _probe_lock:
        if key in _probe_cache:
            return _probe_cache[key]
    ok = fourcc_of(p).lower() in _WEB_TAGS
    with _probe_lock:
        _probe_cache[key] = ok
    return ok


def web_path(path) -> Path:
    """재생 가능한 파일 경로. 필요하면 한 번만 변환해 옆에 캐시한다.

    변환할 수단이 없으면 원본을 그대로 돌려준다 — 그 경우 웹앱은
    "코덱 때문에 재생할 수 없다"고 사람에게 말해야 한다(app.js).
    """
    p = Path(path)
    if is_web_playable(p):
        return p
    out = p.with_name(p.stem + "__web.mp4")
    if out.is_file() and out.stat().st_mtime >= p.stat().st_mtime:
        return out
    if not _has_encoder("libx264") and not _has_encoder("h264_nvenc"):
        return p
    with _xcode_lock:                       # 동시 요청이 같은 파일을 두 번 굽지 않게
        if out.is_file() and out.stat().st_mtime >= p.stat().st_mtime:
            return out
        tmp = out.with_suffix(".part.mp4")
        #  ★GPU 먼저, 실패하면 CPU★ NVENC 은 빌드돼 있어도 그 순간 드라이버가
        #  안 잡히거나 세션이 꽉 차면 실행에 실패한다. 그때 조용히 포기하면
        #  「변환됐겠지」 하고 원본을 열어 또 검은 화면을 보게 된다.
        for gpu in (True, False):
            enc, vargs = h264_args(gpu)
            if not _has_encoder(enc):
                continue
            cmd = ([_ffmpeg(), "-y", "-v", "error", "-i", str(p), "-an"] + vargs
                   + ["-pix_fmt", "yuv420p", "-movflags", "+faststart",
                      "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", str(tmp)])
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
                if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0:
                    tmp.replace(out)
                    return out
            except (OSError, subprocess.SubprocessError):
                pass
            finally:
                tmp.unlink(missing_ok=True)
            if not gpu:
                break
    return p


# ── 쓰기 ───────────────────────────────────────────────────────────────
class Writer:
    """프레임을 받아 브라우저가 재생할 수 있는 파일로 쓴다.

    path 로 준 확장자는 ★희망 사항★이다. 실제로 쓴 경로는 .path 에 있다.
    """

    def __init__(self, path, fps: float, size, quiet: bool = False):
        self.want = Path(path)
        self.fps = float(fps) or 10.0
        self.w, self.h = int(size[0]), int(size[1])
        self.kind = ""
        self.path = self.want
        self.proc = None
        self.cv = None
        self._broken = False
        self._open(quiet)

    def _open(self, quiet):
        if self._open_ffmpeg("libx264", ".mp4", ["-c:v", "libx264", "-preset", "veryfast",
                                                 "-crf", "23", "-movflags", "+faststart"]):
            self.kind = "ffmpeg/H.264"
        elif self._open_ffmpeg("libvpx-vp9", ".webm", ["-c:v", "libvpx-vp9", "-b:v", "0",
                                                       "-crf", "34", "-row-mt", "1"]):
            self.kind = "ffmpeg/VP9"
        elif self._open_cv2("vp09", ".webm"):
            self.kind = "cv2/VP9"
        elif self._open_cv2("VP80", ".webm"):
            self.kind = "cv2/VP8"
        elif self._open_cv2("mp4v", ".mp4"):
            self.kind = "cv2/mp4v"
            if not quiet:
                print("[encode] ⚠ H.264/VP9 인코더가 없어 mp4v 로 쓴다 — "
                      "브라우저에서는 재생되지 않는다. `sudo apt install ffmpeg` 로 해결.")
        else:
            raise RuntimeError(f"영상을 열 수 없다: {self.want}")

    def _open_ffmpeg(self, enc, ext, vargs):
        if not _has_encoder(enc):
            return False
        out = self.want.with_suffix(ext)
        # H.264/VP9 은 yuv420p 에서 홀수 해상도를 못 쓴다 → 짝수로 패딩
        cmd = [_ffmpeg(), "-y", "-v", "error",
               "-f", "rawvideo", "-pix_fmt", "bgr24",
               "-s", f"{self.w}x{self.h}", "-r", f"{self.fps}",
               "-i", "-", "-an", *vargs,
               "-pix_fmt", "yuv420p",
               "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", str(out)]
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE)
        except OSError:
            self.proc = None
            return False
        self.path = out
        return True

    def _open_cv2(self, tag, ext):
        out = self.want.with_suffix(ext)
        try:
            w = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*tag),
                                self.fps, (self.w, self.h))
        except cv2.error:
            return False
        if not w.isOpened():
            w.release()
            return False
        self.cv, self.path = w, out
        return True

    def write(self, img):
        if img.shape[1] != self.w or img.shape[0] != self.h:
            img = cv2.resize(img, (self.w, self.h))
        if self.proc is not None:
            if self._broken:
                return
            try:
                self.proc.stdin.write(np.ascontiguousarray(img).tobytes())
            except (BrokenPipeError, OSError, ValueError):
                self._broken = True
        elif self.cv is not None:
            self.cv.write(img)

    def release(self):
        if self.proc is not None:
            try:
                if self.proc.stdin and not self.proc.stdin.closed:
                    self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            if self.proc.returncode not in (0, None):
                err = (self.proc.stderr.read() or b"").decode(errors="replace")[-400:]
                print(f"[encode] ⚠ ffmpeg 종료코드 {self.proc.returncode}: {err}")
            self.proc = None
        if self.cv is not None:
            self.cv.release()
            self.cv = None


# ══════════════════════════════════════════════════════════════════════
#  CLI — 이미 있는 영상을 브라우저가 여는 형태로 바꾼다
# ══════════════════════════════════════════════════════════════════════
#  ★왜 명령이 필요해졌나★ [2026-09-06]
#  보정 스튜디오가 정적 페이지가 되면서 서버가 없어졌다. 예전에는 서버가 파일을
#  열어 주기 전에 web_path() 를 대신 불러 줬는데, 이제 브라우저는 파일을 직접
#  열고 ★재생 못 하면 그냥 못 연다★. 그래서 사람이 부를 자리를 만든다.
#
#  녹화가 mp4v(MPEG-4 Part 2)로 굽는 한 이 일은 계속 필요하다 —
#  브라우저가 mp4 안에서 받아 주는 것은 H.264(avc1)·AV1 뿐이다.
_VIDEO_EXT = {".mp4", ".avi", ".mkv", ".mov", ".m4v", ".webm"}


def _mb(p) -> float:
    try:
        return Path(p).stat().st_size / 1e6
    except OSError:
        return 0.0


def main(argv=None) -> int:
    import argparse
    import time as _time

    ap = argparse.ArgumentParser(
        prog="python3 -m tb.encode",
        description="영상을 브라우저가 재생하는 H.264 mp4 로 바꾼다 "
                    "(원본은 건드리지 않고 <이름>__web.mp4 를 옆에 만든다)")
    ap.add_argument("paths", nargs="+", help="영상 파일 또는 폴더")
    ap.add_argument("--cpu", action="store_true",
                    help="GPU(NVENC) 를 쓰지 않는다 — 결과를 다른 기계와 맞출 때")
    ap.add_argument("--force", action="store_true", help="이미 만든 것도 다시 굽는다")
    a = ap.parse_args(argv)

    if not _ffmpeg():
        print("⛔ ffmpeg 이 없다 — sudo apt install ffmpeg")
        return 2

    files = []
    for raw in a.paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            files += sorted(q for q in p.iterdir()
                            if q.suffix.lower() in _VIDEO_EXT and "__web" not in q.stem)
        elif p.is_file():
            files.append(p)
        else:
            print(f"⛔ 없는 경로: {p}")
            return 2

    enc, _ = h264_args(not a.cpu)
    print(f"인코더 {enc}  ·  대상 {len(files)}개")
    rc = 0
    for p in files:
        if is_web_playable(p) and not a.force:
            print(f"  · {p.name} — 이미 브라우저가 연다({fourcc_of(p) or p.suffix}), 건너뜀")
            continue
        out = p.with_name(p.stem + "__web.mp4")
        if a.force:
            out.unlink(missing_ok=True)
        t0 = _time.time()
        #  ★web_path() 를 그대로 쓴다★ 변환 규칙(faststart·짝수 크기·GPU→CPU 폴백)이
        #  한 곳에만 있어야 화면에서 여는 파일과 여기서 만든 파일이 같다.
        got = web_path(p) if not a.cpu else _cpu_only(p)
        dt = _time.time() - t0
        if Path(got) == p:
            print(f"  ⛔ {p.name} — 변환 실패(코덱·디스크·권한을 볼 것)")
            rc = 1
            continue
        print(f"  ✅ {Path(got).name}  {_mb(p):.0f}MB → {_mb(got):.0f}MB  {dt:.1f}초")
    return rc


def _cpu_only(p):
    """--cpu 일 때만 쓰는 우회 — NVENC 을 아예 후보에서 뺀다."""
    global _probe_cache
    with _probe_lock:
        saved = _probe_cache.get(("enc", "h264_nvenc"))
        _probe_cache[("enc", "h264_nvenc")] = False
    try:
        return web_path(p)
    finally:
        with _probe_lock:
            if saved is None:
                _probe_cache.pop(("enc", "h264_nvenc"), None)
            else:
                _probe_cache[("enc", "h264_nvenc")] = saved


if __name__ == "__main__":
    import sys
    sys.exit(main())
