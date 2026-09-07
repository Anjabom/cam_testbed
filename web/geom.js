/* 기하 — 대상 노드가 하는 변환을 브라우저에서 그대로 재현한다.
 *
 * ★왜 여기에 한 벌 더 있나★ [2026-09-06]
 * 예전 스튜디오는 서버(`tb.geometry`)가 그림을 그려 JPEG 로 내려보냈고,
 * JS 는 값만 보냈다. 「두 벌을 두지 않는다」가 그때의 방어선이었다.
 * 스튜디오가 정적 페이지(github.io)로 옮겨 오면서 서버가 없어졌으므로
 * 그 방어선은 성립하지 않는다. 대신 ★두 벌이 같음을 기계가 증명한다★:
 *
 *     tools/bake_reference.py  → cv2 로 대조점을 구워 web/reference.js 로
 *     tb/selftest.py  t_geom_js → node 로 이 파일을 불러 그 대조점과 맞춰 본다
 *
 * 그래서 이 파일을 고치면 ★반드시★ `python3 -m tb.selftest` 를 돌려야 한다.
 * 여기가 틀리면 화면이 보여 주는 BEV 와 노드가 만드는 BEV 가 갈라지고,
 * 그러면 여기서 맞춘 값이 실차에서 틀린다 — 이 저장소가 가장 두려워하는 실패다.
 *
 * ★OpenCV 의 구현을 그대로 옮긴다★ 「더 나은 방법」으로 고치지 않는다.
 * 노드가 cv2 를 쓰므로 cv2 와 같아야 하고, 다르면 그게 곧 오차다.
 *
 * ★워크스페이스 고유명은 여기 없다★ 파라미터 이름은 docs/tuning.js(데이터)에만.
 */
(function (root) {
  'use strict';

  //  왜곡 계수는 [k1, k2, p1, p2, k3, k4, k5, k6] 순이다(cv2 와 같다).
  //  모자라면 0 으로 채운다 — 계약은 보통 5개(k1 k2 p1 p2 k3)만 적는다.
  function padD(D) {
    var d = [0, 0, 0, 0, 0, 0, 0, 0];
    for (var i = 0; i < 8 && i < (D || []).length; i++) d[i] = +D[i] || 0;
    return d;
  }

  //  계약의 K 는 [fx, fy, cx, cy] 넉 줄이다(3x3 행렬이 아니다).
  function kOf(K) {
    return { fx: +K[0], fy: +K[1], cx: +K[2], cy: +K[3] };
  }

  // ── ① 왜곡 — 정규좌표 → 왜곡된 정규좌표 (닫힌 식) ─────────────────
  //  cv2.initUndistortRectifyMap 이 픽셀마다 하는 계산과 같다.
  function distortNorm(x, y, d) {
    var r2 = x * x + y * y;
    var num = 1 + ((d[4] * r2 + d[1]) * r2 + d[0]) * r2;      // k3 k2 k1
    var den = 1 + ((d[7] * r2 + d[6]) * r2 + d[5]) * r2;      // k6 k5 k4
    var kr = num / den;
    var xd = x * kr + 2 * d[2] * x * y + d[3] * (r2 + 2 * x * x);
    var yd = y * kr + d[2] * (r2 + 2 * y * y) + 2 * d[3] * x * y;
    return [xd, yd];
  }

  // ── ② 왜곡의 역 — 픽셀 → 정규좌표 (반복법) ───────────────────────
  //  cv2.undistortPoints 와 같은 반복이다. ★5회★ 는 임의의 수가 아니라
  //  cv2 의 기본값이라 그대로 쓴다(다르게 하면 newK 가 미세하게 갈라진다).
  function undistortPointNorm(u, v, K, d, iters) {
    var k = kOf(K);
    var x0 = (u - k.cx) / k.fx, y0 = (v - k.cy) / k.fy;
    var x = x0, y = y0;
    var n = (iters == null) ? 5 : iters;
    for (var j = 0; j < n; j++) {
      var r2 = x * x + y * y;
      var icdist = (1 + ((d[7] * r2 + d[6]) * r2 + d[5]) * r2)
                 / (1 + ((d[4] * r2 + d[1]) * r2 + d[0]) * r2);
      var dx = 2 * d[2] * x * y + d[3] * (r2 + 2 * x * x);
      var dy = d[2] * (r2 + 2 * y * y) + 2 * d[3] * x * y;
      x = (x0 - dx) * icdist;
      y = (y0 - dy) * icdist;
    }
    return [x, y];
  }

  // ── ③ 유효 화면 사각형 — cv2 의 icvGetRectangles ─────────────────
  //  9x9 격자를 왜곡보정해 「전부 유효한 안쪽 사각형」과 「전체를 덮는
  //  바깥 사각형」을 찾는다. P 를 주면 그 행렬로 픽셀 좌표까지 옮긴다.
  function getRectangles(K, d, size, P) {
    var N = 9, w = size[0], h = size[1];
    var iX0 = -Infinity, iX1 = Infinity, iY0 = -Infinity, iY1 = Infinity;
    var oX0 = Infinity, oX1 = -Infinity, oY0 = Infinity, oY1 = -Infinity;
    for (var y = 0; y < N; y++) {
      for (var x = 0; x < N; x++) {
        var p = undistortPointNorm(x * w / (N - 1), y * h / (N - 1), K, d);
        var px = p[0], py = p[1];
        if (P) { var pk = kOf(P); px = pk.fx * px + pk.cx; py = pk.fy * py + pk.cy; }
        if (px < oX0) oX0 = px;
        if (px > oX1) oX1 = px;
        if (py < oY0) oY0 = py;
        if (py > oY1) oY1 = py;
        if (x === 0 && px > iX0) iX0 = px;
        if (x === N - 1 && px < iX1) iX1 = px;
        if (y === 0 && py > iY0) iY0 = py;
        if (y === N - 1 && py < iY1) iY1 = py;
      }
    }
    return {
      inner: [iX0, iY0, iX1 - iX0, iY1 - iY0],
      outer: [oX0, oY0, oX1 - oX0, oY1 - oY0]
    };
  }

  // ── ④ 새 카메라 행렬 — cv2.getOptimalNewCameraMatrix ─────────────
  //  ★생략할 수 없다★ 이 카메라에서 fx 가 956 → 614 로 바뀐다.
  //  건너뛰면 화면의 보정 결과가 노드의 것과 통째로 다른 그림이 된다.
  //  반환: {K: [fx, fy, cx, cy], roi: [x, y, w, h]}
  function optimalNewCameraMatrix(K, D, size, alpha) {
    var d = padD(D), w = size[0], h = size[1], a = +alpha || 0;
    var r = getRectangles(K, d, size, null);
    var fx0 = (w - 1) / r.inner[2], fy0 = (h - 1) / r.inner[3];
    var fx1 = (w - 1) / r.outer[2], fy1 = (h - 1) / r.outer[3];
    var newK = [
      fx0 * (1 - a) + fx1 * a,
      fy0 * (1 - a) + fy1 * a,
      (-fx0 * r.inner[0]) * (1 - a) + (-fx1 * r.outer[0]) * a,
      (-fy0 * r.inner[1]) * (1 - a) + (-fy1 * r.outer[1]) * a
    ];
    //  유효영역 ROI 는 ★새 행렬로 다시 잰다★ (cv2 와 같은 순서)
    var r2 = getRectangles(K, d, size, newK);
    var rx = Math.round(r2.inner[0]), ry = Math.round(r2.inner[1]);
    var rw = Math.round(r2.inner[2]), rh = Math.round(r2.inner[3]);
    //  화면과 교집합 — cv2 의 `r &= Rect(0,0,w,h)` 와 같다
    var x0 = Math.max(rx, 0), y0 = Math.max(ry, 0);
    var x1 = Math.min(rx + rw, w), y1 = Math.min(ry + rh, h);
    return { K: newK, roi: [x0, y0, Math.max(0, x1 - x0), Math.max(0, y1 - y0)] };
  }

  // ── ⑤ 보정 맵 — 보정된 픽셀 (u,v) → 원본 픽셀 ────────────────────
  //  remap 이 픽셀마다 참조하는 좌표다. WebGL 셰이더도 이 식을 쓴다.
  function undistortMapPoint(u, v, K, D, newK) {
    var d = padD(D), nk = kOf(newK), k = kOf(K);
    var xy = distortNorm((u - nk.cx) / nk.fx, (v - nk.cy) / nk.fy, d);
    return [k.fx * xy[0] + k.cx, k.fy * xy[1] + k.cy];
  }

  // ── ⑥ ROI crop → 원래 크기 resize ────────────────────────────────
  //  tb.geometry.Undistorter 가 remap 뒤에 하는 두 줄이다.
  //  ★cv2.resize 의 픽셀 중심 규약★ 출력 u → 입력 (u+0.5)*rw/w - 0.5.
  //  이걸 빼먹으면 반 픽셀씩 밀린다 — 클릭으로 맞추는 화면에서는 보인다.
  function cropResizeToFull(u, v, roi, size) {
    var sx = roi[2] / size[0], sy = roi[3] / size[1];
    return [(u + 0.5) * sx - 0.5 + roi[0], (v + 0.5) * sy - 0.5 + roi[1]];
  }

  // ── ⑦ 4점 호모그래피 — cv2.getPerspectiveTransform ───────────────
  //  8x8 선형계를 가우스 소거로 푼다(점 4쌍이면 해가 하나다).
  function getPerspectiveTransform(src, dst) {
    var A = [], b = [];
    for (var i = 0; i < 4; i++) {
      var sx = src[i][0], sy = src[i][1], dx = dst[i][0], dy = dst[i][1];
      A.push([sx, sy, 1, 0, 0, 0, -sx * dx, -sy * dx]); b.push(dx);
      A.push([0, 0, 0, sx, sy, 1, -sx * dy, -sy * dy]); b.push(dy);
    }
    var x = solve8(A, b);
    return [[x[0], x[1], x[2]], [x[3], x[4], x[5]], [x[6], x[7], 1]];
  }

  //  부분 피벗 가우스 소거 — 8x8 이라 이걸로 충분하다.
  function solve8(A, b) {
    var n = b.length, i, j, k;
    var M = A.map(function (row, r) { return row.concat([b[r]]); });
    for (i = 0; i < n; i++) {
      var piv = i;
      for (j = i + 1; j < n; j++) {
        if (Math.abs(M[j][i]) > Math.abs(M[piv][i])) piv = j;
      }
      var t = M[i]; M[i] = M[piv]; M[piv] = t;
      if (Math.abs(M[i][i]) < 1e-12) throw new Error('사각형이 퇴화했습니다(세 점이 한 직선)');
      for (j = i + 1; j < n; j++) {
        var f = M[j][i] / M[i][i];
        for (k = i; k <= n; k++) M[j][k] -= f * M[i][k];
      }
    }
    var x = new Array(n);
    for (i = n - 1; i >= 0; i--) {
      var s = M[i][n];
      for (j = i + 1; j < n; j++) s -= M[i][j] * x[j];
      x[i] = s / M[i][i];
    }
    return x;
  }

  function applyH(H, x, y) {
    var w = H[2][0] * x + H[2][1] * y + H[2][2];
    return [(H[0][0] * x + H[0][1] * y + H[0][2]) / w,
            (H[1][0] * x + H[1][1] * y + H[1][2]) / w];
  }

  function invert3(H) {
    var a = H[0][0], b = H[0][1], c = H[0][2],
        d = H[1][0], e = H[1][1], f = H[1][2],
        g = H[2][0], h = H[2][1], i = H[2][2];
    var det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
    if (Math.abs(det) < 1e-15) throw new Error('역행렬이 없습니다');
    return [[(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
            [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
            [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]];
  }

  // ── ⑧ IPM — tb.geometry.ipm_matrices 와 같은 대응 ────────────────
  //  사각형은 [TL, TR, BR, BL] 순서고 BEV 의 네 귀퉁이로 간다.
  function ipmMatrices(quad, bevW, bevH) {
    var dst = [[0, 0], [bevW, 0], [bevW, bevH], [0, bevH]];
    var M = getPerspectiveTransform(quad, dst);
    return { M: M, Minv: invert3(M) };
  }

  // ── ⑨ 한 줄로 꿴 것 — BEV 픽셀 → 원본 영상 픽셀 ──────────────────
  //  변환 순서는 tb.geometry 의 머리말과 같다:
  //     원본 → remap(보정) → ROI crop → 원래 크기 resize → warpPerspective
  //  화면(WebGL)은 이 역방향을 픽셀마다 밟아 원본에서 색을 집어 온다.
  function bevToSource(bx, by, cal) {
    var p = applyH(cal.Minv, bx, by);                 // BEV → 보정·리사이즈된 영상
    var q = cropResizeToFull(p[0], p[1], cal.roi, cal.size);  // → 보정 전체 크기
    return undistortMapPoint(q[0], q[1], cal.K, cal.D, cal.newK);  // → 원본
  }

  //  화면이 값 하나로 들고 다니는 묶음. tuning 이 바뀔 때마다 다시 만든다.
  function makeCal(t) {
    var size = t.size, opt = optimalNewCameraMatrix(t.K, t.D, size, t.alpha);
    var ipm = ipmMatrices(quadPts(t.quad), t.bev[0], t.bev[1]);
    return { size: size, K: t.K, D: t.D, alpha: t.alpha || 0,
             newK: opt.K, roi: opt.roi, bev: t.bev,
             M: ipm.M, Minv: ipm.Minv };
  }

  //  사각형은 저장할 때 [x0,y0,x1,y1,x2,y2,x3,y3] 납작한 8개 수다
  //  (노드 파라미터가 그 모양이라 그렇다). 다룰 때만 점 4개로 편다.
  function quadPts(flat) {
    if (flat.length === 4 && Array.isArray(flat[0])) return flat;
    var p = [];
    for (var i = 0; i < 8; i += 2) p.push([+flat[i], +flat[i + 1]]);
    return p;
  }
  function quadFlat(pts) {
    var out = [];
    pts.forEach(function (p) { out.push(p[0], p[1]); });
    return out;
  }

  // ── ⑩ 사각형 건전성 — ★tb.geometry.quad_is_sane 과 글자 그대로 같은 판정★
  //  처음에는 「화면 밖이면 거절」을 여기 넣었다가 대조에서 걸렸다(2026-09-06).
  //  파이썬 쪽에는 그 규칙이 없다 — 실제로 잘 맞춘 사각형의 아래 두 점은 화면을
  //  살짝 벗어난다(그래야 BEV 밑변까지 지면이 찬다). 화면 밖을 막았다면 제대로
  //  맞춘 값을 「틀렸다」고 말할 뻔했다. 판정은 한 곳에서만 정한다.
  //
  //  경고만 하고 막지는 않는다 — 극단적인 배치가 필요한 경우가 있어서다.
  function quadIsSane(pts, w, h) {
    var p = quadPts(pts), why = [], i;
    //  볼록성 + 방향 — TL→TR→BR→BL 순이면 외적 부호가 일정해야 한다
    var cr = [];
    for (i = 0; i < 4; i++) {
      var a = p[i], b = p[(i + 1) % 4], c = p[(i + 2) % 4];
      cr.push((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]));
    }
    var pos = cr.every(function (x) { return x > 0; });
    var neg = cr.every(function (x) { return x < 0; });
    if (!pos && !neg) why.push('사각형이 볼록하지 않다(점 순서가 TL→TR→BR→BL 인지 확인)');
    //  신발끈 공식 — 면적이 화면의 0.2% 도 안 되면 사실상 퇴화다
    var area = 0;
    for (i = 0; i < 4; i++) {
      var q1 = p[i], q2 = p[(i + 1) % 4];
      area += q1[0] * q2[1] - q1[1] * q2[0];
    }
    area = Math.abs(area) / 2;
    if (area < w * h * 0.002) why.push('면적이 너무 작다');
    var top = Math.hypot(p[1][0] - p[0][0], p[1][1] - p[0][1]);
    var bot = Math.hypot(p[2][0] - p[3][0], p[2][1] - p[3][1]);
    if (top < 4 || bot < 4) why.push('윗변 또는 아랫변이 거의 0');
    else if (top > bot) why.push('윗변이 아랫변보다 길다 — 지면 평면이면 보통 반대다');
    return [why.length === 0, why.join(' · ')];
  }

  root.Geom = {
    padD: padD,
    distortNorm: distortNorm,
    undistortPointNorm: undistortPointNorm,
    getRectangles: getRectangles,
    optimalNewCameraMatrix: optimalNewCameraMatrix,
    undistortMapPoint: undistortMapPoint,
    cropResizeToFull: cropResizeToFull,
    getPerspectiveTransform: getPerspectiveTransform,
    applyH: applyH,
    invert3: invert3,
    ipmMatrices: ipmMatrices,
    bevToSource: bevToSource,
    makeCal: makeCal,
    quadPts: quadPts,
    quadFlat: quadFlat,
    quadIsSane: quadIsSane
  };
})(typeof window !== 'undefined' ? window : globalThis);
