# 새 워크스페이스 붙이기 — 계약 한 장 쓰기

**테스트베드를 복사하지 않는다. 계약 파일을 하나 더 만든다.**
계약이 대상을 아는 유일한 통로다 — 여기가 틀리면 리포트는 초록인데 잰 값이 엉뚱하다.

## 순서

### ① 빈 계약을 만든다

```bash
cd "$TB" && python3 -c "
from tb import config
print(config.new_contract('<이름>', '<워크스페이스 절대경로>'))"
```

`contracts/<이름>.yaml` 이 생긴다. 노드·토픽 자리는 `TODO` 다 —
그대로 두면 `doctor` 가 「계약에 TODO 가 남아 있지 않다 ❌」로 막는다.

### ② 실측이 가능하면 실측을 먼저 한다

대상 시스템을 평소처럼 띄울 수 있으면 그게 제일 정확하다. 토픽·타입·필드 배치를
**실제 메시지에서** 읽는다:

```bash
# 다른 터미널에서 대상 기동 후 — 무엇이 떠 있고 어떤 모양인가
source /opt/ros/humble/setup.bash && source <워크스페이스>/install/setup.bash
ros2 topic list
ros2 topic info -v <토픽>                 # 타입과 발행자
ros2 topic hz <토픽>                      # 프레임당 한 번 나가는가 (sync_topic 후보)
ros2 topic echo --once <토픽>             # ★필드가 배열의 몇 번째인지★ 여기서 센다
ros2 param dump /<노드>                    # declare 된 파라미터 전부
```

숫자 배열(`Float32MultiArray` 같은 것)은 이름이 없으므로 **몇 번째가 무엇인지는
발행 코드를 봐야 안다.** `echo` 로 본 값과 소스의 `msg.data = [...]` 를 나란히 놓고
센다 — 여기서 한 칸 밀리면 리포트 전체가 조용히 엉뚱한 값을 잰다.

### ③ 못 띄우면 소스를 읽어 채운다

대상 워크스페이스의 `src/` 에서 다음을 찾아 계약을 채운다:

| 계약 항목 | 소스에서 찾을 것 |
|---|---|
| `nodes[].package` / `executable` | `setup.py` 의 `entry_points` → `console_scripts` |
| `nodes[].node_name` | `super().__init__("...")` 에 넘긴 이름 |
| `nodes[].params` | `declare_parameter(...)` 목록. **테스트에서 꺼야 할 것**(창 띄우기·녹화)을 반드시 끈다 |
| `stimulus.image_topic` | 인지 노드가 `create_subscription(Image, ...)` 하는 토픽 |
| `stimulus.aux` | 그 노드가 **구독하는데 아무도 안 보내는** 토픽. 없으면 게이트가 아예 안 돈다 |
| `sync_topic` | 프레임 처리가 끝날 때마다 **정확히 한 번** 나가는 토픽 |
| `observe` | 판정에 쓸 출력 토픽 전부 |
| `signals[].path` | 발행 코드에서 값이 배열의 몇 번째로 들어가는지 (`data[2]` 처럼) |
| `debug_image_topic` | 디버그 그림을 발행하는 토픽 — **이게 있어야 디버그 영상이 남는다** |

**`path:` 는 후보 리스트로 쓴다.** 신 포맷을 앞에, 구 포맷을 뒤에 두면 마이그레이션
중에도 회귀 비교가 안 끊긴다. 어느 쪽이 맞았는지는 리포트의 「계약 정합」이 알려준다.

```yaml
theta_deg: { topic: /lane_metrics, path: [theta_lane_deg, "data[2]"] }
#                                         ^신 포맷        ^구 포맷
```

### ④ 실차와 같은 조건을 강제한다 (`require_params`)

가중치 백엔드가 다르면 **지연도 마스크도 실차와 다른 것을 재게 되는데 리포트는 그대로
초록으로 나온다.** 실행 전에 막는다:

```yaml
require_params:
  <가중치 파라미터명>:
    endswith: .engine        # 실차가 TensorRT 면
    exists: true
    why: "실차와 같은 백엔드로 재야 지연·마스크가 실차와 같다"
```

### ⑤ 검증하고 사용자 확인을 받는다

```bash
cd "$TB" && python3 -m tb.run doctor --contract contracts/<이름>.yaml \
    --video /절대/경로/영상.mp4
```

첫 런을 짧게 돌려 **「계약 정합」 표**를 보는 것이 진짜 검증이다 — 선언한 경로가
실제 메시지에 맞았는지는 메시지를 받아 봐야만 안다:

```bash
cd "$TB" && python3 -m tb.run run --contract contracts/<이름>.yaml \
    --video /절대/경로/영상.mp4 --limit 40 --note "계약이 맞았는지"
```

**계약을 저장하기 전에 사용자에게 보여 준다.** 최소한 이 셋은 짚어서 확인받는다:
- `sync_topic` 이 프레임당 정확히 한 번 나가는 게 맞나 (아니면 lockstep 이 어긋난다)
- `signals` 의 이름과 단위가 맞나 (`theta` 가 도인지 라디안인지)
- `params` 에서 꺼야 할 것(창·녹화)을 다 껐나

### ⑥ 참고할 본

`contracts/white_camera.yaml` 이 가장 완전한 예다 — 주석이 사실상 시험 근거 문서다.
`contracts/demo_foreign.yaml` 은 **노드를 띄우지 않고 돌고 있는 시스템에 붙는**
`attach: true` 예다(실차에서 기록만 뜰 때).

## 계약에 쓰지 않는 것

**합격/불합격 기준은 어디에도 쓰지 않는다.** 이 테스트베드는 판정하지 않는다 —
재고, 그리고, 남긴다. 계약이 정하는 것은 「어디를 보는가」와 「어떻게 재는가」뿐이다.

계약에 넣으면 리포트가 훨씬 쓸모 있어지는 절이 셋 있다. 전부 **관측**이지 판정이 아니다.

| 절 | 무엇 | 왜 |
|---|---|---|
| `consumers:` | 받는 쪽 노드의 게이트 조건들 | 「차선을 봤나」가 아니라 **받는 쪽이 실제로 썼나**. 최대 병목 단계가 곧 다음에 손댈 곳이다 |
| `events:` | 상태가 바뀌는 신호와 그때 같이 볼 값 | "언제 브레이크가 들어갔나 · 그때 정지선까지 몇 px 였나" |
| `log_events:` | 노드 로그에서 셀 문구 | 토픽에 안 나오는 근거(기동 배너·개입 사유) |
| `calibration:` | 맞출 대상과 그 파라미터 이름 | 이게 있어야 **보정 스튜디오**에 이 워크스페이스가 뜬다 |

`contracts/white_camera.yaml` 의 그 절들이 가장 완전한 예다.
