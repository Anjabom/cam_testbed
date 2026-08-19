"""계약 초안 생성기 — 돌고 있는 ROS 그래프를 들여다보고 계약 YAML 을 뽑아낸다.

다른 워크스페이스에 테스트베드를 붙일 때 첫 단계다. 대상 시스템을 평소처럼 띄워
놓고 이걸 돌리면, 토픽·타입·필드 배치를 실제 메시지에서 읽어 계약 초안을 만든다.
사람이 할 일은 ★어느 값이 무슨 의미인지 이름 붙이는 것★뿐이다.

    ros2 launch <다른패키지> whatever.launch.py      # 대상 시스템 기동
    python3 -m tb.discover --out contracts/other.yaml --seconds 8

숫자 배열(Float32MultiArray 등)은 의미를 알 수 없으므로 `data[i]` 를 인덱스별로
전부 뽑아 `f0, f1, …` 이름으로 적어 둔다. 사람이 이름만 바꾸면 계약이 완성된다.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

import rclpy
import yaml
from rclpy.node import Node
from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile,
                       ReliabilityPolicy)
from rosidl_runtime_py.convert import message_to_ordereddict
from rosidl_runtime_py.utilities import get_message

# 관찰 대상에서 기본 제외 — 대용량이거나 인프라 토픽
SKIP_TYPES = {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage",
              "sensor_msgs/msg/PointCloud2", "tf2_msgs/msg/TFMessage"}
SKIP_TOPICS = {"/rosout", "/parameter_events", "/clock", "/tf", "/tf_static"}

NUM = (int, float)

# MultiArray 의 layout 은 메타데이터라 신호가 아니다
BOILERPLATE = ("layout.", "header.")


def _flatten(d, prefix=""):
    """메시지 dict → {경로식: 예시값}. 숫자 배열은 인덱스별로 펼친다."""
    out = {}
    for k, v in d.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, path + "."))
        elif isinstance(v, (list, tuple)):
            nums = [x for x in v if isinstance(x, NUM)]
            if nums and len(nums) == len(v) and len(v) <= 64:
                for i, x in enumerate(v):
                    out[f"{path}[{i}]"] = x
            else:
                out[path] = f"<{type(v).__name__}[{len(v)}]>"
        elif isinstance(v, (str, bool)) or isinstance(v, NUM):
            out[path] = v
    return out


class Scout(Node):
    def __init__(self, include, exclude):
        super().__init__("testbed_discover")
        self.include, self.exclude = include, exclude
        self.seen = {}       # topic -> {"type":…, "n":…, "fields":{path: 예시}}
        self.subs = {}
        self.create_timer(0.5, self.scan)

    def _qos(self, topic):
        rel, dur = ReliabilityPolicy.RELIABLE, DurabilityPolicy.VOLATILE
        for i in self.get_publishers_info_by_topic(topic):
            if i.qos_profile.reliability == ReliabilityPolicy.BEST_EFFORT:
                rel = ReliabilityPolicy.BEST_EFFORT
            if i.qos_profile.durability == DurabilityPolicy.TRANSIENT_LOCAL:
                dur = DurabilityPolicy.TRANSIENT_LOCAL
        return QoSProfile(reliability=rel, durability=dur,
                          history=HistoryPolicy.KEEP_LAST, depth=10)

    def scan(self):
        for name, types in self.get_topic_names_and_types():
            if name in self.subs or not types:
                continue
            if name in SKIP_TOPICS or types[0] in SKIP_TYPES:
                continue
            if self.include and not any(s in name for s in self.include):
                continue
            if any(s in name for s in self.exclude):
                continue
            try:
                cls = get_message(types[0])
            except Exception:   # noqa: BLE001
                continue
            self.seen[name] = {"type": types[0], "n": 0, "fields": {}}
            self.subs[name] = self.create_subscription(
                cls, name, lambda m, t=name: self._on(t, m), self._qos(name))
            self.get_logger().info(f"발견: {name} [{types[0]}]")

    def _on(self, topic, msg):
        e = self.seen[topic]
        e["n"] += 1
        if e["n"] <= 3:      # 앞 몇 개만 봐도 배치는 알 수 있다
            e["fields"] = _flatten(message_to_ordereddict(msg))


def build_contract(seen, name, workspace):
    live = {t: e for t, e in seen.items() if e["n"] > 0}
    observe = sorted(live)
    signals, notes, numeric, categorical = {}, [], [], []

    def add(key, topic, path, sample):
        signals[key] = {"topic": topic, "path": [path]}
        (categorical if isinstance(sample, str) else numeric).append(key)

    for topic in observe:
        e = live[topic]
        short = topic.strip("/").replace("/", "_")
        fields = {k: v for k, v in e["fields"].items()
                  if not k.startswith(BOILERPLATE)}
        if len(fields) == 1 and "data" in fields:
            add(short, topic, "data", fields["data"])
            continue
        for path, sample in fields.items():
            if not isinstance(sample, (str, bool)) and not isinstance(sample, NUM):
                continue
            key = f"{short}_{path.replace('[', '').replace(']', '').replace('.', '_')}"
            add(key, topic, path, sample)
        if any("[" in k for k in fields):
            notes.append(f"{topic}: 숫자 배열이라 의미를 알 수 없다 — "
                         f"신호 이름을 사람이 붙일 것")
    return {
        "version": 1,
        "name": name,
        "workspace": workspace or "TODO: 대상 워크스페이스 경로",
        "nodes": [{"id": "TODO", "package": "TODO", "executable": "TODO",
                   "node_name": "TODO", "params": {}}],
        "stimulus": {"image_topic": "/image_raw"},
        "sync_topic": observe[0] if observe else None,
        "observe": observe,
        "signals": signals,
        "compare_signals": numeric,
        "compare_categorical": categorical,
        "compare_sequence": [],
        "hold_signals": [],
        "mirror_odd_signals": [],
    }, notes, live


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="tb.discover")
    ap.add_argument("--out", default="", help="계약 초안을 쓸 경로 (없으면 표준출력)")
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--name", default="discovered")
    ap.add_argument("--workspace", default="")
    ap.add_argument("--include", default="", help="이 문자열이 든 토픽만 (쉼표)")
    ap.add_argument("--exclude", default="", help="이 문자열이 든 토픽 제외 (쉼표)")
    args, ros_argv = ap.parse_known_args(argv)

    # ★있는 계약을 덮어쓰지 않는다★ 초안은 통째로 새로 쓰는 것이라, 웹의 0단계로
    #   만들어 둔 계약에 같은 이름으로 내보내면 `workspace:` 와 손으로 적은 주석
    #   (= 시험 근거 문서)이 전부 날아간다. 옆에 초안을 두고 사람이 옮겨 붙인다.
    if args.out and pathlib.Path(args.out).exists():
        alt = pathlib.Path(args.out).with_suffix(".draft.yaml")
        print(f"[discover] 이미 있는 파일을 덮어쓰지 않는다: {args.out}\n"
              f"           초안은 통째로 새로 쓰는 것이라 workspace: 와 주석이 날아간다.\n"
              f"           → --out {alt} 로 뽑아 필요한 줄만 옮겨 붙일 것.", file=sys.stderr)
        return 2

    rclpy.init(args=ros_argv)
    node = Scout([s for s in args.include.split(",") if s],
                 [s for s in args.exclude.split(",") if s])
    t0 = time.time()
    while rclpy.ok() and time.time() - t0 < args.seconds:
        rclpy.spin_once(node, timeout_sec=0.05)

    draft, notes, live = build_contract(node.seen, args.name, args.workspace)
    node.destroy_node()
    rclpy.shutdown()

    if not live:
        print("[discover] 메시지를 하나도 못 받았다. 대상 시스템이 돌고 있는지, "
              "ROS_DOMAIN_ID 가 같은지 확인할 것.", file=sys.stderr)
        return 1

    text = ("# tb.discover 가 만든 계약 초안 — 사람이 손봐야 완성된다.\n"
            "#  1) nodes: 를 실제 패키지/실행파일로 채운다\n"
            "#  2) 신호 이름을 의미 있는 것으로 바꾼다 (아래 관찰 결과 참고)\n"
            "#  3) sync_topic 을 '프레임 처리가 끝났음'을 알리는 토픽으로 바꾼다\n"
            "#  4) 상태 유지형 신호는 hold_signals 로, 타이머 발행 신호는\n"
            "#     compare_sequence 로 옮긴다\n"
            + "".join(f"#  ! {n}\n" for n in notes)
            + yaml.safe_dump(draft, allow_unicode=True, sort_keys=False, width=100))

    print("── 관찰 결과 ──", file=sys.stderr)
    for t, e in sorted(live.items()):
        print(f"  {t}  [{e['type']}]  {e['n']}건", file=sys.stderr)
        for path, sample in list(e["fields"].items())[:12]:
            print(f"      {path:<24} = {sample!r}", file=sys.stderr)

    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"\n계약 초안 → {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
