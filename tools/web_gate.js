/* 스튜디오 페이지 발행 전 관문 — node 만 있으면 돈다(cv2·ROS 불필요).
 *
 *     node tools/web_gate.js        → 통과면 0, 아니면 1 과 사유
 *
 * ★왜 tb.selftest 가 아니라 이것인가★ [2026-10-06] github.io 로 나가는 것은 web/
 * 뿐이고, 그 페이지가 틀리는 길은 둘이다 — 기하가 cv2 와 갈라지거나(geom.js),
 * mp4v 해독이 다른 프레임을 내거나(mp4frames.js · vendor/mpeg4dec.js). 둘 다 정답표
 * (web/reference.js · tools/fixtures/mp4v_ref.json)가 이미 구워져 있어 node 로만 잰다.
 * tb.selftest 전체는 rclpy 와 ★정답표를 구운 cv2 판(4.5.4)★ 이 있어야 해서
 * GitHub 의 러너에서는 돌지 않는다.
 *
 * ★문턱은 tb/selftest.py 의 t_geom_js · t_mpeg4_js 와 같다★ 한쪽을 고치면 다른 쪽도.
 */
'use strict';

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const fails = [];
const run = (f) => JSON.parse(execFileSync(process.execPath, [path.join(__dirname, f), '--json'],
                                           { encoding: 'utf8' }));

//  ① 페이지가 부르는 파일이 다 있는가 — 하나만 빠져도 github.io 에서는 빈 화면이다
const html = fs.readFileSync(path.join(ROOT, 'web', 'index.html'), 'utf8');
const refs = [...html.matchAll(/(?:src|href)="([^"#:]+)"/g)].map((m) => m[1]);
for (const f of refs.concat(['vendor/mpeg4dec.js'])) {
  if (!fs.existsSync(path.join(ROOT, 'web', f))) fails.push(`web/${f} 가 없다`);
}

//  ② 기하 — t_geom_js 와 같은 문턱
const g = run('geom_check.js');
for (const c of g.cases) {
  if (c.roi !== 0) fails.push(`유효영역 ROI(${c.name}) ${c.roi}`);
  if (!(c.newK < 1e-3)) fails.push(`새 카메라 행렬(${c.name}) ${c.newK}`);
  if (!(c.map < 0.01)) fails.push(`보정 맵(${c.name}) ${c.map}px`);
  if (!(c.H < 1e-6)) fails.push(`호모그래피(${c.name}) ${c.H}`);
  if (!(c.bevToSrc < 0.1)) fails.push(`끝에서 끝까지(${c.name}) ${c.bevToSrc}px`);
}
if (g.quadMismatch.length) fails.push('사각형 건전성 ' + g.quadMismatch.join(' · '));

//  ③ mp4v 해독 — t_mpeg4_js 와 같은 문턱
const m = run('mpeg4_check.js');
if (!m.clips.length) fails.push('mp4v 대조 클립이 없다');
for (const c of m.clips) {
  if (c.wrongFrame.length) fails.push(`틀린 프레임(${c.name}) ${c.wrongFrame.slice(0, 5).join(' ')}`);
  if (!(c.worst < 6)) fails.push(`색 차이(${c.name}) ${c.worst}`);
}

if (fails.length) {
  console.error('❌ 발행하지 않는다 —\n  ' + fails.join('\n  '));
  process.exit(1);
}
console.log(`✅ 기하 최대 ${g.maxErr.toExponential(2)} · mp4v `
            + m.clips.map((c) => `${c.name} ${c.worst.toFixed(2)}`).join(' · '));
