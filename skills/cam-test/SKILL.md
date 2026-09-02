---
name: cam-test
description: ROS 2 카메라 인지 워크스페이스를 cam_testbed 로 시험한다 — 영상을 밀어 넣어 실행하고, 디버그 영상을 남기고, 결과를 그 워크스페이스의 testbed_results/ 에 보관하고, 개선 피드백까지 만든다. "이 워크스페이스 테스트해줘", "이 영상으로 돌려봐", "테스트베드 돌려", "카메라 인지 시험", "차선 인식 테스트", "디버그 영상 뽑아줘", "회귀 비교" 같은 요청에 쓴다. 다른 워크스페이스에서 호출해도 된다.
---

# cam-test — 워크스페이스 밖에서 카메라 인지를 시험한다

대상 워크스페이스를 import 하지 않는다. `ros2 run` 서브프로세스로 띄우고 DDS 로만 대화한다.
영상을 이미지 토픽으로 밀어 넣고, 나오는 토픽을 전부 기록해 **지표 · 불변식 · 회귀 비교**로
판정한다. Ground Truth 는 없다.

**테스트베드가 대상을 아는 통로는 계약(`contracts/*.yaml`) 한 파일뿐이다.**
워크스페이스 1개 = 계약 1개. 새 워크스페이스를 붙일 때도 테스트베드를 복사하지 않는다.

## 0단계 — 어디서 돌릴지부터 정한다

```bash
TB="${CLAUDE_PLUGIN_ROOT:-$HOME/cam_testbed}"      # 테스트베드 위치
WS="$(pwd)"                                        # 시험 대상 = 지금 있는 워크스페이스
[ -d "$TB/tb" ] || echo "테스트베드를 못 찾았다"
```

**`$TB` 를 못 찾으면 여기서 멈추고 사용자에게 위치를 묻는다.** 스킬은 ROS 2·GPU·가중치·
테스트베드 자체를 설치해 주지 못한다 — 없으면 없다고 말하는 것이 맞다.

모든 `tb.run` 명령은 `cd "$TB"` 하고 친다 (`python3 -m` 이 그 디렉터리를 sys.path 로 쓴다).
ROS 언더레이도 먼저 source 한다:

```bash
source /opt/ros/humble/setup.bash
cd "$TB" && [ -f local.yaml ] || cp local.yaml.example local.yaml
```

`local.yaml` 을 새로 만들었으면 **가중치 경로를 이 머신에 맞게 고쳐야 한다** —
`.example` 의 경로는 남의 머신 것이다. 사용자에게 가중치 위치를 묻는다.

## 1단계 — 계약이 있는가

```bash
grep -l "workspace:.*$WS" "$TB"/contracts/*.yaml
```

- **있다** → 그 계약을 쓴다. 2단계로.
- **없다** → 새로 붙이는 워크스페이스다. **[attach.md](attach.md) 를 읽고 그대로 한다.**
  계약 초안을 만들고 나면 반드시 사용자 확인을 받는다 — 계약이 틀리면 리포트는 초록인데
  잰 값이 엉뚱한, 로그를 뒤져야만 드러나는 거짓말이 된다.

검증은 항상 이걸로 한다(아무것도 바꾸지 않는다):

```bash
cd "$TB" && python3 -m tb.run doctor --contract contracts/<계약>.yaml
```

## 2단계 — 영상과 시나리오

사용자가 영상 경로를 준다. 시나리오에는 **논리 이름만** 적고 실제 경로는 `local.yaml` 에 둔다
(시나리오는 머신 독립이어야 한다). 둘 다 기존 함수로 한다 — 손으로 YAML 을 쓰지 않는다:

```bash
cd "$TB" && python3 -c "
from tb import config
print(config.set_video('<논리이름>', '<영상 절대경로>'))     # 해상도·fps·프레임수를 되돌려준다
"
```

그 다음, **이미 그 계약을 쓰는 시나리오가 있으면 본을 떠서** 판정 기준과 근거 주석을
물려받는다. 없으면 빈 틀을 만든다:

```bash
cd "$TB" && python3 -c "
from tb import config
print(config.clone_scenario('<본 시나리오>.yaml', '<새이름>', '<논리이름>'))
# 본이 없을 때만:
# print(config.new_scenario('<새이름>', '<계약파일명>.yaml', '<논리이름>'))
"
```

실행 전에 막힐 것이 있는지 본다. `block` 이 비어 있어야 돌린다:

```bash
cd "$TB" && python3 -c "
import json
from tb import config
r = config.resolve_scenario('<새이름>.yaml')
print(json.dumps({k: r[k] for k in ('block', 'warn', 'workspace', 'nodes')},
                 ensure_ascii=False, indent=1))"
```

`block` 에 걸리는 것은 셋이다 — TODO 가 남은 초안 계약, 빌드 안 된 워크스페이스, 없는 영상.

## 3단계 — 실행

```bash
cd "$TB" && python3 -m tb.run run --scenario scenarios/<새이름>.yaml
```

- **디버그 영상은 기본으로 남는다**(`--record-debug` 가 기본 켜짐, 런당 2~7MB).
  끄지 않는다 — 나중에 "그때 노드가 뭘 봤나"를 물으면 답할 방법이 그 mp4 뿐이다.
- 두 번째 실행부터는 `--baseline <기준이름>` 을 붙여 이전과 비교한다.
- 오래 걸린다(영상 길이 × 프레임당 처리). **백그라운드로 돌리고 기다린다.**
- 기준 등록은 `python3 -m tb.run baseline <런>` 이고, 이름은 **시나리오의 `name:`** 을 따른다.

## 4단계 — 피드백

```bash
cd "$TB" && python3 -m tb.run feedback runs/<런ID> [--vs runs/<이전런>]
```

`feedback.md` 는 "무엇을 고쳐야 하나" 순으로 정렬된 개선 요청문이다. 이걸 읽고
**진단과 제안까지** 한다 — 원인 후보, 볼 파일, 고칠 방향. **대상 워크스페이스 코드는
사용자가 따로 지시할 때만 고친다.**

**내보내기(5단계) 전에 뽑는다** — 그래야 `feedback.md` 가 보관본에 같이 들어간다.

## 5단계 — 대상 워크스페이스로 내보낸다

```bash
cd "$TB" && python3 skills/cam-test/export.py runs/<런ID> --ws "$WS"
```

`<워크스페이스>/testbed_results/<런ID>/` 에 리포트·`summary.json`·`signals.csv`·
**디버그 영상**·`run_env.json`(호스트·GPU·가중치·코드 해시)이 들어가고, `INDEX.md` 에 한 줄이
쌓인다(같은 런을 다시 내보내면 그 줄을 갈아 끼운다). `COLCON_IGNORE` 와 `.gitignore`
등록도 이때 자동으로 된다.

원본은 `$TB/runs/` 에 그대로 남는다 — 재해석(`reanalyze`)·재실행(`replay`)은 원본으로 하고,
새 산출물이 생기면 **다시 내보내면 된다**(덮어쓴다).

## 결과를 읽을 때 — 순서를 지킨다

1. **「행 N」을 먼저 본다.** 행이 0 인데 체크는 「13/13 통과」로 찍힌다 — 잰 것이 없으면
   위반할 것도 없기 때문이다. `export.py` 가 이 경우 경고를 찍는다.
2. **「계약 정합」 표**를 본다. `❌ drift` 면 숫자를 논하기 전에 **계약의 그 한 줄**을 고친다.
   `🔁 fallback` 은 정상(마이그레이션 중). `· silent` 는 그 토픽이 한 번도 안 왔다는 뜻이다.
3. 그 다음에 체크 통과 여부와 회귀 비교를 본다.

## 절대 하지 않는 것

- **체크를 통과시키려고 임계값을 느슨하게 고치지 않는다.** 기준이 이 차량·영상에 안 맞는다고
  판단되면 **근거를 먼저 말하고 수정을 제안**한다. 재는 쪽(`tb/analyze.py`·계약)을 고쳐
  숫자를 좋게 만드는 것도 같은 금지다.
- **`tb/*.py` 와 `web/*` 에 대상 워크스페이스의 토픽명·필드 배치·노드명·파라미터명을 쓰지
  않는다.** 전부 계약 YAML 에만 있다. 포맷이 바뀌면 계약의 `path:` 후보 한 줄을 고친다.
- **베이스라인을 머신 너머로 옮기지 않는다.** GPU·가중치·영상이 다르면 값이 달라진다.
  새 머신에서는 기준을 다시 등록한다 — `run_env.json` 이 그 판단의 근거다.
- **시나리오에 절대경로를 박지 않는다.** 영상은 `local.yaml` 의 `videos:` 를 거친다.
- 테스트베드에 외부 의존성을 추가하지 않는다(표준 라이브러리만 — 현장에 네트워크가 없다).

## 그 밖의 명령

`tb.run` 서브커맨드가 곧 이 스킬의 실행 엔진이다. 사람이 화면으로 보고 싶어 하면
웹앱을 띄운다 — 실행·비교·기준 등록·프레임 탐색이 전부 그 안에 있다.

| 하고 싶은 것 | 명령 |
|---|---|
| 화면으로 보기 | `python3 -m tb.run app` (별도 창) / `web` (브라우저) |
| 옛 런의 디버그 영상 | `python3 -m tb.run replay <런>` — 영상은 실행 중에만 잡힌다 |
| 계약 고친 뒤 과거 런 다시 읽기 | `python3 -m tb.run reanalyze <런>` (`raw.jsonl` 이 남아 있다) |
| 판정에 쓴 값으로 그림 그리기 | `python3 -m tb.run render <런> --mp4 auto` |
| 조건에 맞는 프레임 뽑기 | `python3 -m tb.run harvest <런> --where "<식>"` |
| 대상 빌드 | `python3 -m tb.run build --contract <계약>` (파이썬만 고쳤으면 불필요) |
| 테스트베드 자체 검사 | `python3 -m tb.selftest` (ROS·영상 불필요) |

`$TB/README.md` 에 § 번호로 전체 설계와 절차가 있다.
사람이 읽는 사용 설명은 [USAGE.md](USAGE.md) — 사용자가 "이 스킬 어떻게 쓰냐"고 물으면 거기를 가리킨다.
