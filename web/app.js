/* 카메라 보정 스튜디오 — 화면.
 *
 * ★규칙★ 기하 계산은 절대 여기서 하지 않는다. BEV·수직도·왜곡보정은 전부
 * 서버의 `tb.geometry` 가 계산한다 — 그래야 화면이 보여 주는 BEV 와 대상
 * 노드가 실제로 만드는 BEV 가 같다. JS 는 값을 보내고 그림을 받아 건다.
 *
 * ★맞출 대상과 이름도 여기 없다★ 편집 대상(사각형·ROI·기준선…)은 프로필의
 * `calibration.targets` 에서 온다. 이름을 JS 에 박으면 다른 카메라·다른
 * 워크스페이스에서 화면이 통째로 빈다. */
(function () {
  'use strict';

  var view = document.getElementById('view');
  var hintEl = document.getElementById('hint');
  var footEl = document.getElementById('footmeta');

  // ── 유틸 ────────────────────────────────────────────────────────
  function h(tag, attrs, kids) {
    var e = document.createElement(tag);
    for (var k in (attrs || {})) {
      if (k === 'class') e.className = attrs[k];
      else if (k === 'html') e.innerHTML = attrs[k];
      else if (k === 'text') e.textContent = attrs[k];
      else if (k.slice(0, 2) === 'on') e.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    }
    (kids || []).forEach(function (c) {
      if (c == null) return;
      e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return e;
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }

  function get(url) {
    return fetch(url).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
        return j;
      }, function () { throw new Error('HTTP ' + r.status); });
    });
  }
  function post(url, body) {
    return fetch(url, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}) })
      .then(function (r) {
        return r.json().then(function (j) {
          if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
          return j;
        }, function () { throw new Error('HTTP ' + r.status); });
      });
  }

  // ── 파일 올리기 ─────────────────────────────────────────────────
  //  ★fetch 를 쓰지 않는다★ fetch 는 업로드 진행률을 주지 않는다. 몇 GB 짜리
  //  영상을 아무 표시 없이 기다리게 하면 사람은 멈춘 줄 알고 새로고침한다.
  //  폼(multipart)으로 감싸지도 않는다 — 서버가 이름만 헤더로 받고 본문은
  //  날바이트로 파일에 흘린다(경계 문자열 파서를 양쪽에 두지 않는다).
  function upload(file, onProg) {
    return new Promise(function (res, rej) {
      var x = new XMLHttpRequest();
      x.open('POST', '/api/upload');
      x.setRequestHeader('Content-Type', 'application/octet-stream');
      //  헤더는 ASCII 만 담는다 → 한글 파일명은 퍼센트 인코딩해서 보낸다.
      x.setRequestHeader('X-Filename', encodeURIComponent(file.name));
      x.upload.onprogress = function (e) {
        if (e.lengthComputable && onProg) onProg(e.loaded, e.total);
      };
      x.onload = function () {
        var j = {};
        try { j = JSON.parse(x.responseText); } catch (err) { j = {}; }
        if (x.status >= 200 && x.status < 300) res(j);
        else rej(new Error(j.error || ('HTTP ' + x.status)));
      };
      x.onerror = function () { rej(new Error('연결이 끊겼습니다')); };
      x.onabort = function () { rej(new Error('취소했습니다')); };
      x.send(file);
    });
  }

  //  ★페이지 어디에 떨어뜨려도 받는다★ 작은 사각형을 조준하게 만들면
  //  드래그&드롭이 파일 선택 창보다 불편해진다.
  //  화면을 다시 그릴 때마다 리스너를 새로 달면 한 번 떨어뜨린 파일이 여러 번
  //  올라간다 → 리스너는 여기서 ★한 번만★ 달고, 받는 사람만 갈아 끼운다.
  var DROP = { on: null };
  (function () {
    var depth = 0;                      // 자식 위를 지날 때마다 leave 가 뜬다
    function stop(e) { e.preventDefault(); e.stopPropagation(); }
    function off() { depth = 0; document.body.classList.remove('dropping'); }
    document.addEventListener('dragenter', function (e) {
      stop(e); depth++; document.body.classList.add('dropping');
    });
    document.addEventListener('dragover', stop);
    document.addEventListener('dragleave', function (e) {
      stop(e); if (--depth <= 0) off();
    });
    document.addEventListener('drop', function (e) {
      stop(e); off();
      var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f && DROP.on) DROP.on(f);
    });
  }());

  var hintTimer = null;
  function say(msg, bad) {
    hintEl.textContent = (bad ? '오류: ' : '') + msg;
    hintEl.className = 'hint' + (bad ? ' no' : '');
    if (hintTimer) clearTimeout(hintTimer);
    hintTimer = setTimeout(function () { hintEl.textContent = ''; }, 6000);
  }
  function fail(e) {
    clear(view);
    view.appendChild(h('h1', { text: '열지 못했습니다' }));
    view.appendChild(h('p', { class: 'no', text: e.message || String(e) }));
    view.appendChild(h('p', { class: 'help', html:
      '보정 프로필이 하나도 없으면 이 화면은 열리지 않습니다. '
      + '<code>contracts/*.yaml</code> 에 <code>calibration:</code> 블록이 있거나, '
      + '아래 «새 프로필» 로 <code>calib/*.yaml</code> 을 하나 만드세요.' }));
    view.appendChild(newProfileBar());
  }
  function sec(t, sub) {
    var e = [h('h2', { text: t })];
    if (sub) e.push(h('p', { class: 'help', html: sub }));
    return h('div', { class: 'sect' }, e);
  }
  function bar(kids) { return h('div', { class: 'framebar' }, kids); }
  function mut(t) { return h('span', { class: 'mut', text: t }); }

  function fmtSize(n) {
    return n > 1e9 ? (n / 1e9).toFixed(1) + ' GB'
      : n > 1e6 ? (n / 1e6).toFixed(1) + ' MB' : Math.round(n / 1e3) + ' kB';
  }

  // ── 새 프로필 (워크스페이스 없이 카메라만 실험할 때) ──────────────
  function newProfileBar() {
    var nm = h('input', { class: 'wherebox', placeholder: '이름 (예: mycam)' });
    var w = h('input', { type: 'number', class: 'wherebox',
                         style: 'max-width:110px', value: '1920' });
    var ht = h('input', { type: 'number', class: 'wherebox',
                          style: 'max-width:110px', value: '1080' });
    return bar([
      mut('새 프로필'), nm, mut('해상도'), w, h('span', { text: '×' }), ht,
      h('button', { text: '만들기', title: 'calib/<이름>.yaml 을 만듭니다',
        onclick: function () {
          post('/api/profile/new', { name: nm.value.trim(),
                                     width: +w.value, height: +ht.value })
            .then(function (d) { location.reload(); return d; })
            .catch(function (e) { say(e.message, true); });
        } }),
      h('span', { class: 'sub', text:
        'K/D 는 자리표시자로 채워집니다 — «체스보드 보정» 으로 실측하세요' }),
    ]);
  }

  // ══════════════════════════════════════════════════════════════
  //  파일 고르기 — 서버가 보는 파일 시스템을 훑는다 (업로드가 아니다)
  // ══════════════════════════════════════════════════════════════
  function pickSource(startDir, onPick) {
    var back = h('div', { class: 'modalback' });
    var pathIn = h('input', { class: 'wherebox', style: 'flex:1',
                              placeholder: '/경로/를/직접/입력해도 됩니다' });
    var list = h('div', { class: 'browse' });
    var rootLbl = h('p', { class: 'sub' });
    var here = '';

    function close() { document.body.removeChild(back); }
    function load(d) {
      list.textContent = '읽는 중…';
      get('/api/browse?dir=' + encodeURIComponent(d || '')).then(function (r) {
        here = r.dir;
        pathIn.value = r.dir;
        rootLbl.textContent = (r.roots || []).length
          ? '훑을 수 있는 곳: ' + r.roots.join('  ·  ')
            + '   (넓히려면 local.yaml 의 browse_roots:)'
          : '';
        clear(list);
        if (r.error) list.appendChild(h('p', { class: 'no', text: r.error }));
        list.appendChild(h('div', { class: 'brow up', onclick: function () { load(r.up); } },
          [h('b', { text: '⌃ 상위 폴더' }), mut(r.up)]));
        r.dirs.forEach(function (e) {
          list.appendChild(h('div', { class: 'brow', onclick: function () { load(e.path); } },
            [h('b', { text: '📁 ' + e.name })]));
        });
        r.files.forEach(function (e) {
          list.appendChild(h('div', { class: 'brow file', onclick: function () {
            close(); onPick(e.path);
          } }, [h('b', { text: (e.kind === 'image' ? '🖼 ' : '🎞 ') + e.name }),
                mut(fmtSize(e.size))]));
        });
        if (!r.dirs.length && !r.files.length) {
          list.appendChild(h('p', { class: 'sub', text: '이 폴더에는 영상도 이미지도 없습니다' }));
        }
      }).catch(function (e) { list.textContent = '오류: ' + e.message; });
    }
    var box = h('div', { class: 'modal' }, [
      h('h2', { text: '영상 · 이미지 고르기' }),
      h('p', { class: 'help', html:
        '<b>이 스튜디오가 도는 컴퓨터</b>의 파일입니다 — 복사되지 않고 경로만 씁니다. '
        + '사진 한 장으로도 보정할 수 있습니다.<br>'
        + '지금 보고 있는 <b>이 기기</b>에 있는 파일이라면 여기가 아니라 '
        + '«파일 올리기» 이거나, 창 아무 데나 끌어다 놓으면 됩니다.' }),
      bar([pathIn,
           h('button', { text: '열기', onclick: function () { load(pathIn.value.trim()); } }),
           h('button', { text: '이 경로 쓰기', class: 'primary', onclick: function () {
             var v = pathIn.value.trim();
             if (v) { close(); onPick(v); }
           } })]),
      list,
      rootLbl,
      bar([h('span', { class: 'spacer' }),
           h('button', { text: '닫기', onclick: close })]),
    ]);
    back.appendChild(box);
    back.addEventListener('click', function (e) { if (e.target === back) close(); });
    document.body.appendChild(back);
    load(startDir || '');
  }

  // ══════════════════════════════════════════════════════════════
  //  오래 걸리는 일 — 진행 로그를 보여 주며 기다린다
  // ══════════════════════════════════════════════════════════════
  function watchTask(box, onDone) {
    box.textContent = '시작하는 중…';
    var t = setInterval(function () {
      get('/api/task').then(function (s) {
        box.textContent = (s.log || []).join('\n') || '…';
        box.scrollTop = box.scrollHeight;
        if (!s.done) return;
        clearInterval(t);
        if (s.error) {
          box.textContent += '\n\n오류: ' + s.error;
          say(s.error, true);
        } else if (onDone) { onDone(s.result); }
      }).catch(function (e) { clearInterval(t); box.textContent = '오류: ' + e.message; });
    }, 900);
    var prev = window.__stopPoll;
    window.__stopPoll = function () { clearInterval(t); if (prev) prev(); };
  }

  // ══════════════════════════════════════════════════════════════
  //  스튜디오 본체
  // ══════════════════════════════════════════════════════════════
  var CAL = null;                       // 화면을 떠날 때 재생을 세우려고 잡아 둔다

  function renderStudio(st) {
    clear(view);
    if (CAL && CAL.off) CAL.off.forEach(function (f2) { f2(); });
    if (st.error) return fail(new Error(st.error));

    /* ── 편집 대상 — ★프로필이 준다★ ─────────────────────────────
       quad(사각형) · rect(ROI) · scale(길이 재기) · bev_row/bev_dist(가로선).
       종류만 알고 이름은 모른다. */
    var KIND_LABEL = { quad: 'IPM 사각형', rect: 'ROI', scale: '길이 재기' };
    var modes = [];
    var T = st.targets || {};
    Object.keys(T).forEach(function (k) {
      if (T[k].kind === 'quad') modes.push({ id: 'quad', key: k, label: KIND_LABEL.quad, hint: T[k].hint });
      else if (T[k].kind === 'rect') modes.push({ id: k, key: k, label: k, hint: T[k].hint });
    });
    Object.keys(T).forEach(function (k) {
      if (T[k].kind === 'scale') modes.push({ id: 'measure', key: k, label: KIND_LABEL.scale, hint: T[k].hint });
    });
    /* BEV 가로선 : 거리로 판정하는 노드의 기준선과 문턱.
       BEV 는 원근이 펴져 있어 가로선 하나가 곧 '차에서 얼마'다. 기준선(bev_row)을
       먼저 놓고 문턱(bev_dist)은 그 선에서의 거리로 잡는다 — 기준선을 옮기면
       문턱이 통째로 따라온다(따로 잡으면 반드시 어긋난다). */
    var BEVKIND = {}, BUMPER = '';
    Object.keys(T).forEach(function (k) {
      if (T[k].kind !== 'bev_row' && T[k].kind !== 'bev_dist') return;
      BEVKIND[k] = T[k].kind;
      if (T[k].kind === 'bev_row' && !BUMPER) BUMPER = k;
      modes.push({ id: k, key: k, label: k, hint: T[k].hint });
    });
    function bumperY() {
      return BUMPER ? Number(S.bevRows[BUMPER]) : ((st.bev || {}).h || 480);
    }
    function rowY(k) {
      return BEVKIND[k] === 'bev_row' ? Number(S.bevRows[k]) : bumperY() - Number(S.bevRows[k]);
    }
    function setRowY(k, y) {
      S.bevRows[k] = Math.round(BEVKIND[k] === 'bev_row' ? y : bumperY() - y);
    }
    if (!modes.length) modes.push({ id: 'quad', key: '', label: KIND_LABEL.quad, hint: '' });

    var S = {
      st: st, src: null, video: '', frame: 0, total: 0, still: false,
      quad: (st.quad || []).slice(), rects: JSON.parse(JSON.stringify(st.rects || {})),
      px2m: st.px2m, lengthM: st.length_m,
      bevRows: JSON.parse(JSON.stringify(st.bev_rows || {})),
      mode: modes[0].id, sel: 0, meas: [], realM: st.length_m,
      undist: st.undistort !== false, grid: true, playing: false, fps: 15,
      busy: false, dirty: false, timer: null, meta: null, off: [],
    };
    CAL = S;

    // ── 1) 프로필 ───────────────────────────────────────────────
    var pSel = h('select', {}, (st.profiles || []).map(function (p) {
      return h('option', { value: p.id,
        text: p.id + (p.error ? '  (열 수 없음)' : '  · 대상 ' + p.targets + '개') });
    }));
    pSel.value = st.id || '';
    pSel.addEventListener('change', function () {
      stopPlay();
      open(pSel.value, S.video);
    });

    // ── 2) 소스 (영상 또는 이미지) ───────────────────────────────
    var srcLbl = h('span', { class: 'srcname', text: '고르지 않음' });
    var recentSel = h('select', {}, [h('option', { value: '', text: '최근 연 것…' })]
      .concat((st.recent || []).map(function (r) {
        return h('option', { value: r.path,
          text: (r.path.split('/').pop()) + (r.frames ? '  (' + r.frames + '프레임)' : '') });
      })));
    recentSel.addEventListener('change', function () {
      if (recentSel.value) setSource(recentSel.value);
    });

    //  ★보내고 나서 거절당하지 않게★ 한도와 남은 자리를 먼저 본다.
    //  8GB 를 다 올린 뒤에 「너무 큽니다」를 듣는 것은 도구가 아니다.
    var upIn = h('input', { type: 'file', style: 'display:none',
                            accept: 'video/*,image/*' });
    var upBtn = h('button', { text: '파일 올리기',
      title: '이 기기에 있는 영상·사진을 스튜디오가 도는 컴퓨터로 보냅니다',
      onclick: function () { upIn.click(); } });
    var upLbl = h('span', { class: 'sub' });
    upIn.addEventListener('change', function () {
      if (upIn.files && upIn.files[0]) takeFile(upIn.files[0]);
      upIn.value = '';                 // 같은 파일을 다시 골라도 change 가 나게
    });

    function takeFile(f) {
      var lim = st.upload || {};
      if (lim.max && f.size > lim.max) {
        say(f.name + ' 은 한도보다 큽니다 — ' + fmtSize(f.size)
            + ' > ' + fmtSize(lim.max), true);
        return;
      }
      if (lim.free != null && f.size + (lim.keep_free || 0) > lim.free) {
        say('그 컴퓨터에 자리가 모자랍니다 — 남은 자리 ' + fmtSize(lim.free)
            + ', 이 파일 ' + fmtSize(f.size), true);
        return;
      }
      upBtn.disabled = true;
      upLbl.textContent = f.name + '  0%';
      upload(f, function (a, b) {
        upLbl.textContent = f.name + '  ' + Math.round(a / b * 100) + '%  ('
          + fmtSize(a) + ' / ' + fmtSize(b) + ')';
      }).then(function (info) {
        upBtn.disabled = false;
        upLbl.textContent = '';
        say(info.name + ' → ' + (lim.dir || '') + ' 에 놓았습니다');
        setSource(info.path);
        return info;
      }).catch(function (e) {
        upBtn.disabled = false;
        upLbl.textContent = '';
        say(e.message, true);
      });
    }
    DROP.on = takeFile;                // 창 아무 데나 끌어다 놓아도 같은 길

    function setSource(path) {
      stopPlay();
      post('/api/source', { path: path }).then(function (info) {
        if (!info.openable) throw new Error('열 수 없는 파일입니다: ' + path);
        S.src = info; S.video = info.path;
        S.total = info.frames || 0;
        S.still = !!info.still || S.total <= 1;
        S.frame = 0;
        srcLbl.textContent = info.name + '  ' + info.w + '×' + info.h
          + (S.still ? '  (사진)' : '  ' + info.frames + '프레임 · ' + info.fps + 'fps');
        if (info.w && st.size && info.w !== st.size[0]) {
          say('⚠ 프로필 해상도(' + st.size[0] + '×' + st.size[1] + ')와 다른 소스입니다 — '
              + '왜곡보정 계수가 이 영상 것이 아닐 수 있습니다');
        }
        playRow.style.display = S.still ? 'none' : '';
        slider.style.display = S.still ? 'none' : '';
        syncBar(); draw();
      }).catch(function (e) { say(e.message, true); });
    }

    // ── 3) 프레임 바 ────────────────────────────────────────────
    var slider = h('input', { type: 'range', min: '0', max: '1', value: '0',
                              class: 'calslider' });
    slider.addEventListener('input', function () {
      stopPlay(); S.frame = parseInt(slider.value, 10) || 0; syncBar(); draw();
    });
    var frIn = h('input', { type: 'number', class: 'wherebox', style: 'max-width:100px' });
    frIn.addEventListener('change', function () { goto(parseInt(frIn.value, 10) || 0); });
    var totLbl = mut('');
    var playBtn = h('button', { class: 'sigbtn', text: '▶ 재생', title: '스페이스바',
                                onclick: togglePlay });
    var fpsIn = h('input', { type: 'number', value: '15', min: '1', max: '60',
                             class: 'wherebox', style: 'max-width:64px' });
    fpsIn.addEventListener('change', function () {
      S.fps = Math.max(1, Math.min(60, parseInt(fpsIn.value, 10) || 15));
      if (S.playing) { stopPlay(); togglePlay(); }
    });
    function goto(n) {
      S.frame = Math.max(0, S.total ? Math.min(S.total - 1, n) : n);
      syncBar(); draw();
    }
    function step(d) { stopPlay(); goto(S.frame + d); }
    function togglePlay() {
      if (S.still) return;
      if (S.playing) return stopPlay();
      S.playing = true;
      playBtn.textContent = '⏸ 정지';
      playBtn.classList.add('on');
      S.timer = setInterval(function () {
        if (S.total && S.frame >= S.total - 1) return stopPlay();
        S.frame += 1; syncBar(); draw();
      }, Math.round(1000 / S.fps));
    }
    function stopPlay() {
      S.playing = false;
      if (S.timer) { clearInterval(S.timer); S.timer = null; }
      playBtn.textContent = '▶ 재생';
      playBtn.classList.remove('on');
    }
    window.__stopPoll = stopPlay;
    S.off.push(stopPlay);
    function syncBar() {
      slider.max = String(Math.max(1, S.total - 1));
      slider.value = String(S.frame);
      frIn.value = String(S.frame);
      totLbl.textContent = S.total ? '/ ' + (S.total - 1) : '';
    }

    function toggleBtn(label, getv, setv, title) {
      var b = h('button', { class: 'sigbtn', title: title, onclick: function () {
        setv(!getv()); sync(); draw();
      } });
      function sync() {
        var on = getv();
        b.classList.toggle('on', on);
        b.textContent = label + ' ' + (on ? 'ON' : 'OFF');
      }
      b.__sync = sync;
      sync();
      return b;
    }
    var undBtn = toggleBtn('왜곡보정', function () { return S.undist; },
      function (v) { S.undist = v; }, '렌즈 왜곡을 펴서 볼지 여부 (u)');
    var gridBtn = toggleBtn('격자', function () { return S.grid; },
      function (v) { S.grid = v; }, 'BEV 에 0.5m 격자를 겹칩니다 (g)');

    // ── 4) 편집 대상 탭 ─────────────────────────────────────────
    var hintEl2 = h('p', { class: 'sub' });
    var modeBtns = modes.map(function (m, i) {
      return h('button', { class: 'sigbtn', text: (i + 1) + '. ' + m.label,
        title: m.hint || '', onclick: function () { setMode(m.id); } });
    });
    function setMode(id) {
      S.mode = id; S.sel = 0; S.meas = [];
      modes.forEach(function (m, i) {
        modeBtns[i].classList.toggle('on', m.id === S.mode);
      });
      var cur = modes.filter(function (m) { return m.id === S.mode; })[0] || {};
      hintEl2.textContent = (cur.hint ? cur.hint + '  —  ' : '') + (
        S.mode === 'measure'
          ? '오른쪽 BEV 에서 실제 길이를 아는 두 점을 클릭하세요 (차선 폭이 가장 쉽습니다).'
          : S.mode === 'quad'
            ? '왼쪽 원본에서 점을 끌어 옮기세요. 빈 곳을 누르면 고른 점이 그 자리로 갑니다.'
            : BEVKIND[S.mode]
              ? 'BEV 화면에서 선을 끌거나 ↑↓ 로 옮깁니다.'
              : '왼쪽 원본에서 드래그하면 새 사각형, 모서리를 잡으면 그 모서리만 움직입니다.');
      drawFields(); draw();
    }

    // ── 5) 값 패널 ──────────────────────────────────────────────
    var fields = h('div', { class: 'framebar' });
    var syncers = [];
    function numField(label, get2, set2, stepv) {
      var i = h('input', { type: 'number', step: String(stepv || 1),
                           class: 'wherebox', style: 'max-width:96px',
                           value: String(get2()) });
      i.addEventListener('change', function () {
        var v = parseFloat(i.value);
        if (v === v) { set2(v); draw(); }
      });
      i.__sync = function () { if (document.activeElement !== i) i.value = String(get2()); };
      return [mut(label), i];
    }
    var applyBtn = h('button', { text: 'px2m 적용', onclick: function () {
      if (S.meas.length < 2) return;
      var d = Math.hypot(S.meas[0][0] - S.meas[1][0], S.meas[0][1] - S.meas[1][1]);
      if (d < 2) return;
      S.px2m = Number((S.realM / d).toFixed(6));
      S.lengthM = S.realM;                 // 잰 길이가 곧 차선 폭이다
      S.meas = [];
      drawFields(); draw();
    } });
    function drawFields() {
      clear(fields); syncers = [];
      function add(pair) { fields.appendChild(pair[0]); fields.appendChild(pair[1]);
                           syncers.push(pair[1].__sync); }
      if (S.mode === 'quad') {
        ['TL', 'TR', 'BR', 'BL'].forEach(function (lab, i) {
          fields.appendChild(h('button', {
            class: 'sigbtn' + (S.sel === i ? ' on' : ''), text: lab,
            onclick: function () { S.sel = i; drawFields(); } }));
          add(numField('x', function () { return Math.round(S.quad[i * 2]); },
                       function (v) { S.quad[i * 2] = v; }));
          add(numField('y', function () { return Math.round(S.quad[i * 2 + 1]); },
                       function (v) { S.quad[i * 2 + 1] = v; }));
        });
      } else if (S.rects[S.mode]) {
        ['x0', 'y0', 'x1', 'y1'].forEach(function (lab, i) {
          add(numField(lab, function () { return Math.round(S.rects[S.mode][i]); },
                       function (v) { S.rects[S.mode][i] = v; }));
        });
        fields.appendChild(h('button', { text: '화면 전체', onclick: function () {
          S.rects[S.mode] = [0, 0, st.size[0], st.size[1]]; drawFields(); draw();
        } }));
      } else if (BEVKIND[S.mode]) {
        var isRow = BEVKIND[S.mode] === 'bev_row';
        add(numField(isRow ? 'BEV 행 [px]' : '기준선에서 [px]',
                     function () { return Math.round(S.bevRows[S.mode]); },
                     function (v) { S.bevRows[S.mode] = v; }));
        if (!isRow) {
          fields.appendChild(h('span', { class: 'sub',
            text: '≈ ' + (S.bevRows[S.mode] * S.px2m).toFixed(2) + ' m' }));
        }
      } else if (S.mode === 'measure') {
        add(numField('실제 길이 [m]', function () { return S.realM; },
                     function (v) { S.realM = v; }, 0.05));
        fields.appendChild(applyBtn);
        fields.appendChild(h('button', { text: '측정 지우기',
          onclick: function () { S.meas = []; draw(); } }));
      }
      fields.appendChild(h('span', { class: 'spacer' }));
      add(numField('px2m', function () { return S.px2m; },
                   function (v) { S.px2m = v; }, 0.0001));
      add(numField('실측 길이 [m]', function () { return S.lengthM; },
                   function (v) { S.lengthM = v; }, 0.05));
      syncFields();
    }
    function syncFields() { syncers.forEach(function (f2) { if (f2) f2(); }); }

    // ── 6) 큰 화면 ──────────────────────────────────────────────
    var img = h('img', { class: 'bigov', style: 'cursor:crosshair;user-select:none' });
    var meta = h('div', { class: 'readout' });

    /* 클릭 좌표 → 원본 픽셀. ★서버가 준 meta 로만 환산한다★
       (예전에는 리사이즈 전 값을 써서 6~7% 어긋났다) */
    function at(e) {
      var m = S.meta;
      if (!m) return null;
      var r = img.getBoundingClientRect();
      var x = (e.clientX - r.left) / r.width * m.disp_w;
      var y = (e.clientY - r.top) / r.height * m.disp_h;
      if (x <= m.split_x) return { panel: 'src', x: x / m.src_scale, y: y / m.src_scale };
      return { panel: 'bev', x: (x - m.split_x) / m.bev_scale, y: y / m.bev_scale };
    }
    function handles() {                 // [x, y, 쓰기 함수]
      if (S.mode === 'quad') {
        return [0, 1, 2, 3].map(function (i) {
          return [S.quad[i * 2], S.quad[i * 2 + 1], function (x, y) {
            S.quad[i * 2] = Math.round(x); S.quad[i * 2 + 1] = Math.round(y); }];
        });
      }
      var r = S.rects[S.mode];
      if (!r) return [];
      return [0, 1].map(function (i) {
        return [r[i * 2], r[i * 2 + 1], function (x, y) {
          r[i * 2] = Math.round(x); r[i * 2 + 1] = Math.round(y); }];
      });
    }

    var drag = null;
    img.addEventListener('mousedown', function (e) {
      var p = at(e);
      if (!p) return;
      e.preventDefault();
      if (p.panel === 'bev') {
        if (BEVKIND[S.mode]) {                 // 가로선을 끈다
          setRowY(S.mode, p.y);
          drag = ['bev', S.mode];
          drawFields(); return draw();
        }
        if (S.mode !== 'measure') return;
        if (S.meas.length >= 2) S.meas = [];
        S.meas.push([p.x, p.y]);
        return draw();
      }
      if (S.mode === 'measure') return;
      var hs = handles();
      var near = -1, best = 1e9;
      hs.forEach(function (hh, i) {
        var d = Math.hypot(hh[0] - p.x, hh[1] - p.y);
        if (d < best) { best = d; near = i; }
      });
      var reach = 70 / Math.max(1e-6, S.meta.src_scale);      // 화면상 70px
      if (near >= 0 && best < reach) {
        S.sel = near; drag = hs[near];
      } else if (S.rects[S.mode]) {
        S.rects[S.mode] = [Math.round(p.x), Math.round(p.y),
                           Math.round(p.x), Math.round(p.y)];
        S.sel = 1; drag = handles()[1];                       // 반대 모서리를 끈다
      } else {
        drag = hs[S.sel];                                     // 고른 점을 그리로
      }
      if (drag) { drag[2](p.x, p.y); drawFields(); draw(); }
    });
    function onMove(e) {
      if (!drag) return;
      var p = at(e);
      if (!p) return;
      if (drag[0] === 'bev') {
        if (p.panel !== 'bev') return;
        setRowY(drag[1], p.y);
        syncFields(); return draw();
      }
      if (p.panel !== 'src') return;
      drag[2](p.x, p.y);
      syncFields(); draw();
    }
    function onUp() { if (drag) { drag = null; drawFields(); } }
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    S.off.push(function () {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.onkeydown = null;
    });

    document.onkeydown = function (e) {
      if (/^(INPUT|SELECT|TEXTAREA)$/.test((e.target || {}).tagName || '')) return;
      if (document.querySelector('.modalback')) return;
      var nud = { ArrowLeft: [-1, 0], ArrowRight: [1, 0],
                  ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
      if (nud && BEVKIND[S.mode]) {
        e.preventDefault();
        setRowY(S.mode, rowY(S.mode) + nud[1] * (e.shiftKey ? 10 : 1));
        drawFields(); return draw();
      }
      if (nud) {
        var hs = handles();
        if (!hs.length || S.sel >= hs.length) return;
        e.preventDefault();
        var m2 = e.shiftKey ? 10 : 1;
        hs[S.sel][2](hs[S.sel][0] + nud[0] * m2, hs[S.sel][1] + nud[1] * m2);
        syncFields(); return draw();
      }
      if (e.key === ' ') { e.preventDefault(); return togglePlay(); }
      if (e.key === ',') return step(-1);
      if (e.key === '.') return step(1);
      if (e.key === '[') return step(-30);
      if (e.key === ']') return step(30);
      if (e.key === 'g') { S.grid = !S.grid; return refreshToggles(); }
      if (e.key === 'u') { S.undist = !S.undist; return refreshToggles(); }
      if (e.key === 'r') return reset();
      var n = parseInt(e.key, 10);
      if (n >= 1 && n <= modes.length) return setMode(modes[n - 1].id);
    };
    function refreshToggles() { undBtn.__sync(); gridBtn.__sync(); draw(); }
    function reset() {
      S.quad = (st.quad || []).slice();
      S.rects = JSON.parse(JSON.stringify(st.rects || {}));
      S.px2m = st.px2m; S.lengthM = st.length_m; S.realM = st.length_m;
      S.bevRows = JSON.parse(JSON.stringify(st.bev_rows || {}));
      S.meas = [];
      drawFields(); draw();
      say('파일에 있던 값으로 되돌렸습니다');
    }

    // ── 7) 그리기 — 서버에 한 번에 하나만 물어본다 ───────────────
    function body() {
      return { profile: st.id, video: S.video, frame: S.frame,
               quad: S.quad, rects: S.rects, px2m: S.px2m, length_m: S.lengthM,
               bev_rows: S.bevRows, undistort: S.undist, grid: S.grid,
               mode: S.mode, meas: S.meas, w: 1400 };
    }
    var yamlTimer = null;
    function draw() {
      if (yamlTimer) clearTimeout(yamlTimer);
      yamlTimer = setTimeout(refreshYaml, 500);
      if (!S.video) {
        meta.textContent = '먼저 영상이나 이미지를 고르세요.';
        return;
      }
      if (S.busy) { S.dirty = true; return; }
      S.busy = true;
      post('/api/calib/view', body()).then(function (d) {
        S.busy = false;
        S.meta = d.meta;
        img.src = 'data:image/jpeg;base64,' + d.img;
        drawMeta(d.meta);
        if (S.dirty) { S.dirty = false; draw(); }
      }).catch(function (e) {
        S.busy = false; meta.textContent = '오류: ' + e.message;
      });
    }
    function drawMeta(m) {
      clear(meta);
      var v = m.verticality_deg;
      var verdict = v == null ? '선을 못 찾았습니다'
        : v < 2 ? '잘 맞았습니다' : v < 5 ? '거의 맞았습니다'
        : '좌우 변을 차선에 더 붙이세요';
      meta.appendChild(h('span', {}, [
        h('b', { text: '수직도 ' + (v == null ? '—' : v + '°') }),
        document.createTextNode(' (' + verdict + ', 선 ' + m.lines + '개)')]));
      meta.appendChild(h('span', { text: 'BEV 폭 ' + m.bev_width_m + ' m' }));
      if (!S.still) meta.appendChild(mut('frame ' + S.frame));
      if (!m.sane) {
        meta.appendChild(h('span', { class: 'no', text: '⚠ ' + (m.why || []).join(' / ') }));
      }
      if (S.mode === 'measure') {
        if (S.meas.length >= 2) {
          var d = Math.hypot(S.meas[0][0] - S.meas[1][0], S.meas[0][1] - S.meas[1][1]);
          meta.appendChild(h('span', { class: 'tb', text:
            '잰 거리 ' + d.toFixed(1) + ' px = ' + S.realM.toFixed(2) + ' m  →  px2m = '
            + (S.realM / Math.max(1e-6, d)).toFixed(6) }));
          applyBtn.disabled = false;
          applyBtn.textContent = 'px2m 적용 → ' + (S.realM / Math.max(1e-6, d)).toFixed(6);
        } else {
          applyBtn.disabled = true;
          applyBtn.textContent = 'px2m 적용';
          meta.appendChild(mut('BEV 에서 두 점을 클릭하세요 (지금 ' + S.meas.length + '/2)'));
        }
      }
    }

    // ── 8) 여러 프레임 확인 · 자동 미세조정 ──────────────────────
    /* ★한 장에만 맞는 사각형이 흔하다★ 곡선 구간 한 장으로 맞춰 놓고 직선에서
       어긋난 적이 있다. 네 장을 같이 보면 그게 바로 눈에 띈다. */
    function sampleFrames(n) {
      if (S.still || S.total <= 1) return [S.frame];
      var out = [];
      for (var i = 0; i < n; i++) {
        out.push(Math.round(S.total * (0.12 + 0.76 * (i / Math.max(1, n - 1)))));
      }
      return out;
    }
    var mvImg = h('img', { class: 'bigov' });
    var mvOut = h('div', { class: 'readout' });
    var mvBtn = h('button', { text: '4장 한꺼번에 보기',
      title: '영상 곳곳의 프레임에서 같은 사각형이 통하는지 봅니다',
      onclick: function () {
        if (!S.video) return say('먼저 소스를 고르세요', true);
        mvOut.textContent = '만드는 중…';
        var b = body(); b.frames = sampleFrames(4); b.w = 1200;
        post('/api/calib/multiview', b).then(function (d) {
          mvImg.src = 'data:image/jpeg;base64,' + d.img;
          clear(mvOut);
          mvOut.appendChild(h('span', { text: '수직도 ' + (d.devs.join('° / ') || '—') + '°' }));
          if (d.spread != null) {
            mvOut.appendChild(h('span', { class: d.spread > 3 ? 'no' : 'ok',
              text: '편차 ' + d.spread + '°'
                + (d.spread > 3 ? ' — 한 장면에만 맞은 사각형입니다' : ' — 장면이 바뀌어도 버팁니다') }));
          }
        }).catch(function (e) { mvOut.textContent = '오류: ' + e.message; });
      } });
    var optOut = h('span', { class: 'mut' });
    var optBtn = h('button', { text: '자동 미세조정',
      title: '지금 사각형 주변에서 수직도가 더 좋은 자리를 찾습니다',
      onclick: function () {
        if (!S.video) return say('먼저 소스를 고르세요', true);
        optOut.textContent = '찾는 중… (여러 프레임을 재므로 몇 초 걸립니다)';
        var b = body(); b.frames = sampleFrames(6);
        post('/api/calib/optimize', b).then(function (d) {
          optOut.textContent = d.note;
          if (d.improved) {
            S.quad = d.quad.slice();
            drawFields(); draw();
            say('사각형을 다듬었습니다 — 저장하지는 않았습니다');
          }
        }).catch(function (e) { optOut.textContent = '오류: ' + e.message; });
      } });

    // ── 9) 스냅샷 — 값 세트를 이름 붙여 오간다 ────────────────────
    var snapSel = h('select', {});
    function fillSnaps(names) {
      clear(snapSel);
      snapSel.appendChild(h('option', { value: '', text: '저장된 값…' }));
      (names || []).forEach(function (n) {
        snapSel.appendChild(h('option', { value: n, text: n }));
      });
    }
    fillSnaps(st.snapshots);
    var snapName = h('input', { class: 'wherebox', style: 'max-width:160px',
                                placeholder: '이름 (예: 야간_A)' });
    function applyValues(w, msg) {
      if (w.quad) S.quad = w.quad.slice();
      if (w.rects) S.rects = JSON.parse(JSON.stringify(w.rects));
      if (w.bev_rows) S.bevRows = JSON.parse(JSON.stringify(w.bev_rows));
      if (w.px2m) S.px2m = w.px2m;
      if (w.length_m) S.lengthM = w.length_m;
      drawFields(); draw();
      say(msg);
    }
    var snapRow = bar([
      mut('스냅샷'), snapName,
      h('button', { text: '지금 값 저장', onclick: function () {
        post('/api/snapshot/save', Object.assign(body(), { name: snapName.value }))
          .then(function (d) { fillSnaps(d.names); snapSel.value = snapName.value;
                               say('«' + snapName.value + '» 로 저장했습니다'); })
          .catch(function (e) { say(e.message, true); });
      } }),
      snapSel,
      h('button', { text: '불러오기', onclick: function () {
        if (!snapSel.value) return;
        post('/api/snapshot/load', { profile: st.id, name: snapSel.value })
          .then(function (w) { applyValues(w, '«' + snapSel.value + '» 값을 올렸습니다 (저장 안 함)'); })
          .catch(function (e) { say(e.message, true); });
      } }),
      h('button', { text: '지우기', onclick: function () {
        if (!snapSel.value) return;
        post('/api/snapshot/delete', { profile: st.id, name: snapSel.value })
          .then(function (d) { fillSnaps(d.names); say('지웠습니다'); })
          .catch(function (e) { say(e.message, true); });
      } }),
      h('span', { class: 'spacer' }),
      h('button', { text: '되돌리기', title: '파일에 있던 값으로 (r)', onclick: reset }),
    ]);

    // ── 10) 워크스페이스 기본값 ─────────────────────────────────
    /* 보정 값의 진짜 주인은 워크스페이스다. 그런데 노드는 그것을 소스의
       declare_parameter 기본값으로 갖고 있어서 계약에 옮겨 적을 수밖에 없었다.
       `tb.run params` 가 노드에게 직접 물어 캐시해 두고, 여기서 그 값으로
       되돌릴 수 있게 한다 — 「내가 지금 노드와 같은 값에서 출발했나」의 답이다. */
    var wsBox = mut('');
    function wsLabel() {
      wsBox.textContent = st.ws_stamp ? ('워크스페이스 값 읽은 시각 ' + st.ws_stamp)
                                      : '워크스페이스 값을 아직 안 읽었습니다';
    }
    wsLabel();
    var wsLog = h('pre', { class: 'tasklog' });
    var wsRow = bar([
      h('button', { text: '워크스페이스 기본값 불러오기',
        title: '노드가 스스로 선언한 값으로 되돌립니다',
        onclick: function () {
          if (!st.ws_values) return say('워크스페이스 값이 없습니다 — 먼저 «다시 읽기»', true);
          applyValues(st.ws_values, '노드가 선언한 값으로 되돌렸습니다 (저장 안 함)');
        } }),
      h('button', { text: '다시 읽기',
        title: '노드를 한 번 띄워 파라미터를 새로 받아 옵니다 (20~40초)',
        onclick: function () {
          post('/api/task/wsparams', { profile: st.id })
            .then(function () { watchTask(wsLog, function () {
              get('/api/state?profile=' + encodeURIComponent(st.id)).then(function (s2) {
                st.ws_values = s2.ws_values; st.ws_stamp = s2.ws_stamp;
                wsLabel(); say('받아 왔습니다 — «불러오기» 로 적용하세요');
              });
            }); })
            .catch(function (e) { say(e.message, true); });
        } }),
      wsBox,
    ]);

    // ── 11) 저장 ────────────────────────────────────────────────
    var yamlBox = h('pre', { class: 'md' });
    function refreshYaml() {
      if (!st.id) return;
      post('/api/calib/yaml', body())
        .then(function (d) { yamlBox.textContent = d.yaml; })
        .catch(function (e) { yamlBox.textContent = '오류: ' + e.message; });
    }
    var saveBtn = h('button', { class: 'primary', text: '저장', onclick: function () {
      post('/api/calib/save', body()).then(function (d) {
        say(d.path + ' 에 저장했습니다');
        return get('/api/state?profile=' + encodeURIComponent(st.id))
          .then(function (s2) {
            st.quad = s2.quad; st.rects = s2.rects; st.px2m = s2.px2m;
            st.length_m = s2.length_m; st.bev_rows = s2.bev_rows;
          });
      }).catch(function (e) { say(e.message, true); });
    } });

    // ── 12) 내보내기 ────────────────────────────────────────────
    var expBox = h('pre', { class: 'md' });
    var expPath = h('input', { class: 'wherebox', style: 'flex:1',
                               placeholder: '/경로/워크스페이스/config/camera.yaml' });
    var expBtn = h('button', { text: '명령·YAML 만들기',
      title: '지금 값을 ros2 명령과 파라미터 yaml 로 — 파일은 안 고칩니다',
      onclick: function () {
        expBox.textContent = '만드는 중…';
        post('/api/calib/export', body()).then(function (d) {
          expBox.textContent = (d.launch ? d.launch + '\n\n' : '')
            + '# ── 파라미터 파일 ──\n' + d.params_yaml;
        }).catch(function (e) { expBox.textContent = '오류: ' + e.message; });
      } });
    var writeBtn = h('button', { text: '이 파일에 쓴다', onclick: function () {
      var p = expPath.value.trim();
      if (!p) return say('쓸 파일 경로를 적으세요', true);
      if (!window.confirm(p + '\n\n이 파일을 위 내용으로 ★통째로★ 바꿉니다. 계속할까요?')) return;
      post('/api/calib/export/write', Object.assign(body(), { path: p }))
        .then(function (d) {
          say(d.path + ' 에 ' + (d.existed ? '덮어썼습니다' : '새로 썼습니다'));
        }).catch(function (e) { say(e.message, true); });
    } });

    // ── 13) 체스보드 실측 보정 ──────────────────────────────────
    var cbFolder = h('input', { class: 'wherebox', style: 'flex:1',
                                placeholder: '체스보드 사진들이 있는 폴더' });
    var cbCols = h('input', { type: 'number', class: 'wherebox',
                              style: 'max-width:80px', value: '9' });
    var cbRows = h('input', { type: 'number', class: 'wherebox',
                              style: 'max-width:80px', value: '6' });
    var cbSq = h('input', { type: 'number', class: 'wherebox', step: '0.5',
                            style: 'max-width:90px', value: '25' });
    var cbLog = h('pre', { class: 'tasklog' });
    var cbApply = h('button', { text: '이 값을 프로필에 쓴다', disabled: true });
    var cbResult = null;
    cbApply.addEventListener('click', function () {
      if (!cbResult) return;
      post('/api/intrinsics/apply', { profile: st.id, K: cbResult.K,
                                      D: cbResult.D, size: cbResult.size })
        .then(function (d) { say(d.path + ' 의 K/D/size 를 갈아 끼웠습니다');
                             setTimeout(function () { location.reload(); }, 900); })
        .catch(function (e) { say(e.message, true); });
    });
    var cbBtn = h('button', { text: '보정 실행', onclick: function () {
      cbResult = null; cbApply.disabled = true;
      post('/api/task/chessboard', { profile: st.id, folder: cbFolder.value.trim(),
                                     cols: +cbCols.value, rows: +cbRows.value,
                                     square_mm: +cbSq.value })
        .then(function () { watchTask(cbLog, function (r) {
          cbResult = r;
          cbApply.disabled = !r || !r.ok;
          cbLog.textContent += '\n\nK = [' + r.K.join(', ') + ']\nD = [' + r.D.join(', ') + ']'
            + '\n해상도 ' + r.size.join('×') + '  ·  사진 ' + r.n + '장';
          if (!r.ok) cbLog.textContent += '\n\n⚠ 재투영 오차가 1px 을 넘습니다 — '
            + '흐리거나 각도가 비슷한 사진을 빼고 다시 재세요. 그대로 쓰면 안 됩니다.';
        }); })
        .catch(function (e) { say(e.message, true); });
    } });

    // ── 14) 노드와 대조 ─────────────────────────────────────────
    var vDbg = h('input', { class: 'wherebox', style: 'flex:1',
                            placeholder: '테스트 실행이 남긴 디버그 mp4 경로' });
    var vStart = h('input', { type: 'number', class: 'wherebox',
                              style: 'max-width:90px', value: '0' });
    var vLog = h('pre', { class: 'tasklog' });
    var vImg = h('img', { class: 'bigov', style: 'display:none' });
    var vBtn = h('button', { text: '노드와 대조', onclick: function () {
      if (!S.video) return say('먼저 원본 영상을 고르세요', true);
      var b = body();
      b.debug = vDbg.value.trim();
      b.start = +vStart.value || 0;
      post('/api/task/verify', b).then(function () {
        watchTask(vLog, function () {
          vImg.style.display = '';
          vImg.src = '/api/png?t=' + Date.now();
        });
      }).catch(function (e) { say(e.message, true); });
    } });

    // ── 조립 ────────────────────────────────────────────────────
    var playRow = bar([
      playBtn,
      h('button', { class: 'sigbtn', text: '처음', onclick: function () { step(-1e9); } }),
      h('button', { class: 'sigbtn', text: '−30', title: '[', onclick: function () { step(-30); } }),
      h('button', { class: 'sigbtn', text: '−1', title: ',', onclick: function () { step(-1); } }),
      h('button', { class: 'sigbtn', text: '+1', title: '.', onclick: function () { step(1); } }),
      h('button', { class: 'sigbtn', text: '+30', title: ']', onclick: function () { step(30); } }),
      frIn, totLbl, mut('fps'), fpsIn,
    ]);

    view.appendChild(bar([
      mut('프로필'), pSel,
      h('span', { class: 'spacer' }),
      mut(st.workspace ? '워크스페이스 ' + st.workspace : '독립 프로필 (워크스페이스 없음)'),
    ]));
    view.appendChild(bar([
      h('button', { class: 'primary', text: '영상·이미지 고르기', onclick: function () {
        pickSource(S.video ? S.video.replace(/\/[^/]*$/, '') : '', setSource);
      } }),
      upBtn, upIn, recentSel, srcLbl, upLbl,
      h('span', { class: 'spacer' }), undBtn, gridBtn,
    ]));
    view.appendChild(playRow);
    view.appendChild(slider);
    view.appendChild(bar(modeBtns));
    view.appendChild(hintEl2);
    view.appendChild(fields);
    view.appendChild(img);
    view.appendChild(meta);
    view.appendChild(h('p', { class: 'sub', text:
      '단축키 — 스페이스 재생/정지 · , . 한 프레임 · [ ] 30프레임 · 방향키 1px · '
      + 'Shift+방향키 10px · 1~' + modes.length + ' 편집 대상 · g 격자 · u 왜곡보정 · r 되돌리기' }));

    view.appendChild(sec('한 장면에만 맞지 않았는지',
      '지금 사각형이 <b>영상 곳곳에서도</b> 통하는지 봅니다. 한 프레임에서 완벽해 보이는 '
      + '사각형이 다른 장면에서 무너지는 일이 흔합니다. '
      + '<b>자동 미세조정</b>은 지금 자리 주변에서 수직도가 더 좋은 사각형을 찾습니다 — '
      + '대강이라도 차선 위에 올려 둔 뒤에 눌러야 뜻이 있습니다.'));
    view.appendChild(bar([mvBtn, optBtn, optOut]));
    view.appendChild(mvOut);
    view.appendChild(mvImg);

    view.appendChild(sec('값 관리',
      '<b>스냅샷</b>은 지금 값 전부를 이름 붙여 둡니다 — 「이게 나은가 저게 나은가」를 '
      + '오갈 때 씁니다(이 PC 의 <code>local.yaml</code> 에 남습니다).'));
    view.appendChild(snapRow);
    if (st.workspace) view.appendChild(wsRow);
    if (st.workspace) view.appendChild(wsLog);

    view.appendChild(sec('저장',
      st.kind === 'calib'
        ? '독립 프로필이라 값은 <b>그 프로필 파일의 <code>default:</code></b> 에 들어갑니다. 주석은 그대로 남습니다.'
        : '보정 값은 카메라·차량마다 다른 <b>기계에 묶인 값</b>이라 <code>local.yaml</code> 에 씁니다. '
          + '계약 파일은 저장소에 올라가므로 건드리지 않습니다. 주석은 그대로 남습니다.'));
    view.appendChild(bar([saveBtn, h('button', { text: 'YAML 새로 고침', onclick: refreshYaml })]));
    view.appendChild(yamlBox);

    view.appendChild(sec('워크스페이스로 내보내기',
      '맞춘 값을 <b>실차에서 그대로 쓸 수 있는 형태</b>로 만듭니다 — <code>ros2</code> 명령과 '
      + '<code>--params-file</code> 용 yaml.<br>'
      + '⚠️ 이 값은 <b>맞출 때 쓴 영상의 카메라 설정</b>입니다. 그 영상이 실차 카메라로 '
      + '찍힌 것이 아니면 실차에 그대로 쓰면 안 됩니다.'));
    view.appendChild(bar([expBtn]));
    view.appendChild(expBox);
    view.appendChild(bar([mut('파일에 직접 쓰기'), expPath, writeBtn]));

    view.appendChild(sec('체스보드로 K/D 실측',
      '카메라 내부 파라미터를 <b>사진에서 직접</b> 잽니다. 체스보드를 각도·거리를 바꿔 가며 '
      + '12장 이상 찍어 한 폴더에 두세요. 코너 수는 <b>칸 수가 아니라 내부 코너 수</b>입니다 '
      + '(8×7 칸 → 7×6). 재투영 오차가 0.5px 아래면 좋은 값입니다.'));
    view.appendChild(bar([mut('폴더'), cbFolder,
      h('button', { text: '찾기', onclick: function () {
        pickSource(cbFolder.value.trim(), function (p) {
          cbFolder.value = p.replace(/\/[^/]*$/, '');
        });
      } })]));
    view.appendChild(bar([mut('내부 코너'), cbCols, h('span', { text: '×' }), cbRows,
                          mut('한 칸 [mm]'), cbSq, cbBtn, cbApply]));
    view.appendChild(cbLog);

    if (st.verify) {
      view.appendChild(sec('노드와 대조',
        '여기서 그리는 BEV 가 <b>대상 노드가 실제로 만드는 BEV</b> 와 같은지 봅니다. '
        + '노드가 남긴 디버그 mp4 가 필요합니다 '
        + '(<code>python3 -m tb.run run …</code> 이 기본으로 남깁니다). '
        + '에지 일치율 0.75 이상이면 같은 변환입니다.'));
      view.appendChild(bar([mut('디버그 mp4'), vDbg,
        h('button', { text: '찾기', onclick: function () {
          pickSource(vDbg.value.trim(), function (p) { vDbg.value = p; });
        } }),
        mut('원본 start'), vStart, vBtn]));
      view.appendChild(vLog);
      view.appendChild(vImg);
    }

    view.appendChild(sec('프로필'));
    view.appendChild(newProfileBar());

    setMode(S.mode);
    syncBar();
    drawFields();
    //  ★마지막에 연 것을 그대로 다시 연다★ 스튜디오는 같은 영상을 며칠에 걸쳐
    //  붙드는 도구다. 매번 고르게 하면 그 자체가 일이 된다.
    if ((st.recent || []).length) setSource(st.recent[0].path);
    else draw();
  }

  // ══════════════════════════════════════════════════════════════
  //  도움말 — 「시험은 어디로 갔나」에 답하는 화면
  // ══════════════════════════════════════════════════════════════
  function renderHelp() {
    clear(view);
    if (CAL && CAL.off) CAL.off.forEach(function (f2) { f2(); });
    document.onkeydown = null;
    view.appendChild(h('h1', { text: '사용 안내' }));
    view.appendChild(h('div', { class: 'md doc', html:
      '<h2>이 웹앱은 카메라 보정 도구입니다</h2>'
      + '<p>영상이나 사진 한 장을 열어 <b>IPM 사각형·ROI·픽셀↔미터·BEV 기준선</b>을 '
      + '손으로 맞춥니다. 화면의 BEV 는 대상 노드가 만드는 것과 <b>같은 변환</b>으로 '
      + '그려집니다(서버의 <code>tb.geometry</code>) — 그래서 여기서 맞춘 값이 노드에 '
      + '그대로 적용됩니다. «노드와 대조» 가 그 사실을 매번 확인해 줍니다.</p>'

      + '<h2>순서</h2>'
      + '<ol>'
      + '<li><b>프로필을 고릅니다.</b> 워크스페이스에 붙은 계약(<code>contracts/*.yaml</code>)이거나, '
      + '워크스페이스 없이 카메라만 볼 때는 «새 프로필» 로 만든 <code>calib/*.yaml</code> 입니다. '
      + '무엇을 맞출지(사각형·ROI·기준선)는 전부 프로필이 정합니다.</li>'
      + '<li><b>영상이나 이미지를 고릅니다.</b> 등록 절차는 없습니다 — 경로만 씁니다. '
      + '사진 한 장으로도 됩니다.</li>'
      + '<li><b>맞춥니다.</b> 요령은 하나입니다 — <b>사각형의 좌우 변을 차선 위에 올려라.</b> '
      + '지면은 평면이라 그렇게 놓으면 BEV 에서 차선이 정확히 수직으로 섭니다. '
      + '수직이 아니면 사각형이 틀린 것이고, 격자가 그 자입니다.</li>'
      + '<li><b>여러 장면에서 확인합니다.</b> 한 프레임에만 맞는 사각형이 흔합니다.</li>'
      + '<li><b>저장하고 내보냅니다.</b> 저장은 이 PC 안이고, 내보내기는 워크스페이스로 '
      + '가져갈 형태입니다.</li>'
      + '</ol>'

      + '<h2>알고리즘 시험은 어디로 갔나</h2>'
      + '<p>웹앱에서 뺐습니다. 예전에는 워크스페이스를 등록하고 · 영상에 이름을 붙이고 · '
      + '시나리오를 만들고 · 임계값을 정한 다음에야 한 번 돌릴 수 있었는데, '
      + '그 네 단계가 실제로 하려던 일(영상 하나 돌려 보기)보다 컸습니다. '
      + '지금은 <b>명령 한 줄</b>이거나, <b>클로드에게 말로</b> 시킵니다.</p>'
      + '<pre>source /opt/ros/humble/setup.bash\n'
      + 'python3 -m tb.run doctor --contract contracts/&lt;계약&gt;.yaml\n'
      + 'python3 -m tb.run run --contract contracts/&lt;계약&gt;.yaml \\\n'
      + '    --video /경로/영상.mp4 --start 300 --limit 900 \\\n'
      + '    --note "정지선에서 제때 서는지" --out ~/&lt;워크스페이스&gt;</pre>'
      + '<p>결과는 <code>&lt;워크스페이스&gt;/testbed_results/&lt;런&gt;/</code> 에 '
      + '리포트·CSV·<b>디버그 영상</b>·실행 조건으로 남습니다. '
      + '합격/불합격을 찍지 않습니다 — 그 판단은 결과를 읽는 사람이 합니다.</p>'
      + '<p>클로드 코드에서는 워크스페이스에서 <b>«이 영상으로 테스트해줘»</b> 라고 하면 '
      + '<code>cam-test</code> 스킬이 계약을 찾고 · 돌리고 · 결과를 정리해 읽어 줍니다.</p>'

      + '<h2>이 화면이 안 열릴 때</h2>'
      + '<ul>'
      + '<li><b>프로필이 없습니다</b> — 계약에 <code>calibration:</code> 블록이 없으면 '
      + '목록에 안 뜹니다. «새 프로필» 로 하나 만들면 워크스페이스 없이도 됩니다.</li>'
      + '<li><b>왜곡보정이 이상합니다</b> — 프로필의 K/D 가 이 카메라 것이 아닐 수 있습니다. '
      + '«체스보드로 K/D 실측» 으로 다시 재세요.</li>'
      + '<li><b>BEV 에서 선을 못 찾습니다</b> — 차선이 안 보이는 프레임입니다. '
      + '다른 프레임으로 옮기세요.</li>'
      + '</ul>' }));
  }

  // ══════════════════════════════════════════════════════════════
  //  라우팅 — 화면은 둘뿐이다
  // ══════════════════════════════════════════════════════════════
  var CUR = '';
  function open(pid, keepVideo) {
    view.appendChild(h('div', { class: 'loading', text: '불러오는 중…' }));
    get('/api/state' + (pid ? '?profile=' + encodeURIComponent(pid) : ''))
      .then(function (st) {
        CUR = st.id || '';
        //  프로필을 바꿔도 보던 영상은 그대로 둔다(같은 카메라를 여러 프로필로 본다)
        if (keepVideo) {
          st.recent = [{ path: keepVideo }].concat(
            (st.recent || []).filter(function (r) { return r.path !== keepVideo; }));
        }
        renderStudio(st);
        footEl.textContent = st.file ? (st.kind + '/' + st.file) : '';
      })
      .catch(fail);
  }

  function route() {
    var hash = location.hash.replace(/^#/, '') || '/';
    document.querySelectorAll('nav a').forEach(function (a) {
      a.classList.toggle('on', a.getAttribute('href') === '#' + hash);
    });
    if (window.__stopPoll) { window.__stopPoll(); window.__stopPoll = null; }
    clear(view);
    if (hash === '/help') return renderHelp();
    return open(CUR, CAL ? CAL.video : '');
  }
  window.addEventListener('hashchange', route);
  route();
})();
