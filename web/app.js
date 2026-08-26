/* 테스트베드 웹 뷰어 — 라우팅과 렌더링.
 *
 * ★규칙★ 판정은 절대 여기서 하지 않는다. summary.json 의 checks[].ok 를
 * 색칠할 뿐이고, 임계값 비교를 JS 에 한 벌 더 쓰지 않는다(엔진과 어긋난다). */
(function () {
  'use strict';

  var ROOTDIR = '';                     // 테스트베드 루트 — 명령 안내에 쓴다
  var REPO = '';                        // 저장소 주소 — 「직접 돌려 보기」 링크

  /* ★정적 내보내기(GitHub Pages)★ 에서는 서버가 없다 — API 응답이 미리 구워진
     파일이고, ros2 를 띄우거나 설정을 쓰는 화면은 아예 열지 않는다.
     tb/publish.py 가 index.html 에 window.STATIC 을 심는다. */
  var STATIC = !!window.STATIC;

  /* 서버가 있어야만 되는 구역. 정적에서는 탭·홈 카드에서 빼고 라우트도 막는다.
     «결과 비교» 는 비교 작업을 띄우는 화면이라 여기 든다 — 이미 나온 비교는
     실행 상세의 «비교» 탭에 그대로 있다. */
  var SERVERONLY = { newtest: 1, exec: 1, calib: 1, check: 1, tools: 1,
                     compare: 1, trash: 1, visual: 1 };
  var SECNAME = { newtest: '새 시험 시작', exec: '테스트 실행', calib: '카메라 보정',
                  check: '환경 점검', tools: '도구', compare: '결과 비교',
                  trash: '휴지통', frames: '프레임 탐색', visual: '시각화' };

  /* 라우트를 구운 파일 이름으로. ★tb/publish.py 가 같은 규칙으로 쓴다★ —
     한쪽만 고치면 화면이 통째로 빈다. 쿼리는 경로 한 칸이 된다
     (log?name=perception → api/runs/<런>/log/perception.txt). */
  var TEXTY = { report: 1, compare: 1, feedback: 1, log: 1 };
  function apiURL(u) {
    if (!STATIC) return u;
    var qi = u.indexOf('?');
    var path = (qi < 0 ? u : u.slice(0, qi)).replace(/^\/api\//, '');
    var tail = path.split('/').pop();
    if (qi >= 0) {
      var v = u.slice(qi + 1).split('&')[0].split('=')[1] || '';
      path += '/' + decodeURIComponent(v);
    }
    return 'api/' + path + (TEXTY[tail] ? '.txt' : '.json');
  }
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
    var u = apiURL(url);
    return fetch(u).then(function (r) {
      if (!r.ok) {
        /* 정적 호스팅의 404 는 JSON 이 아니라 HTML 이다 — r.json() 을 바로
           부르면 진짜 원인이 파싱 오류로 덮인다. */
        return r.text().then(function (t) {
          var msg = '';
          try { msg = JSON.parse(t).error || ''; } catch (e) { msg = ''; }
          throw new Error(msg || ('HTTP ' + r.status));
        });
      }
      var ct = r.headers.get('content-type') || '';
      var isText = STATIC ? /\.txt$/.test(u) : ct.indexOf('json') < 0;
      return isText ? r.text() : r.json();
    });
  }

  /* 정적에서는 쓰기가 없다. 남아 있는 버튼이 조용히 실패하지 않게 여기서 막는다. */
  function readOnly() {
    hintEl.textContent = '읽기 전용 사이트입니다 — 이 동작은 서버가 있어야 합니다';
    setTimeout(function () { hintEl.textContent = ''; }, 3000);
    return Promise.resolve({ error: '읽기 전용' });
  }
  var f = window.fmtNum;
  function pct(v, d) { return v == null ? '—' : (v * 100).toFixed(d == null ? 1 : d) + '%'; }
  function shortTime(s) {
    if (!s) return '—';
    return String(s).replace('T', ' ').slice(5, 16);
  }
  function badge(txt, cls) { return h('span', { class: 'badge ' + cls, text: txt }); }

  function checkBadge(r) {
    if (!r.checks_total) return badge('—', 'n');
    var bad = r.checks_bad || 0;
    return badge(r.checks_ok + '/' + r.checks_total, bad ? 'r' : 'g');
  }
  function cmpBadge(v) {
    if (!v) return badge('—', 'n');
    return badge(v, v === 'PASS' ? 'g' : 'r');
  }

  function cli(cmd) { return h('div', { class: 'cli', text: '$ ' + cmd }); }

  function spinner(msg) {
    return h('div', { class: 'spin' }, [
      h('i', {}), h('span', { text: msg || '불러오는 중…' })]);
  }

  /* 작업 실행 — 웹앱은 CLI 를 부를 뿐이다. 결과는 파일로 남는다.
     화면에는 CLI 이름(run·inject…) 대신 사람이 읽는 이름을 쓴다. */
  var JOBNAME = { run: '실행', inject: '주입 검증', doctor: '환경 점검',
                  selftest: '자체 검사', discover: '계약 초안',
                  render: '경로 영상 만들기', harvest: '프레임 추출',
                  baseline: '기준 등록', reanalyze: '재분석', compare: '결과 비교' };
  function postJob(kind, args, onDone) {
    if (STATIC) return readOnly();
    return fetch('/api/jobs', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: kind, args: args || [] }) })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        hintEl.textContent = j.error ? j.error
                             : ((JOBNAME[kind] || kind) + ' 시작했습니다');
        setTimeout(function () { hintEl.textContent = ''; }, 3000);
        if (onDone) onDone(j);
        return j;
      });
  }

  /* 작업이 끝날 때까지 기다린다. 화면을 떠나면 라우터가 세운다(__stopPoll).
     짧게 도는 작업(파라미터 읽기 같은)을 한 줄로 기다리기 위한 것이다. */
  function waitJob(done) {
    var t = setInterval(function () {
      get('/api/status').then(function (st) {
        if (st && st.running) return;
        clearInterval(t);
        if (done) done(st);
      }).catch(function () { clearInterval(t); });
    }, 1500);
    var prev = window.__stopPoll;
    window.__stopPoll = function () { clearInterval(t); if (prev) prev(); };
  }

  // ── 홈 — 무엇을 할지 고른다 ─────────────────────────────────────
  //   첫 화면이 곧바로 목록이면 "지금 뭘 하려던 거였지"를 화면이 안 도와준다.
  var HOME = [
    ['#/newtest', '새 시험 시작',
     '돌리기 전에 등록하는 것은 전부 여기 — 워크스페이스 · 영상 · 시나리오, '
     + '그다음 카메라 맞추기.'],
    ['#/exec', '테스트 실행',
     '있는 시나리오를 골라 돌립니다. 무엇이 쓰일지 먼저 펼쳐 보여 주고, '
     + '파라미터도 여기서 고칩니다.'],
    ['#/runs', '실행 기록',
     '지난 결과를 모아 봅니다. 검색·고정·메모·삭제, 그리고 클로드 코드에 넘길 피드백 만들기.'],
    ['#/compare', '결과 비교',
     '기준과 실행, 또는 두 실행의 신호 차이를 봅니다.'],
    ['#/calib', '카메라 보정',
     'IPM 4점과 px2m 을 맞춥니다. 차선 폭이 실제 길이와 다르면 여기부터.'],
    ['#/baselines', '기준 관리',
     '비교의 기준으로 삼은 결과들. 어떤 영상·구간에서 나왔는지 함께 봅니다.'],
    ['#/check', '환경 점검',
     '계약·워크스페이스·영상이 제대로 물려 있는지, 테스트베드 자체는 이상 없는지.'],
    ['#/tools', '도구',
     '터미널에서 쓰는 명령을 전부 여기서. 인자를 골라 넣고 명령줄을 확인한 뒤 실행합니다.'],
    ['#/help', '사용 안내',
     '처음 켠 사람이 어떤 순서로 쓰면 되는지.'],
  ];

  function renderHome() {
    clear(view);
    view.appendChild(h('h1', { text: '카메라 테스트베드' }));
    view.appendChild(h('p', { class: 'sub',
      text: '무엇을 할지 고르세요. 위쪽 탭으로도 바로 갑니다.' }));
    var cards = HOME.filter(function (c) {
      return !(STATIC && SERVERONLY[c[0].replace('#/', '')]);
    });
    view.appendChild(h('div', { class: 'homegrid' }, cards.map(function (c) {
      return h('a', { class: 'hcard', href: c[0] }, [
        h('div', { class: 'ht', text: c[1] }),
        h('div', { class: 'hd', text: c[2] }),
      ]);
    })));
    footEl.textContent = '';
  }

  // ── 실행 기록 ───────────────────────────────────────────────────
  //   정리(고정·메모·태그·삭제)는 결과 파일을 건드리지 않는다.
  //   고정/메모/태그는 runs/_index.json 에, 삭제는 runs/_trash/ 이동으로 끝난다.
  var RUNQ = { q: '', mode: '', state: '', sort: 'time' };
  var SEL = {};

  function postJSON(url, body) {
    if (STATIC) return readOnly();
    return fetch(url, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json(); });
  }
  function saveMeta(id, patch) {
    patch.id = id;
    return postJSON('/api/runs/meta', patch);
  }
  function selIds() {
    return Object.keys(SEL).filter(function (k) { return SEL[k]; });
  }
  function rateOf(r) {
    var c = (r.contribution || [])[0];
    return (c && c.rate != null) ? c.rate : 9;      // 값 없는 것은 뒤로
  }
  function biasOf(r) {
    return (r.theta && r.theta.abs_bias_deg != null) ? r.theta.abs_bias_deg : -1;
  }
  function matchRun(r) {
    var q = RUNQ.q.trim().toLowerCase();
    if (q) {
      var hay = [r.id, r.scenario, r.variant, r.memo, (r.tags || []).join(' ')]
        .filter(Boolean).join(' ').toLowerCase();
      if (hay.indexOf(q) < 0) return false;
    }
    if (RUNQ.mode && r.mode !== RUNQ.mode) return false;
    if (RUNQ.state === 'fail' && !(r.checks_bad > 0)) return false;
    if (RUNQ.state === 'diff' && r.compare !== 'DIFF') return false;
    if (RUNQ.state === 'none' && r.has_summary) return false;
    if (RUNQ.state === 'pin' && !r.pin) return false;
    return true;
  }
  function cmpRun(a, b) {
    if (!!b.pin !== !!a.pin) return b.pin ? 1 : -1;   // 고정은 언제나 위
    var s = RUNQ.sort;
    if (s === 'old') return a.id < b.id ? -1 : 1;
    if (s === 'fail') return (b.checks_bad || 0) - (a.checks_bad || 0);
    if (s === 'contrib') return rateOf(a) - rateOf(b);
    if (s === 'bias') return biasOf(b) - biasOf(a);
    return a.id < b.id ? 1 : -1;
  }
  function reloadRuns() { return get('/api/runs').then(renderRuns).catch(fail); }

  function renderRuns(data) {
    var runs = data.runs || [];
    SEL = {};
    clear(view);
    view.appendChild(h('h1', { text: '실행 기록' }));
    view.appendChild(h('p', { class: 'sub',
      text: '행을 누르면 상세로 들어갑니다. 왼쪽을 체크하면 여러 건을 한꺼번에 정리합니다.' }));

    if (!runs.length) {
      view.appendChild(h('div', { class: 'empty',
        text: '아직 실행한 결과가 없습니다. «테스트 실행» 탭에서 시나리오를 하나 돌려 보세요.' }));
      return;
    }

    // ── 도구 막대 ────────────────────────────────────────────────
    var qIn = h('input', { class: 'q', type: 'search', value: RUNQ.q,
      placeholder: '검색 — 시나리오·태그·메모·이름',
      oninput: function () { RUNQ.q = this.value; paint(); } });
    function sel(key, opts) {
      return h('select', { onchange: function () { RUNQ[key] = this.value; paint(); } },
        opts.map(function (o) {
          return h('option', { value: o[0], text: o[1],
                               selected: RUNQ[key] === o[0] ? 'selected' : null });
        }));
    }
    var modeSel = sel('mode', [['', '모드 전체'], ['lockstep', 'lockstep'],
                               ['realtime', 'realtime']]);
    var stateSel = sel('state', [['', '상태 전체'], ['fail', '체크 실패 있음'],
                                 ['diff', '기준과 다름'], ['pin', '고정한 것만'],
                                 ['none', '분석 결과 없음']]);
    var sortSel = sel('sort', [['time', '최신순'], ['old', '오래된순'],
                               ['fail', '실패 많은순'],
                               ['contrib', '실질 기여율 낮은순'],
                               ['bias', 'θ 편향 큰순']]);
    view.appendChild(h('div', { class: 'toolbar' }, [
      qIn, modeSel, stateSel, sortSel,
      h('span', { class: 'spacer' }),
      STATIC ? null : h('a', { class: 'gobtn', href: '#/trash', text: '휴지통' }),
    ]));

    var selbar = h('div', { class: 'selbar' });
    view.appendChild(selbar);
    var tblBox = h('div', {});
    view.appendChild(tblBox);
    var foot = h('div', {});
    view.appendChild(foot);

    // ── 선택 막대 ────────────────────────────────────────────────
    function paintSel() {
      clear(selbar);
      var ids = selIds();
      if (!ids.length) { selbar.className = 'selbar'; return; }
      selbar.className = 'selbar on';
      selbar.appendChild(h('b', { text: '선택 ' + ids.length + '건' }));
      selbar.appendChild(h('button', { text: '고정', onclick: function () {
        Promise.all(ids.map(function (id) { return saveMeta(id, { pin: true }); }))
          .then(reloadRuns);
      } }));
      selbar.appendChild(h('button', { text: '고정 해제', onclick: function () {
        Promise.all(ids.map(function (id) { return saveMeta(id, { pin: false }); }))
          .then(reloadRuns);
      } }));
      selbar.appendChild(h('button', { text: '메모', disabled: ids.length !== 1 ? '' : null,
        title: '한 건만 골랐을 때 쓸 수 있습니다', onclick: function () {
          var cur = (runs.filter(function (r) { return r.id === ids[0]; })[0] || {}).memo || '';
          var v = prompt('이 실행에 남길 메모', cur);
          if (v == null) return;
          saveMeta(ids[0], { memo: v }).then(reloadRuns);
        } }));
      selbar.appendChild(h('button', { text: '태그', onclick: function () {
        var v = prompt('태그 (쉼표로 구분). 비우면 지웁니다.', '');
        if (v == null) return;
        var tags = v.split(',').map(function (t) { return t.trim(); })
                    .filter(Boolean);
        Promise.all(ids.map(function (id) { return saveMeta(id, { tags: tags }); }))
          .then(reloadRuns);
      } }));
      selbar.appendChild(h('span', { class: 'spacer' }));
      selbar.appendChild(h('button', { class: 'danger', text: '삭제', onclick: function () {
        if (!confirm('선택한 ' + ids.length + '건을 휴지통으로 보냅니다.\n'
                     + 'runs/_trash/ 로 옮길 뿐이라 나중에 복원할 수 있습니다.')) return;
        postJSON('/api/runs/trash', { ids: ids }).then(function (j) {
          hintEl.textContent = (j.moved || []).length + '건을 휴지통으로 보냈습니다';
          setTimeout(function () { hintEl.textContent = ''; }, 3000);
          reloadRuns();
        });
      } }));
      selbar.appendChild(h('button', { text: '선택 해제', onclick: function () {
        SEL = {}; paint();
      } }));
    }

    // ── 표 ───────────────────────────────────────────────────────
    function starCell(r) {
      return h('td', { class: 'star', onclick: function (e) {
        e.stopPropagation();
        saveMeta(r.id, { pin: !r.pin }).then(reloadRuns);
      } }, [h('span', { class: r.pin ? 'on' : '', text: r.pin ? '★' : '☆' })]);
    }
    function checkCell(r) {
      return h('td', { class: 'pick', onclick: function (e) { e.stopPropagation(); } }, [
        h('input', { type: 'checkbox', checked: SEL[r.id] ? 'checked' : null,
          onchange: function () { SEL[r.id] = this.checked; paintSel(); } })]);
    }
    function nameCell(r) {
      var kids = [h('div', { text: r.scenario || r.id.slice(0, 14) })];
      var tags = (r.tags || []).map(function (t) {
        return h('span', { class: 'tag', text: t });
      });
      if (tags.length) kids.push(h('div', { class: 'tags' }, tags));
      if (r.memo) kids.push(h('div', { class: 'memo', text: r.memo }));
      return h('td', {}, kids);
    }

    function paint() {
      var rows = runs.filter(matchRun).sort(cmpRun);
      clear(tblBox);
      clear(foot);
      paintSel();
      if (!rows.length) {
        tblBox.appendChild(h('div', { class: 'empty', text: '조건에 맞는 실행이 없습니다.' }));
        return;
      }
      // [보이는 이름, 마우스를 올렸을 때 나오는 설명]
      var head = [['', ''], ['', ''], ['시각', ''], ['시나리오', ''], ['변형', ''],
                  ['모드', 'lockstep = 한 프레임씩 · realtime = 실제 속도로'],
                  ['결과 행', '분석에 쓰인 출력 행 수'],
                  ['인식률', '차선을 본 프레임의 비율'],
                  ['체크', '통과한 불변식 체크 수 / 전체'],
                  ['기여율', '실질 기여율 — 받는 쪽 노드가 실제로 갖다 쓴 프레임의 비율'],
                  ['θ 편향', '직진 구간에서 θ 가 0 에서 벗어난 정도'],
                  ['기준 대비', '기준으로 등록해 둔 결과와 같은지'], ['', '']];
      var thead = h('tr', {}, head.map(function (t) {
        return h('th', { text: t[0], title: t[1] || null });
      }));
      var body = h('tbody', {}, rows.map(function (r) {
        var go = function () { location.hash = '#/run/' + r.id; };
        if (!r.has_summary) {
          return h('tr', { class: 'click', onclick: go }, [
            checkCell(r), starCell(r),
            h('td', { class: 'mono', text: r.id }),
            h('td', { colspan: '9', class: 'mut',
                      text: r.has_inject ? '주입 검증 결과'
                                         : (r.memo || r.note || '분석 결과 없음') }),
            h('td', {}, [h('span', { class: 'mut', text: '›' })]),
          ]);
        }
        var contrib = (r.contribution && r.contribution[0]) || {};
        var bias = (r.theta || {}).abs_bias_deg;
        return h('tr', { class: 'click', onclick: go }, [
          checkCell(r), starCell(r),
          h('td', { class: 'mono', text: shortTime(r.when) }),
          nameCell(r),
          h('td', { class: 'mono', text: r.variant || '—' }),
          h('td', { class: 'mono mut', text: r.mode || '—' }),
          h('td', { class: 'mono num', text: r.rows != null ? String(r.rows) : '—' }),
          h('td', { class: 'mono num', text: r.valid_rate != null ? pct(r.valid_rate, 0) : '—' }),
          h('td', {}, [checkBadge(r)]),
          h('td', {}, [contrib.rate == null ? badge('—', 'n')
                       : badge(pct(contrib.rate), contrib.rate >= 0.3 ? 'g' : 'r')]),
          h('td', {}, [bias == null ? badge('—', 'n')
                       : badge(bias.toFixed(2) + '°', bias <= 1 ? 'g' : 'y')]),
          h('td', {}, [cmpBadge(r.compare)]),
          h('td', { class: 'mut', text: r.has_feedback ? '📝 ›' : '›' }),
        ]);
      }));
      tblBox.appendChild(h('div', { class: 'tbl' }, [
        h('table', {}, [h('thead', {}, [thead]), body])]));
      foot.appendChild(h('p', { class: 'sub',
        text: '보이는 ' + rows.length + '건 / 전체 ' + runs.length + '건' }));
      foot.appendChild(cli('python3 -m tb.run list'));
    }

    paint();
    footEl.textContent = '실행 ' + runs.length + '건';
  }

  // ── 휴지통 ──────────────────────────────────────────────────────
  function renderTrash(data) {
    var items = data.trash || [];
    var pick = {};
    clear(view);
    view.appendChild(h('a', { class: 'back', href: '#/runs', text: '‹ 실행 기록' }));
    view.appendChild(h('h1', { text: '휴지통' }));
    view.appendChild(h('p', { class: 'sub',
      text: '삭제한 실행은 지워지지 않고 runs/_trash/ 에 그대로 있습니다. '
            + '복원하거나, 디스크를 비우려면 완전히 지웁니다.' }));
    if (!items.length) {
      view.appendChild(h('div', { class: 'empty', text: '휴지통이 비어 있습니다.' }));
      return;
    }
    var bar = h('div', { class: 'framebar' }, [
      h('button', { text: '선택 복원', onclick: function () {
        var ids = Object.keys(pick).filter(function (k) { return pick[k]; });
        if (!ids.length) return;
        postJSON('/api/runs/restore', { ids: ids }).then(function () {
          get('/api/trash').then(renderTrash);
        });
      } }),
      h('span', { class: 'spacer' }),
      h('button', { class: 'danger', text: '휴지통 비우기 (완전 삭제)', onclick: function () {
        if (!confirm(items.length + '건을 영구히 지웁니다. 되돌릴 수 없습니다.')) return;
        postJSON('/api/trash/empty', {}).then(function (j) {
          hintEl.textContent = (j.removed || 0) + '건을 완전히 지웠습니다';
          setTimeout(function () { hintEl.textContent = ''; }, 3000);
          get('/api/trash').then(renderTrash);
        });
      } }),
    ]);
    view.appendChild(bar);
    var body = h('tbody', {}, items.map(function (t) {
      return h('tr', {}, [
        h('td', { class: 'pick' }, [h('input', { type: 'checkbox',
          onchange: function () { pick[t.id] = this.checked; } })]),
        h('td', { class: 'mono', text: t.id }),
        h('td', { class: 'mono mut', text: shortTime(t.when) }),
      ]);
    }));
    view.appendChild(h('div', { class: 'tbl' }, [h('table', {}, [
      h('thead', {}, [h('tr', {}, ['', '이름', '옮긴 시각'].map(function (x) {
        return h('th', { text: x }); }))]), body])]));
    footEl.textContent = '휴지통 ' + items.length + '건';
  }

  // ── 기준 목록 ───────────────────────────────────────────────────
  function renderBaselines(data) {
    var bl = data.baselines || [];
    clear(view);
    view.appendChild(h('h1', { text: '기준 관리' }));
    view.appendChild(h('p', { class: 'sub',
      text: '비교의 기준이 되는 결과입니다. 영상·구간이 다르면 비교해도 의미가 없어서 '
            + '어디서 나온 기준인지 함께 남깁니다.' }));
    if (!bl.length) {
      view.appendChild(h('div', { class: 'empty', text: '등록된 기준이 없습니다.' }));
      return;
    }
    var thead = h('tr', {}, ['이름', '시나리오', '영상', '구간', '모드', '등록 시각']
      .map(function (t) { return h('th', { text: t }); }));
    var body = h('tbody', {}, bl.map(function (b) {
      var seg = (b.start != null || b.limit != null)
        ? 'start ' + (b.start != null ? b.start : '?') +
          ' / limit ' + (b.limit != null ? b.limit : '?') +
          (b.stride > 1 ? ' / stride ' + b.stride : '')
        : '—';
      return h('tr', {}, [
        h('td', { class: 'mono', text: b.name }),
        h('td', { text: b.scenario || '—' }),
        h('td', { class: 'mono mut', text: b.video_key || (b.video || '—').split('/').pop() }),
        h('td', { class: 'mono mut', text: seg }),
        h('td', { class: 'mono mut', text: b.mode || '—' }),
        h('td', { class: 'mono mut', text: shortTime(b.when) }),
      ]);
    }));
    view.appendChild(h('div', { class: 'tbl' }, [
      h('table', {}, [h('thead', {}, [thead]), body])]));
    view.appendChild(cli('python3 -m tb.run baseline <실행> --name <이름>'));
  }

  // ── 실행 상세 ─────────────────────────────────────────────────────
  function sectionTitle(t) { return h('h2', { text: t }); }

  function renderChecks(checks) {
    if (!checks || !checks.length) return null;
    var thead = h('tr', {}, ['항목', '측정값', '기준', '결과', '설명']
      .map(function (t) { return h('th', { text: t }); }));
    var body = h('tbody', {}, checks.map(function (c) {
      var b = c.bound || {};
      var bound = Object.keys(b).map(function (k) { return k + '=' + b[k]; }).join(' / ') || '—';
      var mark = c.ok === true ? badge('통과', 'g')
        : c.ok === false ? badge('실패', 'r') : badge('미판정', 'y');
      return h('tr', {}, [
        h('td', { class: 'mono', text: c.check }),
        h('td', { class: 'mono num', text: c.value == null ? '—' : f(c.value) }),
        h('td', { class: 'mono mut', text: bound }),
        h('td', {}, [mark]),
        h('td', { class: 'mut', text: c.note || '' }),
      ]);
    }));
    return h('div', { class: 'tbl' }, [h('table', {}, [h('thead', {}, [thead]), body])]);
  }

  /* ── 단계 전이 — ★언제 물었나★ ──────────────────────────────────
     계약의 events: 가 정한 신호가 언제 어느 값으로 바뀌었고, ★그 순간★ 다른
     신호가 얼마였는지. 단계적으로 개입하는 노드(예비제동 → 확정 정지)는
     이 표가 판정의 본체다 — 평균으로는 '언제' 를 말할 수 없다. */
  function renderEvents(events) {
    if (!events || !events.length) return null;
    var wrap = h('div', {});
    events.forEach(function (ev) {
      var tr = ev.transitions || [];
      wrap.appendChild(sectionTitle('단계 전이 — ' + (ev.label || ev.signal)));
      if (ev.why) wrap.appendChild(h('p', { class: 'sub', text: ev.why }));
      if (!tr.length) {
        wrap.appendChild(h('div', { class: 'empty', text: '전이가 한 번도 없었습니다.' }));
        return;
      }
      var at = ev.at || [];
      var head = ['프레임', '시각[s]', '이전 → 이후'].concat(at);
      var thead = h('tr', {}, head.map(function (t) { return h('th', { text: t }); }));
      var body = h('tbody', {}, tr.map(function (t) {
        var up = Number(t.to) > Number(t.from);
        return h('tr', {}, [
          h('td', { class: 'mono', text: String(t.frame) }),
          h('td', { class: 'mono num', text: t.t_s == null ? '—' : t.t_s.toFixed(2) }),
          h('td', {}, [badge(String(t.from) + ' → ' + String(t.to), up ? 'r' : 'g')]),
        ].concat(at.map(function (k) {
          return h('td', { class: 'mono num', text: f(t[k]) });
        })));
      }));
      wrap.appendChild(h('div', { class: 'tbl' }, [
        h('table', {}, [h('thead', {}, [thead]), body])]));
    });
    return wrap;
  }

  /* ── 노드 로그 — 토픽에 없는 근거 ──────────────────────────────────
     기동 배너(어떤 가중치·캘리브를 읽었나)와 개입 사유([예비제동]/[정지선 앞]…)는
     토픽으로 나오지 않는다. 계약의 log_events: 가 찾을 것을 정의하고, 여기서는
     몇 번 나왔는지와 실제 줄 하나를 보여 준다. */
  function renderLogEvents(le) {
    if (!le || !Object.keys(le).length) return null;
    var rows = Object.keys(le).map(function (k) { return [k, le[k]]; });
    rows.sort(function (a, b) { return (b[1].count || 0) - (a[1].count || 0); });
    var thead = h('tr', {}, ['이벤트', '횟수', '본 것'].map(function (t) {
      return h('th', { text: t });
    }));
    var body = h('tbody', {}, rows.map(function (r) {
      var e = r[1];
      var note = e.sample || e.error || (e.log_missing ? '로그 파일 없음' : '—');
      return h('tr', { title: e.why || '' }, [
        h('td', { class: 'mono', text: r[0] }),
        h('td', {}, [badge(String(e.count || 0), e.count ? 'g' : 'n')]),
        h('td', { class: 'mono small', text: note.slice(0, 180) }),
      ]);
    }));
    return h('div', { class: 'tbl' }, [
      h('table', {}, [h('thead', {}, [thead]), body])]);
  }

  function renderGates(funnel) {
    if (!funnel || !funnel.length) return null;
    var box = h('div', {});
    funnel.forEach(function (fn) {
      var total = fn.total || 1;
      var rows = (fn.stages || []).map(function (s) {
        var w = Math.max(0.4, (s.kept / total) * 100);
        var worst = (s.name === fn.bottleneck && s.dropped > 0);
        return h('div', { class: 'stage' + (worst ? ' worst' : '') }, [
          h('div', { class: 'nm', text: s.name, title: s.expr }),
          h('div', { class: 'bar' }, [h('i', { style: 'width:' + w + '%' })]),
          h('div', { class: 'val', text: s.kept + '  ' + (s.cum_rate * 100).toFixed(1) + '%' }),
        ]);
      });
      box.appendChild(h('div', { class: 'gate' }, [
        h('div', { class: 'hd' }, [
          h('span', { class: 'nm', text: fn.label || fn.id }),
          h('span', { class: 'rt' + (fn.rate >= 0.3 ? ' ok' : ' no'),
                      text: '실질 기여율 ' + pct(fn.rate) }),
          h('span', { class: 'src', text: fn.source || '' }),
        ]),
        h('div', { class: 'stage' }, [
          h('div', { class: 'nm', text: '전체' }),
          h('div', { class: 'bar' }, [h('i', { style: 'width:100%' })]),
          h('div', { class: 'val', text: total + '  100.0%' }),
        ]),
      ].concat(rows).concat([
        fn.bottleneck ? h('div', { class: 'mut', style: 'font-size:12px;margin-top:9px',
          text: '가장 많이 걸러지는 곳: ' + fn.bottleneck
                + ' — 여기를 고치지 않으면 기여율은 오르지 않습니다' }) : null,
      ])));
    });
    return box;
  }

  function renderTheta(tq) {
    if (!tq || tq.n == null || tq.note) return null;
    var items = [
      ['θ 편향', tq.bias_deg == null ? '—' : tq.bias_deg.toFixed(3) + '°',
       '직진인데 0 이 아니면 헤딩이 한쪽으로 끌립니다'],
      ['진동 대역 비중', tq.vibration_frac == null ? '—' : tq.vibration_frac.toFixed(3),
       '크면 카메라가 조향 떨림을 만들고 있습니다'],
      ['직진 구간 비율', pct(tq.straight_frac),
       'θ 변화율로 어림잡습니다 (자이로가 없어서)'],
      ['가장 센 진동수', tq.peak_hz == null ? '—' : tq.peak_hz.toFixed(3) + ' Hz', ''],
    ];
    return h('div', { class: 'cards' }, items.map(function (it) {
      return h('div', { class: 'card' }, [
        h('div', { class: 'k', text: it[0] }),
        h('div', { class: 'v', text: it[1] }),
        it[2] ? h('div', { class: 'd', text: it[2] }) : null,
      ]);
    }));
  }

  function renderFlags(fr) {
    if (!fr) return null;
    return h('div', { class: 'cards' }, Object.keys(fr).map(function (k) {
      return h('div', { class: 'card' }, [
        h('div', { class: 'k', text: k }),
        h('div', { class: 'v', text: pct(fr[k]) }),
      ]);
    }));
  }

  function renderSignalStats(sigs) {
    if (!sigs) return null;
    var nums = Object.keys(sigs).filter(function (k) { return sigs[k].kind === 'num'; });
    if (!nums.length) return null;
    var thead = h('tr', {}, [['신호', ''], ['개수', ''], ['평균', ''], ['표준편차', ''],
                             ['최소', ''], ['최대', ''],
                             ['변화폭 p95', '이웃한 두 프레임 사이 변화량의 95 백분위']]
      .map(function (t) { return h('th', { text: t[0], title: t[1] || null }); }));
    var body = h('tbody', {}, nums.map(function (k) {
      var s = sigs[k];
      return h('tr', {}, [
        h('td', { class: 'mono', text: k }),
        h('td', { class: 'mono num', text: String(s.n) }),
        h('td', { class: 'mono num', text: f(s.mean) }),
        h('td', { class: 'mono num', text: f(s.std) }),
        h('td', { class: 'mono num', text: f(s.min) }),
        h('td', { class: 'mono num', text: f(s.max) }),
        h('td', { class: 'mono num', text: f(s.p95_abs_diff) }),
      ]);
    }));
    return h('div', { class: 'tbl' }, [h('table', {}, [h('thead', {}, [thead]), body])]);
  }

  function renderDrift(drift) {
    if (!drift) return null;
    var notable = drift.filter(function (d) { return d.status !== 'ok'; });
    var okN = drift.length - notable.length;
    var box = h('div', {}, [h('p', { class: 'sub',
      text: '신호 ' + drift.length + '개 중 ' + okN
            + '개가 계약에 처음 적어 둔 경로에서 그대로 잡혔습니다.' })]);
    if (!notable.length) return box;
    var label = { drift: ['경로 다름', 'r'], fallback: ['대체 경로', 'y'],
                  silent: ['수신 없음', 'n'] };
    var thead = h('tr', {}, [['신호', ''], ['상태', ''],
                             ['계약에 적은 경로', ''], ['실제로 맞은 경로', ''],
                             ['조회', '이 신호를 찾아본 횟수'],
                             ['못 찾음', '찾아봤지만 값이 없던 횟수']]
      .map(function (t) { return h('th', { text: t[0], title: t[1] || null }); }));
    var body = h('tbody', {}, notable.map(function (d) {
      var L = label[d.status] || ['?', 'n'];
      var txt = (d.status === 'drift' && d.optional) ? '선택 신호' : L[0];
      return h('tr', {}, [
        h('td', { class: 'mono', text: d.signal }),
        h('td', {}, [badge(txt, d.optional ? 'n' : L[1])]),
        h('td', { class: 'mono mut', text: (d.declared || []).join(', ') }),
        h('td', { class: 'mono', text: d.matched || '—' }),
        h('td', { class: 'mono num mut', text: String(d.tries) }),
        h('td', { class: 'mono num mut', text: String(d.misses) }),
      ]);
    }));
    box.appendChild(h('div', { class: 'tbl' }, [
      h('table', {}, [h('thead', {}, [thead]), body])]));
    return box;
  }

  /* 디버그 영상 패널 — 플롯 커서와 frame 번호로 묶인다.
   * mp4 의 0번이 영상의 몇 번 프레임인지는 뷰어가 debug_meta.json 에 남겨 둔다.
   * 그게 없으면 meta.start 로 근사하고, 사용자가 ±로 미세조정할 수 있게 한다. */
  /* <video> 가 소리 없이 검은 화면으로 있는 것이 제일 나쁘다 — 코덱 때문에
     못 여는 것인지, 파일이 없는 것인지 사람이 알 수 있어야 한다.
     서버는 재생 불가 코덱일 때 415 와 이유(JSON)를 준다. */
  function videoDiag(vid, url) {
    var note = h('div', { class: 'mut', style: 'font-size:12px;margin-top:6px;color:#c2410c' });
    vid.addEventListener('error', function () {
      note.textContent = '영상을 재생할 수 없습니다 — 원인을 확인하는 중…';
      fetch(url, { headers: { Range: 'bytes=0-1' } }).then(function (r) {
        if (r.status === 415) {
          return r.json().then(function (j) {
            note.textContent = '⚠ ' + (j.error || '재생할 수 없는 코덱입니다') +
                               ' (' + (j.file || '') + ' · ' + (j.codec || '?') + ')';
          });
        }
        if (r.status === 404) { note.textContent = '⚠ 영상 파일이 없습니다.'; return null; }
        note.textContent = '⚠ 영상을 열지 못했습니다 (HTTP ' + r.status + ').';
        return null;
      }).catch(function () { note.textContent = '⚠ 서버에 연결하지 못했습니다.'; });
    });
    return note;
  }

  function makeVideoPanel(id, align, metaStart) {
    var first = (align && align.first_frame >= 0) ? align.first_frame
                : (metaStart != null ? metaStart : 0);
    var fps = (align && align.fps) ? align.fps : 15;
    var offset = 0;

    var vurl = '/api/runs/' + encodeURIComponent(id) + '/video';
    var vid = h('video', { src: vurl,
                           preload: 'metadata', controls: 'controls',
                           style: 'width:100%;border-radius:5px;background:#000' });
    var vnote = videoDiag(vid, vurl);
    var label = h('span', { class: 'mut' });
    var exact = h('span', { class: 'mut' });

    function setFrame(fr) {
      if (fr == null) { label.textContent = ''; return; }
      var idx = fr - first + offset;
      var t = Math.max(0, idx / fps);
      if (isFinite(t)) { try { vid.currentTime = t; } catch (e) { /* 로딩 전 */ } }
      label.textContent = 'frame ' + fr + '  →  영상 ' + idx + '번째 ('
                         + t.toFixed(2) + '초)';
    }
    function bump(d) {
      offset += d;
      exact.textContent = '맞춤 ' + (offset >= 0 ? '+' : '') + offset + '프레임';
    }
    bump(0);

    var panel = h('div', { class: 'vidwrap' }, [
      h('div', { class: 'vidbar' }, [
        h('span', { class: 'vt', text: '디버그 영상' }),
        label, h('span', { class: 'spacer' }),
        exact,
        h('button', { text: '−1', title: '영상을 1프레임 앞으로 당깁니다',
                      onclick: function () { bump(-1); } }),
        h('button', { text: '+1', title: '영상을 1프레임 뒤로 밉니다',
                      onclick: function () { bump(1); } }),
      ]),
      vid,
      vnote,
      h('div', { class: 'mut', style: 'font-size:11.5px;margin-top:7px',
        text: '그래프의 한 점을 누르면 영상이 그 프레임으로 갑니다. 어긋나면 ± 로 맞추세요.' }),
    ]);
    panel.setFrame = setFrame;
    return panel;
  }

  /* 경로 오버레이 영상 — 정지 이미지 한 장이 아니라 ★영상★으로 본다.
   * mp4 는 frame 순서대로 담기고 progress.json 에 frames 목록이 남으므로
   * 재생 시간 ↔ frame 번호를 정확히 오갈 수 있다. */
  function makePathPanel(id, onFrame) {
    var wrap = h('div', { class: 'vidwrap' });
    var body = h('div', {});
    wrap.appendChild(h('div', { class: 'vidbar' }, [
      h('span', { class: 'vt', text: '경로 영상' }),
      h('span', { class: 'mut', id: 'pv-st' }),
    ]));
    wrap.appendChild(body);
    var st = { frames: [], fps: 10, vid: null, syncing: false };

    function toFrame(t) {
      var i = Math.round(t * st.fps);
      i = Math.max(0, Math.min(st.frames.length - 1, i));
      return st.frames[i];
    }
    function toTime(fr) {
      var i = st.frames.indexOf(fr);
      if (i < 0) {                       // 가장 가까운 것
        i = 0;
        for (var k = 0; k < st.frames.length; k++) {
          if (Math.abs(st.frames[k] - fr) < Math.abs(st.frames[i] - fr)) i = k;
        }
      }
      return i / st.fps;
    }

    function build(meta) {
      clear(body);
      var purl = '/api/runs/' + encodeURIComponent(id) + '/pathvideo';
      var vid = h('video', {
        src: purl, controls: 'controls', preload: 'metadata',
        style: 'width:100%;border-radius:5px;background:#000' });
      var pnote = videoDiag(vid, purl);
      st.vid = vid;
      st.frames = meta.frames || [];
      st.fps = meta.fps || 10;
      vid.addEventListener('timeupdate', function () {
        if (st.syncing) return;
        var fr = toFrame(vid.currentTime);
        if (fr != null && onFrame) onFrame(fr, true);
      });
      body.appendChild(vid);
      body.appendChild(pnote);
      body.appendChild(h('div', { class: 'mut', style: 'font-size:11.5px;margin-top:7px',
        text: '재생하면 아래 그래프의 커서가 따라가고, 그래프를 누르면 영상이 그 프레임으로 갑니다. '
              + '왼쪽 차선 파랑 · 오른쪽 차선 빨강 · 중심선(주행 경로) 주황 · '
              + 'θ 를 잰 두 점을 이은 선 노랑 · 접선 회색' }));
      // 배속은 ★다시 굽지 않고★ playbackRate 로 바꾼다 — 즉시 반영되고
      // 프레임↔시간 대응(toFrame/toTime)도 그대로 유지된다.
      var rates = [0.25, 0.5, 1, 2];
      var rbtns = rates.map(function (r) {
        return h('button', { text: r + '×', class: r === 1 ? 'primary' : '',
          onclick: function () {
            vid.playbackRate = r;
            rbtns.forEach(function (b2, i) { b2.className = rates[i] === r ? 'primary' : ''; });
          } });
      });
      var b = h('div', { class: 'framebar' }, [
        h('span', { class: 'mut', text: '배속' }),
      ].concat(rbtns).concat([
        h('span', { class: 'spacer' }),
        h('span', { class: 'mut mono',
          text: (meta.fps ? meta.fps.toFixed(1) + 'fps · ' : '') +
                (st.frames.length ? st.frames.length + '프레임' : '') }),
        h('button', { text: '다시 만들기', onclick: make }),
      ]));
      body.appendChild(b);
      body.appendChild(rdOptions());
    }

    /* 만들 때 쓰는 인자 — 기본값은 「전체 프레임을 원본 속도로」다.
       CLI 의 `tb.run render` 가 받는 나머지는 아래 «영상 옵션» 에서 바꾼다. */
    var rdLimit = h('input', { type: 'number', value: '0', style: 'width:90px' });
    var rdWidth = h('input', { type: 'number', value: '1400', style: 'width:90px' });
    var rdFps = h('input', { type: 'number', step: 'any', placeholder: '원본과 같게',
                             style: 'width:110px' });
    var rdWhere = h('input', { type: 'text', placeholder: '(전부)', size: '24' });
    var rdFrames = h('input', { type: 'text', placeholder: '예: 1090,850', size: '18' });
    function rdArgs() {
      // --fps 를 주지 않으면 엔진이 ★원본 영상과 같은 속도★로 맞춘다.
      // 예전에는 10 을 박아 30fps 영상이 1/3 배속으로 나왔다.
      var a = [id, '--mp4', 'auto',
               '--limit', String(Number(rdLimit.value) || 0),
               '--width', String(Number(rdWidth.value) || 1400)];
      if (rdFps.value && Number(rdFps.value)) a.push('--fps', rdFps.value);
      if (rdWhere.value.trim()) a.push('--where', rdWhere.value.trim());
      if (rdFrames.value.trim()) a.push('--frames', rdFrames.value.trim());
      return a;
    }
    function rdOptions() {
      var det = h('details', { class: 'reg' });
      det.appendChild(h('summary', { text: '영상 옵션' }));
      det.appendChild(h('div', { class: 'regbody toolform' }, [
        h('div', { class: 'toolopt' }, [h('label', { class: 'mono', text: '--limit' }), rdLimit,
          h('span', { class: 'mut', text: '몇 장까지 (0=전부)' })]),
        h('div', { class: 'toolopt' }, [h('label', { class: 'mono', text: '--width' }), rdWidth,
          h('span', { class: 'mut', text: '가로 픽셀' })]),
        h('div', { class: 'toolopt' }, [h('label', { class: 'mono', text: '--fps' }), rdFps,
          h('span', { class: 'mut', text: '재생 속도 (비우면 원본과 같게)' })]),
        h('div', { class: 'toolopt' }, [h('label', { class: 'mono', text: '--where' }), rdWhere,
          h('span', { class: 'mut', text: '조건에 맞는 프레임만' })]),
        h('div', { class: 'toolopt' }, [h('label', { class: 'mono', text: '--frames' }), rdFrames,
          h('span', { class: 'mut', text: '프레임 번호를 쉼표로' })]),
      ]));
      return det;
    }

    function make() {
      clear(body);
      body.appendChild(spinner('경로 영상을 만드는 중… (400프레임 약 30초)'));
      postJob('render', rdArgs());
      var t = setInterval(function () {
        get('/api/runs/' + encodeURIComponent(id) + '/pathmeta').then(function (m) {
          var el = body.querySelector('.spin span');
          if (m.total && !m.finished && el) {
            el.textContent = '만드는 중 ' + m.done + '/' + m.total +
                             '  (약 ' + Math.round(m.eta_s || 0) + '초 남음)';
          }
          if (m.finished && m.exists) { clearInterval(t); build(m); }
        }).catch(function () {});
      }, 1200);
    }

    get('/api/runs/' + encodeURIComponent(id) + '/pathmeta').then(function (m) {
      if (m.exists && m.finished) { build(m); return; }
      clear(body);
      body.appendChild(h('p', { class: 'sub',
        text: '아직 경로 영상이 없습니다. 만들면 좌·우 차선과 중심선, θ 가 '
              + '그려진 영상을 재생할 수 있습니다.' }));
      body.appendChild(h('button', { class: 'primary', text: '경로 영상 만들기', onclick: make }));
      body.appendChild(rdOptions());
    }).catch(function () {});

    wrap.seekFrame = function (fr) {
      if (!st.vid || !st.frames.length) return;
      st.syncing = true;
      try { st.vid.currentTime = toTime(fr); } catch (e) { /* 로딩 전 */ }
      setTimeout(function () { st.syncing = false; }, 250);
    };
    return wrap;
  }

  function renderPlot(id, rows, videoPanel, ref, pathPanel) {
    if (!rows || !rows.length) return null;
    var numeric = [];
    var sample = rows[Math.floor(rows.length / 2)] || rows[0];
    Object.keys(sample).forEach(function (k) {
      if (k === 'frame') return;
      if (typeof sample[k] === 'number') numeric.push(k);
    });
    var prefer = ['theta_deg', 'cte_rear_m', 'conf_eff', 'lane_width_m', 'flags'];
    var chosen = prefer.filter(function (k) { return numeric.indexOf(k) >= 0; }).slice(0, 3);
    if (!chosen.length) chosen = numeric.slice(0, 2);

    var canvas = h('canvas');
    var readout = h('div', { class: 'readout' });
    var bar = h('div', { class: 'plotbar' });
    var wrap = h('div', { class: 'plotwrap' }, [bar, canvas, readout]);

    var plot = new window.TimePlot(canvas, { height: 260 });
    if (ref) ref.plot = plot;
    var pal = window.plotPalette();

    function refresh() {
      plot.setData(rows, chosen);
      var full = chosen.length >= 6;
      Array.prototype.forEach.call(bar.children, function (b) {
        var k = b.getAttribute('data-k');
        var i = chosen.indexOf(k);
        if (i >= 0) { b.classList.add('on'); b.style.background = pal[i % pal.length]; }
        else { b.classList.remove('on'); b.style.background = ''; }
        // 6개가 차면 나머지는 눌러도 무시됐다 — 안 눌리는 게 보여야 한다
        b.disabled = full && i < 0;
        b.title = b.disabled ? '한 번에 6개까지 — 켜진 것을 하나 끄고 고르세요' : '';
      });
    }
    numeric.forEach(function (k) {
      bar.appendChild(h('button', { class: 'sigbtn', 'data-k': k, text: k,
        onclick: function () {
          var i = chosen.indexOf(k);
          if (i >= 0) chosen.splice(i, 1);
          else if (chosen.length < 6) chosen.push(k);
          refresh();
        } }));
    });
    plot.onCursor = function (fr) {
      clear(readout);
      if (fr == null) return;
      var row = plot.rowAtFrame(fr);
      if (!row) return;
      readout.appendChild(h('span', {}, [h('b', { text: 'frame ' + row.frame })]));
      chosen.forEach(function (k) {
        readout.appendChild(h('span', { text: k + ' ' }, [h('b', { text: f(row[k]) })]));
      });
      if (videoPanel && videoPanel.setFrame) videoPanel.setFrame(row.frame);
      if (pathPanel && pathPanel.seekFrame) pathPanel.seekFrame(row.frame);
    };
    if (ref) {
      ref.readout = function (fr) {                 // 영상 재생 → 판독값 갱신
        var row = plot.rowAtFrame(fr);
        if (!row) return;
        clear(readout);
        readout.appendChild(h('span', {}, [h('b', { text: 'frame ' + row.frame })]));
        chosen.forEach(function (k) {
          readout.appendChild(h('span', { text: k + ' ' }, [h('b', { text: f(row[k]) })]));
        });
      };
    }
    refresh();
    setTimeout(refresh, 30);
    return wrap;
  }

  /* ── 실행 상세 : 탭 5개 ────────────────────────────────────────────
   * 섹션 10개를 한 페이지에 쌓으면 스크롤이 4000px 넘는다.
   * 들어가자마자 판정과 게이트 통과율이 보이고, 무거운 것(영상)은
   * 그 탭을 열 때만 로드한다. 탭은 URL 에 남아 새로고침해도 유지된다. */
  function renderRun(id, data, signals, tab) {
    var s = data.summary || {};
    var m = s.meta || {};
    tab = tab || 'summary';
    clear(view);

    view.appendChild(h('a', { class: 'back', href: '#/runs', text: '‹ 실행 기록' }));
    var checks = data.checks || [];
    view.appendChild(h('div', { class: 'rowhd' }, [
      h('h1', { text: id }),
      checkBadge({ checks_ok: checks.filter(function (c) { return c.ok === true; }).length,
                   checks_total: checks.length,
                   checks_bad: checks.filter(function (c) { return c.ok === false; }).length }),
    ]));
    view.appendChild(h('p', { class: 'sub',
      text: [m.scenario, m.variant, m.mode,
             m.perturb && m.perturb !== 'none' ? '섭동 ' + m.perturb : null,
             (m.video || '').split('/').pop(), shortTime(m.when)]
        .filter(Boolean).join(' · ') }));

    var TABS = [['summary', '요약'], ['visual', '시각화'],
                ['detail', '상세'], ['raw', '리포트'], ['feedback', '피드백']]
      .filter(function (t) { return !(STATIC && SERVERONLY[t[0]]); });
    view.appendChild(h('div', { class: 'tabs' }, TABS.map(function (t) {
      return h('a', { class: 'tab' + (t[0] === tab ? ' on' : ''),
        href: '#/run/' + encodeURIComponent(id) + '/' + t[0], text: t[1] });
    })));

    var pane = h('div', {});
    view.appendChild(pane);

    if (tab === 'summary') {
      var cards = [
        ['결과 행', s.rows,
         '넣은 프레임 ' + (s.frames_pushed == null ? '?' : s.frames_pushed) + '개 중'],
      ];
      /* '차선 인식률' 은 플래그를 선언한 계약에서만 뜻이 있다 — 정지선 계약에는
         차선이 없어서 늘 100% 로 보이고, 그게 더 헷갈린다. */
      if (s.flag_rate) {
        cards.push(['차선 인식률', s.valid_rate == null ? '—' : pct(s.valid_rate),
                    '차선을 본 프레임의 비율']);
      }
      cards.push(
        ['유실률', s.drop_rate == null ? '—' : pct(s.drop_rate, 2),
         '넣었는데 결과가 안 나온 비율 (lockstep 이면 0)'],
        ['지연 p95', s.latency_p95_ms == null ? '—' : s.latency_p95_ms.toFixed(0) + ' ms',
         '400ms 를 넘으면 받는 쪽이 그 값을 버립니다']);
      pane.appendChild(h('div', { class: 'cards' }, cards.map(function (c) {
        return h('div', { class: 'card' }, [
          h('div', { class: 'k', text: c[0] }),
          h('div', { class: 'v', text: c[1] == null ? '—' : String(c[1]) }),
          h('div', { class: 'd', text: c[2] }),
        ]);
      })));
      var g = renderGates(s.funnel);
      if (g) { pane.appendChild(sectionTitle('게이트 통과율')); pane.appendChild(g); }
      var th = renderTheta(s.theta_quality);
      if (th) {
        pane.appendChild(sectionTitle('θ 품질 — 받는 쪽이 실제로 쓰는 유일한 값'));
        pane.appendChild(th);
      }
      var ch = renderChecks(checks);
      if (ch) { pane.appendChild(sectionTitle('불변식 체크')); pane.appendChild(ch); }
      var evp = renderEvents(s.events);
      if (evp) pane.appendChild(evp);          // 제목을 자기가 붙인다
      var lg = renderLogEvents(s.log_events);
      if (lg) {
        pane.appendChild(sectionTitle('노드 로그 — 토픽에 없는 근거'));
        pane.appendChild(lg);
      }

      pane.appendChild(sectionTitle('이어서 할 일'));
      pane.appendChild(h('div', { class: 'framebar' }, [
        STATIC ? null
          : h('a', { class: 'gobtn', href: '#/run/' + encodeURIComponent(id) + '/frames',
                     text: '프레임 탐색 ›' }),
        h('button', { text: '기준으로 등록', onclick: function () {
          var nm = prompt('기준 이름 — 앞으로의 실행을 이 결과와 비교합니다',
                          (m.scenario || 'regression'));
          if (nm) postJob('baseline', [id, '--name', nm, '--force']);
        } }),
        h('button', { text: '재분석', title: '계약을 고친 뒤 raw.jsonl 로 다시 분석합니다',
                      onclick: function () { postJob('reanalyze', [id]); } }),
      ]));
      pane.appendChild(cli('python3 -m tb.run reanalyze ' + id));

    } else if (tab === 'visual') {
      var plotRef = { plot: null };
      var pathPanel = makePathPanel(id, function (fr) {
        if (plotRef.plot) plotRef.plot.setCursorFrame(fr, false);
        if (plotRef.readout) plotRef.readout(fr);
      });
      pane.appendChild(sectionTitle('경로 영상 — 판정에 쓴 값으로 다시 그린 화면'));
      pane.appendChild(pathPanel);

      var vpanel = (data.video ? makeVideoPanel(id, data.video.align, m.start) : null);
      var pl = renderPlot(id, signals, vpanel, plotRef, pathPanel);
      if (pl) {
        pane.appendChild(sectionTitle('신호 그래프'));
        var grid = h('div', { class: 'plotgrid' });
        grid.appendChild(pl);
        if (vpanel) grid.appendChild(vpanel);
        pane.appendChild(grid);
      }

    } else if (tab === 'feedback') {
      renderFeedbackPane(pane, id);

    } else if (tab === 'detail') {
      var fl = renderFlags(s.flag_rate);
      if (fl) { pane.appendChild(sectionTitle('플래그 발생률')); pane.appendChild(fl); }
      var stt = renderSignalStats(s.signals);
      if (stt) { pane.appendChild(sectionTitle('신호 통계')); pane.appendChild(stt); }
      var dr = renderDrift(data.drift);
      if (dr) { pane.appendChild(sectionTitle('신호 경로 점검')); pane.appendChild(dr); }

    } else {
      var box = h('div', {});
      pane.appendChild(box);
      box.appendChild(spinner());
      get('/api/runs/' + encodeURIComponent(id) + '/compare').then(function (md) {
        clear(box);
        box.appendChild(sectionTitle('기준과 비교'));
        var v = /판정: (\w+)/.exec(md);
        if (v) box.appendChild(h('p', { class: 'sub' }, [badge(v[1], v[1] === 'PASS' ? 'g' : 'r')]));
        box.appendChild(h('div', { class: 'md', text: md }));
        loadReport();
      }).catch(function () { clear(box); loadReport(); });
      function loadReport() {
        pane.appendChild(sectionTitle('리포트 원문'));
        var rp = h('div', { class: 'md' });
        rp.appendChild(spinner());
        pane.appendChild(rp);
        get('/api/runs/' + encodeURIComponent(id) + '/report').then(function (md) {
          rp.textContent = md;
        }).catch(function () { rp.textContent = '(리포트가 없습니다)'; });
      }
    }
    footEl.textContent = id;
  }

  // ── 피드백 — 결과를 코드 개선 요청문으로 ─────────────────────────
  //   문서를 만드는 것은 엔진(tb.feedback)이다. 여기서는 옵션을 넘기고
  //   만들어진 것을 보여 주고 복사시킬 뿐이다 — 판정을 다시 하지 않는다.
  function copyText(txt, btn) {
    var label = btn.textContent;
    function done() {
      btn.textContent = '복사됨';
      setTimeout(function () { btn.textContent = label; }, 1600);
    }
    function manual() {
      var ta = document.createElement('textarea');
      ta.value = txt;
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
        done();
      } catch (e) {
        alert('복사하지 못했습니다. 아래 문서를 직접 선택해 복사하세요.');
      }
      document.body.removeChild(ta);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(done, manual);
    } else {
      manual();
    }
  }

  function renderFeedbackPane(pane, id) {
    pane.appendChild(h('p', { class: 'sub',
      text: '이 결과에서 잘된 점과 안 좋은 점을 정리해, 클로드 코드에 그대로 '
            + '붙여넣을 수 있는 문서로 만듭니다. 값과 판정은 엔진이 만든 '
            + 'summary.json 을 그대로 옮깁니다 — 여기서 다시 판정하지 않습니다.' }));

    var vsSel = h('select', {},
      [h('option', { value: '', text: '(이전 실행과 비교하지 않음)' })]);
    get('/api/runs').then(function (d) {
      (d.runs || []).filter(function (r) { return r.has_summary && r.id !== id; })
        .slice(0, 50)
        .forEach(function (r) {
          vsSel.appendChild(h('option', { value: r.id,
            text: r.id + ' · ' + (r.scenario || '') + ' ' +
                  (r.checks_ok || 0) + '/' + (r.checks_total || 0) }));
        });
    }).catch(function () {});

    var note = h('textarea', { class: 'note', rows: '3',
      placeholder: '사람이 본 것 (선택) — 예: 좌회전 구간에서 오른쪽 차선을 자주 놓친다' });
    var out = h('pre', { class: 'md fb', text: '' });
    var cmdLine = h('div', { class: 'cli' });
    /* 문서가 아직 없을 때도 눌리던 버튼 — 안내문("아직 만들지 않았습니다")까지
       복사해 놓고 «복사됨» 이라고 했다. 진짜 문서가 있을 때만 눌리게 한다. */
    var copyBtn = h('button', { text: '전체 복사', disabled: 'disabled',
      title: '먼저 «피드백 만들기» 를 누르세요',
      onclick: function () { copyText(out.textContent, this); } });
    function setMd(md) {
      out.textContent = md;
      copyBtn.disabled = !md;
      copyBtn.title = md ? '' : '먼저 «피드백 만들기» 를 누르세요';
    }
    var makeBtn = h('button', { class: 'primary', text: '피드백 만들기',
                                onclick: function () { make(); } });

    pane.appendChild(sectionTitle('만들기'));
    pane.appendChild(h('div', { class: 'fbbar' }, [
      h('label', { text: '이전 실행과 비교' }), vsSel, makeBtn, copyBtn,
    ]));
    pane.appendChild(note);
    pane.appendChild(h('p', { class: 'sub',
      text: '만들어진 문서는 runs/' + id + '/feedback.md 에도 저장됩니다. '
            + '터미널에 아래를 붙여넣으면 클로드 코드가 그대로 읽습니다.' }));
    pane.appendChild(cmdLine);
    pane.appendChild(sectionTitle('문서'));
    pane.appendChild(out);

    function setCmd() {
      cmdLine.textContent = '$ cd ' + (ROOTDIR || '.') +
        ' && claude "$(cat runs/' + id + '/feedback.md)"';
    }
    function make() {
      makeBtn.disabled = true;
      out.textContent = '만드는 중…';
      postJSON('/api/feedback',
               { run: id, vs: vsSel.value, note: note.value })
        .then(function (j) {
          makeBtn.disabled = false;
          if (j.error) { out.textContent = '오류: ' + j.error; return; }
          setMd(j.md);
          setCmd();
          hintEl.textContent = 'feedback.md 저장됨';
          setTimeout(function () { hintEl.textContent = ''; }, 3000);
        })
        .catch(function (e) {
          makeBtn.disabled = false;
          out.textContent = '오류: ' + e.message;
        });
    }

    setCmd();
    get('/api/runs/' + encodeURIComponent(id) + '/feedback')
      .then(function (md) { setMd(md); })
      .catch(function () {
        out.textContent = '(아직 만들지 않았습니다 — 위의 «피드백 만들기» 를 누르세요.)';
      });
  }

  function renderInject(id, cases) {
    clear(view);
    view.appendChild(h('a', { class: 'back', href: '#/runs', text: '‹ 실행 기록' }));
    var okN = cases.filter(function (c) { return c.ok; }).length;
    view.appendChild(h('div', { class: 'rowhd' }, [
      h('h1', { text: '주입 검증' }),
      badge(okN + '/' + cases.length, okN === cases.length ? 'g' : 'r'),
    ]));
    view.appendChild(h('p', { class: 'sub',
      text: '영상과 YOLO 없이 좌표 변환 계산만 확인합니다. 입력을 직접 만들었기 때문에 '
            + '정답을 정확히 알고 있고, 여기서 틀리면 인지가 아니라 계산이 틀린 것입니다.' }));
    var thead = h('tr', {}, ['케이스', '항목', '기댓값', '실제값', '오차', '허용 오차', '']
      .map(function (t) { return h('th', { text: t }); }));
    var trs = [];
    cases.forEach(function (c) {
      (c.checks || []).forEach(function (k, i) {
        trs.push(h('tr', {}, [
          h('td', { class: 'mono', text: i === 0 ? c.name : '' }),
          h('td', { class: 'mono mut', text: k.key }),
          h('td', { class: 'mono num', text: f(k.want) }),
          h('td', { class: 'mono num', text: k.have == null ? '—' : f(k.have) }),
          h('td', { class: 'mono num', text: k.have == null ? '—' : f(Math.abs(k.have - k.want)) }),
          h('td', { class: 'mono num mut', text: f(k.tol) }),
          h('td', {}, [badge(k.ok ? '통과' : '실패', k.ok ? 'g' : 'r')]),
        ]));
      });
    });
    view.appendChild(h('div', { class: 'tbl' }, [
      h('table', {}, [h('thead', {}, [thead]), h('tbody', {}, trs)])]));
    var notes = h('div', { style: 'margin-top:16px' });
    cases.forEach(function (c) {
      if (c.desc) {
        notes.appendChild(h('p', { class: 'sub', style: 'margin:0 0 8px',
          html: '<b>' + c.name + '</b> — ' + c.desc }));
      }
    });
    view.appendChild(sectionTitle('각 케이스가 무엇을 보는가'));
    view.appendChild(notes);
    view.appendChild(cli('python3 -m tb.run inject --scenario scenarios/regression.yaml'));
  }

  /* ── 프레임 탐색 : 능동 학습의 입구 ─────────────────────────────
   * 조건으로 걸러 ★표★로 본다. 그림은 프레임 뷰어가 담당한다 —
   * 썸네일 그리드는 한 장씩 영상을 seek 해야 해서 느리고, 원본+BEV 를
   * 작게 욱여넣으면 정작 봐야 할 곡선이 안 보인다. */
  /* 프레임 탐색의 프리셋·표의 열·플래그 이름은 ★그 런의 계약★ 이 정한다
     (`frame_presets:` `frame_columns:` `flag_bits:`). 계약이 아무것도 안 적었을
     때만 아래 폴백을 쓴다 — 예전에는 이게 유일한 값이라 다른 워크스페이스를
     붙이면 표가 통째로 '—' 가 됐다. */
  var PRESETS_FALLBACK = [
    { label: '전부', where: '' },
    { label: '플래그 있음', where: 'int(flags) != 0', default: true },
  ];
  var FLAGBITS_FALLBACK = [[1, 'NO_LANE'], [2, 'WIDTH_BAD'], [4, 'CTE_JUMP'],
                           [8, 'CONF_LOW'], [16, 'SINGLE']];

  function flagNames(v, bits) {
    if (typeof v !== 'number') return '—';
    var bb = (bits && bits.length) ? bits : FLAGBITS_FALLBACK;
    var iv = Math.round(v);
    if (iv === 0) return 'CLEAN';
    return bb.filter(function (b) { return iv & b[0]; })
             .map(function (b) { return b[1]; }).join(' ') || String(iv);
  }

  /* 필터 결과를 기억해 프레임 뷰어의 ←→ 가 ★이 목록 안에서만★ 움직이게 한다.
     ±1 프레임으로 넘기면 방금 거른 조건 밖으로 새어 나간다. */
  var FRAMESET = { runId: null, where: '', frames: [] };

  function renderFrames(id, state, ui) {
    ui = ui || {};
    var PRESETS = (ui.frame_presets && ui.frame_presets.length)
      ? ui.frame_presets : PRESETS_FALLBACK;
    clear(view);
    view.appendChild(h('a', { class: 'back', href: '#/run/' + encodeURIComponent(id),
                              text: '‹ ' + id }));
    view.appendChild(h('h1', { text: '프레임 탐색' }));
    view.appendChild(h('p', { class: 'sub',
      text: '조건을 걸어 프레임을 추립니다. 게이트에서 가장 많이 걸러지는 단계를 그대로 '
            + '조건으로 쓰면 고쳐야 할 프레임만 모입니다. 행을 누르면 그 프레임의 경로를 크게 봅니다.' }));

    var input = h('input', { type: 'text', value: state.where, class: 'wherebox',
      placeholder: '조건식 — 예: int(flags) % 4 >= 2' });
    var limitSel = h('select', {}, [50, 120, 300, 1000].map(function (n) {
      return h('option', { value: String(n), selected: n === state.limit ? 'selected' : null,
                           text: n + '행' });
    }));
    function go() {
      state.where = input.value.trim();
      state.limit = parseInt(limitSel.value, 10);
      load();
    }
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') go(); });

    var presets = h('div', { class: 'plotbar' }, PRESETS.map(function (p2) {
      var on = (p2.where || '') === state.where;
      return h('button', { class: 'sigbtn' + (on ? ' on' : ''),
        text: p2.label, style: on ? 'background:var(--accent)' : '',
        onclick: function () { input.value = p2.where || ''; go(); } });
    }));

    view.appendChild(h('div', { class: 'framebar' }, [
      input, limitSel, h('button', { text: '적용', onclick: go }),
      h('span', { class: 'spacer' }),
      h('button', { text: '이 조건으로 추출',
        title: '조건에 맞는 원본 프레임을 이미지로 저장합니다 (라벨링용)',
        onclick: function () { postJob('harvest', hvArgs()); } }),
    ]));
    view.appendChild(presets);

    /* 추출 세부 옵션 — CLI 의 `tb.run harvest` 가 받는 나머지. */
    var hvOut = h('input', { type: 'text', placeholder: '(비우면 <런>/harvest)', size: '26' });
    var hvW = h('input', { type: 'number', placeholder: '0 = 원본', style: 'width:110px' });
    var hvDry = h('input', { type: 'checkbox' });
    function hvArgs() {
      var a = [id, '--where', state.where, '--limit', String(state.limit)];
      if (hvOut.value.trim()) a.push('--out', hvOut.value.trim());
      if (hvW.value && Number(hvW.value)) a.push('--width', hvW.value);
      if (hvDry.checked) a.push('--dry-run');
      return a;
    }
    var hvDet = h('details', { class: 'reg' });
    hvDet.appendChild(h('summary', { text: '추출 옵션' }));
    hvDet.appendChild(h('div', { class: 'regbody toolform' }, [
      h('div', { class: 'toolopt' }, [h('label', { class: 'mono', text: '--out' }), hvOut,
        h('span', { class: 'mut', text: '저장 폴더' })]),
      h('div', { class: 'toolopt' }, [h('label', { class: 'mono', text: '--width' }), hvW,
        h('span', { class: 'mut', text: '가로 축소 (0=원본)' })]),
      h('div', { class: 'toolflags' }, [
        h('label', { class: 'toolflag' }, [hvDry,
          h('span', { class: 'mono', text: '--dry-run' }),
          h('span', { class: 'mut', text: '저장하지 않고 몇 장이 뽑히는지만 세어 본다' })]),
      ]),
    ]));
    view.appendChild(hvDet);

    var info = h('p', { class: 'sub' });
    var box = h('div', {});
    var preview = h('div', {});
    view.appendChild(info);
    view.appendChild(box);
    view.appendChild(preview);
    var cliBox = h('div', {});
    view.appendChild(cliBox);

    function load() {
      clear(box); clear(preview);
      info.textContent = '불러오는 중…';
      var url = '/api/runs/' + encodeURIComponent(id) + '/frames?limit=' + state.limit +
                '&where=' + encodeURIComponent(state.where);
      get(url).then(function (d) {
        var cnt = Object.keys(d.counts || {}).map(function (k) {
          return k + ' ' + d.counts[k];
        }).join(' · ');
        info.textContent = '조건에 맞는 프레임 ' + d.matched + '개 (전체 ' +
          d.total_rows + '개) · 화면에 ' + d.shown + '개' + (cnt ? '  —  ' + cnt : '');

        var frames = (d.frames || []).map(function (x) { return x.frame; });
        FRAMESET = { runId: id, where: state.where, frames: frames };

        clear(box);
        if (!frames.length) {
          box.appendChild(h('div', { class: 'empty', text: '조건에 맞는 프레임이 없습니다.' }));
          return;
        }
        var cols = d.columns || [];
        var head = [h('th', { text: 'frame' })];
        if (d.flag_signal) {
          head.push(h('th', { text: '플래그', title: '이 프레임에서 걸린 조건' }));
        }
        cols.forEach(function (c) { head.push(h('th', { text: c })); });
        head.push(h('th', { text: '' }));
        var thead = h('tr', {}, head);
        var body = h('tbody', {}, (d.frames || []).map(function (fr) {
          var cells = [h('td', { class: 'mono', text: String(fr.frame) })];
          if (d.flag_signal) {
            var fn = flagNames(fr.flags, d.flag_bits);
            cells.push(h('td', {}, [badge(fn, fn === 'CLEAN' ? 'g' : 'r')]));
          }
          cols.forEach(function (c) {
            var v = fr[c];
            cells.push(h('td', { class: 'mono num',
                                 text: (typeof v === 'string') ? v : f(v) }));
          });
          cells.push(h('td', { class: 'mut', text: '›' }));
          return h('tr', { class: 'click', onclick: function () {
            location.hash = '#/run/' + encodeURIComponent(id) + '/frame/' + fr.frame;
          } }, cells);
        }));
        box.appendChild(h('div', { class: 'tbl' }, [
          h('table', {}, [h('thead', {}, [thead]), body])]));

        // 첫 프레임 미리보기 — 표만 보면 감이 안 오니 한 장은 띄워 둔다
        clear(preview);
        preview.appendChild(sectionTitle('미리보기 — frame ' + frames[0]));
        preview.appendChild(h('img', { class: 'bigov',
          src: '/api/runs/' + encodeURIComponent(id) + '/overlay?n=' + frames[0] + '&w=1100' }));

        clear(cliBox);
        cliBox.appendChild(cli('python3 -m tb.run harvest ' + id +
          (state.where ? ' --where "' + state.where + '"' : '') +
          ' --limit ' + state.limit));
      }).catch(function (e) {
        info.textContent = '';
        clear(box);
        box.appendChild(h('div', { class: 'empty', text: '오류: ' + e.message }));
      });
    }
    load();
  }

  /* 프레임 하나를 크게 — 판정에 쓴 값으로 그린 경로를 확인한다 */
  function renderFrameOne(id, frame) {
    clear(view);
    view.appendChild(h('a', { class: 'back',
      href: '#/run/' + encodeURIComponent(id) + '/frames', text: '‹ 프레임 탐색' }));
    view.appendChild(h('h1', { text: 'frame ' + frame }));
    var inSet = (FRAMESET.runId === id && FRAMESET.frames.indexOf(frame) >= 0);
    view.appendChild(h('p', { class: 'sub',
      text: '테스트베드가 판정에 쓴 곡선 계수로 다시 그린 화면입니다 — '
            + '리포트의 숫자가 어디서 나왔는지 여기서 확인합니다.'
            + (inSet ? '  ← → 키는 방금 거른 ' + FRAMESET.frames.length + '개 안에서만 움직입니다.'
                     : '  ← → 키는 바로 옆 프레임으로 움직입니다.') }));

    var img = h('img', { class: 'bigov',
      src: '/api/runs/' + encodeURIComponent(id) + '/overlay?n=' + frame + '&w=1600' });
    var nav = h('div', { class: 'framebar' }, [
      h('button', { text: '‹ 이전', onclick: function () { step(-1); } }),
      h('button', { text: '다음 ›', onclick: function () { step(1); } }),
      h('span', { class: 'spacer' }),
      h('a', { class: 'gobtn', href: '#/run/' + encodeURIComponent(id), text: '실행 상세 ›' }),
    ]);
    function step(d) {
      var list = (FRAMESET.runId === id && FRAMESET.frames.length)
                 ? FRAMESET.frames : null;
      var next;
      if (list) {
        var i = list.indexOf(frame);
        if (i < 0) {                       // 목록 밖이면 가장 가까운 곳으로
          i = 0;
          for (var k = 0; k < list.length; k++) {
            if (Math.abs(list[k] - frame) < Math.abs(list[i] - frame)) i = k;
          }
        }
        next = list[Math.max(0, Math.min(list.length - 1, i + d))];
      } else {
        next = frame + d;
      }
      if (next !== frame) {
        location.hash = '#/run/' + encodeURIComponent(id) + '/frame/' + next;
      }
    }
    view.appendChild(nav);
    view.appendChild(img);
    view.appendChild(cli('python3 -m tb.run render ' + id + ' --frames ' + frame));
    document.onkeydown = function (e) {
      if (e.key === 'ArrowLeft') step(-1);
      if (e.key === 'ArrowRight') step(1);
    };
  }

  /* ── 점검 : doctor · selftest ─────────────────────────────────── */
  /* ── 실행 : 시나리오를 고르고 돌린다 ─────────────────────────────
   * ★이 화면의 핵심은 버튼이 아니라 "무엇이 쓰이는지"를 미리 보여 주는 것이다.★
   * 시나리오 → 계약 → 워크스페이스, 시나리오 → 별칭 → 실제 영상 파일까지
   * 해석은 전부 엔진(tb.config)이 하고 여기서는 그 결과를 표시만 한다. */
  var execTimer = null;                 // 화면을 다시 그려도 폴링이 겹치지 않게
  function renderExec(cfg) {
    clear(view);
    if (execTimer) { clearInterval(execTimer); execTimer = null; }
    var state = { plan: null, lastRun: null };

    view.appendChild(h('h1', { text: '테스트 실행' }));
    view.appendChild(h('p', { class: 'sub',
      text: '시나리오를 고르면 그게 실제로 어느 워크스페이스와 어느 영상을 쓰는지 먼저 보여 줍니다.' }));

    // ── 1) 시나리오 고르기 ──────────────────────────────────────
    var scenarios = (cfg.scenarios || []).map(function (s) { return s.file; });
    var scSel = h('select', {}, scenarios.map(function (f2) {
      return h('option', { value: f2, text: f2 });
    }));
    if (cfg.suggest && scenarios.indexOf(cfg.suggest) >= 0) scSel.value = cfg.suggest;
    var tagIn = h('input', { type: 'text', placeholder: '태그 (선택)', size: '12' });
    tagIn.addEventListener('input', function () { syncCmd(); });
    var recCb = h('input', { type: 'checkbox' });
    recCb.addEventListener('change', function () { syncCmd(); });

    /* 고급 옵션 — CLI 의 `tb.run run` 이 받는 나머지 인자들.
       평소에는 접혀 있다. 여기 없는 명령은 «도구» 탭에 전부 있다. */
    var advCont = h('select', {}, [h('option', { value: '', text: '(시나리오가 정한 것)' })]
      .concat((cfg.contracts || []).map(function (c) {
        return h('option', { value: c.file, text: c.file }); })));
    var advVar = h('input', { type: 'text', placeholder: '전부', size: '18' });
    var advBase = h('select', {}, [h('option', { value: '', text: '(비교 안 함)' })]
      .concat((cfg.baselines || []).map(function (b) {
        return h('option', { value: b, text: b }); })));
    var advDom = h('input', { type: 'number', placeholder: '0', style: 'width:80px' });
    var advKeep = h('input', { type: 'checkbox' });
    var advWatch = h('input', { type: 'checkbox' });
    var planBox = h('div', {});
    var advBox = h('div', {});
    var cmdPrev = h('div', {});
    var runBar = h('div', { class: 'framebar' });
    var liveBox = h('div', {});
    var parBox = h('div', {});

    view.appendChild(h('div', { class: 'framebar' }, [
      h('span', { class: 'mut', text: '시나리오' }), scSel,
      h('span', { class: 'spacer' }),
      h('span', { class: 'mut', text: '태그' }), tagIn,
      h('label', { class: 'mut', style: 'display:flex;gap:5px;align-items:center' },
        [recCb, h('span', { text: '디버그 영상 기록' })]),
    ]));
    view.appendChild(planBox);
    view.appendChild(parBox);
    view.appendChild(advBox);
    view.appendChild(runBar);
    view.appendChild(cmdPrev);
    view.appendChild(liveBox);

    // ── 2) 해석 결과 — 무엇이 쓰이는가 ──────────────────────────
    function row(k, v, cls) {
      return h('tr', {}, [
        h('td', { class: 'mut', style: 'white-space:nowrap', text: k }),
        h('td', { class: cls || '' }, [typeof v === 'string' ? document.createTextNode(v) : v]),
      ]);
    }
    function mark(ok, txt) {
      return h('span', {}, [
        h('b', { class: ok ? 'ok' : 'no', text: ok ? '✅ ' : '⛔ ' }),
        h('span', { class: 'mono', text: txt })]);
    }

    function drawPlan(p) {
      state.plan = p;
      clear(planBox);
      if (p.error) {
        planBox.appendChild(h('div', { class: 'empty', text: p.error }));
        clear(parBox);                 // 앞 시나리오의 파라미터가 남으면 안 된다
        drawRunBar(); return;
      }
      var t = h('table', { class: 'tbl' });
      var tb = h('tbody', {});

      tb.appendChild(row('계약', h('span', {}, [
        h('span', { class: 'mono', text: (p.contract_file || '—') }),
        h('span', { class: 'mut', text: '  →  ' + (p.contract || '?') })])));
      if (p.attach) {
        tb.appendChild(row('모드', h('b', { class: 'wn',
          text: 'attach — 노드를 띄우지 않고, 돌고 있는 시스템을 보기만 합니다' })));
      } else {
        tb.appendChild(row('워크스페이스',
          mark(p.block.join(' ').indexOf(p.workspace) < 0, p.workspace || '(없음)')));
        tb.appendChild(row('띄울 노드', h('span', { class: 'mono mut',
          text: (p.nodes || []).map(function (n) { return n.cmd; }).join('  ·  ') || '없음' })));
      }

      var v = p.video || {};
      var vtxt = (p.video_key || '(없음)');
      if (v.path) {
        vtxt += '  →  ' + v.path;
        if (v.frames) vtxt += '   (' + v.frames + '프레임 · ' + v.w + '×' + v.h +
                              ' · ' + v.fps + 'fps · ' + v.size_mb + 'MB)';
      }
      if (!p.video_key && p.attach) {
        tb.appendChild(row('영상', h('span', { class: 'mut',
          text: '— attach 모드라 영상을 넣지 않습니다 (돌고 있는 시스템을 그대로 봅니다)' })));
      } else {
        tb.appendChild(row('영상', mark(!!v.exists, vtxt)));
      }
      if (p.video_key && !p.video_registered) {
        tb.appendChild(row('', h('span', { class: 'wn',
          text: '⚠ local.yaml 의 videos: 에 등록되지 않은 이름이라 경로로 그대로 읽었습니다' })));
      }

      var span = p.limit ? (p.start + ' ~ ' + (p.start + p.limit) + '  (' + p.limit + '프레임)')
                         : (p.start + ' ~ 끝까지');
      tb.appendChild(row('구간 · 재생', h('span', { class: 'mono' }, [
        h('span', { text: span }),
        h('span', { class: 'mut', text: '   ' + p.mode +
          (p.sim_time ? ' · sim_time' : '') +
          (p.variants && p.variants.length > 1 ? ' · 변형 ' + p.variants.join(',') : '') })])));

      t.appendChild(tb);
      planBox.appendChild(t);

      (p.block || []).forEach(function (m) {
        planBox.appendChild(h('div', { class: 'empty', style: 'text-align:left',
          html: '<b class="no">⛔ 못 돌립니다</b> — ' + m }));
      });
      (p.warn || []).forEach(function (m) {
        planBox.appendChild(h('div', { class: 'empty', style: 'text-align:left',
          html: '<b class="wn">⚠</b> ' + m }));
      });
      planBox.appendChild(cli(p.cmd || ''));
      drawParams(p);
      drawAdv();
      drawRunBar();
    }

    /* ── 덮어쓰는 값 — ★고치고 다시 돌리는 고리★ 를 화면 안에서 닫는다 ──
     * 노드가 선언한 기본값 위에 시나리오·local.yaml 이 덮어쓰는 것들이다.
     * ★값의 종류(참·거짓/수/문자열)는 엔진(tb.config)이 판단한다★ — 여기서 한 벌 더
     * 판단하면 `show_window: "false"` 같은 것을 문자열로 써서 노드가 참으로
     * 읽는다. 리스트(IPM 4점 등)는 «카메라 보정» 이 맡으므로 잠가 둔다. */
    function drawParams(p) {
      clear(parBox);
      var nodes = (p.nodes || []).map(function (n) { return n.id; });
      if (!nodes.length) return;                 // attach 계약 — 띄우는 노드가 없다
      var flat = [];
      Object.keys(p.params || {}).forEach(function (nid) {
        Object.keys(p.params[nid]).forEach(function (k) {
          flat.push([nid, k, p.params[nid][k]]);
        });
      });
      var det = h('details', { class: 'reg' });
      det.appendChild(h('summary', { text: '덮어쓰는 값 — '
        + (flat.length
           ? flat.map(function (f2) { return f2[0] + '.' + f2[1] + '=' + f2[2]; })
               .join('  ·  ')
           : '없음 (노드가 선언한 기본값 그대로)') }));
      var b = h('div', { class: 'regbody' });
      b.appendChild(h('p', { class: 'help', html:
        '여기서 고치면 <b>파일에 그대로 저장</b>되고(주석 보존) 다음 실행부터 적용됩니다. '
        + '⚠ 파라미터를 바꾸면 기준 자동 비교에는 «조건이 다르다» 경고가 붙습니다 — '
        + '기준은 조건을 고정한 것이라서입니다. 파라미터를 바꾼 것끼리는 '
        + '<a href="#/compare">결과 비교</a> 로 <b>런 대 런</b>으로 보세요. '
        + '한 번에 둘을 나란히 보고 싶으면 시나리오의 <code>variants:</code> 를 씁니다. '
        + '<b>출처</b> 열은 지금 그 값이 어느 파일에서 왔는지입니다 — 시나리오에서 온 값을 '
        + 'local.yaml 에 저장하면 그 뒤로는 <b>local.yaml 이 이깁니다</b>(우선순위가 뒤다).' }));

      var edit = {};
      var ptb = h('tbody', {});
      flat.forEach(function (f2) {
        var key = f2[0] + '.' + f2[1];
        var locked = f2[2] !== null && typeof f2[2] === 'object';
        var inp = h('input', { type: 'text', size: '34',
          value: locked ? JSON.stringify(f2[2]) : String(f2[2]),
          disabled: locked ? 'disabled' : null });
        if (!locked) {
          inp.addEventListener('input', function () { edit[key] = inp.value; });
        }
        //  local.yaml 의 params 는 계약을 가리지 않고 전부 실린다 — 이 계약이
        //  안 띄우는 노드의 값은 고쳐도 이 시험에 아무 영향이 없다. 그걸 말해 준다.
        var mine = nodes.indexOf(f2[0]) >= 0;
        var loc2 = ((p.params_local || {})[f2[0]] || {});
        ptb.appendChild(h('tr', {}, [
          h('td', { class: 'mono mut', text: f2[0] }),
          h('td', { class: 'mono', text: f2[1] }),
          h('td', {}, [inp]),
          h('td', { class: 'mut',
                    text: loc2[f2[1]] !== undefined ? 'local.yaml' : '시나리오' }),
          h('td', { class: 'mut', text: locked ? '→ «카메라 보정» 에서'
                                       : (mine ? '' : '⚠ 이 계약이 안 띄우는 노드') }),
        ]));
      });
      if (flat.length) {
        b.appendChild(h('div', { class: 'tbl' }, [h('table', {}, [
          h('thead', {}, [h('tr', {}, ['노드', '파라미터', '값', '출처', '']
            .map(function (t2) { return h('th', { text: t2 }); }))]), ptb])]));
      }

      var aNid = h('select', {}, nodes.map(function (n) {
        return h('option', { value: n, text: n });
      }));
      var aKey = h('input', { type: 'text', placeholder: '파라미터 이름', size: '22' });
      var aVal = h('input', { type: 'text', placeholder: '값', size: '18' });
      var tgt = h('select', {}, [
        h('option', { value: 'local', text: 'local.yaml — 이 PC 에만' }),
        h('option', { value: scSel.value,
                      text: 'scenarios/' + scSel.value + ' — 시험에 굳힌다' })]);
      b.appendChild(h('div', { class: 'framebar' }, [
        h('span', { class: 'mut', text: '추가' }), aNid, aKey, aVal]));
      b.appendChild(h('div', { class: 'framebar' }, [
        h('span', { class: 'mut', text: '저장할 곳' }), tgt,
        h('button', { class: 'primary', text: '저장하고 다시 읽기', onclick: function () {
          var body2 = {};
          function put(nid, k, v) {
            body2[nid] = body2[nid] || {};
            body2[nid][k] = v;
          }
          Object.keys(edit).forEach(function (k2) {
            var i = k2.indexOf('.');
            put(k2.slice(0, i), k2.slice(i + 1), edit[k2]);
          });
          if (aKey.value.trim()) put(aNid.value, aKey.value.trim(), aVal.value);
          if (!Object.keys(body2).length) { say({}, '바뀐 값이 없습니다'); return; }
          post('/api/config/params', { params: body2, target: tgt.value })
            .then(function (j) { if (say(j, j.path + ' 에 저장했습니다')) load(); });
        } }),
        h('span', { class: 'mut',
          text: '값을 지우려면 파일에서 그 줄을 직접 지웁니다 (여기서는 덮어쓰기만)' }),
      ]));
      det.appendChild(b);
      parBox.appendChild(det);
    }

    // ── 3) 실행 버튼 ────────────────────────────────────────────
    function args() {
      var a = ['--scenario', 'scenarios/' + scSel.value];
      if (advCont.value) a.push('--contract', 'contracts/' + advCont.value);
      advVar.value.split(',').forEach(function (v) {
        v = v.trim();
        if (v) a.push('--variant', v);
      });
      if (tagIn.value.trim()) a.push('--tag', tagIn.value.trim());
      if (advBase.value) a.push('--baseline', advBase.value);
      if (advDom.value && Number(advDom.value)) a.push('--domain', advDom.value);
      if (recCb.checked) a.push('--record-debug');
      if (advWatch.checked) a.push('--watch');
      if (advKeep.checked) a.push('--keep-going');
      return a;
    }
    function drawAdv() {
      clear(advBox);
      var det = h('details', { class: 'reg' });
      det.appendChild(h('summary', { text: '고급 옵션' }));
      var b = h('div', { class: 'regbody toolform' });
      function row(lab, el, help) {
        return h('div', { class: 'toolopt' }, [
          h('label', { class: 'mono', text: lab }), el,
          help ? h('span', { class: 'mut', text: help }) : null]);
      }
      b.appendChild(row('--contract', advCont, '시나리오의 contract: 를 덮어쓴다'));
      b.appendChild(row('--variant', advVar,
        '이 변형만 돌린다 (쉼표로 여러 개). 이 시나리오의 변형: '
        + (((state.plan || {}).variants || []).join(', ') || '—')));
      b.appendChild(row('--baseline', advBase, '끝나고 이 기준과 자동 비교'));
      b.appendChild(row('--domain', advDom, 'ROS_DOMAIN_ID (0=기본)'));
      b.appendChild(h('div', { class: 'toolflags' }, [
        h('label', { class: 'toolflag' }, [advWatch,
          h('span', { class: 'mono', text: '--watch' }),
          h('span', { class: 'mut', text:
            '디버그 영상 창을 띄운다 — ★서버가 도는 PC 의 화면★에 뜬다. '
            + '멀리서 브라우저로 붙었다면 아래 라이브 화면을 보면 된다.' })]),
        h('label', { class: 'toolflag' }, [advKeep,
          h('span', { class: 'mono', text: '--keep-going' }),
          h('span', { class: 'mut', text: '변형 하나가 실패해도 나머지를 계속' })]),
      ]));
      det.appendChild(b);
      advBox.appendChild(det);
      b.addEventListener('input', syncCmd);
      b.addEventListener('change', syncCmd);
    }

    function drawRunBar() {
      clear(runBar);
      var blocked = state.plan && state.plan.block && state.plan.block.length > 0;
      runBar.appendChild(h('button', { class: 'primary', text: '실행',
        disabled: blocked ? 'disabled' : null,
        title: blocked ? '위의 ⛔ 를 먼저 해결하세요' : '',
        onclick: function () { start('run', args()); } }));
      runBar.appendChild(h('button', { text: '주입 검증만',
        title: '영상과 YOLO 없이 좌표 변환 계산만 몇 초 만에 확인합니다',
        onclick: function () { start('inject', ['--scenario', 'scenarios/' + scSel.value]); } }));
      runBar.appendChild(h('button', { text: '환경 점검',
        onclick: function () { start('doctor', ['--scenario', 'scenarios/' + scSel.value]); } }));
      //  ★고치고 다시 돌리는 고리의 첫 칸★ 위의 ⚠ '소스가 빌드보다 새롭다' 를 본
      //  자리에서 바로 누를 수 있어야 한다 — 안 그러면 터미널로 나가야 하고,
      //  그 사이에 그냥 «실행» 을 눌러 ★고치기 전 코드★ 를 재게 된다.
      runBar.appendChild(h('button', { text: '워크스페이스 빌드',
        title: '대상 코드를 고쳤으면 먼저 빌드해야 그 변경이 반영됩니다',
        onclick: function () { start('build', ['--scenario', 'scenarios/' + scSel.value]); } }));
      runBar.appendChild(h('span', { class: 'spacer' }));
      runBar.appendChild(h('button', { text: '중지', onclick: stop }));
      syncCmd();
    }
    // 고급 옵션을 켰을 때 ★실제로 나갈 명령줄★을 눈으로 확인시킨다.
    // 값이 바뀌는 족족 다시 쓴다 — 실행하고 나서야 무엇이 나갔는지 알면 늦다.
    function syncCmd() {
      clear(cmdPrev);
      cmdPrev.appendChild(cli('python3 -m tb.run run ' + args().join(' ')));
    }

    function start(kind, a) {
      postJob(kind, a).then(function (j) {
        if (j.error) return;
        poll();
      });
    }
    function stop() {
      fetch('/api/jobs/stop', { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          hintEl.textContent = j.error || '중지 신호를 보냈습니다';
          setTimeout(function () { hintEl.textContent = ''; }, 3000);
        });
    }

    // ── 4) 실행 중 화면 — «도구» 탭과 같은 것을 본다 (jobBox)
    function drawLive(st) {
      clear(liveBox);
      if (!st.running && !st.kind) return;
      liveBox.appendChild(jobBox(st));
    }

    function poll() {
      if (execTimer) clearInterval(execTimer);
      var tick = function () {
        if (location.hash.indexOf('/exec') < 0) {
          clearInterval(execTimer); execTimer = null; return;
        }
        get('/api/status').then(function (st) {
          drawLive(st);
          if (!st.running) { clearInterval(execTimer); execTimer = null; }
        }).catch(function () { clearInterval(execTimer); execTimer = null; });
      };
      tick();
      execTimer = setInterval(tick, 1200);
    }

    // ── 5) 저장 — 파라미터만. 등록(영상·계약·시나리오)은 «새 시험 시작» 이 한다
    //   같은 폼을 두 화면에 두면 한쪽만 고쳐진다. 이 화면은 고르고 돌리기만 한다.
    function post(url, body2) {
      return fetch(url, { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body2) }).then(function (r) { return r.json(); });
    }
    function say(j, okmsg) {
      hintEl.textContent = j.error ? ('오류: ' + j.error) : okmsg;
      setTimeout(function () { hintEl.textContent = ''; }, 5000);
      return !j.error;
    }

    function load() {
      clear(planBox);
      planBox.appendChild(spinner('무엇을 쓰게 되는지 확인하는 중…'));
      get('/api/scenario?name=' + encodeURIComponent(scSel.value))
        .then(drawPlan)
        .catch(function (e) {
          clear(planBox);
          planBox.appendChild(h('div', { class: 'empty', text: '오류: ' + e.message }));
        });
    }
    scSel.addEventListener('change', load);
    if (scenarios.length) load();
    else planBox.appendChild(h('div', { class: 'empty',
      text: 'scenarios/ 가 비어 있습니다 — «새 시험 시작» 에서 하나 만드세요.' }));
    view.appendChild(h('p', { class: 'sub', html:
      '영상·워크스페이스·시나리오를 등록하거나 새로 만들려면 '
      + '<a href="#/newtest">새 시험 시작</a> 으로. 이 화면은 있는 시나리오를 돌립니다.' }));
    poll();
  }

  function renderCheck(state) {
    clear(view);
    view.appendChild(h('h1', { text: '환경 점검' }));
    view.appendChild(h('p', { class: 'sub',
      text: '환경·계약·가중치·영상이 제대로 물려 있는지, 테스트베드 자체에 문제가 없는지 확인합니다.' }));

    var scSel = h('select', {}, (state.scenarios || []).map(function (f2) {
      return h('option', { value: f2, text: f2 });
    }));
    var out = h('div', { class: 'md' });
    var cSel = h('select', {}, [h('option', { value: '', text: '(시나리오가 정한 것)' })].concat(
      (state.contracts || []).map(function (x) { return h('option', { value: x, text: x }); })));

    /* 인자는 명세가 검사한다 — 화면은 순서대로 넘기기만 한다 */
    function run(name, argv) {
      out.textContent = '실행 중…';
      var qs = (argv || []).map(function (a) { return 'a=' + encodeURIComponent(a); });
      get('/api/quick/' + name + (qs.length ? '?' + qs.join('&') : ''))
        .then(function (d) {
          out.textContent = (d.cmd ? '$ ' + d.cmd + '\n\n' : '') + (d.out || '(출력 없음)');
          hintEl.textContent = (JOBNAME[name] || name) + ' → '
                               + (d.rc === 0 ? '이상 없음' : '문제 있음');
          setTimeout(function () { hintEl.textContent = ''; }, 4000);
        }).catch(function (e) { out.textContent = '오류: ' + e.message; });
    }
    function doctorArgs() {
      var a = ['--scenario', 'scenarios/' + scSel.value];
      if (cSel.value) a.push('--contract', 'contracts/' + cSel.value);
      return a;
    }

    view.appendChild(h('div', { class: 'framebar' }, [
      h('span', { class: 'mut', text: '시나리오' }), scSel,
      h('span', { class: 'mut', text: '계약' }), cSel,
      h('button', { class: 'primary', text: '환경 점검 (doctor)',
                    onclick: function () { run('doctor', doctorArgs()); } }),
      h('button', { text: '자체 검사 (selftest)', onclick: function () { run('selftest', []); } }),
    ]));

    /* 계약 초안 — 인자가 여럿이라 따로 묶는다. 돌고 있는 ROS 그래프가 있어야 한다. */
    var dSec = h('input', { type: 'number', value: '8', step: '1', style: 'width:80px' });
    var dName = h('input', { type: 'text', placeholder: 'discovered', size: '16' });
    var dOut = h('input', { type: 'text', placeholder: '(화면에만)', size: '26' });
    var dWs = h('input', { type: 'text', placeholder: '/home/me/other_ws', size: '26' });
    var dInc = h('input', { type: 'text', placeholder: '이 문자열이 든 토픽만 (쉼표)', size: '22' });
    var dExc = h('input', { type: 'text', placeholder: '제외할 토픽 (쉼표)', size: '22' });
    function discArgs() {
      var a = [];
      if (dSec.value) a.push('--seconds', dSec.value);
      if (dName.value.trim()) a.push('--name', dName.value.trim());
      if (dOut.value.trim()) a.push('--out', dOut.value.trim());
      if (dWs.value.trim()) a.push('--workspace', dWs.value.trim());
      if (dInc.value.trim()) a.push('--include', dInc.value.trim());
      if (dExc.value.trim()) a.push('--exclude', dExc.value.trim());
      return a;
    }
    var dDet = h('details', { class: 'reg' });
    dDet.appendChild(h('summary', { text: '계약 초안 (discover) — 다른 워크스페이스를 붙일 때' }));
    dDet.appendChild(h('div', { class: 'regbody toolform' }, [
      h('p', { class: 'help', html:
        '<b>대상 시스템을 평소처럼 띄워 둔 상태</b>에서 누르세요. 돌고 있는 토픽·타입·필드 '
        + '배치를 실제 메시지에서 읽어 계약 초안을 만듭니다. '
        + '<code>--out</code> 에 <code>contracts/이름.yaml</code> 을 적으면 파일로 남습니다.' }),
      h('div', { class: 'toolopt' }, [h('label', { class: 'mono', text: '--seconds' }), dSec,
        h('span', { class: 'mut', text: '몇 초 동안 들을 것인가' })]),
      h('div', { class: 'toolopt' }, [h('label', { class: 'mono', text: '--name' }), dName,
        h('span', { class: 'mut', text: '계약 이름' })]),
      h('div', { class: 'toolopt' }, [h('label', { class: 'mono', text: '--out' }), dOut,
        h('span', { class: 'mut', text: '쓸 파일 (비우면 화면에만)' })]),
      h('div', { class: 'toolopt' }, [h('label', { class: 'mono', text: '--workspace' }), dWs,
        h('span', { class: 'mut', text: '대상 워크스페이스 경로' })]),
      h('div', { class: 'toolopt' }, [h('label', { class: 'mono', text: '--include' }), dInc, null]),
      h('div', { class: 'toolopt' }, [h('label', { class: 'mono', text: '--exclude' }), dExc, null]),
      h('div', { class: 'framebar' }, [
        h('button', { class: 'primary', text: '계약 초안 뽑기',
                      onclick: function () { run('discover', discArgs()); } }),
      ]),
    ]));
    view.appendChild(dDet);
    view.appendChild(out);
    view.appendChild(h('p', { class: 'sub', html:
      '다른 명령은 <a href="#/tools">도구</a> 탭에 전부 있습니다.' }));
  }

  /* ── 결과 비교 : 두 실행(또는 기준)을 골라 회귀 비교 ──────────────────── */
  function renderCompare(state) {
    clear(view);
    view.appendChild(h('h1', { text: '결과 비교' }));
    view.appendChild(h('p', { class: 'sub',
      text: '기준과 실행, 또는 두 실행을 골라 신호별 차이를 봅니다. 조건이 다르면 결과 위에 경고가 붙습니다.' }));

    function opts(list) {
      return list.map(function (x) { return h('option', { value: x, text: x }); });
    }
    var aSel = h('select', {}, opts(state.baselines.concat(state.runs)));
    var bSel = h('select', {}, opts(state.runs));
    if (state.runs.length) bSel.value = state.runs[0];
    var out = h('div', { class: 'md' });
    /* 계약·시나리오를 바꿔 가며 비교할 수 있다 — 계약을 고친 뒤 옛 결과를
       새 해석으로 다시 보는 것이 회귀 확인의 절반이다. */
    var cSel = h('select', {}, [h('option', { value: '', text: '(기본)' })].concat(
      (state.contracts || []).map(function (x) { return h('option', { value: x, text: x }); })));
    var sSel = h('select', {}, [h('option', { value: '', text: '(기본)' })].concat(
      (state.scenarios || []).map(function (x) { return h('option', { value: x, text: x }); })));
    function cmpArgs() {
      var a = [aSel.value, bSel.value];
      if (cSel.value) a.push('--contract', 'contracts/' + cSel.value);
      if (sSel.value) a.push('--scenario', 'scenarios/' + sSel.value);
      return a;
    }

    view.appendChild(h('div', { class: 'framebar' }, [
      h('span', { class: 'mut', text: '기준' }), aSel,
      h('span', { class: 'mut', text: '→ 현재' }), bSel,
      h('button', { class: 'primary', text: '비교', onclick: function () {
        out.textContent = '비교 중…';
        postJob('compare', cmpArgs()).then(function () {
          setTimeout(function () {
            get('/api/status').then(function (st) {
              out.textContent = st.log_tail || '(출력 없음)';
            });
          }, 1500);
        });
      } }),
    ]));
    var adv = h('details', { class: 'reg' });
    adv.appendChild(h('summary', { text: '고급 옵션' }));
    adv.appendChild(h('div', { class: 'regbody toolform' }, [
      h('div', { class: 'toolopt' }, [h('label', { class: 'mono', text: '--contract' }), cSel,
        h('span', { class: 'mut', text: '이 계약으로 다시 해석해 비교한다' })]),
      h('div', { class: 'toolopt' }, [h('label', { class: 'mono', text: '--scenario' }), sSel,
        h('span', { class: 'mut', text: '허용오차(compare_tol)를 이 시나리오 것으로 쓴다' })]),
    ]));
    view.appendChild(adv);
    view.appendChild(out);
  }

  /* ── 캘리브레이션 : 영상을 돌려 가며 사각형·ROI·px2m 을 직접 맞춘다 ──
   * 기하 계산은 서버(tb.geometry)가 한다 — 대상 노드가 하는 변환을 그대로
   * 재현한 코드가 이미 있고, 그걸 JS 에 한 벌 더 쓰면 반드시 어긋난다.
   * 여기서 하는 일은 ★어디를 찍었는지 원본 좌표로 되돌리는 것★뿐이고,
   * 그 환산은 서버가 준 meta(disp_w/disp_h/split_x/src_scale)만 쓴다. */
  var CAL = null;                       // 화면을 떠나도 살아 있는 편집 상태

  function renderCalib(st) {
    clear(view);
    if (CAL) {
      if (CAL.timer) clearInterval(CAL.timer);
      (CAL.off || []).forEach(function (f2) { f2(); });   // 이전 화면의 리스너 해제
    }

    view.appendChild(h('h1', { text: '카메라 보정' }));
    view.appendChild(h('p', { class: 'sub',
      text: 'IPM 사각형의 좌우 변을 차선 위에 올리세요. 지면은 평평해서 제대로 올리면 '
            + 'BEV(위에서 내려다본 화면)에서 차선이 수직으로 섭니다. 수직에 얼마나 가까운지를 '
            + '아래 «수직도» 로 재 줍니다 (직선 구간에서만 의미가 있습니다).' }));

    /* ── 편집 대상 — 전부 계약의 targets 에서 온다 ────────────────
       계약에 ROI 를 하나 더 늘려도 이 화면은 고치지 않는다. */
    var KIND_LABEL = { quad: 'IPM 사각형', rect: 'ROI', scale: '길이 재기' };
    var modes = [];
    Object.keys(st.targets || {}).forEach(function (k) {
      var t = st.targets[k];
      if (t.kind === 'quad') modes.push({ id: 'quad', key: k, label: KIND_LABEL.quad, hint: t.hint });
      else if (t.kind === 'rect') modes.push({ id: k, key: k, label: k, hint: t.hint });
    });
    Object.keys(st.targets || {}).forEach(function (k) {
      var t = st.targets[k];
      if (t.kind === 'scale') modes.push({ id: 'measure', key: k, label: KIND_LABEL.scale, hint: t.hint });
    });
    /* ── BEV 가로선 : 거리로 판정하는 노드의 기준선과 문턱 ────────
       BEV 는 원근이 펴져 있어 가로선 하나가 곧 '차에서 얼마'다.
       기준선(bev_row)을 먼저 놓고 문턱(bev_dist)은 그 선에서의 거리로 잡는다 —
       기준선을 옮기면 문턱이 통째로 따라온다. */
    var BEVKIND = {}, BUMPER = '';
    Object.keys(st.targets || {}).forEach(function (k) {
      var t = st.targets[k];
      if (t.kind !== 'bev_row' && t.kind !== 'bev_dist') return;
      BEVKIND[k] = t.kind;
      if (t.kind === 'bev_row' && !BUMPER) BUMPER = k;
      modes.push({ id: k, key: k, label: k, hint: t.hint });
    });
    function bumperY() {
      return BUMPER ? Number(S.bevRows[BUMPER]) : ((st.bev || {}).h || 480);
    }
    function rowY(k) {
      return BEVKIND[k] === 'bev_row' ? Number(S.bevRows[k])
                                      : bumperY() - Number(S.bevRows[k]);
    }
    function setRowY(k, y) {
      S.bevRows[k] = Math.round(BEVKIND[k] === 'bev_row' ? y : bumperY() - y);
    }
    if (!modes.length) modes.push({ id: 'quad', key: '', label: KIND_LABEL.quad, hint: '' });

    var vidNames = Object.keys(st.videos || {});
    var curVid = st.video || (vidNames.length ? st.videos[vidNames[0]].path : '');
    var S = {
      st: st, video: curVid, frame: st.start || 0,
      quad: (st.quad || []).slice(), rects: JSON.parse(JSON.stringify(st.rects || {})),
      px2m: st.px2m, lengthM: st.length_m,
      bevRows: JSON.parse(JSON.stringify(st.bev_rows || {})),
      mode: modes[0].id, sel: 0, meas: [], realM: st.length_m,
      undist: st.undistort !== false, grid: true, playing: false, fps: 15,
      busy: false, dirty: false, timer: null, meta: null, off: [],
    };
    CAL = S;

    function vidInfo() {
      for (var i = 0; i < vidNames.length; i++) {
        if (st.videos[vidNames[i]].path === S.video) return st.videos[vidNames[i]];
      }
      return {};
    }
    function total() { return vidInfo().frames || 0; }
    if (total()) S.frame = Math.max(0, Math.min(total() - 1, S.frame));

    // ── 1) 시나리오 · 영상 ──────────────────────────────────────
    var scSel = h('select', {}, (st.scenarios || []).map(function (f2) {
      return h('option', { value: f2, text: f2 });
    }));
    scSel.value = st.scenario || '';
    scSel.addEventListener('change', function () {
      stopPlay();
      var want = scSel.value;
      get('/api/calib?scenario=' + encodeURIComponent(want))
        .then(renderCalib)
        .catch(function (e) {
          /* 그 계약에 calibration: 이 없으면 맞출 게 없다. 화면을 날리지 않고
             고르기 전으로 되돌린다 — 안 그러면 탭을 다시 눌러야 한다. */
          scSel.value = st.scenario || '';
          say(want + ' 로는 보정할 수 없습니다 — ' + e.message, true);
        });
    });

    var vidSel = h('select', {}, vidNames.map(function (k) {
      var v = st.videos[k];
      return h('option', { value: v.path,
        text: k + (v.frames ? '  (' + v.frames + '프레임)' : '  (열 수 없음)') });
    }));
    vidSel.value = S.video;
    vidSel.addEventListener('change', function () {
      S.video = vidSel.value;
      S.frame = Math.min(S.frame, Math.max(0, total() - 1));
      syncBar(); draw();
    });

    // ── 2) 프레임 바 — 재생하며 정지, 슬라이더로 훑기 ────────────
    var slider = h('input', { type: 'range', min: '0', max: '1', value: '0',
                              class: 'calslider' });
    slider.addEventListener('input', function () {
      stopPlay(); S.frame = parseInt(slider.value, 10) || 0; syncBar(); draw();
    });
    var frIn = h('input', { type: 'number', class: 'wherebox', style: 'max-width:100px' });
    frIn.addEventListener('change', function () { goto(parseInt(frIn.value, 10) || 0); });
    var totLbl = h('span', { class: 'mut' });
    var playBtn = h('button', { class: 'sigbtn', text: '▶ 재생',
                                title: '스페이스바' , onclick: togglePlay });
    var fpsIn = h('input', { type: 'number', value: '15', min: '1', max: '60',
                             class: 'wherebox', style: 'max-width:64px' });
    fpsIn.addEventListener('change', function () {
      S.fps = Math.max(1, Math.min(60, parseInt(fpsIn.value, 10) || 15));
      if (S.playing) { stopPlay(); togglePlay(); }
    });

    function goto(n) {
      var t = total();
      S.frame = Math.max(0, t ? Math.min(t - 1, n) : n);
      syncBar(); draw();
    }
    function step(d) { stopPlay(); goto(S.frame + d); }
    function togglePlay() {
      if (S.playing) return stopPlay();
      S.playing = true;
      playBtn.textContent = '⏸ 정지';
      playBtn.classList.add('on');
      S.timer = setInterval(function () {
        if (location.hash.indexOf('/calib') < 0) return stopPlay();
        var t = total();
        if (t && S.frame >= t - 1) return stopPlay();
        S.frame += 1; syncBar(); draw();
      }, Math.round(1000 / S.fps));
    }
    function stopPlay() {
      S.playing = false;
      if (S.timer) { clearInterval(S.timer); S.timer = null; }
      playBtn.textContent = '▶ 재생';
      playBtn.classList.remove('on');
    }
    window.__stopPoll = stopPlay;        // 다른 화면으로 가면 라우터가 세운다

    function syncBar() {
      var t = total();
      slider.max = String(Math.max(1, t - 1));
      slider.value = String(S.frame);
      frIn.value = String(S.frame);
      totLbl.textContent = t ? '/ ' + (t - 1) : '(프레임 수를 모릅니다)';
    }

    var undBtn = toggleBtn('왜곡보정', function () { return S.undist; },
      function (v) { S.undist = v; }, '렌즈 왜곡을 펴서 볼지 여부 (u)');
    var gridBtn = toggleBtn('격자', function () { return S.grid; },
      function (v) { S.grid = v; }, 'BEV 에 0.5m 격자를 겹칩니다 (g)');

    function toggleBtn(label, getv, setv, title) {
      var b = h('button', { class: 'sigbtn', title: title, onclick: function () {
        setv(!getv()); sync(); draw();
      } });
      function sync() {
        var on = getv();
        b.classList.toggle('on', on);
        b.style.background = on ? 'var(--accent)' : '';
        b.textContent = label + ' ' + (on ? 'ON' : 'OFF');
      }
      sync();
      return b;
    }

    // ── 3) 편집 대상 탭 ─────────────────────────────────────────
    var hintEl2 = h('p', { class: 'sub' });
    var modeBtns = modes.map(function (m, i) {
      return h('button', { class: 'sigbtn', text: (i + 1) + '. ' + m.label,
        title: m.hint || '', onclick: function () { setMode(m.id); } });
    });
    function setMode(id) {
      S.mode = id; S.sel = 0; S.meas = [];
      modes.forEach(function (m, i) {
        var on = m.id === S.mode;
        modeBtns[i].classList.toggle('on', on);
        modeBtns[i].style.background = on ? 'var(--accent)' : '';
      });
      var cur = modes.filter(function (m) { return m.id === S.mode; })[0] || {};
      hintEl2.textContent = (cur.hint ? cur.hint + '  —  ' : '') + (
        S.mode === 'measure'
          ? '오른쪽 BEV 에서 실제 길이를 아는 두 점을 클릭하세요 (차선 폭이 가장 쉽습니다).'
          : S.mode === 'quad'
            ? '왼쪽 원본에서 점을 끌어 옮기세요. 빈 곳을 누르면 고른 점이 그 자리로 갑니다.'
            : '왼쪽 원본에서 드래그하면 새 사각형, 모서리를 잡으면 그 모서리만 움직입니다.');
      drawFields(); draw();
    }

    // ── 4) 값 패널 — 지금 고른 대상의 숫자를 직접 넣을 수도 있다 ──
    var fields = h('div', { class: 'framebar' });
    function numField(label, get2, set2, stepv) {
      var i = h('input', { type: 'number', step: String(stepv || 1),
                           class: 'wherebox', style: 'max-width:96px',
                           value: String(get2()) });
      i.addEventListener('change', function () {
        var v = parseFloat(i.value);
        if (v === v) { set2(v); draw(); }
      });
      i.__sync = function () { if (document.activeElement !== i) i.value = String(get2()); };
      return [h('span', { class: 'mut', text: label }), i];
    }
    var syncers = [];
    function drawFields() {
      clear(fields); syncers = [];
      function add(pair) { fields.appendChild(pair[0]); fields.appendChild(pair[1]);
                           syncers.push(pair[1].__sync); }
      if (S.mode === 'quad') {
        ['TL', 'TR', 'BR', 'BL'].forEach(function (lab, i) {
          fields.appendChild(h('button', {
            class: 'sigbtn' + (S.sel === i ? ' on' : ''), text: lab,
            style: S.sel === i ? 'background:var(--accent)' : '',
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
        add(numField(isRow ? 'BEV 행 [px]' : '범퍼선에서 [px]',
                     function () { return Math.round(S.bevRows[S.mode]); },
                     function (v) { S.bevRows[S.mode] = v; }));
        if (!isRow) {
          fields.appendChild(h('span', { class: 'sub',
            text: '≈ ' + (S.bevRows[S.mode] * S.px2m).toFixed(2) + ' m' }));
        }
        fields.appendChild(h('span', { class: 'sub',
          text: 'BEV 화면에서 선을 끌거나 ↑↓ 로 옮깁니다' }));
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
      add(numField('lane_width [m]', function () { return S.lengthM; },
                   function (v) { S.lengthM = v; }, 0.05));
      syncFields();
    }
    function syncFields() { syncers.forEach(function (f2) { if (f2) f2(); }); }

    var applyBtn = h('button', { text: 'px2m 적용', onclick: function () {
      if (S.meas.length < 2) return;
      var d = Math.hypot(S.meas[0][0] - S.meas[1][0], S.meas[0][1] - S.meas[1][1]);
      if (d < 2) return;
      S.px2m = Number((S.realM / d).toFixed(6));
      S.lengthM = S.realM;                 // 잰 길이가 곧 차선 폭이다
      S.meas = [];
      drawFields(); draw();
    } });

    // ── 5) 화면 ─────────────────────────────────────────────────
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
      if (x <= m.split_x) {
        return { panel: 'src', x: x / m.src_scale, y: y / m.src_scale };
      }
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
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    S.off.push(function () {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
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

    document.onkeydown = function (e) {
      if (/^(INPUT|SELECT|TEXTAREA)$/.test((e.target || {}).tagName || '')) return;
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
    function refreshToggles() {
      // 토글 버튼의 라벨은 '지금 상태'다 — 키로 바꿨으면 버튼도 따라와야 한다
      [[undBtn, S.undist, '왜곡보정'], [gridBtn, S.grid, '격자']].forEach(function (t) {
        t[0].classList.toggle('on', t[1]);
        t[0].style.background = t[1] ? 'var(--accent)' : '';
        t[0].textContent = t[2] + ' ' + (t[1] ? 'ON' : 'OFF');
      });
      draw();
    }
    function reset() {
      S.quad = (st.quad || []).slice();
      S.rects = JSON.parse(JSON.stringify(st.rects || {}));
      S.px2m = st.px2m; S.lengthM = st.length_m; S.realM = st.length_m;
      S.bevRows = JSON.parse(JSON.stringify(st.bev_rows || {}));
      S.meas = [];
      drawFields(); draw();
      say('파일에 있던 값으로 되돌렸습니다');
    }

    // ── 6) 그리기 — 서버에 한 번에 하나만 물어본다 ───────────────
    function body() {
      return { scenario: scSel.value, video: S.video, frame: S.frame,
               quad: S.quad, rects: S.rects, px2m: S.px2m, length_m: S.lengthM,
               bev_rows: S.bevRows,
               undistort: S.undist, grid: S.grid, mode: S.mode,
               meas: S.meas, w: 1400 };
    }
    var yamlTimer = null;
    function draw() {
      if (yamlTimer) clearTimeout(yamlTimer);
      yamlTimer = setTimeout(refreshYaml, 500);
      if (!S.video) { meta.textContent = '먼저 영상을 등록하세요 («새 시험 시작» 1단계)'; return; }
      if (S.busy) { S.dirty = true; return; }
      S.busy = true;
      postJSON('/api/calib/view', body()).then(function (d) {
        S.busy = false;
        if (d.error) { meta.textContent = '오류: ' + d.error; return; }
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
      meta.appendChild(h('span', { class: 'mut', text: 'frame ' + S.frame }));
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
          meta.appendChild(h('span', { class: 'mut',
            text: 'BEV 에서 두 점을 클릭하세요 (지금 ' + S.meas.length + '/2)' }));
        }
      }
    }

    // ── 7) 저장 · 대조 ──────────────────────────────────────────
    var yamlBox = h('div', { class: 'md', style: 'max-height:240px' });
    var saveSel = h('select', {}, [h('option', { value: 'local', text: 'local.yaml (이 PC 전용)' })]
      .concat((st.scenarios || []).map(function (f2) {
        return h('option', { value: f2, text: 'scenarios/' + f2 });
      })));
    function say(msg, bad) {
      hintEl.textContent = (bad ? '오류: ' : '') + msg;
      setTimeout(function () { hintEl.textContent = ''; }, 5000);
    }
    function refreshYaml() {
      postJSON('/api/calib/yaml', body()).then(function (d) {
        yamlBox.textContent = d.error ? ('오류: ' + d.error) : d.yaml;
      }).catch(function (e) { yamlBox.textContent = '오류: ' + e.message; });
    }
    var saveBtn = h('button', { class: 'primary', text: '저장', onclick: function () {
      var b = body();
      b.target = saveSel.value;
      postJSON('/api/calib/save', b).then(function (d) {
        if (d.error) return say(d.error, true);
        say(d.path + ' 에 저장했습니다');
        get('/api/calib?scenario=' + encodeURIComponent(scSel.value))
          .then(function (s2) { st.quad = s2.quad; st.rects = s2.rects;
                                st.px2m = s2.px2m; st.length_m = s2.length_m;
                                st.bev_rows = s2.bev_rows; })
          .catch(function () {});
      }).catch(function (e) { say(e.message, true); });
    } });

    /* ── ⓐ 워크스페이스 기본값 ─────────────────────────────────────
       캘리브 값의 진짜 주인은 워크스페이스다. 그런데 white1 은 그것을 소스의
       declare_parameter 기본값으로 갖고 있어서 계약에 옮겨 적을 수밖에 없었다.
       `tb.run params` 가 노드에게 직접 물어 캐시해 두고, 여기서 그 값으로
       되돌릴 수 있게 한다 — 「내가 지금 노드와 같은 값에서 출발했나」의 답이다. */
    var wsBox = h('span', { class: 'mut' });
    function wsLabel() {
      wsBox.textContent = st.ws_stamp
        ? ('워크스페이스 값 읽은 시각 ' + st.ws_stamp)
        : '워크스페이스 값을 아직 안 읽었습니다';
    }
    wsLabel();
    var wsLoad = h('button', {
      text: '워크스페이스 기본값 불러오기',
      title: '노드가 스스로 선언한 값으로 되돌립니다 (사다리꼴·범퍼선·문턱·px2m)',
      onclick: function () {
        var w = st.ws_values;
        if (!w) return say('워크스페이스 값이 없습니다 — 먼저 «다시 읽기»', true);
        S.quad = (w.quad || []).slice();
        S.rects = JSON.parse(JSON.stringify(w.rects || {}));
        S.bevRows = JSON.parse(JSON.stringify(w.bev_rows || {}));
        if (w.px2m) S.px2m = w.px2m;
        if (w.length_m) S.lengthM = w.length_m;
        drawFields(); draw();
        say('노드가 선언한 값으로 되돌렸습니다 (저장하지는 않았습니다)');
      } });
    var wsRead = h('button', {
      text: '다시 읽기', title: '노드를 한 번 띄워 파라미터를 새로 받아 옵니다 (20~40초)',
      onclick: function () {
        say('노드를 띄워 파라미터를 묻습니다 — 20~40초');
        postJSON('/api/calib/wsparams', { scenario: scSel.value })
          .then(function () { waitJob(function () {
            get('/api/calib?scenario=' + encodeURIComponent(scSel.value))
              .then(function (s2) {
                st.ws_values = s2.ws_values; st.ws_stamp = s2.ws_stamp;
                wsLabel(); say('받아 왔습니다 — «불러오기» 로 적용하세요');
              }).catch(function () {});
          }); })
          .catch(function (e) { say(e.message, true); });
      } });

    /* ── ⓑ 실차로 내보내기 ────────────────────────────────────────
       맞춘 값이 테스트베드 안에만 남으면 실차 반영이 사람의 손 옮겨 적기로
       남는다. 워크스페이스 파일은 건드리지 않고 붙여 넣을 것만 만들어 준다. */
    var expBox = h('div', { class: 'md', style: 'max-height:220px' });
    var expBtn = h('button', {
      text: '실차 명령으로 내보내기',
      title: '지금 값을 ros2 명령과 파라미터 yaml 로 — 워크스페이스 파일은 안 고칩니다',
      onclick: function () {
        expBox.textContent = '만드는 중…';
        postJSON('/api/calib/export', body()).then(function (d) {
          if (d.error) { expBox.textContent = '오류: ' + d.error; return; }
          expBox.textContent = d.launch + '\n\n# ── 파라미터 파일 ──\n' + d.params_yaml;
        }).catch(function (e) { expBox.textContent = '오류: ' + e.message; });
      } });

    var runSel = h('select', {}, (st.runs || []).map(function (r) {
      return h('option', { value: r, text: r });
    }));
    var vOut = h('div', { class: 'md' });
    var vBtn = h('button', { text: '노드와 대조', onclick: function () {
      if (!(st.runs || []).length) return;
      vOut.textContent = '대조 중… (프레임을 여러 장 맞춰 보므로 시간이 걸립니다)';
      postJSON('/api/calib/verify', { scenario: scSel.value, run: runSel.value })
        .then(function (d) { vOut.textContent = d.error ? ('오류: ' + d.error) : d.out; })
        .catch(function (e) { vOut.textContent = '오류: ' + e.message; });
    } });

    // ── 조립 ────────────────────────────────────────────────────
    view.appendChild(h('div', { class: 'framebar' }, [
      h('span', { class: 'mut', text: '시나리오' }), scSel,
      h('span', { class: 'mut', text: '영상' }), vidSel,
      h('span', { class: 'spacer' }),
      h('span', { class: 'mut', text: '계약 ' + (st.contract_file || '') }),
    ]));
    view.appendChild(h('div', { class: 'framebar' }, [
      playBtn,
      h('button', { class: 'sigbtn', text: '처음', title: '0번 프레임으로',
                    onclick: function () { step(-1e9); } }),
      h('button', { class: 'sigbtn', text: '−30', title: '[', onclick: function () { step(-30); } }),
      h('button', { class: 'sigbtn', text: '−1', title: ',', onclick: function () { step(-1); } }),
      h('button', { class: 'sigbtn', text: '+1', title: '.', onclick: function () { step(1); } }),
      h('button', { class: 'sigbtn', text: '+30', title: ']', onclick: function () { step(30); } }),
      frIn, totLbl,
      h('span', { class: 'mut', text: 'fps' }), fpsIn,
      h('span', { class: 'spacer' }), undBtn, gridBtn,
    ]));
    view.appendChild(slider);
    view.appendChild(h('div', { class: 'framebar' }, modeBtns.concat([
      h('span', { class: 'spacer' }),
      h('button', { text: '되돌리기', title: '파일에 있던 값으로 (r)', onclick: reset }),
    ])));
    /* ⓐ 워크스페이스 기본값 — 「노드와 같은 값에서 출발했나」 */
    view.appendChild(h('div', { class: 'framebar' }, [
      wsLoad, wsRead, wsBox,
    ]));
    view.appendChild(hintEl2);
    view.appendChild(fields);
    view.appendChild(img);
    view.appendChild(meta);
    view.appendChild(h('p', { class: 'sub', text:
      '단축키 — 스페이스 재생/정지 · , . 한 프레임 · [ ] 30프레임 · 방향키 1px · '
      + 'Shift+방향키 10px · 1~' + modes.length + ' 편집 대상 · g 격자 · u 왜곡보정 · r 되돌리기' }));

    view.appendChild(sectionTitle('저장'));
    view.appendChild(h('p', { class: 'help', html:
      '캘리브 값은 카메라·영상마다 다르므로 보통 <b>local.yaml</b> 에 둡니다. '
      + '시나리오에 굳혀 두고 싶을 때만 시나리오를 고르세요. 두 경우 모두 <b>주석은 그대로</b> 남습니다.' }));
    view.appendChild(h('div', { class: 'framebar' }, [
      h('span', { class: 'mut', text: '저장할 곳' }), saveSel, saveBtn,
      h('button', { text: 'YAML 새로 고침', onclick: refreshYaml }),
    ]));
    view.appendChild(yamlBox);

    /* ⓑ 실차로 내보내기 */
    view.appendChild(sectionTitle('실차로 내보내기'));
    view.appendChild(h('p', { class: 'help', html:
      '맞춘 값을 <b>실차에서 그대로 쓸 수 있는 형태</b>로 만듭니다 — '
      + '<code>ros2</code> 명령과 <code>--params-file</code> 용 yaml. '
      + '<b>워크스페이스 파일은 고치지 않습니다.</b><br>'
      + '⚠️ 이 값은 <b>맞출 때 쓴 영상의 카메라 설정</b>입니다. 그 영상이 실차 카메라로 '
      + '찍힌 것이 아니면 실차에 그대로 쓰면 안 됩니다.' }));
    view.appendChild(h('div', { class: 'framebar' }, [expBtn]));
    view.appendChild(expBox);

    view.appendChild(sectionTitle('노드와 대조'));
    view.appendChild(h('p', { class: 'help', html:
      '여기서 그리는 BEV 가 <b>노드가 실제로 만드는 BEV</b> 와 같은지 확인합니다. '
      + '<code>--record-debug</code> 로 디버그 영상을 남긴 실행이 있어야 합니다.' }));
    view.appendChild(h('div', { class: 'framebar' }, (st.runs || []).length
      ? [h('span', { class: 'mut', text: '실행' }), runSel, vBtn]
      : [h('span', { class: 'mut', text:
          '디버그 영상이 있는 실행이 없습니다 — 테스트 실행에서 «디버그 영상 기록» 을 켜고 한 번 돌리세요.' })]));
    view.appendChild(vOut);
    view.appendChild(cli('python3 -m tb.calibrate --scenario scenarios/'
                         + (st.scenario || 'regression.yaml')));

    setMode(S.mode);
    syncBar();
    refreshYaml();
  }

  /* ── 새 시험 시작 — 돌리기 전에 등록하는 것은 전부 여기 ────────────────
   * ★왜 마법사인가★ 시험을 새로 차리는 일은 순서가 있다(워크스페이스 → 영상 →
   * 시나리오 → 카메라 맞추기 → 돌리기). 그 순서를 기억하는 것이 사람 몫이면
   * 반드시 한 단계를 빼먹는다 — 특히 ★카메라 맞추기★ 를 빼먹고 남의 영상 값으로
   * 판정하게 된다. 그래서 순서를 화면이 들고 있는다.
   *
   * ★쓰기는 여기, 실행은 «테스트 실행»★ 등록 폼이 두 화면에 있으면 같은 것을
   * 두 벌 짜게 되고 한쪽만 고쳐진다. 그래서 local.yaml·contracts·scenarios 를
   * 건드리는 폼은 전부 이 화면에만 있고, 실행 화면은 고르고 돌리기만 한다.
   * 카메라 맞추기(3)와 돌리기(4)는 이미 있는 화면으로 보낸다. */
  var WIZ = { video: '', path: '', real: true, src: '', name: '', scen: '' };

  function renderNewTest(cfg) {
    clear(view);
    view.appendChild(h('h1', { text: '새 시험 시작' }));
    view.appendChild(h('p', { class: 'sub',
      text: '돌리기 전에 등록하는 것은 전부 여기 있습니다 — 워크스페이스 · 영상 · 시나리오. '
            + '순서대로 거치면 됩니다. 특히 3단계(카메라 맞추기)를 빼먹으면 '
            + '남의 영상 값으로 판정하게 됩니다.' }));

    var scens = (cfg.scenarios || []).map(function (s) { return s.file; });
    var stopline = scens.filter(function (f2) { return f2.indexOf('stopline') === 0; });
    WIZ.src = WIZ.src || (stopline.indexOf('stopline_field.yaml') >= 0
                          ? 'stopline_field.yaml' : (stopline[0] || scens[0] || ''));

    function reload() { return get('/api/config').then(renderNewTest).catch(fail); }
    function say(j, okmsg) {
      hintEl.textContent = j.error ? ('오류: ' + j.error) : okmsg;
      setTimeout(function () { hintEl.textContent = ''; }, 5000);
      return !j.error;
    }
    function stepBox(n, title, desc, body) {
      var b = h('div', { class: 'card wizstep' }, [
        h('div', { class: 'k', text: n + '. ' + title }),
        h('div', { class: 'd', text: desc }),
      ]);
      (body || []).forEach(function (x) { if (x) b.appendChild(x); });
      return b;
    }

    // ── 0) 워크스페이스 붙이기 ──────────────────────────────────
    //   테스트베드를 복사하지 않는다 — 계약 파일만 하나 더 만든다.
    var ctb = h('tbody', {});
    (cfg.contracts || []).forEach(function (c) {
      var wi = h('input', { type: 'text', value: c.workspace || '', size: '34' });
      ctb.appendChild(h('tr', {}, [
        h('td', { class: 'mono', text: c.file }),
        h('td', {}, [wi]),
        h('td', {}, [c.attach ? h('span', { class: 'mut', text: 'attach' })
                     : (c.setup_ok ? h('b', { class: 'ok', text: '✅ 빌드됨' })
                        : h('b', { class: 'no',
                            text: c.ws_exists ? '⛔ 빌드 안 됨' : '⛔ 경로 없음' }))]),
        h('td', { class: 'mono mut', text: (c.nodes || []).join(' · ') }),
        h('td', {}, [h('button', { text: '경로 저장', onclick: function () {
          postJSON('/api/config/contract/workspace',
                   { file: c.file, workspace: wi.value.trim() })
            .then(function (j) { if (say(j, c.file + ' 갱신됨')) reload(); });
        } })]),
      ]));
    });
    var cn = h('input', { type: 'text', placeholder: '계약 이름 (예: other_ws)', size: '22' });
    var cw = h('input', { type: 'text', placeholder: '/home/me/other_ws', size: '32' });
    var ca = h('input', { type: 'checkbox' });

    view.appendChild(stepBox('0', '워크스페이스 붙이기 (새 대상일 때만)',
      '워크스페이스 하나 = 계약 파일 하나입니다. 테스트베드를 복사하지 않고 계약만 늘립니다.', [
        h('div', { class: 'tbl' }, [h('table', {}, [ctb])]),
        h('div', { class: 'framebar' }, [
          cn, cw,
          h('label', { class: 'mut', style: 'display:flex;gap:5px;align-items:center' },
            [ca, h('span', { text: 'attach (관찰만)' })]),
          h('button', { text: '계약 만들기', onclick: function () {
            postJSON('/api/config/contract', { name: cn.value.trim(),
                                               workspace: cw.value.trim(),
                                               attach: ca.checked })
              .then(function (j) { if (say(j, 'contracts/' + j.file + ' 생성됨')) reload(); });
          } })]),
        h('p', { class: 'help', html:
          '새로 만든 계약은 <b>노드·토픽이 TODO 로 비어 있습니다</b> — 대상 시스템을 '
          + '평소처럼 띄운 뒤 <b>환경 점검</b> 탭의 <code>discover</code> 가 돌고 있는 '
          + '토픽·타입·필드 배치를 읽어 채워 줍니다. ⛔ <b>빌드 안 됨</b> 이면 대상에서 '
          + '<code>colcon build</code> 부터.' }),
      ]));

    // ── 1) 영상 등록 ────────────────────────────────────────────
    var vtb = h('tbody', {});
    Object.keys(cfg.videos || {}).forEach(function (k) {
      var v = cfg.videos[k];
      vtb.appendChild(h('tr', {}, [
        h('td', { class: 'mono', text: k }),
        h('td', { class: 'mono mut', text: v.path }),
        h('td', { class: 'mono', text: v.exists
                  ? (v.frames + '프레임 · ' + v.w + '×' + v.h) : '' }),
        h('td', {}, [v.exists ? h('b', { class: 'ok', text: '✅' })
                              : h('b', { class: 'no', text: '⛔ 파일 없음' })]),
        h('td', {}, [h('button', { text: '삭제', onclick: function () {
          postJSON('/api/config/video', { name: k, delete: true })
            .then(function (j) { if (say(j, k + ' 삭제됨')) reload(); });
        } })]),
      ]));
    });
    var vName = h('input', { type: 'text', placeholder: '논리 이름 — 예: field_0820',
                             value: WIZ.video, size: '20' });
    var vPath = h('input', { type: 'text', placeholder: '/home/…/촬영본.mp4',
                             value: WIZ.path, size: '40' });
    var vReal = h('input', { type: 'checkbox', checked: WIZ.real ? 'checked' : null });
    var vOut2 = h('div', { class: 'mut' });

    view.appendChild(stepBox('1', '영상 등록',
      '실제 경로는 local.yaml 에 두고, 시나리오에는 논리 이름만 씁니다 — '
      + '그래야 다른 PC 로 옮겨도 시나리오를 고치지 않습니다.', [
        Object.keys(cfg.videos || {}).length
          ? h('div', { class: 'tbl' }, [h('table', {}, [vtb])]) : null,
        h('div', { class: 'framebar' }, [
          h('span', { class: 'mut', text: '이름' }), vName,
          h('span', { class: 'mut', text: '경로' }), vPath,
          h('button', { class: 'primary', text: '등록하고 열어 보기', onclick: function () {
            WIZ.video = vName.value.trim(); WIZ.path = vPath.value.trim();
            WIZ.real = vReal.checked;
            if (!WIZ.video || !WIZ.path) {
              vOut2.textContent = '이름과 경로를 모두 적으세요'; return;
            }
            vOut2.textContent = '등록 중…';
            postJSON('/api/config/video', { name: WIZ.video, path: WIZ.path })
              .then(function (d) {
                if (d.error) { vOut2.textContent = '오류: ' + d.error; return; }
                var v = d.video || {};
                var msg = v.exists
                  ? ('✅ ' + v.frames + '프레임 · ' + (v.size_mb || '?') + 'MB · '
                     + (v.fps ? v.fps.toFixed(1) + 'fps' : '?fps'))
                  : '⛔ 그 경로에 파일이 없습니다';
                vOut2.textContent = msg;
                //  다시 그리면 이 줄이 사라지므로 위쪽 알림줄에도 남긴다
                if (v.exists) { say({}, WIZ.video + ' 등록됨 — ' + msg); reload(); }
              }).catch(function (e) { vOut2.textContent = '오류: ' + e.message; });
          } })]),
        h('div', { class: 'framebar' }, [
          h('label', { class: 'toolflag' }, [vReal,
            h('span', { text: '실차 카메라로 찍은 영상이다' }),
            h('span', { class: 'mut',
              text: '— 아니면 여기서 맞춘 BEV 값을 실차에 쓰면 안 됩니다' })])]),
        vOut2,
      ]));

    // ── 2) 시나리오 만들기 ──────────────────────────────────────
    //   두 갈래다 — ★본 떠서★(있는 시험을 새 영상으로) 와 ★빈 틀★(새 워크스페이스).
    //   본 떠서가 기본이다: 판정과 그 근거 주석을 통째로 물려받는다.
    var how = h('select', {}, [
      h('option', { value: 'clone', text: '있는 시험을 본 떠서 (권장)' }),
      h('option', { value: 'new', text: '빈 틀에서 새로 (새 워크스페이스)' })]);
    var srcSel = h('select', {}, scens.map(function (f2) {
      return h('option', { value: f2, selected: f2 === WIZ.src ? 'selected' : null,
                           text: f2 });
    }));
    var sName = h('input', { type: 'text', placeholder: '새 시나리오 이름 — 예: sl_0820',
                             size: '22' });
    var sCont = h('select', {}, (cfg.contracts || []).map(function (c) {
      return h('option', { value: c.file, text: c.file });
    }));
    var sVid = h('select', {}, Object.keys(cfg.videos || {}).map(function (k) {
      return h('option', { value: k, selected: k === WIZ.video ? 'selected' : null, text: k });
    }));
    var sMode = h('select', {}, ['lockstep', 'realtime'].map(function (k) {
      return h('option', { value: k, text: k });
    }));
    var sStart = h('input', { type: 'number', placeholder: '0', style: 'width:100px' });
    var sLimit = h('input', { type: 'number', placeholder: '0 = 전체', style: 'width:110px' });
    var sOut = h('div', { class: 'mut' });
    var cloneBar = h('div', { class: 'framebar' }, [
      h('span', { class: 'mut', text: '본' }), srcSel,
      h('span', { class: 'mut', text: '— 이 시험의 판정을 그대로 물려받습니다' })]);
    var newBar = h('div', { class: 'framebar' }, [
      h('span', { class: 'mut', text: '계약' }), sCont,
      h('span', { class: 'mut', text: '모드' }), sMode]);
    function syncHow() {
      cloneBar.style.display = how.value === 'clone' ? '' : 'none';
      newBar.style.display = how.value === 'new' ? '' : 'none';
    }
    how.addEventListener('change', syncHow);
    syncHow();

    view.appendChild(stepBox('2', '시나리오 만들기',
      '무엇을 어떻게 돌릴지입니다. 본 떠서 만들면 판정 기준과 그 근거 주석을 '
      + '그대로 물려받습니다. 구간은 비워 두고(전체) 3·4단계에서 좁히면 됩니다.', [
        h('div', { class: 'framebar' }, [
          how, h('span', { class: 'mut', text: '새 이름' }), sName,
          h('span', { class: 'mut', text: '영상' }), sVid,
          h('button', { class: 'primary', text: '만들기', onclick: function () {
            var nm = sName.value.trim();
            if (!nm) { sOut.textContent = '새 시나리오 이름을 적으세요'; return; }
            if (!sVid.value) { sOut.textContent = '먼저 1단계에서 영상을 등록하세요'; return; }
            var st = sStart.value ? Number(sStart.value) : 0;
            var li = sLimit.value ? Number(sLimit.value) : 0;
            if (how.value === 'new') {
              postJSON('/api/config/scenario', {
                name: nm, contract: sCont.value, video: sVid.value,
                mode: sMode.value, start: st, limit: li,
              }).then(function (d) {
                if (d.error) { sOut.textContent = '오류: ' + d.error; return; }
                WIZ.scen = d.file; WIZ.name = nm;
                sOut.textContent = '✅ scenarios/' + d.file + ' 를 만들었습니다 '
                                   + '(빈 틀입니다 — checks: 를 채워야 판정이 생깁니다)';
                reload();
              }).catch(function (e) { sOut.textContent = '오류: ' + e.message; });
              return;
            }
            //  ★실차 카메라 여부는 시나리오 머리말에 박아 둔다★ — 여기서 맞춘 BEV 값을
            //  실차에 쓸 수 있는지가 그 한 줄에 걸린다(§13.11).
            postJSON('/api/config/scenario/clone', {
              src: srcSel.value, name: nm, video: sVid.value, start: st, limit: li,
              note: '영상 ' + sVid.value + ' ('
                    + ((cfg.videos || {})[sVid.value] || {}).path + ')\n'
                    + '실차 카메라로 촬영: '
                    + (vReal.checked ? '예' : '★아니오 — BEV 값을 실차에 쓰지 말 것★'),
            }).then(function (d) {
              if (d.error) { sOut.textContent = '오류: ' + d.error; return; }
              WIZ.scen = d.file; WIZ.name = nm;
              sOut.textContent = '✅ scenarios/' + d.file + ' 를 만들었습니다 ('
                                 + d.from + ' 의 판정을 그대로 물려받았습니다)';
              reload();
            }).catch(function (e) { sOut.textContent = '오류: ' + e.message; });
          } })]),
        cloneBar, newBar,
        h('div', { class: 'framebar' }, [
          h('span', { class: 'mut', text: 'start' }), sStart,
          h('span', { class: 'mut', text: 'limit' }), sLimit,
          h('span', { class: 'mut', text: '(비우면 영상 전체)' })]),
        sOut,
        WIZ.scen ? h('p', { class: 'help', html:
          '만든 것: <code>scenarios/' + WIZ.scen + '</code>' }) : null,
      ]));

    // ── 3) 카메라 맞추기 ────────────────────────────────────────
    view.appendChild(stepBox('3', '카메라 맞추기 (STOPLINE_TEST 단계 2)',
      '사다리꼴 → 범퍼선 → 두 문턱 순서로. ★«워크스페이스 기본값 불러오기» 로 '
      + '노드가 선언한 값에서 출발하세요★ — 그러면 실차와 같은 자리에서 시작합니다.', [
        h('div', { class: 'framebar' }, [
          h('a', { class: 'gobtn',
                   href: '#/calib' + (WIZ.scen ? '?s=' + encodeURIComponent(WIZ.scen) : ''),
                   text: '카메라 보정으로 ›' }),
          h('span', { class: 'mut',
            text: '맞춘 뒤 «저장» → local.yaml (또는 이 시나리오)' })]),
        h('p', { class: 'help', html:
          '자로 재야 하는 것은 두 가지입니다 — <b>범퍼선</b>(앞범퍼 바로 앞 노면에 테이프)과 '
          + '<b>px2m</b>(BEV 에서 실측 길이를 아는 두 점). 이 둘 없이는 두 문턱을 미터로 '
          + '환산할 수 없습니다.' }),
      ]));

    // ── 4) 돌리고 보기 ─────────────────────────────────────────
    view.appendChild(stepBox('4', '돌리고 보기',
      '먼저 «정지선이 잡히는가»(관문)를 봅니다. 잡히지 않으면 그 뒤 단계는 의미가 없습니다.', [
        h('div', { class: 'framebar' }, [
          h('a', { class: 'gobtn', href: '#/exec', text: '테스트 실행으로 ›' }),
          h('a', { class: 'gobtn', href: '#/runs', text: '실행 기록 ›' })]),
        h('p', { class: 'help', html:
          '실행 화면은 <b>고르고 돌리기만</b> 합니다 — 무엇이 쓰일지(계약·워크스페이스·'
          + '영상 파일·구간·덮어쓰는 값)를 먼저 펼쳐 보여 주고, ⛔ 가 있으면 버튼이 잠깁니다. '
          + '파라미터는 그 화면의 <b>«덮어쓰는 값»</b> 에서 고쳐 다시 돌립니다.' }),
        h('p', { class: 'help', html:
          '결과에서 순서대로 볼 것 — ① <b>프레임 탐색 → «정지선 검출»</b> 로 '
          + '<b>정말 정지선을 잡았는지</b> 눈으로 (차체·횡단보도 오검출) '
          + '② 요약의 <b>단계 전이 표</b> 로 0단→1단→2단이 언제·어느 거리에서 '
          + '③ <b>시각화</b> 에서 자홍색 거리선이 노면의 정지선과 겹치는지.' }),
      ]));

    view.appendChild(h('p', { class: 'help', html:
      '⚠️ <b>지그 시나리오와 섞지 마세요.</b> <code>stopline_demo_*</code> 는 '
      + '<code>track_record.mp4</code> 전용이고 합성 목업·그 영상용 문턱이 박혀 있습니다. '
      + '새 영상은 <code>stopline_field.yaml</code> 를 본으로 쓰세요.' }));
  }

  /* ── 도구 : 터미널에서 되는 것을 전부 여기서 ──────────────────────
   * 폼도 화이트리스트도 서버의 COMMANDS 명세 하나에서 나온다. 화면에 인자를
   * 따로 적어 두면 CLI 에 인자가 늘어도 웹은 모르고, 반대로 화면에만 있는
   * 인자는 서버가 거부한다 — 그래서 여기에는 인자 이름이 하나도 없다. */
  var TOOLS = { pick: '', vals: {}, timer: null };

  /* ── 터널 : 이 로컬 앱을 인터넷으로 (cloudflared) ────────────────
   * 정적 사이트는 읽기 전용이다. 진짜 편집(삭제·실행·보정)을 남이 하려면
   * ★서버 자신★을 열어야 한다. 서버는 ros2 run 을 띄우므로 토큰이 필수 —
   * 서버(start_tunnel)가 토큰 없이는 시작을 거부한다. */
  function tunnelPanel() {
    var box = h('div', { class: 'livebox', style: 'margin:0 0 16px' });
    view.appendChild(box);
    function draw(st) {
      clear(box);
      box.appendChild(h('div', { class: 'livehd' }, [
        h('b', { text: st.running ? '● 터널 열림' : '○ 터널' }),
        h('span', { class: 'mut',
          text: '이 앱을 인터넷으로 — 삭제·실행·보정을 원격에서 (읽기 전용 아님)' }),
      ]));
      if (!st.have_token) {
        box.appendChild(h('p', { class: 'help',
          html: '⚠ <code>TB_WEB_TOKEN</code> 이 없어 열 수 없습니다. 서버는 명령을 '
            + '실행하므로 인증 없이 열면 위험합니다. 터미널에서:<br>'
            + '<code>TB_WEB_TOKEN=아무비밀번호 python3 -m tb.run web</code> 로 다시 띄운 뒤 '
            + '이 화면을 새로고침하세요.' }));
        return;
      }
      if (!st.have_cloudflared) {
        box.appendChild(h('p', { class: 'help',
          html: '⚠ <code>cloudflared</code> 가 없습니다. 설치 후 다시:<br>'
            + '<code>cloudflared</code> — developers.cloudflare.com 의 설치 안내 참고.' }));
        return;
      }
      if (st.running && st.url) {
        box.appendChild(h('div', { class: 'framebar' }, [
          h('span', { class: 'mut', text: '공개 주소' }),
          h('a', { href: st.url, target: '_blank', rel: 'noopener',
                   class: 'mono', text: st.url }),
        ]));
        box.appendChild(h('p', { class: 'help',
          text: '이 주소 + 비밀번호(TB_WEB_TOKEN)를 함께 알려 주세요. 브라우저가 '
            + '로그인 창을 띄웁니다 (사용자명은 아무거나, 비번=토큰). '
            + '끄면 주소는 즉시 죽습니다.' }));
        box.appendChild(h('button', { class: 'danger', text: '터널 끄기',
          onclick: function () {
            post('/api/tunnel', { action: 'stop' }).then(function () { poll(); });
          } }));
      } else if (st.running) {
        box.appendChild(h('div', { class: 'framebar' },
          [spinner('주소 받는 중…'),
           h('button', { text: '끄기', onclick: function () {
             post('/api/tunnel', { action: 'stop' }).then(function () { poll(); }); } })]));
      } else {
        box.appendChild(h('button', { class: 'primary', text: '터널 켜기',
          onclick: function () {
            post('/api/tunnel', { action: 'start',
                                  port: Number(location.port) || 8770 })
              .then(function (j) {
                if (j && j.error) hintEl.textContent = '오류: ' + j.error;
                poll();
              });
          } }));
      }
    }
    function post(url, body2) {
      return fetch(url, { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body2) }).then(function (r) { return r.json(); });
    }
    function poll() { get('/api/tunnel').then(draw).catch(function () {}); }
    poll();
    var t = setInterval(poll, 3000);
    var prev = window.__stopPoll;
    window.__stopPoll = function () { clearInterval(t); if (prev) prev(); };
  }

  function renderTools(spec, want) {
    clear(view);
    if (TOOLS.timer) { clearInterval(TOOLS.timer); TOOLS.timer = null; }
    var cmds = spec.commands || [];
    var choices = spec.choices || {};

    view.appendChild(h('h1', { text: '도구' }));
    view.appendChild(h('p', { class: 'sub',
      text: '터미널에서 쓰는 명령을 그대로 씁니다. 고르면 그 명령이 받는 인자만 물어보고, '
            + '실제로 어떤 명령줄이 되는지 아래에 그대로 보여 줍니다.' }));

    tunnelPanel();

    var pick = want || TOOLS.pick || (cmds.length ? cmds[0].id : '');
    if (!cmds.filter(function (c) { return c.id === pick; }).length) {
      pick = cmds.length ? cmds[0].id : '';
    }
    TOOLS.pick = pick;

    var listBox = h('div', { class: 'toollist' });
    var formBox = h('div', { class: 'toolform' });
    var cmdBox = h('div', { class: 'cli' });
    var runBar = h('div', { class: 'framebar' });
    var outBox = h('div', {});

    cmds.forEach(function (c) {
      listBox.appendChild(h('button', {
        class: 'toolbtn' + (c.id === pick ? ' on' : ''),
        onclick: function () { location.hash = '/tools/' + c.id; } }, [
        h('b', { text: c.title }),
        h('span', { class: 'mono mut', text: c.id }),
      ]));
    });

    var cur = cmds.filter(function (c) { return c.id === pick; })[0];

    /* 값 저장소 — 명령을 오가도 입력한 값이 남아 있게 명령별로 따로 둔다 */
    TOOLS.vals[pick] = TOOLS.vals[pick] || {};
    var V = TOOLS.vals[pick];

    function optionsFor(a) {
      var src = (choices[a.src] || []).slice();
      return src;
    }

    function field(a, key) {
      var t = a.type;
      if (t === 'flag') {
        var cb = h('input', { type: 'checkbox' });
        cb.checked = !!V[key];
        cb.addEventListener('change', function () { V[key] = cb.checked; sync(); });
        return h('label', { class: 'toolopt' }, [cb, h('span', { text: a.flag })]);
      }
      var inp;
      if (t === 'choice') {
        inp = h('select', {}, [h('option', { value: '', text: '(안 씀)' })].concat(
          optionsFor(a).map(function (o) { return h('option', { value: o, text: o }); })));
      } else if (t === 'run') {
        inp = h('select', {}, [h('option', { value: '', text: '(안 씀)' })].concat(
          (choices.runs || []).map(function (o) { return h('option', { value: o, text: o }); })));
      } else {
        inp = h('input', { type: (t === 'int' || t === 'float') ? 'number' : 'text',
                           step: t === 'float' ? 'any' : (t === 'int' ? '1' : null),
                           placeholder: a.repeat ? '쉼표로 여러 개' : '' });
      }
      inp.value = V[key] == null ? '' : V[key];
      inp.className = 'wherebox';
      inp.addEventListener(t === 'choice' || t === 'run' ? 'change' : 'input',
        function () { V[key] = inp.value; sync(); });
      return h('div', { class: 'toolopt' }, [
        h('label', { class: 'mono', text: a.flag || a.name }), inp,
        a.help ? h('span', { class: 'mut', text: a.help }) : null,
      ]);
    }

    function argv() {
      var out = [];
      (cur.pos || []).forEach(function (pp, i) {
        var v = (V['pos' + i] || '').trim();
        if (v) out.push(v);
      });
      (cur.args || []).forEach(function (a) {
        var v = V[a.flag];
        if (a.type === 'flag') { if (v) out.push(a.flag); return; }
        v = (v == null ? '' : String(v)).trim();
        if (!v) return;
        if (a.repeat) {
          v.split(',').forEach(function (one) {
            one = one.trim();
            if (one) { out.push(a.flag); out.push(one); }
          });
          return;
        }
        out.push(a.flag);
        out.push((a.prefix && v.indexOf('/') < 0) ? a.prefix + v : v);
      });
      return out;
    }
    function sync() {
      cmdBox.textContent = '$ python3 -m ' + (cur.module || []).join(' ') + ' '
                           + argv().join(' ');
    }

    function drawForm() {
      clear(formBox);
      formBox.appendChild(h('p', { class: 'help', text: cur.desc || '' }));
      (cur.pos || []).forEach(function (pp, i) {
        var key = 'pos' + i;
        var inp;
        if (pp.type === 'run') {
          inp = h('select', {}, [h('option', { value: '', text: '(고르세요)' })].concat(
            (choices.runs || []).map(function (o) {
              return h('option', { value: o, text: o }); })));
        } else {
          inp = h('select', {}, [h('option', { value: '', text: '(고르세요)' })].concat(
            (choices.baselines || []).concat(choices.runs || []).map(function (o) {
              return h('option', { value: o, text: o }); })));
        }
        inp.className = 'wherebox';
        inp.value = V[key] || '';
        inp.addEventListener('change', function () { V[key] = inp.value; sync(); });
        formBox.appendChild(h('div', { class: 'toolopt' }, [
          h('label', { class: 'mono', text: pp.name
            + (pp.required ? ' *' : '') }), inp,
          h('span', { class: 'mut', text: pp.help || '' })]));
      });
      var flags = (cur.args || []).filter(function (a) { return a.type === 'flag'; });
      var rest = (cur.args || []).filter(function (a) { return a.type !== 'flag'; });
      rest.forEach(function (a) { formBox.appendChild(field(a, a.flag)); });
      if (flags.length) {
        formBox.appendChild(h('div', { class: 'toolflags' },
          flags.map(function (a) {
            var cb = h('input', { type: 'checkbox' });
            cb.checked = !!V[a.flag];
            cb.addEventListener('change', function () { V[a.flag] = cb.checked; sync(); });
            return h('label', { class: 'toolflag', title: a.help || '' },
                     [cb, h('span', { class: 'mono', text: a.flag }),
                      h('span', { class: 'mut', text: a.help || '' })]);
          })));
      }
      sync();
    }

    function drawRunBar() {
      clear(runBar);
      runBar.appendChild(h('button', { class: 'primary', text: '실행',
        onclick: function () {
          outBox.textContent = '';
          if (cur.quick) {
            outBox.appendChild(spinner('돌리는 중…'));
            var qs = argv().map(function (a) { return 'a=' + encodeURIComponent(a); });
            get('/api/quick/' + cur.id + (qs.length ? '?' + qs.join('&') : ''))
              .then(function (d) {
                clear(outBox);
                outBox.appendChild(h('div', { class: 'md',
                  text: (d.out || '(출력 없음)') }));
                say((d.rc === 0 ? '이상 없음' : '문제 있음') + ' (종료코드 ' + d.rc + ')');
              })
              .catch(function (e) { outBox.textContent = '오류: ' + e.message; });
            return;
          }
          postJob(cur.id, argv()).then(function (j) { if (!j.error) poll(); });
        } }));
      runBar.appendChild(h('button', { text: '값 지우기', onclick: function () {
        TOOLS.vals[pick] = {}; V = TOOLS.vals[pick]; drawForm(); } }));
      runBar.appendChild(h('span', { class: 'spacer' }));
      runBar.appendChild(h('button', { text: '중지', onclick: function () {
        fetch('/api/jobs/stop', { method: 'POST' })
          .then(function (r) { return r.json(); })
          .then(function (j) { say(j.error || '중지 신호를 보냈습니다'); });
      } }));
    }
    function say(m) {
      hintEl.textContent = m;
      setTimeout(function () { hintEl.textContent = ''; }, 4000);
    }

    function poll() {
      if (TOOLS.timer) clearInterval(TOOLS.timer);
      var tick = function () {
        if (location.hash.indexOf('/tools') < 0) {
          clearInterval(TOOLS.timer); TOOLS.timer = null; return;
        }
        get('/api/status').then(function (st) {
          clear(outBox);
          outBox.appendChild(jobBox(st));
          if (!st.running) { clearInterval(TOOLS.timer); TOOLS.timer = null; }
        }).catch(function () { clearInterval(TOOLS.timer); TOOLS.timer = null; });
      };
      tick();
      TOOLS.timer = setInterval(tick, 1200);
    }
    window.__stopPoll = function () {
      if (TOOLS.timer) { clearInterval(TOOLS.timer); TOOLS.timer = null; }
    };

    view.appendChild(h('div', { class: 'toolwrap' }, [
      listBox,
      h('div', {}, [formBox, cmdBox, runBar, outBox]),
    ]));

    view.appendChild(sectionTitle('여기 없는 명령'));
    view.appendChild(h('div', { class: 'tbl' }, [h('table', {}, [
      h('tbody', {}, (spec.omitted || []).map(function (o) {
        return h('tr', {}, [h('td', { class: 'mono', text: o[0] }),
                            h('td', { text: o[1] })]);
      })),
    ])]));

    drawForm();
    drawRunBar();
    get('/api/status').then(function (st) {
      if (st.running) poll();
    }).catch(function () {});
  }

  /* 실행 중 화면 — «테스트 실행» 과 «도구» 가 같은 것을 본다 */
  function jobBox(st) {
    var pr = st.progress || {};
    var frac = pr.total ? Math.min(1, (pr.pushed || 0) / pr.total) : 0;
    var head = h('div', { class: 'livehd' }, [
      h('b', { text: st.running ? '● 실행 중' : '○ 끝났습니다' }),
      h('span', { class: 'mut mono', text: st.cmd || (st.kind || '') }),
      h('span', { class: 'spacer' }),
      h('span', { text: (st.elapsed_s || 0).toFixed(1) + 's' }),
    ]);
    if (pr.total) {
      head.appendChild(h('span', { text: '  ' + (pr.pushed || 0) + '/' + pr.total +
        '  sync ' + (pr.sync || 0) + (pr.fps ? '  ' + pr.fps.toFixed(1) + 'fps' : '') }));
    }
    if (!st.running && st.returncode != null) {
      head.appendChild(h('b', { class: st.returncode === 0 ? 'ok' : 'no',
        text: '  종료코드 ' + st.returncode }));
    }
    var box = h('div', { class: 'livebox' }, [head]);
    if (pr.total) box.appendChild(h('div', { class: 'pbar' },
      [h('i', { style: 'width:' + (frac * 100).toFixed(1) + '%' })]));
    if (st.has_live && st.running) {
      box.appendChild(h('img', { class: 'liveimg', alt: '라이브 화면',
        src: '/api/runs/' + encodeURIComponent(st.run) + '/live?t=' + Date.now() }));
    }
    if (st.run && !st.running) {
      box.appendChild(h('div', { class: 'framebar' }, [
        h('button', { class: 'primary', text: '결과 보기 →',
          onclick: function () { location.hash = '/run/' + st.run; } }),
        h('span', { class: 'mut mono', text: st.run }),
      ]));
    }
    if (st.log_tail) {
      box.appendChild(h('div', { class: 'md', style: 'margin-top:11px;max-height:280px',
        text: st.log_tail }));
    }
    return box;
  }

  /* ── 사용방법 : 오프라인에서도 보이는 안내 ─────────────────────── */
  function renderHelp() {
    clear(view);
    view.appendChild(h('h1', { text: '사용 안내' }));
    view.appendChild(h('p', { class: 'sub',
      text: '워크스페이스와 영상을 고르는 것부터 결과를 판단하는 것까지.' }));

    function sec(t) { return h('h2', { text: t }); }
    function para(t) { return h('p', { class: 'help', html: t }); }
    function steps(items) {
      return h('ol', { class: 'helpsteps' }, items.map(function (it) {
        return h('li', {}, [h('b', { text: it[0] }), h('span', { html: it[1] })]);
      }));
    }

    /*  정적 사이트로 온 사람은 ★이게 앱인지 리포트인지★ 부터 모른다.
     *  아래 §0~ 는 전부 "돌리는 법" 이라 먼저 무엇을 보고 있는지를 말한다. */
    if (STATIC) {
      view.appendChild(sec('이 사이트는 무엇인가'));
      view.appendChild(para(
        'ROS 2 카메라 인지 노드를 <b>밖에서</b> 시험한 결과를 그대로 굳혀 놓은 것이다. ' +
        '원본 영상을 이미지 토픽으로 밀어 넣고 나오는 토픽을 전부 기록해 ' +
        '지표 · 불변식 · 회귀 비교로 판정했다. <b>판정은 이미 끝나 있고</b> ' +
        '이 화면은 그 결과를 읽을 뿐이다 — 여기서 다시 계산하지 않는다.'));
      view.appendChild(para(
        '실행 · 카메라 보정 · 도구 · 환경 점검은 없다. ' +
        'ROS 2 노드를 서브프로세스로 띄우고 영상을 읽어야 하는 일이라 ' +
        '정적 호스팅에서는 할 수 없다. <b>직접 돌리면 전부 있다.</b>'));

      view.appendChild(sec('내 머신에서 돌려 보려면'));
      view.appendChild(steps([
        ['받는다 ', '<code>git clone ' + (REPO || '<저장소>') + '</code> — ' +
                    'ROS 2 워크스페이스 <b>밖</b>에 둔다(<code>COLCON_IGNORE</code> 가 있다).'],
        ['환경 ', '<code>source /opt/ros/humble/setup.bash</code>. ' +
                  '외부 파이썬 의존성은 없다 — 표준 라이브러리와 워크스페이스가 이미 쓰는 것뿐이다.'],
        ['이 머신의 경로 ', '<code>cp local.yaml.example local.yaml</code> 뒤 영상 · 가중치 경로를 채운다. ' +
                            '시나리오에는 절대경로를 쓰지 않는다.'],
        ['띄운다 ', '<code>python3 -m tb.run app</code> — 같은 화면이 서버와 함께 열린다.'],
        ['자기 노드에 붙이려면 ', '<code>contracts/</code> 에 계약 YAML 을 하나 더 만든다. ' +
                                  '토픽명 · 필드 배치 · 노드명은 전부 거기 있고 <code>tb/*.py</code> 는 그대로 둔다.'],
      ]));
      view.appendChild(para(
        '자세한 설계와 절차는 저장소의 <code>README.md</code> 에 있다' +
        (REPO ? ' — <a href="' + REPO + '" target="_blank" rel="noopener">' + REPO + '</a>' : '') +
        '.'));
      view.appendChild(sec('아래는 전체 기능 안내 — 직접 돌릴 때의 이야기다'));
    }

    /*  ★한 흐름으로 보이는 순서★ 아래 §0~§11 은 화면별 설명이라, 처음 켠 사람이
     *  "그래서 뭘 먼저 누르나" 를 알 수 없었다. 그 답을 맨 위에 둔다. */
    view.appendChild(sec('처음 켰다면 — 이 순서대로'));
    view.appendChild(h('div', { class: 'md', text:
      '  ┌ 첫 설정 (한 번) ───────────────────────────────────┐\n' +
      '    «새 시험 시작»  0 워크스페이스 → 1 영상 → 2 시나리오\n' +
      '                    3 카메라 맞추기 → 4 돌리기\n' +
      '  └────────────────────────────────────────────────────┘\n' +
      '  ┌ 평소 루프 (매번) ──────────────────────────────────┐\n' +
      '    대상 코드 수정\n' +
      '        ↓\n' +
      '    «테스트 실행» → [워크스페이스 빌드] → [환경 점검] → [실행]\n' +
      '        ↓\n' +
      '    결과 판단 ① 성립했나 ② 정말 봤나(프레임 탐색) ③ 언제 물었나\n' +
      '              ④ 받는 쪽이 썼나 ⑤ 어제와 달라졌나\n' +
      '        ↓\n' +
      '    파라미터만 고칠 때는 «덮어쓰는 값» 에서 고쳐 바로 다시 실행 (빌드 불필요)\n' +
      '  └────────────────────────────────────────────────────┘' }));
    view.appendChild(para(
      '원칙 하나만 알면 헤맬 일이 없다 — <b>파일을 쓰는 일은 «새 시험 시작», ' +
      '돌리는 일은 «테스트 실행»</b>. 등록 폼이 두 화면에 있으면 같은 것을 두 벌 짜게 되고 ' +
      '한쪽만 고쳐진다. 그래서 <code>local.yaml</code>·<code>contracts/</code>·' +
      '<code>scenarios/</code> 를 건드리는 폼은 전부 «새 시험 시작» 에만 있고, ' +
      '실행 화면은 고르고 돌리기만 한다.'));
    view.appendChild(para(
      '<b>어느 화면도 대상 워크스페이스의 파일은 고치지 않는다.</b> ' +
      '«워크스페이스 빌드» 만이 예외로 대상의 <code>build/</code>·<code>install/</code> 에 쓴다 — ' +
      '그래서 어디에 무엇을 빌드할지는 <b>계약</b>이 정하고 임의 경로를 받지 않는다.'));

    view.appendChild(sec('0. 구조 — 파일 세 종류가 전부다'));
    view.appendChild(para(
      '테스트베드는 <b>워크스페이스 밖</b>에 따로 있고(현재 <code>~/cam_testbed</code>), ' +
      '대상은 <b>계약 파일</b>로만 안다. 그래서 워크스페이스를 여러 개 붙여도 ' +
      '<b>테스트베드를 복사하지 않는다</b> — 계약만 하나 더 만든다.'));
    view.appendChild(h('div', { class: 'md', text:
      '~/cam_testbed/                        ← 테스트베드 한 벌\n' +
      '  contracts/white_camera.yaml         "어느 워크스페이스의 어떤 노드인가"\n' +
      '  contracts/other_ws.yaml             워크스페이스를 더 붙이면 여기에 추가\n' +
      '  scenarios/regression.yaml           "어느 영상을 어떻게 돌릴 것인가"\n' +
      '  local.yaml                          "이 PC 에서 그 영상이 어디 있는가" (공유 X)\n' +
      '  runs/                               결과' }));
    view.appendChild(h('table', { class: 'tbl' }, [h('tbody', {}, [
      h('tr', {}, [h('td', { html: '<b>계약</b>' }),
        h('td', { html: '대상 = <b>워크스페이스 + 노드 + 토픽 + 신호</b>. ' +
          '워크스페이스 하나에 계약 하나.' })]),
      h('tr', {}, [h('td', { html: '<b>시나리오</b>' }),
        h('td', { html: '실행 방법 = <b>영상 + 구간 + 재생모드 + 파라미터</b>. ' +
          '같은 계약에 시나리오를 여러 개 둘 수 있다(회귀용 400프레임, 실시간용 전체 …).' })]),
      h('tr', {}, [h('td', { html: '<b>local.yaml</b>' }),
        h('td', { html: '<b>이 PC 에서만 맞는 것</b> = 영상 실제 경로, 가중치 경로. ' +
          'git 에 안 올린다. 그래서 시나리오는 다른 PC 에서도 그대로 돈다.' })]),
    ])]));

    view.appendChild(sec('1. 등록하기 — «새 시험 시작» 탭'));
    view.appendChild(para(
      '<b>파일을 건드리는 일은 전부 «새 시험 시작» 탭에 있다</b> — 워크스페이스(0) · ' +
      '영상(1) · 시나리오(2). 실행 화면은 있는 것을 고르고 돌리기만 한다. ' +
      '파일을 직접 열어도 되고 화면에서 해도 된다 — 같은 결과다.'));
    view.appendChild(steps([
      ['① 영상 등록', '<b>별칭</b>과 <b>실제 경로</b>를 넣고 <code>영상 등록</code>. ' +
       '등록할 때 파일을 실제로 열어 보고 <b>프레임 수·해상도·fps</b> 를 확인해 준다 — ' +
       '경로 오타나 깨진 파일이면 그 자리에서 거절한다. ' +
       '<code>local.yaml</code> 의 <code>videos:</code> 에 기록되고 <b>주석은 보존된다</b>.'],
      ['② 워크스페이스 등록', '<b>계약 이름</b>과 <b>워크스페이스 경로</b>를 넣고 ' +
       '<code>계약 만들기</code>. <code>install/setup.bash</code> 가 없으면 ' +
       '"빌드 먼저"라고 알려 준다. 기존 계약의 경로만 바꾸려면 위 표에서 고치고 ' +
       '<code>경로 저장</code>.'],
      ['③ 계약 채우기', '새로 만든 계약은 <b>노드·토픽이 TODO 로 비어 있다</b>. ' +
       '대상 시스템을 평소처럼 띄운 뒤 <b>환경 점검</b> 탭의 <code>discover</code> 를 누르면 ' +
       '돌고 있는 토픽·타입·필드 배치를 읽어 <code>signals:</code> 초안을 뽑아 준다. ' +
       '숫자 배열은 의미를 알 수 없으니 이름만 사람이 붙인다 ' +
       '(<code>nav_state_data0</code> → <code>cte_m</code>).'],
      ['④ 시나리오 만들기', '두 갈래다. <b>본 떠서</b>는 있는 시험을 새 영상으로 옮기는 것이라 ' +
       '<b>판정과 그 근거 주석을 그대로 물려받는다</b> — 새 영상은 이쪽이다. ' +
       '<b>빈 틀</b>은 새 워크스페이스를 붙일 때. ' +
       '<b>lockstep</b> 은 한 프레임씩 밀어 넣어 결정적이라 회귀 비교용, ' +
       '<b>realtime</b> 은 실제 fps 로 밀어 타이밍·유실을 보는 용도다.'],
    ]));
    view.appendChild(para(
      '<b>영상을 새로 찍었을 때</b>는 ①만 하면 된다 — 같은 별칭에 새 경로를 등록하면 ' +
      '모든 시나리오가 자동으로 새 영상을 쓴다. 단 <b>기준(baseline)은 영상에 묶여 있으므로</b> ' +
      '영상이 바뀌면 회귀 비교에 경고가 뜨고, 새 기준을 등록해야 한다.'));

    view.appendChild(sec('2. 테스트 실행 탭 — 돌리기 전에 무엇이 쓰이는지 보여 준다'));
    view.appendChild(para(
      '시나리오를 고르면 <b>그게 실제로 무엇을 쓸지 풀어서</b> 보여 준다. ' +
      '"왜 안 돌지"를 로그에서 찾을 일이 없도록 실행 전에 다 드러낸다.'));
    view.appendChild(h('table', { class: 'tbl' }, [h('tbody', {}, [
      h('tr', {}, [h('td', { html: '<b>계약</b>' }),
        h('td', { html: '어느 계약이 뽑혔는지. 시나리오의 <code>contract:</code> → ' +
          '<code>local.yaml</code> 의 <code>default_contract</code> 순으로 정해진다.' })]),
      h('tr', {}, [h('td', { html: '<b>워크스페이스</b>' }),
        h('td', { html: '경로와 <b>빌드 여부</b>. ⛔ 면 «워크스페이스 빌드» 를 먼저 누른다. ' +
          '⚠ <b>소스가 빌드보다 새롭다</b> 는 mtime 만 비교한 것이라 ' +
          '<b>파이썬만 고쳤을 때도 뜬다</b> — 그때는 그냥 돌려도 된다(심볼릭 링크라 ' +
          '고친 코드가 그대로 돈다).' })]),
      h('tr', {}, [h('td', { html: '<b>띄울 노드</b>' }),
        h('td', { html: '실제로 실행될 <code>ros2 run …</code> 줄. 여기 없는 노드는 안 뜬다.' })]),
      h('tr', {}, [h('td', { html: '<b>영상</b>' }),
        h('td', { html: '<b>별칭 → 실제 파일 → 프레임 수·해상도</b>. ' +
          '등록 안 된 이름이면 ⚠ 가 붙는다.' })]),
      h('tr', {}, [h('td', { html: '<b>구간·재생</b>' }),
        h('td', { html: 'start~limit 과 모드. <b>구간이 영상보다 길면</b> 미리 경고한다.' })]),
      h('tr', {}, [h('td', { html: '<b>덮어쓰는 값</b>' }),
        h('td', { html: '시나리오·local.yaml 이 노드 기본 파라미터 위에 덮어쓰는 것. ' +
          '가중치 경로가 여기 나온다. <b>펴면 그 자리에서 고쳐 저장할 수 있다</b> — ' +
          '고치고 다시 돌리는 고리를 화면 안에서 닫으려는 것이다(주석은 보존된다). ' +
          '단 <b>기준 비교에는 «조건이 다르다» 경고가 붙는다</b> — ' +
          '파라미터를 바꾼 것끼리는 «결과 비교» 로 런 대 런으로 본다.' })]),
    ])]));
    view.appendChild(para(
      '<b>⛔ 가 하나라도 있으면 실행 버튼이 잠긴다.</b> 못 돌 것을 돌려 놓고 ' +
      '30초 뒤에 실패를 보는 대신 지금 알려 준다. ⚠ 는 돌긴 하지만 알고 있어야 하는 것이다. ' +
      '맨 아래 <code>$</code> 줄은 <b>이 화면이 실제로 부를 명령</b>이라 터미널에 그대로 붙여도 된다.'));

    view.appendChild(sec('3. 실행 · 진행 상황'));
    view.appendChild(steps([
      ['워크스페이스 빌드', '<b>파이썬 코드만 고쳤다면 누를 필요가 없다</b> — ' +
       '<code>--symlink-install</code> 이라 <code>ros2 run</code> 이 읽는 파일이 ' +
       '곧 <code>src/</code> 의 그 파일이다(<code>build/&lt;pkg&gt;/&lt;pkg&gt;</code> 가 ' +
       'src 를 가리키는 심볼릭 링크). <b>빌드가 필요한 때는 넷</b>: 새 실행파일' +
       '(<code>setup.py</code> 의 <code>entry_points</code>) · <code>package.xml</code> 의 ' +
       '의존성 · C++ 패키지 · <b>처음 한 번</b>. 빌드할 곳과 패키지는 <b>계약</b>이 정한다 ' +
       '(<code>--packages-up-to</code> 라 의존 패키지까지 함께 선다).'],
      ['환경 점검', '<code>doctor</code> 를 그 자리에서 돌린다 — 계약·영상·가중치에 더해 ' +
       '<b>이름 정합</b>(체크가 가리키는 신호가 계약에 실제로 있는가)까지 본다. ' +
       '오타는 예전에 런을 다 돌린 뒤에야 ⚠ 로 드러났다.'],
      ['주입 검증만', '영상·YOLO 없이 <b>변환 수학만</b> 몇 초에 확인한다. ' +
       '입력을 직접 만들어 참값을 알고 있으므로 여기서 실패하면 인지가 아니라 <b>계산</b>이 틀린 것이다. ' +
       '<b>실행 전에 이걸 먼저 돌리는 습관을 권한다.</b>'],
      ['실행', '진행률 막대, <b>라이브 화면</b>(처리 중인 프레임), 로그가 1.2초마다 갱신된다. ' +
       '400프레임에 30초쯤. 끝나면 <code>결과 보기 →</code> 가 뜬다.'],
      ['태그', '같은 시나리오를 조건만 바꿔 여러 번 돌릴 때 실행 이름에 붙는 꼬리표다. ' +
       '<b>기준 비교는 태그가 아니라 시나리오 이름으로 갈린다</b> — 태그를 붙여도 회귀 비교는 그대로 된다.'],
      ['디버그 영상 기록', '대상 노드의 디버그 화면을 영상으로 남긴다. ' +
       '<b>결과 숫자는 바뀌지 않는다</b>(실측 확인). 원본과 같은 속도로 저장된다.'],
      ['중지', 'SIGINT 를 보낸다. 한 번에 한 작업만 돈다 — ROS 노드가 겹치면 서로를 방해한다.'],
    ]));

    view.appendChild(sec('4. 돌리기 전 환경 점검'));
    view.appendChild(para(
      '<b>환경 점검</b> 탭에서 <code>doctor</code> 를 돌린다. 가중치·영상·실행파일이 ' +
      '제자리에 있는지 확인한다. <code>selftest</code> 는 테스트베드 자신이 멀쩡한지 본다.'));

    view.appendChild(sec('5. 결과 판단 — 순서가 중요하다'));
    view.appendChild(para(
      '실행을 열면 <b>요약 · 시각화 · 상세 · 리포트 · 피드백</b> 다섯 탭이 있다. ' +
      '<b>위에서 아래로</b> 읽되, 위가 깨지면 아래는 볼 필요가 없다. ' +
      '표에 나오는 말이 헷갈리면 아래 <b>11. 단어집</b> 에 다 적어 두었다.'));
    view.appendChild(steps([
      ['① 실행이 성립했나', '<code>유실률</code> 0, <code>지연 p95</code> &lt; 400ms. ' +
       '아니면 여기서 멈춘다 — 아래 값을 못 믿는다.'],
      ['② 받는 쪽이 실제로 썼나', '<b>게이트 통과율</b>. 이게 최종 판정이다. ' +
       '낮으면 <b>병목 단계</b>를 본다 — 다른 데를 고쳐도 안 오른다.'],
      ['③ θ 는 쓸 만한가', '<code>θ 편향</code> 이 0 에서 멀면 헤딩이 한쪽으로 끌린다. ' +
       '<code>진동 대역 비중</code> 이 크면 카메라가 조향 떨림을 만든다.'],
      ['④ 왜 그런가', '<b>시각화</b> 탭에서 경로 영상을 본다. 차선을 놓쳤는지, ' +
       '봤는데 값이 틀렸는지가 눈으로 갈린다. 영상은 <b>원본과 같은 속도</b>로 만들어지고, ' +
       '천천히 볼 때는 <code>배속</code> 버튼(0.25×~2×)을 쓴다 — 다시 굽지 않는다.'],
      ['⑤ 어제와 달라졌나', '<b>리포트</b> 탭의 기준 비교. 같은 코드면 ' +
       '<code>max|Δ| = 0.0000</code> 이 나온다 — 차이가 있으면 전부 코드 탓이다.'],
    ]));

    view.appendChild(sec('6. 판단 기준은 어디서 오는가'));
    view.appendChild(para(
      '판정표의 기준값은 지어낸 것이 아니다. 대부분 <b>받는 쪽 노드가 실제로 쓰는 상수</b>에서 왔다 — ' +
      '예를 들어 <code>conf ≥ 0.35</code> 는 <code>gps_imu</code> 의 ' +
      '<code>CAM_HEAD_CONF_MIN</code> 그대로다. 그 임계값을 못 넘으면 ' +
      '<b>실제로 주행에 안 쓰인다</b>는 사실 진술이라 논쟁의 여지가 없다.'));

    view.appendChild(sec('7. 고칠 곳을 찾았으면'));
    view.appendChild(steps([
      ['프레임 탐색', '게이트 병목을 그대로 조건으로 넣으면 <b>고쳐야 할 프레임만</b> 모인다. ' +
       '행을 누르면 경로를 크게 본다.'],
      ['추출', '<code>이 조건으로 추출</code> 로 원본 프레임을 뽑아 라벨링에 쓴다(능동 학습).'],
      ['카메라 보정', '차선 폭이 실제 길이와 다르거나 BEV 가 기울면 <b>카메라 보정</b> 탭에서 ' +
       'IPM 4점을 맞춘다. <b>수직도</b>가 그 자다(직선 구간에서만 의미 있음).'],
      ['기준 등록', '결과가 납득되면 <code>기준으로 등록</code>. 이후 실행은 자동으로 비교된다.'],
    ]));

    view.appendChild(sec('8. 기록 정리 — 쌓인 실행을 다루는 법'));
    view.appendChild(para(
      '<b>실행 기록</b> 탭에서 검색·필터·정렬로 원하는 실행만 남긴 뒤, ' +
      '왼쪽 체크박스로 여러 건을 한꺼번에 다룬다. ' +
      '정리 정보는 결과 파일을 건드리지 않는다 — 고정·메모·태그는 ' +
      '<code>runs/_index.json</code> 한 파일에만 쌓인다.'));
    view.appendChild(steps([
      ['고정 (★)', '자주 보는 실행은 별을 눌러 <b>맨 위에 붙인다</b>. 정렬을 바꿔도 위에 남는다.'],
      ['메모 · 태그', '"폭 게이트 고친 뒤" 같은 한 줄을 붙여 둔다. 검색어에도 걸린다.'],
      ['삭제', '고른 실행을 <code>runs/_trash/</code> 로 <b>옮길 뿐</b> 지우지 않는다. ' +
       '<b>휴지통</b>에서 복원하거나, 디스크가 필요하면 거기서 완전히 지운다.'],
      ['분석 결과 없음', '상태 필터에서 <b>분석 결과 없음</b>만 골라 한 번에 치우면 목록이 깨끗해진다.'],
    ]));

    view.appendChild(sec('9. 피드백 — 결과를 코드 개선으로'));
    view.appendChild(para(
      '실행 상세의 <b>피드백</b> 탭에서 <code>피드백 만들기</code> 를 누르면, ' +
      '이 실행의 <b>잘된 점 · 안 좋은 점 · 병목 · 볼 곳</b>을 정리한 문서를 만든다. ' +
      '값과 판정은 전부 엔진이 만든 <code>summary.json</code> 을 그대로 옮긴 것이라 ' +
      '화면이 따로 판정하지 않는다.'));
    view.appendChild(steps([
      ['① 만들기', '필요하면 <b>이전 실행</b>을 골라 개선 전/후를 함께 담고, ' +
       '사람이 본 것을 메모에 적는다(선택). <code>runs/&lt;실행&gt;/feedback.md</code> 로도 저장된다.'],
      ['② 넘기기', '<code>전체 복사</code> 로 클로드 코드 대화창에 붙여넣거나, ' +
       '화면의 <code>claude "$(cat runs/…/feedback.md)"</code> 명령을 터미널에 붙여넣는다.'],
      ['③ 고친 뒤 다시', '코드를 고쳤으면 같은 시나리오를 다시 돌리고, ' +
       '새 실행의 피드백을 만들 때 <b>이전 실행</b>으로 방금 그 실행을 고른다 — ' +
       '무엇이 좋아지고 무엇이 나빠졌는지가 표로 나온다.'],
    ]));
    view.appendChild(para(
      '문서 마지막에는 <b>규칙</b>이 함께 실린다 — 테스트베드 코드를 건드리지 말 것, ' +
      '<b>임계값을 느슨하게 해서 통과시키지 말 것</b>, 통과하던 체크를 깨지 말 것. ' +
      '기준 자체가 이 차량·영상에 안 맞는다고 판단되면 근거를 먼저 말하도록 되어 있다.'));

    view.appendChild(sec('10. 알아 둘 것'));
    view.appendChild(h('ul', { class: 'helpul' }, [
      h('li', { html: '<b>lockstep</b> 은 완전히 결정적이다 — 두 번 실행하면 값이 정확히 같다. ' +
        '그래서 비교에서 뜨는 차이는 전부 코드 변경 탓이다.' }),
      h('li', { html: '<b>lockstep 과 realtime 은 서로 비교하면 안 된다.</b> ' +
        'EMA 가 보는 프레임 간격이 달라진다. 각자의 기준을 둔다.' }),
      h('li', { html: '<b>기준은 영상·구간에 묶여 있다.</b> 다른 영상으로 만든 기준과 비교하면 ' +
        '숫자는 나오지만 의미가 없다 — 조건이 다르면 경고가 뜬다.' }),
      h('li', { html: '이 웹앱은 <b>외부 의존성이 0</b>이다. 인터넷 없이 그대로 돈다. ' +
        '원격에서 볼 때만 <code>ssh -L 8770:localhost:8770 …</code>.' }),
      h('li', { html: '<b>영상이 검은 화면이면</b> 코덱 문제다. OpenCV 기본 <code>mp4v</code> 는 ' +
        '브라우저가 재생하지 못한다 — 서버가 처음 열 때 자동으로 H.264 로 변환한다. ' +
        '변환도 안 되면 화면에 이유가 뜬다(<code>sudo apt install ffmpeg</code>).' }),
    ]));

    /* 단어집 — 화면에 나오는 말을 한자리에 모아 둔다.
     * 새 용어를 화면에 쓰기 시작하면 ★여기에 한 줄 늘린다★. 뜻이 두 곳에
     * 따로 적히면 반드시 어긋나므로, 설명의 출처는 여기 하나로 둔다. */
    var GLOSSARY = [
      ['정지선 앞 정지 (white1)', [
        ['sl_px',
         '<b>이 시험의 판정값.</b> BEV(위에서 내려다본 화면)에서 <b>정지선 → 앞범퍼</b> ' +
         '픽셀 거리. <b>가까울수록 작다</b> · <code>-1</code> = 미검출 · ' +
         '<code>0</code> = 범퍼선 도달(또는 지나침).'],
        ['B1 · B2 (두 문턱)',
         '<code>sl_brake1_px</code>(1단 예비제동) · <code>sl_brake2_px</code>(2단 확정 정지). ' +
         '정지선이 B1 안으로 들어오면 부드럽게 줄이고, B2 안이면 세운다. ' +
         '<b>둘 다 실측값이다</b> — 기본값은 근거가 없다(절차서 단계 2).'],
        ['1단 예비제동',
         '리니어 1/3 행정 + 구동 차단. 실측 감속도 1.30 m/s². ' +
         '<b>1단을 물면 차는 스스로 기어가지 못한다</b> — 그래서 대기 상한이 필요하다.'],
        ['2단 확정 정지',
         '리니어 전행정. 실측 감속도 2.2~3.8 m/s² — 1초 안에 세우는 힘이다.'],
        ['대기 (sl_wait)',
         '빨간불은 확정됐는데 정지선이 아직 멀어 <b>아무 단계도 안 건</b> 구간. ' +
         '이 시험에서 <b>유일하게 참는 경우</b>다.'],
        ['놓침',
         '정지선을 봤다가 <code>sl_stale_s</code>(0.5초) 동안 못 본 것. ' +
         '접근하면 정지선은 차체에 가려 <b>반드시</b> 사라지므로, 그것을 ' +
         '「이미 선 위」로 읽고 즉시 2단이다.'],
        ['근접도 (tl_near)',
         '빨간 박스의 <b>높이[px]</b>. 「얼마나 가까운가」의 대리값이고, ' +
         '이 값이 게이트를 넘어야 RED, 못 넘으면 RED_FAR(멀다)다.'],
        ['범퍼행 (bev_bumper_y_px)',
         'BEV 에서 <b>거리 0 의 기준선</b>. 앞범퍼 바로 앞 노면에 테이프를 붙여 잰다. ' +
         '차체에 가려 안 보이면 BEV 높이보다 큰 값이 된다.'],
        ['합성 자극 (overlay)',
         '목업 신호등 그림을 화면에 얹는 것. 영상에 신호등이 없으면 정지선 추론이 ' +
         '아예 안 돌기 때문이다. <b>판정을 통과시키는 장치가 아니다</b> — ' +
         '노드의 YOLO 가 실제로 검출해야 아무 일이든 일어난다.'],
        ['단계 전이 표',
         '리니어 단계가 <b>언제·어느 거리에서</b> 올라갔는지. ' +
         '평균으로는 「언제」를 말할 수 없어서 따로 재는 표다.'],
        ['워크스페이스 기본값',
         '<b>노드가 스스로 선언한 값</b>(<code>ros2 param dump</code>). ' +
         '캘리브를 여기서 출발해야 실차와 같은 자리에서 시작한다.'],
        ['지그 (demo) 시나리오',
         '<code>stopline_demo_*</code> 는 <code>track_record.mp4</code> 전용이다. ' +
         '구간·목업 크기·문턱이 그 영상에서 뽑은 값이므로 <b>실차 판정에 쓰면 안 된다</b>.'],
      ]],
      ['결과를 읽는 말', [
        ['실질 기여율',
         '카메라가 낸 프레임 중 <b>받는 쪽이 게이트를 모두 통과시켜 실제로 쓴</b> 비율. ' +
         '이 앱에서 가장 중요한 숫자이고, 사실상 최종 판정이다.'],
        ['게이트',
         '받는 쪽이 그 값을 쓸지 버릴지 가르는 조건. 임계값은 지어낸 것이 아니라 ' +
         '<b>받는 쪽 코드의 상수를 그대로 옮겨 적은 것</b>이다.'],
        ['결과 행',
         '분석에 쓰인 출력 행 수. 넣은 프레임 수보다 적으면 그만큼 유실된 것이다.'],
        ['차선 인식률',
         '차선을 <b>본</b> 프레임의 비율. 봤다는 뜻이지 잘 봤다는 뜻은 아니다.'],
        ['유실률',
         '넣었는데 결과가 돌아오지 않은 비율. <code>lockstep</code> 이면 0 이어야 한다.'],
        ['지연 p95',
         '100번 중 95번은 이 시간 안에 결과가 나왔다는 뜻. ' +
         '400ms 를 넘으면 받는 쪽이 그 값을 버린다.'],
        ['불변식 체크',
         '어떤 실행에서든 지켜져야 하는 조건. <b>통과 · 실패 · 미판정</b> 셋으로 나온다.'],
        ['미판정',
         '값 자체가 없어서 판정할 수 없었던 체크. 실패와 다르다.'],
        ['기준',
         '앞으로의 실행을 견줄 결과 한 벌. <code>기준으로 등록</code> 으로 만든다. ' +
         '<b>영상·구간에 묶여 있어</b> 조건이 다르면 비교해도 의미가 없다.'],
        ['기준 대비',
         '기준과 값이 같으면 <b>PASS</b>, 달라졌으면 <b>DIFF</b>.'],
        ['θ 편향',
         '직진 구간에서 θ 가 0 에서 벗어난 정도. 0 이 아니면 헤딩이 한쪽으로 끌린다.'],
        ['진동 대역 비중',
         '조향 진동수 대역에 실린 힘. 크면 카메라가 조향 떨림을 만들고 있다.'],
        ['변화폭 p95',
         '이웃한 두 프레임 사이 변화량의 95 백분위. 값이 얼마나 튀는지 본다.'],
        ['플래그',
         '그 프레임에서 걸린 조건을 비트로 모아 둔 것. ' +
         '<code>CLEAN</code> 이면 아무것도 안 걸렸다.'],
      ]],
      ['신호 이름 — 그래프와 프레임 표에 나오는 것', [
        ['θ <span class="mut">theta_deg</span>',
         '차선을 기준으로 차량이 틀어진 각도. 받는 쪽이 헤딩 보정에 쓰는 <b>유일한 값</b>이다.'],
        ['cte',
         '차선 중심에서 옆으로 벗어난 거리 (cross track error).'],
        ['conf',
         '인지가 매긴 신뢰도. 원본 값이다.'],
        ['conf_eff',
         '게이트를 반영한 유효 신뢰도. <b>0 이면 받는 쪽이 카메라를 쓰지 않는다.</b>'],
        ['차선 폭',
         '좌우 차선 사이의 거리. 실제 길이와 다르면 <b>카메라 보정</b>부터 본다.'],
        ['중심선',
         '좌우 차선의 가운데. 차가 따라갈 경로다. 경로 영상에서 주황색.'],
      ]],
      ['무엇을 어떻게 돌리나', [
        ['계약',
         '<b>대상이 무엇인가.</b> 워크스페이스 + 노드 + 토픽 + 신호. ' +
         '워크스페이스 하나에 계약 하나.'],
        ['시나리오',
         '<b>어떻게 돌릴 것인가.</b> 영상 + 구간 + 재생 모드 + 파라미터. ' +
         '같은 계약에 여러 개를 둘 수 있다.'],
        ['local.yaml',
         '<b>이 PC 에서만 맞는 것.</b> 영상 실제 경로, 가중치 경로. git 에 올리지 않는다.'],
        ['별칭',
         '시나리오가 영상을 부르는 짧은 이름. 실제 경로는 <code>local.yaml</code> 에 있다.'],
        ['실행',
         '한 번 돌린 결과 한 벌. <code>runs/&lt;시각&gt;_&lt;태그&gt;_&lt;변형&gt;/</code> 에 남는다.'],
        ['변형',
         '같은 시나리오를 조건만 바꿔 돌린 것(좌우 반전 등).'],
        ['태그',
         '실행 이름에 붙이는 꼬리표. ' +
         '기준 비교는 태그가 아니라 <b>시나리오 이름</b>으로 갈린다.'],
        ['신호',
         '계약이 <b>토픽의 어느 필드를 무슨 이름으로 부를지</b> 정해 둔 것.'],
      ]],
      ['실행 방식', [
        ['lockstep',
         '한 프레임씩 밀어 넣고 결과를 기다린다. 완전히 결정적이라 <b>비교용</b>이다.'],
        ['realtime',
         '실제 fps 로 민다. 타이밍과 유실을 본다. ' +
         '<b>lockstep 결과와 서로 비교하면 안 된다.</b>'],
        ['attach',
         '노드를 띄우지 않고 이미 돌고 있는 시스템을 보기만 한다.'],
        ['주입 검증',
         '영상·YOLO 없이 <b>직접 만든 입력</b>으로 좌표 변환 계산만 확인한다. ' +
         '정답을 알고 있어서, 여기서 틀리면 인지가 아니라 계산이 틀린 것이다.'],
        ['섭동',
         '입력을 일부러 흔들어 결과가 얼마나 버티는지 보는 것.'],
      ]],
      ['카메라 보정', [
        ['IPM',
         '원근이 들어간 화면을 위에서 내려다본 것처럼 펴는 변환.'],
        ['BEV',
         '그렇게 편 결과 화면 (bird\'s eye view).'],
        ['사각형',
         'IPM 이 펼 영역을 정하는, 원본 화면 위의 네 점.'],
        ['px2m',
         'BEV 픽셀 하나가 실제로 몇 미터인가.'],
        ['수직도',
         'BEV 에서 차선이 수직에 얼마나 가까운지. <b>사각형이 잘 맞았는지 재는 자</b>다. ' +
         '직선 구간에서만 의미가 있다.'],
        ['왜곡보정',
         '렌즈 때문에 휜 화면을 펴는 것.'],
      ]],
      ['그 밖', [
        ['받는 쪽',
         '이 신호를 <b>받아서 쓰는 쪽</b>의 노드 (<code>gps_imu</code> 등). ' +
         '카메라가 아무리 잘 봐도 받는 쪽이 안 쓰면 주행에는 아무 기여가 없다.'],
        ['신호 경로 점검',
         '계약에 적어 둔 경로에서 값이 실제로 나왔는지 확인한 결과.'],
        ['능동 학습',
         '고쳐야 할 프레임만 골라 라벨링해 다시 학습시키는 방식. ' +
         '<b>프레임 탐색 → 이 조건으로 추출</b> 이 그 입구다.'],
      ]],
    ];

    view.appendChild(sec('11. 단어집 — 화면에 나오는 말'));
    view.appendChild(para(
      '이 앱이 쓰는 말을 한자리에 모았다. 화면 어딘가에서 뜻이 헷갈리면 여기서 찾는다.'));
    GLOSSARY.forEach(function (g) {
      view.appendChild(h('h3', { class: 'glosshd', text: g[0] }));
      view.appendChild(h('div', { class: 'tbl gloss' }, [h('table', {}, [
        h('tbody', {}, g[1].map(function (t) {
          return h('tr', {}, [h('td', { html: t[0] }), h('td', { html: t[1] })]);
        })),
      ])]));
    });

    view.appendChild(sec('12. 터미널에서'));
    view.appendChild(para(
      '아래 명령은 <b>전부 <a href="#/tools">도구</a> 탭에 그대로 있다</b> — 인자를 골라 넣으면 ' +
      '어떤 명령줄이 되는지 보여 주고 그대로 실행한다. 터미널이 편하면 터미널에서 쓰면 된다.'));
    view.appendChild(h('div', { class: 'md', text:
      'python3 -m tb.run doctor                                # 점검\n' +
      'python3 -m tb.run inject --scenario scenarios/regression.yaml\n' +
      'python3 -m tb.run run    --scenario scenarios/regression.yaml\n' +
      'python3 -m tb.run run    --scenario ... --watch         # 창 띄우고 space/n\n' +
      'python3 -m tb.run baseline <실행> --name regression\n' +
      'python3 -m tb.run compare <A> <B>\n' +
      'python3 -m tb.run reanalyze <실행>                      # 계약 수정 후 재해석\n' +
      'python3 -m tb.run render <실행> --mp4 auto              # 경로 영상\n' +
      'python3 -m tb.run harvest <실행> --where "int(flags) % 4 >= 2"\n' +
      'python3 -m tb.run feedback <실행> --vs <이전 실행>      # 개선 요청문\n' +
      'python3 -m tb.run list\n' +
      'python3 -m tb.selftest\n' +
      'python3 -m tb.discover --seconds 8 --out contracts/other.yaml\n' +
      'python3 -m tb.calibrate --scenario scenarios/regression.yaml' }));
    view.appendChild(para(
      '웹에 없는 것은 셋뿐이고 이유가 분명하다. <code>tb.run web / app</code> 은 <b>이 앱 자신</b>을 ' +
      '띄우는 명령이고, <code>tb.calibrate</code> 는 <a href="#/calib">카메라 보정</a> 탭이 대신하며, ' +
      '<code>tb.viewer / player / probe</code> 는 <code>tb.run</code> 이 내부적으로 띄우는 모듈이다. ' +
      '<code>--watch</code> 는 도구 탭에 있지만, 그 창은 <b>서버가 도는 PC 의 화면</b>에 뜬다 — ' +
      '멀리서 브라우저로 붙었다면 라이브 화면이 그 자리를 대신한다.'));
  }

  // ── 라우팅 ──────────────────────────────────────────────────────
  function setNav(name) {
    document.querySelectorAll('[data-nav]').forEach(function (a) {
      a.classList.toggle('on', a.getAttribute('data-nav') === name);
    });
  }
  function fail(e) {
    clear(view);
    view.appendChild(h('div', { class: 'empty', text: '오류: ' + e.message }));
  }

  /* 서버가 있어야 하는 화면을 정적에서 열었을 때. 「없는 경로」로 보이면
     사이트가 깨진 것처럼 읽히므로 왜 없는지를 말한다. */
  function staticNotice(sec) {
    setNav('');
    clear(view);
    view.appendChild(h('div', { class: 'empty' }, [
      h('p', { text: '«' + (SECNAME[sec] || sec) + '» 은 읽기 전용 사이트에 없습니다 — '
                     + 'ROS 2 노드를 띄우거나, 설정 파일을 쓰거나, '
                     + '원본 영상을 읽어야 하는 기능입니다.' }),
      h('p', {}, [h('a', { href: REPO || '#/help',
                           text: '내 머신에서 돌려 보는 법 →' })]),
    ]));
  }

  function route() {
    if (window.__stopPoll) { window.__stopPoll(); window.__stopPoll = null; }
    document.onkeydown = null;
    var hash = location.hash.replace(/^#/, '') || '/';
    hintEl.textContent = '';
    view.innerHTML = '<div class="loading">불러오는 중…</div>';

    var sec = hash.replace(/^\//, '').split(/[/?]/)[0];
    if (STATIC && SERVERONLY[sec]) { staticNotice(sec); return null; }

    if (hash === '/' || hash === '') {
      setNav('home');
      renderHome();
      return null;
    }
    if (hash === '/runs') {
      setNav('runs');
      return get('/api/runs').then(renderRuns).catch(fail);
    }
    if (hash === '/trash') {
      setNav('runs');
      return get('/api/trash').then(renderTrash).catch(fail);
    }
    if (hash === '/help') { setNav('help'); renderHelp(); return null; }
    if (hash === '/newtest') {
      setNav('newtest');
      //  등록 폼이 통째로 여기 있으므로 계약·영상 목록까지 필요하다(/api/config).
      return get('/api/config').then(renderNewTest).catch(fail);
    }
    var mc = hash.match(/^\/calib(?:\?s=([^&]+))?$/);
    if (mc) {
      setNav('calib');
      //  «새 시험 시작» 이 방금 만든 시나리오로 곧바로 열 수 있게 (?s=…)
      var want = mc[1] ? decodeURIComponent(mc[1]) : '';
      return get('/api/calib' + (want ? '?scenario=' + encodeURIComponent(want) : ''))
        .then(renderCalib).catch(fail);
    }
    var mt2 = hash.match(/^\/tools(?:\/([A-Za-z0-9_-]+))?$/);
    if (mt2) {
      setNav('tools');
      return get('/api/commands').then(function (sp) {
        renderTools(sp, mt2[1] || '');
      }).catch(fail);
    }
    if (hash === '/check') {
      setNav('check');
      return get('/api/meta').then(function (mt) {
        renderCheck({ scenarios: mt.scenarios || [], contracts: mt.contracts || [] });
      }).catch(fail);
    }
    if (hash === '/compare') {
      setNav('compare');
      return get('/api/runs').then(function (d) {
        return get('/api/baselines').then(function (b) {
          return get('/api/meta').then(function (mt) {
            renderCompare({
              runs: (d.runs || []).filter(function (r) { return r.has_summary; })
                      .map(function (r) { return r.id; }),
              baselines: (b.baselines || []).map(function (x) { return x.name; }),
              contracts: mt.contracts || [], scenarios: mt.scenarios || [],
            });
          });
        });
      }).catch(fail);
    }
    if (hash === '/exec') {
      setNav('exec');
      return get('/api/config').then(function (c) {
        return get('/api/baselines').then(function (b) {
          c.baselines = (b.baselines || []).map(function (x) { return x.name; });
          renderExec(c);
        }).catch(function () { renderExec(c); });
      }).catch(fail);
    }
    if (hash === '/baselines') {
      setNav('baselines');
      return get('/api/baselines').then(renderBaselines).catch(fail);
    }
    /* 프레임 탐색과 프레임 한 장은 원본 영상에서 cv2 로 뽑는다 — 정적에는
       그 영상이 없다. 빈 표로 열리면 사이트가 깨진 것처럼 보이므로 막는다. */
    if (STATIC && /^\/run\/[^/]+\/frames?(\/|$)/.test(hash)) {
      staticNotice('frames');
      return null;
    }
    var m1 = hash.match(/^\/run\/([^/]+)\/frame\/(-?\d+)$/);
    if (m1) {
      setNav('runs');
      renderFrameOne(decodeURIComponent(m1[1]), parseInt(m1[2], 10));
      return null;
    }
    var mf = hash.match(/^\/run\/([^/]+)\/frames$/);
    if (mf) {
      setNav('runs');
      var fid = decodeURIComponent(mf[1]);
      /* 기본 조건도 계약이 정한다 — 전에는 'int(flags) != 0' 이 박혀 있어서
         플래그가 없는 계약에서는 빈 목록으로 열렸다. */
      return get('/api/runs/' + encodeURIComponent(fid)).then(function (data) {
        var ui = data.ui || {};
        var def = (ui.frame_presets || []).filter(function (p2) { return p2.default; })[0]
                  || (ui.frame_presets || [])[0] || { where: 'int(flags) != 0' };
        renderFrames(fid, { where: def.where || '', limit: 24 }, ui);
      }).catch(function () {
        renderFrames(fid, { where: '', limit: 24 }, null);
      });
    }
    var m = hash.match(/^\/run\/([^/]+)(?:\/(summary|visual|detail|raw|feedback))?$/);
    if (m) {
      setNav('runs');
      var id = decodeURIComponent(m[1]);
      var tab = m[2] || 'summary';
      return get('/api/runs/' + encodeURIComponent(id))
        .then(function (data) {
          return get('/api/runs/' + encodeURIComponent(id) + '/signals')
            .then(function (sg) { renderRun(id, data, sg.rows, tab); })
            .catch(function () { renderRun(id, data, null, tab); });
        })
        .catch(function () {
          return get('/api/runs/' + encodeURIComponent(id) + '/inject')
            .then(function (j) { renderInject(id, j.cases || []); })
            .catch(fail);
        });
    }
    clear(view);
    view.appendChild(h('div', { class: 'empty', text: '없는 경로: ' + hash }));
  }

  window.addEventListener('hashchange', route);
  if (STATIC) {
    document.querySelectorAll('[data-nav]').forEach(function (a) {
      if (SERVERONLY[a.getAttribute('data-nav')]) a.remove();
    });
  }
  get('/api/meta').then(function (mt) {
    ROOTDIR = mt.root || '';
    REPO = mt.repo || '';
    footEl.textContent = mt.root;
    if (STATIC) {
      /* 읽기 전용이라는 것과 ★직접 돌리는 법★ 을 모든 화면에 남긴다 —
         처음 온 사람은 이게 앱인지 리포트인지부터 모른다. */
      var bar = h('div', { class: 'sbanner' }, [
        h('b', { text: '읽기 전용' }),
        h('span', { text: '지난 시험 결과만 보는 사이트입니다. '
                          + '실행 · 카메라 보정 · 도구는 서버가 있어야 합니다.' }),
        REPO ? h('a', { href: REPO, target: '_blank', rel: 'noopener',
                        text: '내 머신에서 돌려 보기 →' }) : null,
      ]);
      var top = document.querySelector('header.top');
      top.parentNode.insertBefore(bar, top.nextSibling);
    }
  }).catch(function () {});
  route();
})();
