/* 카메라 보정 스튜디오 — 화면과 조작.
 *
 * ★서버가 없다★ [2026-09-06] 예전에는 파이썬 서버가 그림을 그려 JPEG 로
 * 내려보냈다. 지금은 정적 페이지 하나다 — 영상은 이 브라우저 안에서만 열리고
 * 아무 데도 올라가지 않는다. 그래서 로그인도, 올리기도, 폴더 훑기도 없다.
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

  function cal() {
    var c = T.camera;
    lastCal = G.makeCal({ size: c.size, K: c.K, D: c.D, alpha: c.alpha,
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
    if (m > 0) {
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
        setRowY(t, Math.round(p[1]));
        moving = true;
        el.setPointerCapture(ev.pointerId);
        draw();
      } else if (t && t.kind === 'scale') {
        if (meas.length >= 2) meas = [];
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
    if (t && t.kind === 'scale') {
      var box = div('box');
      box.appendChild(label('실측 길이 [m]', numInput('realm', 3.0, 0.01, function () {
        applyScale(); draw();
      })));
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
      })));
      ex.appendChild(b2);
    }
    if (t && t.kind === 'size') {
      var b3 = div('box');
      b3.appendChild(label('가로', numInput('bw', t.value[0], 1, function (v) {
        t.value[0] = Math.max(16, v | 0); save(); draw();
      })));
      b3.appendChild(label('세로', numInput('bh', t.value[1], 1, function (v) {
        t.value[1] = Math.max(16, v | 0); save(); draw();
      })));
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
  function numInput(id, v, step, on) {
    var i = document.createElement('input');
    i.type = 'number'; i.id = id; i.value = v; i.step = step;
    i.oninput = function () {
      var x = parseFloat(i.value);
      if (!isNaN(x)) on(x);
    };
    return i;
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
      if (media.w !== T.camera.size[0] || media.h !== T.camera.size[1]) {
        f.push('카메라 ' + T.camera.size.join('×') + ' 로 늘려 잽니다(노드와 같다)');
      }
    } else {
      f.push('영상 없음');
    }
    var c = lastCal;
    if (c) f.push('유효영역 ' + c.roi.join(','));
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

  function openVideo(f, url) {
    var info = null;
    sniffCodec(f).then(function (i) {
      info = i;
      //  못 여는 코덱이면 ★열어 보기 전에★ 말해 준다. 그래도 시도는 한다 —
      //  이 스니핑은 안내용이라 틀릴 수 있고, 틀렸으면 그냥 열리면 된다.
      if (info && info.playable === false) {
        banner(badCodecMessage(info, f.name), convertCommand(f.name));
      }
    });

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
    if (!renderer || !media) return;
    try { renderer.setSource(media.el); } catch (e) { banner('그리기 실패: ' + e.message); }
  }

  function bindTimeline() {
    var seek = $('seek');
    seek.addEventListener('input', function () {
      if (!media || media.kind !== 'video') return;
      media.el.currentTime = media.dur * (seek.value / 1000);
      $('tpos').textContent = media.el.currentTime.toFixed(2) + 's / ' + media.dur.toFixed(2) + 's';
    });
    function step(d) {
      if (!media || media.kind !== 'video') return;
      var t = Math.max(0, Math.min(media.dur, media.el.currentTime + d));
      media.el.currentTime = t;
      seek.value = Math.round(1000 * t / media.dur);
      $('tpos').textContent = t.toFixed(2) + 's / ' + media.dur.toFixed(2) + 's';
    }
    //  1/30 초 — 실차 카메라가 30fps 라 한 프레임에 해당한다
    $('back').onclick = function () { step(-1 / 30); };
    $('fwd').onclick = function () { step(1 / 30); };
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

    //  화살표로 1px 씩 — 드래그로는 못 맞추는 자리가 있다
    window.addEventListener('keydown', function (ev) {
      var t = find(mode);
      if (!t || ev.metaKey || ev.ctrlKey) return;
      var d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[ev.key];
      if (!d) return;
      var step = ev.shiftKey ? 10 : 1;
      if (t.kind === 'bev_row' || t.kind === 'bev_dist') {
        setRowY(t, rowY(t) + d[1] * step);
      } else if (t.kind === 'quad') {
        for (var i = 0; i < 8; i += 2) {
          t.value[i] += d[0] * step; t.value[i + 1] += d[1] * step;
        }
      } else if (t.kind === 'rect') {
        t.value[0] += d[0] * step; t.value[2] += d[0] * step;
        t.value[1] += d[1] * step; t.value[3] += d[1] * step;
      } else { return; }
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
