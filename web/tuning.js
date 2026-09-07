/* 맞출 것들 — ★이름은 전부 여기에만 있다★
 *
 * `app.js` 와 `geom.js` 에는 대상 워크스페이스의 파라미터 이름이 한 글자도
 * 없다. 화면은 ★종류★(quad / rect / scale / number / bev_row / bev_dist)만 알고,
 * 무엇을 어떤 이름으로 내보낼지는 이 파일이 정한다. 이름을 화면 코드에 박으면
 * 다른 카메라·다른 워크스페이스에서 화면이 통째로 비거나 엉뚱한 값을 쓴다.
 *
 * ★예전에는 이 자리가 계약(contracts/*.yaml)의 calibration.targets 였다★
 * [2026-09-06] 계약 4개(white_camera · white_vote · white1_stopline · black_vote)의
 * 대상들을 ★합쳐서 여기 한 벌★ 로 두었다 — 화면에서 프로필을 고르지 않는다.
 * 같은 것을 다른 이름으로 부르던 자리(ipm_src_pts ↔ bev_src_pts,
 * pixel_to_meter_bev ↔ bev_px_to_m)는 한 항목이 이름 여러 개를 들고 내보낸다.
 *
 * ★카메라(K·D)는 실측값, 맞추는 값들은 자리표시자다★
 * K·D 는 이 차의 값이라 기본으로 두는 편이 낫다(안 그러면 열 때마다 다시 넣어야
 * 한다). 사각형·ROI·척도·문턱은 영상마다 다시 맞추는 것이라 무난한 값에서
 * 시작한다. 지난번 값은 «불러오기» 로 JSON 을 열면 된다.
 *
 * 계약 파일의 calibration: 블록은 지우지 않았다. 시험 엔진(tb/run.py)이 런마다
 * ★실효 캘리브 값★ 을 기록하는 데 그걸 쓴다 — 지우면 「기하가 바뀐 것」을
 * 「노드가 회귀한 것」으로 잘못 읽게 된다.
 */
window.TUNING = {
  //  ── 카메라 ─────────────────────────────────────────────────────
  //  ★이 차의 실측값이다★ [2026-09-07] 노드가 소스에 박아 둔 어안 보정 모델과
  //  같은 값이어야 한다 — 다르면 화면이 보여 주는 BEV 와 노드가 만드는 BEV 가
  //  갈라지고, 여기서 맞춘 값이 실차에서 틀린다.
  //  화면의 «카메라 (크기 · K · D)» 를 펴면 여기 값을 그대로 고칠 수 있다
  //  (고친 값은 그 브라우저에 남고, «내보내기» 의 설정 JSON 에도 들어간다).
  camera: {
    size: [1920, 1080],
    K: [956.30137, 962.44368, 979.72871, 531.00886],   // fx fy cx cy
    D: [-0.276622, 0.050981, 0.000303, -0.001998, 0.0],  // k1 k2 p1 p2 k3
    alpha: 0.0
  },

  //  ── 맞출 것들 ──────────────────────────────────────────────────
  //  params: 어느 노드에 어떤 이름으로 나가는가. 이름이 여럿이면 전부 나간다.
  targets: [
    {
      id: 'bev_size', kind: 'size', label: 'BEV 크기',
      params: [['perception', 'bev_w', 0], ['perception', 'bev_h', 1]],
      value: [640, 480],
      hint: '세로를 키우면 더 먼 곳까지 편다. 차선 판은 640×480, 투표(lane_vote) 판은 640×1000 을 쓴다.'
    },
    {
      id: 'ipm_src', kind: 'quad', label: 'IPM 사각형',
      params: [['perception', 'ipm_src_pts'], ['traffic_light', 'bev_src_pts']],
      value: [640, 620, 1280, 620, 1860, 1070, 60, 1070],
      hint: '좌우 변을 ★차선 위에★ 올려라. 지면은 평면이라 그렇게 놓으면 BEV 에서 차선이 정확히 수직이 된다 — 수직이 아니면 사각형이 틀린 것이고, 격자가 그 자다.'
    },
    {
      id: 'lane_roi', kind: 'rect', label: '차선 ROI',
      params: [['perception', 'lane_roi_xmin', 0], ['perception', 'lane_roi_ymin', 1],
               ['perception', 'lane_roi_xmax', 2], ['perception', 'lane_roi_ymax', 3]],
      value: [0, 540, 1920, 1080],
      hint: '차선 세그멘테이션을 돌릴 영역. 하늘·차체를 빼면 빨라진다.'
    },
    {
      id: 'tl_roi', kind: 'rect', label: '신호등 ROI',
      params: [['perception', 'tl_roi_xmin', 0], ['perception', 'tl_roi_ymin', 1],
               ['perception', 'tl_roi_xmax', 2], ['perception', 'tl_roi_ymax', 3],
               ['traffic_light', 'tl_roi_xmin', 0], ['traffic_light', 'tl_roi_ymin', 1],
               ['traffic_light', 'tl_roi_xmax', 2], ['traffic_light', 'tl_roi_ymax', 3]],
      value: [0, 0, 1920, 600],
      hint: '신호등 검출 영역. 보통 화면 위쪽 절반. ★정지선 seg 는 ROI 없이 화면 전체로 돈다★ — 모델이 전체 화면으로 학습돼서 잘라 넣으면 종횡비가 달라진다.'
    },
    {
      id: 'px2m', kind: 'scale', label: '픽셀↔미터',
      params: [['perception', 'pixel_to_meter_bev'], ['camera_judgment', 'pixel_to_meter_bev'],
               ['traffic_light', 'bev_px_to_m']],
      value: 0.01,
      hint: 'BEV 에서 실측 길이를 아는 두 점을 찍어라(차선폭이 제일 쉽다). ★lane_vote 판에서는 이 값이 격자 해상도 자체다★ — 거리 d 가 픽셀 해상도로 풀린다.'
    },
    {
      id: 'lane_width', kind: 'number', label: '차선폭 [m]',
      params: [['perception', 'lane_width_m'], ['camera_judgment', 'lane_width_m']],
      value: 3.0, step: 0.01,
      hint: '좌우 차선 중심 간 거리. lane_vote 의 폭 후보 3개가 여기서 나온다(width ± span).'
    },
    {
      id: 'bumper', kind: 'bev_row', label: 'BEV 범퍼행',
      params: [['traffic_light', 'bev_bumper_y_px']],
      value: 0,
      hint: '★거리 0 의 기준★ 앞범퍼 바로 앞 노면이 BEV 의 몇 번째 행인가. 차체에 가려 안 보이면 BEV 높이보다 큰 값이 된다. 0 이면 「안 정했음」이다.'
    },
    {
      id: 'brake1', kind: 'bev_dist', label: '1단 예비제동 문턱',
      params: [['traffic_light', 'sl_brake1_px']],
      value: 0,
      hint: '⚠️ 정지선이 ★처음 잡히는 거리보다 가까워야★ 한다 — 안 보이는 곳에 문턱을 두면 그 구간이 통째로 없는 것과 같다.'
    },
    {
      id: 'brake2', kind: 'bev_dist', label: '2단 확정정지 문턱',
      params: [['traffic_light', 'sl_brake2_px']],
      value: 0,
      hint: '⚠️ 정지선이 ★차체에 가려 사라지는 거리보다 멀어야★ 한다 — 그보다 가까우면 문턱에 닿기 전에 화면에서 사라져 「놓침」 경로로 선다.'
    }
  ]
};
