"""계약·시나리오가 서로 가리키는 ★이름★ 이 실제로 있는지 본다.

★왜 필요한가★ 체크의 신호 이름을 하나 잘못 적으면 `_stat_value` 가 None 을 내고
`run_checks` 는 그것을 ok:None — "값 없음(신호 결측)" ⚠️ 로 남긴다. ★실패가 아니다.★
그래서 판정 하나가 조용히 사라진 채 리포트가 초록으로 나온다. 게다가 그걸 아는 시점이
YOLO 로딩 + lockstep 런을 ★전부 돌린 뒤★ 다. 이 파일은 그 질문을 실행 전에 묻는다.

★판정을 바꾸지 않는다★ 여기서 하는 일은 경고를 만드는 것뿐이다. 런 결과의 ⚠️ 는
그대로 둔다 — '신호가 안 왔다'와 '이름이 틀렸다'는 리포트에서 같은 칸을 쓰지만,
후자는 여기서 미리 잡히므로 런까지 갈 일이 없다.

이름 대조만 하므로 ★워크스페이스 고유명이 이 파일에 들어오지 않는다★. 무엇이
정의됐는지는 전부 계약이 말해 준다.

    from tb.lint import lint
    problems = lint(contract, scenario_dict)      # [] 면 깨끗하다
"""
from __future__ import annotations

from . import expr

# 신호가 아니어도 행에 항상 있는 열 (analyze._rows 가 붙인다)
BUILTIN_COLS = ("frame", "latency_ms")

# `{signal: x, stat: …}` 로 쓸 수 있는 것 — analyze._stat_value 의 마지막 절
SIGNAL_STATS = {"mean", "std", "min", "max", "p95", "frac_nonzero", "frac_zero",
                "p95_abs_diff", "max_abs_diff", "increases", "decreases"}
# `{where: …, stat: …}` (signal 없음) — analyze.span_stats 가 내는 키
SPAN_STATS = {"count", "frac", "runs", "run_max_frames", "run_max_s",
              "first_frame", "last_frame"}
# `{event: …, stat: …}` — at:<신호> 는 따로 본다
EVENT_STATS = {"count", "frame", "t_s"}
# `{stat: …}` 만 있는 것 — analyze.stats() 가 summary 에 넣는 ★스칼라★ 키
SUMMARY_STATS = {"rows", "frame_first", "frame_last", "frames_pushed",
                 "frames_expected", "drop_rate", "latency_p50_ms",
                 "latency_p95_ms", "latency_max_ms", "scene_fps",
                 "valid_rows", "valid_rate"}
# `{stat: "theta:<키>"}` — analyze.theta_quality 가 내는 키
THETA_KEYS = {"n", "straight_frac", "bias_deg", "straight_std_deg",
              "abs_bias_deg", "vibration_frac", "peak_hz"}


def _defined(contract):
    return set(contract.signals) | set(BUILTIN_COLS)


def _near(name, known):
    """오타를 눈에 보이게 — 앞뒤 한 글자 차이나 부분일치면 그걸 같이 보여 준다."""
    lo = str(name).lower()
    hit = [k for k in sorted(known) if lo in k.lower() or k.lower() in lo]
    return f" (혹시 {', '.join(hit[:3])}?)" if hit else ""


def _check_names(where, defined, tag, out):
    """조건식 하나 — 문법·화이트리스트(expr)와 참조 이름을 함께 본다."""
    try:
        expr.compile_expr(where)
    except SyntaxError as e:
        out.append(f"{tag}: 조건식이 잘못됐다 — {e}")
        return
    for n in sorted(expr.names(where)):
        if n not in defined:
            out.append(f"{tag}: 조건식이 없는 신호 `{n}` 를 쓴다{_near(n, defined)}")


def _refs(names, defined, tag, out):
    for n in names:
        if n is not None and n not in defined:
            out.append(f"{tag}: 없는 신호 `{n}`{_near(n, defined)}")


def lint_contract(contract):
    """계약 하나가 자기 안에서 아귀가 맞는가 — signals: 에 없는 이름을 쓰지 않는가."""
    out = []
    defined = _defined(contract)
    raw = contract.raw

    for key in ("compare_signals", "compare_categorical", "compare_sequence",
                "hold_signals", "mirror_odd_signals", "frame_columns"):
        _refs([str(x) for x in (raw.get(key) or [])], defined, key, out)
    _refs([str(k) for k in (raw.get("hold_initial") or {})], defined,
          "hold_initial", out)

    fb = raw.get("flag_bits") or {}
    if fb.get("signal"):
        _refs([str(fb["signal"])], defined, "flag_bits.signal", out)
    tq = raw.get("theta_quality") or {}
    if tq.get("signal"):
        _refs([str(tq["signal"])], defined, "theta_quality.signal", out)

    # events: 는 dict 하나이거나 목록이다 (analyze.event_table 과 같은 규칙)
    evs = raw.get("events")
    for e in ([evs] if isinstance(evs, dict) else (evs or [])):
        _refs([e.get("signal")] + [str(x) for x in (e.get("at") or [])],
              defined, f"events[{e.get('signal')}]", out)

    r = raw.get("render") or {}
    _refs([str(x) for x in (r.get("readout") or [])], defined, "render.readout", out)
    bd = r.get("bev_dist") or {}
    if bd.get("distance"):
        _refs([str(bd["distance"])], defined, "render.bev_dist.distance", out)

    for p in (raw.get("frame_presets") or []):
        if p.get("where"):
            _check_names(str(p["where"]), defined,
                         f"frame_presets[{p.get('label', '')}]", out)

    for con in (raw.get("consumers") or []):
        for stg in (con.get("stages") or []):
            if stg.get("expr"):
                _check_names(str(stg["expr"]), defined,
                             f"consumers.{con.get('id')}[{stg.get('name', '')}]", out)
    return out


def _lint_check(chk, contract, defined, tag, out):
    """체크 한 줄 — 가리키는 이름과 통계 이름이 실제로 있는가."""
    if not isinstance(chk, dict):
        out.append(f"{tag}: 체크가 사전(dict)이 아니다")
        return
    stat = str(chk.get("stat") or "")
    if not stat:
        out.append(f"{tag}: stat: 이 없다 — 무엇을 잴지 정해야 한다")
        return
    sig, where, ev = chk.get("signal"), chk.get("where"), chk.get("event")

    if sig is not None:
        _refs([str(sig)], defined, tag, out)
    if where is not None:
        _check_names(str(where), defined, tag, out)
    if ev is not None:
        # "brake_level:0->1" / "tl_state:*->RED" — 앞부분이 신호 이름이다
        _refs([str(ev).split(":", 1)[0]], defined, tag, out)

    # ── stat 어휘 ─────────────────────────────────────────────────────
    if stat.startswith("at:"):
        if ev is None:
            out.append(f"{tag}: `at:` 는 event: 와 함께 써야 한다")
        _refs([stat[3:]], defined, f"{tag} stat:at", out)
        return
    if stat.startswith("log:"):
        known = set(contract.raw.get("log_events") or {})
        if stat[4:] not in known:
            out.append(f"{tag}: 계약의 log_events 에 없는 `{stat[4:]}`"
                       f"{_near(stat[4:], known)}")
        return
    if stat.startswith("contribution:"):
        known = {str(c.get("id")) for c in (contract.raw.get("consumers") or [])}
        if stat[13:] not in known:
            out.append(f"{tag}: 계약의 consumers 에 없는 `{stat[13:]}`"
                       f"{_near(stat[13:], known)}")
        return
    if stat.startswith("theta:"):
        if not (contract.raw.get("theta_quality") or {}):
            out.append(f"{tag}: 계약에 theta_quality: 가 없어 `{stat}` 는 늘 값이 없다")
        elif stat[6:] not in THETA_KEYS:
            out.append(f"{tag}: θ 품질 키가 아니다 `{stat[6:]}`{_near(stat[6:], THETA_KEYS)}")
        return
    if stat.startswith("flag_rate:"):
        fb = contract.raw.get("flag_bits") or {}
        known = {str(v) for v in (fb.get("bits") or {}).values()} | {"CLEAN"}
        if not fb.get("signal"):
            out.append(f"{tag}: 계약에 flag_bits: 가 없어 `{stat}` 는 늘 값이 없다")
        elif stat[10:] not in known:
            out.append(f"{tag}: 플래그 이름이 아니다 `{stat[10:]}`{_near(stat[10:], known)}")
        return

    if sig is not None:
        vocab, what = SIGNAL_STATS, "신호 통계"
    elif ev is not None:
        vocab, what = EVENT_STATS, "전이 통계"
    elif where is not None:
        vocab, what = SPAN_STATS, "구간 통계"
    else:
        vocab, what = SUMMARY_STATS, "요약 지표"
    if stat not in vocab:
        out.append(f"{tag}: {what}에 `{stat}` 는 없다{_near(stat, vocab)}")


def lint_scenario(contract, scenario):
    """시나리오의 checks: 가 이 계약으로 실제로 판정될 수 있는가."""
    out = []
    defined = _defined(contract)
    for i, chk in enumerate(scenario.get("checks") or []):
        _lint_check(chk, contract, defined, f"checks[{i}]", out)
    for v in (scenario.get("variants") or []):
        vn = v.get("name", "?")
        for i, chk in enumerate(v.get("checks") or []):
            _lint_check(chk, contract, defined, f"variants[{vn}].checks[{i}]", out)
    # compare_tol 의 오타는 ★허용오차가 조용히 _default 로 떨어진다★ — 회귀 비교가
    # 통과해 버리는 쪽으로 틀리므로 특히 조용하다.
    _refs([str(k) for k in (scenario.get("compare_tol") or {}) if k != "_default"],
          defined, "compare_tol", out)
    return out


def lint(contract, scenario=None):
    """계약(+ 있으면 시나리오)의 이름 참조를 전부 대조한다 — [] 면 깨끗하다."""
    out = lint_contract(contract)
    if scenario:
        out += lint_scenario(contract, scenario)
    return out
