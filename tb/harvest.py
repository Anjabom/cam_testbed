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
