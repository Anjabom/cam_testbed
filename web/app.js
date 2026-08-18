/* 테스트베드 웹 뷰어 — 라우팅과 렌더링.
 *
 * ★규칙★ 판정은 절대 여기서 하지 않는다. summary.json 의 checks[].ok 를
 * 색칠할 뿐이고, 임계값 비교를 JS 에 한 벌 더 쓰지 않는다(엔진과 어긋난다). */
(function () {
  'use strict';

  var ROOTDIR = '';                     // 테스트베드 루트 — 명령 안내에 쓴다
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
      if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || r.status); });
      var ct = r.headers.get('content-type') || '';
      return ct.indexOf('json') >= 0 ? r.json() : r.text();
    });
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

  // ── 홈 — 무엇을 할지 고른다 ─────────────────────────────────────
  //   첫 화면이 곧바로 목록이면 "지금 뭘 하려던 거였지"를 화면이 안 도와준다.
  var HOME = [
    ['#/exec', '테스트 실행',
     '시나리오를 골라 돌립니다. 진행률과 라이브 화면을 함께 봅니다.'],
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
    ['#/help', '사용 안내',
     '처음 켠 사람이 어떤 순서로 쓰면 되는지.'],
  ];

  function renderHome() {
    clear(view);
    view.appendChild(h('h1', { text: '카메라 테스트베드' }));
    view.appendChild(h('p', { class: 'sub',
      text: '무엇을 할지 고르세요. 위쪽 탭으로도 바로 갑니다.' }));
    view.appendChild(h('div', { class: 'homegrid' }, HOME.map(function (c) {
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
      h('a', { class: 'gobtn', href: '#/trash', text: '휴지통' }),
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
                  ['기여율', '실질 기여율 — 하류 노드가 실제로 갖다 쓴 프레임의 비율'],
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
    }

    function make() {
      clear(body);
      body.appendChild(spinner('경로 영상을 만드는 중… (400프레임 약 30초)'));
      // --fps 를 주지 않는다 → 엔진이 ★원본 영상과 같은 속도★로 맞춘다.
      // 예전에는 10 을 박아 30fps 영상이 1/3 배속으로 나왔다.
      postJob('render', [id, '--limit', '0', '--mp4', 'auto', '--width', '1400']);
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
                ['detail', '상세'], ['raw', '리포트'], ['feedback', '피드백']];
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
        ['차선 인식률', s.valid_rate == null ? '—' : pct(s.valid_rate),
         '차선을 본 프레임의 비율'],
        ['유실률', s.drop_rate == null ? '—' : pct(s.drop_rate, 2),
         '넣었는데 결과가 안 나온 비율 (lockstep 이면 0)'],
        ['지연 p95', s.latency_p95_ms == null ? '—' : s.latency_p95_ms.toFixed(0) + ' ms',
         '400ms 를 넘으면 하류가 그 값을 버립니다'],
      ];
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
        pane.appendChild(sectionTitle('θ 품질 — 하류가 실제로 쓰는 유일한 값'));
        pane.appendChild(th);
      }
      var ch = renderChecks(checks);
      if (ch) { pane.appendChild(sectionTitle('불변식 체크')); pane.appendChild(ch); }

      pane.appendChild(sectionTitle('이어서 할 일'));
      pane.appendChild(h('div', { class: 'framebar' }, [
        h('a', { class: 'gobtn', href: '#/run/' + encodeURIComponent(id) + '/frames',
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
  var PRESETS = [
    ['전부', ''],
    ['플래그 있음', 'int(flags) != 0'],
    ['차선 없음', 'int(flags) % 2 == 1'],
    ['폭 게이트 탈락', 'int(flags) % 4 >= 2'],
    ['한쪽 차선만', 'int(flags) % 32 >= 16'],
    ['conf 낮음', 'conf_raw < 0.3'],
    ['θ 대역 밖', 'abs(theta_deg) > 15'],
    ['하류가 쓴 프레임', 'int(flags) == 0 and conf_eff >= 0.35 and abs(theta_deg) >= 0.5 and abs(theta_deg) <= 15'],
  ];
  var FLAGBITS = [[1, 'NO_LANE'], [2, 'WIDTH_BAD'], [4, 'CTE_JUMP'],
                  [8, 'CONF_LOW'], [16, 'SINGLE']];

  function flagNames(v) {
    if (typeof v !== 'number') return '—';
    var iv = Math.round(v);
    if (iv === 0) return 'CLEAN';
    return FLAGBITS.filter(function (b) { return iv & b[0]; })
                   .map(function (b) { return b[1]; }).join(' ') || String(iv);
  }

  /* 필터 결과를 기억해 프레임 뷰어의 ←→ 가 ★이 목록 안에서만★ 움직이게 한다.
     ±1 프레임으로 넘기면 방금 거른 조건 밖으로 새어 나간다. */
  var FRAMESET = { runId: null, where: '', frames: [] };

  function renderFrames(id, state) {
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
      return h('button', { class: 'sigbtn' + (p2[1] === state.where ? ' on' : ''),
        text: p2[0], style: p2[1] === state.where ? 'background:var(--accent)' : '',
        onclick: function () { input.value = p2[1]; go(); } });
    }));

    view.appendChild(h('div', { class: 'framebar' }, [
      input, limitSel, h('button', { text: '적용', onclick: go }),
      h('span', { class: 'spacer' }),
      h('button', { text: '이 조건으로 추출',
        title: '조건에 맞는 원본 프레임을 이미지로 저장합니다 (라벨링용)',
        onclick: function () {
          postJob('harvest', [id, '--where', state.where, '--limit', String(state.limit)]);
        } }),
    ]));
    view.appendChild(presets);

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
        var thead = h('tr', {}, [['frame', ''], ['플래그', '이 프레임에서 걸린 조건'],
                                 ['θ [°]', '차선을 기준으로 차량이 틀어진 각도'],
                                 ['cte [m]', '차선 중심에서 옆으로 벗어난 거리'],
                                 ['폭 [m]', '좌우 차선 사이의 거리'],
                                 ['conf', '인지가 매긴 신뢰도'], ['', '']]
          .map(function (t) { return h('th', { text: t[0], title: t[1] || null }); }));
        var body = h('tbody', {}, (d.frames || []).map(function (fr) {
          var fn = flagNames(fr.flags);
          return h('tr', { class: 'click', onclick: function () {
            location.hash = '#/run/' + encodeURIComponent(id) + '/frame/' + fr.frame;
          } }, [
            h('td', { class: 'mono', text: String(fr.frame) }),
            h('td', {}, [badge(fn, fn === 'CLEAN' ? 'g' : 'r')]),
            h('td', { class: 'mono num', text: f(fr.theta_deg) }),
            h('td', { class: 'mono num', text: f(fr.cte_rear_m) }),
            h('td', { class: 'mono num', text: f(fr.lane_width_m) }),
            h('td', { class: 'mono num', text: f(fr.conf_raw) }),
            h('td', { class: 'mut', text: '›' }),
          ]);
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
    var recCb = h('input', { type: 'checkbox' });
    var planBox = h('div', {});
    var runBar = h('div', { class: 'framebar' });
    var liveBox = h('div', {});
    var regBox = h('div', {});

    view.appendChild(h('div', { class: 'framebar' }, [
      h('span', { class: 'mut', text: '시나리오' }), scSel,
      h('span', { class: 'spacer' }),
      h('span', { class: 'mut', text: '태그' }), tagIn,
      h('label', { class: 'mut', style: 'display:flex;gap:5px;align-items:center' },
        [recCb, h('span', { text: '디버그 영상 기록' })]),
    ]));
    view.appendChild(planBox);
    view.appendChild(runBar);
    view.appendChild(liveBox);
    view.appendChild(regBox);

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
      if (state.regContractSel && p.contract_file && !state.regTouched) {
        state.regContractSel.value = p.contract_file;
      }
      clear(planBox);
      if (p.error) {
        planBox.appendChild(h('div', { class: 'empty', text: p.error }));
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

      var pk = Object.keys(p.params || {});
      if (pk.length) {
        var flat = [];
        pk.forEach(function (nid) {
          Object.keys(p.params[nid]).forEach(function (k) {
            flat.push(nid + '.' + k + '=' + p.params[nid][k]);
          });
        });
        tb.appendChild(row('덮어쓰는 값', h('span', { class: 'mono mut',
          text: flat.join('  ·  ') })));
      }
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
      drawRunBar();
    }

    // ── 3) 실행 버튼 ────────────────────────────────────────────
    function args() {
      var a = ['--scenario', 'scenarios/' + scSel.value];
      if (tagIn.value.trim()) a.push('--tag', tagIn.value.trim());
      if (recCb.checked) a.push('--record-debug');
      return a;
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
      runBar.appendChild(h('span', { class: 'spacer' }));
      runBar.appendChild(h('button', { text: '중지', onclick: stop }));
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

    // ── 4) 실행 중 화면 — 진행률·라이브·로그 ────────────────────
    function drawLive(st) {
      var pr = st.progress || {};
      var frac = pr.total ? Math.min(1, (pr.pushed || 0) / pr.total) : 0;
      clear(liveBox);
      if (!st.running && !st.kind) return;

      var head = h('div', { class: 'livehd' }, [
        h('b', { text: st.running ? '● 실행 중' : '○ 끝났습니다' }),
        h('span', { class: 'mut', text: (st.kind || '') + ' ' + ((st.args || []).join(' ')) }),
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
      liveBox.appendChild(box);
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

    // ── 5) 등록 — 워크스페이스·영상·시나리오 ────────────────────
    function reload() {
      get('/api/config').then(function (c2) { renderExec(c2); });
    }
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

    function drawReg() {
      clear(regBox);
      // 등록된 게 없으면(처음 쓰는 사람) 펴 둔다. 그다음부터는 접힌 채로 둔다.
      var nvid = Object.keys(cfg.videos || {}).length;
      var det = h('details', { class: 'reg', open: nvid ? null : 'open' });
      det.appendChild(h('summary', { text: '워크스페이스 · 영상 등록' }));
      var body2 = h('div', { class: 'regbody' });

      // 영상
      body2.appendChild(h('h3', { text: '영상' }));
      body2.appendChild(h('p', { class: 'help',
        html: '시나리오에는 <b>별칭</b>만 적고 실제 경로는 여기서 등록합니다 — ' +
              '그래야 다른 PC 로 옮겨도 시나리오를 고치지 않아도 됩니다.' }));
      var vt = h('table', { class: 'tbl' });
      var vtb = h('tbody', {});
      Object.keys(cfg.videos || {}).forEach(function (k) {
        var v = cfg.videos[k];
        vtb.appendChild(h('tr', {}, [
          h('td', { class: 'mono', text: k }),
          h('td', { class: 'mono mut', text: v.path }),
          h('td', { class: 'mono', text: v.exists ? (v.frames + '프레임 · ' + v.w + '×' + v.h) : '' }),
          h('td', {}, [v.exists ? h('b', { class: 'ok', text: '✅' })
                                : h('b', { class: 'no', text: '⛔ 파일 없음' })]),
          h('td', {}, [h('button', { text: '삭제', onclick: function () {
            post('/api/config/video', { name: k, delete: true })
              .then(function (j) { if (say(j, k + ' 삭제됨')) reload(); });
          } })]),
        ]));
      });
      vt.appendChild(vtb);
      body2.appendChild(vt);
      var vn = h('input', { type: 'text', placeholder: '별칭 (예: track_2026)', size: '22' });
      var vp = h('input', { type: 'text', placeholder: '/home/me/영상.mp4', size: '42' });
      body2.appendChild(h('div', { class: 'framebar' }, [
        vn, vp,
        h('button', { class: 'primary', text: '영상 등록', onclick: function () {
          post('/api/config/video', { name: vn.value.trim(), path: vp.value.trim() })
            .then(function (j) {
              if (say(j, vn.value + ' 등록됨 (' + ((j.video || {}).frames || '?') + '프레임)')) reload();
            });
        } }),
      ]));

      // 워크스페이스(계약)
      body2.appendChild(h('h3', { text: '워크스페이스' }));
      body2.appendChild(h('p', { class: 'help',
        html: '워크스페이스 하나 = 계약 파일 하나입니다. 테스트베드를 복사하지 않고 ' +
              '계약만 늘립니다. 새로 만들면 <b>노드·토픽은 TODO 로 비어 있으니</b> ' +
              '대상 시스템을 띄운 뒤 <b>환경 점검</b> 탭의 <code>discover</code> 로 채웁니다.' }));
      var ct = h('table', { class: 'tbl' });
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
            post('/api/config/contract/workspace', { file: c.file, workspace: wi.value.trim() })
              .then(function (j) { if (say(j, c.file + ' 갱신됨')) reload(); });
          } })]),
        ]));
      });
      ct.appendChild(ctb);
      body2.appendChild(ct);
      var cn = h('input', { type: 'text', placeholder: '계약 이름 (예: other_ws)', size: '22' });
      var cw = h('input', { type: 'text', placeholder: '/home/me/other_ws', size: '32' });
      var ca = h('input', { type: 'checkbox' });
      body2.appendChild(h('div', { class: 'framebar' }, [
        cn, cw,
        h('label', { class: 'mut', style: 'display:flex;gap:5px;align-items:center' },
          [ca, h('span', { text: 'attach (관찰만)' })]),
        h('button', { class: 'primary', text: '계약 만들기', onclick: function () {
          post('/api/config/contract', { name: cn.value.trim(), workspace: cw.value.trim(),
                                         attach: ca.checked })
            .then(function (j) { if (say(j, 'contracts/' + j.file + ' 생성됨')) reload(); });
        } }),
      ]));

      // 시나리오
      body2.appendChild(h('h3', { text: '시나리오' }));
      body2.appendChild(h('p', { class: 'help',
        html: '<b>무엇을 어떻게 돌릴지</b>입니다. 계약(대상)과 분리돼 있어 같은 워크스페이스에 ' +
              '구간·모드가 다른 시나리오를 여러 개 둘 수 있습니다.' }));
      var sn = h('input', { type: 'text', placeholder: '이름 (예: reg_2026)', size: '18' });
      var sc2 = h('select', {}, (cfg.contracts || []).map(function (c) {
        return h('option', { value: c.file, text: c.file });
      }));
      // 시나리오 해석이 끝나면 그 계약을 기본으로 맞춰 준다.
      // 단 사람이 한 번 고른 뒤에는 건드리지 않는다.
      sc2.addEventListener('change', function () { state.regTouched = true; });
      state.regContractSel = sc2;
      if (state.plan && state.plan.contract_file) sc2.value = state.plan.contract_file;
      var sv = h('select', {}, Object.keys(cfg.videos || {}).map(function (k) {
        return h('option', { value: k, text: k });
      }));
      var sm = h('select', {}, ['lockstep', 'realtime'].map(function (k) {
        return h('option', { value: k, text: k });
      }));
      var ss = h('input', { type: 'number', value: '0', size: '6', style: 'width:80px' });
      var sl = h('input', { type: 'number', value: '400', size: '6', style: 'width:80px' });
      body2.appendChild(h('div', { class: 'framebar' }, [
        sn, sc2, sv, sm,
        h('span', { class: 'mut', text: '시작 프레임' }), ss,
        h('span', { class: 'mut', text: '프레임 수' }), sl,
        h('button', { class: 'primary', text: '시나리오 만들기', onclick: function () {
          post('/api/config/scenario', { name: sn.value.trim(), contract: sc2.value,
                                         video: sv.value, mode: sm.value,
                                         start: Number(ss.value), limit: Number(sl.value) })
            .then(function (j) { if (say(j, 'scenarios/' + j.file + ' 생성됨')) reload(); });
        } }),
      ]));

      det.appendChild(body2);
      regBox.appendChild(det);
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
      text: 'scenarios/ 가 비어 있습니다 — 아래에서 시나리오를 만드세요.' }));
    drawReg();
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

    function run(name, extra) {
      out.textContent = '실행 중…';
      var url = '/api/quick/' + name + (extra ? '?scenario=' + encodeURIComponent(extra) : '');
      get(url).then(function (d) {
        out.textContent = d.out || '(출력 없음)';
        hintEl.textContent = (JOBNAME[name] || name) + ' → '
                             + (d.rc === 0 ? '이상 없음' : '문제 있음');
        setTimeout(function () { hintEl.textContent = ''; }, 4000);
      }).catch(function (e) { out.textContent = '오류: ' + e.message; });
    }

    view.appendChild(h('div', { class: 'framebar' }, [
      h('span', { class: 'mut', text: '시나리오' }), scSel,
      h('button', { class: 'primary', text: '환경 점검 (doctor)',
                    onclick: function () { run('doctor', 'scenarios/' + scSel.value); } }),
      h('button', { text: '자체 검사 (selftest)', onclick: function () { run('selftest'); } }),
      h('button', { text: '계약 초안 (discover)',
                    title: '돌고 있는 ROS 시스템을 읽어 계약 초안을 뽑습니다 (약 6초)',
                    onclick: function () { run('discover'); } }),
    ]));
    view.appendChild(out);
    view.appendChild(cli('python3 -m tb.run doctor  /  python3 -m tb.selftest'));
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

    view.appendChild(h('div', { class: 'framebar' }, [
      h('span', { class: 'mut', text: '기준' }), aSel,
      h('span', { class: 'mut', text: '→ 현재' }), bSel,
      h('button', { class: 'primary', text: '비교', onclick: function () {
        out.textContent = '비교 중…';
        postJob('compare', [aSel.value, bSel.value]).then(function () {
          setTimeout(function () {
            get('/api/status').then(function (st) {
              out.textContent = st.log_tail || '(출력 없음)';
            });
          }, 1500);
        });
      } }),
    ]));
    view.appendChild(out);
  }

  /* ── 캘리브레이션 : 4점을 끌어 BEV 를 맞춘다 ────────────────────
   * 기하 계산은 서버(tb.geometry)가 한다 — 대상 노드가 하는 변환을 그대로
   * 재현한 코드가 이미 있고, 그걸 JS 에 한 벌 더 쓰면 반드시 어긋난다. */
  function renderCalib(st) {
    clear(view);
    view.appendChild(h('h1', { text: '카메라 보정' }));
    view.appendChild(h('p', { class: 'sub',
      text: 'IPM 사각형의 좌우 변을 차선 위에 올리세요. 지면은 평평해서 제대로 올리면 '
            + 'BEV(위에서 내려다본 화면)에서 차선이 수직으로 섭니다. 수직에 얼마나 가까운지를 '
            + '아래 «수직도» 로 재 줍니다 (직선 구간에서만 의미가 있습니다).' }));

    var quad = (st.quad || [620, 650, 1300, 650, 1920, 1080, 0, 1080]).map(Number);
    var vids = Object.keys(st.videos || {});
    var state = { frame: 780, px2m: 0.006, undist: true, sel: 0, video: null,
                  mode: 'quad', meas: [], realM: 3.0 };
    state.video = vids.length ? st.videos[vids[0]] : null;

    var vidSel = h('select', {}, vids.map(function (k) {
      return h('option', { value: st.videos[k], text: k });
    }));
    vidSel.addEventListener('change', function () { state.video = vidSel.value; draw(); });

    var frIn = h('input', { type: 'number', value: '780', class: 'wherebox',
                            style: 'max-width:110px' });
    frIn.addEventListener('change', function () {
      state.frame = parseInt(frIn.value, 10) || 0; draw();
    });

    var pxIn = h('input', { type: 'number', step: '0.0001', value: '0.006',
                            class: 'wherebox', style: 'max-width:120px' });
    pxIn.addEventListener('change', function () {
      state.px2m = parseFloat(pxIn.value) || 0.006; draw();
    });

    var img = h('img', { class: 'bigov', style: 'cursor:crosshair' });
    var meta = h('div', { class: 'readout' });
    var yamlBox = h('div', { class: 'md', style: 'max-height:220px' });

    var ptBtns = ['TL', 'TR', 'BR', 'BL'].map(function (t, i) {
      return h('button', { class: 'sigbtn' + (i === 0 ? ' on' : ''), text: t,
        style: i === 0 ? 'background:var(--accent)' : '',
        onclick: function () {
          state.sel = i;
          ptBtns.forEach(function (b, k) {
            b.classList.toggle('on', k === i);
            b.style.background = k === i ? 'var(--accent)' : '';
          });
        } });
    });

    /* 클릭 처리 — 왼쪽(원본)은 사각형 점 이동, 오른쪽(BEV)은 측정.
       화면은 축소돼 있으므로 패널 폭 대비 비율로 좌표를 되돌린다. */
    img.addEventListener('click', function (e) {
      if (!img.__meta) return;
      var r = img.getBoundingClientRect();
      var x = (e.clientX - r.left) / r.width * img.__dispW;
      var y = (e.clientY - r.top) / r.height * img.__dispH;
      var mm = img.__meta;
      if (x <= mm.panel_w) {
        if (state.mode !== 'quad') return;
        quad[state.sel * 2] = Math.round(x / mm.panel_w * mm.src_w);
        quad[state.sel * 2 + 1] = Math.round(y / mm.bev_h * mm.src_h);
      } else {
        if (state.mode !== 'measure') return;
        // BEV 픽셀 좌표 — 표시된 폭이 실제 bev_w 와 다를 수 있으므로 비율로 환산
        var bevDisp = mm.panel_w + mm.bev_w;
        var bx = (x - mm.panel_w) / (bevDisp - mm.panel_w) * mm.bev_w;
        var by = y / mm.bev_h * mm.bev_h;
        if (state.meas.length >= 2) state.meas = [];
        state.meas.push([bx, by]);
      }
      draw();
    });

    function nudge(dx, dy) {
      if (state.mode !== 'quad') return;
      quad[state.sel * 2] += dx;
      quad[state.sel * 2 + 1] += dy;
      draw();
    }
    function measInfo() {
      if (state.meas.length < 2) {
        return state.mode === 'measure'
          ? 'BEV 에서 실제 길이를 아는 두 점을 클릭하세요 (차선 폭이 가장 쉽습니다)'
          : '';
      }
      var a = state.meas[0], b = state.meas[1];
      var d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      var v = state.realM / Math.max(1e-6, d);
      return '잰 거리 ' + d.toFixed(1) + ' px = ' + state.realM.toFixed(2) + ' m  →  px2m = '
             + v.toFixed(6);
    }
    function applyMeas() {
      if (state.meas.length < 2) return;
      var a = state.meas[0], b = state.meas[1];
      var d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (d < 2) return;
      state.px2m = state.realM / d;
      pxIn.value = state.px2m.toFixed(6);
      state.meas = [];
      draw();
    }
    document.onkeydown = function (e) {
      var k = { ArrowLeft: [-1, 0], ArrowRight: [1, 0],
                ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
      if (!k) return;
      e.preventDefault();
      var m2 = e.shiftKey ? 10 : 1;
      nudge(k[0] * m2, k[1] * m2);
    };

    function draw() {
      syncMeasBtns();
      if (!state.video) { meta.textContent = 'local.yaml 의 videos: 에 영상을 등록하세요'; return; }
      var mq = state.meas.length
               ? '&meas=' + state.meas.map(function (p2) {
                   return p2[0].toFixed(1) + ',' + p2[1].toFixed(1); }).join(';')
               : '';
      var url = '/api/calib/view?quad=' + quad.join(',') +
                '&frame=' + state.frame + '&px2m=' + state.px2m +
                '&video=' + encodeURIComponent(state.video) +
                '&undistort=' + (state.undist ? 1 : 0) + '&w=1400' + mq;
      get(url).then(function (d) {
        img.src = 'data:image/jpeg;base64,' + d.img;
        img.__meta = d.meta;
        img.__dispW = 1400;
        img.__dispH = d.meta.bev_h;
        clear(meta);
        var v = d.meta.verticality_deg;
        var verdict = v == null ? '선을 못 찾았습니다'
          : v < 2 ? '잘 맞았습니다' : v < 5 ? '거의 맞았습니다'
          : '좌우 변을 차선에 더 붙이세요';
        meta.appendChild(h('span', {}, [h('b', {
          text: '수직도 ' + (v == null ? '—' : v + '°') }), 
          document.createTextNode(' (' + verdict + ', 선 ' + d.meta.lines + '개)')]));
        meta.appendChild(h('span', { text: 'BEV 폭 ' + d.meta.bev_width_m + ' m' }));
        if (!d.meta.sane) {
          meta.appendChild(h('span', { class: 'no', text: '⚠ ' + (d.meta.why || []).join(' / ') }));
        }
        var mi = measInfo();
        if (mi) meta.appendChild(h('span', { class: 'tb', text: mi }));
        var hint = document.getElementById('calibhint');
        if (hint) {
          hint.textContent = state.mode === 'quad'
            ? '왼쪽 원본 화면을 클릭하면 고른 점이 그 자리로 갑니다. 방향키는 1px, Shift+방향키는 10px.'
            : '오른쪽 BEV 에서 두 점을 클릭하세요. 실제 길이를 넣고 «px2m 적용» 을 누릅니다.';
        }
        yamlBox.textContent = buildYaml();
      }).catch(function (e) { meta.textContent = '오류: ' + e.message; });
    }

    function buildYaml() {
      var out = 'params:\n';
      var byNode = {};
      Object.keys(st.targets || {}).forEach(function (k) {
        var t = st.targets[k];
        if (t.kind !== 'quad') return;
        (t.nodes || []).forEach(function (n) {
          byNode[n] = byNode[n] || {};
          byNode[n][t.param] = quad;
        });
      });
      Object.keys(st.targets || {}).forEach(function (k) {
        var t = st.targets[k];
        if (t.kind !== 'scale') return;
        (t.nodes || []).forEach(function (n) {
          byNode[n] = byNode[n] || {};
          byNode[n][t.param] = state.px2m;
        });
      });
      Object.keys(byNode).forEach(function (n) {
        out += '  ' + n + ':\n';
        Object.keys(byNode[n]).forEach(function (k) {
          var v = byNode[n][k];
          out += '    ' + k + ': ' +
                 (Array.isArray(v) ? '[' + v.join(', ') + ']' : v.toFixed(6)) + '\n';
        });
      });
      return out || '(계약에 quad 대상이 없습니다)';
    }

    var realIn = h('input', { type: 'number', step: '0.05', value: '3.0',
                              class: 'wherebox', style: 'max-width:100px' });
    realIn.addEventListener('change', function () {
      state.realM = parseFloat(realIn.value) || 3.0; draw();
    });
    var modeBtns = [['quad', '사각형 맞추기'],
                    ['measure', '길이 재기 (px2m)']].map(function (t) {
      return h('button', { class: 'sigbtn' + (t[0] === state.mode ? ' on' : ''),
        text: t[1], style: t[0] === state.mode ? 'background:var(--accent)' : '',
        onclick: function () {
          state.mode = t[0]; state.meas = [];
          modeBtns.forEach(function (b, k) {
            var on = (['quad', 'measure'][k] === state.mode);
            b.classList.toggle('on', on);
            b.style.background = on ? 'var(--accent)' : '';
          });
          draw();
        } });
    });
    var applyBtn = h('button', { text: 'px2m 적용', onclick: applyMeas });
    /* 측정 두 점이 없으면 applyMeas 는 조용히 아무것도 안 했다 — 그래서 눌러도
       반응이 없어 보였다. 지금은 못 누르는 이유가 버튼에 적힌다.
       실측 길이 입력도 측정 모드에서만 의미가 있으므로 같이 잠근다. */
    function syncMeasBtns() {
      var measuring = state.mode === 'measure';
      var ready = measuring && state.meas.length >= 2;
      realIn.disabled = !measuring;
      applyBtn.disabled = !ready;
      if (ready) {
        var a = state.meas[0], b = state.meas[1];
        var d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        applyBtn.textContent = 'px2m 적용 → ' + (state.realM / Math.max(1e-6, d)).toFixed(6);
        applyBtn.title = '찍은 두 점 사이를 실제 ' + state.realM.toFixed(2)
                         + ' m 로 놓고 px2m 을 다시 계산합니다';
      } else {
        applyBtn.textContent = 'px2m 적용';
        applyBtn.title = measuring
          ? 'BEV 에서 두 점을 클릭하면 눌립니다 (지금 ' + state.meas.length + '/2)'
          : '«길이 재기» 모드에서만 씁니다';
      }
    }

    /* 왜곡보정 토글 — 라벨이 '지금 상태'다. 누르라는 명령이 아니라 켜져 있다는 표시라서
       다른 토글(점·모드)과 같은 on 스타일을 그대로 쓴다. */
    var undBtn = h('button', { class: 'sigbtn on', style: 'background:var(--accent)',
      text: '왜곡보정 ON', title: '렌즈 왜곡을 펴서 볼지 여부',
      onclick: function () {
        state.undist = !state.undist;
        undBtn.classList.toggle('on', state.undist);
        undBtn.style.background = state.undist ? 'var(--accent)' : '';
        undBtn.textContent = '왜곡보정 ' + (state.undist ? 'ON' : 'OFF');
        draw();
      } });

    view.appendChild(h('div', { class: 'framebar' }, [
      h('span', { class: 'mut', text: '영상' }), vidSel,
      h('span', { class: 'mut', text: 'frame' }), frIn,
      h('span', { class: 'mut', text: 'px2m' }), pxIn,
      undBtn,
    ]));
    view.appendChild(h('div', { class: 'framebar' }, modeBtns.concat([
      h('span', { class: 'mut', text: '실제 길이 [m]' }), realIn, applyBtn,
      h('span', { class: 'spacer' }),
      h('span', { class: 'mut', text: '점' }),
    ]).concat(ptBtns)));
    view.appendChild(h('p', { class: 'sub', id: 'calibhint',
      text: '왼쪽 원본 화면을 클릭하면 고른 점이 그 자리로 갑니다. 방향키는 1px, Shift+방향키는 10px.' }));
    view.appendChild(img);
    view.appendChild(meta);
    view.appendChild(sectionTitle('시나리오에 붙여 넣을 값'));
    view.appendChild(yamlBox);
    view.appendChild(cli('python3 -m tb.calibrate --scenario scenarios/regression.yaml'));
    draw();
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

    view.appendChild(sec('1. 등록하기 — 테스트 실행 탭 아래 \'워크스페이스 · 영상 등록\''));
    view.appendChild(para(
      '<b>테스트 실행</b> 탭 맨 아래 접힌 칸을 펴면 셋 다 여기서 등록된다. ' +
      '파일을 직접 열어도 되고(오른쪽 칸이 그 파일이다) 화면에서 해도 된다 — 같은 결과다.'));
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
      ['④ 시나리오 만들기', '계약·영상·모드·구간을 고르고 <code>시나리오 만들기</code>. ' +
       '<b>lockstep</b> 은 한 프레임씩 밀어 넣어 결정적이라 회귀 비교용, ' +
       '<b>realtime</b> 은 실제 fps 로 밀어 타이밍·드롭을 보는 용도다.'],
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
        h('td', { html: '경로와 <b>빌드 여부</b>. ⛔ 면 대상에서 <code>colcon build</code> 부터.' })]),
      h('tr', {}, [h('td', { html: '<b>띄울 노드</b>' }),
        h('td', { html: '실제로 실행될 <code>ros2 run …</code> 줄. 여기 없는 노드는 안 뜬다.' })]),
      h('tr', {}, [h('td', { html: '<b>영상</b>' }),
        h('td', { html: '<b>별칭 → 실제 파일 → 프레임 수·해상도</b>. ' +
          '등록 안 된 이름이면 ⚠ 가 붙는다.' })]),
      h('tr', {}, [h('td', { html: '<b>구간·재생</b>' }),
        h('td', { html: 'start~limit 과 모드. <b>구간이 영상보다 길면</b> 미리 경고한다.' })]),
      h('tr', {}, [h('td', { html: '<b>덮어쓰는 값</b>' }),
        h('td', { html: '시나리오·local.yaml 이 노드 기본 파라미터 위에 덮어쓰는 것. ' +
          '가중치 경로가 여기 나온다.' })]),
    ])]));
    view.appendChild(para(
      '<b>⛔ 가 하나라도 있으면 실행 버튼이 잠긴다.</b> 못 돌 것을 돌려 놓고 ' +
      '30초 뒤에 실패를 보는 대신 지금 알려 준다. ⚠ 는 돌긴 하지만 알고 있어야 하는 것이다. ' +
      '맨 아래 <code>$</code> 줄은 <b>이 화면이 실제로 부를 명령</b>이라 터미널에 그대로 붙여도 된다.'));

    view.appendChild(sec('3. 실행 · 진행 상황'));
    view.appendChild(steps([
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
      ['② 하류가 실제로 썼나', '<b>게이트 통과율</b>. 이게 최종 판정이다. ' +
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
      '판정표의 기준값은 지어낸 것이 아니다. 대부분 <b>하류 노드가 실제로 쓰는 상수</b>에서 왔다 — ' +
      '예를 들어 <code>conf ≥ 0.35</code> 는 <code>gps_imu</code> 의 ' +
      '<code>CAM_HEAD_CONF_MIN</code> 그대로다. 그 문턱을 못 넘으면 ' +
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
      ['결과를 읽는 말', [
        ['실질 기여율',
         '카메라가 낸 프레임 중 <b>하류가 게이트를 모두 통과시켜 실제로 쓴</b> 비율. ' +
         '이 앱에서 가장 중요한 숫자이고, 사실상 최종 판정이다.'],
        ['게이트',
         '하류가 받은 값을 쓸지 버릴지 가르는 조건. 문턱값은 지어낸 것이 아니라 ' +
         '<b>하류 코드의 상수를 그대로 옮겨 적은 것</b>이다.'],
        ['결과 행',
         '분석에 쓰인 출력 행 수. 넣은 프레임 수보다 적으면 그만큼 유실된 것이다.'],
        ['차선 인식률',
         '차선을 <b>본</b> 프레임의 비율. 봤다는 뜻이지 잘 봤다는 뜻은 아니다.'],
        ['유실률',
         '넣었는데 결과가 돌아오지 않은 비율. <code>lockstep</code> 이면 0 이어야 한다.'],
        ['지연 p95',
         '100번 중 95번은 이 시간 안에 결과가 나왔다는 뜻. ' +
         '400ms 를 넘으면 하류가 그 값을 버린다.'],
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
         '차선을 기준으로 차량이 틀어진 각도. 하류가 헤딩 보정에 쓰는 <b>유일한 값</b>이다.'],
        ['cte',
         '차선 중심에서 옆으로 벗어난 거리 (cross track error).'],
        ['conf',
         '인지가 매긴 신뢰도. 원본 값이다.'],
        ['conf_eff',
         '게이트를 반영한 유효 신뢰도. <b>0 이면 하류가 카메라를 쓰지 않는다.</b>'],
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
        ['하류',
         '이 신호를 <b>받아서 쓰는 쪽</b>의 노드. 카메라가 아무리 잘 봐도 ' +
         '하류가 안 쓰면 주행에는 아무 기여가 없다.'],
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
    view.appendChild(h('div', { class: 'md', text:
      'python3 -m tb.run doctor                                # 점검\n' +
      'python3 -m tb.run inject --scenario scenarios/regression.yaml\n' +
      'python3 -m tb.run run    --scenario scenarios/regression.yaml\n' +
      'python3 -m tb.run run    --scenario ... --watch         # 창 띄우고 space/n\n' +
      'python3 -m tb.run baseline <실행> --name regression\n' +
      'python3 -m tb.run render <실행> --mp4 auto              # 경로 영상\n' +
      'python3 -m tb.run harvest <실행> --where "int(flags) % 4 >= 2"\n' +
      'python3 -m tb.run feedback <실행> --vs <이전 실행>      # 개선 요청문\n' +
      'python3 -m tb.calibrate --scenario scenarios/regression.yaml\n' +
      'python3 -m tb.run list' }));
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

  function route() {
    if (window.__stopPoll) { window.__stopPoll(); window.__stopPoll = null; }
    document.onkeydown = null;
    var hash = location.hash.replace(/^#/, '') || '/';
    hintEl.textContent = '';
    view.innerHTML = '<div class="loading">불러오는 중…</div>';

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
    if (hash === '/calib') {
      setNav('calib');
      return get('/api/calib').then(renderCalib).catch(fail);
    }
    if (hash === '/check') {
      setNav('check');
      return get('/api/meta').then(function (mt) {
        renderCheck({ scenarios: mt.scenarios || [] });
      }).catch(fail);
    }
    if (hash === '/compare') {
      setNav('compare');
      return get('/api/runs').then(function (d) {
        return get('/api/baselines').then(function (b) {
          renderCompare({
            runs: (d.runs || []).filter(function (r) { return r.has_summary; })
                    .map(function (r) { return r.id; }),
            baselines: (b.baselines || []).map(function (x) { return x.name; }),
          });
        });
      }).catch(fail);
    }
    if (hash === '/exec') {
      setNav('exec');
      return get('/api/config').then(renderExec).catch(fail);
    }
    if (hash === '/baselines') {
      setNav('baselines');
      return get('/api/baselines').then(renderBaselines).catch(fail);
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
      renderFrames(decodeURIComponent(mf[1]),
                   { where: 'int(flags) != 0', limit: 24 });
      return null;
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
  get('/api/meta').then(function (mt) {
    ROOTDIR = mt.root || '';
    footEl.textContent = mt.root;
  }).catch(function () {});
  route();
})();
