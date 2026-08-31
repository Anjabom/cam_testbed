# cam_testbed — 카메라 인지 노드 테스트 플랫폼 개발 기록

> `notion/gen_log.sh` 가 `git log` 에서 생성한 문서다. **직접 편집 금지** (다음 생성 때 덮어쓴다).
> 설계·경계 규칙은 `CLAUDE.md` 와 `README.md`, 현재 현황은 `notion/STATUS.md` 를 고친다.
> 서사(무엇을 왜 그렇게 결정했나)는 `notion/DEV_HISTORY.md`.
> 생성일 2026-08-31 · 커밋 17개

## 현재 현황 (2026-08-31)

### 무엇이 되는가

`~/cam_testbed` 는 ROS 2 카메라 인지 노드를 **밖에서** 시험하는 테스트 플랫폼이다.
대상 워크스페이스를 import 하지 않고 `ros2 run` 서브프로세스로 띄워 **DDS 로만** 대화한다.
원본 영상을 이미지 토픽으로 밀어 넣고, 나오는 토픽을 전부 기록해 **지표 · 불변식 · 회귀 비교**
로 판정한다. Ground Truth 는 (아직) 없다.

**사람이 치는 명령은 실질적으로 하나다** — `python3 -m tb.run app`. 실행·비교·기준 등록·
재해석·피드백·캘리브레이션·프레임 탐색이 전부 웹앱 안에 있다.

| | 개수 | |
|---|---|---|
| 계약 (`contracts/`) | 3 | `white_camera`(차선) · `white1_stopline`(정지선) · `demo_foreign`(데모) |
| 시나리오 (`scenarios/`) | 13 | 실차 야간 5 · 지그(데모 영상) 5 · 회귀/강건성/실시간 3 |
| 등록된 기준 (`baselines/`) | 3 | `regression` · `stopline_demo_detect` · `stopline_demo_detect_off` |
| 쌓인 실행 (`runs/`, git 제외) | 44 | |
| 자체 검사 (`tb/selftest.py`) | 44 | ROS·영상 없이 돈다 |

**공개 사이트**: `docs/` 를 GitHub Pages 로 굽는다(`tb.run publish`). 결과만 보는 읽기 전용
정적 사이트 — 실행·테스트 준비·보정·도구는 빠진다.

### 설계의 한 줄

**대상 워크스페이스 코드가 바뀌어도 `tb/*.py` 는 바뀌지 않는다.**
토픽명·필드 배치·노드명·파라미터명은 `contracts/*.yaml` 에만 있다. 워크스페이스 1개 = 계약 1개.
새 워크스페이스를 붙일 때도 코드를 복사하지 않고 **계약 파일을 하나 더 만든다**.

### 지금 붙어 있는 대상

| 대상 | 계약 | 상태 |
|---|---|---|
| `~/gold_ws/gold_ws/src/white1` | `white1_stopline` | 정지선·신호등. 야간 영상 3 시나리오 |
| `~/white_cam_ws/src/white` | `white_camera` | 차선. 야간 영상 2 시나리오 |

### 미검증 · 열려 있는 것

- **GT(GPS 참값) 대조 — 설계만 있고 코드는 없다.** `GT_DESIGN.md` 참조. 실측 데이터
  (영상 + GPS 동시 주행)도 아직 없다. 승인되면 구현하고 문서는 README § 로 편입한다.
- **야간 차선 시나리오의 진동(vibration_frac) 이 두 영상 모두 기준 초과.** 카메라 문제인지
  판정 기준이 이 차량에 안 맞는 것인지 아직 못 갈랐다. ★기준을 느슨하게 해서 통과시키지 않는다★.
- **정지선 2단 정지(단계 4) 는 아직 판정을 못 세웠다.** 근접도·확정 시간 두 문턱을 실측으로
  채워야 하는데, 그러려면 정지선 앞에서 실제로 서는 주행 영상이 필요하다.
- **베이스라인은 머신을 넘지 못한다.** GPU·가중치·영상이 다르면 값이 달라진다. 새 머신에서는
  기준을 다시 등록해야 한다.
- **`aux` 스케줄은 전이 프레임에서 ±1 프레임 밀릴 수 있다** (이미지와 다른 콜백 그룹).
  프레임 단위로 정확해야 하는 판정은 두지 않는다 — 구간 판정은 안전하다.

---

## 날짜별 개발 일지

### 2026-08-31 — 시나리오: 실차 야간 영상 5건 · GT 대조 설계 초안 · 사이트 갱신

실차 카메라로 찍은 야간 영상 두 클립(night_a 1292프레임 43초 / night_b 930프레임
31초)에 시나리오 5개를 붙였다. 같은 밤·같은 차·같은 카메라로 이어 찍은 것이라
장면만 다르다.

차선 (contracts/white_camera.yaml)
  lane_night_a · lane_night_b — 실도로라 실내 모형 트랙용 regression.yaml 과
  차선폭 규격이 다르다. local.yaml 의 실차 기본값을 그대로 쓰고 덮어쓰지 않는다.

정지선 (contracts/white1_stopline.yaml)
  stopline_night_a — 인지 시험 "있는 곳에서 잡고 없는 곳에서는 안 잡는가"
    ⚠️ ★이 파일의 전제가 뒤집혔다★ 종전에는 "이 영상엔 정지선이 없다(25프레임
    균등 샘플로 확인)" 를 근거로 ★오검출 시험★ 이었다. 그 표본이 구간을 통째로
    건너뛴 것이다 — 실제로는 프레임 307~377 · 542~621 · 673~736 에 정지선이 있고,
    세그 마스크를 원본에 그려 확인했더니 검출 208프레임이 ★전부 실재하는 정지선★
    이며 오검출은 0건이다. 즉 종전 판정은 ★정검출을 오검출로 세고 있었다★.
    균등 샘플로 "없음" 을 결론내지 말 것 — 없음은 표본으로 증명되지 않는다.
  stopline_night_b — 오검출 시험. 이쪽은 실제로 정지선이 없다.
  stopline_approach_night_a — 2단계 정지 시험(realtime, 단계 4)
    night_a 는 ★구조적으로 정지선 경로를 밟을 수 없다★ — 목업을 프레임 0부터
    고정으로 붙여 RED 가 즉시 확정되고, 우리 차선 정지선(542~621)이 나오기 20초
    전에 2단이 물린다. 그래서 그 파일의 정지 근거는 전부 [정지선 없음] 이고
    [예비제동]·[정지선 앞] 은 한 번도 안 나온다. 그래서 파일을 갈랐다:
    night_a = "무엇을 정지선으로 보는가"(인지, lockstep) / 이 파일 = "그래서
    올바른 순서로 무는가"(판단, realtime).

GT_DESIGN.md — GPS 참값 대조 설계 (★초안, 미구현★)
  "GPS rosbag 을 쓰면 더 정확한 테스트가 되는가" 에 답하고 착수 전에 구현을
  확정하려는 문서다. 대상 워크스페이스가 이미 RTK-GPS 참값을 만들어(signed_cte)
  카메라 신호와 ★같은 CSV 행★ 에 기록하므로, 그 CSV 를 signals.csv 에 gt_* 열로
  조인하면 GT 없이 재던 지표에 더해 "카메라가 실제와 얼마나 맞는가" 를 처음으로
  잴 수 있다. 방침 — ① 정지 위치·횡오차 둘 다 ② 정렬은 자동(상호상관)+수동 보정
  ③ 지금은 설계만. 경계는 그대로다: 신호 이름은 계약에만, tb/gt.py 와 web/* 에는
  워크스페이스 고유명 0글자.
  승인되면 핵심은 README § 로 편입하고 이 파일은 지운다.

docs/ (생성물 — `tb.run publish` 가 구웠다)
  별표한 실행 목록 갱신(regression_base 빠지고 lane_night_b_base 들어옴) ·
  피드백 문서에 「관측값」 절 반영 · nav 문구 «테스트 준비».

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

### 2026-08-31 — 테스트 준비: 계약을 먼저 고르고 판정을 물려받는다 — 본 떠서 · 합쳐서 · 가져오기

★고른 본을 따라 계약이 조용히 바뀌었다★ 가 이번 변경의 출발점이다. 예전에는
«본 떠서»/«빈 틀» 갈래를 먼저 고르게 했고 본 목록에 ★모든 계약의 시나리오가★
섞여 나왔다. clone 은 본의 contract: 줄을 그대로 물려받으므로, 다른 워크스페이스의
시나리오를 본으로 뜨면 대상이 말없이 갈아 끼워졌다.

이제 ★계약이 첫 칸★ 이다 (계약 하나 = 워크스페이스 하나)
  고른 계약의 시나리오만 본 목록에 뜨고, ★갈래는 고른 개수가 정한다★:
    0개 = 빈 틀   — 새 워크스페이스용. 그 계약의 signals: 이름 목록을 같이 보여
                    준다(물려받을 판정이 없어 checks: 를 손으로 써야 하고, 그때
                    필요한 유일한 정보가 이 목록이다)
    1개 = 본 떠서 — 영상만 갈아 끼운다. 판정과 ★그 근거 주석까지★ 그대로
    2개+ = 합쳐서 — config.compose_scenario. 목적이 다른 checks: 를 한 영상에
                    같이 걸고 싶을 때(예: 인지 판정 + 개입 판정). 계약이 다르면
                    거부한다. compare_tol 이 본들끼리 다르면 머리말에 경고를 적는다
  기본 계약은 default_contract → 빌드된 첫 계약 → 첫 줄 순이다.
  ★알파벳 첫 파일이 demo_foreign★ 이라 데모가 기본으로 뽑히면 곤란해서다.

«다른 계약의 판정 가져오기» (config.preview_checks / graft_scenario)
  clone/compose 는 계약을 넘을 수가 없다 — 본의 contract: 를 물려받으므로.
  여기서는 대상 계약을 받아 ★빈 틀 위에 고른 판정만★ 얹는다. 어느 판정이 이 계약
  에서 성립하는지는 tb.lint 로 물어보기만 하고 ★파일을 쓰지 않는다★
  (그래서 실행 중 잠금 위에 둔다). 새 라우트는 config/scenario/preview 하나.
  성립하지 않는 판정은 체크가 꺼진 채로 사유와 함께 보인다.

계약이 TODO 초안이어도 ★막지는 않는다★
  새 워크스페이스는 discover 로 계약을 채우기 ★전에★ 시나리오부터 만들어 두는
  순서가 실제로 있다. 다만 조용히 두면 3·4단계까지 가서야 `ros2 run TODO TODO`
  를 만나므로, 시나리오를 ★만드는 자리★ 에서 미리 알린다.

용어: «새 시험 시작» → «테스트 준비»
  하는 일이 '시험을 시작' 이 아니라 '돌리기 전에 갖추는 것' 이다. 원칙은 그대로 —
  ★파일을 쓰는 일은 «테스트 준비», 돌리는 일은 «테스트 실행»★.
  README·nav·style.css·publish.py 의 문구와 §13.9 표를 같이 고쳤다.

이 커밋에 함께 담기는 것 (여러 주제를 건드리는 공용 파일이라 나누지 않았다)
  · config.set_aux_schedule — 앞 커밋(저작)의 결과를 시나리오에 적는다
  · selftest t_feedback_observations — 앞앞 커밋(관측값 절)의 검사
  · README 의 피드백 문서 절 번호(6.관측값 …) — 같은 이유

자체 검사 44개 통과 (t_compose_scenario · t_graft_across_contracts 추가) · flake8 통과.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

### 2026-08-31 — 그림·검증: 그 런이 실제로 쓴 파라미터로 그린다 — 조용히 틀린 그림이 제일 나쁘다

night_b 런에서 ★멀쩡한 판정값을 버그로 읽었다★. 경로 오버레이의 사다리꼴이
노드 (750,650)(1170,650)(1810,1080)(260,1080) 인데 계약 default
(750,560)(1170,560)(1920,1080)(0,1080) 로 그려져, 자홍색 거리선이 205px(≈1.2m)
먼 곳에 얹혔다. 값은 옳았고 그림만 틀렸다. 그림 없이 원본만 나오는 것보다
★조용히 틀린 그림★ 이 나쁘다 — 그래서 셋을 같이 고친다.

harvest.effective_params(run_dir) — 실효값을 앞에서부터
  ① params_actual.yaml (`ros2 param dump` 로 뜬 실효값)
  ② summary.json 의 meta.params (테스트베드가 ★요청한★ 값)
  ②만으로 부족한 이유: 아무도 안 준 파라미터는 노드 자기 기본값으로 도는데
  그것이 계약의 default 와 얼마든지 어긋난다.
  ★과거 런도 되살아난다★ — params_actual.yaml 이 런 디렉터리에 남으므로
  다시 돌릴 필요 없이 그리기만 다시 하면 옳은 그림이 나온다.

render: 그 값으로 IPM 사각형·거리선을 그리고, 계약 default 로 떨어졌으면
  주황(C_WARN)으로 "이 그림을 믿으면 안 되는 이유" 를 화면에 적는다.

lint.lint_calibration_drift(contract, ws_params) — 낡음을 조용히 두지 않는다
  계약의 default: 는 노드 기본값을 옮겨 적은 ★문서★ 다. 그 낡음은 판정이 아니라
  ★그림과 게이트 통과율★ 을 흔들어서 조용하다. 어느 쪽이 옳은지는 여기서 못
  정하므로 ★경고만★ 한다 — 재캘리브된 것일 수도, 캐시가 낡은 것일 수도 있다.
  파라미터 이름도 노드 id 도 계약의 calibration.targets 가 주므로 워크스페이스
  고유명은 여전히 0글자다. lint(..., ws_params=) 로 선택적으로 붙는다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

### 2026-08-31 — 자극·저작: 프레임에 따라 바뀌는 aux 값 — 영상을 보며 키로 찍어 만든다

★신호등이 안 찍힌 영상으로 정지선을 시험할 수 있는가★ 에 답하는 변경이다.
white1 의 traffic_light 는 빨간 박스를 본 적이 있어야만 정지선 추론을 돌린다
(_sl_should_run). 그 게이트를 열어야 하는데 ★화면에 그림을 합성하는 방식은
성립하지 않았다★ — 박스 높이가 곧 거리의 대리값이라, 검출되는 크기의 합성 박스는
실차 문턱과 7배 어긋난다. 그래서 ★박스 높이를 직접 준다★.

계약: stimulus.aux[] 에 schedule 과 keys (white1_stopline)
  /tl/fake_box_h — 15px→RED_FAR(게이트만 열림, 제동 없음) · 40px→RED · 0=없음.
  ★fields 도 schedule 도 두지 않았다★ — 아무것도 없으면 발행 자체를 안 해
  노드가 종전대로(빨간불 없음) 돈다. 켜 두고 실차에 나가면 boot_inject /
  near_inject 로그가 나고, 실차 시나리오가 그것을 max:0 으로 막는다
  (계약이 스스로 백도어를 잠근다).

player: aux 가 두 갈래가 된다
  · fields:   고정값을 rate_hz 로 계속 — 늘 켜져 있는 허락 신호
  · schedule: {프레임: 값} — 타이머가 아니라 ★프레임마다★, 사이는 계단(hold).
              보간하지 않는 이유는 이게 '언제 무엇이 되었나' 를 적는 표이지
              물리량이 아니라서다(sched_value).
  매 프레임 내는 이유: RELIABLE 이어도 대상 노드가 늦게 뜨면 첫 발행을 놓치고
  그 뒤로 영영 기본값으로 돈다. 이미지보다 ★먼저★ 낸다.
  ⚠️ 콜백 그룹이 달라 전이 프레임에서 ±1 밀릴 수 있다 — 구간 판정은 안전하다.

viewer --watch: 타임라인을 사람이 저작한다
  계약의 keys 가 준 키를 누르면 그 순간의 frame_idx 를 적어 두었다가 끝날 때
  schedule.yaml / schedule.json 으로 뱉는다. ★키 이름·값·라벨은 전부 계약이 준다★
  (경계 규칙 ①·④). 이 노드는 debug_topics 가 비어 있어 디버그 이미지를 안 내므로,
  저작 배경으로 ★플레이어가 밀어넣는 자극 이미지★ 를 그대로 받아 깐다 — 그것이
  곧 노드가 보는 그림이고 프레임 번호와 짝이다.
  라벨은 geometry.put_text(PIL) 로 그린다 — cv2.putText 는 ASCII 만 그려 한글이
  통째로 ???? 가 된다.

계약의 workspace: · calibration file: 경로를 ~/gold_ws/gold_ws 로 (머신에서 이동).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

### 2026-08-31 — 피드백: 판정이 없어도 문서에 알맹이가 남는 「관측값」 절

★새 워크스페이스·새 영상에서는 checks: 를 쓸 수가 없다★ — "이 값이 이래야 한다"
는 기준은 정상이 뭔지 알아야 쓴다. 그래서 판정 0개로 먼저 돌리게 되는데, 그때
피드백 문서가 「나쁜 점 없음」만 적고 끝났다. 판정이 ★없는 것★과 전부 ★통과한
것★은 전혀 다른데 문서만 보면 «다 정상» 으로 읽힌다 — 이게 이번 변경의 축이다.

observations() — `## n. 관측값` 절 (신규)
  · 숫자 신호   : 평균·표준편차·최소·최대·p95·유효율
  · 상태 신호   : 값별 분포
  · 전이        : 계약의 events: 가 선언한 신호의 값이 바뀐 순간들
  · 노드 로그   : 토픽에 안 나오는 근거(log_events)
  전부 summary.json 에 ★이미 계산돼 있는★ 값이다. 여기서 새로 판정하지 않는다.

절 번호가 하나씩 밀린다 (6.관측값 · 7.개선 전/후 · 8.사람 메모 · 9.요청)
「개선 전/후」는 --vs 를 준 때만 붙는다 — 전/후 대조는 «결과 비교» 의 일이다.

자체 검사 t_feedback_observations 는 다음 커밋(공용 파일)에 함께 들어간다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

### 2026-08-26 — 웹앱: 로컬 앱을 인터넷으로 여는 터널 + 요청 인증

정적 사이트(docs/)는 읽기 전용이라 삭제·실행·보정을 남이 하려면 서버 자신을
열어야 한다. 서버는 ros2 run 을 subprocess 로 띄우므로 인증이 필수 —

- server.py: TB_WEB_TOKEN 설정 시 모든 요청에 HTTP Basic 인증(stdlib base64/hmac,
  의존성 0). 미설정 시 기존처럼 127.0.0.1 로컬 전용·무인증(회귀 없음).
  --host 0.0.0.0 을 토큰 없이 띄우면 거부(fail-safe)
- 터널은 단일 JOB 슬롯이 아니라 자기 슬롯 — 터널 도는 동안 테스트도 돌아간다.
  start_tunnel 은 토큰 없거나 cloudflared 없으면 시작 거부
- app.js: 「도구」 탭 «터널» 패널 — 켜기/끄기·공개 URL·토큰 안내
- selftest.t_web_auth: 인증 판정 검사
- README §11.8

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### 2026-08-26 — 사이트: 별표한 실행 7건으로 결과 갱신

lane_night_b · stopline_night_b · inject · clean · t042 ·
stopline_night_a · regression — 웹앱에서 핀 꽂은 것 전부.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### 2026-08-26 — 웹앱: 결과만 보는 정적 사이트로 내보내기 (GitHub Pages)

읽기 화면은 전부 app.js 의 get() 하나를 지나가고 서버는 runs/ 를 JSON 으로
옮길 뿐이라(판정은 이미 summary.json 에 있다), 그 서버 함수를 그대로 불러
응답을 파일로 굽는다 — 화면 코드를 한 벌 더 쓰지 않는다.

- tb/publish.py: 서버 함수 재사용해 docs/ 로 굽기. 홈 경로 스크럽·.nojekyll
- web/app.js: window.STATIC 감지 → 서버 필요한 탭 숨기고 "왜 없는지+직접
  돌리는 법" 안내. 읽기 전용 배너. «사용 안내» 에 설치 가이드
- tb.run publish 서브커맨드 + 「도구」 탭 버튼(COMMANDS)
- selftest.t_publish_names: publish.py 의 api_path() ↔ app.js 의 apiURL()
  이름 규칙을 양쪽 대조 (어긋나면 사이트가 404 로만 열린다)
- .gitignore: runs/ → /runs/ — 앵커 없으면 docs/api/runs/ 까지 먹어
  배포 사이트만 404 나던 것을 고침
- README §11.7 · CLAUDE.md 함정

run.py·server.py·selftest.py 에는 진행 중이던 이름정합 린터·calib 드리프트
작업이 같은 파일에 들어 있어 함께 딸려 온다.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### 2026-08-19 — 검증·빌드: 이름 정합 린터 · 워크스페이스 빌드를 웹에서 · 판정이 조용히 사라지던 두 곳

★판정이 조용히 사라지는 것★ 을 막는 것이 이번 변경의 축이다. 셋 다 결과가
초록으로 나오는데 실은 판정이 없거나 틀린 경우였다.

1. 이름 정합 린터 (tb/lint.py, 신규)
   checks 의 신호 이름을 하나 잘못 적으면 _stat_value 가 None 을 내고 run_checks 는
   ok:None(⚠ 값 없음)으로 남긴다 — 실패가 아니다. 그래서 판정 하나가 사라진 채
   리포트가 초록이고, 그걸 아는 시점이 YOLO 로딩 + 런을 전부 돌린 뒤였다.
   이미 있었지만 아무도 안 쓰던 expr.names()(docstring: "계약 검증에 쓴다") 위에
   얹었다. 계약↔시나리오의 모든 이름 참조와 stat 어휘, 조건식 문법까지 대조한다.
   doctor 의 「이름 정합」 · 웹 실행 화면의 warn · selftest 세 곳에서 같은 함수가 돈다.
   저장소의 계약 3 · 시나리오 8 은 전부 깨끗하다(오탐 0).

2. reanalyze 가 판정을 통째로 잃던 것 (실측 13개 → 0개)
   checks 는 시나리오에만 있는데 --scenario 를 빼면 못 읽었다. 원인은 런이 자기가
   쓴 시나리오를 기억하지 않는 것이라, 이미 같은 이유로 있던 contract_file 을 본떠
   meta 에 scenario_file 을 남기고 scenario_of_run() 을 붙였다. 변형의 checks 도
   함께 센다. 못 찾으면 조용히 넘기지 않고 경고한다.

3. 진행률 분모 (player.progress_total)
   limit 은 이미 「투입 장수」인데 stride 로 또 나눴다 — stride 2 · limit 200 이면
   분모가 100 이 되어 진행률이 200% 까지 갔다. 표시만의 문제가 아니다: 합성
   오버레이의 '다가오는 속도' 가 이 분모를 쓰므로 목업이 두 배로 빨리 커진다.
   기존 시나리오는 전부 stride 1 이거나 오버레이가 없어 베이스라인 값은 안 바뀐다.

★웹에서 안 되던 한 칸★
   tb.run build 를 더했다. 빌드할 곳과 패키지는 계약(workspace: · nodes[].package)이
   정하므로 tb/*.py 에 워크스페이스 이름은 여전히 0글자다. --packages-select 가
   아니라 -up-to 인 이유는 exec_depend 로 걸린 패키지가 안 서면 실행이 실패해서다
   (white1 → nxde. 실측: 2개 패키지가 선다). 끝나면 stale 을 되재서 확인한다.
   web/server.py 의 COMMANDS 에 한 항목을 넣으니 renderTools 가 제네릭이라
   폼·명령줄 미리보기·백그라운드 실행·로그가 JS 0줄로 딸려 왔다. 실행 화면에도
   버튼을 뒀다. selftest 의 t_commands_wired 가 허용 목록과 CLI 의 어긋남을 잡는다.

★사실관계 정정 — --symlink-install★
   문서가 "파이썬 모듈은 사본이라 고치면 재빌드해야 한다"고 쓴 곳이 있었는데
   틀렸다. build/<pkg>/<pkg> 가 src/<pkg>/<pkg> 를 가리키는 심볼릭 링크라
   ros2 run 이 읽는 파일이 곧 편집하는 파일이다(import 로 확인, 두 워크스페이스 동일).
   파이썬만 고쳤으면 빌드가 필요 없고, 필요한 때는 넷뿐이다 — setup.py 의
   entry_points · package.xml 의 의존성 · C++ · 처음 한 번.
   같은 이유로 stale_sources 경고도 둘로 갈랐다(needs_rebuild): 예전에는 코드를
   고칠 때마다 "지금 돌리면 고치기 전 코드가 돈다"는 거짓 경보가 떴다.

그 밖에
 - discover --out 이 있는 파일을 덮어쓰지 않는다 (workspace: 와 주석이 날아갔다)
 - new_scenario 빈 틀에 요약 지표 checks 2줄 (빈 틀은 판정이 0개라 늘 초록이었다)
 - README: §4.5 start·limit·stride 의 단위, §7.2 고치고 다시 돌리기,
   §3.5 에 「어떤 순서로 쓰나」. 웹앱 «사용 안내» 맨 위에도 같은 흐름을 넣었다

자체 검사 34개 통과 · flake8 통과. 이 커밋에는 세션 이전부터 커밋되지 않고 있던
«새 시험 시작» 마법사 작업(web/app.js 등)도 같이 담긴다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>

### 2026-08-19 — 웹앱: 정지선 시험 · 새 영상으로 갈아타는 마법사 · 카메라 설정을 워크스페이스에게 묻기

노트북 영상은 실차와 카메라 위치·화각이 다르므로 그것으로 white1 을 고치는 것은
의미가 없다(지시). 그래서 ★새로 찍은 영상으로 갈아타는 길★ 을 웹앱에 만들고,
지금 있는 영상용 시나리오는 지그임을 이름으로 못 박았다. white1 은 고치지 않았다.

시나리오 정리
  stopline_{detect,approach,regression} → stopline_demo_* 로 이름 변경
    머리말에 "★track_record.mp4 전용 지그★ — 실차 판정용이 아니다" 를 박았다.
    남겨 두는 이유는 둘 — ① 테스트베드 자기 회귀 ② README §12 발견 사항 재현
  scenarios/stopline_field.yaml 신설 — ★새로 찍은 영상용★
    합성 목업 없음 · 근접도/확정 문턱은 노드 기본값 · 시간 기준은 절차서 값
    판정 23개(단계 0·1·4). 두 문턱은 단계 2 실측 뒤에 채운다

웹앱 «새 시험 시작» (신규 탭)
  네 단계를 화면이 들고 있다 — 영상 등록 → 시나리오(본 떠서) → 카메라 맞추기 → 돌리기.
  사람이 순서를 기억하면 ★3단계를 빼먹고 남의 영상 값으로 판정★ 하게 된다.
  · 1단계에서 "실차 카메라로 찍은 영상인가" 를 묻고 시나리오 머리말에 적어 둔다
  · config.clone_scenario — 있는 시나리오를 ★본으로 떠서★ 영상만 갈아 끼운다.
    판정과 그 근거 주석을 그대로 물려받는다(빈 틀을 만드는 new_scenario 와 다르다)

카메라 설정 ⓐ 읽기 — 노드에게 직접 묻는다
  tb.run params : 노드를 한 번 띄우고 ros2 param dump → runs/_params/<계약>.yaml
    소스를 파싱하거나 import 하지 않는다(결합 규칙 유지). 실측 67개 파라미터.
    camera_model.py 의 기본값이 바뀌면 다음 dump 에서 저절로 따라온다.
  캘리브 값 우선순위 = 시나리오/local params → 이 캐시 → 계약의 default
  «워크스페이스 기본값 불러오기» / «다시 읽기» 버튼
  런마다 params_actual.yaml — 그 런에서 노드가 ★실제로 들고 있던★ 값

카메라 설정 ⓑ 쓰기 — 실차로 되돌린다
  «실차 명령으로 내보내기» → ros2 launch 인자 + --params-file 용 yaml.
  ★워크스페이스 파일은 고치지 않는다★ (계약의 deploy.launch 가 런치 이름을 준다)
  ⚠️ 맞춘 값은 '맞출 때 쓴 영상의 카메라 설정' 이라는 경고를 화면·파일에 남긴다

화면이 계약을 따라간다 (박아 둔 것을 계약으로)
  frame_presets: / frame_columns: — 프레임 탐색의 프리셋과 표의 열.
    전에는 app.js 에 차선용 8개가 박혀 있어 정지선 런에서는 표가 전부 '—' 였다.
    정지선 프리셋 8개: 정지선 검출 / 미검출 / 대기 중(0단) / 1단 / 2단 /
    RED 인데 정지선 없음 / 범퍼선 도달. 기본 조건도 계약이 정한다.
  요약 탭에 「단계 전이」·「노드 로그」 패널 (report.md 에만 있던 것)
  '차선 인식률' 카드는 플래그를 선언한 계약에서만
  단어집에 정지선 12항목 (sl_px · B1/B2 · 대기 · 놓침 · 근접도 · 범퍼행 …)

realtime 은 기준(baseline)을 등록하지 않는다
  유실 패턴이 기계마다 달라 프레임별 비교가 성립하지 않는다(실측 87프레임 중 1프레임
  어긋나 max|Δsl_px| 84px). 프레임별 회귀는 lockstep 의 일이고 realtime 은 판정으로
  본다. approach·regression 기준을 지우고 그 이유를 시나리오·README 에 적었다.

자체 검사 27개 통과 (웹앱 JS 문법 · contract_ui · clone_scenario 추가) · 린트 통과

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>

### 2026-08-19 — 정지선 앞 정지 시험 — white1 계약·시나리오 셋과 「언제 물었나」 판정

gold_ws/src/white1/STOPLINE_TEST.md 의 단계 0·1·3·4·5 를 영상으로 돌린다.
워크스페이스는 복사하지 않았다 — 계약 하나를 더 두고 workspace: 한 줄만 gold_ws
로 가리킨다(README §11 의 절차 그대로). tb/*.py 에는 white1 의 토픽·파라미터
이름이 여전히 한 글자도 없다.

새 파일
  contracts/white1_stopline.yaml   결합점. 신호 10 · 로그이벤트 15 · 캘리브 대상 6
  scenarios/stopline_detect.yaml   단계 1 ★관문★ (lockstep, 변형 2개)
  scenarios/stopline_approach.yaml 단계 3·4 판정표 (realtime 0.25배속)
  scenarios/stopline_regression.yaml 단계 5 회귀 (변형 4개 = 5-1·5-2·5-3·5-7)
  assets/red_light_mock.png        합성 자극용 목업 신호등 + 만드는 스크립트

일반 기능 (워크스페이스 무관)
  · 구간 체크   {where: …, stat: count|frac|runs|run_max_frames|run_max_s}
                signal 을 함께 주면 그 조건에 맞는 행만 골라 신호 통계
  · 전이 체크   {event: "sig:0->1", stat: count|frame|t_s|at:<신호>}
                {signal: …, stat: decreases|increases} 로 단조성
  · 로그 체크   계약의 log_events: (노드+정규식) → {stat: "log:<이름>"}
                토픽에 없는 근거(기동 배너·개입 사유)를 판정에 넣는다
  · events:     전이 표를 리포트·웹에 그대로 (언제 무엇이 얼마였나)
  · hold_initial:  변화분만 발행되는 신호의 ★첫 전이★ 를 잃지 않는다
  · scene_fps   초 단위 판정을 프레임÷영상fps×배속 으로 (노드가 겪은 시간)
  · overlay:    합성 자극 — 목업 그림을 화면에 얹는다(위치·크기·구간)
  · prime:      첫 추론(3.3초)을 측정 구간 밖에서 끝낸다 — realtime 필수
  · sync_settle_ms:  동기 토픽보다 늦게 나오는 값을 그 프레임 행에 붙인다
  · 변형이 params: 와 checks: 를 가진다 (대조군은 섭동이 아니라 파라미터다)
  · 캘리브 kind bev_row / bev_dist — BEV 위의 기준선과 거리 문턱
  · undistort: {file: …} — 노드가 읽는 캘리브 yaml 을 그대로 읽는다(옮겨 적지 않음)
  · render.bev_dist — 디버그 토픽이 없는 노드의 화면을 테스트베드가 그린다

고친 것
  · render·harvest·reanalyze 의 --scenario 기본값(regression.yaml)을 없앴다.
    런이 자기 계약을 기억하게 하고(meta.contract_file) 그것을 먼저 쓴다 —
    전에는 다른 계약의 런을 그리면 조용히 빈 그림이 나왔다.
  · 런 메타에 그 계약의 노드 파라미터만 남긴다. local.yaml 이 계약 여럿을
    함께 쓰므로, 다른 계약용 항목까지 적으면 회귀가 '조건이 다르다'고 오경고했다.
  · doctor 도 같은 이유로 local.yaml 의 남의 노드는 실패로 보지 않는다.
  · 섭동 대조 표를 계약의 compare_signals 기반으로 (차선 신호가 박혀 있었다).

찾은 것 (README §12)
  ① 왜곡보정을 켜면 정지선 검출이 0% 다 (보정 OFF 69.4%, p95|Δsl_px| 94.5px)
  ② 프레임이 0.5~3초 끊기면 내부 상태가 30Hz 로 왕복한다 (해제 76 / 재체결 75)
  ③ 1단을 물면 '신호등 코앞' 상한이 40px → 28px 로 내려간다(히스테리시스 누수)
  ④ 절차서 단계 1 의 sl_gate_red_s:=99999 한 줄로는 관문이 안 열린다

자체 검사 24개 통과 · 기존 워크스페이스 회귀 PASS(값 동일)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>

### 2026-08-19 — 웹앱: 영상 보며 캘리브레이션 · 터미널 명령 전부를 「도구」 탭으로

■ 카메라 보정 — 한 장짜리 미리보기에서 편집기로

기존 화면은 프레임 번호를 손으로 치는 정지 화면 하나였고, 계약이 선언한
rect 타깃(lane_roi·tl_roi)은 그리지도 고치지도 못했으며, 맞춘 값을 저장할
방법이 없었다. 이제 CLI(`tb.calibrate`)가 하는 일을 전부 한다.

  - 재생/정지·슬라이더·±1/±30 프레임으로 맞출 장면을 찾는다
  - IPM 4점은 드래그(가장 가까운 점), ROI 는 드래그로 새로 그리거나
    모서리를 잡아 조정. 단축키는 CLI 와 같다(space , . [ ] 방향키 1~4 g u r)
  - local.yaml 이나 시나리오에 바로 저장 (주석 보존)
  - 「노드와 대조」가 `tb.calibrate --verify` 를 그 자리에서 돌린다

편집 대상 탭은 계약의 calibration.targets 에서 자동으로 나온다. 현재 값
읽기와 저장 형식은 tb.calibrate.Calib 를 그대로 재사용하므로, 계약에 ROI 를
하나 더 늘려도 웹 코드는 고치지 않고 CLI 와 어긋날 수도 없다.

★클릭 좌표가 6.6% 어긋나던 것을 고쳤다★ — 서버는 1400x450 JPEG 을 주는데
화면은 1400x480 으로 가정하고 패널 경계도 리사이즈 전 값(853, 실제 800)을
썼다. 「직접 맞추는」 화면에서 이건 치명적이다. 이제 서버가 표시 좌표계
기준으로 disp_w/disp_h/split_x/src_scale 을 주고 환산은 그것만 쓴다.

프레임마다 계약·시나리오 YAML 을 전부 다시 읽던 것(34ms)을 mtime 캐시로
바꿔 10.6fps → 22.5fps (HTTP 왕복 포함). 재생을 막고 있던 건 영상 처리가
아니라 파일 읽기였다.

■ params: 쓰기 (tb.config.set_params)

캘리브 값을 local.yaml/시나리오의 params: 에 주석을 보존하며 병합한다.
줄 단위로 갈아 끼우는 방식이라 파일 모양이 예상과 다르면 조용히 망가진다 —
실제로 `perception: {show_window: false, …}` 같은 흐름식 매핑에 키를 중복
생성해 show_window 를 날려 먹었다(YAML 은 중복 키를 에러로 보지 않는다).
그래서 흐름식을 블록식으로 펴서 병합하고, 저장 전에 ★원본의 모든 키가 그대로
살아 있는지 대조★해 하나라도 사라지면 파일을 건드리지 않는다.

to_params() 가 float32 를 float() 로 넓혀 614.4 를 614.4000244140625 로
저장하던 것도 고쳤다(CLI 의 `s` 저장에도 있던 버그다).

■ 도구 탭 — 터미널에서 되는 것은 웹에서도 된다

명령 12개 전부. 고르면 그 명령이 받는 인자만 물어보고, 실제로 나갈 명령줄을
보여 준 뒤 실행한다. 기존 화면에도 빠져 있던 인자를 고급 옵션으로 채웠다
(실행 --contract/--variant/--baseline/--domain/--watch/--keep-going,
 비교 --contract/--scenario, 점검 discover 전체, 경로 영상, 프레임 추출).

★화이트리스트와 입력 폼이 한 곳에서 나온다★ — server.py 의 COMMANDS 명세
하나가 둘의 원본이다. 화면에 인자를 따로 적어 두면 CLI 에 인자가 늘어도 웹은
모르고, 반대로 화면에만 있는 인자는 서버가 거부한다. 그래서 화면 코드에는
인자 이름이 한 글자도 없다.

인자는 타입별로 검사한다(int/float/이름/경로/실행이름/선택지). 셸을 거치지
않으므로(shell=False) 조건식의 `>` `<` 는 통과시키고 `;` `|` `` ` `` `$` 는
막는다 — `< >` 까지 막았더니 문서에 적힌 `--where "int(flags) % 4 >= 2"` 가
통째로 거부됐다.

실행 패널의 오진 둘도 고쳤다. 명령 이름이 두 번 찍히던 것(reanalyze
reanalyze …)과, 「결과 보기」가 엉뚱한 런을 가리키던 것 — 디렉터리 mtime 은
안의 파일을 고쳐도 안 바뀌므로 "가장 최근 폴더"가 답이 아니다. 대상 런을
명령에서 기억하고, 진행률·라이브는 런을 새로 만드는 명령에만 붙인다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>

### 2026-08-19 — 웹앱: 같은 화면을 별도의 창으로 (`tb.run app`)

주소를 외워 브라우저에 치고 탭 스무 개 사이에서 찾는 것은 도구가 아니다.
크로미움 계열의 앱 모드(`--app=`)로 주소창·탭이 없는 전용 창을 띄운다.

UI 코드는 한 줄도 다르지 않다 — 같은 서버, 같은 app.js 다. 창을 여는 방식만
다르므로 영상 재생·플롯이 지금 검증된 그대로 동작한다.

Electron 도 Qt 도 쓰지 않는다. 이미 깔려 있는 브라우저가 프레임워크 없이 해
주는 일이라, pip 설치 0 이라는 이 저장소의 약속을 창 하나 때문에 깨지 않는다.
브라우저가 하나도 없으면 안내하고 기본 브라우저로 내려간다.

전용 프로필은 runs/ 가 아니라 ~/.cache/cam-testbed/window/ 에 둔다.
수십 MB 짜리 브라우저 프로필은 실행 결과가 아니고, 거기 섞이면
「실행 기록」의 의미도 `du -sh runs/` 도 흐려진다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>

### 2026-08-18 — 용어: 워크스페이스 전체 표기 통일

지난 커밋에서 웹앱 문구만 「받는 쪽」으로 바꿨던 것을 나머지 코드·계약·
시나리오·문서까지 넓히고, 같은 것을 두 가지 말로 부르던 곳을 맞췄다.

- 하류 → 받는 쪽, 소비자 → 받는 쪽, 상류 → 보내는 쪽
- 드롭(률) → 유실(률).  `drop_rate` 같은 식별자는 그대로 둔다
- 문턱(값) → 임계값.  계약의 게이트 단계 이름 `conf 문턱` → `conf 임계값`
- 웹 서버 시작 줄의 「런 N개」 → 「실행 N개」 (웹앱 표기와 통일)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>

### 2026-08-18 — 용어: 「하류」를 「받는 쪽」으로 통일

웹앱 화면에 쓰이던 「하류」가 어색해 전부 「받는 쪽」(노드를 가리킬 땐
「받는 쪽 노드」)으로 바꿨다.

- web/app.js: 실행 목록 툴팁, 지연 카드, θ 품질 제목, 프레임 탐색 프리셋,
  사용안내 판정 순서, 단어집 항목과 「그 밖」의 표제어
- tb/analyze.py, tb/feedback.py: 리포트·피드백 탭에 그대로 뜨는 생성 문구.
  표기가 섞이지 않게 「소비자 게이트 통과율」 제목도 함께 맞췄다.

계약 파일과 나머지 코드 주석의 「하류」는 화면에 안 나오므로 그대로 뒀다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>

### 2026-08-18 — README: 새 노트북으로 옮기는 절차 (§11.5) 추가

이식할 때 고치는 곳이 코드가 아니라 데이터 파일 3개뿐이라는 것과,
그 3개가 각각 무엇인지를 단계별로 적었다.

- 0~6단계: 런타임 전제 → 클론(gh 설치 포함) → colcon build →
  local.yaml → 계약의 workspace: → doctor → 첫 런과 기준 재등록
- ★베이스라인은 머신을 넘지 못한다★ — 추론 백엔드가 바뀌면
  compare_tol(flags 0.0)에 걸려 첫 회귀는 DIFF 가 정상이다.
  코드 회귀가 아니므로 그 머신 기준을 새로 등록한다.
- 절대경로가 박힌 3군데와 grep 확인법
- 새 노트북 + 새 워크스페이스를 함께 붙일 때는 §11 을 얹는다
- 자주 걸리는 것 표

§3 에서 §11.5 로 가는 링크를 하나 걸었다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>

### 2026-08-18 — 카메라 인지 테스트베드 초기 커밋

계약(contract) YAML 하나로 대상 워크스페이스를 붙이는 구조.
tb/ 코드에는 대상 패키지의 토픽명·필드 배치가 들어 있지 않다.

- tb/       테스트베드 엔진 (실행·분석·주입·캘리브·렌더)
- web/      로컬 웹앱 (실행·리포트·등록 화면)
- contracts/ 계약 — 워크스페이스를 아는 유일한 통로
- scenarios/ 무엇을 어떻게 돌릴지 (회귀·강건성·실시간)
- cases/    신호 주입 케이스 (변환 수학 격리 검증)
- baselines/ 회귀 기준

머신마다 다른 것(local.yaml)과 실행 결과(runs/)는 버전관리에서 뺀다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>

