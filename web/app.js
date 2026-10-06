/* 카메라 보정 스튜디오 — 화면과 조작.
 *
 * ★두 곳에서 돈다★ [2026-10-06] github.io(서버 없음)와 이 기계의 tb.run studio
 * (서버 있음). 같은 파일들이다. 영상은 어느 쪽이든 아무 데도 올라가지 않는다.
 *   · 서버가 있으면 이 기계의 폴더를 훑고 프레임을 cv2 로 받는다.
 *   · 없으면 이 브라우저가 파일·폴더를 직접 읽는다. mp4v 는 페이지 안의 wasm 이
 *     푼다(mp4frames.js) — 9/7 에 github.io 를 접게 만든 「녹화를 하나도 못 연다」를
 *     그것으로 넘는다.
 * 두 갈래 모두 「프레임 번호 → 그림」 한 모양(media.kind === 'frames')이라,
 * 아래 코드는 프레임이 어디서 오는지 모른다.
 *
 * ★맞출 대상과 이름은 여기 없다★ 이 파일은 ★종류★(quad / rect / scale /
 * number / size / bev_row / bev_dist)만 안다. 무엇을 어떤 파라미터 이름으로
 * 내보낼지는 tuning.js 가 정한다 — 이름을 여기 박으면 다른 카메라·다른
 * 워크스페이스에서 화면이 통째로 빈다.
 *
 * ★기하는 geom.js 하나를 지난다★ 내보내는 숫자는 전부 거기서 나오고, 그것이
 * cv2 와 같은 값을 내는지는 tb/selftest.py 의 t_geom_js 가 증명한다.
 */
(function () {
  'use strict';

  var G = window.Geom;
  var $ = function (id) { return document.getElementById(id); };
  var KEY = 'cam_studio_tuning_v1';

  //  화면에 잡히는 크기. BEV 는 세로로 길어(640×1000) 세로 기준으로 맞춘다.
  var SRC_BOX = [660, 470], BEV_BOX = [430, 500];
  var GRAB = 13;                       // 점을 잡는 반경 [표시 px]

  var T = restore() || clone(window.TUNING);
  var mode = '';                       // 지금 편집 중인 대상 id ('' = 없음)
  var media = null;                    // {el, kind, w, h, dur}
  var renderer = null;
  var meas = [];                       // 척도 재기 — BEV 좌표 두 점
  var drag = null;                     // {kind, idx}
  var lastCal = null;
  //  ★서버가 있으면 이 기계의 영상을 코덱 상관없이 연다★ (tb/studio.py)
  //  브라우저는 mp4v 를 못 열지만, 서버는 cv2 로 디코드해 프레임만 넘긴다.
  //  없으면(github.io·파일로 연 경우) 이 브라우저가 폴더·파일을 직접 읽는다.
  var server = null;
  var lastDir = '';   // 마지막에 훑은 폴더 — 다시 열 때 거기서 시작한다
  //  서버가 없을 때 고른 폴더 — [{name, handle}] 경로 순서대로(맨 끝이 지금 폴더)
  var localDirs = null;
  var frameSeq = 0;   // 마지막으로 요청한 프레임 — 늦게 온 옛 그림을 버린다
  var liveInputs = [];  // 드래그하면 따라 움직이는 숫자칸들
  var showGrid = true;   // g — BEV 격자
  var useUndist = true;  // u — 왜곡보정 (★보기 전용 토글이다★ 값은 안 바뀐다)
  var playing = null;    // 스페이스 — 프레임 자동 넘기기
  var undoStack = [];    // r — 되돌리기

  // ══════════════════════════════════════════════════════════════════
  //  값 다루기 — 종류로만 찾는다(이름을 모른다)
  // ══════════════════════════════════════════════════════════════════
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function byKind(k) {
    return T.targets.filter(function (t) { return t.kind === k; });
  }
  function one(k) { return byKind(k)[0] || null; }
  function find(id) {
    var r = T.targets.filter(function (t) { return t.id === id; });
    return r[0] || null;
  }
  function quadT() { return one('quad'); }
  function bevSize() {
    var t = one('size');
    return t ? [Math.max(16, t.value[0] | 0), Math.max(16, t.value[1] | 0)] : [640, 480];
  }
  function px2m() { var t = one('scale'); return t ? +t.value || 0 : 0; }

  //  거리 0 의 기준행 — ★0 은 「아직 안 정했다」★ 그때는 BEV 밑변이 기준이다
  //  (tb.calibrate 와 같은 규약이다).
  //  이 규약을 두 군데서 다르게 쓰면 기준선은 밑변인데 그림은 맨 위에 그려진다 —
  //  실제로 그렇게 났다. 그래서 읽는 자리를 이 함수 하나로 모은다.
  function bumperY() {
    var t = one('bev_row');
    var v = t ? +t.value : 0;
    return v > 0 ? v : bevSize()[1];
  }
  //  bev_row 는 ★행 자체★, bev_dist 는 ★기준선에서의 거리★ 다.
  //  기준선을 옮기면 문턱이 통째로 따라오게 하려는 것이다.
  function rowY(t) {
    return t.kind === 'bev_row' ? bumperY() : bumperY() - (+t.value);
  }
  function setRowY(t, y) {
    t.value = t.kind === 'bev_row' ? y : bumperY() - y;
  }
  function bevLines() { return byKind('bev_row').concat(byKind('bev_dist')); }

  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(T)); } catch (e) { /* 사생활 모드 */ }
  }
  function restore() {
    try {
      var s = localStorage.getItem(KEY);
      return s ? JSON.parse(s) : null;
    } catch (e) { return null; }
  }

  //  ★되돌리기★ 값 전체를 통째로 찍어 둔다. 대상이 아홉 개뿐이라 이 정도면
  //  충분하고, 「어느 항목의 몇 번째 편집」을 추적하는 것보다 훨씬 짧다.
  //  찍는 자리는 ★편집이 시작되는 순간★ 이다 — 드래그를 잡을 때, 키로 밀기 전,
  //  숫자칸에 커서를 둘 때. 그래야 한 번의 되돌리기가 한 번의 편집을 되돌린다.
  function snapshot() {
    undoStack.push(JSON.stringify(T.targets.map(function (t) { return t.value; })));
    if (undoStack.length > 40) undoStack.shift();
  }

  function undo() {
    var s = undoStack.pop();
    if (!s) return false;
    JSON.parse(s).forEach(function (v, i) {
      if (T.targets[i]) T.targets[i].value = v;
    });
    save(); renderPanel(); draw();
    return true;
  }

  function cal() {
    var c = T.camera;
    //  u 로 끄면 ★그림만★ 왜곡보정 없는 상태로 본다(원본이 실제로 휘어 있는지,
    //  K·D 가 일을 하고 있는지 눈으로 확인하는 용도다). 내보내는 값은 그대로다.
    lastCal = G.makeCal({ size: c.size, K: c.K,
                          D: useUndist ? c.D : [0, 0, 0, 0, 0], alpha: c.alpha,
                          quad: quadT().value, bev: bevSize() });
    return lastCal;
  }

  function mul3(A, B) {
    var C = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
    for (var i = 0; i < 3; i++) {
      for (var j = 0; j < 3; j++) {
        C[i][j] = A[i][0] * B[0][j] + A[i][1] * B[1][j] + A[i][2] * B[2][j];
      }
    }
    return C;
  }
  function fit(w, h, box) {
    var s = Math.min(box[0] / w, box[1] / h);
    return { w: Math.max(1, Math.round(w * s)), h: Math.max(1, Math.round(h * s)), s: s };
  }

  // ══════════════════════════════════════════════════════════════════
  //  그리기
  // ══════════════════════════════════════════════════════════════════
  function draw() {
    var c = cal();
    var und = T.camera.size;

    // ── 원본(보정 후) ──
    var f = fit(und[0], und[1], SRC_BOX);
    var sc = $('srcC');
    sc.width = f.w; sc.height = f.h;
    var ctx = sc.getContext('2d');
    if (renderer && media) {
      ctx.drawImage(renderer.render(f.w, f.h, [[und[0] / f.w, 0, 0],
                                               [0, und[1] / f.h, 0],
                                               [0, 0, 1]], c), 0, 0);
    } else {
      ctx.fillStyle = '#0b0d10'; ctx.fillRect(0, 0, f.w, f.h);
      ctx.fillStyle = '#5c6675'; ctx.font = '14px system-ui'; ctx.textAlign = 'center';
      ctx.fillText('영상이나 사진을 열어 주세요', f.w / 2, f.h / 2);
      ctx.textAlign = 'left';
    }
    drawSrcOverlay(ctx, f.s);

    // ── BEV ──
    var bv = bevSize();
    var g = fit(bv[0], bv[1], BEV_BOX);
    var bc = $('bevC');
    bc.width = g.w; bc.height = g.h;
    var bx = bc.getContext('2d');
    if (renderer && media) {
      var H = mul3(c.Minv, [[bv[0] / g.w, 0, 0], [0, bv[1] / g.h, 0], [0, 0, 1]]);
      bx.drawImage(renderer.render(g.w, g.h, H, c), 0, 0);
    } else {
      bx.fillStyle = '#0b0d10'; bx.fillRect(0, 0, g.w, g.h);
    }
    drawBevOverlay(bx, g.s, bv);

    renderValues();
    renderFoot();
    syncInputs();
  }

  function drawSrcOverlay(ctx, s) {
    var q = G.quadPts(quadT().value);
    var on = (mode === '' || mode === quadT().id);

    byKind('rect').forEach(function (t) {
      var sel = (mode === t.id);
      var v = t.value;
      ctx.strokeStyle = sel ? '#3cc8ff' : '#7b8494';
      ctx.lineWidth = sel ? 2.5 : 1.5;
      ctx.strokeRect(v[0] * s, v[1] * s, (v[2] - v[0]) * s, (v[3] - v[1]) * s);
      ctx.fillStyle = sel ? '#3cc8ff' : '#7b8494';
      ctx.font = '12px system-ui';
      ctx.fillText(t.label, v[0] * s + 6, v[1] * s + 15);
      if (sel) {
        dot(ctx, v[0] * s, v[1] * s, '#ffe14a');
        dot(ctx, v[2] * s, v[3] * s, '#ffe14a');
      }
    });

    ctx.strokeStyle = on ? '#4aa3ff' : '#8b93a2';
    ctx.lineWidth = on ? 2.5 : 1.5;
    ctx.beginPath();
    q.forEach(function (p, i) {
      if (i === 0) ctx.moveTo(p[0] * s, p[1] * s); else ctx.lineTo(p[0] * s, p[1] * s);
    });
    ctx.closePath(); ctx.stroke();
    ['TL', 'TR', 'BR', 'BL'].forEach(function (lab, i) {
      if (on) dot(ctx, q[i][0] * s, q[i][1] * s, '#ffe14a');
      ctx.fillStyle = on ? '#ffe14a' : '#8b93a2';
      ctx.font = '12px system-ui';
      //  오른쪽·아래 모서리는 글자가 캔버스 밖으로 나간다 — 안쪽으로 붙인다
      var lx = Math.min(q[i][0] * s + 11, ctx.canvas.width - 22);
      ctx.fillText(lab, Math.max(2, lx), Math.min(Math.max(12, q[i][1] * s - 7),
                                                 ctx.canvas.height - 4));
    });

    if (!useUndist) {
      //  ★좌표는 보정된 화면 기준이다★ 지금 밑그림은 보정 전이라 사각형이
      //  살짝 어긋나 보이는 것이 정상이다 — 그걸 모르면 멀쩡한 값을 고치게 된다.
      ctx.fillStyle = '#ffb454';
      ctx.font = 'bold 12px system-ui';
      ctx.fillText('왜곡보정 OFF — 보기 전용 (좌표는 보정 화면 기준)', 10, 16);
    }
    var ok = G.quadIsSane(q, T.camera.size[0], T.camera.size[1]);
    if (!ok[0]) {
      ctx.fillStyle = '#ff6b6b'; ctx.font = 'bold 13px system-ui';
      ctx.fillText('⚠ ' + ok[1], 10, ctx.canvas.height - 10);
    }
  }

  function drawBevOverlay(ctx, s, bv) {
    var m = px2m(), by = bumperY();

    //  ★격자는 기준선(범퍼)에서 잰다★ 노드의 디버그 그림은 BEV 밑변에서 재지만,
    //  사람이 알고 싶은 것은 「차 앞에서 몇 m」다. 이 격자는 눈금일 뿐이고
    //  내보내는 값에는 들어가지 않는다.
    if (m > 0 && showGrid) {
      ctx.font = '11px ui-monospace, monospace';
      for (var i = 1; i <= 40; i++) {
        var y = (by - i * 0.5 / m) * s;
        if (y < 0) break;
        var big = (i % 2 === 0);
        ctx.strokeStyle = big ? 'rgba(150,160,180,.45)' : 'rgba(150,160,180,.2)';
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(ctx.canvas.width, y); ctx.stroke();
        if (big) {
          ctx.fillStyle = 'rgba(190,200,215,.7)';
          ctx.fillText((i * 0.5).toFixed(1) + 'm', 4, y - 3);
        }
      }
    }
    ctx.strokeStyle = 'rgba(90,200,255,.35)';
    ctx.beginPath();
    ctx.moveTo(ctx.canvas.width / 2, 0); ctx.lineTo(ctx.canvas.width / 2, ctx.canvas.height);
    ctx.stroke();

    var used = [];
    bevLines().forEach(function (t) {
      var sel = (mode === t.id), y = rowY(t) * s;
      var col = t.kind === 'bev_row' ? '#6bd07f' : '#ffb454';
      ctx.strokeStyle = sel ? '#ffe14a' : col;
      ctx.lineWidth = sel ? 2.5 : 1.5;
      ctx.setLineDash(t.kind === 'bev_row' ? [] : [7, 5]);
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(ctx.canvas.width, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = sel ? '#ffe14a' : col;
      ctx.font = '11px system-ui';
      var lab = t.label + ' ' + Math.round(+t.value)
              + (t.kind === 'bev_dist' && m > 0 ? ' (' + (t.value * m).toFixed(2) + 'm)' : '');
      //  값이 아직 0 이면 선이 전부 같은 자리에 온다 — 글자가 포개지면 못 읽는다.
      //  바닥에 붙었으면 아래로 밀 자리가 없으니 ★위로★ 쌓는다.
      var ly = Math.max(11, y - 4);
      var dir = (ly > ctx.canvas.height - 30) ? -13 : 13;
      while (used.some(function (u) { return Math.abs(u - ly) < 12; })) ly += dir;
      used.push(ly);
      ctx.fillText(lab, 6, ly);
    });

    meas.forEach(function (p) { dot(ctx, p[0] * s, p[1] * s, '#ffe14a', 5); });
    if (meas.length === 2) {
      ctx.strokeStyle = '#ffe14a'; ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(meas[0][0] * s, meas[0][1] * s);
      ctx.lineTo(meas[1][0] * s, meas[1][1] * s);
      ctx.stroke();
    }
    if (m > 0) {
      ctx.fillStyle = 'rgba(190,200,215,.75)';
      ctx.font = '11px ui-monospace, monospace';
      ctx.fillText('폭 ' + (bv[0] * m).toFixed(2) + 'm', 6, ctx.canvas.height - 6);
    }
  }

  function dot(ctx, x, y, col, r) {
    ctx.fillStyle = col;
    ctx.beginPath(); ctx.arc(x, y, r || 6, 0, Math.PI * 2); ctx.fill();
  }

  // ══════════════════════════════════════════════════════════════════
  //  조작 — 원본 화면
  // ══════════════════════════════════════════════════════════════════
  function srcXY(ev) {
    var r = $('srcC').getBoundingClientRect();
    var s = T.camera.size[0] / r.width;                 // 표시 px → 보정 영상 px
    return [(ev.clientX - r.left) * s, (ev.clientY - r.top) * s, r.width / T.camera.size[0]];
  }

  function handlesAt(x, y, scale) {
    //  잡을 수 있는 점 — 지금 편집 중인 대상의 것만. 아무것도 안 골랐으면 사각형.
    var t = find(mode) || quadT();
    if (t.kind === 'quad') {
      var q = G.quadPts(t.value), best = -1, bd = GRAB / scale;
      q.forEach(function (p, i) {
        var d = Math.hypot(p[0] - x, p[1] - y);
        if (d < bd) { bd = d; best = i; }
      });
      return best < 0 ? null : { t: t, idx: best };
    }
    if (t.kind === 'rect') {
      var v = t.value, cand = [[v[0], v[1], 0], [v[2], v[3], 1]], hit = null, d2 = GRAB / scale;
      cand.forEach(function (p) {
        var d = Math.hypot(p[0] - x, p[1] - y);
        if (d < d2) { d2 = d; hit = { t: t, idx: p[2] }; }
      });
      return hit;
    }
    return null;
  }

  function applyDrag(x, y) {
    var t = drag.t;
    if (t.kind === 'quad') {
      t.value[drag.idx * 2] = Math.round(x * 10) / 10;
      t.value[drag.idx * 2 + 1] = Math.round(y * 10) / 10;
    } else if (t.kind === 'rect') {
      var v = t.value;
      if (drag.idx === 0) { v[0] = Math.round(x); v[1] = Math.round(y); }
      else { v[2] = Math.round(x); v[3] = Math.round(y); }
      //  뒤집힌 사각형은 노드에서 빈 ROI 가 된다 — 여기서 바로 세운다
      if (v[2] < v[0]) { var tx = v[0]; v[0] = v[2]; v[2] = tx; }
      if (v[3] < v[1]) { var ty = v[1]; v[1] = v[3]; v[3] = ty; }
    }
  }

  function bindSrc() {
    var el = $('srcC');
    el.addEventListener('pointerdown', function (ev) {
      var p = srcXY(ev), h = handlesAt(p[0], p[1], p[2]);
      if (!h) return;
      snapshot();
      drag = h;
      if (mode !== h.t.id) { mode = h.t.id; renderPanel(); }
      el.setPointerCapture(ev.pointerId);
      ev.preventDefault();
    });
    el.addEventListener('pointermove', function (ev) {
      if (!drag) return;
      var p = srcXY(ev);
      applyDrag(p[0], p[1]);
      draw();
    });
    el.addEventListener('pointerup', function () {
      if (drag) { drag = null; save(); renderPanel(); }
    });
    el.addEventListener('pointercancel', function () { drag = null; });
  }

  // ══════════════════════════════════════════════════════════════════
  //  조작 — BEV 화면 (기준선·문턱·척도)
  // ══════════════════════════════════════════════════════════════════
  function bindBev() {
    var el = $('bevC');
    var moving = false;
    function xy(ev) {
      var r = el.getBoundingClientRect(), bv = bevSize();
      return [(ev.clientX - r.left) * bv[0] / r.width,
              (ev.clientY - r.top) * bv[1] / r.height];
    }
    el.addEventListener('pointerdown', function (ev) {
      var t = find(mode), p = xy(ev);
      if (t && (t.kind === 'bev_row' || t.kind === 'bev_dist')) {
        snapshot();
        setRowY(t, Math.round(p[1]));
        moving = true;
        el.setPointerCapture(ev.pointerId);
        draw();
      } else if (t && t.kind === 'scale') {
        if (meas.length >= 2) meas = [];
        if (!meas.length) snapshot();
        meas.push([Math.round(p[0]), Math.round(p[1])]);
        if (meas.length === 2) applyScale();
        draw(); renderPanel();
      }
      ev.preventDefault();
    });
    el.addEventListener('pointermove', function (ev) {
      if (!moving) return;
      var t = find(mode);
      if (t) { setRowY(t, Math.round(xy(ev)[1])); draw(); }
    });
    el.addEventListener('pointerup', function () {
      if (moving) { moving = false; save(); renderPanel(); }
    });
  }

  //  두 점 사이의 ★실측 길이★ 를 알면 픽셀당 미터가 나온다.
  function applyScale() {
    var t = one('scale');
    var real = parseFloat($('realm') && $('realm').value);
    if (!(real > 0) || meas.length !== 2) return;
    var d = Math.hypot(meas[0][0] - meas[1][0], meas[0][1] - meas[1][1]);
    if (d < 1) return;
    t.value = Math.round((real / d) * 1e6) / 1e6;
    save();
  }

  // ══════════════════════════════════════════════════════════════════
  //  패널
  // ══════════════════════════════════════════════════════════════════
  function renderPanel() {
    var box = $('targets');
    box.innerHTML = '';
    T.targets.forEach(function (t) {
      var b = document.createElement('button');
      b.className = 'btn sm' + (mode === t.id ? ' on' : '');
      b.textContent = t.label;
      b.onclick = function () {
        mode = (mode === t.id ? '' : t.id);
        meas = [];
        renderPanel(); draw();
      };
      box.appendChild(b);
    });
    var cur = find(mode);
    $('hint').textContent = cur ? cur.hint : '맞출 것을 고르세요. 사각형은 아무것도 안 고른 상태에서도 끌 수 있습니다.';
    renderExtra(cur);
    renderValues();
  }

  //  종류별로 필요한 입력칸만. 숫자로 직접 넣는 길을 반드시 둔다 —
  //  드래그는 1px 을 못 맞추는데 문턱은 1px 이 뜻을 갖는 자리가 있다.
  function renderExtra(t) {
    var ex = $('extra');
    ex.innerHTML = '';
    liveInputs = [];

    //  ★끌기만으로는 1px 을 못 맞춘다★ 문턱·사각형은 1px 이 뜻을 갖는 자리가
    //  있어서, 옛 웹앱처럼 좌표를 직접 치는 길을 같이 둔다. 두 길은 같은 값을
    //  보고 있으므로 끌면 숫자가 따라 움직이고, 숫자를 치면 그림이 따라 움직인다.
    if (t && t.kind === 'quad') {
      var qb = div('box');
      ['TL', 'TR', 'BR', 'BL'].forEach(function (tag, i) {
        qb.appendChild(xyRow(tag, [
          { lab: 'x', input: numInput('q' + (i * 2), t.value[i * 2], 1,
              function (v) { t.value[i * 2] = v; save(); draw(); },
              function () { return t.value[i * 2]; }) },
          { lab: 'y', input: numInput('q' + (i * 2 + 1), t.value[i * 2 + 1], 1,
              function (v) { t.value[i * 2 + 1] = v; save(); draw(); },
              function () { return t.value[i * 2 + 1]; }) }
        ]));
      });
      ex.appendChild(qb);
    }
    if (t && t.kind === 'rect') {
      var rb = div('box');
      [['좌상', 0, 1], ['우하', 2, 3]].forEach(function (r) {
        rb.appendChild(xyRow(r[0], [
          { lab: 'x', input: numInput('r' + r[1], t.value[r[1]], 1,
              function (v) { t.value[r[1]] = Math.round(v); save(); draw(); },
              function () { return t.value[r[1]]; }) },
          { lab: 'y', input: numInput('r' + r[2], t.value[r[2]], 1,
              function (v) { t.value[r[2]] = Math.round(v); save(); draw(); },
              function () { return t.value[r[2]]; }) }
        ]));
      });
      ex.appendChild(rb);
    }
    if (t && t.kind === 'scale') {
      var box = div('box');
      box.appendChild(label('실측 길이 [m]', numInput('realm', 3.0, 0.01, function () {
        applyScale(); draw();
      })));
      box.appendChild(label('픽셀↔미터 (직접)', numInput('px2mv', +t.value, 0.000001,
        function (v) { t.value = v; save(); draw(); },
        function () { return +t.value; })));
      var p = document.createElement('p');
      p.className = 'mono';
      p.textContent = meas.length === 2
        ? '두 점 거리 ' + Math.round(Math.hypot(meas[0][0] - meas[1][0],
                                               meas[0][1] - meas[1][1])) + 'px'
        : 'BEV 에서 길이를 아는 두 점을 찍으세요 (' + meas.length + '/2)';
      box.appendChild(p);
      ex.appendChild(box);
    }
    if (t && (t.kind === 'number' || t.kind === 'bev_row' || t.kind === 'bev_dist')) {
      var b2 = div('box');
      b2.appendChild(label(t.label, numInput('nval', +t.value, t.step || 1, function (v) {
        t.value = v; save(); draw();
      }, function () { return +t.value; })));
      ex.appendChild(b2);
    }
    if (t && t.kind === 'size') {
      var b3 = div('box');
      b3.appendChild(label('가로', numInput('bw', t.value[0], 1, function (v) {
        t.value[0] = Math.max(16, v | 0); save(); draw();
      }, function () { return t.value[0]; })));
      b3.appendChild(label('세로', numInput('bh', t.value[1], 1, function (v) {
        t.value[1] = Math.max(16, v | 0); save(); draw();
      }, function () { return t.value[1]; })));
      ex.appendChild(b3);
    }
    ex.appendChild(cameraBox());
  }

  //  카메라 내부값 — 자주 안 건드리므로 접어 둔다. 여기가 노드의 소스에 박힌
  //  값과 다르면 ★화면과 노드가 다른 그림을 본다★ — 맞춘 값이 실차에서 틀린다.
  function cameraBox() {
    var d = document.createElement('details');
    d.className = 'box';
    var s = document.createElement('summary');
    s.textContent = '카메라 (크기 · K · D)';
    d.appendChild(s);
    var c = T.camera;
    d.appendChild(label('가로', numInput('cw', c.size[0], 1, function (v) {
      c.size[0] = Math.max(16, v | 0); save(); draw();
    })));
    d.appendChild(label('세로', numInput('ch', c.size[1], 1, function (v) {
      c.size[1] = Math.max(16, v | 0); save(); draw();
    })));
    d.appendChild(label('K (fx fy cx cy)', txtInput('ck', c.K.join(' '), function (v) {
      var a = nums(v);
      if (a.length === 4) { c.K = a; save(); draw(); }
    })));
    d.appendChild(label('D (k1 k2 p1 p2 k3)', txtInput('cd', c.D.join(' '), function (v) {
      var a = nums(v);
      if (a.length >= 4) { c.D = a; save(); draw(); }
    })));
    if (media && (media.w !== c.size[0] || media.h !== c.size[1])) {
      var b = document.createElement('button');
      b.className = 'btn sm';
      b.textContent = '카메라 크기를 이 영상(' + media.w + '×' + media.h + ')에 맞추기';
      b.onclick = function () {
        c.size = [media.w, media.h]; save(); draw(); renderPanel();
      };
      d.appendChild(b);
    }
    return d;
  }

  function div(cls) { var e = document.createElement('div'); e.className = cls; return e; }
  function label(text, input) {
    var l = document.createElement('label');
    l.appendChild(document.createTextNode(text));
    l.appendChild(input);
    return l;
  }
  //  get 을 주면 ★드래그하는 동안에도 칸의 숫자가 따라 움직인다★.
  //  단, 사람이 그 칸에 커서를 두고 타이핑 중이면 건드리지 않는다 —
  //  안 그러면 "79" 까지 친 것을 화면이 791 로 되돌려 못 고치게 된다.
  function numInput(id, v, step, on, get) {
    var i = document.createElement('input');
    i.type = 'number'; i.id = id; i.value = v; i.step = step;
    i.oninput = function () {
      var x = parseFloat(i.value);
      if (!isNaN(x)) on(x);
    };
    if (get) liveInputs.push({ el: i, get: get });
    //  ★칸마다 한 번만 찍는다★ oninput 은 글자마다 오므로 거기서 찍으면
    //  되돌리기가 「한 글자 지우기」가 된다.
    i.addEventListener('focus', snapshot);
    return i;
  }

  function syncInputs() {
    liveInputs.forEach(function (n) {
      if (document.activeElement !== n.el) {
        var v = n.get();
        if (String(v) !== n.el.value) n.el.value = v;
      }
    });
  }

  //  x/y 처럼 짝으로 붙는 칸 — 라벨을 앞에 달아 한 줄로 만든다
  function xyRow(tag, fields) {
    var row = div('');
    row.style.cssText = 'display:flex;align-items:center;gap:5px;margin:3px 0';
    var t = document.createElement('span');
    t.className = 'mono';
    t.style.cssText = 'flex:0 0 30px;color:var(--dim)';
    t.textContent = tag;
    row.appendChild(t);
    fields.forEach(function (f) {
      var lab = document.createElement('span');
      lab.className = 'mono';
      lab.style.cssText = 'flex:0 0 10px;color:var(--dim)';
      lab.textContent = f.lab;
      row.appendChild(lab);
      row.appendChild(f.input);
    });
    return row;
  }
  function txtInput(id, v, on) {
    var i = document.createElement('input');
    i.type = 'text'; i.id = id; i.value = v;
    i.oninput = function () { on(i.value); };
    return i;
  }
  function nums(s) {
    return (s.match(/-?\d+(\.\d+)?([eE][-+]?\d+)?/g) || []).map(Number);
  }

  function fmt(t) {
    var v = t.value;
    if (t.kind === 'quad') {
      return G.quadPts(v).map(function (p) {
        return p[0].toFixed(0) + ',' + p[1].toFixed(0);
      }).join('  ');
    }
    if (Array.isArray(v)) return v.join(', ');
    if (t.kind === 'scale') return (+v).toFixed(6);
    if (t.kind === 'number') return (+v).toFixed(2);
    return String(Math.round(+v));
  }

  function renderValues() {
    var box = $('values');
    box.innerHTML = '';
    T.targets.forEach(function (t) {
      var r = div('row');
      var k = div('k'); k.textContent = t.label;
      var v = div('v'); v.textContent = fmt(t);
      if (t.kind === 'bev_dist' && px2m() > 0) {
        v.textContent += '  (' + (t.value * px2m()).toFixed(2) + 'm)';
      }
      r.appendChild(k); r.appendChild(v);
      r.onclick = function () { mode = t.id; meas = []; renderPanel(); draw(); };
      box.appendChild(r);
    });
  }

  function renderFoot() {
    var f = [];
    if (media) {
      f.push(media.name + ' · ' + media.w + '×' + media.h);
      if (media.kind === 'frames' && media.frames > 1) {
        f.push('프레임 ' + media.i + ' / ' + (media.frames - 1)
               + (media.fps ? '  (' + (media.i / media.fps).toFixed(2) + 's)' : ''));
      }
      if (media.w !== T.camera.size[0] || media.h !== T.camera.size[1]) {
        f.push('카메라 ' + T.camera.size.join('×') + ' 로 늘려 잽니다(노드와 같다)');
      }
    } else {
      f.push('영상 없음');
    }
    var c = lastCal;
    if (c) f.push('유효영역 ' + c.roi.join(','));
    if (playing) f.push('▶ 재생중');
    if (!showGrid) f.push('격자 OFF');
    if (!useUndist) f.push('왜곡보정 OFF');
    $('foot').textContent = f.join('  ·  ');
  }

  // ══════════════════════════════════════════════════════════════════
  //  영상·사진 열기 — ★이 브라우저 안에서만★ 열린다
  // ══════════════════════════════════════════════════════════════════
  //  ★코덱을 미리 알아본다★ [2026-09-06]
  //  실패한 실측: 이 기계에서 녹화한 mp4 가 전부 `mp4v`(MPEG-4 Part 2)였다.
  //  cv2.VideoWriter 의 기본 코덱인데, 브라우저가 mp4 안에서 받아 주는 것은
  //  H.264(avc1)·AV1 뿐이라 ★한 장도 안 열린다★(용량과는 무관하다 —
  //  3.5MB 짜리도 못 연다). 그때 「열지 못합니다」만 띄우면 사람은 파일이
  //  깨졌다고 생각하고 다른 영상을 찾는다. 그래서 ★무엇이 문제인지와 고칠
  //  명령까지★ 말해 준다.
  //
  //  박스 파서를 쓰지 않는다 — 필요한 것은 stsd 안의 네 글자 이름 하나뿐이라
  //  앞뒤 조각에서 그 문자열을 찾는다(moov 는 파일 끝에 있는 경우가 흔해서
  //  뒤쪽도 본다). 어디까지나 ★안내용★ 이고, 열어 보는 것은 그대로 해 본다.
  var CODEC_TAGS = [
    ['avc1', true, 'H.264'], ['h264', true, 'H.264'], ['av01', true, 'AV1'],
    ['vp09', true, 'VP9'], ['vp08', true, 'VP8'],
    ['mp4v', false, 'MPEG-4 Part 2 (cv2 의 mp4v)'],
    ['hvc1', false, 'HEVC/H.265'], ['hev1', false, 'HEVC/H.265'],
    ['mjpa', false, 'Motion JPEG'], ['MJPG', false, 'Motion JPEG']
  ];

  function sniffCodec(file) {
    var CHUNK = 1024 * 512;
    function read(blob) {
      return new Promise(function (res) {
        var r = new FileReader();
        r.onload = function () { res(new Uint8Array(r.result)); };
        r.onerror = function () { res(new Uint8Array(0)); };
        r.readAsArrayBuffer(blob);
      });
    }
    var head = file.slice(0, Math.min(CHUNK, file.size));
    var tail = file.slice(Math.max(0, file.size - CHUNK));
    return Promise.all([read(head), read(tail)]).then(function (parts) {
      for (var i = 0; i < CODEC_TAGS.length; i++) {
        var tag = CODEC_TAGS[i];
        for (var k = 0; k < parts.length; k++) {
          if (findAscii(parts[k], tag[0])) {
            return { tag: tag[0], playable: tag[1], label: tag[2] };
          }
        }
      }
      return null;                      // 모르겠으면 아무 말도 하지 않는다
    });
  }

  function findAscii(buf, s) {
    var n = s.length, i, j;
    for (i = 0; i + n <= buf.length; i++) {
      for (j = 0; j < n; j++) {
        if (buf[i + j] !== s.charCodeAt(j)) break;
      }
      if (j === n) return true;
    }
    return false;
  }

  function badCodecMessage(info, name) {
    return (info ? '이 영상은 ' + info.label + ' 이라 브라우저가 열지 못합니다'
                 : '이 영상을 브라우저가 열지 못합니다')
      + ' — ★용량 때문이 아닙니다.★ H.264 로 한 번 바꾸면 열립니다'
      + (info && info.tag === 'mp4v'
         ? ' (녹화가 cv2 의 mp4v 로 굽고 있습니다).' : '.');
  }

  function convertCommand(name) {
    return 'python3 -m tb.encode ' + (name || '<영상>')
         + '      # 또는: ffmpeg -i ' + (name || '<영상>')
         + ' -an -c:v h264_nvenc -preset p4 -cq 26 -pix_fmt yuv420p'
         + ' -movflags +faststart ' + (name || '<영상>').replace(/\.[^.]+$/, '') + '__web.mp4';
  }

  function openFile(f) {
    var url = URL.createObjectURL(f);
    if (/^video/.test(f.type) || /\.(mp4|webm|mov|mkv|avi|m4v)$/i.test(f.name)) {
      openVideo(f, url);
    } else {
      var im = new Image();
      im.onload = function () {
        media = { el: im, kind: 'image', w: im.naturalWidth, h: im.naturalHeight,
                  dur: 0, name: f.name };
        $('timeline').hidden = true;
        clearBanner();
        upload(); draw(); renderPanel();
      };
      im.onerror = function () { banner('이 사진을 열지 못했습니다.'); };
      im.src = url;
    }
  }

  // ══════════════════════════════════════════════════════════════════
  //  프레임 소스 — 서버(cv2)든 페이지 안 wasm(mp4v)이든 ★프레임 번호로★ 받는다
  // ══════════════════════════════════════════════════════════════════
  function api(path) {
    return fetch(path).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
        return j;
      }, function () { throw new Error('HTTP ' + r.status); });
    });
  }

  //  ★프레임 소스★ — info() 와 frame(i) → Promise<그림> 두 개.
  //  서버(cv2)와 페이지 안 wasm(mp4frames.js)이 같은 모양이라 화면은 하나다.
  function serverSource(path) {
    return api('api/info?path=' + encodeURIComponent(path)).then(function (inf) {
      var url = null;
      return {
        kind: inf.kind,
        info: function () { return inf; },
        //  ★Image.src 에 주소를 바로 꽂지 않는다★ 그러면 서버가 돌려준 오류
        //  메시지를 읽을 길이 없어 화면이 이유 없이 비어 버린다. blob 으로 받아
        //  오류는 본문에서 읽고, 성공한 것만 그림으로 만든다.
        frame: function (i) {
          return fetch('api/frame?path=' + encodeURIComponent(path) + '&i=' + i)
            .then(function (r) {
              if (!r.ok) return r.json().then(function (j) { throw new Error(j.error); });
              return r.blob();
            })
            .then(function (b) {
              return new Promise(function (res, rej) {
                var img = new Image();
                if (url) URL.revokeObjectURL(url);
                url = URL.createObjectURL(b);
                img.onload = function () { res(img); };
                img.onerror = function () { rej(new Error('그림을 읽지 못했습니다')); };
                img.src = url;
              });
            });
        }
      };
    });
  }

  function openServerFile(path) {
    serverSource(path).then(function (src) {
      openFrames(src, path.split(/[\\/]/).pop(), src.kind !== 'video');
    }).catch(function (e) { banner('열지 못했습니다 — ' + e.message); });
  }

  function openFrames(src, name, still) {
    var inf = src.info();
    media = { el: null, kind: 'frames', src: src, name: inf.name || name,
              w: inf.w, h: inf.h, frames: inf.frames, fps: inf.fps, i: 0,
              dur: inf.fps > 0 ? inf.frames / inf.fps : 0 };
    $('timeline').hidden = !!still;
    $('seek').value = 0;
    clearBanner();
    loadFrame(0);
    renderPanel();
  }

  function loadFrame(i) {
    if (!media || media.kind !== 'frames') return;
    var m = media;
    m.i = Math.max(0, Math.min(i, Math.max(0, m.frames - 1)));
    var seq = ++frameSeq;
    m.src.frame(m.i)
      .then(function (pic) {
        //  ★늦게 온 옛 그림은 버린다★ 끌기는 요청을 연달아 보내고 답은 순서대로
        //  오지 않을 수 있다 — 그대로 그리면 손을 놓은 자리와 다른 그림이 남는다.
        if (seq !== frameSeq || media !== m || !pic) return;
        m.el = pic;
        upload(); draw(); renderFoot();
      })
      .catch(function (e) {
        if (seq === frameSeq) banner('프레임을 읽지 못했습니다 — ' + e.message);
      });
  }

  function showBrowser(dir) {
    api('api/browse?dir=' + encodeURIComponent(dir || '')).then(function (r) {
      lastDir = r.dir;
      renderBrowser(r, { up: function () { showBrowser(r.up); },
                         dir: function (d) { showBrowser(d.path); },
                         file: function (f) { openServerFile(f.path); },
                         typed: function (p) { showBrowser(p); } });
    }).catch(function (e) { banner('폴더를 읽지 못했습니다 — ' + e.message); });
  }

  //  목록 그리기 — 서버의 폴더든 이 브라우저가 고른 폴더든 같은 모양이다.
  //  r = {dir, error?, dirs:[{name,…}], files:[{name, kind, mb,…}]}
  function renderBrowser(r, on) {
    (function () {
      var ex = $('extra');
      ex.innerHTML = '';
      var box = div('box');
      var head = div('');
      head.className = 'mono';
      head.style.cssText = 'margin-bottom:6px;word-break:break-all';
      head.textContent = r.dir;
      box.appendChild(head);
      if (r.error) {
        var er = document.createElement('p');
        er.className = 'bad'; er.textContent = r.error;
        box.appendChild(er);
      }
      var up = document.createElement('button');
      up.className = 'btn sm'; up.textContent = '⬆ 위로';
      up.onclick = on.up;
      up.disabled = !on.up;
      box.appendChild(up);
      var close = document.createElement('button');
      close.className = 'btn sm'; close.textContent = '닫기';
      close.style.marginLeft = '6px';
      close.onclick = function () { renderPanel(); };
      box.appendChild(close);

      (on.extra || []).forEach(function (x) {
        var e = document.createElement('button');
        e.className = 'btn sm'; e.textContent = x[0]; e.onclick = x[1];
        e.style.marginLeft = '6px';
        box.appendChild(e);
      });

      //  ★경로를 직접 칠 수 있게 둔다★ 목록만 있으면 깊은 폴더까지 여러 번
      //  눌러야 한다. 서버는 여기 친 경로도 뿌리 안인지 그대로 검사한다.
      //  (브라우저가 고른 폴더는 경로가 없다 — 그때는 칸을 두지 않는다)
      if (on.typed) {
        var go = txtInput('gopath', '', function () { /* Enter 로 간다 */ });
        go.placeholder = '폴더 경로를 직접 입력 (Enter)';
        go.style.marginTop = '6px';
        go.onkeydown = function (ev) {
          if (ev.key === 'Enter' && go.value.trim()) on.typed(go.value.trim());
        };
        box.appendChild(go);
      }

      var list = div('');
      list.style.cssText = 'max-height:320px;overflow:auto;margin-top:6px';
      r.dirs.forEach(function (d) {
        var b = document.createElement('button');
        b.className = 'btn sm';
        b.style.cssText = 'display:block;width:100%;text-align:left;margin:2px 0';
        b.textContent = '📁 ' + d.name;
        b.onclick = function () { on.dir(d); };
        list.appendChild(b);
      });
      r.files.forEach(function (f) {
        var b = document.createElement('button');
        b.className = 'btn sm';
        b.style.cssText = 'display:block;width:100%;text-align:left;margin:2px 0';
        b.textContent = (f.kind === 'video' ? '🎞 ' : '🖼 ') + f.name
                      + '   ' + f.mb + 'MB';
        b.onclick = function () { on.file(f); };
        list.appendChild(b);
      });
      if (!r.dirs.length && !r.files.length) {
        var em = document.createElement('p');
        em.className = 'mono';
        em.textContent = '이 폴더에는 열 수 있는 영상·사진이 없습니다';
        list.appendChild(em);
      }
      box.appendChild(list);
      ex.appendChild(box);
    })();
  }

  //  ── 서버가 없을 때: 이 브라우저가 폴더를 직접 읽는다 ──────────────
  //  showDirectoryPicker 는 Chrome·Edge 에만 있다. 없으면 파일 하나씩 고른다.
  //  ★폴더 내용은 이 브라우저 안에서만 읽힌다★ 아무 데도 보내지 않는다.
  var MEDIA_RE = /\.(mp4|m4v|mov|webm|mkv|avi|png|jpe?g|bmp|webp)$/i;

  function pickFolder() {
    window.showDirectoryPicker({ id: 'cam-studio', mode: 'read' }).then(function (h) {
      localDirs = [{ name: h.name, handle: h }];
      showLocal();
    }).catch(function (e) {
      if (e && e.name !== 'AbortError') banner('폴더를 열지 못했습니다 — ' + e.message);
    });
  }

  function showLocal() {
    var cur = localDirs[localDirs.length - 1].handle;
    var dirs = [], files = [];
    var it = cur.values();
    function next() {
      return it.next().then(function (r) {
        if (r.done) return null;
        var h = r.value;
        if (h.name.charAt(0) === '.') return next();
        if (h.kind === 'directory') { dirs.push({ name: h.name, handle: h }); return next(); }
        if (!MEDIA_RE.test(h.name)) return next();
        return h.getFile().then(function (f) {
          files.push({ name: h.name, file: f, mb: (f.size / 1048576).toFixed(1),
                       kind: /\.(png|jpe?g|bmp|webp)$/i.test(h.name) ? 'image' : 'video' });
          return next();
        });
      });
    }
    next().then(function () {
      var by = function (a, b) { return a.name < b.name ? -1 : a.name > b.name ? 1 : 0; };
      dirs.sort(by); files.sort(by);
      renderBrowser({ dir: localDirs.map(function (d) { return d.name; }).join(' / '),
                      dirs: dirs, files: files }, {
        up: localDirs.length > 1 ? function () { localDirs.pop(); showLocal(); } : null,
        dir: function (d) { localDirs.push(d); showLocal(); },
        file: function (f) { openFile(f.file); },
        extra: [['다른 폴더', pickFolder], ['파일 하나', function () { $('file').click(); }]]
      });
    }).catch(function (e) { banner('폴더를 읽지 못했습니다 — ' + e.message); });
  }

  function openLocal() {
    if (!window.showDirectoryPicker) { $('file').click(); return; }
    if (localDirs) showLocal(); else pickFolder();
  }

  function openVideo(f, url) {
    sniffCodec(f).then(function (info) {
      //  ★mp4v 는 페이지 안에서 푼다★ <video> 는 이 코덱을 못 연다.
      if (info && info.tag === 'mp4v' && window.Mp4Frames) {
        banner('mp4v 영상을 이 페이지 안에서 풀고 있습니다…');
        window.Mp4Frames.openFile(f).then(function (src) {
          if (!src) { openVideoTag(f, url, info); return; }
          URL.revokeObjectURL(url);
          openFrames(src, f.name);
        }).catch(function (e) {
          banner(badCodecMessage(info, f.name) + ' (페이지 안 해독도 실패: ' + e.message + ')',
                 convertCommand(f.name));
        });
        return;
      }
      openVideoTag(f, url, info);
    });
  }

  function openVideoTag(f, url, info) {
    //  못 여는 코덱이면 ★열어 보기 전에★ 말해 준다. 그래도 시도는 한다 —
    //  이 스니핑은 안내용이라 틀릴 수 있고, 틀렸으면 그냥 열리면 된다.
    if (info && info.playable === false) {
      banner(badCodecMessage(info, f.name), convertCommand(f.name));
    }

    var v = document.createElement('video');
    v.src = url; v.muted = true; v.playsInline = true; v.preload = 'auto';

    //  ★loadeddata 가 아니라 loadedmetadata 에서 연다★ 2GB 짜리 주행영상은
    //  첫 프레임까지 디코드되기를 기다리면 한참 걸린다(헤드리스에서는 아예
    //  안 온 적도 있다). 크기·길이는 메타데이터만으로 다 알 수 있으므로 화면을
    //  먼저 열고, 그림은 아래 seek 로 첫 프레임을 받아 채운다.
    v.addEventListener('loadedmetadata', function () {
      media = { el: v, kind: 'video', w: v.videoWidth, h: v.videoHeight,
                dur: isFinite(v.duration) ? v.duration : 0, name: f.name };
      $('timeline').hidden = false;
      $('seek').value = 0;
      clearBanner();
      draw(); renderPanel();
      //  첫 프레임 요청 — 0 으로 두면 seeked 가 안 오는 브라우저가 있다
      try { v.currentTime = Math.min(0.04, (media.dur || 1) / 2); } catch (e) { /* 무시 */ }
    });
    v.addEventListener('seeked', function () { upload(); draw(); });
    v.addEventListener('loadeddata', function () { upload(); draw(); });
    v.addEventListener('error', function () {
      banner(badCodecMessage(info, f.name), convertCommand(f.name));
    });
  }

  function upload() {
    if (!renderer || !media || !media.el) return;
    try { renderer.setSource(media.el); } catch (e) { banner('그리기 실패: ' + e.message); }
  }

  function bindTimeline() {
    var seek = $('seek');
    seek.addEventListener('input', function () {
      if (!media) return;
      var f = seek.value / 1000;
      if (media.kind === 'frames') {
        loadFrame(Math.round(f * Math.max(0, media.frames - 1)));
      } else if (media.kind === 'video') {
        media.el.currentTime = media.dur * f;
      }
      renderFoot();
    });
    $('back').onclick = function () { stepFrame(-1); };
    $('fwd').onclick = function () { stepFrame(1); };
  }

  //  ★프레임 단위로 움직인다★ 보정은 「이 프레임에서 사각형이 맞나」를 보는
  //  일이라, 0.1초씩 뛰면 맞출 수가 없다. 프레임 소스(서버·mp4v)에서는 정확히
  //  한 프레임이고, <video>(H.264) 에서는 30fps 를 가정한 근사다.
  function stepFrame(d) {
    if (!media) return;
    var seek = $('seek');
    if (media.kind === 'frames') {
      loadFrame(media.i + d);
      seek.value = Math.round(1000 * media.i / Math.max(1, media.frames - 1));
    } else if (media.kind === 'video') {
      var t = Math.max(0, Math.min(media.dur, media.el.currentTime + d / 30));
      media.el.currentTime = t;
      seek.value = Math.round(1000 * t / (media.dur || 1));
    }
    renderFoot();
  }

  //  ★재생은 타이머로 한다★ <video> 의 play() 를 쓰면 프레임 소스(그림 한 장씩
  //  받는 쪽 — 서버·mp4v)와 길이 갈라진다. 10fps 면 「어느 프레임에서 맞출까」를 고르기에
  //  충분하고, 서버는 한 장 1ms, 페이지 안 mp4v 는 1080p 한 장 20ms 남짓이라 따라온다.
  function togglePlay() {
    if (playing) {
      clearInterval(playing);
      playing = null;
    } else if (media && media.kind !== 'image') {
      playing = setInterval(function () {
        //  끝에 닿으면 스스로 멈춘다 — 안 그러면 마지막 프레임을 계속 다시 받는다
        if (media.kind === 'frames' && media.i >= media.frames - 1) { togglePlay(); return; }
        if (media.kind === 'video' && media.el.currentTime >= media.dur - 0.05) {
          togglePlay(); return;
        }
        stepFrame(1);
      }, 100);
    }
    renderFoot();
  }

  // ══════════════════════════════════════════════════════════════════
  //  내보내기 — 이 화면의 산출물은 ★노드 파라미터 몇 줄★ 이다
  // ══════════════════════════════════════════════════════════════════
  function numFmt(kind, v) {
    if (kind === 'size' || kind === 'rect') return String(Math.round(v));
    //  ★사각형은 반드시 실수로 쓴다★ 노드가 2026-08-31 자로 기본값을 float 로
    //  바꿔서, 정수로 주면 InvalidParameterTypeException 으로 기동 즉시 죽는다.
    if (kind === 'quad') return (+v).toFixed(1);
    if (kind === 'scale') return (+v).toFixed(6);
    if (kind === 'number') return (+v).toFixed(3);
    return (+v).toFixed(1);
  }

  function buildParams() {
    var out = {};
    T.targets.forEach(function (t) {
      (t.params || []).forEach(function (p) {
        var node = p[0], name = p[1], idx = p[2];
        out[node] = out[node] || {};
        if (idx == null) {
          out[node][name] = Array.isArray(t.value)
            ? '[' + t.value.map(function (v) { return numFmt(t.kind, v); }).join(', ') + ']'
            : numFmt(t.kind, t.value);
        } else {
          out[node][name] = numFmt(t.kind, t.value[idx]);
        }
      });
    });
    return out;
  }

  function yamlText() {
    var p = buildParams(), lines = [
      '# 카메라 보정 스튜디오 — ' + new Date().toISOString().slice(0, 16).replace('T', ' '),
      '# local.yaml 의 params: 아래에 붙이거나, 노드 파라미터로 그대로 준다.',
      '# ★쓰지 않는 노드의 블록은 지운다★ 없는 파라미터를 주면 노드가 기동하지 않는다.',
      'params:'
    ];
    Object.keys(p).forEach(function (node) {
      lines.push('  ' + node + ':');
      Object.keys(p[node]).forEach(function (k) {
        lines.push('    ' + k + ': ' + p[node][k]);
      });
    });
    return lines.join('\n') + '\n';
  }

  function download(name, text, type) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: type || 'text/plain' }));
    a.download = name;
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
  }

  function showExport() {
    var ex = $('extra');
    ex.innerHTML = '';
    var box = div('box');
    var pre = document.createElement('pre');
    pre.className = 'mono';
    pre.style.whiteSpace = 'pre-wrap';
    pre.textContent = yamlText();
    box.appendChild(pre);
    var row = div('');
    [['params.yaml 받기', function () { download('params.yaml', yamlText(), 'text/yaml'); }],
     ['설정(JSON) 받기', function () {
       download('tuning.json', JSON.stringify(T, null, 1), 'application/json');
     }],
     ['복사', function () {
       if (navigator.clipboard) navigator.clipboard.writeText(yamlText());
     }],
     ['닫기', function () { renderPanel(); }]
    ].forEach(function (b) {
      var e = document.createElement('button');
      e.className = 'btn sm'; e.textContent = b[0]; e.onclick = b[1];
      e.style.marginRight = '6px';
      row.appendChild(e);
    });
    box.appendChild(row);
    ex.appendChild(box);
  }

  function importJSON(text) {
    var o;
    try { o = JSON.parse(text); } catch (e) { banner('JSON 을 읽지 못했습니다.'); return; }
    if (!o || !o.camera || !o.targets) { banner('이 파일에는 camera/targets 가 없습니다.'); return; }
    //  ★모르는 항목은 버린다★ 화면이 아는 종류만 남겨야 예전 형식을 열어도
    //  화면이 깨지지 않는다. 값은 id 로 맞춘다.
    var base = clone(window.TUNING);
    base.camera = o.camera;
    base.targets.forEach(function (t) {
      var m = (o.targets || []).filter(function (x) { return x.id === t.id; })[0];
      if (m && m.value != null) t.value = m.value;
    });
    T = base;
    mode = ''; meas = [];
    save(); renderPanel(); draw();
  }

  // ══════════════════════════════════════════════════════════════════
  //  스스로 대조 — 「이 화면의 기하가 cv2 와 같은가」
  // ══════════════════════════════════════════════════════════════════
  function selfCheck() {
    var ref = window.GEOM_REF;
    if (!ref) return null;
    var worst = 0;
    ref.cases.forEach(function (c) {
      var opt = G.optimalNewCameraMatrix(c.K, c.D, c.size, c.alpha);
      opt.K.forEach(function (v, i) { worst = Math.max(worst, Math.abs(v - c.newK[i])); });
      opt.roi.forEach(function (v, i) { worst = Math.max(worst, Math.abs(v - c.roi[i])); });
      var cc = G.makeCal({ size: c.size, K: c.K, D: c.D, alpha: c.alpha,
                           quad: c.quad, bev: c.bev });
      c.bevToSrc.forEach(function (s) {
        var p = G.bevToSource(s[0], s[1], cc);
        worst = Math.max(worst, Math.hypot(p[0] - s[2], p[1] - s[3]));
      });
    });
    return worst;
  }

  //  띠 — 사유만 적고 끝내지 않는다. ★고칠 명령을 같이 준다★
  //  (여기서 막히는 사람은 대개 터미널 앞에 있다).
  function banner(msg, cmd) {
    var b = $('banner');
    b.hidden = false;
    b.innerHTML = '';
    b.appendChild(document.createTextNode(msg));
    if (!cmd) return;
    var pre = document.createElement('pre');
    pre.className = 'mono';
    pre.style.cssText = 'margin:6px 0 0;white-space:pre-wrap;user-select:all';
    pre.textContent = cmd;
    b.appendChild(pre);
    var cp = document.createElement('button');
    cp.className = 'btn sm';
    cp.textContent = '명령 복사';
    cp.onclick = function () {
      if (navigator.clipboard) {
        navigator.clipboard.writeText(cmd);
        cp.textContent = '복사됨';
      }
    };
    b.appendChild(cp);
  }

  function clearBanner() {
    var b = $('banner');
    b.hidden = true;
    b.innerHTML = '';
  }

  // ══════════════════════════════════════════════════════════════════
  //  시작
  // ══════════════════════════════════════════════════════════════════
  function init() {
    try {
      renderer = window.Render.create();
    } catch (e) {
      renderer = null;
    }
    if (!renderer) {
      banner('이 브라우저에서 WebGL 을 쓸 수 없습니다 — 그림 없이 값만 다룰 수 있습니다.');
    }

    var err = selfCheck();
    if (err == null) {
      $('check').textContent = '대조표 없음';
    } else if (err > 0.1) {
      $('check').textContent = '기하 대조 ' + err.toFixed(3) + 'px';
      $('check').className = 'mono bad';
      banner('★이 화면의 기하가 cv2 와 어긋납니다 (' + err.toFixed(3) + 'px)★ '
             + '여기서 맞춘 값은 실차에서 틀립니다 — 고치기 전에는 쓰지 마세요.');
    } else {
      $('check').textContent = '기하 대조 ' + err.toFixed(3) + 'px (cv2 ' + window.GEOM_REF.cv2 + ')';
      $('check').className = 'mono ok';
    }

    //  ★서버가 있나★ 있으면 이 기계의 영상을 코덱 상관없이 연다.
    //  없으면(github.io·파일로 연 경우) 조용히 브라우저 쪽 열기로 간다 — fetch 가 실패하는
    //  것이 정상적인 경우라 오류를 화면에 내지 않는다.
    fetch('api/ping').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !j.studio) return;
        server = j;
        //  ★버튼을 하나 더 만들지 않는다★ 예전에는 «이 기계의 영상 열기» 를
        //  따로 붙였는데, 같은 일을 두 갈래로 하니 "왜 이건 열리고 저건 안
        //  열리지" 가 생겼다. 여는 길은 하나고, 서버가 있으면 그 길이 폴더
        //  목록으로 바뀔 뿐이다.
        renderFoot();
      })
      .catch(function () { /* 정적으로 연 것이다 */ });

    $('open').onclick = function () {
      if (server) showBrowser(lastDir);
      else openLocal();
    };
    $('file').onchange = function (e) { if (e.target.files[0]) openFile(e.target.files[0]); };
    $('loadf').onchange = function (e) {
      var f = e.target.files[0];
      if (!f) return;
      var r = new FileReader();
      r.onload = function () { importJSON(r.result); };
      r.readAsText(f);
    };
    $('save').onclick = showExport;
    $('reset').onclick = function () {
      if (!window.confirm('맞춘 값을 전부 기본값으로 되돌립니다.')) return;
      T = clone(window.TUNING); mode = ''; meas = [];
      save(); renderPanel(); draw();
    };

    //  ★단축키★ 옛 웹앱에 있던 것들을 그대로 되살렸다. 보정은 「프레임을 옮겨
    //  가며 같은 사각형을 확인하는」 일이라, 손이 마우스를 떠나지 않아야 한다.
    window.addEventListener('keydown', function (ev) {
      //  ★칸에 커서가 있으면 아무것도 하지 않는다★ 안 그러면 좌표칸에 "1" 을
      //  치는 순간 편집 대상이 바뀌고, 화살표는 칸의 숫자와 사각형을 동시에
      //  움직인다(숫자 입력을 붙이면서 생긴 구멍이라 여기서 막는다).
      var el = document.activeElement;
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) return;
      if (ev.metaKey || ev.ctrlKey || ev.altKey) return;

      // ── 프레임 옮기기 ──
      var jump = { ' ': 'play', ',': -1, '.': 1, '[': -30, ']': 30 }[ev.key];
      if (jump !== undefined) {
        if (jump === 'play') togglePlay(); else stepFrame(jump);
        ev.preventDefault();
        return;
      }

      // ── 보기 토글 · 되돌리기 ──
      if (ev.key === 'g' || ev.key === 'G') {
        showGrid = !showGrid; draw(); ev.preventDefault(); return;
      }
      if (ev.key === 'u' || ev.key === 'U') {
        useUndist = !useUndist; draw(); ev.preventDefault(); return;
      }
      if (ev.key === 'r' || ev.key === 'R') {
        if (undo()) ev.preventDefault();
        return;
      }

      // ── 1~9 로 편집 대상 고르기 (오른쪽 단추 차례와 같다) ──
      if (ev.key >= '1' && ev.key <= '9') {
        var t9 = T.targets[+ev.key - 1];
        if (t9) {
          mode = t9.id; meas = [];
          renderPanel(); draw();
          ev.preventDefault();
        }
        return;
      }
      if (ev.key === 'Escape') {
        mode = ''; meas = []; renderPanel(); draw();
        return;
      }

      // ── 화살표로 1px, Shift 면 10px ──
      var t = find(mode);
      if (!t) return;
      var d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0],
                ArrowUp: [0, -1], ArrowDown: [0, 1] }[ev.key];
      if (!d) return;
      var step = ev.shiftKey ? 10 : 1;
      snapshot();
      if (t.kind === 'bev_row' || t.kind === 'bev_dist') {
        setRowY(t, rowY(t) + d[1] * step);
      } else if (t.kind === 'quad') {
        for (var i = 0; i < 8; i += 2) {
          t.value[i] += d[0] * step; t.value[i + 1] += d[1] * step;
        }
      } else if (t.kind === 'rect') {
        t.value[0] += d[0] * step; t.value[2] += d[0] * step;
        t.value[1] += d[1] * step; t.value[3] += d[1] * step;
      } else {
        undoStack.pop();          // 안 움직였으면 되돌릴 것도 없다
        return;
      }
      ev.preventDefault();
      save(); draw();
    });

    bindSrc(); bindBev(); bindTimeline();
    renderPanel(); draw();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else { init(); }
})();
