"""분석 — JSONL 원기록 → 신호 테이블 → 지표/불변식/회귀비교.

여기에도 white 의 토픽·필드 이름은 없다. 전부 Contract 를 통해 온다.

★GT 가 없을 때 무엇을 근거로 판정하는가★
  1) 회귀(golden) : 같은 영상 · 같은 파라미터에서 이전 버전과 값이 같은가.
                    코드 변경의 부작용을 잡는다. GT 불필요.
  2) 불변식       : 진실을 몰라도 반드시 성립해야 하는 성질
                    (차선폭은 거의 일정하다 / θ 는 프레임 간 튀지 않는다 …)
  3) 섭동 대조    : 같은 장면을 밝기·블러·압축만 바꿔 넣었을 때
                    출력이 얼마나 무너지는가 = 강건성. 역시 GT 불필요.
"""
from __future__ import annotations

import csv
import json
import math
import re
import statistics as st
from pathlib import Path

NUM = (int, float)


# ══════════════════════════════════════════════════════════════════════
#  JSONL → 프레임별 신호 테이블
# ══════════════════════════════════════════════════════════════════════
def build_table(jsonl_path, contract, discard_first=0):
    """프레임 번호를 행 키로 삼아 신호를 모은다.

    한 프레임 안에 같은 토픽이 여러 번 오면 마지막 값을 쓴다.
    lockstep 재생에서는 프레임 1개 = 처리 사이클 1개가 보장된다.

    discard_first: 앞의 N행을 버린다. YOLO 는 첫 predict 에서 지연 로딩되므로
      첫 몇 프레임의 지연이 수십 초로 찍혀 통계를 오염시킨다.
    """
    by_topic = contract.signals_by_topic()
    rows = {}
    n_lines = 0
    with open(jsonl_path) as f:
        for line in f:
            if not line.strip():
                continue
            n_lines += 1
            rec = json.loads(line)
            frame = rec.get("frame", -1)
            if frame < 0:
                continue
            sigs = by_topic.get(rec["topic"])
            if not sigs:
                continue
            r = rows.setdefault(frame, {"frame": frame, "_t": [], "_tf": rec.get("t_frame", 0.0)})
            r["_t"].append(rec["t"])
            for s in sigs:
                v = s.extract(rec["msg"])
                if v is not None:
                    r[s.name] = v

    out = []
    order = sorted(rows)
    # 상태 신호는 다음 발행까지 값이 유지된다 — 앞 값으로 채운다.
    # 채우기는 버리기 전에 해야 워밍업 구간의 값도 이어진다.
    #  계약이 선언한 '발행 전 값'으로 시작한다(contract.hold_initial 주석 참고)
    held = dict(getattr(contract, "hold_initial", None) or {})
    for frame in order:
        r = rows[frame]
        for name in contract.hold_signals:
            if name in r:
                held[name] = r[name]
            elif name in held:
                r[name] = held[name]
    if discard_first:
        order = order[discard_first:]
    for frame in order:
        r = rows[frame]
        ts = r.pop("_t")
        tf = r.pop("_tf")
        r["latency_ms"] = (max(ts) - tf) * 1000.0 if tf else float("nan")
        out.append(r)
    return out, n_lines


def write_csv(rows, contract, path):
    cols = ["frame", "latency_ms"] + list(contract.signals.keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return cols


def read_csv(path):
    rows = []
    with open(path) as f:
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


# ══════════════════════════════════════════════════════════════════════
#  통계 도우미
# ══════════════════════════════════════════════════════════════════════
def _nums(rows, key):
    out = []
    for r in rows:
        v = r.get(key)
        if isinstance(v, NUM) and not (isinstance(v, float) and math.isnan(v)):
            out.append(float(v))
    return out


def _p(vals, q):
    if not vals:
        return float("nan")
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def _abs_diffs(rows, key):
    vals, prev = [], None
    for r in rows:
        v = r.get(key)
        if not isinstance(v, NUM):
            prev = None
            continue
        if prev is not None:
            vals.append(abs(float(v) - prev))
        prev = float(v)
    return vals


def _steps(rows, key):
    """프레임 간 ★부호 있는★ 변화. _abs_diffs 와 달리 방향을 남긴다."""
    vals, prev = [], None
    for r in rows:
        v = r.get(key)
        if not isinstance(v, NUM):
            prev = None
            continue
        if prev is not None:
            vals.append(float(v) - prev)
        prev = float(v)
    return vals


def flag_names(contract):
    fb = contract.flag_bits or {}
    return fb.get("signal"), {int(k): v for k, v in (fb.get("bits") or {}).items()}


def valid_rows(rows, contract):
    """'차선 없음' 이 아닌 행만. 플래그 정의가 없으면 전부."""
    sig, bits = flag_names(contract)
    if not sig:
        return rows
    novalid = [b for b, n in bits.items() if n == "NO_LANE"]
    if not novalid:
        return rows
    m = novalid[0]
    return [r for r in rows if isinstance(r.get(sig), NUM) and not (int(r[sig]) & m)]


# ══════════════════════════════════════════════════════════════════════
#  구간 · 전이 · 로그 — ★언제 물었나★ 를 재는 도구
# ══════════════════════════════════════════════════════════════════════
#  평균과 분위수로는 '단계가 언제 올라갔나'를 말할 수 없다. 단계적으로 개입하는
#  노드(예비제동 → 확정 정지처럼)를 판정하려면 재야 할 것이 셋이다:
#    ① 조건이 참인 구간이 얼마나 이어졌나      → spans / span_stats
#    ② 값이 언제 어느 값에서 어느 값으로 바뀌었나 → transitions / find_events
#    ③ 그 순간 ★다른 신호★ 가 얼마였나          → find_events + rows[i]
#  셋 다 여기 있고, 어느 것에도 워크스페이스의 신호 이름은 없다 — 조건식과 전이
#  규격은 전부 시나리오의 checks: 와 계약이 준다.
#
#  ★시간은 벽시계가 아니라 영상 시간이다★ lockstep 재생에서 벽시계는 기계 속도에
#  좌우되지만 프레임 번호는 ★장면 안의 시간★ 이다. 그래서 초 단위 값은 전부
#  (프레임 차이 ÷ 영상 fps) 로 잰다 — 실차에서 실제로 흐른 시간과 같다.
# ══════════════════════════════════════════════════════════════════════
def scene_fps(meta, contract=None):
    """초 단위 판정에 쓸 fps — ★노드가 겪은 시간★ 의 기준이다.

    영상 fps × 배속(rate). 배속을 곱하는 이유는, 재는 대상이 대개 ★노드 안의 시간
    상수★(확정 시간·신선도·대기 상한 같은 것)라서다. 영상을 반 배속으로 밀면 노드가
    보기에는 두 배로 느리게 다가온 것이고, 판정도 그 시간으로 해야 맞는다.
    (lockstep 은 배속을 쓰지 않으므로 rate 는 1 이고 프레임 시간 = 장면 시간이다.)
    """
    rate = 1.0
    try:
        rate = float((meta or {}).get("rate") or 1.0) or 1.0
    except (TypeError, ValueError):
        rate = 1.0
    for v in ((meta or {}).get("video_fps"),
              ((contract.raw.get("theta_quality") or {}).get("fps")
               if contract is not None else None)):
        try:
            f = float(v or 0.0)
        except (TypeError, ValueError):
            continue
        if f > 0:
            return f * rate
    return 30.0 * rate


def _frame_span_s(rows, i0, i1, fps):
    """행 i0..i1(양끝 포함)이 장면에서 차지한 시간 [s]."""
    f0, f1 = rows[i0].get("frame"), rows[i1].get("frame")
    if not isinstance(f0, NUM) or not isinstance(f1, NUM):
        return (i1 - i0 + 1) / fps
    return (float(f1) - float(f0) + 1.0) / fps


def spans(rows, where):
    """조건식이 참인 ★연속 구간★ [(i0, i1)] — i1 포함.

    값이 없어 판정 보류(None)인 행은 구간을 ★끊지 않고 건너뛴다★. 상태 신호는
    hold_signals 로 채워지지만 첫 발행 이전 구간은 비어 있을 수 있어서다.
    """
    from .expr import evaluate
    out, start, last = [], None, None
    for i, r in enumerate(rows):
        v = evaluate(where, r)
        if v is None:
            continue
        if v:
            if start is None:
                start = i
            last = i
        elif start is not None:
            out.append((start, last))
            start = None
    if start is not None:
        out.append((start, last))
    return out


def span_stats(rows, where, fps):
    """조건식 하나에서 나오는 값들.

    count 조건에 맞은 행 수 · frac 그 비율 · runs 구간 개수
    run_max_frames 가장 긴 구간의 행 수 · run_max_s 그 구간의 ★영상 시간★
    """
    sp = spans(rows, where)
    n = sum((i1 - i0 + 1) for i0, i1 in sp)
    return {
        "count": n,
        "frac": (n / len(rows)) if rows else 0.0,
        "runs": len(sp),
        "run_max_frames": max(((i1 - i0 + 1) for i0, i1 in sp), default=0),
        "run_max_s": max((_frame_span_s(rows, i0, i1, fps) for i0, i1 in sp),
                         default=0.0),
        "first_frame": (rows[sp[0][0]].get("frame") if sp else None),
        "last_frame": (rows[sp[-1][1]].get("frame") if sp else None),
    }


def _val_match(v, pat):
    """전이 규격의 한쪽(`0` · `RED` · `*`)이 실제 값 v 와 맞는가."""
    if pat == "*":
        return True
    if isinstance(v, NUM) and not isinstance(v, bool):
        try:
            return abs(float(v) - float(pat)) < 1e-9
        except (TypeError, ValueError):
            return False
    if isinstance(v, bool):
        return str(pat).lower() in (("true", "1") if v else ("false", "0"))
    return str(v) == pat


def parse_event(spec):
    """`brake_level:0->1` · `tl_state:*->RED` → (신호, 이전, 이후)."""
    sig, _, rest = str(spec).partition(":")
    a, _, b = rest.partition("->")
    if not sig.strip() or not rest or not b.strip():
        raise ValueError(f"이벤트 규격이 아니다: {spec!r}  (예: brake_level:0->1)")
    return sig.strip(), a.strip(), b.strip()


def transitions(rows, sig):
    """신호 값이 바뀐 지점 [(i, 이전값, 이후값)]. 결측 행은 건너뛴다."""
    out, prev, seen = [], None, False
    for i, r in enumerate(rows):
        v = r.get(sig)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        if seen and v != prev:
            out.append((i, prev, v))
        prev, seen = v, True
    return out


def find_events(rows, spec, fps):
    """전이 규격에 맞는 사건 목록 — 각각 어느 행(i)·어느 프레임·몇 초인지."""
    sig, a, b = parse_event(spec)
    f0 = rows[0].get("frame") if rows else None
    out = []
    for i, prev, cur in transitions(rows, sig):
        if not (_val_match(prev, a) and _val_match(cur, b)):
            continue
        fr = rows[i].get("frame")
        t = ((float(fr) - float(f0)) / fps
             if isinstance(fr, NUM) and isinstance(f0, NUM) else None)
        out.append({"i": i, "frame": fr, "from": prev, "to": cur, "t_s": t})
    return out


def event_table(rows, contract, fps):
    """계약의 `events:` 블록 — 어느 신호가 언제 바뀌었고 ★그때 무엇이 얼마였나★.

    판정(checks)과 달리 합격/불합격이 없다. 리포트와 웹 화면이 그대로 보여 주는
    표이고, 사람이 '0단 → 1단 → 2단' 을 한눈에 확인하는 곳이다.
    """
    specs = ((contract.raw.get("events") if contract is not None else None) or [])
    if isinstance(specs, dict):
        specs = [specs]
    f0 = rows[0].get("frame") if rows else None
    out = []
    for spec in specs:
        sig = spec.get("signal")
        if not sig:
            continue
        at = [str(x) for x in (spec.get("at") or [])]
        items = []
        for i, prev, cur in transitions(rows, sig):
            fr = rows[i].get("frame")
            it = {"frame": fr, "from": prev, "to": cur,
                  "t_s": ((float(fr) - float(f0)) / fps
                          if isinstance(fr, NUM) and isinstance(f0, NUM) else None)}
            for k in at:
                it[k] = rows[i].get(k)
            items.append(it)
        out.append({"signal": sig, "label": spec.get("label", sig), "at": at,
                    "why": spec.get("why", ""), "transitions": items})
    return out


def log_events(run_dir, contract):
    """계약의 `log_events:` 를 ★노드 로그★ 에서 센다.

    ★왜 로그를 보는가★ 노드가 토픽으로 내지 않는 것이 있다 — 기동 때 어떤 가중치·
    캘리브를 읽었는지, 개입의 ★사유★ 가 무엇이었는지. 사람이 눈으로 읽으라고 적힌
    그 줄들이 사실은 판정 근거다(시험 절차서의 '기동 로그 4줄'이 그것이다).
    노드 이름과 정규식은 ★계약에만★ 있다 — 이 함수는 무엇을 찾는지 모른다.
    """
    specs = ((contract.raw.get("log_events") or {}) if contract is not None else {})
    out = {}
    for name, spec in specs.items():
        node = str(spec.get("node") or "")
        path = Path(run_dir) / f"{node}.log"
        rec = {"node": node, "count": 0, "why": spec.get("why", ""),
               "sample": "", "log_missing": not path.exists()}
        if path.exists():
            text = path.read_text(errors="replace")
            try:
                rx = re.compile(str(spec.get("pattern") or ""))
            except re.error as e:
                rec["error"] = str(e)
                out[name] = rec
                continue
            hits = list(rx.finditer(text))
            rec["count"] = len(hits)
            if hits:
                s = text.rfind("\n", 0, hits[0].start()) + 1
                e = text.find("\n", hits[0].end())
                rec["sample"] = text[s:(e if e > 0 else len(text))].strip()[:300]
        out[name] = rec
    return out


# ══════════════════════════════════════════════════════════════════════
#  단일 런 지표
# ══════════════════════════════════════════════════════════════════════
def summarize(rows, contract, meta=None):
    meta = meta or {}
    s = {"meta": meta, "rows": len(rows)}
    if not rows:
        return s

    frames = [r["frame"] for r in rows]
    span = max(frames) - min(frames) + 1
    s["frame_first"], s["frame_last"] = min(frames), max(frames)
    s["frames_pushed"] = meta.get("frames_pushed", span)
    # 의도적으로 버린 워밍업 행은 유실이 아니다
    expected = max(1, s["frames_pushed"] - int(meta.get("discard_first", 0)))
    s["frames_expected"] = expected
    s["drop_rate"] = max(0.0, 1.0 - len(rows) / expected)

    lat = _nums(rows, "latency_ms")
    if lat:
        s["latency_p50_ms"] = _p(lat, 0.50)
        s["latency_p95_ms"] = _p(lat, 0.95)
        s["latency_max_ms"] = max(lat)

    fsig, bits = flag_names(contract)
    if fsig:
        fv = _nums(rows, fsig)
        s["flag_rate"] = {
            name: (sum(1 for v in fv if int(v) & bit) / len(fv) if fv else 0.0)
            for bit, name in sorted(bits.items())
        }
        s["flag_rate"]["CLEAN"] = (sum(1 for v in fv if int(v) == 0) / len(fv)) if fv else 0.0

    s["funnel"] = funnel(rows, contract)
    s["theta_quality"] = theta_quality(rows, contract)
    # 초 단위 판정의 기준 — 벽시계가 아니라 ★영상 시간★ 이다(scene_fps 주석)
    s["scene_fps"] = scene_fps(meta, contract)
    s["events"] = event_table(rows, contract, s["scene_fps"])

    vrows = valid_rows(rows, contract)
    s["valid_rows"] = len(vrows)
    s["valid_rate"] = len(vrows) / max(1, len(rows))

    s["signals"] = {}
    for name in contract.signals:
        vals = _nums(rows, name)
        if vals:
            vv = _nums(vrows, name)
            d = _abs_diffs(rows, name)
            s["signals"][name] = {
                "kind": "num", "n": len(vals),
                "mean": st.fmean(vals), "std": (st.pstdev(vals) if len(vals) > 1 else 0.0),
                "min": min(vals), "max": max(vals), "p95": _p(vals, 0.95),
                "valid_mean": (st.fmean(vv) if vv else None),
                "valid_std": (st.pstdev(vv) if len(vv) > 1 else 0.0),
                "p95_abs_diff": _p(d, 0.95) if d else 0.0,
                "max_abs_diff": max(d) if d else 0.0,
                "frac_nonzero": sum(1 for v in vals if v != 0.0) / len(vals),
                # ★단조성★ 되돌아간 횟수. 단계가 올라가기만 해야 하는 신호
                # (리니어 브레이크처럼)에서 이 값이 0 이 아니면 규약이 깨진 것이다.
                "decreases": sum(1 for d in _steps(rows, name) if d < 0),
                "increases": sum(1 for d in _steps(rows, name) if d > 0),
            }
        else:
            cats = [r.get(name) for r in rows if isinstance(r.get(name), str)]
            if cats:
                counts = {}
                for c in cats:
                    counts[c] = counts.get(c, 0) + 1
                trans = sum(1 for a, b in zip(cats, cats[1:]) if a != b)
                s["signals"][name] = {"kind": "cat", "n": len(cats),
                                      "counts": counts, "transitions": trans}
    return s


# ══════════════════════════════════════════════════════════════════════
#  θ 품질 — gps_imu 가 실제로 쓰는 유일한 값의 건강 진단
# ══════════════════════════════════════════════════════════════════════
#  gps_imu 는 θ 를 ★직진 구간에서만★ 저게인으로 누적 적용한다. 그래서 중요한 건
#  순간 정확도가 아니라 두 가지다:
#    · bias        직진에서 평균이 0 이 아니면 헤딩이 계속 한쪽으로 끌린다
#    · 저주파 진동  조향 진동 대역에 파워가 있으면 카메라가 진동을 ★만든다★
#                  (실측으로 조향진동의 최대 52% 가 카메라 탓이라 trust 를 낮춘 이력)
# ══════════════════════════════════════════════════════════════════════
def theta_quality(rows, contract):
    cfg = contract.raw.get("theta_quality") or {}
    if not cfg:
        return {}
    name = cfg.get("signal", "theta_deg")
    fps = float(cfg.get("fps", 30.0))
    rate_max = float(cfg.get("straight_max_rate_dps", 10.0))
    lo, hi = cfg.get("vibration_band_hz", [0.08, 0.18])

    th = [r.get(name) for r in rows]
    th = [float(v) for v in th if isinstance(v, NUM)]
    if len(th) < 16:
        return {"n": len(th), "note": "표본 부족"}

    # 자이로가 없으므로 θ 변화율로 직진을 근사한다(테스트베드는 영상만 받는다)
    rate = [0.0] * len(th)
    for i in range(1, len(th) - 1):
        rate[i] = abs(th[i + 1] - th[i - 1]) * 0.5 * fps
    rate[0] = rate[1] if len(rate) > 1 else 0.0
    rate[-1] = rate[-2] if len(rate) > 1 else 0.0
    straight = [t for t, r in zip(th, rate) if r <= rate_max]

    out = {
        "n": len(th),
        "straight_frac": len(straight) / len(th),
        "bias_deg": (st.fmean(straight) if straight else None),
        "straight_std_deg": (st.pstdev(straight) if len(straight) > 1 else None),
        "abs_bias_deg": (abs(st.fmean(straight)) if straight else None),
    }

    # 진동 대역 파워 비중 (DC 제외)
    m = st.fmean(th)
    x = [v - m for v in th]
    n = len(x)
    freqs = [k * fps / n for k in range(n // 2 + 1)]
    power = []
    for k in range(n // 2 + 1):
        re = im = 0.0
        for i, v in enumerate(x):
            ang = -2.0 * math.pi * k * i / n
            re += v * math.cos(ang)
            im += v * math.sin(ang)
        power.append(re * re + im * im)
    tot = sum(power[1:]) or 1.0
    band = sum(p for f, p in zip(freqs[1:], power[1:]) if lo <= f <= hi)
    out["vibration_frac"] = band / tot
    peak = max(range(1, len(power)), key=lambda k: power[k])
    out["peak_hz"] = freqs[peak]
    return out


def report_theta(tq):
    if not tq or tq.get("note"):
        return ""
    L = ["## θ 품질 — gps_imu 가 쓰는 유일한 값", "",
         "gps_imu 는 θ 를 직진 구간에서만 저게인으로 누적한다. 그래서 순간 정확도보다",
         "**편향(bias)** 과 **저주파 진동**이 중요하다.", "",
         "| 항목 | 값 | 뜻 |", "|---|---|---|"]
    L.append(f"| 직진 구간 비율 | {_f(tq.get('straight_frac'), 3)} | "
             f"θ 변화율로 근사 (테스트베드에 자이로가 없다) |")
    L.append(f"| **bias** | {_f(tq.get('bias_deg'), 3)}° | "
             f"직진에서 0 이 아니면 헤딩이 한쪽으로 계속 끌린다 |")
    L.append(f"| 직진 표준편차 | {_f(tq.get('straight_std_deg'), 3)}° | "
             f"클수록 관측 잡음이 크다 |")
    L.append(f"| **진동 대역 파워** | {_f(tq.get('vibration_frac'), 3)} | "
             f"조향 진동 대역 비중. 크면 카메라가 진동을 만든다 |")
    L.append(f"| 주피크 | {_f(tq.get('peak_hz'), 3)} Hz | — |")
    L.append("")
    return "\n".join(L)


# ══════════════════════════════════════════════════════════════════════
#  받는 쪽 게이트 통과율 (funnel)
# ══════════════════════════════════════════════════════════════════════
#  ★가장 중요한 지표다★
#  "차선을 잘 봤나"가 아니라 "그래서 받는 쪽이 실제로 썼나"를 잰다.
#  받는 쪽 노드(gps_imu 등)는 자기 게이트를 갖고 있어서, 인지가 아무리 좋아도
#  게이트를 못 넘으면 주행에 ★한 프레임도 기여하지 않는다★.
#  단계별 탈락률을 보면 무엇을 고쳐야 하는지가 바로 나온다.
# ══════════════════════════════════════════════════════════════════════
def funnel(rows, contract):
    from .expr import evaluate
    out = []
    for cons in (contract.consumers or []):
        stages, alive = [], list(rows)
        n0 = len(alive)
        for stg in cons.get("stages", []):
            kept, undecided = [], 0
            for r in alive:
                v = evaluate(stg["expr"], r)
                if v is None:
                    undecided += 1
                elif v:
                    kept.append(r)
            stages.append({
                "name": stg["name"], "expr": stg["expr"],
                "why": stg.get("why", ""), "const": stg.get("const", ""),
                "kept": len(kept), "in": len(alive),
                "dropped": len(alive) - len(kept) - undecided,
                "undecided": undecided,
                "pass_rate": len(kept) / max(1, len(alive)),
                "cum_rate": len(kept) / max(1, n0),
            })
            alive = kept
        worst = (max(stages, key=lambda s: s["dropped"])["name"]
                 if stages else None)
        out.append({
            "id": cons.get("id", "?"), "label": cons.get("label", ""),
            "source": cons.get("source", ""), "total": n0,
            "contributing": len(alive),
            "rate": len(alive) / max(1, n0),
            "bottleneck": worst, "stages": stages,
        })
    return out


def report_funnel(funnels):
    if not funnels:
        return ""
    L = ["## 받는 쪽 게이트 통과율", "",
         "차선을 봤는가가 아니라 **받는 쪽이 실제로 썼는가**를 잰다.",
         "받는 쪽 노드는 자기 게이트를 갖고 있어서, 인지가 좋아도 게이트를 못 넘으면",
         "주행에 한 프레임도 기여하지 않는다.", ""]
    for f in funnels:
        L += [f"### {f['label'] or f['id']}"
              + (f"  <sub>{f['source']}</sub>" if f["source"] else ""), "",
              f"**실질 기여율 {f['rate'] * 100:.1f}%** "
              f"({f['contributing']} / {f['total']} 프레임)"
              + (f" · 최대 병목: **{f['bottleneck']}**" if f["bottleneck"] else ""), "",
              "| 단계 | 조건 | 통과 | 이 단계 통과율 | 누적 | 탈락 |",
              "|---|---|---|---|---|---|"]
        for s in f["stages"]:
            mark = " 🔻" if s["name"] == f["bottleneck"] and s["dropped"] else ""
            L.append(f"| {s['name']}{mark} | `{s['expr']}` | {s['kept']} | "
                     f"{s['pass_rate'] * 100:.1f}% | {s['cum_rate'] * 100:.1f}% | "
                     f"{s['dropped']} |")
        L.append("")
        for s in f["stages"]:
            if s["why"]:
                L.append(f"- **{s['name']}** — {s['why']}"
                         + (f" <sub>({s['const']})</sub>" if s["const"] else ""))
        L.append("")
    return "\n".join(L)


# ══════════════════════════════════════════════════════════════════════
#  선언적 체크 (시나리오 YAML 의 checks:)
# ══════════════════════════════════════════════════════════════════════
def _stat_value(summary, rows, contract, chk):
    stat = chk["stat"]
    sig = chk.get("signal")
    fps = summary.get("scene_fps") or scene_fps(summary.get("meta"), contract)

    # ── 조건식 체크 ──────────────────────────────────────────────────
    #    signal 이 없으면 ★구간★ 을 잰다 (count/frac/runs/run_max_frames/run_max_s)
    #        {where: "sl_wait and brake_level > 0", stat: count, max: 0}
    #    signal 이 있으면 ★그 조건에 맞는 행만 골라★ 평소의 신호 통계를 낸다
    #        {where: "sl_px >= 0", signal: sl_px, stat: increases, max: 3}
    if chk.get("where") is not None and sig is None:
        src_rows = valid_rows(rows, contract) if chk.get("when_valid") else rows
        return span_stats(src_rows, chk["where"], fps).get(stat)

    # ── 전이 체크 : 값이 바뀐 그 순간 ────────────────────────────────
    #    {event: "brake_level:0->1", stat: "at:sl_px", min: 230, max: 250}
    #    stat 은 count / frame / t_s / at:<신호>. 사건이 여럿이면 ★첫 번째★ 를
    #    쓴다(접근 1회를 재는 것이 목적이다). last: true 면 마지막 것.
    if chk.get("event") is not None:
        evs = find_events(rows, chk["event"], fps)
        if stat == "count":
            return len(evs)
        if not evs:
            return None
        e = evs[-1] if chk.get("last") else evs[0]
        if stat in ("frame", "t_s"):
            return e.get(stat)
        if stat.startswith("at:"):
            return rows[e["i"]].get(stat.split(":", 1)[1])
        return None

    if sig is None:
        if stat.startswith("log:"):
            e = (summary.get("log_events") or {}).get(stat.split(":", 1)[1])
            return None if e is None else e.get("count")
        if stat.startswith("flag_rate:"):
            return (summary.get("flag_rate") or {}).get(stat.split(":", 1)[1])
        if stat.startswith("theta:"):
            return (summary.get("theta_quality") or {}).get(stat.split(":", 1)[1])
        if stat.startswith("contribution:"):
            key = stat.split(":", 1)[1]
            for f in (summary.get("funnel") or []):
                if f["id"] == key:
                    return f["rate"]
            return None
        return summary.get(stat)
    src = valid_rows(rows, contract) if chk.get("when_valid") else rows
    if chk.get("where") is not None:
        from .expr import evaluate
        src = [r for r in src if evaluate(chk["where"], r) is True]
    vals = _nums(src, sig)
    if stat == "p95_abs_diff":
        d = _abs_diffs(src, sig)
        return _p(d, 0.95) if d else 0.0
    if stat == "max_abs_diff":
        d = _abs_diffs(src, sig)
        return max(d) if d else 0.0
    if stat in ("decreases", "increases"):
        d = _steps(src, sig)
        return sum(1 for x in d if (x < 0 if stat == "decreases" else x > 0))
    if not vals:
        return None
    return {
        "mean": lambda: st.fmean(vals),
        "std": lambda: st.pstdev(vals) if len(vals) > 1 else 0.0,
        "min": lambda: min(vals),
        "max": lambda: max(vals),
        "p95": lambda: _p(vals, 0.95),
        "frac_nonzero": lambda: sum(1 for v in vals if v != 0.0) / len(vals),
        "frac_zero": lambda: sum(1 for v in vals if v == 0.0) / len(vals),
    }.get(stat, lambda: None)()


def run_checks(summary, rows, contract, checks):
    out = []
    for chk in checks or []:
        try:
            v = _stat_value(summary, rows, contract, chk)
        except (ValueError, SyntaxError) as e:
            out.append({"check": str(chk.get("where") or chk.get("event")
                                     or chk.get("stat")),
                        "value": None, "ok": False, "note": f"체크 정의 오류: {e}"})
            continue
        head = (chk.get("event") or chk.get("where") or chk.get("signal", ""))
        label = f"{head}.{chk['stat']}".lstrip(".")
        if v is None:
            out.append({"check": label, "value": None, "ok": None,
                        "note": "값 없음(신호 결측)"})
            continue
        ok, notes = True, []
        try:
            if "min" in chk and v < chk["min"]:
                ok = False; notes.append(f"< {chk['min']}")
            if "max" in chk and v > chk["max"]:
                ok = False; notes.append(f"> {chk['max']}")
            if "eq" in chk and v != chk["eq"]:
                ok = False; notes.append(f"≠ {chk['eq']}")
        except TypeError:
            # 문자열 신호에 크기 비교를 걸었다 — 판정 불가로 남긴다(죽지 않는다)
            out.append({"check": label, "value": v, "ok": None,
                        "note": "숫자가 아니라 크기 비교를 할 수 없다"})
            continue
        out.append({"check": label, "value": v, "ok": ok,
                    "bound": {k: chk[k] for k in ("min", "max") if k in chk},
                    "note": chk.get("why", "") or ", ".join(notes)})
    return out


# ══════════════════════════════════════════════════════════════════════
#  회귀 비교
# ══════════════════════════════════════════════════════════════════════
def compare(base_rows, cur_rows, contract, tol=None):
    tol = tol or {}
    bi = {r["frame"]: r for r in base_rows}
    ci = {r["frame"]: r for r in cur_rows}
    common = sorted(set(bi) & set(ci))
    res = {
        "frames_base": len(bi), "frames_cur": len(ci), "frames_common": len(common),
        "only_base": len(set(bi) - set(ci)), "only_cur": len(set(ci) - set(bi)),
        "numeric": {}, "categorical": {}, "verdict": "PASS",
    }
    if not common:
        res["verdict"] = "NO_OVERLAP"
        return res

    names = contract.compare_signals or [
        n for n in contract.signals
        if any(isinstance(bi[f].get(n), NUM) for f in common[:50])]
    for name in names:
        d, nb = [], 0
        for f in common:
            a, b = bi[f].get(name), ci[f].get(name)
            if isinstance(a, NUM) and isinstance(b, NUM):
                d.append(abs(float(b) - float(a)))
                nb += 1
        if not d:
            continue
        t = float(tol.get(name, tol.get("_default", 1e-6)))
        res["numeric"][name] = {
            "n": nb, "max": max(d), "rms": math.sqrt(sum(x * x for x in d) / len(d)),
            "p95": _p(d, 0.95), "tol": t,
            "changed_frac": sum(1 for x in d if x > t) / len(d),
            "ok": max(d) <= t,
        }
        if max(d) > t:
            res["verdict"] = "DIFF"

    for name in contract.compare_categorical:
        diff = [f for f in common
                if str(bi[f].get(name)) != str(ci[f].get(name))]
        if not any(name in bi[f] for f in common[:50]):
            continue
        res["categorical"][name] = {
            "n": len(common), "differing": len(diff),
            "frac": len(diff) / len(common),
            "first_diff": (diff[0] if diff else None),
            "example": ([f"frame {f}: {bi[f].get(name)} → {ci[f].get(name)}"
                         for f in diff[:3]] if diff else []),
            "ok": not diff,
        }
        if diff:
            res["verdict"] = "DIFF"

    # 타이머/변화시 발행 신호 — 프레임 위치가 아니라 "값이 바뀐 순서"만 본다
    res["sequence"] = {}
    for name in contract.compare_sequence:
        sa, sb = _rle(base_rows, name), _rle(cur_rows, name)
        if not sa and not sb:
            continue
        first = next((i for i, (x, y) in enumerate(zip(sa, sb)) if x != y),
                     None if sa == sb else min(len(sa), len(sb)))
        res["sequence"][name] = {
            "len_base": len(sa), "len_cur": len(sb), "ok": sa == sb,
            "first_diff": first,
            "base": " → ".join(sa[:8]), "cur": " → ".join(sb[:8]),
        }
        if sa != sb:
            res["verdict"] = "DIFF"
    return res


def _rle(rows, name):
    """연속 중복을 지운 값 시퀀스."""
    out = []
    for r in rows:
        v = r.get(name)
        if v is None:
            continue
        v = str(v)
        if not out or out[-1] != v:
            out.append(v)
    return out


def mirror_rows(rows, odd_names):
    """메타모픽: 좌우 반전 런의 값을 원본과 같은 좌표계로 되돌린다.

    부호홀수(odd) 신호는 부호를 뒤집고, 나머지는 그대로 둔다.
    이렇게 맞춘 뒤에도 값이 다르면 좌/우 처리에 비대칭 버그가 있거나
    IPM src_pts 가 좌우대칭이 아니라는 뜻이다.
    """
    odd = set(odd_names)
    out = []
    for r in rows:
        o = dict(r)
        for k in odd:
            if isinstance(o.get(k), NUM):
                o[k] = -float(o[k])
        out.append(o)
    return out


def diff_stats(base_rows, cur_rows, names, contract=None):
    """두 런의 공통 프레임에서 신호별 차이 통계. 판정 없이 숫자만.

    ★둘 다 차선을 본 프레임에서만 값을 비교한다★ — 한쪽만 미검출인 프레임을
    섞으면 |Δcte| 가 "차선 위치 차이"가 아니라 "0 과 실제값의 차이"가 되어
    어느 섭동에서든 비슷한 큰 값이 나온다(= 아무것도 구분 못 하는 지표).
    검출 자체가 엇갈린 비율은 `mismatch_rate` 로 따로 낸다.
    """
    bi = {r["frame"]: r for r in base_rows}
    ci = {r["frame"]: r for r in cur_rows}
    common = sorted(set(bi) & set(ci))

    ok = set(common)
    mismatch = 0
    fsig, _bits = (flag_names(contract) if contract is not None else (None, {}))
    if contract is not None:
        vb = {r["frame"] for r in valid_rows(base_rows, contract)}
        vc = {r["frame"] for r in valid_rows(cur_rows, contract)}
        mismatch = sum(1 for f in common if (f in vb) != (f in vc))
        # ★양쪽 다 CLEAN(플래그 0)인 프레임만 값을 비교한다★
        #   한쪽이 단독차선(SINGLE)으로 떨어진 프레임을 섞으면 |Δcte| 가
        #   "차선 위치 오차"가 아니라 "반폭 추정으로 갈아탄 폭"이 되어
        #   섭동 종류와 무관하게 같은 값이 나온다.
        if fsig:
            cb = {f for f in common if isinstance(bi[f].get(fsig), NUM)
                  and int(bi[f][fsig]) == 0}
            cc = {f for f in common if isinstance(ci[f].get(fsig), NUM)
                  and int(ci[f][fsig]) == 0}
            ok = cb & cc
        else:
            ok = {f for f in common if f in vb and f in vc}

    out = {"n_common": len(common), "n_both_valid": len(ok),
           "mismatch_rate": mismatch / len(common) if common else 0.0}
    for name in names:
        d = [abs(float(ci[f][name]) - float(bi[f][name])) for f in sorted(ok)
             if isinstance(bi[f].get(name), NUM) and isinstance(ci[f].get(name), NUM)]
        out[name] = {"n": len(d), "p95": _p(d, 0.95) if d else None,
                     "max": max(d) if d else None,
                     "rms": (math.sqrt(sum(x * x for x in d) / len(d)) if d else None)}
    return out


def report_robustness(base_name, entries, contract):
    """섭동별로 기준 대비 얼마나 무너졌는가. GT 없이 재는 강건성 지표."""
    L = ["# 섭동 대조 (강건성 / 메타모픽)", "",
         f"기준 런: `{base_name}`", "",
         "같은 프레임에 조건만 바꿔 넣었다. 장면이 같으므로 **출력도 같아야 한다** —",
         "차이가 크다는 것은 그 조건에서 인지가 무너진다는 뜻이다(GT 불필요).", "",
         "열화는 두 갈래로 나타난다:", "",
         "- **놓친다** — 검출 자체가 갈린다. `검출 엇갈림` 은 기준과 검출 여부가 "
         "달라진 프레임 비율.",
         "- **틀린다** — 검출은 했는데 값이 어긋난다. `p95\\|Δ\\|` 는 "
         "**양쪽 다 온전한(플래그 0)** 프레임에서만 잰다.",
         "  비교할 신호는 계약의 `compare_signals` 앞쪽 넷이다.", "",
         ]
    # ★어느 신호를 비교할지는 계약이 정한다★ 표를 특정 워크스페이스의 신호 이름에
    #   맞춰 박아 두면(전에는 cte·θ·차선폭이 박혀 있었다) 다른 계약에서는 빈 칸만
    #   나오는 표가 된다. compare_signals 앞쪽 넷을 그대로 쓴다.
    cols = list(contract.compare_signals)[:4]
    fsig, _b = flag_names(contract)
    head = ["변형"]
    if fsig:
        head += ["유효율", "차선없음", "단독차선"]
    head += ["검출 엇갈림", "둘다비교"] + [rf"p95\|Δ{c}\|" for c in cols]
    L += ["| " + " | ".join(head) + " |",
          "|" + "---|" * len(head)]
    for e in entries:
        d, sm = e["diff"], e["summary"]
        fr = sm.get("flag_rate") or {}
        g = lambda k: _f((d.get(k) or {}).get("p95"), 4)   # noqa: E731
        note = " 🪞" if e.get("mirror") else ""
        cells = [f"{e['name']}{note}"]
        if fsig:
            cells += [_f(sm.get("valid_rate"), 3), _f(fr.get("NO_LANE"), 3),
                      _f(fr.get("SINGLE"), 3)]
        cells += [_f(d.get("mismatch_rate"), 3), str(d.get("n_both_valid", 0))]
        cells += [g(c) for c in cols]
        L.append("| " + " | ".join(cells) + " |")
    if any(e.get("mirror") for e in entries):
        L += ["", "🪞 = 메타모픽 변형. 좌우 반전 후 부호홀수 신호"
                  f"(`{'`, `'.join(contract.mirror_odd) or '없음'}`)를 되돌려 비교했다.",
              "반전만으로 값이 크게 달라지면 좌/우 처리 비대칭이거나 IPM `src_pts` 가 "
              "좌우대칭이 아니라는 뜻이다."]
    L.append("")
    return "\n".join(L)


# ══════════════════════════════════════════════════════════════════════
#  리포트 (마크다운)
# ══════════════════════════════════════════════════════════════════════
def _f(v, n=4):
    if v is None:
        return "—"
    if isinstance(v, float):
        if math.isnan(v):
            return "—"
        return f"{v:.{n}f}"
    return str(v)


def report_run(summary, checks, drift, contract):
    m = summary.get("meta", {})
    L = []
    a = L.append
    a(f"# 실행 리포트 — {m.get('run_id', '?')}")
    a("")
    a(f"- 계약: `{contract.name}` v{contract.version}")
    a(f"- 시나리오: `{m.get('scenario', '?')}`")
    a(f"- 영상: `{m.get('video', '?')}`  섭동: `{m.get('perturb', 'none')}`  "
      f"모드: `{m.get('mode', '?')}`")
    a(f"- 투입 프레임 {summary.get('frames_pushed', '?')} → "
      f"출력 행 {summary.get('rows', 0)} (유실 {_f(summary.get('drop_rate'), 3)})")
    if "latency_p95_ms" in summary:
        a(f"- 지연: p50 {_f(summary['latency_p50_ms'], 1)} ms / "
          f"p95 {_f(summary['latency_p95_ms'], 1)} ms / "
          f"max {_f(summary['latency_max_ms'], 1)} ms")
    a("")

    if checks:
        a("## 판정")
        a("")
        a("| 체크 | 값 | 기준 | 결과 |")
        a("|---|---|---|---|")
        for c in checks:
            b = c.get("bound") or {}
            bd = " / ".join(f"{k}={v}" for k, v in b.items()) or "—"
            mark = "✅" if c["ok"] else ("⚠️" if c["ok"] is None else "❌")
            a(f"| {c['check']} | {_f(c['value'], 4)} | {bd} | {mark} {c.get('note', '')} |")
        a("")

    fn = report_funnel(summary.get("funnel"))
    if fn:
        a(fn)
    tq = report_theta(summary.get("theta_quality"))
    if tq:
        a(tq)

    # ── 단계 전이 — '언제 물었나' ────────────────────────────────────
    for ev in (summary.get("events") or []):
        tr = ev.get("transitions") or []
        a(f"## 단계 전이 — `{ev.get('label') or ev['signal']}`")
        a("")
        if ev.get("why"):
            a(f"> {ev['why']}")
            a("")
        if not tr:
            a("전이가 한 번도 없었다.")
            a("")
            continue
        at = ev.get("at") or []
        a("| 프레임 | 시각[s] | 이전 → 이후 | " + " | ".join(at) + " |")
        a("|---|---|---|" + "---|" * len(at))
        for t in tr:
            vals = " | ".join(_f(t.get(k), 1) for k in at)
            a(f"| {t.get('frame')} | {_f(t.get('t_s'), 2)} | "
              f"{t.get('from')} → {t.get('to')} | {vals} |")
        a("")

    # ── 로그 이벤트 — 토픽으로 나오지 않는 근거 ──────────────────────
    le = summary.get("log_events") or {}
    if le:
        a("## 노드 로그")
        a("")
        a("| 이벤트 | 횟수 | 본 것 |")
        a("|---|---|---|")
        for name, e in le.items():
            note = e.get("sample") or e.get("error") or (
                "로그 파일 없음" if e.get("log_missing") else "—")
            a(f"| {name} | {e.get('count', 0)} | `{note[:160]}` |")
        a("")

    fr = summary.get("flag_rate")
    if fr:
        a("## 플래그 발생률")
        a("")
        a("| 플래그 | 비율 |")
        a("|---|---|")
        for k, v in fr.items():
            a(f"| {k} | {_f(v, 3)} |")
        a("")

    a("## 신호 통계")
    a("")
    a("| 신호 | n | mean | std | min | max | p95\\|Δ\\| |")
    a("|---|---|---|---|---|---|---|")
    for name, s in (summary.get("signals") or {}).items():
        if s["kind"] != "num":
            continue
        a(f"| {name} | {s['n']} | {_f(s['mean'])} | {_f(s['std'])} | "
          f"{_f(s['min'])} | {_f(s['max'])} | {_f(s['p95_abs_diff'])} |")
    a("")
    cats = {k: v for k, v in (summary.get("signals") or {}).items() if v["kind"] == "cat"}
    if cats:
        a("## 이산 신호")
        a("")
        a("| 신호 | 분포 | 전이 횟수 |")
        a("|---|---|---|")
        for name, s in cats.items():
            dist = ", ".join(f"{k}:{v}" for k, v in
                             sorted(s["counts"].items(), key=lambda x: -x[1]))
            a(f"| {name} | {dist} | {s['transitions']} |")
        a("")

    a("## 계약 정합 (스키마 드리프트)")
    a("")
    order = {"drift": 0, "fallback": 1, "silent": 2, "ok": 3}
    notable = sorted([d for d in drift if d["status"] != "ok"],
                     key=lambda d: order[d["status"]])
    n_ok = sum(1 for d in drift if d["status"] == "ok")
    a(f"신호 {len(drift)}개 중 {n_ok}개가 첫 번째 선언 경로로 잡혔다.")
    a("")
    if notable:
        label = {"drift": "❌ 불일치", "fallback": "🔁 대체경로",
                 "silent": "· 미수신"}
        a("| 신호 | 상태 | 선언 | 맞은 경로 | 수신 | 결측 |")
        a("|---|---|---|---|---|---|")
        for d in notable:
            tag = label[d["status"]]
            if d["status"] == "drift" and d["optional"]:
                tag = "· 선택항목(미구현)"
            a(f"| {d['signal']} | {tag} | `{d['declared']}` | "
              f"`{d['matched'] or '—'}` | {d['tries']} | {d['misses']} |")
        a("")
        a("> **불일치**: 메시지는 왔는데 선언한 경로가 하나도 안 맞았다 = 워크스페이스의")
        a("> 메시지 배치가 바뀐 것이다. `contracts/*.yaml` 의 해당 `path:` 한 줄만 고친다.")
        a("> **대체경로**: 두 번째 이후 경로로 잡혔다 — 마이그레이션 중이라는 뜻이고 동작은 정상.")
        a("> **미수신**: 그 토픽에 메시지가 한 번도 안 왔다(발행 조건 미충족일 수 있다).")
    a("")
    return "\n".join(L)


def report_compare(res, base_id, cur_id):
    L = []
    a = L.append
    a(f"# 회귀 비교 — `{base_id}` → `{cur_id}`")
    a("")
    a(f"**판정: {res['verdict']}**")
    a("")
    a(f"- 공통 프레임 {res['frames_common']} "
      f"(기준 전용 {res['only_base']}, 현재 전용 {res['only_cur']})")
    a("")
    if res["numeric"]:
        a("## 수치 신호")
        a("")
        a("| 신호 | n | max\\|Δ\\| | RMS Δ | p95\\|Δ\\| | 허용 | 변화 비율 | |")
        a("|---|---|---|---|---|---|---|---|")
        for k, v in res["numeric"].items():
            a(f"| {k} | {v['n']} | {_f(v['max'])} | {_f(v['rms'])} | {_f(v['p95'])} | "
              f"{_f(v['tol'])} | {_f(v['changed_frac'], 3)} | "
              f"{'✅' if v['ok'] else '❌'} |")
        a("")
    if res["categorical"]:
        a("## 이산 신호 (상태 시퀀스)")
        a("")
        a("| 신호 | 불일치 프레임 | 비율 | 첫 불일치 | |")
        a("|---|---|---|---|---|")
        for k, v in res["categorical"].items():
            a(f"| {k} | {v['differing']} | {_f(v['frac'], 3)} | "
              f"{v['first_diff'] if v['first_diff'] is not None else '—'} | "
              f"{'✅' if v['ok'] else '❌'} |")
        a("")
        for k, v in res["categorical"].items():
            for ex in v["example"]:
                a(f"- `{k}` {ex}")
        a("")
    if res.get("sequence"):
        a("## 상태 전이 시퀀스 (타이머 발행 신호)")
        a("")
        a("프레임 위치가 아니라 값이 바뀐 순서만 비교한다.")
        a("")
        a("| 신호 | 기준 길이 | 현재 길이 | 첫 불일치 | |")
        a("|---|---|---|---|---|")
        for k, v in res["sequence"].items():
            a(f"| {k} | {v['len_base']} | {v['len_cur']} | "
              f"{v['first_diff'] if v['first_diff'] is not None else '—'} | "
              f"{'✅' if v['ok'] else '❌'} |")
        a("")
        for k, v in res["sequence"].items():
            if not v["ok"]:
                a(f"- `{k}` 기준: `{v['base']}`")
                a(f"- `{k}` 현재: `{v['cur']}`")
        a("")
    return "\n".join(L)
