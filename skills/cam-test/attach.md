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
그대로 돌리면 `resolve_scenario` 의 `block` 이 막는다.

### ② 실측이 가능하면 실측을 먼저 한다

대상 시스템을 평소처럼 띄울 수 있으면 그게 제일 정확하다. 토픽·타입·필드 배치를
**실제 메시지에서** 읽는다:

```bash
# 다른 터미널에서 대상 기동 후
cd "$TB" && python3 -m tb.discover --out contracts/<이름>_초안.yaml --seconds 8
```

숫자 배열은 의미를 알 수 없으므로 `f0, f1, …` 로 나온다. **이름 붙이는 것이 사람(과 너)의 일이다.**

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
cd "$TB" && python3 -m tb.run doctor --contract contracts/<이름>.yaml
```

**계약을 저장하기 전에 사용자에게 보여 준다.** 최소한 이 셋은 짚어서 확인받는다:
- `sync_topic` 이 프레임당 정확히 한 번 나가는 게 맞나 (아니면 lockstep 이 어긋난다)
- `signals` 의 이름과 단위가 맞나 (`theta` 가 도인지 라디안인지)
- `params` 에서 꺼야 할 것(창·녹화)을 다 껐나

### ⑥ 참고할 본

`contracts/white_camera.yaml` 이 가장 완전한 예다 — 주석이 사실상 시험 근거 문서다.
`contracts/demo_foreign.yaml` 은 **노드를 띄우지 않고 돌고 있는 시스템에 붙는**
`attach: true` 예다(실차에서 기록만 뜰 때).

## 시험 기준(`checks:`)은 계약이 아니라 시나리오에 쓴다

계약은 "어디를 보는가", 시나리오는 "무엇을 요구하는가"다. 판정 어휘는
`$TB/README.md` 와 `CLAUDE.md` 의 「판정 어휘」에 전부 있다.

기준을 처음 세울 때는 **한 번 돌려 본 실측값에서 출발**하되, 통과시키려고 느슨하게
잡지 않는다. 근거(`why:`)를 못 쓰는 기준은 기준이 아니다.
