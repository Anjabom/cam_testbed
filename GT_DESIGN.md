# GT(Ground Truth) 대조 — 설계 문서 (초안, 미구현)

> **상태**: 설계만. 코드는 아직 없다. 실측 데이터(영상+GPS 동시 주행)도 아직 없다.
> 검토·승인 후 구현한다. 승인되면 이 문서의 핵심은 README § 로 편입하고 이 파일은 지운다.
>
> **왜 이 문서가 있나**: "GPS rosbag 을 쓰면 더 정확한 테스트가 되는가"에 답하고,
> 그 구현을 착수 전에 확정하기 위해서다. 결정된 방침 — ① 정지 위치·횡오차(cte)를
> **둘 다**, ② 정렬은 **자동(상호상관) + 수동 보정**, ③ 지금은 **설계만**.

---

## 0. 한 줄 요약

대상 워크스페이스는 이미 **RTK-GPS 참값**을 만들고(`driving.py` 의 `signed_cte()`,
`record.py` 의 통합 CSV) 카메라 신호와 **같은 CSV 행**에 기록한다. 이 CSV 를 런의
`signals.csv` 에 `gt_*` 열로 조인하면, GT 없이 재던 지표(회귀·불변식·섭동)에 더해
**"카메라가 실제와 얼마나 맞는가"** 를 처음으로 잴 수 있다.

**경계는 그대로다.** `cte_m`·`sl_px`·`gps_quality` 같은 이름은 **계약(`contracts/*.yaml`)
에만** 쓴다. `tb/gt.py` 와 `web/*` 에는 워크스페이스 고유명이 한 글자도 안 들어간다.

---

## 1. 무엇을 GT 로 삼는가 — 두 갈래

성질이 다른 두 종류를 잰다. 둘 다 이번에 구현한다.

### (A) 카메라 신호 정확도 — 횡오차 cte
- **측정값**: 카메라가 낸 `cte_near_m` / `cte_rear_m` (계약 `white_camera`)
- **참값**: `driving.py` 가 GPS 위치와 매핑 경로로 계산한 `/drive_diag[0]` = `cte_m`
  - `signed_cte()` 는 **카메라를 전혀 안 본다** — GPS(`self.x, self.y`)와 웨이포인트만
    쓴다. 그래서 카메라의 독립 참값 자격이 있다.
  - 부호 규약: `+` 왼쪽 / `−` 오른쪽 (경로 진행방향 기준)
- **재는 것**: "카메라가 본 횡오차가 GPS 로 잰 실제 횡오차와 일치하는가"

### (B) 시스템 정지 정확도 — 정지 위치
- **측정값**: 실제 정지한 순간의 차량 위치 (`fix_lat/lon`)
- **참값**: 정지선의 GPS 좌표 (1회 측량, 아래 §3)
- **재는 것**: "차가 정지선 앞 몇 m 에 실제로 섰는가" — 카메라 `sl_px` 픽셀이 아니라
  **end-to-end 정지 결과**. 계약 `white1_stopline`.
- ⚠️ 이건 `sl_px` 정확도가 **아니다**. `sl_px`(픽셀) → 미터 변환은 검증 대상인
  IPM(`px2m`)을 거치므로 그걸로 GT 를 만들면 순환논법이다. 정지 위치는 IPM 을 우회해
  **GPS 만으로** 잰다.

### GT 가 안 되는 것 (명시)
- `lane_width_m` — 차선폭은 GPS 궤적에 없다. GT 불가. 계속 불변식으로만 판정.
- `theta_deg` 절대 정확도 — `/drive_diag[1]` = `heading_err_deg` 로 **부호·추세**는
  대조 가능하나, 카메라 θ(차선 기준)와 GPS heading(궤적 기준)의 기준선이 달라
  절대 일치는 기대하지 않는다. (A)의 보조 지표로만 둔다.

---

## 2. 왜 이제 되나 — RTK 품질이 전제

지난 판단("영상만으로는 미터 GT 불가")은 카메라만 있을 때의 얘기였다. **RTK 가 붙으면
미터 참값이 생긴다.** 단, RTK 품질에 전부 걸려 있다 (`gps.py` 헤더 실측):

| `gps_quality` | 상태 | 실제 오차 | GT 자격 |
|---|---|---|---|
| 4 | RTK **Fixed** | **0.02 m** (악조건 0.10) | ✅ cte 판정(0.1m)보다 5배 정밀 |
| 3 | RTK Float | **2.0 ~ 4.0 m** | ❌ 참값이 측정값보다 20배 부정확 |
| 1·2 | SPS·DGPS | 수 m | ❌ |
| 0 | 없음 | — | ❌ |

**품질 게이트는 선택이 아니라 필수다.** Float 을 GT 로 쓰면 멀쩡한 카메라를 불합격시킨다.
게이트 신호는 이미 CSV 에 있다: `gps_quality`, `gps_sigma_m`, `gps_pos_ok`, `gps_mode`.
1차 기준: **`gps_quality == 4`** (또는 `gps_sigma_m < 0.30`, gps.py 의 `RTK_FIXED_SIGMA_M`).

---

## 3. 데이터 요건 — 지금 없는 것

**구현 전에 사람이 해야 하는 일.** 코드로 대신할 수 없다.

### 지금 상태 (2026-08-20 확인)
| | 날짜 | 위치 | 문제 |
|---|---|---|---|
| 영상 | 2026-07-20 | `~/track_record.mp4` 등 | GPS 없이 찍음. `track_a` 는 "실내 모형"(GPS 불가) |
| 경로 CSV | 2026-08-11 | `white806/gps_data/route_*.csv` | 다른 주행. 영상과 무관 |
| `white1/gps_data`·`ros2bag` | — | 없음 | 통합 CSV 자체가 없음 |

**영상과 GPS 를 같이 찍은 주행이 아직 없다.** → GT 구현의 0단계는 코딩이 아니라 수집이다.

### 수집 규약 (실외 주행 1회)
동시에 남긴다:
1. **카메라 영상** — 원본 해상도 1920×1080 (`perception` 의 undistort 맵이 그 크기 고정)
2. **`ros2 run white1 record` 통합 CSV** — 카메라 신호 + GPS 참값이 같은 행에
3. **그 주행에 쓴 `gps_data/route_*.csv`** — `cte_m` 이 이 경로 기준으로 계산됨

### (B) 정지 위치를 재려면 추가로
- **정지선 GPS 측량 1회**: 정지선 양 끝점의 lat/lon. 별도 장비 없이 차량 GPS 를
  정지선 위에 세워 `/fix` 를 몇 초 읽어 평균해도 된다(RTK Fixed 상태에서).
- 계약에 `stop_line: {p1: [lat, lon], p2: [lat, lon]}` 로 박는다(절대좌표라 계약이
  이 값의 정당한 소유지다 — 워크스페이스별 1개).

### RTK Fixed 유지가 관건
Float 구간은 판정에서 제외되므로, 주행 중 Fixed 비율이 낮으면 데이터가 반쯤 버려진다.
수집 시 `gps_quality` 를 눈으로 확인한다. 하늘 트인 곳, 기준국 정상.

---

## 4. 정렬 — 영상 프레임 ↔ CSV 시각

테스트베드는 영상을 프레임 단위로 재생하고(`signals.csv` 의 행 키 = 프레임 번호),
record CSV 는 벽시계 20Hz 샘플(`t_rel` 초)이다. 둘을 맞춰야 같은 순간을 대조한다.

### 시간 기준
테스트베드의 초 = `프레임 ÷ scene_fps`(`analyze.scene_fps`, 영상fps×배속). CSV 는 `t_rel`.
그래서 매칭은 **한 개의 오프셋 `offset_s`** 로 표현된다:

    csv_t_rel  ≈  frame / scene_fps  +  offset_s

### 자동 (상호상관)
- **원리**: 같은 영상을 처리하면 카메라 노드는 같은 신호를 낸다. 실주행 CSV 의 `sl_px`
  시퀀스와, 테스트베드가 그 영상을 재생해 얻은 `signals.csv` 의 `sl_px` 시퀀스는
  **같은 파형**이다(샘플레이트만 다름). 두 파형의 상호상관 최대점이 `offset_s`.
- **정렬 신호는 계약이 고른다**(`align.signal`) — 양쪽에 다 있고 잘 변하는 것.
  `sl_px` 가 1순위(정지선 접근에서 크게 변함). cte 시나리오에서는 `cte_m`.
- **함정**: 신호가 거의 안 변하는 구간(직선 정속)은 상관이 평평해 정렬이 약하다.
  이럴 때를 위해 수동 보정이 있다.

### 수동 보정
- 자동으로 찾은 `offset_s` 를 **계약/런 설정으로 덮어쓸 수 있다**.
- 사람이 "영상 N프레임의 사건 = CSV t_rel M초"를 한 쌍 지정하면 offset 이 나온다.
- 웹앱에서: 정렬 결과(겹친 두 파형)를 그려 주고, 슬라이더로 offset 을 밀어 맞춘다.
  자동값이 기본, 사람이 손대면 그 값이 이긴다.

### 재샘플
CSV(20Hz) → 프레임 시각으로 **최근접/선형보간**. GT 는 참값이므로 보간 오차는 작다.
프레임 시각과 CSV 표본이 `max_gap_s` 보다 멀면 그 프레임엔 GT 를 안 붙인다(결측).

---

## 5. 계약 스키마 — `ground_truth:` 블록

**모든 워크스페이스 고유명이 여기 모인다.** 워크스페이스 1개 = 계약 1개 = 이 블록 1개.

```yaml
# contracts/white_camera.yaml  — (A) cte 대조
ground_truth:
  source:
    format: wide_csv           # record.py 통합 CSV (헤더 있는 넓은 표)
    # 실제 CSV 경로는 런/시나리오가 준다(머신 의존) — 여기 절대경로 안 박는다
  align:
    by: xcorr                  # 자동 상호상관
    signal: sl_px              # 양쪽에 다 있는 정렬 마커
    max_shift_s: 5.0
    max_gap_s: 0.15            # 프레임과 CSV 표본이 이보다 멀면 결측
    # offset_s: 0.0            # (선택) 수동 고정. 있으면 자동을 무시
  trust:                       # 이 조건이 아닌 행은 GT 로 안 쓴다
    where: "gps_quality == 4"  # RTK Fixed 만
  columns:                     # GT 열 이름(테스트베드) ← CSV 열 이름(워크스페이스)
    gt_cte_m:       cte_m
    gt_heading_err: heading_err_deg
    gt_quality:     gps_quality
```

```yaml
# contracts/white1_stopline.yaml  — (B) 정지 위치
ground_truth:
  source: {format: wide_csv}
  align: {by: xcorr, signal: sl_px, max_shift_s: 5.0, max_gap_s: 0.15}
  trust: {where: "gps_quality == 4"}
  columns:
    gt_lat: fix_lat
    gt_lon: fix_lon
  stop_line:                   # 정지선 GPS 측량 (절대좌표 → 계약이 소유)
    p1: [36.9676000, 127.8724000]   # ← 실측으로 채운다
    p2: [36.9676100, 127.8724100]
  bumper_offset_m: 0.35        # GPS 안테나 → 앞범퍼 거리
```

`tb.contract.Contract` 에 `ground_truth` 파싱을 추가한다(`self.ground_truth = ...`).
없으면 GT 기능 전체가 조용히 꺼진다(기존 런은 영향 없음).

---

## 6. `tb/gt.py` — 새 모듈 하나 (~150줄 예상)

하는 일은 넷뿐. **여기에 워크스페이스 이름은 없다** — 전부 `contract.ground_truth` 에서 온다.

```
load_gt(csv_path, contract)        CSV → 표(열 이름은 계약의 columns 로 매핑)
align(frame_signal, gt_signal, cfg)  상호상관 → offset_s (또는 계약의 고정값)
join(signals_rows, gt_table, offset, cfg)
                                   signals.csv 각 프레임에 gt_* 열 추가
                                   (재샘플 + trust.where 게이트 + max_gap 결측)
stop_error(gt_table, contract)     (B) 정지 순간 위치 → 정지선까지 서명거리[m]
```

- 좌표 변환(lat/lon → 로컬 평면 m)은 `mapping.py` 와 같은 등거원통(`EARTH_R`) 식을
  쓴다 — 이미 `signed_cte` 가 그 평면에서 계산했으므로 일관된다.
- 정지선까지 거리: 점(차량)—선분(정지선) 서명거리. `signed_cte` 와 같은 외적 부호.

### 순수 함수로 떼어 자체 검사
`align`·`join`·`stop_error` 는 ROS·영상 없이 도는 순수 함수다 → `tb/selftest.py` 에
`t_gt_align`, `t_gt_stop_error` 추가. "여기가 틀리면 모든 GT 판정이 틀리는" 것들이다.
합성 파형으로 offset 을 심고 되찾는지, 정지선 부호가 맞는지 검사(실측 데이터 불필요).

---

## 7. 판정 어휘 — `_stat_value()` 에 추가

계약·시나리오가 신호 이름을 주므로 **새 판정 종류에도 워크스페이스 이름이 코드로 안 들어온다.**
기존 `{signal, stat}` 문법에 `vs:`(대조할 GT 열)를 더한다.

```yaml
checks:
  # (A) cte — 부호부터. 오프셋과 무관하게 즉시 유효
  - {signal: cte_near_m, vs: gt_cte_m, stat: sign_agree, min: 0.95,
     where: "gt_quality == 4",
     why: "부호가 뒤집히면 조향이 반대로 나간다"}
  # (A) cte — 크기. 상수 오프셋 제거 후 (카메라=차선기준, GPS=궤적기준이라 오프셋 있음)
  - {signal: cte_near_m, vs: gt_cte_m, stat: p95_abs_err, detrend: median, max: 0.15,
     where: "gt_quality == 4",
     why: "RTK Fixed 구간에서 카메라 cte 가 GPS 대비 15cm 안"}
  # (B) 정지 위치 — 계약이 stop_line 으로 계산한 값
  - {stat: "gt:stop_error_m", min: 0.0, max: 1.5,
     why: "정지선 넘지 않고(≥0) 1.5m 안에 선다"}
```

새 stat:
- `sign_agree` — 두 신호 부호가 같은 프레임 비율 (`vs` 필수)
- `abs_err` 계열: `p95_abs_err` / `max_abs_err` / `rms_err` (`vs` 필수)
- `detrend: median|mean` — 오차에서 중앙값/평균 오프셋을 빼고 잰다(옵션)
- `gt:stop_error_m` — summary 키. `tb/gt.stop_error()` 결과

조건식은 `tb/expr.py`(AST 화이트리스트)로만 평가 — 기존 규약 그대로, `eval` 안 쓴다.

---

## 8. reanalyze 호환 — 깨지 않는다

`raw.jsonl` 에는 원본 메시지가 그대로 남는다(테스트베드 성질). GT 조인은 **분석 시점**에
`signals.csv` 를 만들 때 CSV 를 옆에서 읽어 열을 더하는 것이라, `raw.jsonl` 을 안 건드린다.
→ **계약의 GT 임계값을 고친 뒤 `reanalyze` 로 과거 런을 다시 판정할 수 있다.**
GT CSV 경로만 그 런이 기억하면 된다(런 메타에 `gt_csv:` 저장).

---

## 9. 웹 노출 — 거의 무수정

판정은 엔진(`analyze`)이 하고 `summary.json` 의 `checks[].ok` 에 담긴다. 웹은 그걸
색칠할 뿐이므로 **(A)(B) 체크는 「요약」 탭에 다른 체크와 똑같이 자동으로 뜬다** —
`app.js` 수정 없이. (경계 규칙 ③: 임계값을 JS 에 다시 쓰지 않는다.)

**추가로 원할 때만**(선택, 이번 범위 밖):
- 「시각화」 탭에 cte 측정 vs GT 겹친 시계열 + 산점도
- 정렬 화면: 두 `sl_px` 파형 겹쳐 그리고 슬라이더로 offset 수동 보정
- 서버가 `contract_ui` 로 GT 존재를 알려 탭을 조건부 표시

---

## 10. 미결·함정

- **cte 오프셋의 정체**: 카메라 cte(차선 중심 기준)와 GPS cte(사람이 몬 궤적 기준)는
  기준선이 다르다. 사람이 차선 중앙으로 정확히 몰았으면 오프셋≈0, 한쪽으로 치우쳐
  몰았으면 그만큼 상수 오프셋. `detrend: median` 이 이걸 흡수하지만, **오프셋이 크면
  "사람이 치우쳐 몰았다"는 신호이기도 하다** — detrend 전 원오프셋도 리포트에 남긴다.
- **정렬 신뢰도**: 상호상관 최대점의 뾰족함(peak sharpness)을 리포트에 낸다. 평평하면
  "이 주행은 자동 정렬이 약하다 → 수동 보정 권장" 경고.
- **DEGRADED 구간**: `gps_mode==1`(DR 융합)은 `gps_quality` 가 4여도 위치가 예측값이다.
  1차엔 `gps_quality==4` 만 믿고, 필요하면 `trust.where` 에 `and gps_mode==0` 추가.
- **20Hz vs 30fps**: CSV 가 영상보다 성기다. 급변 구간(정지 직전)에서 보간 오차가
  가장 크다 — 정지 위치는 보간이 아니라 **정지 순간의 실제 표본**을 쓴다(§6 stop_error).
- **임계값을 느슨히 하지 않는다**(경계 규칙 ⑥). 15cm·1.5m 는 초안값이다. 실측을 보고
  근거를 대서 조정하되, 통과시키려고 재는 쪽(detrend·게이트)을 손보지 않는다.

---

## 11. 구현 단계 (승인 후)

| 단계 | 내용 | 데이터 필요 |
|---|---|---|
| 0 | **수집**: 실외 RTK 주행 1회 (영상+record CSV+정지선 측량) | — (사람) |
| 1 | `Contract` 에 `ground_truth` 파싱 + 자체검사 | 불필요 |
| 2 | `tb/gt.py`: load/align/join/stop_error + `t_gt_*` 자체검사 | 불필요(합성) |
| 3 | `analyze` 에 `vs`/`sign_agree`/`abs_err`/`detrend`/`gt:` stat | 불필요 |
| 4 | 계약에 실제 `ground_truth` 블록 + 정지선 좌표 | §0 수집분 |
| 5 | 실측 런으로 임계값 근거 잡기 | §0 수집분 |
| 6 | (선택) 웹 시각화·수동 정렬 슬라이더 | §0 수집분 |

**1·2·3 단계는 데이터 없이 지금 짤 수 있다**(합성 파형으로 자체검사). 4·5 는 수집이 선행.

---

## 부록 — 확인된 사실 (2026-08-20, `gold_ws/src/white1/white1`)

- `driving.py signed_cte()` — GPS+웨이포인트로 횡오차. 카메라 독립. `/drive_diag[0]`=`cte_m`.
- `record.py` 통합 CSV 열(관련분): `t_wall, t_rel, sl_px, sl_y, tl_state, tl_near_metric,
  tl_red_far, sl_wait, cte_m, heading_err_deg, ego_x_m, ego_y_m, ego_heading_deg,
  ego_fix_ok, fix_lat, fix_lon, fix_status, fix_cov_xx, gps_lat, gps_lon, gps_quality,
  gps_sigma_m, gps_pos_ok, gps_mode, gps_resid_m, …` — 카메라 신호와 GPS 참값이 같은 행.
- `gps.py` — `gps_quality`: 0없음/1SPS/2DGPS/3RTK_FLOAT/**4RTK_FIXED**. `RTK_FIXED_SIGMA_M=0.30`.
- `mapping.py` — 순수 GPS 궤적 CSV(lat/lon 0.25m 간격). `EARTH_R=6378137.0` 등거원통.
- 정렬 마커 후보 `sl_px` 는 `signals.csv`(계약 white1_stopline)와 record CSV 양쪽에 존재.
