# 카메라 인지 테스트 플랫폼 (`testbed/`)

원본 카메라 영상을 워크스페이스에 그대로 밀어 넣고, 나오는 토픽을 전부 기록해
**지표 · 불변식 · 회귀 비교**로 판정한다.

핵심 설계 목표는 하나다:

> **워크스페이스 코드가 바뀌어도 테스트 플랫폼 코드는 바뀌지 않는다.**

---

## 1. 어떻게 결합을 끊었는가

테스트 도구가 대상 코드에 묶이는 경로는 딱 네 가지뿐이다. 넷 다 코드 밖으로 뺐다.

| 결합 경로 | 흔한 방식 | 여기서는 |
|---|---|---|
| ① 파이썬 import | `from white.perception import LaneLine` | **하지 않는다.** `ros2 run` 서브프로세스로 띄우고 DDS 로만 대화 |
| ② 토픽 이름 | 코드에 `"/lane_metrics"` 하드코딩 | `contracts/*.yaml` 에만 존재 |
| ③ 메시지 타입 | `from std_msgs.msg import Float32MultiArray` | 런타임에 광고된 타입 문자열을 `get_message()` 로 되살림 |
| ④ 필드 배치 | 코드에 `msg.data[2]` 하드코딩 | 계약의 `path:` 경로식. 후보를 여러 개 둘 수 있음 |

그래서 **`tb/*.py` 안에는 white 패키지의 토픽명·필드명·노드명이 한 글자도 없다.**
전부 `contracts/white_camera.yaml` 한 파일에 있다.

### 포맷이 바뀌어도 안 깨지는 이유

계약의 `path:` 는 후보 목록이고, 앞에서부터 처음 맞는 것을 쓴다.

```yaml
theta_deg: { topic: /lane_metrics, path: [theta_lane_deg, "data[2]"] }
#                                         ^신 포맷        ^구 포맷
```

핸드오프 문서의 T-02(스탬프 추가) · T-04(θ 접선값 추가)처럼
**메시지가 커지거나 커스텀 msg 로 바뀌는 마이그레이션 중에도 회귀 비교가 끊기지 않는다.**
어느 쪽이 실제로 맞았는지는 리포트의 「계약 정합」 표가 매번 알려준다:

- `ok` — 첫 경로로 맞음(계약과 코드 일치)
- `🔁 fallback` — 뒤쪽 경로로 맞음(마이그레이션 중, 동작 정상)
- `❌ drift` — 메시지는 왔는데 어느 경로도 안 맞음 → **계약의 그 한 줄만 고친다**
- `· silent` — 그 토픽에 메시지가 한 번도 안 옴

노드를 추가하든 토픽을 늘리든 계약 YAML 에 줄을 더할 뿐, `tb/` 는 손대지 않는다.

---

## 2. 구성

```
~/cam_testbed/                     ← 워크스페이스 밖. 대상은 계약으로만 안다
├── contracts/                     ★유일한 결합점★ — 워크스페이스 1개 = 계약 1개
│   ├── white_camera.yaml          이 워크스페이스
│   └── demo_foreign.yaml          다른 시스템에 붙이는 예시(attach 모드)
├── scenarios/                     무엇을 어떻게 돌릴 것인가
│   ├── regression.yaml            회귀(golden) — 재현성 최우선
│   ├── robustness.yaml            섭동 대조 — 강건성/메타모픽
│   └── realtime.yaml              실차 타이밍 재현 — 처리율/지연
├── local.yaml                     ★머신 로컬★ 영상·가중치 실제 경로 (버전관리 제외)
├── .flake8                        린트 설정 (자기 것을 들고 다닌다)
├── fastdds_profile.xml            1080p 무손실 전송용 DDS 설정 (아래 §10)
├── web/                           로컬 웹앱 (표준 라이브러리만 — 의존성 0)
│   ├── server.py                  runs/ 를 읽어 JSON 으로 · 작업 실행 API
│   ├── index.html · app.js        단일 페이지 · 해시 라우팅
│   ├── plot.js                    캔버스 시계열 (라이브러리 없음)
│   └── style.css
├── tb/
│   ├── render.py                  ★경로 생성 시각화★ — 판정에 쓴 값으로 다시 그림
│   ├── harvest.py                 조건에 맞는 프레임을 원본에서 추출
│   ├── selftest.py                테스트베드 자체 검사 (ROS·영상 불필요)
│   ├── inject.py                  합성 신호 주입 — 변환 수학 격리 검증
│   ├── expr.py                    계약 조건식의 안전한 평가기
│   ├── calibrate.py               영상으로 BEV ROI·px2m·ROI 맞추기
│   ├── geometry.py                BEV 변환 재현 + 수직도 측정
│   ├── discover.py                돌고 있는 시스템 → 계약 초안 자동 생성
│   ├── viewer.py                  디버그 영상 뷰어 (정지·단계·스냅샷·mp4)
│   ├── contract.py                계약 로딩 + 경로식 평가 + 드리프트 판정
│   ├── player.py                  영상 → 이미지 토픽 (lockstep/realtime/섭동)
│   ├── probe.py                   계약의 토픽을 타입 불문하고 기록
│   ├── analyze.py                 지표 · 불변식 · 회귀비교 · 리포트
│   └── run.py                     오케스트레이션 CLI
├── baselines/                     회귀 기준 CSV
└── runs/                          실행 결과
```

`COLCON_IGNORE` 가 있어 colcon 빌드 대상이 아니다. 워크스페이스는
`--symlink-install` 로 빌드돼 있으므로 **소스를 고치면 재빌드 없이 바로 다음 런에 반영된다.**

**테스트베드는 워크스페이스 밖에 있다.** 지금 위치는 `~/cam_testbed/` 이고
대상(`~/white_cam_ws`)과는 계약의 `workspace:` 한 줄로만 이어져 있다. 워크스페이스를
더 붙일 때도 **복사하지 않고 계약만 하나 더 만든다.** 자세한 건 §11.

---

## 3. 사용법

```bash
source /opt/ros/humble/setup.bash
cd ~/cam_testbed
cp local.yaml.example local.yaml     # 최초 1회: 가중치·영상 경로를 이 머신에 맞춘다

python3 -m tb.selftest                                     # 테스트베드 자체 검사
python3 -m tb.run doctor                                   # 환경/계약 점검
python3 -m tb.run run --scenario scenarios/regression.yaml # 실행
python3 -m tb.run baseline <런디렉토리명> --name regression # 기준으로 등록
# 코드를 고친 뒤 다시:
python3 -m tb.run run --scenario scenarios/regression.yaml # 기준과 자동 비교
python3 -m tb.run list
python3 -m tb.run compare <기준> <런>          # 임의의 두 결과 비교
python3 -m tb.run reanalyze <런>               # 계약 수정 후 재해석 (재실행 불필요)
python3 -m tb.run feedback <런> --vs <이전 런> # 결과 → 코드 개선 요청문 (feedback.md)
```

`tb.selftest` 는 ROS 도 영상도 없이 도는 순수 함수 검사다. 경로식 평가·드리프트 판정·
상태유지·시퀀스 비교처럼 **여기가 틀리면 모든 판정이 틀리는** 부분만 본다.
계약 문법을 손볼 때 먼저 돌린다.

처음 쓰는 머신이라면 `cp` 한 줄로 끝나지 않는다 — 계약의 `workspace:` 와 베이스라인까지
손봐야 한다. 이식 절차 전체는 **§11.5**.

`reanalyze` 가 중요하다. 워크스페이스의 메시지 배치가 바뀌어 계약의 `path:` 를 고쳤을 때,
**과거 런들을 새 계약으로 다시 해석**할 수 있다. `raw.jsonl` 에 원본 메시지가 그대로
남아 있기 때문이다. 즉 계약이 바뀌어도 **베이스라인을 버리지 않아도 된다.**

결과는 `runs/<시각>_<태그>_<변형>/` 에 남는다:

| 파일 | 내용 |
|---|---|
| `raw.jsonl` | 관찰 토픽의 모든 메시지 원본 (프레임 번호·수신시각 포함) |
| `signals.csv` | 프레임별 신호 테이블 — 외부 분석 도구에 그대로 물릴 수 있다 |
| `report.md` | 지표 · 판정 · 플래그 발생률 · 계약 정합 |
| `compare.md` | 베이스라인 대비 회귀 비교 (기준이 등록돼 있을 때) |
| `summary.json` | 위 전부의 기계 판독용 |
| `feedback.md` | 코딩 에이전트에게 그대로 넘기는 개선 요청문 (`tb.run feedback` 이 만든다) |
| `*.log`, `cmd_*.txt` | 각 노드의 stdout 과 실제 실행된 명령줄 |

---

## 3.5 웹앱 — 모든 기능이 여기 있다

```bash
python3 -m tb.run web            # http://127.0.0.1:8770
python3 -m tb.run web --open     # 브라우저도 함께
```

**외부 의존성 0 · 인터넷 불필요.** 표준 라이브러리만 쓰고 CDN·웹폰트·JS 라이브러리를
하나도 안 씁니다. 대회 현장에서 네트워크가 없어도 그대로 돕니다.
원격에서 볼 때만 SSH 포트포워딩(`ssh -L 8770:localhost:8770 …`).

| 화면 | 하는 일 |
|---|---|
| **홈** | 첫 화면은 목록이 아니라 **기능 고르기**다. 카드 하나가 탭 하나 |
| **실행 기록** | 판정·기여율·θ 편향·회귀를 한 줄에. **검색·필터·정렬**, 체크박스로 **고정(★)·메모·태그·삭제** |
| **└ 휴지통** | 삭제한 실행은 `runs/_trash/` 로 옮겨질 뿐이다. 복원하거나 거기서 완전히 지운다 |
| **실행 상세** | **탭 5개** — 요약(게이트 통과율·θ 품질·판정·작업버튼) / 시각화(**경로 영상 재생** + 시계열, 양방향 동기) / 상세(플래그·신호통계·계약정합) / 원문(회귀비교·리포트) / **피드백**(코드 개선 요청문) |
| **프레임 탐색** | 조건으로 걸러 **표**로. 행을 누르면 프레임 뷰어. 프리셋 8개 · 추출 버튼 |
| **프레임 뷰어** | 경로를 크게. ← → 는 **방금 거른 목록 안에서만** 이동 |
| **실행** | 시나리오를 고르면 **그게 실제로 쓸 계약·워크스페이스·영상 파일·구간·파라미터를 풀어서** 보여 준다. ⛔ 가 있으면 버튼이 잠긴다. 실행/주입검증/점검/중지 · 진행률·라이브 화면·로그 |
| **└ 등록** | 실행 화면 아래 접이식 — **영상**(논리 이름 ↔ 실제 경로, 등록할 때 열어 보고 프레임 수 확인) · **워크스페이스**(계약 만들기/경로 수정) · **시나리오 만들기**. `local.yaml` 의 **주석을 보존**하며 고친다 |
| **결과 비교** | 기준 또는 두 실행을 골라 회귀 비교 |
| **카메라 보정** | 원본 클릭으로 IPM 4점 이동, BEV 실시간 미리보기, **수직도** 표시, **측정 모드**(BEV 두 점 → px2m), 붙일 YAML 생성 |
| **기준 관리** | 베이스라인과 출처(영상·구간·모드) |
| **환경 점검** | doctor · selftest · discover |
| **사용 안내** | 워크스페이스·영상 고르기부터 결과 판단까지. 화면에 나오는 말을 모은 **단어집**(42항목) 포함 — 오프라인에서도 보인다 |

탭 이름은 화면이 하는 일 그대로 쓴다(런/캘리브 같은 은어를 안 쓴다). URL 과
디렉터리 이름(`runs/<시각>_<태그>_<변형>`)은 그대로라 기존 링크·CLI 는 안 깨진다.

### 피드백 — 결과를 코드 개선으로 되먹인다

실행 상세의 **피드백** 탭(또는 `python3 -m tb.run feedback <런>`)이 그 실행의
`summary.json` 을 읽어 **코딩 에이전트에게 그대로 줄 수 있는 문서**를 만든다.
`report.md` 와 재료는 같지만 순서가 다르다 — report 는 "무엇을 쟀나" 순이고,
`feedback.md` 는 **"무엇을 고쳐야 하나"** 순이다.

```
0. 결론 먼저      실패한 체크 수, 가장 급한 것, 실질 기여율, 회귀 판정
1. 실행 조건      워크스페이스 경로·계약·영상·구간·모드·코드 지문 (재현에 필요한 전부)
2. 잘된 점        통과한 체크와 그 기준 — "이걸 깨뜨리면 개선이 아니다"
3. 안 좋은 점     실패한 체크를 ★기준을 벗어난 정도 순★ 으로. 왜 문제인지와 볼 곳
4. 병목           게이트 단계별 통과율·탈락 수·그 단계가 쓰는 상수와 소스 파일
5. 참고 수치      플래그 발생률·θ 품질·지연·계약 정합 이상
6. 개선 전/후     --vs 를 주면: 좋아진 것 / 나빠진 것 / 기여율 변화
7. 사람 메모      웹에서 적은 관찰 (선택)
8. 요청           고칠 범위와 규칙, 그리고 검증 명령
```

마지막 절의 **규칙**이 핵심이다. 자동 생성된 문서는 이렇게 못을 박는다:

- 고칠 것은 **워크스페이스 코드**다. 재는 쪽(테스트베드)을 고쳐 숫자를 좋게 만들지 않는다.
- **임계값을 느슨하게 해서 체크를 통과시키지 않는다.** 값이 아니라 원인을 고친다.
  기준 자체가 이 차량·영상에 안 맞는다고 판단되면 **근거를 먼저 말하고** 시나리오의
  `checks:` 수정을 제안한다 — 말없이 바꾸지 않는다.
- 2절에서 통과한 체크를 깨지 않는다. 깨졌으면 그것도 보고한다.

```bash
# 결과를 클로드 코드에 넘긴다
claude "$(cat runs/0818_194259_fpsfix_base/feedback.md)"

# 고친 뒤 다시 돌리고, 이전 실행과 무엇이 달라졌는지까지 담아 다시 넘긴다
python3 -m tb.run run --scenario scenarios/regression.yaml --tag fix
python3 -m tb.run feedback <새 런> --vs 0818_194259_fpsfix_base
```

`lockstep` 은 결정적이라 같은 코드면 값이 정확히 같다. 그래서 전/후 표에 뜬 변화는
**전부 코드 변경 탓**이고, 에이전트가 자기 수정의 효과를 스스로 확인할 수 있다.
판정은 여전히 엔진만 한다 — 이 문서는 `summary.json` 의 값을 옮겨 적을 뿐이다.

### 지키는 규칙

- **판정은 엔진이 한다.** 웹앱은 `summary.json` 의 `checks[].ok` 를 색칠할 뿐,
  임계값을 JS 에 한 벌 더 쓰지 않는다.
- **기하도 엔진이 한다.** 캘리브 화면의 워프·수직도는 서버(`tb.geometry`)가 계산한다 —
  대상 노드의 변환을 재현한 코드가 이미 있고, JS 에 다시 쓰면 어긋난다.
- **모든 버튼이 CLI 한 줄에 대응한다.** 화면에 그 명령을 그대로 보여 준다.
- **상태는 파일에 있다.** 웹앱은 `runs/` 를 읽고 `tb.*` 를 부를 뿐 자기 DB 가 없다.
  사람이 붙인 정리 정보(고정·메모·태그)도 `runs/_index.json` **한 파일**에 모이고,
  삭제는 `runs/_trash/` 로 **옮기는 것**이라 결과 파일을 지우지 않는다.
- 실행 API 는 **작업·인자 화이트리스트**로 막고 셸 메타문자를 거부한다.
  정적 파일도 화이트리스트라 서버 소스가 나가지 않는다.
- **영상은 재생 가능한 코덱으로만 내보낸다.** 서버가 코덱을 확인하고 필요하면
  H.264 로 한 번 변환해 캐시한다. 못 하면 415 와 이유를 준다 — 검은 화면으로 두지 않는다.

### 경로 영상

**시각화** 탭의 `경로 영상 만들기` 를 누르면 좌/우 차선·중심선·θ 시컨트·접선이 그려진
mp4 를 만든다(400프레임 약 30초, 진행률 표시). 만들고 나면 일반 영상처럼 재생·스크럽·배속이
되고 **플롯 커서와 양방향으로 동기**된다 — 재생하면 커서가 따라가고, 플롯을 누르면 영상이
그 프레임으로 간다.

```bash
python3 -m tb.run render <런> --mp4 auto              # CLI 로도 같은 것
```

시계열·영상·오버레이는 `frame` 번호로 묶인다. 뷰어가 `debug_meta.json` 에
"영상 0번 = 원본 몇 번 프레임"을 실측해 남기므로 정확하고, 어긋나면 화면에서 ±로 맞춘다.

#### 재생 속도 — 기본은 원본과 같다

`--fps` 를 주지 않으면 **원본 영상과 같은 속도**로 굽는다. 프레임을 솎아 냈으면
(`--limit`) 벽시계 시간이 맞도록 그만큼 올린다:

```
fps = 원본fps × 그린프레임수 ÷ (마지막프레임 − 첫프레임 + 1)
```

> 예전 기본값은 10fps 였다. 30fps 로 찍은 영상이 **1/3 배속**으로 저장돼
> "느린 배속이 걸린 것처럼" 보였다 — 디버그 영상(뷰어)도 15fps 고정이라 절반 속도였다.
> 지금은 둘 다 원본을 따라간다.

**천천히 보고 싶을 때는 다시 굽지 않는다.** 화면의 `배속` 버튼(0.25× · 0.5× · 1× · 2×)이
`playbackRate` 를 바꿀 뿐이라 즉시 반영되고, 프레임↔시간 대응도 그대로다.
저장된 파일의 속도까지 바꾸려면 `--fps` 로 직접 지정한다.

#### 코덱 — 여기서 한 번 크게 데였다

`cv2.VideoWriter` 의 기본 코덱 `mp4v` 는 MPEG-4 Part 2 다. 파일은 멀쩡해서 VLC·mpv 로는
잘 열리지만 **브라우저는 재생하지 못한다** — mp4 컨테이너에서 `<video>` 가 받아 주는 것은
H.264(avc1) 뿐이다. 오류도 안 나고 그냥 검은 화면이 되므로 원인을 찾기 어렵다.

pip 로 받은 opencv 에는 H.264 **인코더**가 없다(libx264 가 GPL 이라 빼고 빌드한다).
그래서 `tb/encode.py` 가 순서대로 내려간다:

| 순위 | 방법 | 조건 |
|---|---|---|
| 1 | 시스템 `ffmpeg` 에 raw 프레임 파이프 → **H.264/mp4** | `ffmpeg` + libx264 |
| 2 | cv2 → **VP9/VP8 webm** | ffmpeg 없음 (브라우저는 OK) |
| 3 | cv2 → mp4v | 마지막 수단, **브라우저 재생 불가** |

- `doctor` 가 `영상 코덱` 항목으로 현재 어디에 해당하는지 알려 준다.
- **이미 만들어 둔 mp4v 영상은 다시 돌릴 필요가 없다** — 웹앱이 처음 요청될 때 한 번만
  H.264 로 변환해 `<이름>__web.mp4` 로 옆에 캐시한다(8.3MB 짜리 기준 0.7초).
- 변환할 수단조차 없으면 서버가 415 와 이유를 주고 화면에 안내가 뜬다. 검은 화면으로
  두지 않는다. 해결은 `sudo apt install ffmpeg`.
- 덤으로 파일이 작아진다 — 같은 화질에서 8.29MB → 3.77MB.

### 프레임 탐색 = 능동 학습 입구

게이트 통과율의 **병목 단계를 그대로 조건으로 쓰면 고쳐야 할 프레임만 모인다.**
표에서 확인하고 `이 조건으로 추출` 버튼(또는 CLI)으로 뽑는다:

```bash
python3 -m tb.run harvest <런> --where "int(flags) % 4 >= 2" --limit 200
```

조건식은 계약과 같은 안전한 평가기(AST 화이트리스트)를 쓴다.

---

## 4. 재생 모드 — 왜 셋인가

| 모드 | 동작 | 쓰는 곳 |
|---|---|---|
| `lockstep` | 한 프레임 밀고 `sync_topic` 이 올 때까지 대기 | **회귀 비교.** 모든 프레임이 정확히 한 번씩 처리 → 머신 속도와 무관하게 같은 결과 |
| `realtime` | 영상 fps 로 밀고 노드가 못 따라오면 유실 | 실차 타이밍 재현. 처리율·지연·게이트 반응 |
| `asfast` | 대기 없음 | 처리량 상한 |

실차는 30fps 입력에 6.5Hz 처리라 프레임을 버린다. 그 **유실 패턴은 머신마다 달라서
회귀 비교의 기준이 될 수 없다.** 그래서 재현성(lockstep)과 타이밍 충실도(realtime)를
분리했다. 두 모드에서 EMA 스무딩(`fit_ema_alpha`)이 보는 프레임 간격이 달라지므로
**두 모드의 값을 서로 비교하면 안 된다** — 각자의 베이스라인을 따로 둔다.

---

## 5. Ground Truth 없이 무엇을 판정하는가

핸드오프 문서의 지적대로 GT 없이는 "정확도"를 못 잰다. 대신 GT 없이도 잴 수 있는
세 가지를 판정 근거로 삼는다.

### (1) 회귀 — 이전 버전이 곧 기준
같은 영상·같은 파라미터에서 값이 달라지면 잡는다. `compare_tol` 로 신호별 허용오차를
준다. 정확도는 못 재도 **"내가 방금 고친 게 무엇을 바꿨는가"는 정확히 잰다.**

**실측 재현성**: 같은 코드로 두 번 독립 실행한 결과가 400프레임 전 구간에서
`cte_rear_m`·`theta_deg`·`conf`·`flags` 모두 **max\|Δ\| = 0.0000** 이었다
(`flags` 는 허용오차 0). 즉 비교에서 뜨는 차이는 전부 코드 변경 탓이지 잡음이 아니다.

신호 종류에 따라 비교 방식이 다르다:

| 종류 | 계약 선언 | 비교 방식 |
|---|---|---|
| 수치 | `compare_signals` | 프레임별 `max\|Δ\|` vs `compare_tol` |
| 이산(프레임 동기) | `compare_categorical` | 프레임별 값 일치 |
| 이산(타이머/변화시 발행) | `compare_sequence` | **값이 바뀐 순서**만 비교 |
| 상태 유지형 | `hold_signals` | 다음 발행까지 앞 값으로 채운 뒤 비교 |

`/judgment_state` 는 `camera_judgment` 가 1 Hz 타이머로도 발행해서 어느 프레임에
실리는지가 벽시계에 달려 있다. 프레임별로 비교하면 매번 거짓 DIFF 가 뜨므로
`compare_sequence` 로 뺐다. **재현되지 않는 것을 재현되지 않는다고 선언하는 것도 계약의 일이다.**

### (2) 불변식 — 진실을 몰라도 성립해야 하는 것
시나리오의 `checks:` 에 선언한다.

```yaml
- {signal: lane_width_m, stat: mean, min: 2.0, max: 4.5, when_valid: true}
- {signal: theta_deg,    stat: p95_abs_diff, max: 8.0}
```

차선폭은 물리량이라 평균이 범위를 벗어나면 `px2m`/IPM 캘리브가 틀린 것이고,
θ 가 프레임마다 8도씩 튀면 IMU 헤딩 보정 관측치로 못 쓴다 — 둘 다 GT 없이 판정된다.

사용 가능한 `stat`: `mean` `std` `min` `max` `p95` `p95_abs_diff` `max_abs_diff`
`frac_nonzero` `frac_zero`, 그리고 신호 없이 쓰는 `drop_rate` `latency_p95_ms`
`flag_rate:<플래그명>`. `when_valid: true` 면 차선 미검출 프레임을 뺀다.

### (3) 섭동 대조 — 강건성
`robustness.yaml` 은 **같은 프레임**에 밝기·블러·JPEG·노이즈·해상도만 바꿔 넣고
기준 대비 얼마나 무너지는지를 표로 낸다. 장면이 같은데 출력이 달라지면 그건
진실을 몰라도 오류다. 노이즈는 고정 시드라 재현된다.

`hflip` 은 **메타모픽 테스트**다: 좌우 반전하면 cte·θ 는 **부호만** 뒤집혀야 한다.
크기까지 달라지면 IPM `src_pts` 가 비대칭이거나 좌/우 차선 처리에 비대칭 버그가 있다.
계약의 `mirror_odd_signals` 에 부호홀수 신호를 선언하면 비교 전에 자동으로 되돌린다.

리포트는 열화를 **두 갈래로 분리**해서 낸다. 이 구분이 중요하다:

- **놓친다** — 차선없음/단독차선으로 떨어진다 (`검출 엇갈림`, `단독차선` 열)
- **틀린다** — 검출은 했는데 값이 어긋난다 (`p95\|Δ\|` 열, **양쪽 다 플래그 0** 인 프레임만)

둘을 안 나누면 `p95\|Δcte\|` 가 섭동 종류와 무관하게 전부 ~1.5 m 로 나온다. 그 1.5 m 는
차선 위치 오차가 아니라 **단독차선 모드로 갈아탄 폭**이라, 아무것도 구분하지 못한다.

**이 워크스페이스에서 실제로 나온 결과** (`track_record.mp4` 200프레임):

| 변형 | 단독차선율 | 검출 엇갈림 | p95\|Δcte\| | p95\|Δθ\| |
|---|---|---|---|---|
| base | 0.005 | — | — | — |
| dark (γ=1.8) | **0.236** | 0.046 | 0.060 m | 0.66° |
| noise | **0.226** | 0.021 | 0.057 m | 0.80° |
| blur | 0.133 | 0.031 | 0.059 m | 0.95° |
| jpeg | 0.000 | 0.010 | 0.079 m | 0.50° |
| hflip 🪞 | 0.021 | 0.026 | 0.104 m | **2.01°** |

읽는 법: **값은 안 틀린다. 놓친다.** 양쪽 다 정상 검출한 프레임에서는 어떤 섭동에서도
cte 오차가 0.11 m 이하로 붙어 있다. 문제는 어두워지면 단독차선 모드 진입률이
0.5% → 23.6% 로 뛰는 것이다. 그리고 단독차선 모드의 `conf` 상한 0.25 는
`camera_judgment` 의 `conf_min` 0.15 를 **통과한다** → 반폭 추정 오차가
그대로 융합 필터로 들어간다(핸드오프 문서 P1-1 이 지적한 바로 그것, 이제 수치가 붙었다).

`hflip` 의 θ 편차 2.01° 는 좌우 반전만으로 생긴 것이므로 **기하 비대칭**을 가리킨다
(IPM `src_pts` 또는 `cam_yaw_offset_deg`). 다른 섭동의 3배다.

### (4) 신호 주입 — 변환 수학을 격리해서 검증

```bash
python3 -m tb.run inject --scenario scenarios/regression.yaml
```

영상도 YOLO 도 쓰지 않는다. `camera_judgment` 만 띄우고 **합성 `/lane/state` 를 직접
쏜다.** 입력을 내가 만들었으므로 참값이 정확히 알려져 있고, 값이 다르면 100% 계산이 틀린 것이다.

영상으로 도는 테스트는 "차선을 봤나"와 "값이 맞나"가 섞여 있어서 값이 틀려도 원인을
가릴 수 없다. 이걸로 **인지와 계산을 분리**한다.

케이스는 `cases/theta_math.yaml` 에 있고, **기댓값은 대상 코드에서 베낀 게 아니라
기하로 유도한 것**이다(케이스마다 도출 근거를 함께 적어 둔다). 베껴 오면 검증이 아니라
동어반복이 된다.

| 케이스 | 검증하는 것 |
|---|---|
| 직선 중앙정렬 | 영점 — cte=0, θ=0 |
| 중심선 ±50px | **부호 규약**과 스케일 (0.3 m) |
| 기울기 ±10°/5° | θ = atan(b) — gps_imu 가 쓰는 유일한 값 |
| 곡선 a=0.0005 | **시컨트 근사의 계통오차** (아래) |
| 폭 1.3 m / 2.4 m | 폭 게이트와 `conf_eff` 억제 |
| 단독차선 | 반폭 추정 경로 |
| 차선없음 | `NO_LANE` 처리 |

현재 **10/10 통과** — 변환 수학·부호 규약·스케일·게이트 로직이 전부 정확하다.

입력에 쓰는 이름(`fit_bL` 등)은 계약의 `signals` 가 배열 위치를 알려주므로
**케이스 파일에 배열 인덱스를 적지 않는다.** 메시지 배치가 바뀌어도 케이스는 그대로다.

> **곡선에서 발견한 것.** θ 는 중심선의 두 점을 잇는 **시컨트** 각이다(접선이 아니다).
> `x = a·y² + b·y + c` 일 때
> 시컨트 `θ = atan(a·(y_near+y_look) + b)`, 접선 `θ = atan(2a·y_near + b)`.
> 계수비가 657.6 : 883.2 = **0.745** 라서 곡선에서 θ 를 약 25% 과소평가한다.
> `a=0.0005` 면 시컨트 18.2° vs 접선 23.8° — **5.6° 차이**.
> 다만 `gps_imu` 는 자이로 게이트로 곡선에서 카메라를 아예 안 쓰므로 실주행 영향은 제한적이다.

### (5) θ 품질 — gps_imu 가 쓰는 유일한 값

`gps_imu` 는 카메라에서 **`theta_lane_deg` 하나만** 쓴다. `cte_rear_m` 은 받아서
디버그 발행에만 쓰고 융합에 넣지 않는다. 그것도 직진 구간에서만, 게인 0.04 × trust 0.2
= 초당 최대 0.2° 로 아주 천천히 누적한다.

그래서 중요한 건 순간 정확도가 아니라 두 가지다:

| 지표 | 뜻 | 왜 |
|---|---|---|
| `theta:abs_bias_deg` | 직진 구간 θ 평균의 절댓값 | 0 이 아니면 **헤딩이 계속 한쪽으로 끌린다** |
| `theta:vibration_frac` | 조향 진동 대역(0.08~0.18 Hz) 파워 비중 | 크면 **카메라가 진동을 만든다** |

두 번째는 과거에 실제로 터진 문제다 — `gps_imu.py` 주석에 따르면 조향 진동의
최대 52% 가 카메라 탓이라 `cam_heading_trust` 를 0.5 → 0.2 로 낮춘 이력이 있다.

계약의 `theta_quality:` 에 선언하고, 직진 판정은 θ 변화율로 근사한다
(테스트베드는 영상만 받으므로 자이로가 없다 — 정확히 하려면 §11 의 촬영 계획 참고).

### (6) GT 를 나중에 붙일 때
`signals.csv` 에 `gt_*` 열을 추가로 조인하고 `checks:` 에 오차 항목을 더하면 된다.
합성 BEV 시뮬(핸드오프 T-07)이나 수동 라벨링은 **이 플랫폼의 대체재가 아니라
`signals.csv` 에 열을 더하는 별도 입력**이다.

---

## 6. 영상을 넣을 때 주의할 점

- **원본 카메라 영상**(`track_record.mp4` 등)에는 `enable_undistort: true`.
- **`perception` 이 남긴 `Raw.webm`** 은 이미 어안 보정을 거친 뒤 프레임이다.
  이걸 먹일 때는 반드시 `enable_undistort: false` 로 내려야 이중 보정이 안 된다.
- `records/*/Lane.webm` 류는 오버레이가 그려진 640px 축소본이라 입력으로 쓸 수 없다.
- 해상도는 1920x1080 을 전제한다(`perception` 의 undistort 맵이 그 크기로 고정).

---

## 7. 파라미터 튜닝 루프

시나리오의 `params:` 는 노드 파라미터를 그대로 덮어쓴다. 런치 파일을 건드리지 않는다.

```yaml
params:
  perception:
    fit_ema_alpha: 0.7
    conf_pix_saturate: 150
```

같은 영상·lockstep 이면 값이 결정적이므로, 파라미터만 바꾼 두 런을
`python3 -m tb.run compare <A> <B>` 로 직접 비교하면 그 파라미터의 순효과가 나온다.

---

## 7.5 경로가 잘 생성됐는지 눈으로 보기

대상 노드도 `/lane/debug` 를 그리지만 그건 **그 노드가 보여 주고 싶은 것**이다.
여기서는 **테스트베드가 판정에 실제로 쓴 숫자**(`signals.csv` 의 폴리핏 계수)로 다시 그린다.
그래서 "리포트의 θ 가 왜 이 값인지"를 그림에서 바로 확인할 수 있다.

```bash
python3 -m tb.run render <런> --frames 1090,850          # 특정 프레임
python3 -m tb.run render <런> --where "int(flags) % 4 >= 2" --limit 20
```

웹앱에서는 **런 상세의 플롯에서 한 점을 누르면** 그 프레임의 경로가 그려지고,
**프레임 탐색**은 썸네일 자체를 오버레이로 보여 준다(`오버레이 켬` 버튼으로 원본과 토글).
프레임 하나를 크게 볼 때는 ← → 로 넘긴다.

| 색 | 무엇 |
|---|---|
| 파랑 / 빨강 | 좌 / 우 차선 폴리핏 `x = a·y² + b·y + c` |
| **주황** | **중심선 = 경로 차선** `xc = (xL + xR)/2` (단독차선이면 법선방향 반폭 이동) |
| **노랑** | **θ 를 만드는 시컨트** — 근점과 전방점을 잇는 직선. θ 는 이 직선의 각이다 |
| 회색(가는 선) | 근점에서의 **접선** — 곡선에서 시컨트와 얼마나 다른지 눈으로 보인다 |
| 회색 사각형 | IPM 사각형. 원본에서 곡선이 멀리까지 뻗어 보이면 대개 이게 트랙 밖까지 걸쳐 있어서다 |

원본과 BEV 양쪽에 같은 곡선을 그리고, 왼쪽 위 패널에 판정에 쓴 값과
시컨트/접선 θ · cte · 차선폭을 적는다.

> **자체 검사로 교차 확인한다.** `render.py` 는 그림용 재계산이라 대상 노드와
> 어긋날 수 있다. 그래서 `tb.selftest` 가 **주입 검증과 같은 기하**(θ = atan(b),
> 중심선 = 중앙, 단독차선 반폭 이동)로 렌더러를 검사한다.

---

## 8. 눈으로 보면서 디버깅하기

```bash
python3 -m tb.run run --scenario scenarios/regression.yaml --watch
```

계약의 `debug_topics:` 에 적힌 이미지 토픽을 창으로 띄우고, **그 위에 지금 프레임의
신호값을 겹쳐 그린다.** 숫자와 그림이 같은 프레임에서 나온 것이라
"이 값이 왜 이렇게 나왔는지"를 바로 대조할 수 있다.

| 키 | 동작 |
|---|---|
| `space` | 일시정지 / 재개 |
| `n` | 정지 상태에서 **한 프레임만** 진행 |
| `s` | 현재 화면을 PNG 로 저장 (오버레이 포함) |
| `q` | 뷰어만 닫는다 (실행은 계속) |
| `[` `]` | 오버레이 끄기 / 켜기 |

정지·단계는 `/testbed/control` 로 재생기에 전달된다. lockstep 재생이면
**정확히 한 프레임씩 끊어 볼 수 있다** — 실차에서는 불가능한 디버깅이다.

창 없이 나중에 보고 싶으면:

```bash
python3 -m tb.run run --scenario scenarios/regression.yaml --record-debug
# → runs/<런>/lane_debug.mp4  (오버레이가 구워진 채로 저장된다)
```

> **보는 것은 결과를 바꾸지 않는다.** `--record-debug` 를 켠 런과 안 켠 런을
> 베이스라인 비교한 결과가 전 신호 `max\|Δ\| = 0.0000` 이었다. 대상 노드의 시각화는
> 발행 이후 단계라 값에 영향이 없다는 걸 실측으로 확인했다. 마음 놓고 켜도 된다.

`--watch` 일 때만 `QT_QPA_PLATFORM=offscreen` 을 풀어 준다. 그 외에는 항상 헤드리스라
서버·CI 에서도 그대로 돈다.

---

## 9. 영상으로 카메라 세팅 맞추기

```bash
python3 -m tb.calibrate --scenario scenarios/regression.yaml
```

원본 영상 프레임 위에서 **IPM 사각형·차선 ROI·신호등 ROI 를 끌어 옮기고,
BEV 를 실시간으로 보면서** 맞춘다. 맞출 대상과 파라미터 이름은 전부 계약의
`calibration.targets` 에 있으므로 도구는 워크스페이스를 모른다.

| 키 | 동작 |
|---|---|
| `1` `2` `3` | IPM 사각형 / 차선 ROI / 신호등 ROI 선택 |
| 드래그·방향키 | 가장 가까운 점 이동 (Shift = 10px) |
| `4` → BEV 클릭 ×2 → `Enter` | 실측 길이로 `px2m` 산출 |
| `+` `-` | 실측 기준 길이 조정 (0.05 m 단위) |
| `g` `u` | 격자 / 왜곡보정 토글 |
| `[` `]` `,` `.` | 프레임 이동 (±30 / ±1) |
| `s` | YAML 저장 — 시나리오 `params:` 에 그대로 붙는다 |

### 사각형이 맞았는지 어떻게 아는가

**IPM 사각형의 좌우 변을 차선 위에 올려라.** 지면은 평면이므로 그렇게 놓으면
BEV 에서 차선이 정확히 수직·평행으로 선다. 눈으로만 보지 않아도 되도록
**수직도**를 자동으로 잰다(Hough 로 검출한 선들의 수직 대비 중앙값 편차):

```
수직도 3.1° (선 11개) — 거의 맞다   ※ 직선 구간에서만 의미 있다
```

2° 미만이면 맞은 것, 5° 넘으면 좌우 변을 차선에 더 붙여야 한다.
**곡선 구간에서는 의미가 없다** — 차선 자체가 휘어 있으므로 직선 구간으로 이동해서 볼 것.

### 여기서 맞춘 게 노드에도 그대로 적용되는가

도구는 노드의 변환(`remap` → crop → resize → `warpPerspective`)을 `tb/geometry.py` 로
재현한다. 같은 변환인지 실제로 대조할 수 있다:

```bash
python3 -m tb.run run --scenario scenarios/regression.yaml --record-debug
python3 -m tb.calibrate --scenario scenarios/regression.yaml --verify runs/<런>
```

```
프레임 정렬 오프셋 +0 (일치율 0.811)
프레임 7장 대조 — 에지 일치율 중앙값 0.872
✅ 같은 변환이다 — 여기서 맞춘 값이 노드에 그대로 적용된다.
```

노드가 BEV 위에 곡선·HUD 를 덧그리므로 픽셀 비교는 불가능하다. 그래서 에지가
±2px 안에서 겹치는 비율로 **기하만** 잰다. 일부러 틀린 사각형을 넣으면 0.245 로
떨어지는 것까지 확인했다(음성 대조군).

> ⚠️ **어안 보정 계수는 노드가 파라미터가 아니라 소스에 박아 두었다.**
> (`perception.py` 의 `fx, fy, cx, cy, k1…k3`) 그래서 계약의
> `calibration.undistort` 에 같은 값을 옮겨 적을 수밖에 없다. 노드 쪽이 바뀌면
> 여기도 바꿔야 하고, `--verify` 가 어긋남을 잡아 준다.
> 근본 해결은 노드가 `camera_info` 나 yaml 에서 읽게 하는 것이다.

---

## 10. 1080p 이미지 전송 — 이건 반드시 알아야 한다

테스트베드는 `fastdds_profile.xml` 을 자동으로 적용한다. 없으면 **프레임의 약 5%가
조용히 사라진다.** 실측:

| 설정 | 200프레임 중 수신 | 손실률 |
|---|---|---|
| 기본 | 189 | **5.5%** |
| `fastdds_profile.xml` 적용 | 200 | **0.0%** |

원인: `sensor_msgs/Image` 1920×1080×3 = **6.2 MB**. Fast DDS 의 기본 공유메모리
세그먼트는 512 KB 라 이 샘플이 안 들어가고 UDPv4 로 폴백한다. 그러면 6.2 MB 가
~4400개 UDP 조각으로 쪼개지는데, 커널 수신버퍼(`net.core.rmem_max` 기본 208 KB)를
넘치고 `/image_raw` 구독이 **BEST_EFFORT** 라 재전송이 없다 → 샘플이 통째로 유실된다.

프로파일은 SHM 세그먼트를 128 MB 로 키워 UDP 폴백 자체를 없앤다. `sudo` 가 필요 없다.

> **이건 테스트베드만의 문제가 아니다.** 실차에서 `usb_cam` → `perception` 도 같은
> 경로를 탄다. 같은 호스트에서 도는 한 같은 5% 유실이 있을 수 있다(30fps 기준
> 초당 1.5프레임). 실차 런치에도 이 프로파일을 적용할지는 별도 판단이 필요하다.

또한 lockstep 재생에는 **재투입(retry)** 이 있다: 동기 응답이 `sync_timeout` 안에
안 오면 같은 프레임을 다시 민다. 유실이 남아도 프레임 1:1 대응이 깨지지 않아
회귀 비교가 흔들리지 않는다. 재투입 횟수는 리포트와 `player.json` 에 남는다.

---

## 11. 다른 워크스페이스에 붙이기

**테스트베드 코드는 한 줄도 안 고친다. 계약 파일 하나를 더 만들면 끝이다.**

### 어디에 둘 것인가 — 워크스페이스 안이냐 밖이냐

**밖에 둔다. 이미 그렇게 돼 있다 — `~/cam_testbed/`.** 복사해 다닐 필요가 없다.

`tb/`·`web/` 전체에서 대상 워크스페이스를 가리키는 경로는 **한 군데도 없다**.
코드가 아는 절대 경로는 자기 자신뿐이다:

```python
ROOT = Path(__file__).resolve().parent.parent      # testbed/ (테스트베드 자신)
```

대상은 계약의 `workspace:` 로만 들어오고, 거기의 `install/setup.bash` 를 source 해
`ros2 run` 으로 노드를 띄운다. 그래서 테스트베드가 대상 안에 있든 밖에 있든 결과가 같다.

> 검증: `testbed/` 를 워크스페이스와 무관한 `/tmp/…/tb_ws` 로 통째로 복사해 `doctor` 를
> 돌렸더니 계약은 새 위치에서 읽고 대상 워크스페이스는 `/home/anjabom/white_cam_ws` 로
> 정확히 잡아 **전 항목 통과**했다.

| 두는 곳 | 언제 | 대가 |
|---|---|---|
| **별도 디렉터리 (현재)** | 워크스페이스가 둘 이상이거나 앞으로 늘어날 때 | 계약마다 `workspace:` 를 **절대 경로**로 적어야 한다 |
| 워크스페이스 안 | 대상이 하나뿐이고 같이 버전관리하고 싶을 때 | 워크스페이스마다 사본이 생겨 **고칠 때 N군데를 고쳐야 한다** |

안에 두더라도 `colcon build` 는 `COLCON_IGNORE` 가 막으므로 빌드에 섞이지 않는다.
`workspace:` 를 생략하면 계약 파일의 조상 중 `install/setup.bash` 를 가진 첫 디렉터리를
자동으로 쓴다 — 안에 둔 경우를 위한 편의 기능이므로, **밖에 뒀다면 반드시 명시**한다.
빠뜨리면 `doctor` 가 "절대 경로로 적어야 한다"고 알려 준다.

> 옮긴 뒤 실측: 같은 시나리오를 워크스페이스 안·밖에서 각각 돌려 `compare_signals`
> 8개가 **max|Δ| = 0.000000** 으로 같았다. 벽시계에 묶인 `cmd_speed`(hold 신호,
> 비교 대상 아님)만 395행 중 4행이 달랐다 — 위치와 무관한 §12 의 알려진 제약이다.

### 워크스페이스를 여러 개 붙이는 방법 (복사하지 않는다)

한 벌의 테스트베드에 계약·시나리오만 늘린다:

```
~/cam_testbed/
  contracts/white_camera.yaml     workspace: /home/me/white_cam_ws
  contracts/other_ws.yaml         workspace: /home/me/other_ws
  scenarios/white_reg.yaml        contract: contracts/white_camera.yaml
  scenarios/other_reg.yaml        contract: contracts/other_ws.yaml
  runs/                           런은 계약 이름과 함께 남아 섞이지 않는다
```

**웹앱에서 해도 된다.** 실행 화면 아래 `워크스페이스 · 영상 등록` 을 펴면 계약 생성·
경로 수정·영상 등록·시나리오 생성이 전부 여기서 된다. 파일을 직접 고치는 것과 같은
결과이고, `local.yaml` 의 **주석은 보존된다**(줄 단위로 갈아 끼우고 yaml 왕복을 하지 않는다).

```bash
python3 -m tb.run doctor --contract contracts/other_ws.yaml
python3 -m tb.run run   --scenario scenarios/other_reg.yaml
```

기준(baseline)도 시나리오 이름으로 갈리므로 워크스페이스끼리 서로의 회귀 판정을
건드리지 않는다. `local.yaml` 의 `default_contract:` 로 평소 쓰는 것을 정해 둔다.

**복사해야 하는 경우는 하나뿐이다** — 다른 사람·다른 PC 에 넘길 때. 그때도
`local.yaml` 은 빼고 준다(머신마다 다른 경로가 여기 모여 있다. `local.yaml.example` 참고).

계약이 대상 워크스페이스를 지정한다:

```yaml
workspace: /home/me/other_ws        # 여기의 install/setup.bash 를 source 한다
ros_setup:                          # 추가 오버레이가 있으면
  - /home/me/dep_ws/install/setup.bash
nodes:
  - {id: planner, package: other_pkg, executable: planner, node_name: planner}
```

`workspace:` 를 생략하면 계약 파일의 조상 중 `install/setup.bash` 를 가진 첫 디렉터리를
자동으로 쓴다(테스트베드가 워크스페이스 안에 있는 흔한 경우).

### 순서

**1) 계약 초안을 자동으로 뽑는다** — 대상 시스템을 평소처럼 띄워 놓고:

```bash
ros2 launch other_pkg whatever.launch.py     # 다른 터미널
python3 -m tb.discover --name other_ws \
    --workspace /home/me/other_ws \
    --out contracts/other_ws.yaml --seconds 8
```

돌고 있는 그래프의 토픽·타입·**실제 메시지의 필드 배치**를 읽어 계약 초안을 만든다.
`Float32MultiArray` 같은 숫자 배열은 의미를 알 수 없으므로 `data[0]`, `data[1]` … 을
전부 뽑아 놓는다. 문자열은 `compare_categorical` 로, 숫자는 `compare_signals` 로
자동 분류하고 `layout.*` 같은 메타데이터는 걸러 낸다.

**2) 사람이 이름만 붙인다.** `nav_state_data0` → `cte_m` 처럼. 그리고 네 가지를 정한다:

| 항목 | 무엇을 적나 |
|---|---|
| `nodes:` | 띄울 패키지/실행파일 |
| `stimulus.image_topic` | 영상을 밀어 넣을 토픽 (없으면 `""` → 기록 전용) |
| `sync_topic` | "한 프레임 처리가 끝났다"를 알리는 토픽 (lockstep 의 기준) |
| `hold_signals` / `compare_sequence` | 상태 유지형 / 타이머 발행 신호 |

**3) 점검하고 돌린다:**

```bash
python3 -m tb.run doctor --contract contracts/other_ws.yaml
python3 -m tb.run run --scenario scenarios/other_ws.yaml
```

### attach 모드 — 띄우지 않고 관찰만

```yaml
attach: true
stimulus: {image_topic: ""}     # 자극 없음
```

노드를 띄우지 않고 **이미 돌고 있는 시스템**에 붙어 기록·분석만 한다.
시나리오의 `record_seconds:` 만큼 기록한다. 실차에서 그대로 지표를 뜨거나,
런치 파일이 복잡해서 테스트베드가 재현하기 곤란할 때 쓴다.

영상 자극이 없으면 행 번호의 기준이 없으므로 `sync_topic` 이 올 때마다 1행으로 센다.

> 실제로 검증했다: 이 워크스페이스와 아무 관계 없는 시스템(`/nav/state`, `/nav/mode`,
> `/cmd_vel`, `/dist`)에 `contracts/demo_foreign.yaml` 로 붙여 15초 기록 → **308행**
> 수집·분석·판정까지 정상 동작했다. `tb/` 코드는 한 글자도 안 바꿨다.

### 영상은 논리 이름으로

영상은 머신마다·대회마다 다르므로 시나리오에 절대경로를 박지 않는다:

```yaml
# scenarios/regression.yaml
video: track_a
```
```yaml
# local.yaml  (버전관리 제외)
videos:
  track_a: /home/anjabom/track_record.mp4
```

### 새 영상을 찍었을 때

베이스라인은 **특정 영상·특정 구간에 묶여 있다.** 다른 영상으로 만든 기준과 비교하면
숫자는 나오지만 의미가 없다. 그래서 베이스라인에 출처를 같이 저장하고,
조건이 다르면 경고한다:

```
⚠️  기준과 조건이 다르다:
      video: '/home/…/track_record.mp4' → '/home/…/new_2026.mp4'
      start: 780 → 0
```

비교 리포트 맨 위에도 같은 경고가 박히고, `baseline` 으로 덮어쓸 때는 확인을 받는다.

새 영상이 오면:

```bash
# 1. local.yaml 의 videos: 에 등록하고 시나리오의 video: 를 그 이름으로
# 2. 좌우 차선이 잘 보이는 구간을 start/limit 으로 고른다 (--watch 로 눈으로 확인)
python3 -m tb.run run --scenario scenarios/regression.yaml --watch
# 3. 결과가 납득되면 기준으로 등록
python3 -m tb.run baseline <런디렉토리> --name regression
```

**차량 규격도 같이 확인할 것.** `track_record.mp4` 는 다른 대회의 실내 모형 트랙이라
실측 차선폭이 약 1.3 m 인데 `lane_width_m` 기본값은 실차 규격 3.0 m 다. 그래서
`WIDTH_BAD` 가 54% 프레임에서 뜬다 — 코드 버그가 아니라 영상과 파라미터의 불일치다.
새 영상에서는 `lane_width_m`·`width_min_m`·`width_max_m` 를 실제 트랙에 맞춰야
`conf_eff` 가 살아난다.

---

## 11.5 새 노트북으로 옮기기

**테스트베드는 위치 독립적이다.** `tb/`·`web/` 전체에서 코드가 아는 절대 경로는
자기 자신(`ROOT`)뿐이고, 대상은 계약의 `workspace:` 한 줄로만 들어온다.
그래서 옮길 때 고칠 곳은 **코드가 아니라 3개의 데이터 파일**이다.

저장소: `https://github.com/Anjabom/cam_testbed` (private)

### 무엇이 따라가고 무엇이 안 따라가는가

| | 내용 | 왜 |
|---|---|---|
| **따라간다** | `tb/` · `web/` · `contracts/` · `scenarios/` · `cases/` · `baselines/` · `fastdds_profile.xml` · `.flake8` | 머신과 무관한 로직·설정 |
| **안 따라간다** | `local.yaml` | ★머신마다 다른 경로가 모인 곳★ — 따라가면 새 머신에서 전부 틀린 경로가 된다 |
| **안 따라간다** | `runs/` (132 MB) | 실행 결과. 영상·JSONL 이라 저장소를 부풀리기만 한다 |
| **안 따라간다** | `__pycache__/` · `*.pyc` | 빌드 산출물 |

`.gitignore` 가 이 셋을 막는다. 저장소 실물은 **38개 파일 · 728 KB** 로, 클론이 즉시 끝난다.

`local.yaml.example` 은 따라간다 — 새 머신에서 이걸 복사해 고치는 것이 시작점이다.

### 0단계 — 새 노트북에 있어야 하는 것

| 필요한 것 | 확인 | 없으면 |
|---|---|---|
| ROS 2 (Humble 기준) | `echo $ROS_DISTRO` | 대상 노드를 띄울 수 없다 |
| `rclpy` · `cv2` · `numpy` · `yaml` · `cv_bridge` | `python3 -c "import rclpy, cv2, numpy, yaml"` | `doctor` 가 잡아 준다 |
| `ffmpeg` | `ffmpeg -version` | 영상이 `cv2/mp4v` 로 찍혀 **오류도 없이 검은 화면**이 된다 (§10) |
| 대상 워크스페이스 소스 | — | 계약이 가리킬 곳이 없다 |

웹앱(`web/`)은 **서버·프런트 모두 추가 의존성이 없다** — `web/server.py` 의 최상위 import 는
전부 표준 라이브러리이고, 프런트엔드도 프레임워크를 쓰지 않는다. 다만 프레임 추출·썸네일
기능은 `cv2` 를 함수 안에서 늦게 부르므로, 위 표의 `cv2` 는 어차피 필요하다.

### 1단계 — 클론

저장소가 private 이므로 인증이 필요하다. `gh` 가 가장 간단하고, **sudo 없이** 설치된다:

```bash
# gh 설치 (sudo 불필요 — 공식 바이너리를 홈에 둔다)
#   apt 의 gh 는 낡았고(22.04 는 2.4.0) 설치에 sudo 가 필요하다.
#   릴리스 자산 이름에 버전이 박혀 있으므로 태그를 먼저 조회한다.
mkdir -p ~/.local/bin && cd /tmp
V=$(curl -sS https://api.github.com/repos/cli/cli/releases/latest \
      | grep -oP '"tag_name":\s*"v\K[^"]+')
curl -sSL -o gh.tar.gz \
  "https://github.com/cli/cli/releases/download/v${V}/gh_${V}_linux_amd64.tar.gz"
tar -xzf gh.tar.gz && cp gh_*/bin/gh ~/.local/bin/ && export PATH="$HOME/.local/bin:$PATH"
gh --version

gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git          # 이후 일반 git push/fetch 도 인증된다
```

> ★SSH 가 아니라 HTTPS 를 쓴다★ — 이 계정의 기존 SSH 키가 특정 저장소 전용
> **deploy key** 인 경우가 있어 SSH 로 가면 새 저장소에서 인증이 실패한다.
> `ssh -T git@github.com` 이 `Hi <계정>/<저장소>!` 로 답하면 그게 deploy key 다
> (계정 키라면 `Hi <계정>!` 로만 답한다).

```bash
git clone https://github.com/Anjabom/cam_testbed.git ~/cam_testbed
cd ~/cam_testbed
```

`~/cam_testbed` 가 아닌 곳에 둬도 된다. 테스트베드는 자기 위치를 `__file__` 로 알아낸다.

### 2단계 — 대상 워크스페이스를 빌드한다

계약은 `<workspace>/install/setup.bash` 를 source 해서 `ros2 run` 으로 노드를 띄운다.
**빌드가 안 돼 있으면 그 파일이 없어 아무것도 안 뜬다.**

```bash
cd ~/white_cam_ws
colcon build --symlink-install
```

`--symlink-install` 로 빌드해 두면 이후 **소스를 고쳐도 재빌드 없이 다음 런에 바로 반영된다.**

### 3단계 — `local.yaml` 을 이 머신 것으로 쓴다

```bash
cd ~/cam_testbed
cp local.yaml.example local.yaml
```

예시 파일에는 **이전 머신의 경로가 그대로 들어 있다.** 머신을 타는 것은 `videos:` 와
`params:` 뿐이다 — `default_contract:` 는 저장소 기준 상대경로라 손대지 않아도 된다:

```yaml
default_contract: contracts/white_camera.yaml   # 상대경로 — 그대로 둔다

videos:                          # ★논리 이름 → 이 머신의 실제 경로★
  track_a: /경로/track_record.mp4  #  시나리오는 이름만 알고 경로는 모른다

params:
  perception:
    lane_weights_roi: /경로/best.pt   # 실차는 TensorRT .engine 이지만
    tl_weights_roi:   /경로/best.pt   # GPU/TRT 버전이 다르면 안 열린다 → .pt
    device: cuda                      # ★GPU 가 없으면 cpu★
```

논리 이름을 안 쓰고 시나리오에 절대경로를 박으면 다음 머신에서 또 고쳐야 한다.
`videos:` 에 없는 이름은 경로로 그대로 해석되지만(일회성 실행용), `doctor` 가 경고한다.

**웹앱에서 해도 된다** — 실행 화면 아래 `워크스페이스 · 영상 등록`. 파일을 직접 고치는 것과
같은 결과이고 `local.yaml` 의 **주석이 보존된다**(줄 단위로 갈아 끼운다).

### 4단계 — 계약의 `workspace:` 를 고친다

```yaml
# contracts/white_camera.yaml
workspace: /home/<새계정>/white_cam_ws     # ★절대 경로★
```

테스트베드가 워크스페이스 **밖**에 있으므로 반드시 명시해야 한다. 생략하면 계약 파일의
조상 중 `install/setup.bash` 를 가진 첫 디렉터리를 찾는데, 밖에 있으면 찾지 못한다.
빠뜨리면 `doctor` 가 "절대 경로로 적어야 한다"고 알려 준다.

### 5단계 — 점검

```bash
source /opt/ros/humble/setup.bash
cd ~/cam_testbed
python3 -m tb.selftest                                        # ROS·영상 불필요, 17개 항목
python3 -m tb.run doctor --scenario scenarios/regression.yaml
```

`doctor` 가 위 1~4단계를 전부 대신 확인해 준다. 통과하면 이렇게 나온다:

```
── 테스트베드 ──
  ✅ ROS_DISTRO  humble
  ✅ ros2 실행 가능
  ✅ cv2 / rclpy import
  ✅ Fast DDS 프로파일  1080p 무손실 전송용 — 없으면 프레임 ~5% 유실
  ✅ 영상 코덱  ffmpeg/H.264 — mp4v 뿐이면 웹앱에서 재생 불가
── 계약 ──
  ✅ 계약 파일  /home/…/contracts/white_camera.yaml
     `white_camera` v1 · 노드 2 · 관찰토픽 8 · 신호 27
── 대상 워크스페이스 ──
  ✅ 워크스페이스  /home/…/white_cam_ws
  ✅ setup.bash  /home/…/white_cam_ws/install/setup.bash
  ✅ 실행파일 white perception
  ✅ 실행파일 white camera_judgment
── 시나리오 ──
  ✅ 영상  'track_a' → /home/…/track_record.mp4
  ✅ perception.lane_weights_roi  /home/…/best.pt

판정: OK
```

`실행파일` 항목은 대상 워크스페이스를 source 한 뒤 `ros2 pkg executables` 를 실제로
실행해 대조한 결과다 — **계약에 적은 package/executable 이 진짜 있는지**까지 확인한다.

### 6단계 — 첫 런과 기준 재등록

```bash
python3 -m tb.run run --scenario scenarios/regression.yaml
python3 -m tb.run baseline <런디렉토리명> --name regression
```

### ★가장 중요한 함정 — 베이스라인은 머신을 넘지 못한다★

`baselines/regression.csv` 는 저장소에 따라가지만, **그대로 쓰면 안 된다.**
그 숫자는 **이전 머신의 GPU·드라이버·`device` 설정으로 YOLO 를 돌린 결과**다.
추론 백엔드가 달라지면(다른 GPU, 또는 `cuda`→`cpu`) 세그멘테이션 출력이 미세하게
달라지고, `regression.yaml` 의 `compare_tol` 은 그 차이를 잡아내도록 빡빡하게 잡혀 있다:

```yaml
compare_tol:
  _default: 1.0e-6      # 기본은 완전 일치 요구
  theta_deg:    0.20
  flags:        0.0     # ★플래그는 한 프레임도 달라지면 안 된다★
```

그래서 **새 머신에서 처음 도는 회귀는 DIFF 로 뜨는 것이 정상이다.** 코드 회귀가 아니라
환경이 바뀐 것이므로, 첫 런을 그 머신의 기준으로 새로 등록하고 거기서부터 비교한다.

> `lockstep` 모드는 CPU 속도와 무관하게 같은 결과를 보장하지만, 그건 **타이밍** 이야기다.
> 추론 백엔드가 바뀌는 것까지 흡수하지는 못한다.

베이스라인에는 출처(영상·구간·파라미터·워크스페이스·`code_fingerprint`)가 함께 저장되고,
조건이 다르면 비교 리포트 맨 위에 경고가 박힌다 — 모르고 지나칠 일은 없다.

### 절대 경로가 박힌 곳은 3군데뿐이다

옮긴 뒤 `grep` 으로 직접 확인할 수 있다:

```bash
grep -rn "/home/" --include=*.yaml --include=*.json --include=*.py . | grep -v ^./runs/
```

| 위치 | 무엇 | 조치 |
|---|---|---|
| `contracts/*.yaml` 의 `workspace:` | 대상 워크스페이스 | **수정 필수** (4단계) |
| `local.yaml` | 영상·가중치 경로 | **새로 작성** (3단계) |
| `baselines/*.json` | 기준을 뜰 때의 조건 기록 | 기록용 메타데이터 — 실행에 영향 없음 |

나머지 히트는 전부 **주석·독스트링 예시·입력창 placeholder** 다
(`tb/run.py` 의 사용 예, `scenarios/regression.yaml` 의 실차 기본값 메모, `web/app.js` 의 placeholder).
`local.yaml` 은 갓 클론한 직후에는 아직 없으므로 히트에 안 잡힌다 — 3단계에서 만들고 나면 잡힌다.

### 새 노트북에서 ★새 워크스페이스★ 를 붙일 때

위 절차에 §11 을 얹으면 된다. 테스트베드는 복사하지 않고 **계약만 하나 더 만든다.**

```bash
# 1~2단계는 동일 (클론 + 새 워크스페이스 colcon build)

# 3. 대상 시스템을 평소처럼 띄워 놓고 계약 초안을 뽑는다
ros2 launch <새패키지> whatever.launch.py        # 다른 터미널
python3 -m tb.discover --name other_ws \
    --workspace /home/<계정>/other_ws \
    --out contracts/other_ws.yaml --seconds 8

# 4. 초안의 TODO 를 채운다 (§11 의 표 참고: nodes / image_topic / sync_topic / hold)
# 5. 시나리오를 하나 만들고 점검
python3 -m tb.run doctor --contract contracts/other_ws.yaml
python3 -m tb.run run   --scenario scenarios/other_reg.yaml
```

계약·시나리오·베이스라인이 전부 이름으로 갈리므로 **워크스페이스끼리 서로의 회귀 판정을
건드리지 않는다.** `local.yaml` 의 `default_contract:` 로 평소 쓰는 것을 정해 둔다.

### 자주 걸리는 것

| 증상 | 원인 | 조치 |
|---|---|---|
| `setup.bash 없음` | 대상을 빌드하지 않았다 | 2단계 `colcon build` |
| `실행파일 … ❌` | 계약의 package/executable 이름이 틀렸거나 빌드에서 빠졌다 | 계약 수정 또는 재빌드 |
| `영상 ❌` | `local.yaml` 의 `videos:` 경로가 이 머신에 없다 | 3단계 |
| 웹앱에서 영상이 검은 화면 | `ffmpeg` 이 없어 `cv2/mp4v` 로 찍혔다 | `sudo apt install ffmpeg` 후 재실행 |
| 첫 회귀가 전부 DIFF | 추론 백엔드가 달라졌다 | 정상. 그 머신 기준을 새로 등록 |
| `WIDTH_BAD` 가 절반 넘게 뜬다 | 영상의 트랙 규격과 `lane_width_m` 불일치 | §11 「새 영상을 찍었을 때」 |
| YOLO 가 `.engine` 을 못 연다 | TensorRT/GPU 버전이 다르다 | `local.yaml` 에서 `.pt` 로 덮어쓴다 |

### 되돌려 보내기

새 노트북에서 고친 것을 다시 올릴 때 `local.yaml` 과 `runs/` 는 `.gitignore` 가 알아서 막는다:

```bash
git add -A && git commit -m "…" && git push
```

`git` 신원이 없다는 경고가 나오면:

```bash
git config --global user.name  "Anjabom"
git config --global user.email "vk1124x@gmail.com"
```

---

## 12. 알려진 제약

- `camera_judgment` 는 내부적으로 `time.time()`(wall clock)을 쓴다. 따라서
  `sim_time: true` 를 켜도 신호등 hold(`tl_hold_s`)·staleness 게이트는 **실시간으로 돈다.**
  lockstep 은 벽시계가 실제로 흐르므로 이 게이트들은 30fps 실차와 다르게 동작한다.
  → 신호등 게이트의 타이밍을 보려면 `realtime.yaml` 을 쓴다.
  (근본 해결은 핸드오프 문서의 P0-1 / T-03.)
- 메시지에 헤더 스탬프가 없어(P0-1) 프레임↔출력 정렬은 테스트베드 내부 채널
  `/testbed/frame` 으로 한다. lockstep 에서는 1:1 이 보장되지만 realtime 에서는
  근사다. T-02 로 스탬프가 들어오면 계약의 `stamp_sec`/`stamp_nsec` 이 자동으로
  잡히므로 정확 정렬로 올릴 수 있다.
- 런마다 임의의 `ROS_DOMAIN_ID` 를 써서 다른 ROS 세션과 섞이지 않게 한다.
