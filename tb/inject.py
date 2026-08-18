"""신호 주입 검증 — 변환 수학을 영상·YOLO 없이 격리해서 검사한다.

★왜 필요한가★
영상으로 도는 테스트는 "차선을 봤나"와 "값이 맞나"가 섞여 있어서, 값이 틀려도
인지 탓인지 계산 탓인지 알 수 없다. 여기서는 ★내가 만든 입력★을 직접 넣으므로
참값을 정확히 안다 — 나오는 값이 다르면 100% 계산이 틀린 것이다.

대상 노드를 import 하지 않는다. 계약이 지정한 입력 토픽에 합성 메시지를 쏘고
출력 토픽을 받아 케이스의 기댓값과 대조할 뿐이다. 완전한 블랙박스.

케이스의 기댓값은 ★사람이 물리적으로 추론해서 적는다★. 대상 코드의 수식을
베껴 오면 검증이 아니라 동어반복이 되므로, 케이스마다 도출 근거를 함께 적는다.

    python3 -m tb.run inject --scenario scenarios/regression.yaml
"""
from __future__ import annotations

import math
import re
import time

import rclpy
import yaml
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from rosidl_runtime_py.convert import message_to_ordereddict
from rosidl_runtime_py.utilities import get_message

_IDX = re.compile(r"^data\[(\d+)\]$")


def array_slots(contract, topic):
    """계약의 signals 에서 `이름 → data[] 인덱스` 를 역으로 뽑는다.

    덕분에 케이스를 `fit_bL: 0.176` 처럼 ★이름으로★ 쓸 수 있다.
    배열 위치는 계약이 이미 알고 있으므로 케이스 파일에는 안 적는다.
    """
    slots = {}
    for name, sig in contract.signals.items():
        if sig.topic != topic:
            continue
        m = _IDX.match(sig.paths[0])
        if m:
            slots[name] = int(m.group(1))
    return slots


class Injector(Node):
    def __init__(self, in_topic, in_type, out_topic):
        super().__init__("testbed_inject")
        qos = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                         history=HistoryPolicy.KEEP_LAST, depth=10)
        self.in_cls = get_message(in_type)
        self.pub = self.create_publisher(self.in_cls, in_topic, qos)
        self.out_topic = out_topic
        self.sub = None
        self.latest = None
        self.count = 0
        self.create_timer(0.3, self._attach)
        self._attach()

    def _attach(self):
        if self.sub is not None:
            return
        for name, types in self.get_topic_names_and_types():
            if name == self.out_topic and types:
                self.sub = self.create_subscription(
                    get_message(types[0]), name, self._on_out,
                    QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                               history=HistoryPolicy.KEEP_LAST, depth=10))
                self.get_logger().info(f"출력 구독: {name} ({types[0]})")

    def _on_out(self, msg):
        self.latest = message_to_ordereddict(msg)
        self.count += 1

    def wait_ready(self, timeout=20.0):
        t0 = time.time()
        while time.time() - t0 < timeout and rclpy.ok():
            if self.sub is not None and self.pub.get_subscription_count() > 0:
                return True
            rclpy.spin_once(self, timeout_sec=0.05)
        return False

    def send(self, data, settle=0.5):
        """한 케이스를 쏘고 응답을 기다린다. 반환: 출력 dict 또는 None."""
        before = self.count
        msg = self.in_cls()
        msg.data = [float(x) for x in data]
        self.pub.publish(msg)
        deadline = time.time() + settle
        while self.count == before and time.time() < deadline and rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.01)
        return self.latest if self.count != before else None


def run_cases(contract, cases_path, warmup_hits=3):
    """케이스 전부를 실행하고 결과 목록을 돌려준다."""
    cfg = contract.raw.get("injection") or {}
    if not cfg:
        raise SystemExit("[inject] 계약에 injection: 블록이 없다.")
    in_topic = cfg["input"]["topic"]
    in_type = cfg["input"].get("type", "std_msgs/msg/Float32MultiArray")
    out_topic = cfg["output"]["topic"]
    slots = array_slots(contract, in_topic)
    width = (max(slots.values()) + 1) if slots else 8

    doc = yaml.safe_load(open(cases_path)) or {}
    cases = doc.get("cases", [])
    out_sigs = {n: s for n, s in contract.signals.items() if s.topic == out_topic}

    rclpy.init()
    node = Injector(in_topic, in_type, out_topic)
    results = []
    try:
        if not node.wait_ready():
            raise SystemExit(f"[inject] 대상 노드가 안 붙었다 "
                             f"(구독 {in_topic} / 발행 {out_topic} 확인)")
        for case in cases:
            data = [0.0] * width
            unknown = []
            for name, val in (case.get("input") or {}).items():
                if name in slots:
                    data[slots[name]] = float(val)
                else:
                    unknown.append(name)
            # 상태가 있는 노드(점프 게이트·EMA)를 케이스마다 안정시킨다
            got = None
            for _ in range(warmup_hits):
                got = node.send(data)
            checks = []
            if got is None:
                results.append({"name": case.get("name", "?"), "got": None,
                                "checks": [], "ok": False,
                                "note": "응답 없음", "unknown": unknown,
                                "desc": case.get("desc", "")})
                continue
            tol = case.get("tol") or {}
            default_tol = float(tol.get("_default", 1e-3))
            for key, want in (case.get("expect") or {}).items():
                sig = out_sigs.get(key)
                have = sig.extract(got) if sig else None
                t = float(tol.get(key, default_tol))
                ok = (have is not None
                      and isinstance(have, (int, float))
                      and abs(float(have) - float(want)) <= t)
                checks.append({"key": key, "want": float(want),
                               "have": (float(have)
                                        if isinstance(have, (int, float)) else None),
                               "tol": t, "ok": ok})
            results.append({
                "name": case.get("name", "?"), "desc": case.get("desc", ""),
                "checks": checks, "unknown": unknown,
                "ok": bool(checks) and all(c["ok"] for c in checks),
                "note": "" if checks else "expect 가 비어 있다",
            })
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return results


def report(results):
    n_ok = sum(1 for r in results if r["ok"])
    L = [f"# 신호 주입 검증 — {n_ok}/{len(results)} 통과", "",
         "영상·YOLO 없이 ★변환 수학만★ 검사한다. 입력을 직접 만들었으므로",
         "참값이 정확히 알려져 있다 — 값이 다르면 계산이 틀린 것이다.", "",
         "| 케이스 | 항목 | 기대 | 실제 | 오차 | 허용 | |",
         "|---|---|---|---|---|---|---|"]
    for r in results:
        if not r["checks"]:
            L.append(f"| {r['name']} | — | — | — | — | — | ❌ {r.get('note', '')} |")
            continue
        for i, c in enumerate(r["checks"]):
            nm = r["name"] if i == 0 else ""
            have = "—" if c["have"] is None else f"{c['have']:.4f}"
            err = ("—" if c["have"] is None
                   else f"{abs(c['have'] - c['want']):.4f}")
            L.append(f"| {nm} | {c['key']} | {c['want']:.4f} | {have} | {err} | "
                     f"{c['tol']:.4f} | {'✅' if c['ok'] else '❌'} |")
    L.append("")
    for r in results:
        if r["desc"]:
            L.append(f"- **{r['name']}** — {r['desc']}")
        if r.get("unknown"):
            L.append(f"  - ⚠️ 계약에 없는 입력 이름 무시됨: `{', '.join(r['unknown'])}`")
    L.append("")
    return "\n".join(L)


def theta_secant_vs_tangent(bev_h, bottom_ratio, lookahead_ratio, a, b=0.0):
    """참고 계산 — 시컨트 θ 와 접선 θ 가 곡률에서 얼마나 벌어지나.

    중심선이 x = a·y² + b·y + c 일 때
      시컨트 θ = atan( a·(y_near + y_look) + b )      ← 두 점을 잇는 직선
      접선   θ = atan( 2a·y_near + b )                ← 근점에서의 접선
    직선(a=0)이면 둘이 같고, 곡률이 커질수록 시컨트가 과소평가한다.
    """
    y_near = bev_h * bottom_ratio
    y_look = bev_h * lookahead_ratio
    sec = math.degrees(math.atan(a * (y_near + y_look) + b))
    tan = math.degrees(math.atan(2.0 * a * y_near + b))
    return sec, tan, tan - sec
