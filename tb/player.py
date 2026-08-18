"""영상 재생기 — mp4/webm 을 계약이 지정한 이미지 토픽으로 밀어 넣는다.

워크스페이스 코드는 전혀 모른다. 토픽명은 계약에서 온다.

재생 모드 세 가지:
  realtime  실차 타이밍 재현. 영상 fps × rate 로 밀고, 노드가 못 따라오면
            BEST_EFFORT depth=1 구독이 프레임을 버린다 → 실차와 같은 드롭 패턴.
  lockstep  ★재현성 최우선★. 한 프레임 밀고 sync_topic 이 올 때까지 기다린다.
            모든 프레임이 정확히 한 번씩 처리되므로 머신 속도와 무관하게
            같은 결과가 나온다. 알고리즘/게이트 회귀 비교는 이 모드로 한다.
  asfast    대기 없이 최대 속도. 처리량 상한 측정용.

섭동(perturb)은 원본 영상 하나로 강건성/메타모픽 테스트를 만들기 위한 것이다:
GT 가 없어도 "같은 장면인데 조건만 바뀌었을 때 출력이 얼마나 무너지는가"는 잴 수 있다.
"""
from __future__ import annotations

import argparse
import sys
import time

import cv2
import numpy as np
import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import Image
from std_msgs.msg import Float64MultiArray

FRAME_TOPIC = "/testbed/frame"      # 내부 채널 (frame_idx, t_pub_wall)
CONTROL_TOPIC = "/testbed/control"  # 뷰어 → 재생기 (pause/resume/step)


# ══════════════════════════════════════════════════════════════════════
#  섭동
# ══════════════════════════════════════════════════════════════════════
def make_perturb(spec):
    """'gamma:0.6' 같은 문자열 → 프레임 변환 함수. 여러 개는 '+' 로 잇는다."""
    if not spec or spec == "none":
        return lambda f: f
    fns = []
    for part in str(spec).split("+"):
        part = part.strip()
        if not part:
            continue
        kind, _, arg = part.partition(":")
        kind = kind.lower()
        if kind == "gamma":
            g = float(arg or 0.6)
            lut = np.array([((i / 255.0) ** g) * 255 for i in range(256)], np.uint8)
            fns.append(lambda f, lut=lut: cv2.LUT(f, lut))
        elif kind == "bright":
            b = float(arg or -40)
            fns.append(lambda f, b=b: cv2.convertScaleAbs(f, alpha=1.0, beta=b))
        elif kind == "blur":
            k = int(arg or 5) | 1
            fns.append(lambda f, k=k: cv2.GaussianBlur(f, (k, k), 0))
        elif kind == "noise":
            s = float(arg or 8)
            rng = np.random.default_rng(12345)   # 고정 시드 = 재현 가능
            fns.append(lambda f, s=s, r=rng: np.clip(
                f.astype(np.int16) + r.normal(0, s, f.shape).astype(np.int16),
                0, 255).astype(np.uint8))
        elif kind == "jpeg":
            q = int(arg or 30)

            def _jpeg(f, q=q):
                ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, q])
                return cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else f
            fns.append(_jpeg)
        elif kind == "hflip":
            fns.append(lambda f: cv2.flip(f, 1))
        elif kind == "scale":
            s = float(arg or 0.5)

            def _scale(f, s=s):
                h, w = f.shape[:2]
                small = cv2.resize(f, (max(2, int(w * s)), max(2, int(h * s))))
                return cv2.resize(small, (w, h))
            fns.append(_scale)
        else:
            raise SystemExit(f"[player] 모르는 섭동: {part}")

    def apply(f):
        for fn in fns:
            f = fn(f)
        return f
    return apply


# ══════════════════════════════════════════════════════════════════════
def _set_field(msg, path, value):
    """'linear.x' 같은 경로에 값을 넣는다 (aux 스텁 발행용)."""
    parts = path.split(".")
    tgt = msg
    for p in parts[:-1]:
        tgt = getattr(tgt, p)
    setattr(tgt, parts[-1], type(getattr(tgt, parts[-1]))(value))


class Player(Node):
    def __init__(self, args, aux_specs):
        super().__init__("testbed_player")
        self.args = args
        best = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
                          history=HistoryPolicy.KEEP_LAST, depth=1)
        rel = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                         history=HistoryPolicy.KEEP_LAST, depth=50)

        self.bridge = CvBridge()
        self.pub_img = self.create_publisher(Image, args.image_topic, best)
        self.pub_frame = self.create_publisher(Float64MultiArray, FRAME_TOPIC, rel)
        self.pub_clock = (self.create_publisher(Clock, "/clock", rel)
                          if args.sim_time else None)

        # 뷰어의 일시정지/단계 진행 요청
        from std_msgs.msg import String
        self.paused = False
        self.step_req = 0
        self.create_subscription(String, CONTROL_TOPIC, self._on_ctl, rel)

        # 동기 신호 — 타입을 모른 채 구독해야 하므로 런타임 발견에 맡긴다.
        self.sync_count = 0
        self._sync_sub = None
        self.sync_topic = args.sync_topic or None

        # aux 스텁 (상류 노드 대역)
        self._aux = []
        for spec in aux_specs:
            from rosidl_runtime_py.utilities import get_message
            cls = get_message(spec["type"])
            pub = self.create_publisher(cls, spec["topic"], rel)
            msg = cls()
            for k, v in (spec.get("fields") or {}).items():
                _set_field(msg, k, v)
            period = 1.0 / float(spec.get("rate_hz", 10.0))
            self._aux.append(self.create_timer(period,
                                               lambda p=pub, m=msg: p.publish(m)))

    def attach_sync(self):
        """sync_topic 의 타입이 광고되면 그때 구독한다."""
        if self._sync_sub is not None or not self.sync_topic:
            return True
        from rosidl_runtime_py.utilities import get_message
        for name, types in self.get_topic_names_and_types():
            if name == self.sync_topic and types:
                self._sync_sub = self.create_subscription(
                    get_message(types[0]), name, self._on_sync,
                    QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                               history=HistoryPolicy.KEEP_LAST, depth=50))
                self.get_logger().info(f"동기 토픽 연결: {name} ({types[0]})")
                return True
        return False

    def _on_sync(self, _msg):
        self.sync_count += 1

    def _on_ctl(self, msg):
        cmd = msg.data.strip().lower()
        if cmd == "pause":
            self.paused = True
        elif cmd == "resume":
            self.paused = False
        elif cmd == "step":
            self.step_req += 1

    def wait_if_paused(self):
        """정지 상태면 재개나 단계 요청이 올 때까지 여기서 멈춘다."""
        while self.paused and self.step_req == 0 and rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.02)
        if self.step_req:
            self.step_req -= 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--image-topic", default="/image_raw")
    ap.add_argument("--sync-topic", default="")
    ap.add_argument("--mode", default="lockstep",
                    choices=["lockstep", "realtime", "asfast"])
    ap.add_argument("--rate", type=float, default=1.0)
    ap.add_argument("--fps", type=float, default=0.0, help="0=영상 메타 사용")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0, help="0=끝까지")
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--perturb", default="none")
    ap.add_argument("--sim-time", action="store_true")
    ap.add_argument("--sync-timeout", type=float, default=8.0)
    ap.add_argument("--sync-timeout-first", type=float, default=90.0,
                    help="첫 프레임 대기(YOLO 는 첫 predict 에서 지연 로딩된다)")
    ap.add_argument("--sync-retries", type=int, default=2,
                    help="lockstep: 응답 없을 때 같은 프레임 재투입 횟수")
    ap.add_argument("--sync-settle-ms", type=float, default=20.0,
                    help="lockstep: 동기 수신 후 하류 노드 출력이 "
                         "같은 프레임으로 기록되도록 주는 여유")
    ap.add_argument("--warmup-s", type=float, default=0.0,
                    help="첫 프레임 전 대기 (모델 로딩 여유)")
    ap.add_argument("--aux-json", default="[]")
    ap.add_argument("--stats-out", default="")
    ap.add_argument("--progress-out", default="",
                    help="진행률을 주기적으로 쓸 JSON 경로")
    args, ros_argv = ap.parse_known_args(argv)

    import json
    aux_specs = json.loads(args.aux_json)

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise SystemExit(f"[player] 영상을 열 수 없다: {args.video}")
    fps = args.fps or cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if args.start:
        cap.set(cv2.CAP_PROP_POS_FRAMES, args.start)

    perturb = make_perturb(args.perturb)
    # 진행률 분모 — limit 이 있으면 그것, 없으면 남은 프레임 수
    total_target = args.limit if args.limit else max(0, total - args.start)
    if args.stride > 1 and total_target:
        total_target = total_target // args.stride

    rclpy.init(args=ros_argv)
    node = Player(args, aux_specs)
    log = node.get_logger()
    log.info(f"영상 {args.video} | {total}프레임 @{fps:.2f}fps | "
             f"mode={args.mode} perturb={args.perturb}")

    # 구독자(perception)가 붙을 때까지 기다린다.
    # BEST_EFFORT depth=1 이라 디스커버리 전에 쏘면 그대로 사라진다.
    t0 = time.time()
    while node.pub_img.get_subscription_count() == 0 and time.time() - t0 < 60.0:
        rclpy.spin_once(node, timeout_sec=0.05)
    if node.pub_img.get_subscription_count() == 0:
        log.error("이미지 구독자가 없다 — 대상 노드가 안 떴다")
        node.destroy_node(); rclpy.shutdown(); return 2
    t0 = time.time()
    while not node.attach_sync() and time.time() - t0 < 15.0:
        rclpy.spin_once(node, timeout_sec=0.05)

    if args.warmup_s > 0:
        t0 = time.time()
        while time.time() - t0 < args.warmup_s:
            rclpy.spin_once(node, timeout_sec=0.05)

    sim_t = 0.0
    dt = 1.0 / fps * args.stride
    idx = args.start
    pushed = 0
    retries = 0
    t_start = time.time()
    next_wall = t_start
    timeouts = 0

    try:
        while rclpy.ok():
            ok, frame = cap.read()
            if not ok:
                break
            if args.stride > 1 and (idx - args.start) % args.stride:
                idx += 1
                continue
            node.wait_if_paused()
            frame = perturb(frame)

            if node.pub_clock is not None:
                c = Clock()
                c.clock.sec = int(sim_t)
                c.clock.nanosec = int((sim_t - int(sim_t)) * 1e9)
                node.pub_clock.publish(c)

            img = node.bridge.cv2_to_imgmsg(frame, encoding="bgr8")
            img.header.frame_id = f"testbed:{idx}"
            if args.sim_time:
                img.header.stamp.sec = int(sim_t)
                img.header.stamp.nanosec = int((sim_t - int(sim_t)) * 1e9)
            else:
                img.header.stamp = node.get_clock().now().to_msg()

            lockstep = (args.mode == "lockstep" and node._sync_sub is not None)
            # ★재시도★ 이미지 구독은 BEST_EFFORT depth=1 이라 샘플이 그냥 유실될 수 있다.
            #   lockstep 에서 유실을 그대로 두면 그 프레임 행이 통째로 비고, 유실은
            #   런마다 달라서 회귀 비교가 흔들린다. 같은 프레임을 다시 밀어 1:1 을 지킨다.
            got = False
            for attempt in range((args.sync_retries + 1) if lockstep else 1):
                before = node.sync_count
                t_pub = time.time()
                mark = Float64MultiArray()
                mark.data = [float(idx), t_pub, float(pushed)]
                node.pub_frame.publish(mark)
                node.pub_img.publish(img)
                if not lockstep:
                    break
                budget = (args.sync_timeout_first if pushed < 2
                          else args.sync_timeout)
                deadline = time.time() + budget
                while node.sync_count == before and time.time() < deadline and rclpy.ok():
                    rclpy.spin_once(node, timeout_sec=0.005)
                if node.sync_count != before:
                    got = True
                    break
                retries += 1
                if retries <= 5:
                    log.warn(f"동기 무응답 → 재투입 (frame {idx}, {attempt+1}회)")
            if lockstep and not got:
                timeouts += 1
                log.error(f"동기 타임아웃 확정 (frame {idx})")

            pushed += 1
            idx += 1
            sim_t += dt

            if lockstep:
                # 하류 노드(judgment)가 이 프레임의 결과를 낼 때까지 조금 더 돈다.
                # 안 그러면 /lane_metrics 가 다음 프레임 번호로 기록된다.
                s_end = time.time() + args.sync_settle_ms / 1000.0
                while time.time() < s_end and rclpy.ok():
                    rclpy.spin_once(node, timeout_sec=0.002)
            elif args.mode == "realtime":
                next_wall += dt / max(1e-6, args.rate)
                while time.time() < next_wall and rclpy.ok():
                    rclpy.spin_once(node, timeout_sec=0.002)
            else:
                rclpy.spin_once(node, timeout_sec=0.0)

            if args.limit and pushed >= args.limit:
                break
            if args.progress_out and (pushed % 5 == 0 or pushed == 1):
                el = time.time() - t_start
                try:
                    with open(args.progress_out, "w") as pf:
                        json.dump({"pushed": pushed, "total": total_target,
                                   "sync": node.sync_count, "retries": retries,
                                   "timeouts": timeouts, "elapsed_s": round(el, 1),
                                   "fps": round(pushed / max(el, 1e-6), 2),
                                   "frame": idx - 1, "done": False}, pf)
                except OSError:
                    pass
            if pushed % 100 == 0:
                el = time.time() - t_start
                log.info(f"{pushed}프레임 ({pushed/max(el,1e-6):.1f} fps 투입)")
    finally:
        # 마지막 프레임의 출력이 프로브에 도착할 시간을 준다
        t0 = time.time()
        while time.time() - t0 < 2.0 and rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.05)
        el = time.time() - t_start
        log.info(f"완료: 투입 {pushed}, 동기수신 {node.sync_count}, "
                 f"재투입 {retries}, 타임아웃 {timeouts}, {el:.1f}s")
        if args.progress_out:
            try:
                with open(args.progress_out, "w") as pf:
                    json.dump({"pushed": pushed, "total": total_target,
                               "sync": node.sync_count, "retries": retries,
                               "timeouts": timeouts, "elapsed_s": round(el, 1),
                               "fps": round(pushed / max(el, 1e-6), 2),
                               "frame": idx - 1, "done": True}, pf)
            except OSError:
                pass
        if args.stats_out:
            with open(args.stats_out, "w") as fh:
                json.dump({"frames_pushed": pushed, "sync_received": node.sync_count,
                           "sync_timeouts": timeouts, "sync_retries": retries, "wall_s": el,
                           "video": args.video, "fps": fps, "mode": args.mode,
                           "perturb": args.perturb, "start": args.start,
                           "stride": args.stride, "total_frames": total}, fh, indent=2)
        cap.release()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
