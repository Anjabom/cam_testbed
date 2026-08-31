"""테스트 결과 → 코드 개선 요청문.

★왜 따로 만드는가★
report.md 는 사람이 읽는 문서다. 표가 많고 순서가 "무엇을 쟀나" 순이라,
그대로 코딩 에이전트에게 주면 무엇부터 고쳐야 하는지가 안 보인다.
여기서 만드는 feedback.md 는 순서가 ★무엇을 고쳐야 하나★ 순이다.

  0. 결론  1. 실행 조건  2. 잘된 점  3. 안 좋은 점(심각도순)
  4. 병목과 볼 곳  5. 참고 수치  6. 개선 전/후  7. 사람 메모  8. 요청

★판정은 하지 않는다★
값·기준·통과 여부는 전부 summary.json 에서 그대로 옮긴다. 여기서 임계값을
한 벌 더 쓰면 엔진과 어긋난다. 정렬에 쓰는 '초과율'만 계산한다.

    python3 -m tb.run feedback <실행> [--vs <이전 실행>] [--note "..."]
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════════
#  읽기·계산 도우미
# ══════════════════════════════════════════════════════════════════════
def load(run_dir):
    p = Path(run_dir) / "summary.json"
    if not p.is_file():
        raise SystemExit(f"[feedback] summary.json 이 없다: {p}")
    return json.loads(p.read_text())


def _f(v, n=4):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{n}f}"
    return str(v)


def _note(c):
    """엔진이 why 를 못 채우면 "> 0.3" 같은 문구가 들어온다 — 설명이 아니다."""
    n = (c.get("note") or "").strip()
    return "" if (not n or n[0] in "<>") else n


def _bound(c):
    b = c.get("bound") or {}
    return " / ".join(f"{k}={v}" for k, v in b.items()) or "—"


def excess(c):
    """기준을 얼마나 벗어났나 — 정렬용 상대값. 판정이 아니다."""
    v, b = c.get("value"), c.get("bound") or {}
    if v is None or not isinstance(v, (int, float)):
        return 0.0
    e = 0.0
    if "min" in b and v < b["min"]:
        e = max(e, (b["min"] - v) / (abs(b["min"]) or 1.0))
    if "max" in b and v > b["max"]:
        e = max(e, (v - b["max"]) / (abs(b["max"]) or 1.0))
    return e


def _verdict(run_dir):
    """compare.md 의 판정 한 줄. 서버가 목록에서 하는 것과 같은 방식."""
    p = Path(run_dir) / "compare.md"
    if not p.is_file():
        return None
    head = p.read_text()[:400]
    for v in ("PASS", "DIFF", "NO_OVERLAP"):
        if f"판정: {v}" in head:
            return v
    return None


def _worst_stage(fn):
    """가장 많이 떨어뜨린 단계. bottleneck 이름이 있으면 그것을 우선한다."""
    stages = fn.get("stages") or []
    if not stages:
        return None
    for s in stages:
        if s.get("name") == fn.get("bottleneck"):
            return s
    return max(stages, key=lambda s: s.get("dropped", 0))


# ── "어디를 볼까" — 계약·게이트가 실제로 들고 있는 정보만 쓴다 ──────
_HINT = [
    (("lane_width_m", "half_width_px"),
     "IPM 4점과 px2m — 웹앱 `카메라 보정` 탭, 계약의 `calibration:` 블록. "
     "폭이 물리값과 어긋나면 그 뒤 통계는 전부 같이 어긋난다"),
    (("theta",),
     "θ 를 만드는 곳 — 좌우 차선 fit → 중심선 기울기. "
     "bias 는 IPM 이 좌우로 기울었을 때도 생긴다"),
    (("cte_rear_m", "cte_near_m"),
     "횡편차 계산과 프레임 간 점프 억제(평활·이상치 제거)"),
    (("conf_raw", "conf"),
     "차선 검출 신뢰도를 매기는 부분 — 검출 실패 프레임이 여기서 걸린다"),
    (("drop_rate", "latency"),
     "프레임당 처리 시간. lockstep 에서 유실이 나면 동기 자체가 깨진 것"),
]


def hint_for(name):
    for keys, txt in _HINT:
        if any(k in name for k in keys):
            return txt
    return ""


# ══════════════════════════════════════════════════════════════════════
#  관측값 — ★판정이 없어도 나오는 절★
# ══════════════════════════════════════════════════════════════════════
#  checks: 는 "이 값이 이래야 한다"는 기준이라 ★정상이 뭔지 알아야★ 쓸 수 있다.
#  새 워크스페이스·새 영상에서는 그걸 아직 모른다 — 그래서 판정이 0개인 채로
#  돌리게 되는데, 그때도 이 문서가 알맹이를 갖도록 하는 것이 이 절이다.
#  전부 summary.json 에 이미 계산돼 있는 값이고, 여기서 판정하지 않는다.
def _signal_rows(sig_stats):
    """신호 통계 → (숫자표 줄들, 문자열표 줄들). 계약의 signals: 순서를 지킨다."""
    num, cat = [], []
    for name, st in (sig_stats or {}).items():
        if not isinstance(st, dict):
            continue
        if st.get("kind") == "cat":
            counts = st.get("counts") or {}
            n = st.get("n") or sum(counts.values()) or 1
            top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
            share = ", ".join(f"`{k}` {v}회({v / n * 100:.0f}%)" for k, v in top[:6])
            cat.append(f"| `{name}` | {st.get('n', 0)} | {share or '—'} "
                       f"| {st.get('transitions', 0)} |")
        else:
            num.append(
                f"| `{name}` | {st.get('n', 0)} | {_f(st.get('mean'))} "
                f"| {_f(st.get('std'))} | {_f(st.get('min'))} | {_f(st.get('max'))} "
                f"| {_f(st.get('p95'))} | {st.get('increases', 0)}↑ "
                f"{st.get('decreases', 0)}↓ |")
    return num, cat


def observations(s, n):
    """`## n. 관측값` 절 — 신호 통계 · 전이 · 노드 로그."""
    L = []
    a = L.append
    a(f"## {n}. 관측값 — 신호가 실제로 어떻게 나왔나")
    a("")
    a("판정(`checks:`)과 무관하게 **이번 실행에서 관측된 값 그대로**다. "
      "기준을 세우지 않았으므로 합격/불합격이 아니라, "
      "**물리적으로·논리적으로 말이 되는 값인지** 읽어 볼 자료다.")
    a("")

    num, cat = _signal_rows(s.get("signals"))
    if num:
        a("### 숫자 신호")
        a("")
        a("| 신호 | 표본 | 평균 | 표준편차 | 최소 | 최대 | p95 | 프레임간 증감 |")
        a("|---|---|---|---|---|---|---|---|")
        L.extend(num)
        a("")
        a("- `최소`·`최대` 가 물리적으로 불가능한 값이면 그 신호를 만드는 곳이 "
          "이미 틀린 것이다(캘리브·단위·예외처리).")
        a("- `표준편차` 가 평균에 비해 크면 프레임마다 값이 튄다는 뜻이고, "
          "`증감` 횟수가 표본 수에 육박하면 매 프레임 흔들린 것이다.")
        a("")
    if cat:
        a("### 상태(문자열) 신호")
        a("")
        a("| 신호 | 표본 | 값 분포 | 전이 횟수 |")
        a("|---|---|---|---|")
        L.extend(cat)
        a("")
        a("- 한 값이 100% 면 그 신호는 이 영상에서 ★한 번도 안 바뀐★ 것이다 — "
          "자극이 없었는지, 아니면 로직이 굳었는지 구분해야 한다.")
        a("")

    for ev in (s.get("events") or []):
        trs = ev.get("transitions") or []
        a(f"### 전이 — {ev.get('label', ev.get('signal'))} (`{ev.get('signal')}`)")
        a("")
        if ev.get("why"):
            a(f"> {ev['why']}")
            a("")
        if not trs:
            a("전이가 한 번도 없었다 — 값이 처음 그대로다.")
            a("")
            continue
        at = [str(x) for x in (ev.get("at") or [])]
        a("| 프레임 | 시각[s] | 바뀜 | " + " | ".join(f"`{x}`" for x in at) + " |")
        a("|---|---|---|" + "---|" * len(at))
        for t in trs[:20]:
            cells = " | ".join(_f(t.get(x)) for x in at)
            a(f"| {t.get('frame')} | {_f(t.get('t_s'), 2)} | "
              f"{t.get('from')} → {t.get('to')} | {cells} |")
        if len(trs) > 20:
            a(f"| … | | 그 외 {len(trs) - 20}건 | " + " | " * len(at) + "|")
        a("")

    le = {k: v for k, v in (s.get("log_events") or {}).items()
          if isinstance(v, dict) and v.get("count")}
    if le:
        a("### 노드 로그 — 토픽에 안 나오는 근거")
        a("")
        a("| 로그 | 횟수 | 무엇을 뜻하나 |")
        a("|---|---|---|")
        for k, v in sorted(le.items(), key=lambda kv: kv[1].get("count", 0), reverse=True):
            a(f"| `{k}` | {v.get('count')} | {(v.get('why') or '').strip() or '—'} |")
        a("")
    return L


# ══════════════════════════════════════════════════════════════════════
#  본문
# ══════════════════════════════════════════════════════════════════════
def render(run_dir, prev_dir=None, note=""):
    run_dir = Path(run_dir)
    sj = load(run_dir)
    s = sj.get("summary", {})
    m = s.get("meta", {})
    checks = sj.get("checks", [])
    bad = sorted([c for c in checks if c.get("ok") is False], key=excess, reverse=True)
    good = [c for c in checks if c.get("ok") is True]
    funnels = s.get("funnel") or []
    verdict = _verdict(run_dir)

    L = []
    a = L.append

    a(f"# 개선 요청 — {run_dir.name}")
    a("")
    a("> 카메라 테스트베드가 실행 결과에서 자동 생성한 문서다. "
      "값·기준·통과 여부는 전부 `summary.json` 에서 그대로 옮겼다.")
    a("")

    # ── 0. 결론 ────────────────────────────────────────────────────
    a("## 0. 결론 먼저")
    a("")
    if not checks:
        #  ★"실패 0건" 이라고 쓰면 안 된다★ 판정이 없는 것과 전부 통과한 것은
        #  전혀 다른데, 문서만 보면 구별이 안 돼 «다 정상» 으로 읽힌다.
        a("- **판정(`checks:`)이 아직 없다** — 합격/불합격을 말할 수 없다. "
          "이 실행이 어떤 값을 냈는지는 아래 관측값 절을 볼 것.")
    else:
        a(f"- 불변식 체크 **{len(good)}/{len(checks)} 통과**"
          + (f" — 실패 {len(bad)}건" if bad else " — 전부 통과"))
    if bad:
        top = ", ".join(f"`{c['check']}`" for c in bad[:3])
        a(f"- 가장 급한 것: {top}")
    for fn in funnels:
        a(f"- 실질 기여율 — **{fn.get('label', fn.get('id'))} "
          f"{(fn.get('rate') or 0) * 100:.1f}%** "
          f"({fn.get('contributing', 0)}/{fn.get('total', 0)} 프레임)"
          + (f" · 병목 **{fn['bottleneck']}**" if fn.get("bottleneck") else ""))
    if verdict:
        a(f"- 회귀 비교: **{verdict}**"
          + (" — 기준과 값이 같다. 이번 변경이 출력을 바꾸지 않았다는 뜻이다."
             if verdict == "PASS" else
             " — 기준과 값이 달라졌다. 의도한 변경인지 확인이 필요하다."))
    a("")

    # ── 1. 실행 조건 ───────────────────────────────────────────────
    a("## 1. 이 결과가 나온 조건")
    a("")
    a("| 항목 | 값 |")
    a("|---|---|")
    fp = m.get("code_fingerprint") or {}
    rows = [
        ("고칠 대상 워크스페이스", f"`{m.get('workspace', '?')}`"),
        ("테스트베드", f"`{ROOT}`"),
        ("계약", f"`{m.get('contract', '?')}` v{m.get('contract_version', '?')}"),
        ("시나리오", f"`scenarios/{m.get('scenario', '?')}.yaml`"),
        ("영상", f"`{m.get('video', '?')}` (구간 {m.get('start', 0)}부터 "
                 f"{m.get('limit', 0) or '끝'}프레임, stride {m.get('stride', 1)})"),
        ("모드", f"`{m.get('mode', '?')}`"
                 + (" — 완전 결정적. 같은 코드면 값이 정확히 같다."
                    if m.get("mode") == "lockstep" else "")),
        ("섭동", f"`{m.get('perturb', 'none')}`"),
        ("처리한 행", f"{s.get('rows', 0)} (투입 {s.get('frames_pushed', '?')})"),
        ("코드 지문", f"파일 {fp.get('n_files', '?')}개 · `{fp.get('sha', '?')}`"),
        ("실행 시각", m.get("when", "?")),
    ]
    for k, v in rows:
        a(f"| {k} | {v} |")
    a("")

    # ── 2. 잘된 점 ─────────────────────────────────────────────────
    a("## 2. 잘된 점 — 건드리면 안 되는 것")
    a("")
    if good:
        for c in good:
            a(f"- ✅ `{c['check']}` = {_f(c['value'])} (기준 {_bound(c)})"
              + (f" — {_note(c)}" if _note(c) else ""))
    elif not checks:
        a("- 판정이 없어 «지켜야 할 선» 이 정해져 있지 않다. "
          "아래 관측값이 이번 실행의 기록이니, 고친 뒤 그 값들이 어떻게 변했는지 볼 것.")
    else:
        a("- 통과한 체크가 없다.")
    if verdict == "PASS":
        a("- ✅ 회귀 비교 PASS — 기준 대비 신호가 허용오차 안이다.")
    for fn in funnels:
        ok_stages = [st for st in (fn.get("stages") or [])
                     if (st.get("pass_rate") or 0) >= 0.9]
        if ok_stages:
            nm = ", ".join(f"{st['name']} {st['pass_rate'] * 100:.0f}%"
                           for st in ok_stages)
            a(f"- ✅ {fn.get('label', fn.get('id'))} — 잘 통과한 단계: {nm}")
    a("")
    if checks:
        a("이 값들을 깨뜨리는 수정은 개선이 아니다. 고친 뒤 다시 확인할 것.")
        a("")

    # ── 3. 안 좋은 점 ──────────────────────────────────────────────
    a("## 3. 안 좋은 점 — 고쳐야 할 것 (심각도 순)")
    a("")
    if not checks:
        a("**판정이 없어 여기에 자동으로 올라오는 항목이 없다.** "
          "아래 병목·관측값을 읽고 이상한 값을 직접 짚어야 한다.")
        a("")
    elif not bad:
        a("실패한 체크가 없다. 아래 병목과 관측값만 보면 된다.")
        a("")
    for i, c in enumerate(bad, 1):
        e = excess(c)
        a(f"{i}. **`{c['check']}` = {_f(c['value'])}** — 기준 {_bound(c)}"
          + (f" (기준을 {e * 100:.0f}% 벗어남)" if e else ""))
        if _note(c):
            a(f"   - 왜 문제인가: {_note(c)}")
        hint = hint_for(c["check"])
        if hint:
            a(f"   - 볼 곳: {hint}")
    a("")

    # ── 4. 병목 ────────────────────────────────────────────────────
    a("## 4. 병목 — 받는 쪽이 실제로 못 쓴 이유")
    a("")
    a("차선을 봤는가가 아니라 **받는 쪽 노드가 그 프레임을 실제로 썼는가**를 잰다.")
    a("인지가 좋아도 게이트를 못 넘으면 주행에는 한 프레임도 기여하지 않는다.")
    a("")
    for fn in funnels:
        a(f"### {fn.get('label', fn.get('id'))} — 실질 기여율 "
          f"{(fn.get('rate') or 0) * 100:.1f}% "
          f"({fn.get('contributing', 0)}/{fn.get('total', 0)})")
        a("")
        if fn.get("source"):
            a(f"관련 코드: `{fn['source']}`")
            a("")
        a("| 단계 | 조건 | 이 단계 통과율 | 탈락 | 관련 상수 |")
        a("|---|---|---|---|---|")
        worst = _worst_stage(fn)
        for st in fn.get("stages") or []:
            mark = " 🔻" if worst and st is worst else ""
            a(f"| {st.get('name')}{mark} | `{st.get('expr', '')}` | "
              f"{(st.get('pass_rate') or 0) * 100:.1f}% | {st.get('dropped', 0)} | "
              f"{st.get('const') or '—'} |")
        a("")
        if worst:
            a(f"- 가장 크게 막힌 단계: **{worst.get('name')}** — "
              f"{worst.get('dropped', 0)}프레임 탈락 "
              f"(들어온 {worst.get('in', 0)}개 중)")
            if worst.get("why"):
                a(f"- 그 단계의 뜻: {worst['why']}")
            if worst.get("const"):
                a(f"- 그 단계가 쓰는 상수: `{worst['const']}` — "
                  "값을 바꾸는 것과 원인을 고치는 것은 다르다. 마지막 절의 규칙을 볼 것.")
        a("")

    # ── 5. 참고 수치 ───────────────────────────────────────────────
    a("## 5. 참고 수치")
    a("")
    fr = s.get("flag_rate") or {}
    if fr:
        hot = sorted(fr.items(), key=lambda kv: kv[1] or 0, reverse=True)
        a("- 플래그 발생률: "
          + ", ".join(f"`{k}` {(v or 0) * 100:.1f}%" for k, v in hot if v))
    tq = s.get("theta_quality") or {}
    if tq:
        a(f"- θ 품질: bias {_f(tq.get('bias_deg'), 3)}° · "
          f"직진구간 {(tq.get('straight_frac') or 0) * 100:.0f}% · "
          f"직진 표준편차 {_f(tq.get('straight_std_deg'), 2)}° · "
          f"진동대역 {_f(tq.get('vibration_frac'), 3)}")
    a(f"- 지연: p50 {_f(s.get('latency_p50_ms'), 1)} ms / "
      f"p95 {_f(s.get('latency_p95_ms'), 1)} ms / "
      f"max {_f(s.get('latency_max_ms'), 1)} ms")
    a(f"- 유효 행 {s.get('valid_rows', 0)}/{s.get('rows', 0)} "
      f"({(s.get('valid_rate') or 0) * 100:.1f}%) · "
      f"유실률 {_f(s.get('drop_rate'), 3)}")
    odd = [d for d in sj.get("drift", [])
           if d.get("status") != "ok" and not d.get("optional")]
    if odd:
        a("- 계약 정합 이상: "
          + ", ".join(f"`{d['signal']}`({d['status']})" for d in odd)
          + " — 메시지 배치가 바뀌었으면 계약의 `path:` 한 줄을 고친다.")
    a("")

    # ── 6. 관측값 ──────────────────────────────────────────────────
    #    ★판정이 없어도 이 절은 항상 나온다★ — 이 문서의 알맹이다.
    L.extend(observations(s, 6))

    # ── 7. 개선 전/후 ──────────────────────────────────────────────
    #    ★자동으로 붙지 않는다★ prev_dir 을 준 때만이다. 전/후 비교는 웹앱의
    #    «결과 비교» 에서 사람이 두 실행을 골라 하는 일이고, 이 문서는 기본적으로
    #    ★이번 실행 하나★ 만 말한다.
    n = 7
    if prev_dir:
        L.extend(_diff_section(prev_dir, run_dir, sj, n))
        n += 1

    # ── 7. 사람 메모 ───────────────────────────────────────────────
    if note:
        a(f"## {n}. 사람이 본 것")
        a("")
        a(note.strip())
        a("")
        n += 1

    # ── 8. 요청 ────────────────────────────────────────────────────
    sc = m.get("scenario", "regression")
    a(f"## {n}. 요청")
    a("")
    if checks:
        a(f"위 3·4절을 없애는 방향으로 `{m.get('workspace', '?')}` 의 코드를 고쳐 줘.")
    else:
        #  ★판정이 없을 때는 요청이 달라진다★ "실패를 없애라"고 할 대상이 없다.
        #  대신 관측값을 읽고 ★이상한 것을 찾아 달라★ 고 부탁하는 것이 맞다.
        a(f"이 실행은 **판정 없이 돌린 탐색용**이다. `{m.get('workspace', '?')}` 의 "
          "알고리즘이 어떤 값을 내는지 보려고 돌렸다.")
        a("")
        a("**6절의 관측값을 읽고 다음을 말해 줘:**")
        a("")
        a("1. 물리적으로·논리적으로 **말이 안 되는 값**이 있는가 "
          "(범위를 벗어난 최소/최대, 100% 한 값으로 고정된 상태, "
          "표본 수에 육박하는 증감 횟수 등). 있으면 그 신호를 만드는 코드의 어디를 볼지.")
        a("2. 4절의 병목에서 **받는 쪽이 못 쓴 프레임**이 많다면 그 원인.")
        a("3. 이 관측값을 근거로 **앞으로 걸어 둘 만한 판정(`checks:`)**을 제안해 줘 — "
          f"`scenarios/{sc}.yaml` 에 넣을 것이다. 각 항목의 `why:` 에는 "
          "**여기 관측된 값을 그대로 인용**해서, 왜 그 문턱인지 나중에 재현되게 할 것.")
    a("")
    a("**규칙**")
    a("")
    a(f"1. 수정 대상은 워크스페이스 코드다. 테스트베드(`{ROOT}`)는 재는 쪽이라 "
      "결과를 좋게 만들려고 건드리지 않는다.")
    a("2. **임계값을 느슨하게 해서 체크를 통과시키지 마라.** 값이 아니라 원인을 고친다. "
      f"측정 기준 자체가 이 영상·차량에 안 맞는다고 판단되면, 그 근거를 먼저 말하고 "
      f"`scenarios/{sc}.yaml` 의 `checks:` 를 고치자고 제안할 것 — 말없이 바꾸지 않는다.")
    if checks:
        a("3. 2절에서 통과한 체크를 깨지 않는다. "
          "하나를 고치다 다른 하나가 깨지면 그것도 보고할 것.")
    else:
        a("3. 관측값을 **추측으로 채우지 마라.** 6절에 없는 숫자를 지어내지 말고, "
          "더 봐야 할 것이 있으면 무엇을 어떻게 재야 하는지 말할 것.")
    a("4. 고칠 곳을 정할 때 4절의 병목 단계부터 본다 — 거기가 실제로 주행에 반영되는 길목이다.")
    a("")
    a("**고친 뒤 검증**")
    a("")
    a("```bash")
    a(f"cd {ROOT}")
    a(f"python3 -m tb.run run --scenario scenarios/{sc}.yaml --tag fix")
    a("```")
    a("")
    a(f"전/후를 숫자로 대조하려면 웹앱 **«결과 비교»** 에서 `{run_dir.name}` 과 "
      "새 실행을 골라 비교한다(신호별 차이를 허용오차와 함께 낸다).")
    a("")
    if m.get("mode") == "lockstep":
        a("`lockstep` 이라 같은 코드면 값이 정확히 같다. 숫자가 바뀌었다면 "
          "그건 전부 코드 변경 때문이다.")
    a("")
    return "\n".join(L)


# ══════════════════════════════════════════════════════════════════════
#  개선 전/후
# ══════════════════════════════════════════════════════════════════════
def _diff_section(prev_dir, cur_dir, cur_sj, n=6):
    prev_dir = Path(prev_dir)
    L = []
    a = L.append
    a(f"## {n}. 개선 전/후")
    a("")
    try:
        pj = load(prev_dir)
    except SystemExit:
        a(f"이전 실행 `{prev_dir.name}` 에 분석 결과가 없어 비교하지 못했다.")
        a("")
        return L

    pm = (pj.get("summary", {}).get("meta") or {})
    cm = (cur_sj.get("summary", {}).get("meta") or {})
    a(f"이전 `{prev_dir.name}` → 지금 `{cur_dir.name}`")
    a("")
    if (pm.get("video_key") != cm.get("video_key")
            or pm.get("start") != cm.get("start")
            or pm.get("limit") != cm.get("limit")
            or pm.get("mode") != cm.get("mode")):
        a("> ⚠️ 두 실행의 영상·구간·모드가 다르다. 숫자는 나오지만 "
          "코드 변경의 효과로 읽으면 안 된다.")
        a("")

    pc = {c["check"]: c for c in pj.get("checks", [])}
    better, worse, same = [], [], []
    for c in cur_sj.get("checks", []):
        p = pc.get(c["check"])
        if not p:
            continue
        pv, cv = p.get("value"), c.get("value")
        if not isinstance(pv, (int, float)) or not isinstance(cv, (int, float)):
            continue
        de, ce = excess(p), excess(c)
        line = (f"`{c['check']}` {_f(pv)} → {_f(cv)} (기준 {_bound(c)})")
        if p.get("ok") is False and c.get("ok") is True:
            better.append("✅ " + line + " — **실패에서 통과로**")
        elif p.get("ok") is True and c.get("ok") is False:
            worse.append("❌ " + line + " — **통과에서 실패로**")
        elif ce < de - 1e-9:
            better.append("↗ " + line + f" — 기준 초과 {de * 100:.0f}% → {ce * 100:.0f}%")
        elif ce > de + 1e-9:
            worse.append("↘ " + line + f" — 기준 초과 {de * 100:.0f}% → {ce * 100:.0f}%")
        elif abs(cv - pv) > 1e-12:
            same.append("· " + line)

    a("**좋아진 것**")
    a("")
    for x in better or ["- 없다."]:
        a(f"- {x}" if not x.startswith("-") else x)
    a("")
    a("**나빠진 것**")
    a("")
    for x in worse or ["- 없다."]:
        a(f"- {x}" if not x.startswith("-") else x)
    a("")

    pf = {f["id"]: f for f in (pj.get("summary", {}).get("funnel") or [])}
    cf = (cur_sj.get("summary", {}).get("funnel") or [])
    if pf and cf:
        a("**실질 기여율**")
        a("")
        a("| 받는 쪽 | 이전 | 지금 | 변화 |")
        a("|---|---|---|---|")
        for fn in cf:
            p = pf.get(fn["id"])
            if not p:
                continue
            pr, cr = (p.get("rate") or 0), (fn.get("rate") or 0)
            d = (cr - pr) * 100
            a(f"| {fn.get('label', fn['id'])} | {pr * 100:.1f}% | {cr * 100:.1f}% | "
              f"{'+' if d >= 0 else ''}{d:.1f}%p |")
        a("")
    if same:
        a(f"그 밖에 값만 움직인 체크 {len(same)}개는 기준 안팎이 그대로다.")
        a("")
    return L


def write(run_dir, prev_dir=None, note=""):
    run_dir = Path(run_dir)
    md = render(run_dir, prev_dir, note)
    out = run_dir / "feedback.md"
    out.write_text(md)
    return out
