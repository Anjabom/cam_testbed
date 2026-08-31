"""정적 내보내기 — ★서버 없이 결과만 보는 사이트★를 굽는다 (GitHub Pages).

★왜 이게 되나★
읽기 화면은 전부 `web/app.js` 의 `get()` 하나를 지나가고, 서버는 `runs/` 의 파일을
JSON 으로 옮길 뿐이다 — 판정은 이미 엔진이 끝내 `summary.json` 에 들어 있다.
그래서 ★서버의 그 함수를 그대로 불러★ 응답을 파일로 구워 두면 정적 호스팅에서
같은 화면이 그대로 열린다. 화면 코드를 한 벌 더 쓰지 않는다.

★안 되는 것★ — 실행·테스트 준비·보정·도구·환경 점검.
`ros2 run` 서브프로세스와 `local.yaml` 쓰기가 필요하다. `app.js` 가 `window.STATIC`
을 보고 그 탭을 숨기고, 남은 버튼이 눌리면 읽기 전용이라고 말한다.

★공개된다는 것★
결과가 인터넷에 올라간다. `summary.json` 의 `meta` 에는 워크스페이스·가중치·영상의
★절대경로★가 들어 있어서 그대로 두면 사용자 이름과 머신 구조가 같이 나간다.
그래서 `/home/<사용자>` 를 `~` 로 바꿔 굽는다.

    python3 -m tb.run publish                  # 핀 꽂은 실행만 → docs/
    python3 -m tb.run publish --run 0825_x     # 이름으로 지정 (여러 번)
    python3 -m tb.run publish --all            # 전부
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent

#  런 폴더의 마크다운 → API 의 텍스트 라우트.
TEXT_ROUTES = {"report": "report.md", "compare": "compare.md", "feedback": "feedback.md"}

#  ★JSON 이 아니라 텍스트로 굽는 라우트★ — web/app.js 의 TEXTY 와 같아야 한다.
TEXTY = {"report", "compare", "feedback", "log"}


def api_path(url):
    """서버 라우트 → 구워 둔 파일의 상대 경로.

    ★web/app.js 의 apiURL() 과 같은 규칙★이다 — 한쪽만 고치면 그 화면이
    「HTTP 404」 로만 열리고 왜 비었는지 화면으로는 알 수 없다.
    그래서 selftest 의 t_publish_names 가 양쪽을 대조한다.

        /api/runs                    → api/runs.json
        /api/runs/<런>/report        → api/runs/<런>/report.txt
        /api/runs/<런>/log?name=x    → api/runs/<런>/log/x.txt   (쿼리는 경로 한 칸)
    """
    head, _, query = url.partition("?")
    path = head[5:] if head.startswith("/api/") else head.lstrip("/")
    tail = path.rsplit("/", 1)[-1]
    if query:
        parts = query.split("&")[0].split("=")
        path += "/" + unquote(parts[1] if len(parts) > 1 else "")
    return "api/" + path + (".txt" if tail in TEXTY else ".json")


#  ★홈 경로를 지운다★ — 공개 사이트에 사용자 이름을 올리지 않는다.
#  경로가 통째로 사라지면 "어느 영상이었나"를 못 읽으니 접두사만 ~ 로 바꾼다.
_HOME = re.compile(r"/home/[^/\"\s]+")


def scrub(s):
    return _HOME.sub("~", s)


def _server():
    """웹 서버 모듈을 그대로 빌려 쓴다 (`tb.run web` 과 같은 방식으로 import)."""
    sys.path.insert(0, str(ROOT / "web"))
    import server                                     # noqa: PLC0415
    return server


def _write(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    p.write_text(scrub(text))
    return p


def _repo_url():
    """`직접 돌려 보려면` 안내가 가리킬 곳. 포크해도 자기 저장소를 가리키게."""
    try:
        u = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    if u.startswith("git@github.com:"):
        u = "https://github.com/" + u.split(":", 1)[1]
    return u[:-4] if u.endswith(".git") else u


def _pick(briefs, run_ids, all_runs):
    """무엇을 공개할지. ★기본은 핀 꽂은 것만★ — 실수로 전부 나가지 않게."""
    if run_ids:
        have = {b["id"] for b in briefs}
        missing = [r for r in run_ids if r not in have]
        if missing:
            raise SystemExit("그런 실행이 없습니다: " + ", ".join(missing))
        picked = [b for b in briefs if b["id"] in set(run_ids)]
    elif all_runs:
        picked = list(briefs)
    else:
        picked = [b for b in briefs if b.get("pin")]
        if not picked:
            raise SystemExit(
                "핀 꽂은 실행이 없습니다.\n"
                "  웹앱 «실행 기록» 에서 공개할 실행에 📌 를 찍거나,\n"
                "  --run <이름> 으로 지정하거나, --all 로 전부 내보내세요.")
    #  분석이 없는 런은 화면이 열리지 않는다 — 목록에도 넣지 않는다.
    out = [b for b in picked if b.get("has_summary")]
    if not out:
        raise SystemExit("고른 실행에 분석 결과(summary.json)가 없습니다.")
    return out


def publish(out_dir="docs", run_ids=(), all_runs=False):
    sv = _server()
    out = Path(out_dir)
    if not out.is_absolute():
        out = ROOT / out
    api = out / "api"
    if api.exists():
        shutil.rmtree(api)              # 지운 런이 남아 있지 않게 매번 새로 굽는다

    picked = _pick(sv.list_runs(), list(run_ids), all_runs)

    for b in picked:
        rid = b["id"]
        d = sv.RUNS / rid
        sj = sv._read_json(d / "summary.json")
        sj["id"] = rid
        #  ★화면 설정은 그 런의 계약에서★ (프리셋·열·플래그 이름) — 서버와 같은 경로.
        sj["ui"] = sv.contract_ui(sv._contract_for(d))
        #  영상·프레임은 굽지 않는다. `video` 키가 없으면 app.js 가 영상 패널을
        #  아예 만들지 않아서 404 로 깨지지 않는다(정적에서 cv2 를 부를 수 없다).
        _write(out / api_path("/api/runs/" + rid), sj)
        _write(out / api_path("/api/runs/" + rid + "/signals"),
               {"rows": sv._read_csv(d / "signals.csv")})
        _write(out / api_path("/api/runs/" + rid + "/pathmeta"),
               {"exists": False, "file": ""})
        for route, fname in TEXT_ROUTES.items():
            f = d / fname
            if f.exists():
                _write(out / api_path("/api/runs/" + rid + "/" + route), f.read_text())
        #  노드 로그는 ★.txt 로★ 굽는다 — .gitignore 의 `*.log` 에 걸려
        #  커밋이 조용히 빠지면 사이트에서만 로그가 사라진다.
        for lg in sorted(d.glob("*.log")):
            _write(out / api_path("/api/runs/" + rid + "/log?name=" + lg.stem),
                   lg.read_text(errors="replace"))

    _write(out / api_path("/api/runs"), {"runs": picked})
    _write(out / api_path("/api/baselines"), {"baselines": sv.list_baselines()})
    _write(out / api_path("/api/meta"), {
        #  `root` 는 화면 아래에 그대로 찍힌다 — 정적에서는 경로 대신 출처를 적는다.
        "root": "읽기 전용 · " + time.strftime("%Y-%m-%d %H:%M") + " 내보냄",
        "repo": _repo_url(),
        "contracts": [f.name for f in sorted((ROOT / "contracts").glob("*.yaml"))],
        "scenarios": [f.name for f in sorted((ROOT / "scenarios").glob("*.yaml"))],
    })

    for n in ("app.js", "plot.js", "style.css"):
        shutil.copy2(sv.WEB / n, out / n)
    #  app.js 보다 먼저 실행돼야 한다 — 로드 순서상 plot.js 앞에 끼운다.
    html = (sv.WEB / "index.html").read_text().replace(
        '<script src="plot.js">',
        '<script>window.STATIC = 1;</script>\n  <script src="plot.js">')
    (out / "index.html").write_text(html)
    #  Jekyll 은 _ 로 시작하는 이름을 숨긴다 — 그대로 두면 사라지는 파일이 생긴다.
    (out / ".nojekyll").write_text("")

    n_files = sum(1 for _ in out.rglob("*") if _.is_file())
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"내보냈습니다 → {out}")
    print(f"  실행 {len(picked)}개 · 파일 {n_files}개 · {size / 1e6:.1f}MB")
    for b in picked:
        print(f"    {b['id']}  {b.get('scenario') or ''}")
    return 0
