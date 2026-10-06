/* 브라우저의 mp4v 해독(web/mp4frames.js + web/vendor/mpeg4dec.js)을 cv2 와 대조한다.
 *
 *     node tools/mpeg4_check.js            → 사람이 읽는 표
 *     node tools/mpeg4_check.js --json     → {"clips": …} (자체검사 t_mpeg4_js 가 쓴다)
 *
 * 정답은 tools/bake_mpeg4_reference.py 가 cv2 로 구운 tools/fixtures/mp4v_ref.json.
 *
 * ★순서를 섞어 묻는다★ 처음부터 차례로만 읽으면 「이어서 해독」 길만 지난다.
 * 실제 화면은 뒤로 가기·30프레임 건너뛰기·끌기로 아무 데나 뛴다 — 그래서
 * 차례로 한 번, 섞어서 한 번, 뒤에서부터 한 번 묻고 셋 다 맞아야 한다.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const FIX = path.join(ROOT, 'tools', 'fixtures');
const REF = JSON.parse(fs.readFileSync(path.join(FIX, 'mp4v_ref.json'), 'utf8'));

const sandbox = { console: console };
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(ROOT, 'web', 'mp4frames.js'), 'utf8'), sandbox,
                { filename: 'web/mp4frames.js' });
const M = sandbox.Mp4Frames;
const Mpeg4Dec = require(path.join(ROOT, 'web', 'vendor', 'mpeg4dec.js'));

function signature(rgba, w, h) {
  const [sw, sh] = REF.sig, cw = Math.floor(w / sw), ch = Math.floor(h / sh);
  const out = new Array(sw * sh * 3).fill(0);
  for (let y = 0; y < sh * ch; y++) {
    for (let x = 0; x < sw * cw; x++) {
      const o = (y * w + x) * 4, k = (Math.floor(y / ch) * sw + Math.floor(x / cw)) * 3;
      out[k] += rgba[o]; out[k + 1] += rgba[o + 1]; out[k + 2] += rgba[o + 2];
    }
  }
  return out.map((v) => v / (cw * ch));
}

function maxDiff(a, b) {
  let m = 0;
  for (let i = 0; i < a.length; i++) m = Math.max(m, Math.abs(a[i] - b[i]));
  return m;
}

async function checkClip(name, want) {
  const buf = new Uint8Array(fs.readFileSync(path.join(FIX, name)));
  const read = (o, n) => Promise.resolve(buf.subarray(o, o + n));
  const track = await M.demux(read, buf.length);
  const dec = await Mpeg4Dec();
  const src = new M.Source(dec, track, read, (rgba, w, h) => signature(rgba, w, h));
  const n = want.length;
  const r = { name, codec: track.codec, frames: track.frames, fps: +track.fps.toFixed(3),
              size: [track.width, track.height], worst: 0, wrongFrame: [] };

  //  섞기는 고정된 순서 — 매번 같은 결과가 나와야 검사다
  const shuffled = [...Array(n).keys()].map((i) => (i * 17 + 5) % n);
  const orders = { 차례로: [...Array(n).keys()], 섞어서: shuffled,
                   거꾸로: [...Array(n).keys()].reverse() };
  for (const [label, seq] of Object.entries(orders)) {
    for (const i of seq) {
      const sig = await src.frame(i);
      const d = maxDiff(sig, want[i]);
      r.worst = Math.max(r.worst, d);
      //  ★다른 프레임과 더 가까우면 「틀린 프레임」★ — 색이 조금 다른 것과 구별한다
      let best = i, bd = d;
      want.forEach((w, j) => { const e = maxDiff(sig, w); if (e < bd) { bd = e; best = j; } });
      if (best !== i) r.wrongFrame.push(`${label}:${i}→${best}`);
    }
  }
  //  ★몰려온 요청은 마지막 것만 푼다★ (끌기) — 앞의 것은 null, 마지막은 제 그림
  const burst = [3, 20, 7, n - 7];
  const got = await Promise.all(burst.map((i) => src.frame(i)));
  got.forEach((g, k) => {
    const last = k === burst.length - 1;
    if (last ? !(g && maxDiff(g, want[burst[k]]) < 6) : g !== null) {
      r.wrongFrame.push(`몰아서:${burst[k]}`);
    }
  });
  return r;
}

(async () => {
  const out = { cv2: REF.cv2, clips: [] };
  for (const [name, want] of Object.entries(REF.clips)) out.clips.push(await checkClip(name, want));
  if (process.argv.includes('--json')) {
    process.stdout.write(JSON.stringify(out) + '\n');
    return;
  }
  for (const c of out.clips) {
    console.log(`${c.name}  ${c.codec} ${c.size.join('×')} ${c.frames}장 ${c.fps}fps  `
                + `최대 차이 ${c.worst.toFixed(2)}  틀린 프레임 ${c.wrongFrame.length ? c.wrongFrame.join(' ') : '없음'}`);
  }
})().catch((e) => { console.error(e.stack || e); process.exit(1); });
