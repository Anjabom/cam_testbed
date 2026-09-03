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
from .geometry import put_text            # ★한글이 되는 putText★ (cv2 는 ASCII 만)
from .player import FRAME_TOPIC, _set_field

CONTROL_TOPIC = "/testbed/control"

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

        # ── 타임라인 저작 (계약의 stimulus.aux[].keys 가 있을 때만) ──────
        #   ★키 이름·값·라벨은 전부 계약이 준다★ — 여기에 워크스페이스 어휘가
        #   들어오면 다른 계약에서 엉뚱한 키가 뜬다(경계 규칙 ①·④).
        #   누른 순간의 frame_idx 를 적어 두었다가 끝날 때 schedule: 로 뱉는다.
        self.keys = {}            # 눌린 키 문자 -> (pub, field, value, label)
        self.marks = []           # [(frame, value, label)] — 저작 결과
        for spec in (getattr(contract, "aux", None) or []):
            ks = spec.get("keys") or {}
            if not ks:
                continue
            cls = get_message(spec["type"])
            pub = self.create_publisher(cls, spec["topic"], rel)
            field = str(spec.get("field", "data"))
            for k, kv in ks.items():
                self.keys[str(k)[:1]] = (pub, cls, field, kv.get("value"),
                                         str(kv.get("label", k)), spec["topic"])

        #  ★저작하려면 영상을 봐야 한다★ 이 노드는 디버그 이미지를 안 내므로
        #  (debug_topics 비어 있음), 플레이어가 밀어넣는 ★자극 이미지★ 를 그대로
        #  받아 배경으로 깐다 — 그것이 곧 노드가 보는 그림이고 프레임 번호와 짝이다.
        self.stim_topic = getattr(contract, "image_topic", "") or ""
        if self.keys and self.stim_topic and self.stim_topic not in contract.debug_topics:
            self.create_subscription(
                Image, self.stim_topic,
                lambda m, tt=self.stim_topic: self._on_img(tt, m), best)
            self.get_logger().info(f"저작 배경 영상 구독: {self.stim_topic}")

    def press(self, ch):
        """저작 키 하나 — 지금 값을 발행하고 '몇 프레임에서 눌렀나' 를 적는다."""
        hit = self.keys.get(ch)
        if hit is None:
            return False
        pub, cls, field, val, label, topic = hit
        msg = cls()
        _set_field(msg, field, val)
        pub.publish(msg)
        self.marks.append((self.frame_idx, topic, val, label))
        self.get_logger().info(f"🧪 frame {self.frame_idx} → {label} ({val})")
        return True

    def schedule_json(self):
        """저작 결과를 aux_schedule 모양 {토픽: {프레임: 값}} 으로. 판정 런이 재생한다."""
        out = {}
        for f, topic, val, _lab in self.marks:
            if f >= 0:
                out.setdefault(topic, {})[int(f)] = val
        return out

    def schedule_yaml(self):
        """저작 결과를 시나리오에 붙일 수 있는 모양으로. 빈 저작이면 ''."""
        if not self.marks:
            return ""
        body = ", ".join(f"{f}: {v}" for f, _t, v, _l in self.marks if f >= 0)
        why = " · ".join(f"{f}={l}" for f, _t, _v, l in self.marks if f >= 0)
        return f"    schedule: {{{body}}}   # {why}\n"

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

        #  ★저작 모드★ 키 안내와 지금까지 찍은 마크를 화면 아래에 그린다.
        if self.keys:
            legend = "  ".join(f"[{ch}] {lab}({val:g})"
                               for ch, (_p, _c, _f, val, lab, _tp)
                               in sorted(self.keys.items()))
            put_text(img, legend, (pad, h - 58), 18, (90, 220, 255))
            recent = "  ".join(f"{f}:{lab}" for f, _t, _v, lab in self.marks[-6:])
            put_text(img, f"마크 {len(self.marks)}개  {recent}",
                     (pad, h - 32), 16, (200, 200, 200))
        if paused:
            put_text(img, "⏸ 정지 (space=재개  n=한프레임)", (pad, h - 8), 18,
                     (0, 200, 255))
        return img

    def status_canvas(self, paused):
        """디버그 이미지가 없는 계약을 위한 ★빈 화면★ — 저작 키를 띄우려고 있다.

        이 노드처럼 디버그 토픽을 안 내는 대상은 볼 그림이 없다. 그래도 재생 중에
        키를 누르려면 창이 하나는 떠 있어야 한다(cv2.waitKey 는 창이 있어야 먹는다).
        """
        import numpy as np                                 # noqa: PLC0415
        img = np.zeros((320, 620, 3), np.uint8)
        img[:] = (28, 26, 24)
        put_text(img, "영상 기다리는 중…", (16, 40), 20, (200, 200, 200))
        for i, (ch, (_p, _c, _f, val, label, _tp)) in enumerate(sorted(self.keys.items())):
            put_text(img, f"[{ch}]  {label} = {val:g}", (16, 200 + 28 * i),
                     18, (90, 220, 255))
        put_text(img, "space=정지  n=한프레임  q=닫기", (16, 300), 16,
                 (150, 150, 150))
        return self._draw(img, "타임라인 저작", paused)

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
        cv2.destroyAllWindows()


def _die_with_parent():
    """부모(tb.run)가 죽으면 이 프로세스도 SIGTERM 을 받게 한다 (Linux). [2026-08-25]

    ★고아 창 방지의 핵심★ tb.run 이 --watch 를 exec 로 띄우므로 이 뷰어의 부모는
    곧 tb.run 이다. tb.run 이 timeout·kill 로 ★정상 정리 없이★ 죽어도(그러면 finally
    가 안 돌아 stop() 이 호출되지 않는다) 커널이 여기에 SIGTERM 을 보내 준다 —
    cv2 창을 문 채 영영 남는 일이 없어진다. 다른 OS 에서는 조용히 넘어간다.
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
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--headless", action="store_true",
                    help="창을 띄우지 않고 mp4 저장만 한다")
    ap.add_argument("--fps", type=float, default=15.0,
                    help="원본 영상의 fps (저장 속도는 디버그를 그리는 주기로 맞춘다)")
    ap.add_argument("--jpeg", default="",
                    help="매 프레임 이 경로에 최신 화면을 덮어쓴다(라이브 화면용)")
    ap.add_argument("--seconds", type=float, default=0.0, help="0=무제한")
    args, ros_argv = ap.parse_known_args(argv)

    contract = load_contract(args.contract)
    #  ★저작 키가 있으면 디버그 영상이 없어도 뜬다★ 볼 그림은 없지만 누를 것이 있다.
    authoring = any((s.get("keys") or {}) for s in (getattr(contract, "aux", None) or []))
    if not contract.debug_topics and not authoring:
        print("[viewer] 계약에 debug_topics 도 stimulus.aux[].keys 도 없다 — "
              "볼 것도 누를 것도 없다.", file=sys.stderr)
        return 1
    overlay = ([s for s in args.overlay.split(",") if s]
               or contract.compare_signals[:8])
    if args.save_dir:
        Path(args.save_dir).mkdir(parents=True, exist_ok=True)

    rclpy.init(args=ros_argv)
    node = Viewer(contract, args.save_dir, overlay, args.fps)
    paused = False
    t0 = time.time()
    #  ★띄운 창을 추적한다★ 사용자가 X 로 닫으면 다음 프레임에 imshow 가 다시
    #  띄우므로, 닫힘을 감지해 종료로 처리한다(아래 _user_closed).
    win_shown = set()

    def _user_closed():
        """사용자가 창을 X 로 닫았는가 — 하나라도 닫혔으면 True."""
        for w in list(win_shown):
            try:
                if cv2.getWindowProperty(w, cv2.WND_PROP_VISIBLE) < 1:
                    return True
            except cv2.error:
                return True          # 이미 파괴된 창을 물으면 닫힌 것이다
        return False

    try:
        while rclpy.ok():   # 실행이 끝나면 오케스트레이터가 SIGINT 를 보낸다
            rclpy.spin_once(node, timeout_sec=0.01)
            if not node.frames and node.keys and not args.headless:
                #  창 제목은 ASCII 로 — 한글이면 Qt 제목표시줄에서 ???? 가 된다.
                cv2.imshow("authoring", node.status_canvas(paused))
                win_shown.add("authoring")
            elif "authoring" in win_shown and node.frames:
                #  실제 영상이 오기 시작하면 임시 대기창을 닫는다(둘이 겹치지 않게).
                try:
                    cv2.destroyWindow("authoring")
                except cv2.error:
                    pass
                win_shown.discard("authoring")
            for topic, img in list(node.frames.items()):
                fresh = topic in node.dirty
                node.dirty.discard(topic)
                shown = node._draw(img, topic, paused)
                if fresh:
                    #  ★자극 영상은 mp4 로 남기지 않는다★ 저작 배경일 뿐이라 1080p
                    #  원본을 통째로 다시 굽는 것은 낭비다(디버그 토픽만 기록한다).
                    if topic != getattr(node, "stim_topic", None):
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
                    win_shown.add(topic)
            if not args.headless:
                k = cv2.waitKey(1) & 0xFF
                #  ★X 로 창을 닫았으면 종료★ 안 그러면 다음 프레임에 imshow 가 다시
                #  띄워 '꺼도 계속 켜지는' 것으로 보인다(q 키와 같게 취급한다).
                if _user_closed():
                    break
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
                elif k == ord("q") or k == 27:      # q 또는 ESC 로 닫는다
                    break
                elif k != 255 and node.press(chr(k)):
                    pass          # 계약이 준 저작 키 — press() 가 기록·발행한다
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
        sched = node.schedule_yaml()
        if sched:
            #  ★그대로 시나리오의 aux 항목에 붙여 넣는 모양★ 으로 낸다.
            #  판정 런은 이걸 재생한다 — 사람이 누른 그 순간이 아니라.
            print("\n타임라인 저작 결과 — 시나리오의 stimulus.aux 항목에 붙일 것:\n"
                  + sched, file=sys.stderr)
            if args.save_dir:
                import json                          # noqa: PLC0415
                (Path(args.save_dir) / "schedule.yaml").write_text(sched)
                #  ★기계가 읽는 모양★ {토픽:{프레임:값}} — cmd_run 이 이걸 읽어
                #  시나리오에 저장하고 판정 런을 한 번 더 돌린다(config.set_aux_schedule).
                (Path(args.save_dir) / "schedule.json").write_text(
                    json.dumps(node.schedule_json()))
        node.close()          # ★mp4 를 여기서 닫는다★
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
