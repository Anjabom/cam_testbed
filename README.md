# cam_testbed — ROS 2 카메라 인지를 밖에서 시험한다

대상 워크스페이스를 **import 하지 않는다.** `ros2 run` 서브프로세스로 띄우고 DDS 로만
대화한다. 그래서 대상 코드가 어떻게 바뀌든 이 저장소의 `tb/*.py` 는 바뀌지 않는다.

**한 저장소에 도구가 둘 있다.**

| 무엇 | 어떻게 쓰나 | 무엇이 나오나 |
|---|---|---|
| **시뮬레이터** `tb/` | 명령 한 줄, 또는 클로드 스킬에게 말로 | 계측 리포트 · CSV · **디버그 영상** |
| **보정 스튜디오** `docs/` | 정적 페이지 한 장 (github.io · 서버 없음) | 맞춘 카메라 파라미터 |

---

## 0. 30초 요약

```bash
source /opt/ros/humble/setup.bash
cp local.yaml.example local.yaml       # 최초 1회 — 이 머신의 가중치 경로를 적는다

# ── 시뮬레이션 ──
python3 -m tb.run doctor --contract contracts/black_vote.yaml --video /abs/night.mp4
python3 -m tb.run run    --contract contracts/black_vote.yaml --video /abs/night.mp4 \
        --start 300 --limit 900 --note "정지선에서 제때 서는지" --out ~/black_ws

# ── 카메라 보정 ──
#   설치도 서버도 없다. 어느 기기에서든 링크만 열면 된다:
#     https://anjabom.github.io/cam_testbed/
#   (저장소를 클론했다면 docs/index.html 을 그냥 열어도 똑같이 돈다)
```

결과는 `~/black_ws/testbed_results/<런>/` 에 리포트·CSV·**디버그 영상**·실행 조건으로 남는다.
원본은 `runs/<런>/` 에 그대로 있다.

---

## 1. ★판정하지 않는다★

이 도구는 **재고, 그리고, 남긴다.** 합격/불합격을 찍지 않는다.

2026-09 이전에는 시나리오 YAML 의 `checks:` 가 임계값으로 판정하고, `feedback.md` 가
개선 요청문을 만들고, `baselines/` 가 회귀 기준을 들고 있었다. 전부 없앴다.

1. **임계값의 근거가 없었다.** 대부분 "첫 런에서 이 값이 나왔으니 이걸 기준으로"에서
   출발한 것이라, 실제로는 「지난번과 같은가」를 「좋은가」로 부르고 있었다.
2. **초록 한 줄이 나머지를 안 읽게 만들었다.** 행이 0 인 런도 「13/13 통과」로 찍혔다 —
   위반할 것이 없으면 위반도 없기 때문이다.

지금 나오는 것은 전부 **관측값**이다. 좋은지 나쁜지는 그것을 읽는 사람(과 클로드)이 말한다.
「계약 정합」만은 남았는데, 그건 판정이 아니라 **잰 자리가 맞는가**이기 때문이다.

---

## 2. 계약 — 대상을 아는 유일한 통로

**워크스페이스 1개 = 계약 1개.** 새 워크스페이스를 붙일 때 테스트베드를 복사하지 않고
`contracts/` 에 파일 하나를 더 만든다. 절차는 `skills/cam-test/attach.md`.

```yaml
version: 1
name: black_vote
workspace: /home/mad2/black_ws          # 이곳의 install/setup.bash 를 source 한다

nodes:                                   # import 하지 않고 `ros2 run` 으로 띄운다
  - id: perception
    package: black
    executable: perception
    node_name: perception_node
    params: {show_window: false, record_video: false}
    require_params:                      # ★실차와 같은 조건을 실행 전에 강제★
      lane_weights_roi: {endswith: .engine, exists: true,
                         why: "실차가 TensorRT 로 돈다 — .pt 로 재면 다른 것을 잰다"}

stimulus:
  image_topic: /image_raw                # 영상을 밀어 넣을 곳
  aux: [...]                             # 그 노드가 구독하는데 아무도 안 보내는 토픽

sync_topic: /lane/state                  # 프레임 처리가 끝날 때 한 번 나가는 토픽

signals:
  theta_deg: {topic: /lane_metrics, path: [theta_lane_deg, "data[2]"]}
  #                                        ^신 포맷        ^구 포맷
```

### `path:` 는 후보 리스트다

앞에서부터 처음 맞는 것을 쓴다. 메시지 포맷이 바뀌면 `tb/` 가 아니라 **계약의 그 한 줄**을
고친다. 어느 후보가 맞았는지는 리포트의 「계약 정합」 표가 알려 준다:

| 표시 | 뜻 | 할 일 |
|---|---|---|
| `ok` | 첫 후보로 잡혔다 | — |
| `🔁 대체경로` | 두 번째 이후 후보로 잡혔다 | 정상(마이그레이션 중) |
| `❌ 불일치` | 메시지는 왔는데 어느 경로도 안 맞았다 | **계약의 그 `path:`** 를 고치고 `reanalyze` |
| `· 미수신` | 그 토픽에 메시지가 한 번도 안 왔다 | 발행 조건이 안 맞았을 수 있다 |

### 리포트를 쓸모 있게 만드는 세 절

전부 **관측**이지 판정이 아니다.

```yaml
consumers:            # ★가장 쓸모 있는 표★ 「받는 쪽이 실제로 썼는가」
  - id: gps_imu_heading
    label: GPS/IMU 헤딩 보정
    stages:
      - {name: 차선 검출,  where: "int(flags) % 2 == 0"}
      - {name: conf 임계값, where: "conf_eff >= 0.35",
         why: "gps_imu 는 0.35 를 쓴다 — camera_judgment 의 0.15 보다 훨씬 높다"}

events:               # 상태가 바뀌는 순간과 그때 같이 볼 값
  - {signal: brake_level, at: [sl_px, speed], why: "언제 · 정지선까지 몇 px 에서"}

log_events:           # 토픽에 안 나오는 근거 (기동 배너·개입 사유)
  yolo_backend: {node: perception, match: "TensorRT"}

calibration:          # ★이게 있어야 보정 스튜디오에 이 워크스페이스가 뜬다★
  undistort: {size: [1920, 1080], K: [...], D: [...]}
  bev: {w: 640, h: 1000}
  targets:
    ipm_src: {kind: quad, nodes: [perception], param: ipm_src_pts}
```

---

## 3. 시뮬레이션

### 3.1 입력은 셋뿐이다

**계약 + 영상 경로 + 구간.** 시나리오 파일을 만들지 않는다.

```bash
python3 -m tb.run run --contract contracts/x.yaml --video /abs/a.mp4 \
    --start 300 --limit 900 --note "무엇을 보려는가" --out ~/대상_워크스페이스
```

| 인자 | 언제 |
|---|---|
| `--start` `--limit` `--stride` | 구간을 좁힐 때 |
| `--note` | ★항상★ — 「무엇을 보려는가」. 리포트와 결과 폴더에 남는다 |
| `--out` | 결과를 그 워크스페이스의 `testbed_results/` 로 |
| `--mode realtime` | **타이밍**을 볼 때 (기본 `lockstep`) |
| `--param 노드.이름=값` | 기능을 끄고 대조군을 만들 때 |
| `--perturb blur\|dark\|noise` | 강건성 — 같은 장면을 흐리게/어둡게 |
| `--preset presets/x.yaml` | 자주 쓰는 조합. **인자가 언제나 이긴다** |
| `--no-record-debug` | 디버그 영상을 안 남긴다 (기본은 남긴다) |

### 3.2 재생 모드

| 모드 | 무엇 | 언제 |
|---|---|---|
| `lockstep` | 한 프레임 밀고 `sync_topic` 을 기다린다 | **기본.** 머신 속도와 무관하게 같은 결과 |
| `realtime` | 영상 fps × rate 로 민다. 못 따라오면 프레임을 버린다 | 실차 타이밍 재현 |
| `asfast` | 최대 속도로 민다 | 처리량만 볼 때 |

⚠️ `lockstep` 에서도 **벽시계는 실제로 흐른다.** 대상이 `time.time()` 으로 hold·staleness 를
재면 30fps 실차와 다르게 동작한다 — 타이밍을 보려면 `--mode realtime`.

### 3.3 서브커맨드

| 명령 | 무엇 |
|---|---|
| `doctor` | 돌리기 전에 막힐 것을 전부 본다. 아무것도 안 바꾼다 |
| `run` | 영상을 밀어 넣고 잰다 |
| `replay <런>` | 그때 조건 그대로 다시 — **옛 런의 디버그 영상을 얻는 유일한 길** |
| `reanalyze <런>` | 계약을 고친 뒤 `raw.jsonl` 만 다시 읽는다 |
| `diff <런A> <런B>` | 신호별 차이. **판정하지 않는다** |
| `export <런> --out` | 결과 한 벌을 워크스페이스로 (덮어쓴다) |
| `params` | 노드를 한 번 띄워 **노드가 스스로 선언한 값**을 받아 적는다 |
| `build` | 대상 워크스페이스를 colcon build |
| `list` | 프리셋과 최근 런 |
| `verify` | 런의 디버그 영상과 BEV 기하를 대조한다 (스튜디오가 노드와 같은 그림인가) |

### 3.4 한 번의 흐름 (`tb/run.py` 의 `_one_run`)

1. **계약 해석** — `--contract` → 프리셋의 `contract:` → `local.yaml` 의 `default_contract`
   → `contracts/` 에 하나뿐이면 그것. 여러 개인데 지정이 없으면 고르라고 멈춘다.
2. **기동** — 대상 `install/setup.bash` 를 source 한 프리픽스로:
   - `ros2 run` × 계약의 `nodes`
   - `tb.probe` — 계약의 토픽을 **타입을 모른 채** 전부 기록(`raw.jsonl`)
   - `tb.player` — 영상 → `image_topic`, `aux` 퍼블리셔, 프레임 표식
   - `tb.viewer` — 디버그 이미지 토픽을 받아 mp4 로 녹화(헤드리스)
3. **정렬** — 메시지에 스탬프가 없으므로 테스트베드 내부 토픽 `/testbed/frame` 으로 맞춘다.
4. **계측** — `tb.analyze` 가 `raw.jsonl` → `signals.csv` → 통계·게이트 통과율·θ 품질·
   전이·노드 로그 → `report.md` / `summary.json`.
5. **내보내기** — `--out` 이면 `tb.export` 가 결과 한 벌을 복사하고 `README.md`(폴더 설명)·
   `run_env.json`(호스트·GPU·가중치·코드 해시)을 붙인다.

**`raw.jsonl` 에 원본 메시지가 그대로 남는다** — 계약을 고쳐도 과거 런을 버리지 않고
`reanalyze` 로 다시 읽을 수 있다. **이 성질을 깨는 변경은 하지 않는다.**

### 3.5 리포트를 읽는 순서

1. **「행 N」** — 0 이면 그 아래 모든 표는 빈 입력에서 나온 것이다. 노드 로그를 먼저 본다.
2. **「계약 정합」** — 빨간데 숫자를 논하면 안 된다. 잰 자리가 틀린 것이다.
3. **「받는 쪽 게이트 통과율」** — 인지가 좋아도 게이트를 못 넘으면 주행에 한 프레임도
   기여하지 않는다. **최대 병목 단계가 곧 다음에 손댈 곳이다.**
4. 신호 통계 · 단계 전이 · θ 품질 · 노드 로그.
5. 숫자가 이상하면 **디버그 영상**을 본다.

---

## 4. 보정 스튜디오 — ★정적 페이지 하나★

### 4.0 서버가 없다 [2026-09-06]

`https://anjabom.github.io/cam_testbed/` 를 열면 그게 스튜디오다. 설치도, 로그인도,
포트도, 토큰도 없다. **어느 기기에서든 링크만 있으면 열린다.**

예전에는 이 기계에서 파이썬 서버가 돌면서 그림을 그려 JPEG 로 내려보냈다. 그래서
토큰·업로드·폴더 훑기·방화벽이 딸려 왔고, 그 전부가 「사람이 다른 기기 앞에 있다」는
사정 하나를 감당하려는 장치였다. 지금은 브라우저가 직접 계산한다:

| | 예전 (서버) | 지금 (정적) |
|---|---|---|
| 영상 | 이 기계의 파일을 경로로 고르거나 업로드 | **자기 기기의 파일을 직접 연다** — 아무 데도 안 올라간다 |
| 왜곡보정·BEV | 서버가 cv2 로 그려 JPEG | 브라우저가 WebGL 로 — 드래그가 즉시 반응한다 |
| 인증 | 토큰(Basic) | 필요 없다 — 남의 기계에 아무것도 안 보낸다 |
| 저장 | `local.yaml` 을 서버가 고침 | 파일로 내려받는다(`params.yaml` · 설정 JSON) |

**대신 못 하는 것 넷**(서버가 있어야만 되는 일이라 CLI 로 옮겼다):

| 없어진 것 | 대신 |
|---|---|
| 워크스페이스 값 불러오기 | `python3 -m tb.run params` |
| 노드와 대조 | `python3 -m tb.run verify --run <런>` |
| 체스보드로 K/D 실측 | 없앴다 (연 1회 쓰던 기능) |
| 자동 미세조정 | 없앴다 — 지표를 목적함수로 쓰는 탐색은 방어선을 계속 요구했다 |

### 4.1 ★기하가 두 벌이 됐다 — 그래서 대조한다★

이 저장소의 오래된 규칙은 「기하를 JS 에 한 벌 더 쓰지 않는다」였다. 서버가 없어지면서
그 규칙은 성립하지 않는다. 대신 **두 벌이 같음을 기계가 증명한다**:

```bash
python3 tools/bake_reference.py     # cv2 가 낸 정답표를 docs/reference.js 로 굽는다
python3 -m tb.selftest              # t_geom_js — node 로 JS 를 불러 그 표와 맞춘다
```

실측: 새 카메라 행렬 3e-5 · 보정 맵 9e-5px · 끝에서 끝까지 **0.028px**(대조 방법 자체의
잡음 바닥이 0.03px 다 — cv2 가 좌표를 1/32 픽셀로 반올림한다). 일부러 틀려 본 네 가지
(반픽셀 규약 제거 · 왜곡 반복 5→3 · ROI 재측정 생략 · BEV 모서리 off-by-one)는 전부 잡힌다.

페이지도 열릴 때 스스로 같은 대조를 하고, 어긋나면 **붉은 띠**를 띄운다(오른쪽 아래에
`기하 대조 0.028px` 이 초록으로 떠 있으면 맞은 것이다).

그림을 그리는 셰이더(`docs/render.js`)까지 같은지는 따로 잰다:

```bash
google-chrome --headless=new --enable-unsafe-swiftshader --dump-dom \
    tools/shader_check.html | grep 결과      # 브라우저로 그냥 열어도 된다
```

### 4.2 무엇을 맞추나 — `docs/tuning.js` 한 곳이 정한다

계약 4개의 `calibration.targets` 를 **합쳐서 한 벌**로 두었다. 화면은 종류만 알고
파라미터 이름은 모른다(이름은 전부 `tuning.js` 라는 데이터에 있다).

| 항목 | 종류 | 나가는 이름 |
|---|---|---|
| BEV 크기 | `size` | `bev_w` · `bev_h` |
| IPM 사각형 | `quad` | `ipm_src_pts` / `bev_src_pts` |
| 차선 ROI · 신호등 ROI | `rect` | `lane_roi_*` · `tl_roi_*` |
| 픽셀↔미터 | `scale` | `pixel_to_meter_bev` / `bev_px_to_m` |
| 차선폭 | `number` | `lane_width_m` |
| BEV 범퍼행 | `bev_row` | `bev_bumper_y_px` |
| 1·2단 정지 문턱 | `bev_dist` | `sl_brake1_px` · `sl_brake2_px` |

**★핵심 요령★ 사각형의 좌우 변을 차선 위에 올려라.** 지면은 평면이라 그렇게 놓으면
BEV 에서 차선이 정확히 수직으로 선다. 수직이 아니면 사각형이 틀린 것이고, 격자가 그 자다.

### 4.3 값을 들이고 내는 길

기본값은 **자리표시자**다 — 저장소가 공개라 실측 캘리브를 넣지 않는다. 자기 값은
파일로 오간다(«불러오기» / «내보내기»). 처음 한 번은 이 기계의 `local.yaml` 에서 만든다:

```bash
python3 tools/tuning_from_local.py          # → my_tuning.json (git 제외)
```

내보내기는 `params.yaml`(노드 파라미터, `local.yaml` 의 `params:` 에 그대로 붙는다) ·
설정 JSON(다시 불러올 수 있는 한 벌) · 클립보드 복사 셋이다. 편집 중인 값은 그 브라우저의
`localStorage` 에 남아 새로고침해도 살아 있다.

★사각형은 반드시 실수로 나간다★ — 노드가 2026-08-31 자로 기본값을 float 로 바꿔서,
정수로 주면 기동 즉시 `InvalidParameterTypeException` 으로 죽는다.

### 4.4 계약의 `calibration:` 은 왜 남겼나

스튜디오는 더 이상 계약을 읽지 않는다. 그런데 **시험 엔진은 읽는다** — `tb/run.py` 가
런마다 그 이름들로 **실효 캘리브 값**을 기록한다. 지우면 「기하가 바뀐 것」을 「노드가
회귀한 것」으로 잘못 읽게 된다.

---

## 5. 설정 3층

| 층 | 파일 | 무엇 | git |
|---|---|---|---|
| 결합 | `contracts/*.yaml` · `calib/*.yaml` | 토픽·필드·노드·게이트 상수·보정 대상 | ✅ |
| 재생 조건 | 명령줄 인자 · `presets/*.yaml` | 영상 경로·구간·모드·파라미터 | ✅ |
| 머신 | `local.yaml` | 가중치 경로 · params 덮어쓰기 · 최근 영상 · 보정 스냅샷 | ❌ |

절대 경로가 박히는 곳은 셋뿐이다: 계약의 `workspace:`, `local.yaml`, 프리셋의 `video:`.

`tb/config.py` 가 이 파일들을 **주석을 보존하며** 고친다. 읽기와 쓰기는 **같은 경로**를
지나야 한다(`config._local()`) — 갈라지면 쓴 것과 다른 것을 읽는다(자체 검사가 잡는다).

---

## 6. 저장소 구조

```
tb/                     시뮬레이터 (엔진)
  run.py                오케스트레이터 — 서브커맨드가 곧 이 도구의 입구
  player.py             영상 → 이미지 토픽 (lockstep/realtime/asfast · 섭동 · aux)
  probe.py              토픽을 ★타입을 모른 채★ 기록 (raw.jsonl)
  viewer.py             디버그 이미지 → mp4 (헤드리스 녹화 전용)
  analyze.py            계측 — 신호 테이블 · 통계 · 게이트 통과율 · 리포트
  contract.py           계약 로더 (경로식 · 드리프트 판정)
  expr.py               조건식 평가 (AST 화이트리스트 — eval 을 쓰지 않는다)
  geometry.py           BEV 기하 ★노드가 하는 변환을 그대로 재현★
  calibrate.py          보정 계산부 (Calib · verify)
  config.py             설정 읽기·쓰기 (주석 보존)
  export.py             결과 → 대상 워크스페이스
  encode.py             브라우저에서 재생되는 영상 쓰기 (mp4v 금지)
  selftest.py           자체 검사 (ROS·영상 불필요)

docs/                   보정 스튜디오 — ★그대로 github.io 로 나간다★ (외부 의존성 0)
  index.html            화면 뼈대
  geom.js               기하 — cv2 와 같은 값을 내야 한다 (t_geom_js 가 증명)
  render.js             WebGL — 그림. geom.js 와 같은 식을 쓴다
  app.js                조작 — ★기하도 이름도 여기 없다★
  tuning.js             ★맞출 것과 파라미터 이름은 여기에만★
  reference.js          cv2 정답표 (생성물 — 손으로 고치지 않는다)

tools/                  저장소를 재는 도구 (배포물이 아니다)
  bake_reference.py     cv2 정답표 굽기
  geom_check.js         JS ↔ cv2 대조 (자체 검사가 부른다)
  shader_check.html     GPU 셰이더 ↔ geom.js 대조 (브라우저로 연다)
  tuning_from_local.py  local.yaml → 스튜디오가 여는 my_tuning.json

contracts/*.yaml        워크스페이스 결합 (1 워크스페이스 = 1 파일)
calib/*.yaml            워크스페이스 없는 보정 프로필
presets/*.yaml          자주 쓰는 재생 조건
skills/cam-test/        클로드 스킬 (SKILL.md · USAGE.md · attach.md)
runs/                   실행 결과 (git 제외)
```

---

## 7. 함정 모음

| 함정 | 무엇이 일어나나 |
|---|---|
| **행 0 인데 표는 멀쩡** | 노드가 죽어도 리포트는 나온다. 행부터 본다 |
| **`.pt` 로 재기** | 지연·마스크가 실차와 달라지는데 표는 그대로다. `require_params` 가 막는다 |
| **결과를 머신 넘어 비교** | GPU·가중치가 다르면 값이 다르다. `run_env.json` 이 근거다 |
| **`mp4v` 코덱** | 브라우저가 오류 없이 검은 화면만 띄운다. `tb/encode.py` 를 거친다 |
| **`lockstep` 으로 타이밍 판단** | 벽시계가 실제로 흐른다. `--mode realtime` |
| **디버그 영상을 안 남김** | 사후에 만들 수 없다. `replay` 로 다시 잡는 수밖에 없다 |
| **`--name` 을 폴더 이름으로 착각** | 표시용이다. 폴더는 `--tag` 나 프리셋 이름에서 나온다 |
| **파이썬만 고치고 빌드** | `--symlink-install` 이라 불필요. 빌드가 필요한 건 `setup.py`·`package.xml`·C++ 뿐 |

---

## 8. 고칠 때

```bash
python3 -m tb.selftest       # ★먼저 이것★ ROS·영상 불필요. 순수 함수만 검사한다
python3 -m flake8 tb web     # 린트 (max-line-length 100)
```

새 자체 검사는 `tb/selftest.py` 에 `t_*` 함수로 넣으면 자동으로 잡힌다. **"여기가 틀리면
모든 계측이 틀리는"** 것만 대상이다 — 경로식·드리프트·hold·전이·기하·내보내기·프로필 경로.

경계 규칙은 `CLAUDE.md` 에 있다. 요약하면:

1. `tb/*.py` 와 `docs/*` 에 **대상 워크스페이스의 이름을 한 글자도 쓰지 않는다**
   (이름은 계약과 `docs/tuning.js` 라는 **데이터**에만 있다)
2. 포맷이 바뀌면 계약의 `path:` 한 줄을 고친다
3. 기하는 두 벌(`tb.geometry` · `docs/geom.js`)이지만 **대조로 묶여 있다** —
   한쪽을 고치면 `python3 -m tb.selftest` 가 갈라짐을 잡는다
4. 편집 대상은 `docs/tuning.js` 가 정한다 — 화면은 **종류**만 안다
5. 스튜디오는 **아무것도 실행하지 않는다** (서버가 없다 — 정적 파일뿐이다)
6. **재는 쪽을 고쳐 숫자를 좋게 만들지 않는다**
