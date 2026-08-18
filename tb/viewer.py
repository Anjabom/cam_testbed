"""실시간 뷰어 — 돌아가는 동안 눈으로 보고 손으로 세우면서 디버깅한다.

계약이 `debug_topics:` 로 선언한 이미지 토픽을 띄우고, 그 위에 지금 프레임의
신호값을 겹쳐 그린다. 숫자와 그림이 ★같은 프레임★에서 나온 것이라
"이 값이 왜 이렇게 나왔지"를 바로 눈으로 확인할 수 있다.

여기에도 대상 워크스페이스의 토픽 이름은 없다. 전부 계약에서 온다.

키:
  space  일시정지 / 재개      n  한 프레임 진행 (정지 상태에서)
  s      현재 화면 PNG 저장    q  뷰어 종료 (실행은 계속된다)
  [ ]    오버레이 끄기 / 켜기

정지·단계 진행은 `/testbed/control` 로 재생기에 전달된다. lockstep 재생에서는
정확히 한 프레임씩 끊어 볼 수 있다.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile,
                       ReliabilityPolicy)
from rosidl_runtime_py.convert import message_to_ordereddict
from rosidl_runtime_py.utilities import get_message
from sensor_msgs.msg import Image
from std_msgs.msg import Float64MultiArray, String

from . import encode
from .contract import load as load_contract
from .player import FRAME_TOPIC

CONTROL_TOPIC = "/testbed/control"


class Viewer(Node):
    def __init__(self, contract, save_dir, overlay_signals, fps=15.0):
        super().__init__("testbed_viewer")
        self.c = contract
        self.bridge = CvBridge()
        self.frames = {}          # topic -> 최신 BGR
        self.dirty = set()        # 새 이미지가 온 토픽 (mp4 중복 기록 방지)
        self.values = {}          # signal -> 최신 값
        self.frame_idx = -1
        self.writers = {}
        # mp4 프레임 i 가 영상의 몇 번 프레임인지 — 프론트가 이걸로 정확히 seek 한다.
        # 뷰어는 노드가 디버그를 그리기 시작한 뒤에야 첫 장을 받으므로
        # "0번이 start 번"이라는 보장이 없다. 그래서 실측해서 남긴다.
        self.first_frame = {}
        self.n_written = {}
        self.save_dir = Path(save_dir) if save_dir else None
        self.overlay_signals = overlay_signals
        self.fps = fps
        self.by_topic = contract.signals_by_topic()

        best = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
                          history=HistoryPolicy.KEEP_LAST, depth=1)
        rel = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                         history=HistoryPolicy.KEEP_LAST, depth=10)
        self.pub_ctl = self.create_publisher(String, CONTROL_TOPIC, rel)
        self.create_subscription(Float64MultiArray, FRAME_TOPIC,
                                 self._on_frame, rel)
        for t in contract.debug_topics:
            self.create_subscription(Image, t,
                                     lambda m, tt=t: self._on_img(tt, m), best)
            self.get_logger().info(f"디버그 영상 구독: {t}")
        self._sig_subs = {}
        self.create_timer(0.5, self._attach_signals)
        self._attach_signals()

    def _qos_for(self, topic):
        rel, dur = ReliabilityPolicy.RELIABLE, DurabilityPolicy.VOLATILE
        for i in self.get_publishers_info_by_topic(topic):
            if i.qos_profile.reliability == ReliabilityPolicy.BEST_EFFORT:
                rel = ReliabilityPolicy.BEST_EFFORT
            if i.qos_profile.durability == DurabilityPolicy.TRANSIENT_LOCAL:
                dur = DurabilityPolicy.TRANSIENT_LOCAL
        return QoSProfile(reliability=rel, durability=dur,
                          history=HistoryPolicy.KEEP_LAST, depth=10)

    def _attach_signals(self):
        adv = dict(self.get_topic_names_and_types())
        for topic in self.by_topic:
            if topic in self._sig_subs or topic not in adv or not adv[topic]:
                continue
            try:
                cls = get_message(adv[topic][0])
            except Exception:   # noqa: BLE001
                continue
            self._sig_subs[topic] = self.create_subscription(
                cls, topic, lambda m, tt=topic: self._on_sig(tt, m),
                self._qos_for(topic))

    def _on_frame(self, msg):
        if len(msg.data) >= 1:
            self.frame_idx = int(msg.data[0])

    def _on_sig(self, topic, msg):
        d = message_to_ordereddict(msg)
        for s in self.by_topic.get(topic, []):
            v = s.extract(d)
            if v is not None:
                self.values[s.name] = v

    def _on_img(self, topic, msg):
        try:
            self.frames[topic] = self.bridge.imgmsg_to_cv2(msg, "bgr8")
            self.dirty.add(topic)
        except Exception as e:      # noqa: BLE001
            self.get_logger().warn(f"{topic} 변환 실패: {e}")

    # ── 오버레이 ────────────────────────────────────────────────────
    def _draw(self, img, topic, paused):
        img = img.copy()
        h, w = img.shape[:2]
        lines = [f"frame {self.frame_idx}   {topic}"]
        for name in self.overlay_signals:
            v = self.values.get(name)
            if isinstance(v, float):
                lines.append(f"{name:<14} {v:+.4f}")
            elif v is not None:
                lines.append(f"{name:<14} {v}")
        pad, lh = 8, 22
        bw = 330
        bh = pad * 2 + lh * len(lines)
        panel = img[0:min(bh, h), 0:min(bw, w)]
        cv2.addWeighted(panel, 0.35, panel * 0, 0.65, 0, panel)
        for i, t in enumerate(lines):
            cv2.putText(img, t, (pad, pad + lh * (i + 1) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 255, 180), 1,
                        cv2.LINE_AA)
        if paused:
            cv2.putText(img, "PAUSED  (space=resume  n=step)",
                        (pad, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (0, 200, 255), 2, cv2.LINE_AA)
        return img

    def _record(self, topic, img):
        if self.save_dir is None:
            return
        wtr = self.writers.get(topic)
        if wtr is None:
            name = topic.strip("/").replace("/", "_") + ".mp4"
            # 이 영상은 ★사람이 되돌려 보기 위한 것★이지 타이밍 기록이 아니다.
            # 프레임 1개 = 처리 사이클 1개로 저장하고 재생 fps 는 고정값을 쓴다.
            # 코덱은 encode.Writer 가 고른다 — mp4v 로 쓰면 웹앱에서 재생되지 않는다.
            wtr = encode.Writer(self.save_dir / name, self.fps,
                                (img.shape[1], img.shape[0]))
            self.writers[topic] = wtr
            self.first_frame[topic] = self.frame_idx
            self.n_written[topic] = 0
        wtr.write(img)
        self.n_written[topic] = self.n_written.get(topic, 0) + 1

    def close(self):
        for w in self.writers.values():
            w.release()
        if self.save_dir and self.writers:
            import json
            meta = {}
            for topic, w in self.writers.items():
                name = w.path.name          # 코덱에 따라 .webm 일 수 있다
                meta[name] = {
                    "topic": topic, "fps": self.fps,
                    "first_frame": self.first_frame.get(topic, -1),
                    "count": self.n_written.get(topic, 0),
                }
            try:
                (self.save_dir / "debug_meta.json").write_text(
                    json.dumps(meta, indent=2, ensure_ascii=False))
            except OSError:
                pass
        cv2.destroyAllWindows()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="tb.viewer")
    ap.add_argument("--contract", required=True)
    ap.add_argument("--save-dir", default="", help="디버그 영상을 mp4 로 저장할 곳")
    ap.add_argument("--overlay", default="",
                    help="겹쳐 그릴 신호 (쉼표). 없으면 계약의 compare_signals 앞 8개")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--headless", action="store_true",
                    help="창을 띄우지 않고 mp4 저장만 한다")
    ap.add_argument("--fps", type=float, default=15.0, help="저장할 mp4 재생 속도")
    ap.add_argument("--jpeg", default="",
                    help="매 프레임 이 경로에 최신 화면을 덮어쓴다(라이브 화면용)")
    ap.add_argument("--seconds", type=float, default=0.0, help="0=무제한")
    args, ros_argv = ap.parse_known_args(argv)

    contract = load_contract(args.contract)
    if not contract.debug_topics:
        print("[viewer] 계약에 debug_topics 가 없다 — 볼 것이 없다.", file=sys.stderr)
        return 1
    overlay = ([s for s in args.overlay.split(",") if s]
               or contract.compare_signals[:8])
    if args.save_dir:
        Path(args.save_dir).mkdir(parents=True, exist_ok=True)

    rclpy.init(args=ros_argv)
    node = Viewer(contract, args.save_dir, overlay, args.fps)
    paused = False
    t0 = time.time()
    try:
        while rclpy.ok():   # 실행이 끝나면 오케스트레이터가 SIGINT 를 보낸다
            rclpy.spin_once(node, timeout_sec=0.01)
            for topic, img in list(node.frames.items()):
                fresh = topic in node.dirty
                node.dirty.discard(topic)
                shown = node._draw(img, topic, paused)
                if fresh:
                    node._record(topic, shown)   # 새 프레임일 때만 기록
                    if args.jpeg:
                        # 원자적 교체 — 반쯤 쓰인 파일을 브라우저가 읽지 않게.
                        # imwrite 는 확장자로 포맷을 정하므로 .tmp 를 못 쓴다 →
                        # imencode 로 바이트를 만들어 임시 파일에 쓰고 rename 한다.
                        okj, buf = cv2.imencode(
                            ".jpg", shown, [cv2.IMWRITE_JPEG_QUALITY, 80])
                        if okj:
                            tmp = Path(args.jpeg + ".part")
                            try:
                                tmp.write_bytes(buf.tobytes())
                                tmp.replace(args.jpeg)
                            except OSError:
                                pass
                if not args.headless:
                    if args.scale != 1.0:
                        shown = cv2.resize(shown, None, fx=args.scale, fy=args.scale)
                    cv2.imshow(topic, shown)
            if not args.headless:
                k = cv2.waitKey(1) & 0xFF
                if k == ord(" "):
                    paused = not paused
                    node.pub_ctl.publish(String(data="pause" if paused else "resume"))
                elif k == ord("n"):
                    node.pub_ctl.publish(String(data="step"))
                elif k == ord("s"):
                    for topic, img in node.frames.items():
                        fn = (f"snap_{node.frame_idx}_"
                              f"{topic.strip('/').replace('/', '_')}.png")
                        cv2.imwrite(str(Path(args.save_dir or ".") / fn),
                                    node._draw(img, topic, False))
                    node.get_logger().info(f"스냅샷 저장 (frame {node.frame_idx})")
                elif k == ord("q"):
                    break
            if args.seconds and time.time() - t0 > args.seconds:
                break
    except KeyboardInterrupt:
        pass                # 정상 종료 경로다 — 트레이스백을 남기지 않는다
    finally:
        if paused:
            try:
                node.pub_ctl.publish(String(data="resume"))
                time.sleep(0.2)
            except Exception:   # noqa: BLE001
                pass
        node.close()          # ★mp4 를 여기서 닫는다★
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
