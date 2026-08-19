"""테스트베드 자체 검사 — ROS 도 영상도 필요 없다.

경로식 평가·스키마 드리프트 판정·상태유지·시퀀스 비교처럼
"테스트베드가 틀리면 모든 판정이 틀리는" 부분만 검사한다.

    python3 -m tb.selftest
"""
from __future__ import annotations

import json
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
