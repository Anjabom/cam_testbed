"""계약 로더 — 테스트베드가 워크스페이스를 아는 유일한 통로.

이 파일 안에도 white 패키지의 토픽명/필드명은 없다. 전부 YAML 에서 온다.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

_IDX = re.compile(r"^(.*?)\[(-?\d+)\]$")

_MISSING = object()


def resolve(obj, expr):
    """`data[0]`, `linear.x`, `pose.position.z` 같은 경로식을 dict/list 에 적용.

    못 찾으면 _MISSING 을 돌려준다(예외를 던지지 않는다) — 포맷이 바뀌었을 때
    조용히 죽는 대신 "이 경로는 이제 안 맞는다"로 보고하기 위해서다.
    """
    cur = obj
    for token in str(expr).split("."):
        token = token.strip()
        if not token:
            return _MISSING
        # 이름[인덱스][인덱스]... 을 왼쪽부터 벗겨 낸다
        idxs = []
        while True:
            m = _IDX.match(token)
            if not m:
                break
            token, i = m.group(1), int(m.group(2))
            idxs.insert(0, i)
        if token:
            if not isinstance(cur, dict) or token not in cur:
                return _MISSING
            cur = cur[token]
        for i in idxs:
            try:
                cur = cur[i]
            except (IndexError, KeyError, TypeError):
                return _MISSING
    return cur


class Signal:
    """신호 하나 = (토픽, 후보 경로식 목록). 앞에서부터 처음 맞는 경로를 쓴다."""

    __slots__ = ("name", "topic", "paths", "optional", "hit_path", "miss", "tries")

    def __init__(self, name, spec):
        self.name = name
        self.topic = spec["topic"]
        p = spec.get("path", name)
        self.paths = [p] if isinstance(p, str) else list(p)
        self.optional = bool(spec.get("optional", False))
        self.hit_path = None   # 실제로 맞은 경로 (드리프트 진단용)
        self.miss = 0          # 경로가 안 맞은 횟수
        self.tries = 0         # 이 신호의 토픽에서 메시지를 받은 횟수

    def extract(self, msg_dict):
        """맞는 첫 경로의 값. 전부 실패하면 None."""
        self.tries += 1
        # 이전에 맞았던 경로를 먼저 시도 (핫패스)
        order = self.paths
        if self.hit_path is not None:
            order = [self.hit_path] + [p for p in self.paths if p != self.hit_path]
        for p in order:
            v = resolve(msg_dict, p)
            if v is not _MISSING:
                self.hit_path = p
                return v
        self.miss += 1
        return None


class Contract:
    def __init__(self, data, path):
        self.path = str(Path(path).resolve()) if path != "mem" else "mem"
        self.raw = data
        self.name = data.get("name", "unnamed")
        self.version = data.get("version", 0)

        # ── 대상 워크스페이스 ────────────────────────────────────────
        #   테스트베드는 워크스페이스 ★밖★에 있어도 된다. 여기서 지정한 곳의
        #   install/setup.bash 를 source 해서 노드를 띄운다.
        #   상대 경로는 이 계약 파일 위치 기준. 생략하면 계약 파일의 조상 중
        #   install/setup.bash 를 가진 첫 디렉터리를 쓴다(테스트베드가 워크스페이스
        #   안에 있는 흔한 경우를 자동으로 처리).
        self.workspace = self._resolve_ws(data.get("workspace"))
        # 추가로 source 할 setup.bash (다른 오버레이·의존 워크스페이스)
        self.ros_setup = [str(x) for x in (data.get("ros_setup") or [])]
        # true 면 노드를 띄우지 않고 이미 돌고 있는 시스템에 붙어서 관찰만 한다
        self.attach = bool(data.get("attach", False))

        self.nodes = data.get("nodes", [])
        self.stimulus = data.get("stimulus", {})
        self.image_topic = self.stimulus.get("image_topic", "/image_raw")
        self.aux = self.stimulus.get("aux", []) or []
        self.sync_topic = data.get("sync_topic")
        self.observe = list(data.get("observe", []))
        self.signals = {k: Signal(k, v) for k, v in (data.get("signals") or {}).items()}
        self.flag_bits = data.get("flag_bits") or {}
        self.compare_signals = list(data.get("compare_signals") or [])
        self.compare_categorical = list(data.get("compare_categorical") or [])
        self.mirror_odd = list(data.get("mirror_odd_signals") or [])
        self.compare_sequence = list(data.get("compare_sequence") or [])
        self.hold_signals = list(data.get("hold_signals") or [])
        # 뷰어가 띄울 디버그 이미지 토픽 (있으면 눈으로 보며 디버깅 가능)
        self.debug_topics = list(data.get("debug_topics") or [])
        # 이 신호를 실제로 쓰는 받는 쪽 노드들과 그 게이트
        self.consumers = list(data.get("consumers") or [])

    def _resolve_ws(self, raw):
        if raw:
            p = Path(raw).expanduser()
            if not p.is_absolute() and self.path != "mem":
                p = (Path(self.path).parent / p)
            return p.resolve()
        if self.path == "mem":
            return None
        for anc in Path(self.path).parents:
            if (anc / "install" / "setup.bash").exists():
                return anc
        return None

    def setup_files(self):
        """source 해야 할 setup.bash 목록 (워크스페이스 + 추가 오버레이)."""
        out = []
        if self.workspace:
            f = self.workspace / "install" / "setup.bash"
            if f.exists():
                out.append(str(f))
        for x in self.ros_setup:
            f = Path(x).expanduser()
            if not f.is_absolute() and self.path != "mem":
                f = Path(self.path).parent / f
            out.append(str(f.resolve()))
        return out

    # 신호가 참조하는 토픽은 자동으로 관찰 대상에 포함시킨다.
    def topics(self):
        t = list(self.observe)
        for s in self.signals.values():
            if s.topic not in t:
                t.append(s.topic)
        if self.sync_topic and self.sync_topic not in t:
            t.append(self.sync_topic)
        return t

    def signals_by_topic(self):
        out = {}
        for s in self.signals.values():
            out.setdefault(s.topic, []).append(s)
        return out

    def drift_report(self):
        """어느 신호가 어느 경로로 잡혔는지 / 아예 못 잡았는지."""
        rows = []
        for name, s in self.signals.items():
            rows.append({
                "signal": name, "topic": s.topic,
                "declared": s.paths,
                "matched": s.hit_path,
                "misses": s.miss,
                "tries": s.tries,
                "optional": s.optional,
                # ok=첫 경로로 맞음 / fallback=뒤쪽 경로로 맞음(구포맷 호환 중)
                # drift=메시지는 왔는데 어느 경로도 안 맞음 / silent=메시지 미수신
                "status": ("silent" if s.tries == 0 else
                           "drift" if s.hit_path is None else
                           "ok" if (s.hit_path == s.paths[0] and not s.miss) else
                           "fallback"),
            })
        return rows


def load(path):
    p = Path(path)
    with p.open() as f:
        return Contract(yaml.safe_load(f), p)
