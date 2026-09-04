"""디버그 영상 녹화기 — 대상 노드가 그리는 그림을 mp4 로 남긴다.

계약이 `debug_topics:` 로 선언한 이미지 토픽을 받아, 그 위에 ★같은 프레임★의
신호값을 겹쳐 그려 저장한다. 숫자와 그림이 한 프레임에서 나온 것이라
"이 값이 왜 이렇게 나왔나"를 나중에 눈으로 확인할 수 있다.

★이것이 사후에 만들 수 없는 유일한 기록이다★ — raw.jsonl 에는 숫자만 남는다.
그래서 실행마다 기본으로 켜져 있고, 옛 런의 영상을 보려면 `tb.run replay` 로
같은 조건에서 한 번 더 돌리는 수밖에 없다.

★창을 띄우지 않는다★ [2026-09-04] 예전에는 같은 파일이 대화형 뷰어이기도 해서
space/n 으로 세워 가며 보고, 계약이 준 키로 타임라인을 저작했다. 그 화면은
"돌려 놓고 지켜보는" 사용법을 전제했는데, 실제로는 아무도 지켜보지 않았다 —
결과 폴더의 mp4 를 나중에 보는 것이 전부였다. 남은 것은 녹화뿐이다.

여기에도 대상 워크스페이스의 토픽 이름은 없다. 전부 계약에서 온다.
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
from std_msgs.msg import Float64MultiArray

from . import encode
from .contract import load as load_contract
from .geometry import put_text            # ★한글이 되는 putText★ (cv2 는 ASCII 만)
from .player import FRAME_TOPIC

#  빈 프레임을 한 번에 채울 수 있는 최대 장수. 노드가 오래 멎었을 때(추론 지연·
#  기동 대기) 파일이 폭주하지 않게 두는 안전장치다 — 30fps 기준 10초.
MAX_FILL = 300


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
        # 영상 i 번째 장 = 원본 몇 번 프레임인가 (★장마다★ 실측해 남긴다).
        # first_frame + i 로 계산하면 안 된다 — 노드가 디버그를 매 프레임 내는
        # 보장이 없다. 실제로 1291프레임 런에서 663장만 온 적이 있고(≈2프레임당
        # 1장), 그러면 웹앱의 디버그 영상이 뒤로 갈수록 2배씩 어긋난다.
        self.written_frames = {}
        self.save_dir = Path(save_dir) if save_dir else None
        self.overlay_signals = overlay_signals
        self.fps = fps
        self.by_topic = contract.signals_by_topic()

        best = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
                          history=HistoryPolicy.KEEP_LAST, depth=1)
        rel = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                         history=HistoryPolicy.KEEP_LAST, depth=10)
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
    def _draw(self, img, topic):
        #  ★한글은 geometry.put_text(PIL) 로 그린다★ cv2.putText 는 ASCII 만 그려
        #  라벨이 통째로 ???? 가 된다 — 노드 HUD 가 폰트를 따로 쓰는 것과 같은 이유다.
        img = img.copy()
        h, w = img.shape[:2]
        lines = [f"frame {self.frame_idx}   {topic}"]
        for name in self.overlay_signals:
            v = self.values.get(name)
            if isinstance(v, float):
                lines.append(f"{name:<14} {v:+.4f}")
            elif v is not None:
                lines.append(f"{name:<14} {v}")
        pad, lh = 8, 24
        bw = 360
        bh = pad * 2 + lh * len(lines)
        panel = img[0:min(bh, h), 0:min(bw, w)]
        cv2.addWeighted(panel, 0.35, panel * 0, 0.65, 0, panel)
        for i, t in enumerate(lines):
            put_text(img, t, (pad, pad + lh * (i + 1) - 4), 17, (180, 255, 60))

        return img

    def _record(self, topic, img):
        """디버그 그림을 mp4 로 남긴다 — ★원본 영상과 프레임이 1:1★ 이 되게.

        노드는 매 프레임 디버그를 그리지 않는다(3프레임에 한 장쯤). 예전에는
        «그리는 주기» 를 앞 12장으로 재서 저장 fps 를 낮췄는데, 기동 직후 간격이
        들쭉날쭉해 추정이 런마다 흔들렸다 — 같은 시나리오가 10.006fps 로도,
        5.003fps 로도 저장됐고 뒤엣것은 43.0초 영상이 95.1초(2.2배 느림)가 됐다.

        그래서 추정을 버리고 ★안 그린 프레임은 직전 그림을 그대로 채운다★.
        파일은 원본 fps 로 저장되고 길이·속도가 원본과 같아진다 — mpv 든 브라우저든
        무엇으로 열어도 맞다. 웹앱의 배속 보정도 저절로 1× 가 된다(아래
        written_frames 의 간격이 전부 1 이 되므로). 같은 그림의 반복이라 H.264 가
        거의 공짜로 압축한다.
        """
        if self.save_dir is None:
            return
        wtr = self.writers.get(topic)
        if wtr is None:
            wtr = self._open_writer(topic, img)
            self.first_frame[topic] = self.frame_idx
        frames = self.written_frames.setdefault(topic, [])
        #  frame_idx 를 모르는 런(/testbed/frame 이 없는 attach 관찰)에서는 -1 에
        #  멈춰 있다 → gap 이 0 이하가 되므로 그때는 한 장만 쓴다.
        last = frames[-1] if frames else self.frame_idx - 1
        gap = self.frame_idx - last
        n = 1 if gap <= 0 else min(gap, MAX_FILL)
        for k in range(n):
            wtr.write(img)
            frames.append(last + 1 + k if gap > 0 else self.frame_idx)
        self.n_written[topic] = len(frames)

    def _open_writer(self, topic, img):
        name = topic.strip("/").replace("/", "_") + ".mp4"
        # 코덱은 encode.Writer 가 고른다 — mp4v 로 쓰면 웹앱에서 재생되지 않는다.
        # fps 는 ★원본 영상 그대로★ 다 — 빈 프레임을 채워 1:1 로 쓰기 때문이다.
        wtr = encode.Writer(self.save_dir / name, self.fps,
                            (img.shape[1], img.shape[0]))
        self.writers[topic] = wtr
        return wtr

    def close(self):
        for w in self.writers.values():
            w.release()
        if self.save_dir and self.writers:
            import json
            meta = {}
            for topic, w in self.writers.items():
                name = w.path.name          # 코덱에 따라 .webm 일 수 있다
                meta[name] = {
                    # fps = 이 파일의 저장 속도, src_fps = 원본 영상의 fps.
                    # 빈 프레임을 채워 1:1 로 쓰므로 지금은 둘이 같다 — 예전 런은
                    # 다르고, 웹앱이 둘을 견줘 「원본과 같은 속도」 배속을 정한다.
                    "topic": topic, "fps": self.fps,
                    "src_fps": self.fps,
                    "first_frame": self.first_frame.get(topic, -1),
                    "count": self.n_written.get(topic, 0),
                    "frames": self.written_frames.get(topic, []),
                }
            try:
                (self.save_dir / "debug_meta.json").write_text(
                    json.dumps(meta, indent=2, ensure_ascii=False))
            except OSError:
                pass


def _die_with_parent():
    """부모(tb.run)가 죽으면 이 프로세스도 SIGTERM 을 받게 한다 (Linux). [2026-08-25]

    tb.run 이 이 녹화기를 exec 로 띄우므로 부모는 곧 tb.run 이다. tb.run 이
    timeout·kill 로 ★정상 정리 없이★ 죽어도(그러면 finally 가 안 돌아 stop() 이
    호출되지 않는다) 커널이 여기에 SIGTERM 을 보내 준다 — 고아가 되어 CPU 를
    태우며 남는 일이 없어진다. 다른 OS 에서는 조용히 넘어간다.
    """
    try:
        import ctypes                                 # noqa: PLC0415
        import signal as _sig                         # noqa: PLC0415
        PR_SET_PDEATHSIG = 1
        ctypes.CDLL("libc.so.6").prctl(PR_SET_PDEATHSIG, _sig.SIGTERM, 0, 0, 0)
    except Exception:      # noqa: BLE001
        pass               # Linux 아니거나 libc 없음 — 정상 정리 경로는 그대로 있다


def main(argv=None):
    _die_with_parent()
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="tb.viewer")
    ap.add_argument("--contract", required=True)
    ap.add_argument("--save-dir", default="", help="디버그 영상을 mp4 로 저장할 곳")
    ap.add_argument("--overlay", default="",
                    help="겹쳐 그릴 신호 (쉼표). 없으면 계약의 compare_signals 앞 8개")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="저장 전 축소 배율 (1=원본)")
    ap.add_argument("--fps", type=float, default=15.0,
                    help="원본 영상의 fps (저장 속도를 원본과 1:1 로 맞춘다)")
    ap.add_argument("--jpeg", default="",
                    help="매 프레임 이 경로에 최신 화면을 덮어쓴다 (진행 확인용)")
    ap.add_argument("--seconds", type=float, default=0.0, help="0=무제한")
    args, ros_argv = ap.parse_known_args(argv)

    contract = load_contract(args.contract)
    if not contract.debug_topics:
        print("[viewer] 계약에 debug_topics 가 없다 — 남길 그림이 없다.",
              file=sys.stderr)
        return 1
    overlay = ([s for s in args.overlay.split(",") if s]
               or contract.compare_signals[:8])
    if args.save_dir:
        Path(args.save_dir).mkdir(parents=True, exist_ok=True)

    rclpy.init(args=ros_argv)
    node = Viewer(contract, args.save_dir, overlay, args.fps)
    t0 = time.time()
    try:
        while rclpy.ok():   # 실행이 끝나면 오케스트레이터가 SIGINT 를 보낸다
            rclpy.spin_once(node, timeout_sec=0.01)
            for topic, img in list(node.frames.items()):
                if topic not in node.dirty:
                    continue          # ★새 그림일 때만 기록★ (같은 장 중복 방지)
                node.dirty.discard(topic)
                shown = node._draw(img, topic)
                if args.scale != 1.0:
                    shown = cv2.resize(shown, None, fx=args.scale, fy=args.scale)
                node._record(topic, shown)
                if args.jpeg:
                    # 원자적 교체 — 반쯤 쓰인 파일을 다른 프로세스가 읽지 않게.
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
            if args.seconds and time.time() - t0 > args.seconds:
                break
    except KeyboardInterrupt:
        pass                # 정상 종료 경로다 — 트레이스백을 남기지 않는다
    finally:
        node.close()          # ★mp4 를 여기서 닫는다★
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
