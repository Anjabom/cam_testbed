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

    보정 스튜디오의 «저장» 과 최근 목록이 이 경로를 탄다. yaml.safe_dump 로
    왕복시키면 "왜 이렇게 뒀는지"가 통째로 날아가는데, 그건 되돌리기도 어렵고
    알아채기도 어렵다. 실제 파일 대신 임시 복사본에서 검사한다.
    """
    import shutil
    import tempfile
    from . import config as C

    real = C.ROOT
    tmp = Path(tempfile.mkdtemp(prefix="tbcfg_"))
    try:
        (tmp / "contracts").mkdir()
        (tmp / "calib").mkdir()
        src = real / "local.yaml"
        if not src.exists():
            src = real / "local.yaml.example"
        shutil.copy(src, tmp / "local.yaml")
        before = (tmp / "local.yaml").read_text()
        why = [ln for ln in before.splitlines() if ln.strip().startswith("#")]

        C.ROOT = tmp
        try:
            #  ── 최근 목록 : 없는 파일은 거절한다
            try:
                C.push_recent(str(tmp / "missing.mp4"))
                eq("없는 파일을 거절한다", False, True)
            except ValueError:
                eq("없는 파일을 거절한다", True, True)

            probe = tmp / "probe.png"
            probe.write_bytes(b"x")
            C.push_recent(str(probe))
            C.push_recent(str(probe))            # 두 번 넣어도 하나다
            got = C._local().get("recent") or []
            eq("최근 목록의 맨 앞에 온다", got[0], str(probe))
            eq("두 번 넣어도 한 줄이다",
               sum(1 for x in got if x == str(probe)), 1)

            #  ★기계가 관리하는 절은 몇 번을 써도 한 벌이어야 한다★
            #  실측한 실패: 표식 주석을 안 걷어내서 저장할 때마다 안내 줄이
            #  하나씩 쌓였다(블록만 지우고 그 위 주석은 남았다).
            for _ in range(3):
                C.push_recent(str(probe))
            text = (tmp / "local.yaml").read_text()
            eq("표식 주석이 쌓이지 않는다", text.count(C.RECENT_NOTE), 1)
            eq("recent 절도 하나뿐이다",
               sum(1 for ln in text.splitlines() if ln.startswith("recent:")), 1)

            #  ── 파라미터 쓰기 : 값이 들어가고 주석은 살아 있다
            C.set_params({"zz_node": {"zz_param": 1.25}}, "local")
            text = (tmp / "local.yaml").read_text()
            import yaml as _y
            back = ((_y.safe_load(text) or {}).get("params") or {}).get("zz_node") or {}
            eq("파라미터가 들어갔다", back.get("zz_param"), 1.25)
            still = [ln for ln in text.splitlines() if ln.strip().startswith("#")]
            eq("★원래 주석이 하나도 안 사라졌다★",
               [ln for ln in why if ln not in still], [])

            #  ── 시나리오는 더 이상 없다 — 다른 곳에 쓰려 하면 거절한다
            try:
                C.set_params({"zz_node": {"zz_param": 2}}, "scenarios/x.yaml")
                eq("local.yaml 밖에는 안 쓴다", False, True)
            except ValueError:
                eq("local.yaml 밖에는 안 쓴다", True, True)
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


def t_events():
    """단계 전이 — 「언제 물었나」. 리포트의 «단계 전이» 표가 이걸로 만들어진다."""
    rows = _rows_stage()
    tr = list(A.transitions(rows, "b"))
    eq("전이 두 번 (0→1, 1→2)", len(tr), 2)
    i, prev, cur = tr[0]
    eq("첫 전이 프레임", rows[i]["frame"], 110)
    eq("첫 전이 이전→이후", (prev, cur), (0, 1))
    eq("전이 순간의 다른 값", rows[i]["d"], 70.0)
    #  ★값이 없는 행은 전이가 아니다★ 결측을 상태 변화로 세면 없는 사건이 생긴다
    holey = [{"frame": 0, "b": 0}, {"frame": 1}, {"frame": 2, "b": 0}]
    eq("결측은 전이가 아니다", len(list(A.transitions(holey, "b"))), 0)
    # 문자열 상태도 같은 도구로
    cats = [{"frame": i, "s": ("UNKNOWN" if i < 3 else "RED")} for i in range(6)]
    eq("문자열 전이", len(list(A.transitions(cats, "s"))), 1)


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
    tr = list(A.transitions(rows, "b"))
    eq("0->2 전이가 남는다", [(p, cur) for _i, p, cur in tr], [(0, 2)])


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
    #  ★바뀐 키까지 짚는다★ — dict 를 통째로 찍으면 두 벌이 한 줄로 나와서
    #  무엇이 달라졌는지 사람이 못 읽는다(경고가 있으나 마나가 된다).
    eq("범퍼행이 바뀌면 비교 불가",
       _provenance_diff({"calib": snap}, {"calib": now}),
       [("calib.n.bp", 480.0, 645.0)])
    eq("같으면 조용하다", _provenance_diff({"calib": snap}, {"calib": snap}), [])
    #  옛 런에는 calib 이 없다(None) — None 끼리는 같으므로 종전 기준이 그대로 산다
    eq("옛 런끼리는 종전대로", _provenance_diff({}, {}), [])


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


def t_same_code():
    """재현(replay)의 안전장치 — 「그때 코드와 같은가」.

    여기서 거짓 양성이 나면 ★그때 그림이 아닌 영상★ 을 그때 것이라고 믿게 된다.
    모르면 모른다(None)고 답해야 하고, 사본이 있으면 내용으로 견줘야 한다.
    """
    import json as _json
    import tempfile
    from .run import _code_hashes, _fingerprint_legacy, _same_code

    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "ws"
        (ws / "src/pkg").mkdir(parents=True)
        (ws / "src/pkg/a.py").write_text("v = 1\n")
        rd = Path(td) / "run"
        rd.mkdir()

        _, sha = _code_hashes(ws)
        eq("지문 없는 옛 런은 모른다", _same_code(rd, {}, ws), None)
        (rd / "code.json").write_text(_json.dumps({"sha": sha}))
        eq("사본이 있고 같으면 True", _same_code(rd, {}, ws), True)
        (ws / "src/pkg/a.py").write_text("v = 2\n")     # 내용만 바꾼다
        eq("내용이 바뀌면 False", _same_code(rd, {}, ws), False)

        #  옛 런은 옛 방식(mtime)으로만 견줄 수 있다
        (rd / "code.json").unlink()
        legacy = _fingerprint_legacy(ws)
        eq("옛 지문이 같으면 True",
           _same_code(rd, {"code_fingerprint": {"sha": legacy}}, ws), True)
        eq("옛 지문이 다르면 False",
           _same_code(rd, {"code_fingerprint": {"sha": "0" * 12}}, ws), False)
        eq("no-src 는 모른다",
           _same_code(rd, {"code_fingerprint": {"sha": "no-src"}}, ws), None)


def t_export():
    import shutil
    import tempfile
    from . import export as E

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        run = td / "runs" / "0101_000000_x"
        run.mkdir(parents=True)
        ws = td / "ws"
        (ws / ".git").mkdir(parents=True)
        (ws / ".gitignore").write_text("build/\n")
        (run / "summary.json").write_text(json.dumps({
            "summary": {"rows": 12, "valid_rate": 0.5,
                        "meta": {"run_id": run.name, "label": "야간 A",
                                 "note": "정지선에서 서는지", "mode": "lockstep",
                                 "video": "/v/night_a.mp4", "start": 10, "limit": 60,
                                 "params": {"perception": {"w": "/a/best.engine",
                                                           "device": "cuda"}}}}}))
        (run / "code.json").write_text(json.dumps({"workspace": str(ws),
                                                   "sha": "abc123def456"}))
        (run / "report.md").write_text("# r\n")
        (run / "raw.jsonl").write_text("{}\n")
        (run / "lane_debug.mp4").write_bytes(b"\x00")

        r = E.export(run)
        d = r["dest"]
        eq("워크스페이스 안에 들어간다", d, ws / E.OUT_DIRNAME / run.name)
        eq("디버그 영상이 따라간다", (d / "lane_debug.mp4").is_file(), True)
        #  ★raw.jsonl 은 안 보낸다★ 원본은 테스트베드에 남고(재분석의 근거),
        #  워크스페이스에는 읽을 것만 간다. 이게 새면 결과 폴더가 수백 MB 가 된다.
        eq("원본 기록은 안 따라간다", (d / "raw.jsonl").exists(), False)
        eq("colcon 이 결과를 훑지 않는다",
           (ws / E.OUT_DIRNAME / "COLCON_IGNORE").is_file(), True)
        eq("폴더가 스스로를 설명한다",
           "보려던 것" in (d / "README.md").read_text(), True)
        env = json.loads((d / "run_env.json").read_text())
        eq("가중치가 기록된다", env["weights"], {"perception.w": "/a/best.engine"})
        eq("코드 지문이 기록된다", env["workspace_code_sha"], "abc123def456")
        eq("판정이 아니라 잰 양을 남긴다", r["verdict"],
           {"rows": 12, "valid_rate": 0.5, "empty": False})

        #  같은 런을 다시 내보내는 것이 정상 흐름이다 — 이력이 중복되면 안 된다
        r2 = E.export(run)
        eq(".gitignore 가 두 번 늘지 않는다", r2["gitignored"], False)
        rows = [ln for ln in (ws / E.OUT_DIRNAME / "INDEX.md").read_text().splitlines()
                if ln.startswith("| [")]
        eq("이력은 런당 한 줄", len(rows), 1)
        eq("이력에 의도가 적힌다", "정지선에서 서는지" in rows[0], True)

        #  다른 런은 따로 쌓인다
        run2 = run.parent / "0101_000001_y"
        shutil.copytree(run, run2)
        E.export(run2)
        rows = [ln for ln in (ws / E.OUT_DIRNAME / "INDEX.md").read_text().splitlines()
                if ln.startswith("| [")]
        eq("런이 늘면 줄도 는다", len(rows), 2)

        #  ★행 0 은 조용히 넘어가면 안 된다★ 잰 것이 없는데 표는 멀쩡해 보인다
        (run / "summary.json").write_text(json.dumps(
            {"summary": {"rows": 0, "meta": {}}}))
        got = E.export(run, str(ws))
        eq("빈 런을 잡아낸다", got["verdict"]["empty"], True)
        eq("빈 런이라고 폴더에 적는다",
           "잰 것이 없다" in (got["dest"] / "README.md").read_text(), True)

        #  계측이 안 끝난 런은 내보내지 않는다
        (run / "summary.json").unlink()
        try:
            E.export(run, str(ws))
            FAILS.append("summary.json 없는 런을 내보냈다")
        except SystemExit:
            pass


# ══════════════════════════════════════════════════════════════════════
#  보정 스튜디오 — 프로필 경로와 자동 미세조정의 방어선
# ══════════════════════════════════════════════════════════════════════
def t_portable():
    """★코드에 이 기계의 경로가 없어야 한다★ — 다른 컴퓨터에서 그대로 돌아야 한다.

    절대 경로가 박혀도 되는 곳은 셋뿐이다: 계약의 `workspace:`, `local.yaml`(git 제외),
    프리셋의 `video:`. 그 밖에 사용자 홈 경로가 소스로 새어 들어가면 남의 기계에서
    ★조용히★ 엉뚱한 곳을 보거나 아무것도 못 찾는다.
    """
    root = Path(__file__).resolve().parent.parent
    #  찾는 문자열을 조각으로 만든다 — 통째로 적으면 ★이 파일 자신이 걸린다★
    needles = ("/" + "home" + "/", "/" + "Users" + "/")
    bad = []
    for d, pats in (("tb", ("*.py",)), ("tools", ("*.py", "*.js")),
                    ("web", ("*.js", "*.css", "*.html"))):
        for pat in pats:
            for f in sorted((root / d).glob(pat)):
                for i, ln in enumerate(f.read_text().splitlines(), 1):
                    if any(n in ln for n in needles):
                        bad.append(f"{d}/{f.name}:{i}")
    eq("소스에 사용자 홈 경로가 없다", bad, [])

    #  ★runs/ 는 git 에 없다★ 갓 클론한 기계에는 폴더가 아예 없는데, 예전에는
    #  `tb.run list` 가 그 자리에서 FileNotFoundError 로 죽었다.
    import shutil
    import tempfile
    from . import run as R
    real = R.ROOT
    tmp = Path(tempfile.mkdtemp(prefix="tbruns_"))
    try:
        R.ROOT = tmp
        eq("runs/ 가 없어도 만들어 준다", R.runs_dir().is_dir(), True)
        eq("하위 폴더도", R.runs_dir("_params").is_dir(), True)
    finally:
        R.ROOT = real
        shutil.rmtree(tmp, ignore_errors=True)


def t_geom_js():
    """★화면의 기하(web/geom.js)가 cv2 와 같은 값을 내는가★

    스튜디오가 정적 페이지가 되면서 기하가 브라우저에 한 벌 더 생겼다. 예전에는
    「한 벌만 둔다」가 방어선이었지만 서버가 없어져 그 방어선이 없다 — 대신
    ★두 벌이 같음을 여기서 증명한다★. 여기가 무너지면 화면에서 맞춘 값이
    실차에서 틀리는데, 화면은 멀쩡해 보인다.

    허용치 0.1px 은 대조 방법의 잡음 바닥(0.03px — cv2 가 좌표를 1/32 픽셀
    고정소수점으로 반올림한다)보다 넉넉하게 잡은 것이다. 순서나 반픽셀 규약이
    틀리면 0.5px 이상으로 벌어지므로 그건 이 그물에 걸린다.
    """
    import json as _json
    import shutil as _shutil
    import subprocess as _sp

    root = Path(__file__).resolve().parent.parent
    ref_js = root / "web" / "reference.js"
    if not ref_js.exists():
        FAILS.append("t_geom_js: web/reference.js 가 없다 — "
                     "python3 tools/bake_reference.py 로 구울 것")
        return
    ref = _json.loads(ref_js.read_text().split("=", 1)[1].rsplit(";", 1)[0])

    #  ① 정답표가 낡지 않았는가 — tb/geometry.py 를 고치고 다시 굽지 않으면
    #     JS 는 낡은 표와 사이좋게 맞으면서 정작 노드와는 갈라진다.
    import cv2                                          # noqa: PLC0415
    from .geometry import Undistorter                   # noqa: PLC0415
    for c in ref["cases"]:
        u = Undistorter(c["size"], c["K"], c["D"], c["alpha"])
        now = [float(u.new_K[0, 0]), float(u.new_K[1, 1]),
               float(u.new_K[0, 2]), float(u.new_K[1, 2])]
        gap = max(abs(a - b) for a, b in zip(now, c["newK"]))
        eq(f"정답표가 현재 tb.geometry 와 맞는다({c['name']})", gap < 1e-6, True)
        eq(f"정답표의 ROI 도({c['name']})", [int(v) for v in u.roi], c["roi"])
    del cv2

    #  ② JS 를 실제로 돌려 대조한다. node 가 없는 기계도 있다 — 그때는 건너뛴다
    #     (검사를 못 한 것이지 통과한 것이 아니므로 화면에 남긴다).
    node = _shutil.which("node")
    if not node:
        print("  · t_geom_js: node 가 없어 JS 대조를 건너뛴다")
        return
    p = _sp.run([node, str(root / "tools" / "geom_check.js"), "--json"],
                capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        FAILS.append(f"t_geom_js: geom_check.js 실패 — {p.stderr.strip()[:300]}")
        return
    got = _json.loads(p.stdout)
    for c in got["cases"]:
        eq(f"유효영역 ROI 가 정확히 같다({c['name']})", c["roi"], 0)
        eq(f"새 카메라 행렬({c['name']}) {c['newK']:.2e}", c["newK"] < 1e-3, True)
        eq(f"보정 맵({c['name']}) {c['map']:.2e}px", c["map"] < 0.01, True)
        eq(f"호모그래피({c['name']}) {c['H']:.2e}", c["H"] < 1e-6, True)
        eq(f"끝에서 끝까지({c['name']}) {c['bevToSrc']:.3f}px",
           c["bevToSrc"] < 0.1, True)
    #  ★사각형 건전성도 같은 답이어야 한다★ 한쪽만 거절하면 스튜디오가 통과시킨
    #  값을 노드가 검은 띠로 받는다(실측: JS 에만 「화면 밖 거절」이 있어서
    #  제대로 맞춘 사각형을 틀렸다고 말할 뻔했다).
    eq("사각형 건전성이 파이썬과 같다", got.get("quadMismatch"), [])


def t_studio_paths():
    """★스튜디오 서버가 뿌리 밖 파일을 열어 주지 않는가★

    이 서버는 파일을 읽어 그림으로 넘긴다 — 경로를 요청이 정하므로, 막지 못하면
    브라우저 주소창만으로 이 기계의 아무 파일이나 읽힌다. 예전 웹앱에서 배운 것은
    ★훑는 길과 여는 길이 같은 규칙 하나를 지나야 한다★ 는 것이다(훑기만 막으면
    경로를 아는 사람에게는 아무 방어도 아니다).
    """
    from . import studio                                # noqa: PLC0415

    real = studio.roots()
    root = Path(tempfile.mkdtemp(prefix="tbroot_"))
    (root / "안").mkdir()
    inside = root / "안" / "a.mp4"
    inside.write_bytes(b"x")
    outside = Path(tempfile.mkdtemp(prefix="tbout_")) / "b.mp4"
    outside.write_bytes(b"x")
    try:
        studio.set_roots([root])
        eq("뿌리 안은 통과", studio.in_roots(inside), True)
        eq("뿌리 밖은 거절", studio.in_roots(outside), False)
        for bad in (outside, "/etc/passwd", root / ".." / "etc" / "passwd",
                    str(root) + "/../" + outside.name):
            try:
                studio.check_allowed(bad)
                FAILS.append(f"t_studio_paths: 뿌리 밖을 열어 줬다 — {bad}")
            except ValueError:
                pass
        #  훑기도 같은 규칙을 지난다 — 밖을 가리키면 조용히 첫 뿌리로 되돌린다
        eq("밖을 훑으면 뿌리로", studio.browse(str(outside.parent))["dir"], str(root))
        #  뿌리에서 「위로」가 탈출구가 되면 안 된다
        eq("뿌리 위로는 못 간다", studio.browse(str(root))["up"], str(root))
        eq("화면 파일만 내보낸다", "local.yaml" in studio.STATIC, False)
    finally:
        studio.set_roots(real)
        import shutil as _sh
        _sh.rmtree(root, ignore_errors=True)
        _sh.rmtree(outside.parent, ignore_errors=True)


def t_studio_standalone():
    """★스튜디오는 `tb` 를 import 하지 않는다★ — 그래야 떼어 줄 수 있다.

    `tools/pack_studio.py` 가 `tb/studio.py` 와 `web/` 만 묶어 남의 기계로 보낸다.
    거기에는 이 저장소도 ROS 도 없다. 누군가 편의를 위해 `from .config import …`
    한 줄을 넣는 순간 그 꾸러미는 ImportError 로 죽는데, ★이 기계에서는 멀쩡히
    돌기 때문에 아무도 모른다★. 그래서 기계가 대신 본다.
    """
    import ast                                          # noqa: PLC0415
    root = Path(__file__).resolve().parent.parent
    src = (root / "tb" / "studio.py").read_text()
    bad = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.ImportFrom) and (n.level or (n.module or "").startswith("tb")):
            bad.append(f"line {n.lineno}: from {'.' * (n.level or 0)}{n.module or ''}")
        elif isinstance(n, ast.Import):
            for al in n.names:
                if al.name == "tb" or al.name.startswith("tb."):
                    bad.append(f"line {n.lineno}: import {al.name}")
    eq("studio.py 가 tb 를 import 하지 않는다", bad, [])

    #  꾸러미가 실어 나르는 화면 파일이 전부 있는가 — 하나만 빠져도 흰 화면이다
    import importlib.util                               # noqa: PLC0415
    spec = importlib.util.spec_from_file_location("pack", root / "tools" / "pack_studio.py")
    pack = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pack)
    missing = [f for f in pack.WEB_FILES if not (root / "web" / f).is_file()]
    eq("꾸러미가 실을 화면 파일이 다 있다", missing, [])
    #  서버가 내보내는 목록과 꾸러미가 싣는 목록이 같아야 한다 —
    #  한쪽에만 있는 파일은 「이 기계에서만 되는 화면」이 된다
    from . import studio                                # noqa: PLC0415
    eq("서버 목록과 꾸러미 목록이 같다",
       sorted(studio.STATIC), sorted(pack.WEB_FILES))


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
