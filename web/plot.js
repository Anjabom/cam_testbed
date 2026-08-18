/* 시계열 플롯 — 캔버스 직접. 라이브러리 없음.
 *
 * 신호마다 스케일이 크게 다르므로(θ 는 도, cte 는 미터, flags 는 비트)
 * ★신호별로 따로 정규화★해서 겹쳐 그린다. 절대값은 커서 판독으로 읽는다. */
(function (global) {
  'use strict';

  var PALETTE = ['#37489B', '#A9620A', '#26714F', '#A63631', '#6B4C9A', '#1F7A8C'];
  var PALETTE_DARK = ['#8A9DEA', '#E9A63F', '#4FB88A', '#E27E78', '#B49AE0', '#5FC7D8'];

  function isDark() {
    var t = document.documentElement.getAttribute('data-theme');
    if (t === 'dark') return true;
    if (t === 'light') return false;
    return global.matchMedia &&
           global.matchMedia('(prefers-color-scheme: dark)').matches;
  }
  function css(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }
  function palette() { return isDark() ? PALETTE_DARK : PALETTE; }

  function TimePlot(canvas, opts) {
    this.c = canvas;
    this.opts = opts || {};
    this.rows = [];
    this.keys = [];
    this.cursor = null;      // 프레임 번호
    this.onCursor = null;
    var self = this;

    canvas.addEventListener('mousemove', function (e) { self._pick(e, false); });
    canvas.addEventListener('click', function (e) { self._pick(e, true); });
    canvas.addEventListener('mouseleave', function () {
      self.cursor = null; self.draw(); if (self.onCursor) self.onCursor(null);
    });
    global.addEventListener('resize', function () { self.draw(); });
  }

  TimePlot.prototype.setData = function (rows, keys) {
    this.rows = rows || [];
    this.keys = keys || [];
    this.draw();
  };

  TimePlot.prototype.setCursorFrame = function (f, notify) {
    this.cursor = f;
    this.draw();
    if (notify && this.onCursor) this.onCursor(f);
  };

  TimePlot.prototype._geom = function () {
    var dpr = global.devicePixelRatio || 1;
    var wCss = this.c.clientWidth || 640;
    var hCss = this.opts.height || 260;
    if (this.c.width !== Math.round(wCss * dpr) ||
        this.c.height !== Math.round(hCss * dpr)) {
      this.c.width = Math.round(wCss * dpr);
      this.c.height = Math.round(hCss * dpr);
      this.c.style.height = hCss + 'px';
    }
    return { dpr: dpr, w: wCss, h: hCss, pad: { l: 46, r: 12, t: 10, b: 22 } };
  };

  TimePlot.prototype._frames = function () {
    var f = [];
    for (var i = 0; i < this.rows.length; i++) {
      var v = this.rows[i].frame;
      if (typeof v === 'number') f.push(v);
    }
    return f;
  };

  TimePlot.prototype._pick = function (e, notify) {
    var g = this._geom();
    var r = this.c.getBoundingClientRect();
    var x = e.clientX - r.left;
    var fr = this._frames();
    if (!fr.length) return;
    var f0 = fr[0], f1 = fr[fr.length - 1];
    var t = (x - g.pad.l) / Math.max(1, g.w - g.pad.l - g.pad.r);
    t = Math.max(0, Math.min(1, t));
    this.setCursorFrame(Math.round(f0 + t * (f1 - f0)), true);
    if (!notify && this.onCursor) this.onCursor(this.cursor);
  };

  TimePlot.prototype.rowAtFrame = function (f) {
    if (f == null || !this.rows.length) return null;
    var best = null, bd = Infinity;
    for (var i = 0; i < this.rows.length; i++) {
      var v = this.rows[i].frame;
      if (typeof v !== 'number') continue;
      var d = Math.abs(v - f);
      if (d < bd) { bd = d; best = this.rows[i]; }
    }
    return best;
  };

  TimePlot.prototype.draw = function () {
    var g = this._geom();
    var ctx = this.c.getContext('2d');
    ctx.setTransform(g.dpr, 0, 0, g.dpr, 0, 0);
    ctx.clearRect(0, 0, g.w, g.h);

    var line = css('--line-soft', '#e1e7ee');
    var muted = css('--muted', '#59636f');
    var accent = css('--accent', '#a9620a');
    var fr = this._frames();
    if (!fr.length || !this.keys.length) {
      ctx.fillStyle = muted;
      ctx.font = '12px ui-monospace, monospace';
      ctx.fillText('표시할 신호를 고르세요', g.pad.l, g.h / 2);
      return;
    }
    var f0 = fr[0], f1 = fr[fr.length - 1];
    var span = Math.max(1, f1 - f0);
    var X = function (f) {
      return g.pad.l + (f - f0) / span * (g.w - g.pad.l - g.pad.r);
    };

    // 격자
    ctx.strokeStyle = line; ctx.lineWidth = 1;
    ctx.font = '10px ui-monospace, monospace'; ctx.fillStyle = muted;
    for (var i = 0; i <= 4; i++) {
      var y = g.pad.t + i / 4 * (g.h - g.pad.t - g.pad.b);
      ctx.beginPath(); ctx.moveTo(g.pad.l, y); ctx.lineTo(g.w - g.pad.r, y); ctx.stroke();
    }
    for (var k = 0; k <= 4; k++) {
      var f = f0 + k / 4 * span;
      var xx = X(f);
      ctx.fillText(String(Math.round(f)), xx - 12, g.h - 6);
    }

    // 신호별 정규화해서 겹쳐 그린다
    var pal = palette();
    var self = this;
    this.keys.forEach(function (key, ki) {
      var vals = self.rows.map(function (r) {
        var v = r[key]; return (typeof v === 'number' && isFinite(v)) ? v : null;
      });
      var fin = vals.filter(function (v) { return v !== null; });
      if (!fin.length) return;
      var lo = Math.min.apply(null, fin), hi = Math.max.apply(null, fin);
      if (hi - lo < 1e-12) { hi = lo + 1; }
      var Y = function (v) {
        return g.pad.t + (1 - (v - lo) / (hi - lo)) * (g.h - g.pad.t - g.pad.b);
      };
      ctx.strokeStyle = pal[ki % pal.length];
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      var started = false;
      for (var j = 0; j < self.rows.length; j++) {
        var fv = self.rows[j].frame, vv = vals[j];
        if (typeof fv !== 'number' || vv === null) { started = false; continue; }
        var px = X(fv), py = Y(vv);
        if (!started) { ctx.moveTo(px, py); started = true; } else { ctx.lineTo(px, py); }
      }
      ctx.stroke();
      // 축 라벨(최소/최대)
      ctx.fillStyle = pal[ki % pal.length];
      ctx.font = '9px ui-monospace, monospace';
      ctx.fillText(fmt(hi), 3, g.pad.t + 8 + ki * 10);
    });

    // 커서
    if (this.cursor != null) {
      var cx = X(this.cursor);
      ctx.strokeStyle = accent; ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.moveTo(cx, g.pad.t); ctx.lineTo(cx, g.h - g.pad.b); ctx.stroke();
    }
  };

  function fmt(v) {
    if (v == null) return '—';
    var a = Math.abs(v);
    if (a >= 1000 || (a < 0.001 && a > 0)) return v.toExponential(1);
    return (Math.round(v * 1000) / 1000).toString();
  }

  global.TimePlot = TimePlot;
  global.plotPalette = palette;
  global.fmtNum = fmt;
})(window);
