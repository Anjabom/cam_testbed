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
import statistics as st

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
    held = {}
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
    if sig is None:
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
    vals = _nums(src, sig)
    if stat == "p95_abs_diff":
        d = _abs_diffs(src, sig)
        return _p(d, 0.95) if d else 0.0
    if stat == "max_abs_diff":
        d = _abs_diffs(src, sig)
        return max(d) if d else 0.0
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
        v = _stat_value(summary, rows, contract, chk)
        label = f"{chk.get('signal', '')}.{chk['stat']}".lstrip(".")
        if v is None:
            out.append({"check": label, "value": None, "ok": None,
                        "note": "값 없음(신호 결측)"})
            continue
        ok, notes = True, []
        if "min" in chk and v < chk["min"]:
            ok = False; notes.append(f"< {chk['min']}")
        if "max" in chk and v > chk["max"]:
            ok = False; notes.append(f"> {chk['max']}")
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
         "- **놓친다** — 차선없음/단독차선으로 떨어진다. `검출 엇갈림` 은 기준과 "
         "검출 여부가 갈린 프레임 비율.",
         "- **틀린다** — 검출은 했는데 값이 어긋난다. `p95\\|Δ\\|` 는 "
         "**양쪽 다 플래그 0(CLEAN)** 인 프레임에서만 잰다.", "",
         r"| 변형 | 유효율 | 차선없음 | 단독차선 | conf 평균 | 검출 엇갈림 | "
         r"둘다CLEAN | p95\|Δcte\| [m] | p95\|Δθ\| [°] | p95\|Δ폭\| [m] |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for e in entries:
        d, sm = e["diff"], e["summary"]
        fr = sm.get("flag_rate") or {}
        g = lambda k: _f((d.get(k) or {}).get("p95"), 4)   # noqa: E731
        cf = (sm.get("signals", {}).get("conf_raw") or {}).get("mean")
        note = " 🪞" if e.get("mirror") else ""
        L.append(f"| {e['name']}{note} | "
                 f"{_f(sm.get('valid_rate'), 3)} | {_f(fr.get('NO_LANE'), 3)} | "
                 f"{_f(fr.get('SINGLE'), 3)} | {_f(cf, 3)} | "
                 f"{_f(d.get('mismatch_rate'), 3)} | {d.get('n_both_valid', 0)} | "
                 f"{g('cte_rear_m')} | {g('theta_deg')} | {g('lane_width_m')} |")
    L += ["", "🪞 = 메타모픽 변형. 좌우 반전 후 부호홀수 신호"
              f"(`{'`, `'.join(contract.mirror_odd)}`)를 되돌려 비교했다.",
          "반전만으로 값이 크게 달라지면 좌/우 처리 비대칭이거나 IPM `src_pts` 가 "
          "좌우대칭이 아니라는 뜻이다.", ""]
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
