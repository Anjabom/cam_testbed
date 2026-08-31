"""프레임 수확 — 실패한 프레임만 골라 원본에서 뽑아낸다.

★능동 학습의 입구★
전부 라벨링하는 대신 ★게이트에서 탈락한 프레임만★ 골라 라벨링하면 데이터 효율이
크게 오른다. 어느 프레임이 왜 탈락했는지는 이미 signals.csv 에 있고,
frame 번호로 원본 영상에서 그대로 꺼낼 수 있다.

조건식은 계약과 같은 안전한 평가기를 쓴다(AST 화이트리스트).

    python3 -m tb.run harvest --run <런> --where "int(flags) % 4 >= 2" --out dataset/
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .expr import evaluate


def read_signals(run_dir):
    rows = []
    p = Path(run_dir) / "signals.csv"
    if not p.exists():
        return rows
    with p.open() as f:
        for r in csv.DictReader(f):
            o = {}
            for k, v in r.items():
                if v == "" or v is None:
                    o[k] = None
                    continue
                try:
                    o[k] = float(v)
                except ValueError:
                    o[k] = v
            rows.append(o)
    return rows


def select(rows, where):
    """조건에 맞는 행만. 조건이 없으면 전부."""
    if not where:
        return list(rows)
    out = []
    for r in rows:
        if evaluate(where, r) is True:
            out.append(r)
    return out


def source_video(run_dir):
    """이 런이 쓴 원본 영상 경로."""
    sj = Path(run_dir) / "summary.json"
    if not sj.exists():
        return None
    try:
        meta = json.loads(sj.read_text())["summary"]["meta"]
    except Exception:      # noqa: BLE001
        return None
    v = meta.get("video")
    return v if v and Path(v).exists() else None


def effective_params(run_dir):
    """이 런에서 노드가 ★실제로 들고 있던★ 파라미터. [2026-08-25]

    앞에서부터 처음 있는 것을 쓴다:
      ① `params_actual.yaml`      — `ros2 param dump` 로 뜬 ★실효값★
      ② `summary.json` 의 meta    — 테스트베드가 ★요청한★ 값
      (둘 다 없으면 {} → 그리는 쪽이 계약의 default 로 떨어진다)

    ★①이 필요한 이유★ ②에는 시나리오·local.yaml 이 적어 준 것만 들어 있다.
    아무도 안 준 파라미터는 노드가 자기 기본값으로 도는데, 그 기본값은 계약이
    적어 둔 default 와 ★얼마든지 어긋난다★ — 워크스페이스 코드가 바뀌면 계약은
    가만히 있어도 낡기 때문이다. 실제로 night_b 런에서 사다리꼴이 어긋나
    거리선이 205px(≈1.2m) 먼 곳에 얹혔다(그때 판정값 자체는 멀쩡했다).

    ★과거 런도 이걸로 되살아난다★ `params_actual.yaml` 이 런 디렉터리에 남아
    있으므로, 다시 돌릴 필요 없이 그리기만 다시 하면 옳은 그림이 나온다.
    """
    import yaml                                      # noqa: PLC0415
    rd = Path(run_dir)
    pa = rd / "params_actual.yaml"
    if pa.exists():
        try:
            got = (yaml.safe_load(pa.read_text()) or {}).get("params")
            if got:
                return got
        except Exception:      # noqa: BLE001
            pass               # 깨진 덤프 하나로 그림을 통째로 잃지 않는다
    sj = rd / "summary.json"
    if sj.exists():
        try:
            return json.loads(sj.read_text())["summary"]["meta"].get("params") or {}
        except Exception:      # noqa: BLE001
            pass
    return {}


def grab(video, frames, out_dir, prefix="f", width=0, undistort=None):
    """원본 영상에서 프레임들을 뽑아 PNG 로 저장. 반환: 저장된 경로 목록."""
    import cv2
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"[harvest] 영상을 열 수 없다: {video}")
    saved = []
    for n in sorted(set(int(f) for f in frames)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, n)
        ok, img = cap.read()
        if not ok:
            continue
        if undistort is not None:
            img = undistort(img)
        if width and img.shape[1] > width:
            s = width / float(img.shape[1])
            img = cv2.resize(img, (width, int(round(img.shape[0] * s))))
        p = out_dir / f"{prefix}{n:06d}.png"
        cv2.imwrite(str(p), img)
        saved.append(p)
    cap.release()
    return saved


def summarize(rows, contract=None):
    """수확 후보를 플래그별로 세어 준다 — 무엇을 라벨링할지 정하는 데 쓴다."""
    fb = (contract.flag_bits if contract else {}) or {}
    sig = fb.get("signal", "flags")
    bits = {int(k): v for k, v in (fb.get("bits") or {}).items()}
    counts = {}
    for r in rows:
        v = r.get(sig)
        if not isinstance(v, (int, float)):
            continue
        iv = int(v)
        if iv == 0:
            counts["CLEAN"] = counts.get("CLEAN", 0) + 1
        for bit, name in bits.items():
            if iv & bit:
                counts[name] = counts.get(name, 0) + 1
    return counts
