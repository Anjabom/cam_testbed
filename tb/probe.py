"""토픽 프로브 — 계약에 적힌 토픽을 ★타입을 모른 채★ 전부 기록한다.

메시지 클래스를 import 하지 않는다. 런타임에 광고된 타입 문자열을
get_message() 로 되살려 구독하므로, /lane_metrics 가 Float32MultiArray 에서
커스텀 msg 로 바뀌어도 프로브 코드는 그대로다.

QoS 도 발행자 쪽 광고를 보고 맞춘다 — perception 은 토픽마다 다른 프로파일을
쓰는데(이미지는 BEST_EFFORT, 상태는 RELIABLE) 하드코딩하면 조용히 안 붙는다.

출력: JSONL 한 줄 = 메시지 하나
  {"t": 수신시각, "frame": 그때 투입중이던 프레임번호, "t_frame": 프레임 발행시각,
   "topic": "...", "type": "...", "msg": {…}}
"""
from __future__ import annotations

import argparse
import array
import json
import signal
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile,
                       ReliabilityPolicy)
from rosidl_runtime_py.convert import message_to_ordereddict
from rosidl_runtime_py.utilities import get_message

from .player import FRAME_TOPIC


def _jsonable(o):
    if isinstance(o, (array.array, tuple)):
        return [_jsonable(x) for x in o]
    if isinstance(o, list):
        return [_jsonable(x) for x in o]
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, bytes):
        return list(o)
    if hasattr(o, "tolist"):
        return o.tolist()
    return o


class Probe(Node):
    def __init__(self, topics, out_path, skip_types, anchor=""):
        super().__init__("testbed_probe")
        self.topics = list(topics)
        # 행 번호의 근거. 보통은 재생기가 /testbed/frame 으로 알려 주지만,
        # attach 모드(영상 자극 없이 돌고 있는 시스템 관찰)에서는 재생기가 없다.
        # 그럴 때는 앵커 토픽이 올 때마다 1행으로 센다.
        self.anchor = anchor
        self.ext_frame = False
        self.anchor_n = -1
        self.skip_types = set(skip_types)
        self.f = open(out_path, "w", buffering=1 << 16)
        self.subs = {}
        self.counts = {}
        self.frame = -1
        self.t_frame = 0.0
        self.n = 0

        from std_msgs.msg import Float64MultiArray
        self.create_subscription(
            Float64MultiArray, FRAME_TOPIC, self._on_frame,
            QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                       history=HistoryPolicy.KEEP_LAST, depth=50))
        # 아직 안 뜬 토픽을 계속 찾는다 (노드가 늦게 떠도 놓치지 않음)
        self.create_timer(0.5, self._discover)
        self._discover()

    def _on_frame(self, msg):
        if len(msg.data) >= 2:
            self.ext_frame = True
            self.frame = int(msg.data[0])
            self.t_frame = float(msg.data[1])

    # ── QoS 를 발행자에게서 베껴 온다 ────────────────────────────────
    def _qos_for(self, topic):
        infos = self.get_publishers_info_by_topic(topic)
        rel, dur, depth = ReliabilityPolicy.RELIABLE, DurabilityPolicy.VOLATILE, 50
        for i in infos:
            q = i.qos_profile
            if q.reliability == ReliabilityPolicy.BEST_EFFORT:
                rel = ReliabilityPolicy.BEST_EFFORT
            if q.durability == DurabilityPolicy.TRANSIENT_LOCAL:
                dur = DurabilityPolicy.TRANSIENT_LOCAL
        return QoSProfile(reliability=rel, durability=dur,
                          history=HistoryPolicy.KEEP_LAST, depth=depth)

    def _discover(self):
        advertised = dict(self.get_topic_names_and_types())
        for t in self.topics:
            if t in self.subs or t not in advertised:
                continue
            types = advertised[t]
            if not types:
                continue
            tname = types[0]
            if tname in self.skip_types:
                self.get_logger().info(f"건너뜀(대용량 타입): {t} [{tname}]")
                self.subs[t] = None
                continue
            try:
                cls = get_message(tname)
            except Exception as e:      # noqa: BLE001
                self.get_logger().warn(f"타입 로드 실패 {t} [{tname}]: {e}")
                continue
            self.subs[t] = self.create_subscription(
                cls, t, lambda m, tt=t, ty=tname: self._on_msg(tt, ty, m),
                self._qos_for(t))
            self.counts[t] = 0
            self.get_logger().info(f"구독: {t} [{tname}]")

    def _on_msg(self, topic, tname, msg):
        if not self.ext_frame and self.anchor and topic == self.anchor:
            self.anchor_n += 1
            self.frame = self.anchor_n
            self.t_frame = time.time()
        rec = {
            "t": time.time(),
            "frame": self.frame,
            "t_frame": self.t_frame,
            "topic": topic,
            "type": tname,
            "msg": _jsonable(message_to_ordereddict(msg)),
        }
        self.f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.counts[topic] = self.counts.get(topic, 0) + 1
        self.n += 1

    def close(self):
        try:
            self.f.flush()
            self.f.close()
        except Exception:   # noqa: BLE001
            pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser()
    ap.add_argument("--topics", required=True, help="쉼표 구분")
    ap.add_argument("--out", required=True)
    ap.add_argument("--anchor", default="",
                    help="재생기가 없을 때 행 번호의 기준이 될 토픽")
    ap.add_argument("--skip-types", default="sensor_msgs/msg/Image,"
                                            "sensor_msgs/msg/CompressedImage",
                    help="기록하지 않을 타입(이미지 등)")
    args, ros_argv = ap.parse_known_args(argv)

    rclpy.init(args=ros_argv)
    node = Probe([t for t in args.topics.split(",") if t],
                 args.out,
                 [s for s in args.skip_types.split(",") if s],
                 args.anchor)

    stop = {"v": False}

    def _sig(_s, _f):
        stop["v"] = True
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    try:
        while rclpy.ok() and not stop["v"]:
            rclpy.spin_once(node, timeout_sec=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        node.get_logger().info(
            f"기록 {node.n}건 | " + ", ".join(f"{k}:{v}" for k, v in node.counts.items()))
        node.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
