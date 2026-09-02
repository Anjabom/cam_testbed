# cam-test 스킬 — 자세한 사용 설명

> 이 문서는 **사람이 읽는 안내서**다. Claude 가 따르는 절차서는 [SKILL.md](SKILL.md),
> 새 워크스페이스를 붙이는 절차는 [attach.md](attach.md) 에 따로 있다.

---

## 0. 이 스킬이 무엇을 대신해 주는가

전에는 이랬다.

```bash
cd ~/cam_testbed                       # 워크스페이스를 떠나서
source /opt/ros/humble/setup.bash
vi local.yaml                          # 영상 경로를 등록하고
vi scenarios/새시나리오.yaml            # 시나리오를 만들고
python3 -m tb.run doctor --contract …  # 점검하고
python3 -m tb.run run --scenario …     # 돌리고
ls runs/                               # 결과가 어디 갔는지 찾고
python3 -m tb.run feedback runs/…      # 피드백을 뽑고
                                       # 결과는 cam_testbed 안에만 남는다
```

이제는 **시험할 워크스페이스에서** 이렇게만 말하면 된다.

```
~/white_vote_ws$ claude
> 이 워크스페이스를 /home/me/video/night.mp4 로 테스트해줘
```

스킬이 계약 확인 → 영상 등록 → 시나리오 생성 → 점검 → 실행 → **결과를 이 워크스페이스로
내보내기** → 피드백까지 순서대로 밟는다. 디버그 영상도 자동으로 딸려 온다.

**대신해 주지 못하는 것도 분명히 하자.** 스킬은 ROS 2 · GPU 드라이버 · 학습 가중치 ·
cam_testbed 자체를 설치해 주지 못한다. 그것들이 없으면 "없다"고 말하고 멈춘다.

---

## 1. 설치

### 1-1. 이 머신에 cam_testbed 가 있어야 한다

```bash
git clone https://github.com/Anjabom/cam_testbed.git ~/cam_testbed
cd ~/cam_testbed
cp local.yaml.example local.yaml
```

**`local.yaml` 의 가중치 경로를 이 머신에 맞게 고친다.** `.example` 에 적힌 것은 남의
머신 경로다. 안 고치면 `doctor` 가 잡아낸다.

### 1-2. 플러그인으로 설치한다

```
/plugin marketplace add ~/cam_testbed          # 로컬에서 (또는 레포 URL)
/plugin install cam-test@cam-testbed
```

**왜 프로젝트 스킬(`.claude/skills/`)이 아니라 플러그인인가** — 프로젝트 스킬은 그
디렉터리 안에서만 뜬다. 우리는 `~/white_vote_ws` 에서 부르고 싶으니 플러그인이라야 한다.
플러그인으로 설치하면 `${CLAUDE_PLUGIN_ROOT}` 로 테스트베드 위치가 잡히므로 **어디서
부르든 경로를 알려 줄 필요가 없다.**

### 1-3. 확인

```
~/아무_워크스페이스$ claude
> cam-test 스킬 있어?
```

없다고 하면 `/plugin` 으로 설치 상태를 본다.

---

## 2. 첫 실행 — 이미 계약이 있는 워크스페이스

계약이 이미 있는지는 이렇게 확인된다(스킬이 알아서 한다).

```bash
grep -l "workspace:.*$(pwd)" ~/cam_testbed/contracts/*.yaml
```

있으면 바로 말만 하면 된다.

```
> 이 워크스페이스를 ~/video/night_b.mp4 로 테스트해줘
```

스킬이 밟는 순서와 그때 실제로 나가는 명령:

| 단계 | 하는 일 | 명령 |
|---|---|---|
| 0 | 테스트베드·ROS 확인 | `source /opt/ros/humble/setup.bash` |
| 1 | 계약 점검 | `tb.run doctor --contract contracts/<계약>.yaml` |
| 2 | 영상을 논리 이름으로 등록 | `config.set_video('<이름>', '<경로>')` |
| 2 | 시나리오를 본 떠서 생성 | `config.clone_scenario('<본>.yaml', …)` |
| 2 | 실행 전 차단 확인 | `config.resolve_scenario('<새>.yaml')` → `block` 이 비어야 함 |
| 3 | 실행 | `tb.run run --scenario scenarios/<새>.yaml` |
| 4 | 워크스페이스로 내보내기 | `skills/cam-test/export.py runs/<런> --ws <워크스페이스>` |
| 5 | 피드백 | `tb.run feedback runs/<런>` |

**왜 영상을 논리 이름으로 등록하는가** — 시나리오 파일에 절대경로를 박으면 다른 머신에서
안 돌아간다. 경로는 `local.yaml`(머신별, git 제외)에만 둔다. 이 규칙 하나 때문에 시나리오를
남에게 그대로 줄 수 있다.

**왜 본을 떠 오는가** — 기존 시나리오에는 판정 20여 개와 **그 기준을 왜 그 숫자로 잡았는지
적은 주석**이 들어 있다. `clone_scenario` 는 텍스트를 복사하고 바뀌는 줄(`name`·`video`·
구간·모드)만 갈아 끼우므로 그 주석이 전부 남는다.

---

## 3. 새 워크스페이스 붙이기

계약이 없으면 새로 붙이는 것이다. **이게 이 스킬의 진짜 값어치다** — 이 부분이 원래
사람이 대상 소스를 뒤져 가며 손으로 하던 일이다.

```
> 이 워크스페이스에 테스트베드를 붙여줘
```

스킬이 [attach.md](attach.md) 를 따라 이렇게 한다:

1. `config.new_contract()` 로 빈 계약을 만든다 (노드·토픽 자리는 `TODO`)
2. **대상이 돌고 있으면** `tb.discover` 로 실제 메시지에서 토픽·타입·필드 배치를 읽는다
3. **못 띄우면** `src/` 의 `setup.py`·`create_subscription`·`declare_parameter` 를 읽어 채운다
4. `tb.run doctor` 로 검증
5. **사용자에게 보여 주고 확인받은 뒤에 저장한다**

### ★반드시 사람이 확인해야 하는 셋★

계약이 틀리면 **리포트는 초록인데 잰 값이 엉뚱하다.** 로그를 뒤져야만 드러나는 거짓말이라,
스킬은 이 셋을 짚어서 물어보게 돼 있다.

| 항목 | 틀리면 |
|---|---|
| `sync_topic` | 프레임당 정확히 한 번 나가야 한다. 아니면 lockstep 이 어긋나 정렬이 깨진다 |
| `signals[].path` | 값의 위치와 **단위**(도인지 라디안인지)가 틀리면 모든 판정이 틀린다 |
| `nodes[].params` | 창 띄우기·실차 녹화를 안 끄면 헤드리스에서 죽거나 `~/records` 를 오염시킨다 |

### 포맷이 바뀌어도 안 깨지게 쓰는 법

`path:` 는 **후보 리스트**다. 앞에서부터 처음 맞는 것을 쓴다.

```yaml
theta_deg: { topic: /lane_metrics, path: [theta_lane_deg, "data[2]"] }
#                                         ^신 포맷        ^구 포맷
```

메시지가 커스텀 msg 로 바뀌는 마이그레이션 중에도 회귀 비교가 안 끊긴다. 어느 쪽이 실제로
맞았는지는 리포트의 「계약 정합」이 매번 알려 준다:

- `ok` — 첫 경로로 맞음 (계약과 코드 일치)
- `🔁 fallback` — 뒤쪽 경로로 맞음 (마이그레이션 중, 정상)
- `❌ drift` — 메시지는 왔는데 어느 경로도 안 맞음 → **계약의 그 한 줄만 고친다**
- `· silent` — 그 토픽에 메시지가 한 번도 안 옴

---

## 4. 결과는 어디에 남는가

```
<워크스페이스>/testbed_results/
├── COLCON_IGNORE              ← 자동. 없으면 colcon 이 mp4 를 매 빌드마다 훑는다
├── INDEX.md                   ← 런 한 줄씩 쌓이는 시험 이력
└── 0902_183512_skilltest_base/
    ├── report.md              사람이 읽는 리포트 (표·계약 정합·체크)
    ├── summary.json           ★판정의 원본★ checks[].ok 가 여기 있다
    ├── signals.csv            프레임별 신호 (그래프·재분석용)
    ├── lane_debug.mp4         ★디버그 영상★ 노드가 그때 무엇을 봤는가
    ├── debug_meta.json        그 영상의 프레임↔원본 대응표
    ├── params_actual.yaml     실제로 노드에 들어간 파라미터 전부
    ├── code.json              그때 대상 소스의 내용 해시 (재현용)
    ├── compare.md             기준과 비교했으면
    ├── feedback.md            피드백을 뽑았으면
    └── run_env.json           ★이 숫자가 어느 조건에서 나왔는가★
```

`.gitignore` 에 `testbed_results/` 한 줄도 자동으로 등록된다(대상이 git 저장소일 때).
**영상이 대상 저장소에 커밋되면 되돌리기 어렵다.**

원본은 `~/cam_testbed/runs/` 에 그대로 남는다. 재해석·재실행·웹앱 열람은 원본으로 한다 —
`testbed_results/` 는 **보관본**이다.

### `run_env.json` 을 왜 넣었는가

**베이스라인은 머신을 넘지 못한다.** GPU·가중치·영상이 다르면 값이 달라진다.
결과를 워크스페이스(= 남에게 넘어가는 곳)에 넣는 순간 남의 머신 숫자와 한자리에 섞인다.
나중에 "왜 값이 다르지"를 물을 때 답할 수 있게 최소한만 박아 둔다:

```json
{ "host": "asus",
  "gpu": "NVIDIA GeForce RTX 4060 Laptop GPU, 580.173.02",
  "workspace_code_sha": "241c9823c7b2",     ← 대상 소스 내용 해시
  "testbed_git": "3d1f651",
  "weights": { "perception.lane_weights_roi": "…/best.engine" } }
```

가중치가 `.pt` 냐 `.engine` 이냐로 **θ 가 30° 갈린 적이 있다.** 그래서 가중치 경로를
따로 뽑아 둔다.

---

## 5. 결과를 읽는 순서 — 지켜야 한다

### ① 「행 N」을 체크 통과율보다 먼저 본다

**행이 0 인데 「13/13 통과」로 찍힌다.** 잰 것이 없으면 위반할 것도 없기 때문이다.
`export.py` 가 이 경우 경고를 찍는다:

```
행 0  유효 —  체크 13/13
⚠️ 행이나 체크가 0 이다 — 「전부 통과」로 보여도 잰 것이 없다
```

행이 0 이면 십중팔구 계약의 토픽명이 틀렸거나 노드가 기동 중에 죽은 것이다.
`report.md` 의 「계약 정합」이 전부 `· silent` 인지 먼저 본다.

### ② 「계약 정합」을 본다

`❌ drift` 가 있으면 **숫자를 논하기 전에** 계약의 그 한 줄을 고치고
`tb.run reanalyze <런>` 으로 다시 읽는다. `raw.jsonl` 에 원본 메시지가 그대로 남아 있어서
**과거 런을 새 계약으로 다시 해석할 수 있다** — 베이스라인을 버리지 않아도 된다.

### ③ 그다음 체크와 회귀 비교

---

## 6. 회귀 — 고치고 다시 돌리는 고리

```
> 이 런을 기준으로 등록해줘
> (코드 수정)
> 같은 시나리오로 다시 돌려서 기준과 비교해줘
```

```bash
python3 -m tb.run baseline runs/<런>            # 기준 등록
python3 -m tb.run run --scenario … --baseline <이름>   # 다음 런은 자동 비교
```

기준 이름은 **시나리오의 `name:`** 을 따른다. `--tag` 는 런 폴더 이름을 구분할 뿐
비교 대상을 바꾸지 않는다.

**새 머신에서는 기준을 다시 등록한다.** `baselines/<이름>.json` 의 출처와 실행 조건이
다르면 `compare.md` 에 경고가 붙는다.

---

## 7. 피드백

```bash
python3 -m tb.run feedback runs/<런> [--vs runs/<이전런>] [--note "사람이 본 것"]
```

`report.md` 는 "무엇을 쟀나" 순이라 그대로 주면 무엇부터 고칠지가 안 보인다.
`feedback.md` 는 **"무엇을 고쳐야 하나" 순**이다:

```
0. 결론   1. 실행 조건   2. 잘된 점   3. 안 좋은 점(심각도순)
4. 병목과 볼 곳   5. 참고 수치   6. 개선 전/후   7. 사람 메모   8. 요청
```

**판정을 다시 하지 않는다** — 값·기준·통과 여부는 전부 `summary.json` 에서 그대로 옮긴다.
정렬에 쓰는 '초과율'만 계산한다.

스킬은 이걸 읽고 **진단과 제안까지** 한다 — 원인 후보, 볼 파일, 고칠 방향.
**대상 워크스페이스 코드는 사용자가 따로 지시할 때만 고친다.** 시험 도구가 피시험체를
자기 마음대로 고치면 시험이 아니게 된다.

---

## 8. ★절대 하지 않는 것★

이건 취향이 아니라 이 테스트베드가 성립하는 조건이다. 스킬에도 박혀 있다.

### 체크를 통과시키려고 임계값을 느슨하게 하지 않는다

기준이 이 차량·영상에 안 맞는다고 판단되면 **근거를 먼저 말하고 수정을 제안**한다.
**재는 쪽**(`tb/analyze.py`·계약의 `path`)을 고쳐 숫자를 좋게 만드는 것도 같은 금지다.

### `tb/*.py` 와 `web/*` 에 대상 워크스페이스의 이름을 쓰지 않는다

토픽명·필드 배치·노드명·파라미터명 — 전부 계약 YAML 에만 있다. 이 경계가 무너지면
"워크스페이스가 바뀌어도 테스트베드는 안 바뀐다"는 설계 목표가 통째로 깨진다.

### 시나리오에 절대경로를 박지 않는다

영상은 `local.yaml` 의 `videos:` 를 거친다. 절대경로가 박히는 곳은 셋뿐이다 —
계약의 `workspace:`, `local.yaml`, 등록된 베이스라인.

### 외부 의존성을 추가하지 않는다

테스트베드와 웹앱은 **표준 라이브러리만** 쓴다(CDN·웹폰트·JS 라이브러리 없음).
대회 현장에서 네트워크 없이 돌아야 한다.

---

## 9. 함정 모음 (데인 것들)

| 증상 | 실제 원인 |
|---|---|
| 행 0 인데 「전부 통과」 | 잰 게 없다. 계약 토픽명 또는 노드 기동 실패 |
| 브라우저에서 영상이 **검은 화면**(오류는 없음) | `cv2.VideoWriter` 기본 코덱 `mp4v` 는 브라우저가 못 연다. 영상은 `tb/encode.py` 를 거쳐야 한다 |
| 같은 코드인데 값이 다르다 | 가중치 백엔드(`.pt` ↔ `.engine`)가 다르면 θ 가 30° 갈린다. `run_env.json` 의 `weights` 를 대조 |
| `realtime` 시나리오만 결과가 다르다 | `lockstep` 은 벽시계가 실제로 흐른다. 대상이 `time.time()` 으로 hold·staleness 를 재면 30fps 실차와 다르게 동작한다 |
| 옛 런의 디버그 영상이 없다 | 영상은 **실행 중에만** 잡힌다. `tb.run replay <런>` 으로 그때 설정 그대로 다시 돌린다 |
| 파이썬만 고쳤는데 "빌드가 낡았다" 경고 | `--symlink-install` 이라 파이썬은 재빌드 불필요. mtime 비교라 뜨는 경고다. 무시해도 된다 |
| 다른 ROS 세션과 섞인다 | 런마다 임의의 `ROS_DOMAIN_ID` 를 쓴다. 일부러 그런 것 |

---

## 10. 스킬 밖에서 직접 치는 것

스킬이 못 하거나, 스킬 없이도 돌아야 하는 것들이다.

```bash
cd ~/cam_testbed
python3 -m tb.run app                      # 웹앱 — 실행·비교·프레임 탐색이 전부 여기 있다
python3 -m tb.selftest                     # 자체 검사 (ROS·영상 불필요)
python3 -m flake8 tb web                   # 린트
python3 skills/cam-test/export.py --selftest   # 내보내기 자체 검사
```

**화면으로 보고 싶으면 웹앱이 낫다.** 스킬은 "말로 시키고 결과를 워크스페이스에 남기는"
길이고, 웹앱은 "눈으로 보고 프레임을 뒤지는" 길이다. 둘은 같은 `tb.run` 을 부른다.

---

## 11. 더 읽을 것

| 무엇 | 어디 |
|---|---|
| 전체 설계·절차 (§ 번호로 참조) | `~/cam_testbed/README.md` |
| 경계 규칙·판정 어휘 | `~/cam_testbed/CLAUDE.md` |
| Claude 가 따르는 절차서 | [SKILL.md](SKILL.md) |
| 새 워크스페이스 붙이기 | [attach.md](attach.md) |
| 계약 실例 (주석이 시험 근거 문서다) | `contracts/white_camera.yaml` |
| 노드를 안 띄우고 붙는 예 | `contracts/demo_foreign.yaml` |
