"""테스트베드 자체 검사 — ROS 도 영상도 필요 없다.

경로식 평가·스키마 드리프트 판정·상태유지·시퀀스 비교처럼
"테스트베드가 틀리면 모든 판정이 틀리는" 부분만 검사한다.

    python3 -m tb.selftest
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

from . import analyze
from . import analyze as A   # 새 도구들(구간·전이·로그)을 짧게 부른다
from .contract import Contract, Signal, resolve, _MISSING

FAILS = []


def eq(label, got, want):
    if got != want:
        FAILS.append(f"{label}: got {got!r}, want {want!r}")


def _contract(**over):
    d = {
        "name": "t", "version": 1,
        "signals": {
            "a": {"topic": "/x", "path": ["data[0]"]},
            "th": {"topic": "/x", "path": ["data[2]", "theta"]},
            "st": {"topic": "/s", "path": ["data"]},
            "opt": {"topic": "/x", "path": ["data[9]"], "optional": True},
        },
        "compare_signals": ["a", "th"],
        "compare_categorical": [],
        "compare_sequence": ["st"],
        "hold_signals": ["st"],
        "flag_bits": {"signal": "a", "bits": {1: "NO_LANE"}},
    }
    d.update(over)
    return Contract(d, "mem")


def t_resolve():
    o = {"data": [1, 2, 3], "linear": {"x": 9.0}, "m": [[1, 2], [3, 4]]}
    eq("index", resolve(o, "data[1]"), 2)
    eq("음수 인덱스", resolve(o, "data[-1]"), 3)
    eq("중첩", resolve(o, "linear.x"), 9.0)
    eq("2차원", resolve(o, "m[1][0]"), 3)
    eq("없는 키", resolve(o, "nope"), _MISSING)
    eq("범위 밖", resolve(o, "data[9]"), _MISSING)
    eq("없는 중첩", resolve(o, "linear.y"), _MISSING)


def t_fallback():
    """구/신 포맷을 동시에 지원하는지 — 이게 결합 해제의 핵심이다."""
    s = Signal("th", {"topic": "/x", "path": ["data[2]", "theta"]})
    eq("구포맷", s.extract({"data": [0, 0, 7.5]}), 7.5)
    eq("구포맷 status", s.hit_path, "data[2]")
    s2 = Signal("th", {"topic": "/x", "path": ["data[2]", "theta"]})
    eq("신포맷(대체경로)", s2.extract({"theta": 3.25}), 3.25)
    eq("신포맷 경로", s2.hit_path, "theta")
    eq("신포맷 miss 기록", s2.miss, 0)


def t_drift_status():
    c = _contract()
    for s in c.signals.values():
        if s.topic == "/x":
            s.extract({"data": [1.0, 0.0, 2.0]})
    st = {d["signal"]: d["status"] for d in c.drift_report()}
    eq("정상=ok", st["a"], "ok")
    eq("메시지 미수신=silent", st["st"], "silent")
    eq("선택항목 결측=drift", st["opt"], "drift")

    c2 = _contract()
    for s in c2.signals.values():
        if s.topic == "/x":
            s.extract({"theta": 1.0})      # data 가 사라진 새 포맷
    st2 = {d["signal"]: d["status"] for d in c2.drift_report()}
    eq("경로 불일치=drift", st2["a"], "drift")
    eq("대체경로로 구제=fallback", st2["th"], "fallback")


def t_build_table_hold():
    c = _contract()
    recs = [
        {"t": 1.0, "frame": 0, "t_frame": 0.9, "topic": "/x", "msg": {"data": [1.0, 0, 0.5]}},
        {"t": 1.1, "frame": 0, "t_frame": 0.9, "topic": "/s", "msg": {"data": "GO"}},
        {"t": 2.0, "frame": 1, "t_frame": 1.9, "topic": "/x", "msg": {"data": [2.0, 0, 1.5]}},
        # frame 1 에는 /s 가 없다 → 앞 값 GO 가 유지돼야 한다
        {"t": 3.0, "frame": 2, "t_frame": 2.9, "topic": "/x", "msg": {"data": [3.0, 0, 2.5]}},
        {"t": 3.1, "frame": 2, "t_frame": 2.9, "topic": "/s", "msg": {"data": "STOP"}},
        {"t": 0.5, "frame": -1, "t_frame": 0.0, "topic": "/x", "msg": {"data": [9, 9, 9]}},
    ]
    path = _write(recs)
    rows, n = analyze.build_table(path, c)
    eq("행 수", len(rows), 3)
    eq("frame<0 제외", n, 6)
    eq("상태 유지", [r["st"] for r in rows], ["GO", "GO", "STOP"])
    eq("지연 계산", round(rows[0]["latency_ms"], 1), 200.0)

    rows2, _ = analyze.build_table(path, c, discard_first=1)
    eq("워밍업 버리기", [r["frame"] for r in rows2], [1, 2])
    eq("버려도 hold 는 이어짐", rows2[0]["st"], "GO")
    Path(path).unlink()


def _write(recs):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    for r in recs:
        f.write(json.dumps(r) + "\n")
    f.close()
    return f.name


def t_compare():
    c = _contract()
    base = [{"frame": i, "a": 1.0, "th": 2.0, "st": "GO"} for i in range(5)]
    same = [dict(r) for r in base]
    eq("동일=PASS", analyze.compare(base, same, c)["verdict"], "PASS")

    drifted = [dict(r) for r in base]
    drifted[3]["th"] = 2.5
    r = analyze.compare(base, drifted, c, {"_default": 0.1})
    eq("차이=DIFF", r["verdict"], "DIFF")
    eq("max|Δ|", round(r["numeric"]["th"]["max"], 3), 0.5)
    eq("허용 안쪽은 통과", analyze.compare(base, drifted, c,
                                    {"_default": 1.0})["verdict"], "PASS")

    # 타이머 발행 신호: 프레임 위치가 달라도 순서가 같으면 PASS
    a = [{"frame": 0, "st": "GO"}, {"frame": 1, "st": "GO"}, {"frame": 2, "st": "STOP"}]
    b = [{"frame": 0, "st": "GO"}, {"frame": 1, "st": "STOP"}, {"frame": 2, "st": "STOP"}]
    eq("시퀀스 동일=ok", analyze.compare(a, b, c)["sequence"]["st"]["ok"], True)
    d = [{"frame": 0, "st": "STOP"}, {"frame": 1, "st": "GO"}, {"frame": 2, "st": "GO"}]
    eq("시퀀스 역전=fail", analyze.compare(a, d, c)["sequence"]["st"]["ok"], False)


def t_mirror():
    rows = [{"frame": 0, "a": 1.0, "th": 2.0, "st": "GO"}]
    m = analyze.mirror_rows(rows, ["th"])
    eq("부호홀수만 반전", (m[0]["a"], m[0]["th"]), (1.0, -2.0))
    eq("원본 불변", rows[0]["th"], 2.0)


def t_checks():
    c = _contract()
    rows = [{"frame": i, "a": float(i), "th": 1.0} for i in range(5)]
    sm = analyze.summarize(rows, c, {"frames_pushed": 5})
    res = {r["check"]: r for r in analyze.run_checks(sm, rows, c, [
        {"signal": "a", "stat": "mean", "max": 10},
        {"signal": "a", "stat": "mean", "max": 1},
        {"signal": "th", "stat": "p95_abs_diff", "max": 0.1},
        {"stat": "drop_rate", "max": 0.1},
        {"signal": "없는신호", "stat": "mean", "max": 1},
    ])}
    eq("통과", res["a.mean"]["ok"] is not None, True)
    eq("상한 초과 감지", [r["ok"] for r in analyze.run_checks(
        sm, rows, c, [{"signal": "a", "stat": "mean", "max": 1}])], [False])
    eq("변화 없는 신호", res["th.p95_abs_diff"]["ok"], True)
    eq("결측 신호는 판정 보류", res["없는신호.mean"]["ok"], None)
    eq("drop_rate", res["drop_rate"]["value"], 0.0)


def t_inject_slots():
    """계약의 signals 로부터 `이름 → data[] 인덱스` 역매핑이 되는가."""
    from .contract import Contract
    from .inject import array_slots
    c = Contract({"name": "t", "signals": {
        "aL": {"topic": "/in", "path": ["data[0]"]},
        "bL": {"topic": "/in", "path": ["data[1]"]},
        "conf": {"topic": "/in", "path": ["data[6]"]},
        "other": {"topic": "/out", "path": ["data[0]"]},
        "nested": {"topic": "/in", "path": ["linear.x"]},
    }}, "mem")
    sl = array_slots(c, "/in")
    eq("인덱스 역매핑", sl, {"aL": 0, "bL": 1, "conf": 6})
    eq("다른 토픽 제외", "other" not in sl, True)
    eq("중첩 필드 제외", "nested" not in sl, True)


def t_render():
    """오버레이 렌더러 — 중심선과 θ 가 주입 케이스와 같은 값을 내는가.

    render.py 는 ★그림용 재계산★이라 대상 노드와 어긋날 수 있다.
    그래서 주입 검증에서 쓴 것과 같은 기하로 확인한다.
    """
    import math as _m
    import numpy as np
    from .contract import Contract
    from .render import Renderer
    c = Contract({"name": "t",
                  "calibration": {"bev": {"w": 640, "h": 480},
                                  "targets": {}},
                  "render": {"bottom_ratio": 0.92, "lookahead_ratio": 0.45,
                             "ipm_src_pts": [620, 650, 1300, 650, 1920, 1080, 0, 1080],
                             "signals": {"left": ["aL", "bL", "cL"],
                                         "right": ["aR", "bR", "cR"],
                                         "half_width": "hw"}}}, "mem")
    R = Renderer(c)
    eq("근점 y", round(R.y_near, 1), 441.6)
    eq("전방점 y", round(R.y_look, 1), 216.0)

    # 직선 중앙 — 중심선이 정확히 bev 중앙, θ=0
    row = {"aL": 0, "bL": 0, "cL": 120, "aR": 0, "bR": 0, "cR": 520, "hw": 200}
    lf, rf = R.fits(row)
    eq("좌우 fit 인식", (lf is not None, rf is not None), (True, True))
    eq("중심선 근점", round(R.center_x(lf, rf, R.y_near, 200), 3), 320.0)

    # 기울기 b → θ = atan(b), 주입 케이스와 같아야 한다
    b = _m.tan(_m.radians(10))
    row2 = {"aL": 0, "bL": b, "cL": 120, "aR": 0, "bR": b, "cR": 520, "hw": 200}
    lf2, rf2 = R.fits(row2)
    xn = R.center_x(lf2, rf2, R.y_near, 200)
    xl = R.center_x(lf2, rf2, R.y_look, 200)
    th = -_m.degrees(_m.atan2(xl - xn, R.y_near - R.y_look))
    eq("θ = atan(b) = 10도", round(th, 2), 10.0)

    # 단독차선 — 반폭만큼 밀어 중심선이 320
    row3 = {"aL": 0, "bL": 0, "cL": 120, "aR": 0, "bR": 0, "cR": 0, "hw": 200}
    lf3, rf3 = R.fits(row3)
    eq("우 차선 없음 인식", rf3 is None, True)
    eq("단독 중심선", round(R.center_x(lf3, rf3, R.y_near, 200), 1), 320.0)

    # BEV 밖 구간은 그리지 않는다
    eq("BEV 안", R._in_bev(320.0), True)
    eq("BEV 밖", R._in_bev(5000.0), False)
    segs = R._segments([(100.0, 0.0), (200.0, 1.0), (9999.0, 2.0), (300.0, 3.0),
                        (310.0, 4.0)])
    eq("구간 분할", [len(x) for x in segs], [2, 2])

    # 그리기가 실제로 되는가 (크기만)
    img = np.zeros((1080, 1920, 3), np.uint8)
    out = R.draw(img, dict(row, frame=1, theta_deg=0.0))
    eq("출력 높이 유지", out.shape[0], 1080)
    eq("원본+BEV 가로 결합", out.shape[1] > 1920, True)


def t_theta_quality():
    """θ 품질 — 편향과 진동 대역을 제대로 잡아내는가."""
    import math as _m
    from . import analyze as A
    from .contract import Contract
    c = Contract({"name": "t", "theta_quality": {
        "signal": "th", "fps": 30.0, "straight_max_rate_dps": 10.0,
        "vibration_band_hz": [0.08, 0.18]}}, "mem")

    # 상수 편향 2도 — 변화가 없으니 전부 직진으로 잡히고 bias 는 2.0
    rows = [{"th": 2.0} for _ in range(64)]
    q = A.theta_quality(rows, c)
    eq("편향 검출", round(q["bias_deg"], 3), 2.0)
    eq("전부 직진", round(q["straight_frac"], 3), 1.0)
    eq("잡음 없음", round(q["straight_std_deg"], 3), 0.0)

    # 0.12Hz 진동을 넣으면 진동 대역 파워가 지배적이어야 한다
    n, fps, f0 = 256, 30.0, 0.12
    rows = [{"th": 3.0 * _m.sin(2 * _m.pi * f0 * i / fps)} for i in range(n)]
    q2 = A.theta_quality(rows, c)
    eq("진동 대역 지배", q2["vibration_frac"] > 0.8, True)
    eq("주피크 근사", abs(q2["peak_hz"] - f0) < 0.02, True)

    # 대역 밖(2Hz)이면 비중이 낮아야 한다
    rows = [{"th": 3.0 * _m.sin(2 * _m.pi * 2.0 * i / fps)} for i in range(n)]
    eq("대역 밖은 낮음", A.theta_quality(rows, c)["vibration_frac"] < 0.1, True)

    eq("선언 없으면 빈 결과", A.theta_quality(rows, Contract({"name": "t"}, "mem")), {})


def t_expr():
    """계약 조건식 평가기 — 위험한 표현은 반드시 거부해야 한다."""
    from .expr import compile_expr, evaluate, names
    r = {"conf_eff": 0.5, "theta_deg": -8.0, "flags": 2, "mode": "GO"}
    eq("논리 결합", evaluate("conf_eff >= 0.35 and abs(theta_deg) <= 15", r), True)
    eq("대역 밖", evaluate("abs(theta_deg) <= 5", r), False)
    eq("비트 대용 나머지", evaluate("int(flags) % 4 < 2", r), False)
    eq("문자열 비교", evaluate("mode == 'GO'", r), True)
    eq("결측은 보류", evaluate("없는신호 > 1", r), None)
    eq("참조 이름", names("conf_eff >= 0.35 and abs(theta_deg) <= 15"),
       {"conf_eff", "theta_deg"})
    for bad in ["__import__('os')", "open('/etc/passwd')", "a.b", "[x for x in y]"]:
        try:
            compile_expr(bad)
            FAILS.append(f"위험한 식을 거부하지 못했다: {bad}")
        except SyntaxError:
            pass


def t_funnel():
    """받는 쪽 게이트 통과율 — 단계별 탈락과 병목 지목이 맞는가."""
    from . import analyze as A
    from .contract import Contract
    c = Contract({
        "name": "t", "signals": {},
        "consumers": [{
            "id": "cons", "label": "L",
            "stages": [
                {"name": "s1", "expr": "ok > 0"},
                {"name": "s2", "expr": "conf >= 0.5"},
                {"name": "s3", "expr": "abs(th) <= 10"},
            ],
        }],
    }, "mem")
    rows = (
        [{"ok": 1, "conf": 0.9, "th": 1.0}] * 4 +     # 전부 통과
        [{"ok": 0, "conf": 0.9, "th": 1.0}] * 2 +     # s1 탈락
        [{"ok": 1, "conf": 0.1, "th": 1.0}] * 3 +     # s2 탈락
        [{"ok": 1, "conf": 0.9, "th": 99.0}] * 1      # s3 탈락
    )
    f = A.funnel(rows, c)[0]
    eq("총 프레임", f["total"], 10)
    eq("기여 프레임", f["contributing"], 4)
    eq("기여율", round(f["rate"], 2), 0.4)
    eq("병목 지목", f["bottleneck"], "s2")
    eq("단계별 탈락", [s["dropped"] for s in f["stages"]], [2, 3, 1])
    eq("누적률 단조감소",
       all(a["cum_rate"] >= b["cum_rate"]
           for a, b in zip(f["stages"], f["stages"][1:])), True)

    # 받는 쪽 선언이 없으면 조용히 빈 결과
    eq("받는 쪽 없음", A.funnel(rows, Contract({"name": "t"}, "mem")), [])


def t_params_typing():
    """웹이 보낸 문자열이 ★원래 종류★로 돌아가는가.

    여기가 틀리면 `show_window: "false"` 처럼 문자열로 저장돼 노드가 참으로 읽는다
    (빈 문자열이 아니므로) — 파일에 쓰기 전에 한 번만 판단하는 자리다.
    """
    from .config import clean_params

    got = clean_params({"n": {"b": "false", "B": "True", "i": "50", "f": "3.0",
                              "dev": "cuda:0", "p": "/a/b.pt", "keep": 7}})["n"]
    eq("bool false", got["b"], False)
    eq("bool true", got["B"], True)
    eq("int", got["i"], 50)
    eq("float", got["f"], 3.0)
    eq("문자열은 그대로", got["dev"], "cuda:0")
    eq("경로도 문자열", got["p"], "/a/b.pt")
    eq("숫자는 손대지 않는다", got["keep"], 7)

    for bad in ({}, {"n": {}}, {"n": {"k": [1, 2]}}, {"n": {"k": "a" * 400}},
                {"n": {"k": "a\nb"}}):
        try:
            clean_params(bad)
            eq(f"거절해야 한다: {bad}", True, False)
        except ValueError:
            pass


def t_geometry():
    """BEV 기하 — 왕복 변환과 사각형 검사. 여기가 틀리면 캘리브레이션 전체가 틀린다."""
    import numpy as np
    from .geometry import (bev_to_src, quad_is_sane, src_to_bev, warp_bev)
    quad = [600, 640, 1320, 640, 1900, 1070, 20, 1070]
    W, H, BW, BH = 1920, 1080, 640, 480

    # 사각형의 네 꼭짓점은 BEV 의 네 모서리로 정확히 간다
    corners = [(0, 0), (BW, 0), (BW, BH), (0, BH)]
    for i, c in enumerate(corners):
        x, y = bev_to_src(c, quad, BW, BH)
        eq(f"모서리{i} 역변환", (round(x), round(y)),
           (quad[i * 2], quad[i * 2 + 1]))

    # 왕복 변환은 제자리로 돌아온다
    for pt in [(320, 240), (100, 400), (600, 30)]:
        back = src_to_bev(bev_to_src(pt, quad, BW, BH), quad, BW, BH)
        eq(f"왕복 {pt}", (round(back[0], 1), round(back[1], 1)),
           (round(pt[0], 1), round(pt[1], 1)))

    # 워프 결과 크기
    img = np.zeros((H, W, 3), np.uint8)
    eq("워프 크기", warp_bev(img, quad, BW, BH).shape[:2], (BH, BW))

    # 되돌릴 수 없는 배치를 잡아내는가
    eq("정상 사각형", quad_is_sane(quad, W, H)[0], True)
    eq("자기교차 감지",
       quad_is_sane([600, 640, 1320, 640, 20, 1070, 1900, 1070], W, H)[0], False)
    eq("퇴화 감지",
       quad_is_sane([600, 640, 601, 640, 602, 641, 599, 641], W, H)[0], False)


def t_verticality():
    """수직선은 0도, 기운 선은 그만큼 — 사각형 판정의 자가 맞는지."""
    import numpy as np
    from .geometry import verticality
    img = np.zeros((480, 640, 3), np.uint8)
    for x in (200, 440):
        img[40:440, x - 4:x + 4] = 255
    dev, n = verticality(img)
    eq("수직선 개수>0", n > 0, True)
    eq("수직선 편차≈0", round(dev) <= 1, True)

    img2 = np.zeros((480, 640, 3), np.uint8)
    for x0 in (200, 440):
        for y in range(40, 440):
            x = int(x0 + (y - 40) * np.tan(np.radians(20)))
            img2[y, max(0, x - 4):x + 4] = 255
    dev2, n2 = verticality(img2)
    eq("20도 기운 선 감지", n2 > 0 and 16 <= dev2 <= 24, True)


def t_calibration_contract():
    """계약의 calibration 블록만으로 파라미터를 뽑아낼 수 있는가."""
    from .calibrate import Calib
    from .contract import Contract
    c = Contract({
        "name": "t",
        "calibration": {
            "undistort": {"size": [64, 48], "K": [50, 50, 32, 24],
                          "D": [0, 0, 0, 0, 0], "alpha": 0.0},
            "bev": {"w": 32, "h": 24},
            "targets": {
                "ipm_src": {"kind": "quad", "nodes": ["n1"], "param": "src_pts"},
                "roi": {"kind": "rect", "nodes": ["n1"],
                        "params": ["x0", "y0", "x1", "y1"]},
                "px2m": {"kind": "scale", "nodes": ["n1", "n2"], "param": "p2m"},
            },
        },
    }, "mem")
    cal = Calib(c, {"n1": {"src_pts": [1, 2, 3, 4, 5, 6, 7, 8],
                           "x0": 10, "y0": 11, "x1": 12, "y1": 13, "p2m": 0.01}})
    out = cal.to_params()
    eq("quad 왕복", out["n1"]["src_pts"], [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    eq("rect 를 네 파라미터로", [out["n1"][k] for k in ("x0", "y0", "x1", "y1")],
       [10, 11, 12, 13])
    eq("scale 을 두 노드에", (out["n1"]["p2m"], out["n2"]["p2m"]), (0.01, 0.01))


def t_perturb():
    import numpy as np
    from .player import make_perturb
    img = np.full((20, 30, 3), 128, np.uint8)
    eq("none 은 무변화", make_perturb("none")(img).tolist() == img.tolist(), True)
    eq("gamma 는 밝기를 바꾼다", make_perturb("gamma:1.8")(img).mean() < 128, True)
    eq("hflip 은 크기 보존", make_perturb("hflip")(img).shape, img.shape)
    eq("연쇄 적용", make_perturb("gamma:1.8+blur:3")(img).shape, img.shape)
    n1 = make_perturb("noise:10")(img)
    n2 = make_perturb("noise:10")(img)
    eq("노이즈는 고정 시드(재현 가능)", n1.tolist() == n2.tolist(), True)


def t_config_roundtrip():
    """local.yaml 을 고쳐도 ★주석이 살아 있어야 한다★.

    웹앱의 등록 버튼이 이 경로를 탄다. yaml.safe_dump 로 왕복시키면 "왜 이렇게
    뒀는지"가 통째로 날아가는데, 그건 되돌리기도 어렵고 알아채기도 어렵다.
    실제 파일 대신 임시 복사본에서 검사한다.
    """
    import shutil
    import tempfile
    from . import config as C

    real = C.ROOT
    tmp = Path(tempfile.mkdtemp(prefix="tbcfg_"))
    try:
        (tmp / "contracts").mkdir()
        (tmp / "scenarios").mkdir()
        (tmp / "baselines").mkdir()
        src = real / "local.yaml"
        if not src.exists():
            src = real / "local.yaml.example"
        shutil.copy(src, tmp / "local.yaml")
        before = (tmp / "local.yaml").read_text()
        # 진짜로 열리는 영상을 만든다 — set_video 는 파일을 열어 확인하므로
        # 가짜 바이트를 주면 (정상적으로) 거절당한다.
        import numpy as np
        from . import encode
        w = encode.Writer(tmp / "probe.mp4", 5, (64, 48), quiet=True)
        for i in range(3):
            w.write(np.full((48, 64, 3), i * 60, np.uint8))
        w.release()
        vid = w.path
        eq("만든 영상이 브라우저에서 재생 가능하다", encode.is_web_playable(vid), True)

        C.ROOT = tmp
        try:
            # 존재하지 않는 파일은 거절한다
            try:
                C.set_video("nope", str(tmp / "missing.mp4"))
                eq("없는 파일을 거절한다", False, True)
            except ValueError:
                eq("없는 파일을 거절한다", True, True)
            # 이름 규칙 (경로 탈출 차단)
            try:
                C.set_video("../evil", str(vid))
                eq("경로 탈출 이름을 거절한다", False, True)
            except ValueError:
                eq("경로 탈출 이름을 거절한다", True, True)

            C.set_video("zz_probe", str(vid))
            got = (tmp / "local.yaml").read_text()
            eq("등록되면 줄이 생긴다", "zz_probe:" in got, True)
            C.del_video("zz_probe")
            eq("★삭제 후 원본과 완전히 같다(주석 보존)★",
               (tmp / "local.yaml").read_text(), before)
        finally:
            C.ROOT = real
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════
#  구간 · 전이 · 로그 — 단계적으로 개입하는 노드를 판정하는 도구
# ══════════════════════════════════════════════════════════════════════
def _rows_stage():
    """0단 대기 → 1단 → 2단 으로 올라가는 가짜 주행 한 판.

    frame 은 30fps 영상의 프레임 번호로 읽는다(0.0333초/프레임).
    """
    rows = []
    for i in range(30):
        f = 100 + i
        br = 0 if i < 10 else (1 if i < 22 else 2)
        rows.append({"frame": f, "b": br, "d": 100.0 - 3.0 * i,
                     "w": (br == 0 and i >= 3)})
    return rows


def t_spans():
    rows = _rows_stage()
    st = A.span_stats(rows, "w", 30.0)
    eq("구간 count", st["count"], 7)
    eq("구간 개수", st["runs"], 1)
    eq("구간 길이[s]", round(st["run_max_s"], 3), round(7 / 30.0, 3))
    eq("구간 첫 프레임", st["first_frame"], 103)
    # 조건이 두 토막이면 runs 가 2 여야 한다
    st2 = A.span_stats(rows, "b == 0 or b == 2", 30.0)
    eq("두 토막", st2["runs"], 2)
    # 값이 없는 행은 구간을 끊지 않고 건너뛴다
    rows2 = [{"frame": 1, "w": True}, {"frame": 2}, {"frame": 3, "w": True}]
    eq("결측은 안 끊는다", A.span_stats(rows2, "w", 30.0)["runs"], 1)


def t_events():
    rows = _rows_stage()
    ev = A.find_events(rows, "b:0->1", 30.0)
    eq("0->1 한 번", len(ev), 1)
    eq("0->1 프레임", ev[0]["frame"], 110)
    eq("0->1 시각[s]", round(ev[0]["t_s"], 3), round(10 / 30.0, 3))
    eq("전이 순간의 다른 값", rows[ev[0]["i"]]["d"], 70.0)
    eq("와일드카드", len(A.find_events(rows, "b:*->2", 30.0)), 1)
    eq("없는 전이", len(A.find_events(rows, "b:2->0", 30.0)), 0)
    # 문자열 상태도 같은 문법으로
    cats = [{"frame": i, "s": ("UNKNOWN" if i < 3 else "RED")} for i in range(6)]
    eq("문자열 전이", len(A.find_events(cats, "s:UNKNOWN->RED", 30.0)), 1)
    # 규격이 틀리면 조용히 넘어가지 않는다
    try:
        A.parse_event("b:0")
        FAILS.append("전이 규격 오류를 안 잡았다")
    except ValueError:
        pass


def t_check_kinds():
    """새 체크 문법이 그대로 판정으로 이어지는가."""
    c = _contract(signals={"b": {"topic": "/b", "path": ["data"]},
                           "d": {"topic": "/d", "path": ["data"]},
                           "w": {"topic": "/w", "path": ["data"]}},
                  flag_bits={})
    rows = _rows_stage()
    summ = {"meta": {"video_fps": 30.0}, "scene_fps": 30.0,
            "log_events": {"ok": {"count": 2}, "bad": {"count": 0}}}
    checks = [
        {"where": "w", "stat": "run_max_s", "min": 0.2},
        {"where": "w and b > 0", "stat": "count", "max": 0},
        {"event": "b:0->1", "stat": "at:d", "min": 65, "max": 75},
        {"event": "b:1->2", "stat": "count", "min": 1},
        {"signal": "b", "stat": "decreases", "max": 0},
        {"signal": "d", "where": "b == 1", "stat": "max", "max": 71.0},
        {"stat": "log:ok", "min": 1},
        {"stat": "log:bad", "max": 0},
        {"stat": "log:없는것", "min": 1},
    ]
    got = A.run_checks(summ, rows, c, checks)
    eq("체크 개수", len(got), 9)
    eq("전부 통과", [g["ok"] for g in got][:8], [True] * 8)
    eq("없는 로그이벤트는 판정보류", got[8]["ok"], None)
    # 라벨에 무엇으로 잰 것인지 남는가 (리포트 가독성)
    eq("전이 라벨", got[2]["check"], "b:0->1.at:d")


def t_hold_initial():
    """변화분만 발행되는 신호의 ★첫 전이★ 를 잃지 않는가."""
    import json as _json
    import tempfile
    #  /z 는 프레임마다 오는 신호(행을 만드는 쪽), /b 는 ★변할 때만★ 오는 신호다.
    c = _contract(signals={"b": {"topic": "/b", "path": ["data"]},
                           "z": {"topic": "/z", "path": ["data"]}},
                  hold_signals=["b"], hold_initial={"b": 0}, flag_bits={})
    recs = [{"t": 0.5, "frame": 0, "t_frame": 0.4, "topic": "/z", "msg": {"data": 1}},
            {"t": 1.0, "frame": 1, "t_frame": 0.9, "topic": "/z", "msg": {"data": 1}},
            {"t": 1.0, "frame": 1, "t_frame": 0.9, "topic": "/b", "msg": {"data": 2}}]
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        for r in recs:
            f.write(_json.dumps(r) + "\n")
        path = f.name
    rows, _ = A.build_table(path, c, 0)
    eq("발행 전은 초기값", rows[0]["b"], 0)
    eq("0->2 전이가 남는다", len(A.find_events(rows, "b:0->2", 30.0)), 1)


def t_scene_fps():
    eq("메타 우선", A.scene_fps({"video_fps": 25.0}), 25.0)
    eq("배속을 곱한다", A.scene_fps({"video_fps": 30.0, "rate": 0.25}), 7.5)
    eq("없으면 30", A.scene_fps({}), 30.0)
    eq("계약 폴백", A.scene_fps({}, _contract(theta_quality={"fps": 15.0})), 15.0)


def t_overlay():
    """합성 자극 — 그림이 실제로 그 자리에 얹히는가."""
    import numpy as np
    import tempfile
    import cv2 as _cv2
    from .player import Overlay
    mock = np.zeros((30, 90, 3), np.uint8)
    mock[:, :] = (0, 0, 255)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    _cv2.imwrite(path, mock)
    ov = Overlay({"image": path, "x": 10, "y": 20, "width": 90, "from": 0.5})
    eq("로드", ov.ok, True)
    frame = np.zeros((200, 300, 3), np.uint8)
    out = ov(frame.copy(), 0.2)
    eq("from 이전에는 안 그린다", int(out.sum()), 0)
    out = ov(frame.copy(), 0.7)
    eq("그린다", int(out[25, 50, 2]), 255)
    eq("그 밖은 그대로", int(out[100, 200, 2]), 0)
    eq("빈 규격은 꺼진다", Overlay({}).ok, False)


def t_bev_rows():
    """캘리브 대상 bev_row / bev_dist — 기준선을 옮기면 문턱이 따라온다."""
    c = _contract(calibration={
        "undistort": {"size": [64, 48], "K": [30, 30, 32, 24], "D": [0, 0, 0, 0, 0]},
        "bev": {"w": 64, "h": 48},
        "targets": {
            "quad": {"kind": "quad", "nodes": ["n"], "param": "q",
                     "default": [10, 20, 50, 20, 60, 48, 4, 48]},
            "bump": {"kind": "bev_row", "nodes": ["n"], "param": "bp", "default": 48.0},
            "b1": {"kind": "bev_dist", "nodes": ["n"], "param": "d1", "default": 30.0},
            "b2": {"kind": "bev_dist", "nodes": ["n"], "param": "d2", "default": 10.0},
        }})
    from .calibrate import Calib
    cal = Calib(c, {})
    eq("기준선 인식", cal.bumper_key, "bump")
    eq("기준선 값", cal.bumper_y(), 48.0)
    eq("문턱 행", cal.row_y("b1"), 18.0)          # 48 - 30
    cal.bev_rows["bump"] = 60.0                    # 범퍼가 BEV 밖(차체에 가림)
    eq("기준선을 옮기면 따라온다", cal.row_y("b1"), 30.0)
    cal.set_row_y("b2", 45.0)                      # BEV 45행을 찍었다
    eq("찍은 행 → 거리", cal.bev_rows["b2"], 15.0)
    p = cal.to_params()["n"]
    eq("저장 형식", (p["bp"], p["d1"], p["d2"]), (60.0, 30.0, 15.0))
    eq("순서는 기준선 먼저", cal.bev_keys()[0], "bump")


def t_calib_drift():
    """계약의 `default:` 가 노드 기본값과 어긋난 것을 잡는가. [2026-08-25]

    ★이 드리프트는 판정을 안 건드려서 조용하다★ 그림과 게이트 통과율만 바뀐다.
    실제로 night_b 에서 사다리꼴이 어긋난 채 거리선이 205px 먼 곳에 그려졌고,
    멀쩡한 판정값을 버그로 읽었다.
    """
    from .lint import lint_calibration_drift as drift
    c = _contract(calibration={"targets": {
        "quad": {"kind": "quad", "nodes": ["n"], "param": "q",
                 "default": [10, 20, 50, 20, 60, 48, 4, 48]},
        "bump": {"kind": "bev_row", "nodes": ["n"], "param": "bp", "default": 48.0},
        "roi": {"kind": "rect", "nodes": ["n"],
                "params": ["x0", "y0", "x1", "y1"], "default": [0, 0, 64, 32]},
        "nodef": {"kind": "scale", "nodes": ["n"], "param": "s"},   # default 없음
    }})
    eq("캐시가 없으면 아무 말 안 한다", drift(c, {}), [])
    eq("같으면 조용하다", drift(c, {"n": {
        "q": [10, 20, 50, 20, 60, 48, 4, 48], "bp": 48.0,
        "x0": 0, "y0": 0, "x1": 64, "y1": 32}}), [])
    eq("int/float 차이는 드리프트가 아니다", drift(c, {"n": {"bp": 48}}), [])
    eq("리스트 한 칸만 달라도 잡는다",
       len(drift(c, {"n": {"q": [10, 20, 50, 20, 60, 48, 4, 99]}})), 1)
    eq("여러 파라미터짜리 대상도 잡는다",
       len(drift(c, {"n": {"x0": 0, "y0": 0, "x1": 64, "y1": 99}})), 1)
    #  ★캐시에 없는 파라미터로 경고를 만들지 않는다★ 노드가 그 값을 선언하지
    #  않는 것과 '다르다' 는 것은 다른 이야기다 — 여기서 떠들면 늘 빨개진다.
    eq("캐시에 없으면 넘어간다", drift(c, {"n": {"관계없는것": 1}}), [])
    eq("default 없는 대상은 대조 안 한다", drift(c, {"n": {"s": 0.5}}), [])

    #  ★이 저장소의 계약은 캐시와 맞아야 한다★ (캐시가 있을 때만 본다 —
    #  다른 기계에는 runs/_params/ 가 없다)
    from .run import load_ws_params
    from .contract import load as _load
    for f in sorted((Path(__file__).resolve().parent.parent / "contracts").glob("*.yaml")):
        try:
            cc = _load(f)
        except Exception:      # noqa: BLE001
            continue           # 초안 계약은 t_repo_contracts 가 따로 본다
        ws = load_ws_params(cc)
        if ws:
            eq(f"{f.name} 의 default 가 노드와 같은가", drift(cc, ws), [])


def t_calib_provenance():
    """기하가 바뀌면 ★기준과 비교할 수 없다★ 고 말해 주는가. [2026-08-25]

    사다리꼴·범퍼행이 바뀌면 sl_px 의 뜻 자체가 바뀐다. 그런데 그 값들은 아무도
    ★요청★ 하지 않아 meta 의 params 에 안 남는다 — 노드 기본값이 조용히 바뀌면
    조건은 '같다' 로 나오고, 회귀 비교가 ★기하 변화를 노드 회귀로 읽는다★.
    """
    import tempfile
    from pathlib import Path as _P
    from .run import _calib_snapshot, _provenance_diff
    c = _contract(calibration={
        "undistort": {"param": "und"},
        "targets": {
            "quad": {"kind": "quad", "nodes": ["n"], "param": "q"},
            "bump": {"kind": "bev_row", "nodes": ["n"], "param": "bp"},
        }},
        nodes=[{"id": "n", "package": "p", "executable": "e"}])
    with tempfile.TemporaryDirectory() as d:
        rd = _P(d)
        eq("params_actual 없으면 None", _calib_snapshot(c, rd), None)
        (rd / "params_actual.yaml").write_text(
            "params:\n  n:\n    und: true\n    q: [1, 2, 3, 4, 5, 6, 7, 8]\n"
            "    bp: 480.0\n    무관한것: 9\n")
        snap = _calib_snapshot(c, rd)
        eq("캘리브 대상만 담는다", snap,
           {"n": {"und": True, "q": [1, 2, 3, 4, 5, 6, 7, 8], "bp": 480.0}})
        #  ★가중치 하나 늘었다고 기준이 죽으면 안 된다★ 그래서 대상만 담는다
        eq("무관한 파라미터는 조건이 아니다", "무관한것" in snap["n"], False)

    now = {"n": {"und": True, "q": [1, 2, 3, 4, 5, 6, 7, 8], "bp": 645.0}}
    eq("범퍼행이 바뀌면 비교 불가",
       [k for k, _a, _b in _provenance_diff({"calib": snap}, {"calib": now})],
       ["calib"])
    eq("같으면 조용하다", _provenance_diff({"calib": snap}, {"calib": snap}), [])
    #  옛 런에는 calib 이 없다(None) — None 끼리는 같으므로 종전 기준이 그대로 산다
    eq("옛 런끼리는 종전대로", _provenance_diff({}, {}), [])


def t_render_params():
    """오버레이가 ★그 런이 실제로 쓴 값★ 으로 그려지는가. [2026-08-25]

    여기가 틀리면 판정값은 멀쩡한데 그림만 엉뚱한 곳에 얹혀, 멀쩡한 값을 버그로
    읽게 된다(night_b 에서 실제로 그랬다 — 사다리꼴이 어긋나 거리선이 205px 먼
    곳에 그려졌다). 그림은 조용히 틀리기 때문에 검사로 잡아야 한다.
    """
    import json as _j
    import tempfile
    from pathlib import Path as _P
    from .harvest import effective_params
    from .render import Renderer, _target_value
    c = _contract(calibration={
        "bev": {"w": 64, "h": 48},
        "targets": {
            "quad": {"kind": "quad", "nodes": ["n"], "param": "q",
                     "default": [10, 20, 50, 20, 60, 48, 4, 48]},
            "bump": {"kind": "bev_row", "nodes": ["n"], "param": "bp",
                     "default": 48.0},
            "b1": {"kind": "bev_dist", "nodes": ["n"], "param": "d1",
                   "default": 30.0},
            "sc": {"kind": "scale", "nodes": ["n"], "param": "s", "default": 0.01},
        }},
        render={"bev_dist": {"distance": "a", "missing": -1}})

    # ── ① 값이 여럿인 대상은 ★리스트 그대로★ 나와야 한다 ──────────────
    #   여기서 첫 개만 돌려주던 탓에 quad 가 reshape(4,2) 에서 죽었다.
    t = c.raw["calibration"]["targets"]["quad"]
    eq("리스트 파라미터는 통째로",
       _target_value(t, {"n": {"q": [1, 2, 3, 4, 5, 6, 7, 8]}}),
       [1, 2, 3, 4, 5, 6, 7, 8])
    eq("스칼라 파라미터",
       _target_value(c.raw["calibration"]["targets"]["bump"], {"n": {"bp": 60.0}}),
       [60.0])

    # ── ② params 에 사다리꼴이 있으면 그것으로 그린다 ──────────────────
    R = Renderer(c, {"n": {"q": [1, 2, 33, 2, 44, 48, 5, 48], "bp": 40.0, "d1": 12.0}})
    eq("params 사다리꼴 4×2", R.quad.reshape(-1).tolist(),
       [1.0, 2.0, 33.0, 2.0, 44.0, 48.0, 5.0, 48.0])
    eq("params 사다리꼴이면 경고 없음", R.quad_guessed, False)
    eq("params 기준선", R._bumper_y(), 40.0)
    eq("params 문턱 행", R._row_y("bev_dist", 12.0), 28.0)      # 40 - 12

    # ── ③ 없으면 계약 default 로 떨어지되 ★그 사실을 남긴다★ ──────────
    R2 = Renderer(c, {})
    eq("default 사다리꼴", R2.quad.reshape(-1).tolist(),
       [10.0, 20.0, 50.0, 20.0, 60.0, 48.0, 4.0, 48.0])
    eq("default 로 떨어지면 경고", R2.quad_guessed, True)
    eq("default 기준선", R2._bumper_y(), 48.0)
    eq("default 배율", R2.bd_px2m, 0.01)

    # ── ④ 실효값(params_actual) 이 요청값(summary.meta) 을 이긴다 ──────
    with tempfile.TemporaryDirectory() as d:
        rd = _P(d)
        (rd / "summary.json").write_text(_j.dumps(
            {"summary": {"meta": {"params": {"n": {"q": [0, 0, 1, 0, 1, 1, 0, 1]}}}}}))
        eq("params_actual 없으면 meta", effective_params(rd),
           {"n": {"q": [0, 0, 1, 0, 1, 1, 0, 1]}})
        (rd / "params_actual.yaml").write_text(
            "params:\n  n:\n    q: [9, 9, 8, 9, 8, 8, 9, 8]\n")
        eq("params_actual 이 우선", effective_params(rd),
           {"n": {"q": [9, 9, 8, 9, 8, 8, 9, 8]}})
        (rd / "params_actual.yaml").write_text("이건: [깨진\n  yaml")
        eq("깨진 덤프는 meta 로 폴백", effective_params(rd),
           {"n": {"q": [0, 0, 1, 0, 1, 1, 0, 1]}})


def t_aux_schedule():
    """타임라인 주입 — 계단 값 선택과 계약↔시나리오 병합. [2026-08-25]

    ★여기가 틀리면 전이 프레임의 값이 어긋나 정지선 게이트가 엉뚱하게 열린다.★
    """
    from .player import sched_value
    pts = [(0, 0.0), (300, 15.0), (520, 40.0)]
    eq("첫 점 이전은 None", sched_value([(300, 15.0)], 299), None)
    eq("정확히 그 프레임", sched_value(pts, 300), 15.0)
    eq("사이는 앞 값 유지", sched_value(pts, 400), 15.0)
    eq("경계 직전", sched_value(pts, 519), 15.0)
    eq("다음 점부터", sched_value(pts, 520), 40.0)
    eq("끝 이후도 유지", sched_value(pts, 99999), 40.0)
    eq("0프레임부터 상수", sched_value([(0, 15.0)], 0), 15.0)

    #  계약의 aux 정의(토픽·타입·저작키)에 시나리오의 schedule 이 얹히는가.
    #  ★계약은 안 바뀐다★ — run.py 가 새 dict 를 만든다.
    from .run import _deep_merge
    contract_aux = [{"topic": "/tl_enable", "type": "std_msgs/msg/Bool",
                     "fields": {"data": True}},
                    {"topic": "/tl/fake_box_h", "type": "std_msgs/msg/Float32",
                     "field": "data", "keys": {"f": {"value": 15.0}}}]
    sc_sched = {"/tl/fake_box_h": {0: 0.0, 300: 15.0}}
    va_sched = {}
    sched = _deep_merge(sc_sched, va_sched)
    merged = [dict(a, schedule=sched[a["topic"]]) if a["topic"] in sched else a
              for a in contract_aux]
    eq("허락 항목은 그대로", merged[0].get("schedule"), None)
    eq("주입 항목에 schedule 얹힘", merged[1]["schedule"], {0: 0.0, 300: 15.0})
    eq("계약 원본은 불변", contract_aux[1].get("schedule"), None)
    eq("저작키는 보존", merged[1]["keys"], {"f": {"value": 15.0}})


def t_webapp_js():
    """웹앱 스크립트가 ★문법상 성한가★.

    이 앱은 브라우저가 실행하므로 여기서 안 보고 넘기면 화면이 통째로 백지가 된다
    (서버는 정상이라 로그에도 안 남는다). esprima 가 있으면 파싱해 본다.
    """
    try:
        import esprima
    except ImportError:
        return                                     # 없으면 조용히 건너뛴다
    root = Path(__file__).resolve().parent.parent
    for name in ("app.js", "plot.js"):
        f = root / "web" / name
        if not f.exists():
            continue
        try:
            esprima.parseScript(f.read_text())
        except Exception as e:                      # noqa: BLE001
            FAILS.append(f"web/{name} 문법 오류: {e}")


def t_contract_ui():
    """웹 화면 설정이 계약에서 나오는가 — 박아 두면 계약마다 화면을 고쳐야 한다."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from web.server import contract_ui
    c = _contract(frame_presets=[{"label": "전부", "where": ""},
                                 {"label": "튀는 값", "where": "a > 1", "default": True}],
                  frame_columns=["a", "th"],
                  events={"signal": "st", "at": ["a"], "label": "상태"})
    ui = contract_ui(c)
    eq("프리셋 수", len(ui["frame_presets"]), 2)
    eq("기본 프리셋", [p["label"] for p in ui["frame_presets"] if p["default"]], ["튀는 값"])
    eq("표의 열", ui["frame_columns"], ["a", "th"])
    eq("플래그 신호", ui["flag_signal"], "a")
    eq("전이 대상", ui["events"][0]["signal"], "st")
    #  선언이 없으면 회귀 비교 대상에서 뽑는다 (빈 표가 되지 않게)
    eq("열 폴백", contract_ui(_contract())["frame_columns"], ["a", "th"])


def t_clone_scenario():
    """★있는 시나리오를 본으로 떠서★ 영상만 갈아 끼우는가 (주석이 남아야 한다)."""
    import shutil
    from . import config as C
    src = C.ROOT / "scenarios" / "_selftest_src.yaml"
    out = C.ROOT / "scenarios" / "_selftest_out.yaml"
    src.write_text("# 본의 주석 — 이게 남아야 한다\n"
                   "name: src\ncontract: contracts/x.yaml\n"
                   "video: old            # 논리 이름\n"
                   "mode: lockstep\nstart: 0\nlimit: 0\n"
                   "checks:\n  - {stat: drop_rate, max: 0.02, why: '판정도 남아야 한다'}\n")
    try:
        C.clone_scenario("_selftest_src.yaml", "_selftest_out", "newvid",
                         start=100, limit=50, mode="realtime", note="메모 한 줄")
        t = out.read_text()
        eq("이름 교체", "name: _selftest_out" in t, True)
        eq("영상 교체", "video: newvid" in t, True)
        eq("영상 주석 유지", "# 논리 이름" in t, True)
        eq("구간 교체", ("start: 100" in t and "limit: 50" in t), True)
        eq("모드 교체", "mode: realtime" in t, True)
        eq("본의 주석 유지", "# 본의 주석" in t, True)
        eq("판정 유지", "drop_rate" in t, True)
        eq("메모 기록", "메모 한 줄" in t, True)
        try:
            C.clone_scenario("_selftest_src.yaml", "_selftest_out", "v2")
            FAILS.append("이미 있는 이름을 덮어썼다")
        except ValueError:
            pass
    finally:
        for f in (src, out):
            if f.exists():
                f.unlink()
        del shutil


def t_compose_scenario():
    """★여러 시나리오의 판정을 합쳐★ 새 시나리오를 만드는가 — 겹치는 항목은 한 번만."""
    from . import config as C
    a = C.ROOT / "scenarios" / "_selftest_a.yaml"
    b = C.ROOT / "scenarios" / "_selftest_b.yaml"
    out = C.ROOT / "scenarios" / "_selftest_composed.yaml"
    a.write_text(
        "name: a\ncontract: contracts/x.yaml\nvideo: old\n"
        "mode: lockstep\nstart: 0\nlimit: 0\n"
        "checks:\n"
        "  - {stat: drop_rate, max: 0.02, why: 겹치는 판정}\n"
        "  - {signal: sig_a, stat: mean, min: 1, why: a 고유 판정}\n"
        "compare_tol:\n  _default: 1.0e-6\n  x: 0.1\n")
    b.write_text(
        "name: b\ncontract: contracts/x.yaml\nvideo: old2\n"
        "mode: lockstep\nstart: 0\nlimit: 0\n"
        "checks:\n"
        "  - {stat: drop_rate, max: 0.02, why: 겹치는 판정}\n"
        "  - {signal: sig_b, stat: max, max: 5, why: b 고유 판정}\n"
        "compare_tol:\n  _default: 1.0e-6\n  x: 0.2\n")
    try:
        r = C.compose_scenario(["_selftest_a.yaml", "_selftest_b.yaml"],
                               "_selftest_composed", "newvid", start=10, limit=20)
        d = None
        import yaml as Y
        t = out.read_text()
        d = Y.safe_load(t)
        eq("이름 교체", d["name"], "_selftest_composed")
        eq("영상 교체(첫 본 기준)", d["video"], "newvid")
        eq("구간 교체", (d["start"], d["limit"]), (10, 20))
        #  겹치는 판정은 한 번만, 고유 판정은 둘 다
        eq("판정 개수 — 겹치는 것 dedup", len(d["checks"]), 3)
        why = [c.get("why") for c in d["checks"]]
        eq("a 고유 판정 유지", "a 고유 판정" in why, True)
        eq("b 고유 판정 유지", "b 고유 판정" in why, True)
        #  compare_tol 충돌(x: 0.1 vs 0.2) — 먼저 온 본(a)이 이기고 경고가 남는다
        eq("충돌 시 첫 본이 이긴다", d["compare_tol"]["x"], 0.1)
        eq("충돌 경고 기록", any("compare_tol.x" in w for w in r["warnings"]), True)
        eq("충돌 경고가 파일에도 남는다", "compare_tol 충돌" in t, True)

        #  본이 하나뿐이면 거부 — clone_scenario 를 쓰라고 안내
        try:
            C.compose_scenario(["_selftest_a.yaml"], "_x", "v")
            FAILS.append("본 1개인데 compose 를 허용했다")
        except ValueError:
            pass
        #  계약이 다르면 거부
        c = C.ROOT / "scenarios" / "_selftest_c.yaml"
        c.write_text("name: c\ncontract: contracts/y.yaml\nvideo: v\n"
                     "checks:\n  - {stat: drop_rate, max: 0.02, why: 다른 계약}\n")
        try:
            C.compose_scenario(["_selftest_a.yaml", "_selftest_c.yaml"], "_y", "v")
            FAILS.append("계약이 다른데 compose 를 허용했다")
        except ValueError:
            pass
        c.unlink()
    finally:
        for f in (a, b, out):
            if f.exists():
                f.unlink()


def t_commands_wired():
    """웹의 허용 명령이 ★실제로 존재하는 CLI★ 를 가리키는가.

    허용 목록(web/server.py 의 COMMANDS)과 CLI 가 어긋나면 화면에는 버튼이 보이는데
    누르면 죽는다 — 서버는 백그라운드로 띄우고 끝이라 오류가 로그에만 남는다.
    """
    import importlib.util
    import re as R
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("_srv", root / "web" / "server.py")
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)

    #  tb.run 이 실제로 받는 서브커맨드 (파서를 세우지 않고 소스에서 읽는다 —
    #  파서를 세우면 ROS 가 없는 기계에서 import 가 무거워진다)
    src = (root / "tb" / "run.py").read_text()
    subs = set(R.findall(r'sub\.add_parser\(\s*"([a-z_]+)"', src))
    for cid, c in srv.COMMANDS.items():
        mod = c["module"]
        if mod[0] != "tb.run":
            eq(f"{cid} 모듈 파일", (root / "tb" / f"{mod[0].split('.')[-1]}.py").exists(), True)
            continue
        eq(f"{cid} → tb.run {mod[1]}", mod[1] in subs, True)

    #  ★고치고 다시 돌리는 고리★ 가 웹에서 전부 되는가 (빌드 → 점검 → 실행)
    for need in ("build", "doctor", "run"):
        eq(f"웹에 {need} 가 있다", need in srv.COMMANDS, True)


def t_needs_rebuild():
    """★빌드가 정말 필요한 것★ 만 가려내는가 — 아니면 코드 고칠 때마다 거짓 경보다."""
    from .config import needs_rebuild
    #  심볼릭 링크로 그대로 반영되는 것들 (빌드 불필요)
    eq("모듈 파이썬", needs_rebuild(["white1/traffic_light.py"]), [])
    eq("런치 파이썬", needs_rebuild(["white1/launch/master.launch.py"]), [])
    eq("데이터 yaml", needs_rebuild(["white1/calibration/cam.yaml"]), [])
    #  링크로 해결되지 않는 것들 (빌드 필요)
    eq("setup.py", needs_rebuild(["white1/setup.py"]), ["white1/setup.py"])
    eq("package.xml", needs_rebuild(["white1/package.xml"]), ["white1/package.xml"])
    eq("C++", needs_rebuild(["nxde/src/a.cpp"]), ["nxde/src/a.cpp"])
    #  섞여 있으면 빌드가 필요한 것만 골라낸다
    eq("섞임", needs_rebuild(["a/traffic_light.py", "a/setup.py", "b/x.hpp"]),
       ["a/setup.py", "b/x.hpp"])


def t_progress_total():
    """진행률 분모 — ★오버레이의 '다가오는 속도' 가 이걸 쓴다★ (표시만이 아니다)."""
    from .player import progress_total
    #  limit 은 이미 ★투입 장수★ 라 stride 로 나누면 안 된다 (robustness.yaml)
    eq("limit+stride", progress_total(200, 780, 2, 2191), 200)
    eq("limit+stride1", progress_total(90, 1340, 1, 2191), 90)
    #  limit 이 없을 때만 남은 원본 프레임을 투입 장수로 환산한다
    eq("limit 없음+stride", progress_total(0, 780, 2, 2191), (2191 - 780) // 2)
    eq("limit 없음", progress_total(0, 0, 1, 2191), 2191)
    eq("start 가 영상보다 뒤", progress_total(0, 9999, 1, 2191), 0)


def t_scenario_of_run():
    """런이 ★자기가 쓴 시나리오★ 를 기억하는가 — 못 찾으면 판정이 통째로 사라진다."""
    import json as J
    from . import run as R
    d = Path(tempfile.mkdtemp()) / "r"
    d.mkdir()
    sp = R.ROOT / "scenarios" / "_selftest_scen.yaml"
    sp.write_text("name: _selftest_scen\ncontract: contracts/x.yaml\n")
    try:
        #  ① meta 에 경로가 있으면 그것을 쓴다
        (d / "summary.json").write_text(J.dumps(
            {"summary": {"meta": {"scenario_file": str(sp)}}}))
        eq("경로로 찾는다", R.scenario_of_run(d), str(sp))
        #  ② 옛 런은 경로가 없다 — 이름으로 되찾는다
        (d / "summary.json").write_text(J.dumps(
            {"summary": {"meta": {"scenario": "_selftest_scen"}}}))
        eq("이름으로 되찾는다", R.scenario_of_run(d), str(sp))
        #  ③ 이름이 바뀌어 못 찾으면 None — 부르는 쪽이 경고를 띄운다
        (d / "summary.json").write_text(J.dumps(
            {"summary": {"meta": {"scenario": "없는이름"}}}))
        eq("못 찾으면 None", R.scenario_of_run(d), None)
    finally:
        if sp.exists():
            sp.unlink()


def t_lint():
    """이름 오타를 ★실행 전에★ 잡는가 — 잡히면 그 판정이 조용히 사라지지 않는다."""
    from .lint import lint
    from . import config as C
    c = Contract({
        "name": "t", "version": 1,
        "signals": {"sl_px": {"topic": "/a", "path": ["data"]},
                    "tl_state": {"topic": "/b", "path": ["data"]}},
        "hold_signals": ["sl_px", "sl_pix"],           # 오타 1
        "log_events": {"boot_ok": {"node": "n", "pattern": "x"}},
        "consumers": [{"id": "gate", "stages": [{"name": "s", "expr": "sl_px >= 0"}]}],
        "frame_presets": [{"label": "p", "where": "sl_pxx >= 0"}],   # 오타 2
    }, "mem")
    got = lint(c, {"checks": [
        {"signal": "sl_p", "stat": "mean"},            # 오타 3
        {"where": "sl_wait", "stat": "frac"},          # 오타 4
        {"stat": "drop_ratio", "max": 0.02},           # 오타 5 (요약 지표)
        {"stat": "log:boot_okk", "min": 1},            # 오타 6
        {"stat": "contribution:gate2", "min": 0.3},    # 오타 7
        {"where": "sl_px >= 0", "stat": "mean"},       # 오타 8 (구간에 mean 은 없다)
        # ↓ 전부 정상이라 걸리면 안 된다
        {"stat": "drop_rate", "max": 0.02},
        {"where": "tl_state == 'RED' and sl_px < 0", "stat": "frac", "min": 0.1},
        {"signal": "sl_px", "where": "sl_px >= 0", "stat": "p95_abs_diff", "max": 60},
        {"event": "sl_px:0->1", "stat": "at:tl_state"},
    ], "compare_tol": {"_default": 1e-6, "sl_px": 1.0}})
    eq("오타 8건을 전부 잡는가", len(got), 8)
    #  문자열 리터럴('RED')을 신호로 오인하면 정상 시나리오가 통째로 경고가 된다
    eq("문자열 리터럴은 신호가 아니다", any("RED" in m for m in got), False)
    #  계약 쪽 오타는 빼고 본다 — 이 계약에는 일부러 심어 둔 것이 둘 있다
    from .lint import lint_scenario
    eq("내장 열은 신호로 친다", lint_scenario(c, {"checks": [
        {"signal": "latency_ms", "stat": "p95"}]}), [])

    #  ★이 저장소의 계약·시나리오는 전부 깨끗해야 한다★ — 여기가 빨개지면 방금
    #  고친 YAML 이 판정을 조용히 잃은 것이다(린터가 틀린 것이 아니라).
    for f in sorted((C.ROOT / "scenarios").glob("*.yaml")):
        r = C.resolve_scenario(f.name)
        bad = [m for m in (r.get("warn") or []) if m.startswith("이름이 안 맞는다")]
        eq(f"{f.name} 이름 정합", bad, [])


def t_aux_schedule_write():
    """저작한 타임라인을 시나리오에 쓴다 — ★다른 내용·주석을 안 건드리는가★. [2026-08-25]

    줄 단위 수술이라 파일 모양이 예상과 다르면 조용히 엉뚱한 곳에 붙는다. 여기가
    틀리면 --watch 저작이 시나리오를 망가뜨린다.
    """
    import yaml as Y
    from . import config as C
    scen = C.ROOT / "scenarios" / "_selftest_aux.yaml"
    try:
        # ── ① aux_schedule 이 이미 있는 시나리오 — 블록을 갈아 끼운다 ──────
        scen.write_text(
            "name: x\n"
            "contract: c.yaml\n"
            "video: v\n"
            "aux_schedule:\n"
            "  # 손으로 쓴 설명 — 값이 바뀌면 이 주석은 사라져야 맞다\n"
            '  "/tl/fake_box_h": {0: 15.0}\n'
            "params:\n"
            "  traffic_light:\n"
            "    show_window: false   # 이 주석은 ★반드시★ 살아야 한다\n"
            "variants:\n"
            "  - {name: base, perturb: none}\n")
        C.set_aux_schedule("_selftest_aux.yaml", {"/tl/fake_box_h": {0: 0.0, 543: 35.0}})
        txt = scen.read_text()
        d = Y.safe_load(txt)
        eq("스케줄이 갈렸다", d["aux_schedule"]["/tl/fake_box_h"], {0: 0.0, 543: 35.0})
        eq("params 주석 보존", "이 주석은 ★반드시★ 살아야 한다" in txt, True)
        eq("다른 키 보존", (d["name"], d["video"]), ("x", "v"))
        eq("params 값 보존", d["params"]["traffic_light"]["show_window"], False)
        eq("variants 보존", d["variants"][0]["name"], "base")
        eq("저작 표식 부착", "저작됨" in txt, True)
        eq("손주석은 사라진다", "손으로 쓴 설명" in txt, False)

        # ── ② aux_schedule 이 없던 시나리오 — 새로 넣는다 ────────────────
        scen.write_text(
            "name: y\n"
            "contract: c.yaml\n"
            "video: v   # 이 주석도 살아야 한다\n"
            "params: {}\n")
        C.set_aux_schedule("_selftest_aux.yaml", {"/t": {5: 1}})
        d2 = Y.safe_load(scen.read_text())
        eq("없던 스케줄 추가", d2["aux_schedule"]["/t"], {5: 1})
        eq("추가해도 주석 보존", "이 주석도 살아야 한다" in scen.read_text(), True)

        # ── ③ 빈 스케줄은 거부한다 ───────────────────────────────────────
        try:
            C.set_aux_schedule("_selftest_aux.yaml", {})
            eq("빈 스케줄 거부", "안 막음", "막아야 함")
        except ValueError:
            pass
    finally:
        scen.unlink(missing_ok=True)


def t_feedback_observations():
    """★판정이 0개여도 문서에 알맹이가 있는가★ — 탐색용 실행의 핵심이다.

    checks: 가 없을 때 "실패 0건"으로 읽히면 다 정상인 줄 안다. 그리고 그때야말로
    관측값(신호 통계·전이·로그)이 문서의 전부여야 한다.
    """
    import json as J
    import tempfile
    from . import feedback as F
    summary = {
        "rows": 100, "valid_rows": 100, "valid_rate": 1.0, "drop_rate": 0.0,
        "meta": {"workspace": "/ws", "scenario": "s", "mode": "lockstep"},
        "signals": {
            "dist_m": {"kind": "num", "n": 100, "mean": 2.5, "std": 0.4,
                       "min": 0.1, "max": 9.9, "p95": 3.0,
                       "increases": 40, "decreases": 60},
            "state": {"kind": "cat", "n": 100,
                      "counts": {"IDLE": 90, "GO": 10}, "transitions": 4},
        },
        "events": [{"signal": "state", "label": "상태", "at": ["dist_m"],
                    "why": "한 방향으로만 가야 한다",
                    "transitions": [{"frame": 50, "t_s": 1.67, "from": "IDLE",
                                     "to": "GO", "dist_m": 2.2}]}],
        "log_events": {"boot": {"count": 1, "why": "기동 배너"},
                       "never": {"count": 0, "why": "안 난 것은 표에서 뺀다"}},
    }
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "run"
        d.mkdir()
        (d / "summary.json").write_text(J.dumps({"summary": summary, "checks": []}))
        md = F.render(d)
    #  ★판정 없음을 «전부 통과» 로 말하지 않는가★
    eq("판정 없음을 명시", "판정(`checks:`)이 아직 없다" in md, True)
    eq("«전부 통과» 라고 하지 않는다", "전부 통과" in md, False)
    #  ★관측값이 실제로 실려 있는가★
    eq("숫자 신호 표", "`dist_m`" in md and "9.9" in md, True)
    eq("문자열 신호 분포", "`IDLE` 90회(90%)" in md, True)
    eq("전이표", "IDLE → GO" in md, True)
    eq("전이 순간의 다른 신호", "2.2" in md, True)
    eq("발생한 로그만", "`boot`" in md and "`never`" not in md, True)
    #  ★요청이 «탐색용» 으로 바뀌는가★ (실패를 없애라고 할 대상이 없다)
    eq("탐색용 요청문", "판정 없이 돌린 탐색용" in md, True)
    eq("전/후 비교는 결과 비교로 안내", "«결과 비교»" in md, True)
    eq("자동 전/후 절은 없다", "## 7. 개선 전/후" in md, False)

    #  판정이 있으면 종전 문구가 그대로여야 한다 (회귀 방지)
    with tempfile.TemporaryDirectory() as td:
        d2 = Path(td) / "run2"
        d2.mkdir()
        (d2 / "summary.json").write_text(J.dumps({
            "summary": summary,
            "checks": [{"check": "drop_rate", "ok": True, "value": 0.0,
                        "bound": {"max": 0.02}, "note": "동기 실패"}]}))
        md2 = F.render(d2)
    eq("판정이 있으면 통과 수를 센다", "**1/1 통과**" in md2, True)
    eq("판정이 있어도 관측값은 나온다", "`dist_m`" in md2, True)


def t_scenario_template():
    """빈 틀로 만든 시나리오에도 판정이 있는가 (없으면 리포트가 늘 초록이다)."""
    import yaml as Y
    from . import config as C
    txt = C.SCEN_TMPL.format(name="x", contract="c.yaml", video="v",
                             mode="lockstep", start=0, limit=0)
    d = Y.safe_load(txt)
    eq("판정이 비어 있지 않다", len(d.get("checks") or []) >= 2, True)
    #  이 둘은 ★요약 지표★ 라 신호 이름을 안 쓴다 — 어느 계약에 붙여도 유효하다
    eq("신호 이름을 안 쓴다",
       all("signal" not in c and "where" not in c for c in d["checks"]), True)


def t_publish_names():
    """정적 사이트의 파일 이름 — app.js 의 apiURL() 과 한 글자도 어긋나면 안 된다.

    어긋나면 사이트가 「오류: HTTP 404」 로만 열린다. 화면이 왜 비었는지
    브라우저에서는 알 수 없으므로(서버 로그도 없다) 여기서 잡는다.
    """
    from .publish import TEXTY, api_path

    eq("목록", api_path("/api/runs"), "api/runs.json")
    eq("메타", api_path("/api/meta"), "api/meta.json")
    eq("기준", api_path("/api/baselines"), "api/baselines.json")
    eq("런 하나", api_path("/api/runs/0825_x"), "api/runs/0825_x.json")
    eq("신호", api_path("/api/runs/0825_x/signals"), "api/runs/0825_x/signals.json")
    eq("리포트", api_path("/api/runs/0825_x/report"), "api/runs/0825_x/report.txt")
    eq("비교", api_path("/api/runs/0825_x/compare"), "api/runs/0825_x/compare.txt")
    eq("피드백", api_path("/api/runs/0825_x/feedback"), "api/runs/0825_x/feedback.txt")
    #  쿼리는 경로 한 칸이 된다 — 정적 호스팅은 쿼리스트링으로 파일을 못 고른다
    eq("로그", api_path("/api/runs/0825_x/log?name=perception"),
       "api/runs/0825_x/log/perception.txt")

    #  ★JS 쪽과 대조★ — 텍스트/JSON 구분을 한쪽만 바꾸면 그 탭만 조용히 빈다.
    js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text()
    m = re.search(r"var TEXTY = \{([^}]*)\}", js)
    eq("app.js 에 TEXTY 가 있다", bool(m), True)
    if m:
        eq("TEXTY 가 양쪽 같다", set(re.findall(r"(\w+):", m.group(1))), TEXTY)


def t_web_auth():
    """인증 게이트 — 터널로 노출하면 이게 유일한 방어선이라 반드시 맞아야 한다."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))
    from server import check_basic_auth as chk           # noqa: PLC0415
    import base64 as _b64

    def hdr(user, pw):
        return "Basic " + _b64.b64encode(f"{user}:{pw}".encode()).decode()

    eq("토큰 없으면 통과(로컬)", chk(hdr("x", "y"), ""), True)
    eq("토큰 없으면 헤더 없어도 통과", chk(None, ""), True)
    eq("맞는 토큰 통과", chk(hdr("아무나", "s3cret"), "s3cret"), True)
    eq("틀린 토큰 거부", chk(hdr("x", "nope"), "s3cret"), False)
    eq("헤더 없으면 거부", chk(None, "s3cret"), False)
    eq("Basic 아니면 거부", chk("Bearer s3cret", "s3cret"), False)
    eq("깨진 base64 거부", chk("Basic @@@", "s3cret"), False)
    eq("콜론 없는 값 거부", chk("Basic " + _b64.b64encode(b"nocolon").decode(),
                              "s3cret"), False)


def t_graft_across_contracts():
    """계약을 넘어 판정을 옮길 때 — ★없는 이름은 안 따라와야 한다★.

    따라오면 리포트에 ⚠️「값 없음」 으로 남는다. 실패가 아니라서 초록으로 보이고,
    그 판정은 조용히 사라진 것이다. 그래서 여기서 세 가지를 못 박는다:
    ① preview 가 성립/불성립을 갈라 주는가 ② graft 결과에 lint 가 깨끗한가
    ③ 성립하는 판정이 하나도 없으면 ★거부하는가★(판정 0개면 리포트가 늘 초록이다).
    """
    import yaml as Y
    from . import config as C
    from . import lint as L
    from .contract import load as load_contract

    #  이 저장소의 (시나리오 × 남의 계약) 조합을 훑어 두 경우를 다 찾는다 —
    #  하나라도 성립하는 조합과, 하나도 성립하지 않는 조합.
    pairs = []
    for f in sorted((C.ROOT / "scenarios").glob("*.yaml")):
        doc = Y.safe_load(f.read_text()) or {}
        if not doc.get("checks"):
            continue
        own = Path(str(doc.get("contract") or "")).name
        for cf in sorted((C.ROOT / "contracts").glob("*.yaml")):
            if cf.name == own:
                continue
            pv = C.preview_checks([f.name], cf.name)
            eq(f"{f.name}→{cf.name}: 판정을 다 편다", pv["total"], len(doc["checks"]))
            pairs.append((f.name, cf.name, pv))
    if not pairs:
        return

    #  ★요약 지표 판정은 신호 이름을 안 쓴다★ — 어느 계약에서도 성립해야 한다.
    for f, cf, pv in pairs:
        doc = Y.safe_load((C.ROOT / "scenarios" / f).read_text()) or {}
        for it in pv["items"]:
            c0 = doc["checks"][it["i"]] if it["i"] < len(doc["checks"]) else {}
            if isinstance(c0, dict) and str(c0.get("stat", "")) == "drop_rate":
                eq(f"{f}→{cf}: drop_rate 는 계약을 넘어도 성립", it["problems"], [])

    out = C.ROOT / "scenarios" / "_selftest_graft.yaml"

    good = next(((f, cf, pv) for f, cf, pv in pairs if pv["ok_count"]), None)
    if good:
        f, cf, pv = good
        if out.exists():
            out.unlink()
        try:
            r = C.graft_scenario([f], "_selftest_graft", cf, "v")
            eq("성립하는 것만 남긴다", r["kept"], pv["ok_count"])
            got = Y.safe_load(out.read_text())
            eq("고른 계약을 쓴다", got.get("contract"), f"contracts/{cf}")
            eq("옮긴 결과에 이름 오류가 없다",
               L.lint_scenario(load_contract(C.ROOT / "contracts" / cf), got), [])
        finally:
            if out.exists():
                out.unlink()

    bad = next(((f, cf) for f, cf, pv in pairs if not pv["ok_count"]), None)
    if bad:
        try:
            C.graft_scenario([bad[0]], "_selftest_graft", bad[1], "v")
            eq("성립하는 판정이 없으면 거부한다", "만들어졌다", "거부")
        except ValueError:
            pass
        finally:
            if out.exists():
                out.unlink()


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("t_")]
    for t in tests:
        try:
            t()
        except Exception as e:      # noqa: BLE001
            FAILS.append(f"{t.__name__} 예외: {e!r}")
    if FAILS:
        print(f"❌ 실패 {len(FAILS)}건")
        for f in FAILS:
            print("  -", f)
        return 1
    print(f"✅ 자체 검사 {len(tests)}개 항목 전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
