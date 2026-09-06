/* docs/geom.js 를 cv2 의 정답표(docs/reference.js)와 대조한다.
 *
 *     node tools/geom_check.js            → 사람이 읽는 표
 *     node tools/geom_check.js --json     → {"maxErr": …} (자체검사가 쓴다)
 *
 * ★왜 node 인가★ 이 대조는 브라우저 없이도 돌아야 한다. 화면을 열어 봐야만
 * 알 수 있는 검사는 아무도 안 돌린다. `tb/selftest.py` 의 t_geom_js 가 이걸 부른다.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const sandbox = { window: {}, console: console };
vm.createContext(sandbox);
for (const f of ['docs/geom.js', 'docs/reference.js']) {
  vm.runInContext(fs.readFileSync(path.join(ROOT, f), 'utf8'), sandbox, { filename: f });
}
const G = sandbox.window.Geom;
const REF = sandbox.window.GEOM_REF;

const out = { cv2: REF.cv2, cases: [], maxErr: 0 };

for (const c of REF.cases) {
  const opt = G.optimalNewCameraMatrix(c.K, c.D, c.size, c.alpha);
  const r = { name: c.name };

  //  ① 새 카메라 행렬 — 여기가 틀리면 보정 결과가 통째로 다른 그림이 된다
  r.newK = Math.max(...opt.K.map((v, i) => Math.abs(v - c.newK[i])));
  //  ② 유효영역 ROI — 정수라 정확히 같아야 한다
  r.roi = Math.max(...opt.roi.map((v, i) => Math.abs(v - c.roi[i])));

  //  ③ 보정 맵 — 보정된 픽셀이 원본의 어디를 집어 오는가
  let e = 0;
  for (const [u, v, mx, my] of c.map) {
    const p = G.undistortMapPoint(u, v, c.K, c.D, opt.K);
    e = Math.max(e, Math.hypot(p[0] - mx, p[1] - my));
  }
  r.map = e;

  //  ④ 호모그래피 — cv2.getPerspectiveTransform 과 같은 행렬인가
  const H = G.getPerspectiveTransform(G.quadPts(c.quad),
    [[0, 0], [c.bev[0], 0], [c.bev[0], c.bev[1]], [0, c.bev[1]]]);
  e = 0;
  for (let i = 0; i < 3; i++) {
    for (let j = 0; j < 3; j++) e = Math.max(e, Math.abs(H[i][j] - c.H[i][j]));
  }
  r.H = e;

  //  ⑤ ★끝에서 끝까지★ — BEV 픽셀이 원본의 어느 픽셀에서 왔는가.
  //     ①~④ 가 다 맞아도 순서나 반픽셀 규약이 틀리면 여기서 드러난다.
  const cal = G.makeCal({ size: c.size, K: c.K, D: c.D, alpha: c.alpha,
                          quad: c.quad, bev: c.bev });
  e = 0;
  for (const [bx, by, sx, sy] of c.bevToSrc) {
    const p = G.bevToSource(bx, by, cal);
    e = Math.max(e, Math.hypot(p[0] - sx, p[1] - sy));
  }
  r.bevToSrc = e;

  out.cases.push(r);
  out.maxErr = Math.max(out.maxErr, r.newK, r.roi, r.map, r.H * 1e-3, r.bevToSrc);
}

//  ⑥ 사각형 건전성 — 파이썬(tb.geometry.quad_is_sane)과 같은 답을 내는가.
//     기하가 맞아도 이게 갈라지면 스튜디오가 못 쓸 사각형을 통과시킨다.
out.quadMismatch = [];
for (const q of (REF.quads || [])) {
  const got = G.quadIsSane(q.quad, 1920, 1080)[0];
  if (got !== q.sane) out.quadMismatch.push(q.name + ': JS=' + got + ' 파이썬=' + q.sane);
}

if (process.argv.includes('--json')) {
  process.stdout.write(JSON.stringify(out));
} else {
  for (const c of out.cases) {
    console.log(`── ${c.name}`);
    console.log(`   새 카메라 행렬  ${c.newK.toExponential(2)}`);
    console.log(`   유효영역 ROI    ${c.roi}`);
    console.log(`   보정 맵         ${c.map.toExponential(2)} px`);
    console.log(`   호모그래피      ${c.H.toExponential(2)}`);
    console.log(`   끝에서 끝까지   ${c.bevToSrc.toExponential(2)} px`);
  }
  console.log(`\n사각형 건전성 어긋남 ${out.quadMismatch.length}개`
              + (out.quadMismatch.length ? ' — ' + out.quadMismatch.join(' · ') : ''));
  console.log(`최대 오차 ${out.maxErr.toExponential(2)} (cv2 ${out.cv2})`);
}
