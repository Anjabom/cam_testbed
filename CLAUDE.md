# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 이 저장소는 무엇인가

ROS 2 카메라 인지 노드를 **밖에서** 시험하는 테스트 플랫폼이다. 대상 워크스페이스를 import 하지
않고 `ros2 run` 서브프로세스로 띄워 DDS 로만 대화한다. 원본 영상을 이미지 토픽으로 밀어 넣고,
나오는 토픽을 전부 기록해 **지표 · 불변식 · 회귀 비교**로 판정한다. Ground Truth 는 없다.

설계 목표는 하나다: **대상 워크스페이스 코드가 바뀌어도 `tb/*.py` 는 바뀌지 않는다.**

`COLCON_IGNORE` 가 있어 colcon 빌드 대상이 아니다. 워크스페이스 밖(`~/cam_testbed/`)에 있고,
대상과는 계약의 `workspace:` 한 줄로만 이어진다. 상세 설계·절차는 `README.md`(§ 번호로 참조).

## 절대 어기면 안 되는 경계

1. **`tb/*.py` 와 `web/*` 에 대상 워크스페이스의 토픽명·필드 배치·노드명·파라미터명을 한 글자도
   쓰지 않는다.** 전부 `contracts/*.yaml` 에만 있다. 워크스페이스 1개 = 계약 1개.
   새 워크스페이스를 붙일 때도 **복사하지 않고 계약 파일만 하나 더 만든다**(§11).
2. **계약의 `path:` 는 후보 리스트**이고 앞에서부터 처음 맞는 것을 쓴다. 메시지 포맷이 바뀌면
   `tb/` 가 아니라 계약의 그 한 줄을 고친다. 어느 후보가 맞았는지는 리포트의 「계약 정합」이
   `ok` / `🔁 fallback` / `❌ drift` / `· silent` 로 알려 준다.
3. **판정은 엔진(`tb/analyze.py`)만 한다.** `web/app.js` 는 `summary.json` 의 `checks[].ok` 를
   색칠할 뿐 임계값을 JS 에 다시 쓰지 않는다. 기하(워프·수직도)도 서버의 `tb.geometry` 가 계산한다.
4. **화면 설정도 계약에서 나온다** — 프레임 탐색의 `frame_presets` · `frame_columns` · `events` 는
   계약에 있다(`web/server.py` 의 `contract_ui`). `app.js` 에 신호 이름을 박으면 다른 계약에서
   표가 통째로 빈다.
5. **웹의 허용 명령과 입력 폼은 `web/server.py` 의 `COMMANDS` 하나에서 나온다.** CLI 에 인자를
   늘렸으면 여기도 늘린다(안 늘리면 서버가 거부한다). `shell=False` 로 실행하고 셸 메타문자는 막는다.
6. **체크를 통과시키려고 임계값을 느슨하게 하지 않는다.** 시나리오 `checks:` 의 기준이 이 차량·
   영상에 안 맞는다고 판단되면 근거를 먼저 말하고 수정을 제안한다. 재는 쪽을 고쳐 숫자를 좋게
   만드는 것도 금지다(`tb.run feedback` 이 생성 문서에 이 규칙을 박아 넣는다).

## 자주 쓰는 명령

```bash
source /opt/ros/humble/setup.bash
cp local.yaml.example local.yaml          # 최초 1회: 이 머신의 영상·가중치 경로

python3 -m tb.selftest                    # 자체 검사 (ROS·영상 불필요) — 계약 문법을 건드리면 먼저 이것
python3 -m flake8 tb web                  # 린트 (max-line-length 100, .flake8)
python3 -m tb.run doctor                  # 환경·계약·워크스페이스·영상·코덱 점검
python3 -m tb.run run --scenario scenarios/regression.yaml
python3 -m tb.run baseline <런디렉토리명> --name regression
python3 -m tb.run reanalyze <런>          # 계약을 고친 뒤 과거 런을 재해석 (재실행 불필요)
python3 -m tb.run compare <기준|런> <런>
python3 -m tb.run feedback <런> --vs <이전 런>   # 결과 → 코드 개선 요청문
python3 -m tb.run inject                  # 영상·YOLO 없이 변환 수학만 (수초)
python3 -m tb.run list
python3 -m tb.run app                     # 웹앱을 별도 창으로 (web 은 서버만, :8770)
```

자체 검사 하나만 돌릴 때 — `eq()` 는 예외를 던지지 않고 `FAILS` 에 쌓으므로 **반드시 같이 찍는다**:

```bash
python3 -c "from tb import selftest as s; s.t_events(); print(s.FAILS or '통과')"
```

새 자체 검사는 `tb/selftest.py` 에 `t_*` 이름의 함수로 추가하면 `main()` 이 자동으로 줍는다.
"여기가 틀리면 모든 판정이 틀리는" 순수 함수만 대상이다(경로식·드리프트·hold·시퀀스·구간·전이·기하).

## 실행 한 번의 흐름

`tb/run.py` 가 오케스트레이터다. `_one_run()` 에서:

1. 계약 해석 — 인자 `--contract` → 시나리오 `contract:` → `local.yaml` 의 `default_contract`
   → `contracts/` 에 `.yaml` 이 하나면 그것. 여러 개인데 지정이 없으면 고르라고 멈춘다.
2. 대상 워크스페이스의 `install/setup.bash` 를 source 한 프리픽스로 `ros2 run` × 계약의 `nodes`,
   `tb.probe`(계약의 `observe` 토픽을 타입 불문 기록), `tb.player`(영상 → `stimulus.image_topic`
   + `aux` 퍼블리셔)를 각각 서브프로세스로 띄운다. `attach: true` 면 노드를 띄우지 않고 관찰만 한다.
3. 프레임↔출력 정렬은 테스트베드 내부 토픽 `/testbed/frame` 으로 한다(메시지에 스탬프가 없다).
   `lockstep` 은 한 프레임 밀고 `sync_topic` 을 기다린다 → 머신 속도와 무관하게 같은 결과.
4. `tb.analyze` 가 `raw.jsonl` → `signals.csv`(경로식 평가 + `hold_signals` + `hold_initial`)
   → 통계·게이트 통과율(`consumers`)·θ 품질·전이·노드 로그 → `report.md` / `summary.json`,
   그리고 `baselines/<시나리오 name>.csv` 가 있으면 `compare.md`.

**`raw.jsonl` 에 원본 메시지가 그대로 남는다** — 그래서 계약을 고쳐도 베이스라인을 버리지 않고
`reanalyze` 로 과거 런을 새 계약으로 다시 읽을 수 있다. 이 성질을 깨는 변경은 하지 않는다.

## 설정 3층

| 층 | 파일 | 무엇 |
|---|---|---|
| 결합 | `contracts/*.yaml` | 토픽·필드·노드·게이트 상수·캘리브 대상·화면 설정. 머신 독립 |
| 시나리오 | `scenarios/*.yaml` | 영상(**논리 이름**)·구간·모드·변형·`checks`·`compare_tol`. 머신 독립 |
| 머신 | `local.yaml` (git 제외) | `videos:` 논리 이름 → 실제 경로, 가중치 경로, `params` 덮어쓰기 |

`local.yaml` 이 시나리오 위에 덮어써진다(`_deep_merge`). 시나리오에 절대경로를 박지 않는다.
`tb/config.py` 는 이 파일들을 **주석을 보존하며** 고친다(웹앱의 등록·시나리오 본뜨기가 이걸 쓴다) —
새 쓰기 기능을 넣을 때도 주석을 날리지 않는다. `config.resolve_scenario()` 가 실행 전에
`block`(TODO 초안 계약, 빌드 안 됨, 영상 없음)과 `warn` 을 만든다.

## 판정 어휘 (`checks:`)

전부 `tb/analyze.py` 의 `_stat_value()` 에 있다. 신호 이름과 조건식은 계약·시나리오가 주므로
**새 판정 종류를 넣을 때도 워크스페이스 이름이 코드로 들어오지 않는다.**

- `{stat: <summary 키>}` — `drop_rate`, `latency_p95_ms`, `valid_rate` …
- `{signal: x, stat: mean|std|min|max|p95|frac_nonzero|frac_zero|p95_abs_diff|max_abs_diff|increases|decreases}`
  (`when_valid: true` 로 유효 프레임만, `where:` 로 조건에 맞는 행만)
- `{where: "<식>", stat: count|frac|runs|run_max_frames|run_max_s}` — 조건이 참인 **구간**
- `{event: "sig:0->1"|"sig:*->RED", stat: count|frame|t_s|at:<신호>, last: true}` — 값이 바뀐 **그 순간**
- `{stat: "contribution:<consumer id>"|"theta:<키>"|"flag_rate:<이름>"|"log:<log_events 이름>"}`

조건식은 `tb/expr.py`(AST 화이트리스트)로만 평가한다 — `eval` 을 쓰지 않는다.
**초 단위 판정의 시간 기준은 벽시계가 아니라 `프레임 ÷ 영상fps × 배속`**(`analyze.scene_fps`)이다.

## 함정

- **베이스라인은 머신을 넘지 못한다.** GPU·가중치·영상이 다르면 값이 달라진다. 새 머신에서는
  기준을 다시 등록한다. `baselines/<이름>.json` 의 출처와 실행 조건이 다르면 `compare.md` 에 경고가 붙는다.
- 기준 이름은 **시나리오의 `name:`** 을 따른다. `--tag` 는 런 구분용이라 비교 대상을 바꾸지 않는다.
- 절대 경로가 박히는 곳은 셋뿐이다: 계약의 `workspace:`, `local.yaml`, 그리고 등록된 베이스라인.
- `lockstep` 은 벽시계가 실제로 흐른다. 대상이 `time.time()` 으로 hold·staleness 를 재면
  30fps 실차와 다르게 동작한다 → 타이밍을 보려면 `realtime` 시나리오(§12).
- `cv2.VideoWriter` 기본 코덱 `mp4v` 는 브라우저가 재생하지 못한다(오류 없이 검은 화면).
  영상을 새로 굽는 코드는 반드시 `tb/encode.py` 를 거친다(ffmpeg/H.264 → webm → mp4v 폴백).
- 런마다 임의의 `ROS_DOMAIN_ID` 를 쓴다 — 다른 ROS 세션과 섞이지 않게 하려는 것이다.
- `runs/`, `local.yaml`, `*.log` 는 git 제외. 사람이 붙인 정리 정보는 `runs/_index.json` 한 파일에
  모이고 삭제는 `runs/_trash/` 로 **옮기는 것**이라 결과 파일이 지워지지 않는다.
- 웹앱은 **외부 의존성 0**(표준 라이브러리 · CDN·웹폰트·JS 라이브러리 없음). 대회 현장에서
  네트워크 없이 돌아야 한다 — 의존성을 추가하지 않는다.

## 언어 관행

코드 주석·문서·커밋 메시지는 **한국어**로 쓴다. 커밋 제목은 `영역: 무엇` 꼴이다
(`웹앱: …`, `용어: …`). 주석은 "무엇을 하는가"보다 **왜 그 값인가 · 무엇에 데였는가**를 적는
기존 밀도를 따른다(계약·시나리오 YAML 의 주석이 사실상 시험 근거 문서다).
