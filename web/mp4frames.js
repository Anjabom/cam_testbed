/* mp4 를 ★프레임 번호로★ 연다 — 브라우저가 못 여는 mp4v 를 위해.
 *
 * ★왜 이것이 있나★ [2026-10-06] 이 기계의 녹화는 전부 cv2 기본 코덱 mp4v 인데
 * 브라우저 디코더는 그것을 못 연다(9/7 에 github.io 를 접은 이유). 서버 없이
 * 열려면 해독을 페이지 안에서 해야 한다:
 *   · 상자 읽기(여기) — moov 의 표(stsz·stco·stss…)만 읽어 「n 번째 프레임이
 *     파일의 어디 몇 바이트인가」를 안다. 프레임은 ★그 조각만★ File.slice 로
 *     읽는다 — 2GB 녹화를 메모리에 올리지 않는다.
 *   · 해독 — vendor/mpeg4dec.js (FFmpeg 의 mpeg4 디코더 하나만 wasm 으로 구운 것,
 *     tools/build_mpeg4dec.sh).
 *
 * ★서버 모드와 같은 모양이다★ info() 와 frame(i) 두 개 — app.js 는 프레임이
 * 서버에서 오는지 여기서 오는지 모른다. 그래서 단축키(한 프레임·30프레임)가
 * 두 갈래에서 똑같이 정확히 한 프레임씩 움직인다.
 *
 * ★맞는지는 기계가 본다★ tools/mpeg4_check.js 가 node 에서 이 파일과 디코더를
 * 그대로 불러 cv2 가 같은 파일에서 읽은 그림과 맞춘다(자체검사 t_mpeg4_js).
 */
(function (root) {
  'use strict';

  // ══════════════════════════════════════════════════════════════════
  //  상자 읽기 — 필요한 표만 꺼낸다
  // ══════════════════════════════════════════════════════════════════
  function u32(b, o) { return ((b[o] << 24) >>> 0) + (b[o + 1] << 16) + (b[o + 2] << 8) + b[o + 3]; }
  function i32(b, o) { return u32(b, o) | 0; }
  function u64(b, o) { return u32(b, o) * 4294967296 + u32(b, o + 4); }
  function u16(b, o) { return (b[o] << 8) + b[o + 1]; }
  function fourcc(b, o) { return String.fromCharCode(b[o], b[o + 1], b[o + 2], b[o + 3]); }

  //  상자 하나 — 머리(크기·이름)를 읽고 본문의 [시작, 끝) 을 돌려준다
  function boxes(b, from, to) {
    var out = [], o = from;
    while (o + 8 <= to) {
      var size = u32(b, o), type = fourcc(b, o + 4), hdr = 8;
      if (size === 1) { size = u64(b, o + 8); hdr = 16; } else if (size === 0) { size = to - o; }
      if (size < hdr || o + size > to) break;            // 깨진 상자 — 더 읽지 않는다
      out.push({ type: type, start: o + hdr, end: o + size });
      o += size;
    }
    return out;
  }
  function child(b, box, type) {
    var r = boxes(b, box.start, box.end).filter(function (x) { return x.type === type; });
    return r[0] || null;
  }

  //  최상위에서 moov 를 찾는다. ★moov 는 파일 끝에 있는 경우가 흔하다★
  //  (cv2 가 그렇게 쓴다) — mdat 은 건너뛰기만 하므로 머리 16바이트씩만 읽는다.
  function findMoov(read, size) {
    function step(o) {
      if (o + 8 > size) return Promise.reject(new Error('moov 상자가 없습니다 — mp4 가 아니거나 녹화가 끝나지 않았습니다'));
      return read(o, Math.min(16, size - o)).then(function (h) {
        var sz = u32(h, 0), type = fourcc(h, 4), hdr = 8;
        if (sz === 1) { sz = u64(h, 8); hdr = 16; } else if (sz === 0) { sz = size - o; }
        if (sz < hdr) return Promise.reject(new Error('mp4 상자가 깨졌습니다 (' + o + ')'));
        if (type === 'moov') {
          return read(o, sz).then(function (b) { return { buf: b, box: { start: hdr, end: sz } }; });
        }
        return step(o + sz);
      });
    }
    return step(0);
  }

  //  esds 안의 DecoderSpecificInfo(태그 5) — mpeg4 디코더의 extradata(VOL 머리)다
  function esdsExtra(b, s, e) {
    var o = s + 4;                                       // version/flags
    function len() {
      var n = 0;
      for (var k = 0; k < 4 && o < e; k++) {
        var c = b[o++];
        n = (n << 7) | (c & 0x7f);
        if (!(c & 0x80)) break;
      }
      return n;
    }
    while (o < e) {
      var tag = b[o++], n = len();
      if (tag === 3) {                                   // ES_Descriptor
        var fl = b[o + 2]; o += 3;
        if (fl & 0x80) o += 2;
        if (fl & 0x40) o += 1 + b[o];
        if (fl & 0x20) o += 2;
      } else if (tag === 4) {                            // DecoderConfigDescriptor
        o += 13;
      } else if (tag === 5) {
        return b.slice(o, o + n);
      } else {
        o += n;
      }
    }
    return null;
  }

  //  영상 트랙의 표를 풀어 「표시 순서 i → 표본(파일 위치·크기·키프레임 여부)」로
  function parseTrack(b, trak) {
    var mdia = child(b, trak, 'mdia');
    if (!mdia) return null;
    var hdlr = child(b, mdia, 'hdlr');
    if (!hdlr || fourcc(b, hdlr.start + 8) !== 'vide') return null;
    var mdhd = child(b, mdia, 'mdhd');
    var v = b[mdhd.start];
    var timescale = v === 1 ? u32(b, mdhd.start + 20) : u32(b, mdhd.start + 12);
    var minf = child(b, mdia, 'minf');
    var stbl = minf && child(b, minf, 'stbl');
    if (!stbl) return null;

    var stsd = child(b, stbl, 'stsd');
    var entry = boxes(b, stsd.start + 8, stsd.end)[0];
    var codec = fourcc(b, entry.start - 4);
    var width = u16(b, entry.start + 24), height = u16(b, entry.start + 26);
    var extra = null;
    boxes(b, entry.start + 78, entry.end).forEach(function (x) {
      if (x.type === 'esds') extra = esdsExtra(b, x.start, x.end);
    });

    //  표본 크기
    var stsz = child(b, stbl, 'stsz'), fixed = u32(b, stsz.start + 4), n = u32(b, stsz.start + 8);
    var size = new Array(n);
    for (var k = 0; k < n; k++) size[k] = fixed || u32(b, stsz.start + 12 + 4 * k);

    //  덩어리 위치 (32/64비트)
    var co = child(b, stbl, 'stco'), co64 = !co;
    if (!co) co = child(b, stbl, 'co64');
    var nch = u32(b, co.start + 4), chunk = new Array(nch);
    for (k = 0; k < nch; k++) {
      chunk[k] = co64 ? u64(b, co.start + 8 + 8 * k) : u32(b, co.start + 8 + 4 * k);
    }

    //  덩어리마다 표본 몇 개 → 표본마다 파일 위치
    var stsc = child(b, stbl, 'stsc'), ne = u32(b, stsc.start + 4);
    var offset = new Array(n), s = 0;
    for (var e = 0; e < ne && s < n; e++) {
      var first = u32(b, stsc.start + 8 + 12 * e) - 1;
      var per = u32(b, stsc.start + 12 + 12 * e);
      var last = e + 1 < ne ? u32(b, stsc.start + 8 + 12 * (e + 1)) - 1 : nch;
      for (var c = first; c < last && s < n; c++) {
        var at = chunk[c];
        for (var j = 0; j < per && s < n; j++) { offset[s] = at; at += size[s]; s++; }
      }
    }

    //  시각: 해독 순서(stts) + 표시 어긋남(ctts — B 프레임이 있을 때만)
    var stts = child(b, stbl, 'stts'), cts = new Array(n), t = 0;
    s = 0;
    for (e = 0, ne = u32(b, stts.start + 4); e < ne; e++) {
      var cnt = u32(b, stts.start + 8 + 8 * e), dl = u32(b, stts.start + 12 + 8 * e);
      for (j = 0; j < cnt && s < n; j++) { cts[s++] = t; t += dl; }
    }
    for (; s < n; s++) cts[s] = t;
    var duration = t;
    var ctts = child(b, stbl, 'ctts');
    if (ctts) {
      s = 0;
      for (e = 0, ne = u32(b, ctts.start + 4); e < ne; e++) {
        cnt = u32(b, ctts.start + 8 + 8 * e);
        var off = i32(b, ctts.start + 12 + 8 * e);
        for (j = 0; j < cnt && s < n; j++) cts[s++] += off;
      }
    }

    //  키프레임 — stss 가 없으면 전부 키프레임이다(규격)
    var key = new Array(n), stss = child(b, stbl, 'stss');
    for (k = 0; k < n; k++) key[k] = !stss;
    if (stss) {
      for (k = 0, ne = u32(b, stss.start + 4); k < ne; k++) {
        var idx = u32(b, stss.start + 8 + 4 * k) - 1;
        if (idx >= 0 && idx < n) key[idx] = true;
      }
    }

    //  표시 순서: i 번째로 보이는 프레임이 몇 번째 표본인가
    var order = [];
    for (k = 0; k < n; k++) order.push(k);
    order.sort(function (a, z) { return cts[a] - cts[z] || a - z; });
    var shown = new Array(n);
    order.forEach(function (smp, i) { shown[smp] = i; });

    return {
      codec: codec, width: width, height: height, extra: extra, frames: n,
      fps: duration > 0 ? n * timescale / duration : 0,
      offset: offset, size: size, key: key, order: order, shown: shown
    };
  }

  //  파일 하나를 읽어 영상 트랙의 표를 돌려준다
  function demux(read, fileSize) {
    return findMoov(read, fileSize).then(function (m) {
      var tr = boxes(m.buf, m.box.start, m.box.end).filter(function (x) { return x.type === 'trak'; });
      for (var k = 0; k < tr.length; k++) {
        var t = parseTrack(m.buf, tr[k]);
        if (t) return t;
      }
      throw new Error('영상 트랙이 없습니다');
    });
  }

  // ══════════════════════════════════════════════════════════════════
  //  프레임 소스 — info() · frame(i)
  // ══════════════════════════════════════════════════════════════════
  //  dec  : Mpeg4Dec 모듈 인스턴스 (vendor/mpeg4dec.js)
  //  read : (offset, length) → Promise<Uint8Array>
  //  out  : (rgba, w, h) → 그림 (브라우저에서는 캔버스, node 에서는 그대로)
  function Source(dec, track, read, out) {
    this.dec = dec; this.t = track; this.read = read; this.out = out;
    this.next = -1;        // 다음에 넣을 표본(해독 순서). -1 = 처음부터 다시
    this.cache = null;     // {i, pic} — 마지막으로 낸 그림
    this.queue = Promise.resolve();
    this.ticket = 0;
    var x = track.extra || new Uint8Array(0);
    var p = dec._malloc(Math.max(1, x.length));
    dec.HEAPU8.set(x, p);
    var r = dec._dec_open(p, x.length);
    dec._free(p);
    if (r < 0) throw new Error('디코더를 열지 못했습니다 (' + r + ')');
  }

  Source.prototype.info = function () {
    return { w: this.t.width, h: this.t.height, frames: this.t.frames, fps: this.t.fps };
  };

  //  ★한 줄로 세운다★ 재생 중에 요청이 겹치면 디코더 상태가 섞인다.
  //  ★줄에서 밀린 요청은 건너뛴다★ (null) — 끌기는 요청을 수십 개 보내는데, 하나에
  //  100ms 씩 다 풀면 손을 놓고도 몇 초 동안 그림이 따라온다. 마지막 것만 푼다.
  Source.prototype.frame = function (i) {
    var self = this, ticket = ++this.ticket;
    var job = this.queue.then(function () {
      return ticket === self.ticket ? self._frame(i) : null;
    });
    this.queue = job.catch(function () { /* 다음 요청은 그대로 받는다 */ });
    return job;
  };

  Source.prototype._frame = function (i) {
    var t = this.t;
    i = Math.max(0, Math.min(t.frames - 1, i | 0));
    if (this.cache && this.cache.i === i) return Promise.resolve(this.cache.pic);
    var smp = t.order[i];
    var k = smp;
    while (k > 0 && !t.key[k]) k--;                  // 그 앞의 키프레임

    //  ★같은 GOP 안에서 앞으로 가면 이어서 해독한다★ (한 프레임씩 넘길 때 1장만)
    //  이어 가도 되는 조건: 이미 그 키프레임 뒤를 넣고 있고(k < next), 목표
    //  표본을 아직 다 넣지 않았거나 막 넣었고(next ≤ smp+1), 목표가 이미 나와서
    //  지나가 버리지 않았다(i > lastShown). 아니면 키프레임부터 다시.
    var cont = this.next >= 0 && k < this.next && this.next <= smp + 1
      && (this.lastShown == null || i > this.lastShown);
    if (!cont) { this.dec._dec_flush(); this.next = k; this.lastShown = null; }
    return this._run(i, this.next, Math.min(t.frames, smp + 5), k);
  };

  Source.prototype._run = function (i, from, to, key) {
    var self = this, t = this.t, dec = this.dec;
    //  ★필요한 조각을 한 번에 읽는다★ GOP 안의 표본은 대개 붙어 있다
    var a = t.offset[from], z = a;
    for (var s = from; s < to; s++) z = Math.max(z, t.offset[s] + t.size[s]);
    var whole = z - a < 64 * 1024 * 1024;
    var pre = whole ? this.read(a, z - a) : Promise.resolve(null);

    return pre.then(function (blk) {
      var got = null;
      function take() {
        for (;;) {
          var r = dec._dec_receive();
          if (r !== 1) return;
          var pts = dec._dec_pts();
          self.lastShown = pts;
          if (pts === i) {
            var w = dec._dec_width(), h = dec._dec_height(), p = dec._dec_rgba();
            if (!p) throw new Error('그림 형식이 yuv420p 가 아닙니다');
            got = self.out(dec.HEAPU8.subarray(p, p + w * h * 4), w, h);
          }
        }
      }
      function feed(s) {
        if (got || s >= to) {
          if (!got) {                                   // 끝까지 왔다 — 남은 그림을 비워 낸다
            dec._dec_send(0, 0, 0); take();
            self.next = -1; self.lastShown = null;
          } else {
            self.next = s;
          }
          if (!got) {
            if (key > 0) {                              // 열린 GOP — 한 키프레임 더 앞에서
              var k = key - 1;
              while (k > 0 && !t.key[k]) k--;
              dec._dec_flush(); self.next = k; self.lastShown = null;
              return self._run(i, k, Math.min(t.frames, t.order[i] + 5), k);
            }
            throw new Error('프레임 ' + i + ' 을 해독하지 못했습니다');
          }
          self.cache = { i: i, pic: got };
          return got;
        }
        var sz = t.size[s];
        var bytes = blk ? Promise.resolve(blk.subarray(t.offset[s] - a, t.offset[s] - a + sz))
                        : self.read(t.offset[s], sz);
        return bytes.then(function (pk) {
          var p = dec._malloc(Math.max(1, sz));
          dec.HEAPU8.set(pk, p);
          dec._dec_send(p, sz, t.shown[s]);
          dec._free(p);
          take();
          return feed(s + 1);
        });
      }
      return feed(from);
    });
  };

  //  ── 브라우저: File 하나를 연다 ─────────────────────────────────────
  function fileReader(file) {
    return function (o, n) {
      return file.slice(o, o + n).arrayBuffer().then(function (ab) { return new Uint8Array(ab); });
    };
  }

  //  디코더는 ★처음 mp4v 를 열 때만★ 불러온다 — 760KB 라 사진만 볼 사람에게는 짐이다.
  //  <script> 를 끼워 넣는다(fetch 는 file:// 에서 막히지만 이건 된다).
  var decoderP = null;
  function loadDecoder(base) {
    if (decoderP) return decoderP;
    decoderP = new Promise(function (res, rej) {
      if (root.Mpeg4Dec) { res(); return; }
      var s = document.createElement('script');
      s.src = (base || '') + 'vendor/mpeg4dec.js';
      s.onload = function () { res(); };
      s.onerror = function () { decoderP = null; rej(new Error('vendor/mpeg4dec.js 를 불러오지 못했습니다')); };
      document.head.appendChild(s);
    }).then(function () { return root.Mpeg4Dec(); });
    return decoderP;
  }

  //  그림을 캔버스로 — WebGL 이 그대로 텍스처로 받는다
  function canvasOut() {
    var cv = document.createElement('canvas'), cx = null, img = null;
    return function (rgba, w, h) {
      if (cv.width !== w || cv.height !== h || !cx) {
        cv.width = w; cv.height = h;
        cx = cv.getContext('2d');
        img = cx.createImageData(w, h);
      }
      img.data.set(rgba);
      cx.putImageData(img, 0, 0);
      return cv;
    };
  }

  //  File → Source. mp4v 가 아니면 null (그건 <video> 가 연다)
  function openFile(file) {
    var read = fileReader(file);
    return demux(read, file.size).then(function (t) {
      if (t.codec !== 'mp4v') return null;
      return loadDecoder().then(function (dec) {
        return new Source(dec, t, read, canvasOut());
      });
    });
  }

  root.Mp4Frames = { demux: demux, Source: Source, openFile: openFile };
})(typeof window !== 'undefined' ? window : this);
