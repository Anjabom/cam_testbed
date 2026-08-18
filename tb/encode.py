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
    if not _has_encoder("libx264"):
        return p
    with _xcode_lock:                       # 동시 요청이 같은 파일을 두 번 굽지 않게
        if out.is_file() and out.stat().st_mtime >= p.stat().st_mtime:
            return out
        tmp = out.with_suffix(".part.mp4")
        cmd = [_ffmpeg(), "-y", "-v", "error", "-i", str(p),
               "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart",
               "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", str(tmp)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0:
                tmp.replace(out)
                return out
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            tmp.unlink(missing_ok=True)
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
