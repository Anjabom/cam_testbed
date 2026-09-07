"""이 기계의 실측 캘리브 값 → 스튜디오가 여는 JSON.

스튜디오(web/)는 카메라 K·D 만 실측값을 들고 있고, 맞추는 값(사각형·ROI·척도·문턱)은
자리표시자에서 시작한다. 지난번에 맞춘 값을 그대로 이어 쓰려면 «불러오기» 로 JSON 을
여는데, 그 파일을 이 기계의 local.yaml 에서 처음 만들어 주는 것이 이 도구다.

    python3 tools/tuning_from_local.py                    # → my_tuning.json
    python3 tools/tuning_from_local.py --contract contracts/white_vote.yaml

무엇을 어디서 가져오나:
    · 맞춘 값 (사각형·ROI·척도·문턱)  ← local.yaml 의 params:
    · 카메라 내부값 (size·K·D·alpha)  ← 계약의 calibration.undistort
      (계약이 파일을 가리키면 그 파일에서 읽는다 — tb.geometry 가 하는 것과 같다)

★파라미터 이름은 여기 없다★ 이름은 web/tuning.js 한 곳에만 있고, 이 도구는
node 로 그 파일을 읽어 이름을 얻는다. 양쪽에 이름을 적어 두면 한쪽만 고쳐진다.

my_tuning.json 은 git 에서 빠진다(맞춘 값이 곧 이 차의 기하라서 그렇다).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tb.run import load_yaml               # noqa: E402
from tb.geometry import load_camera_yaml   # noqa: E402

READ_TUNING = (
    "const fs=require('fs'),vm=require('vm');"
    "const s={window:{}};vm.createContext(s);"
    "vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),s);"
    "process.stdout.write(JSON.stringify(s.window.TUNING));"
)


def read_tuning():
    """web/tuning.js 를 node 로 읽어 그대로 받아 온다."""
    p = subprocess.run(["node", "-e", READ_TUNING, str(ROOT / "web" / "tuning.js")],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit("web/tuning.js 를 읽지 못했습니다 (node 가 필요합니다):\n"
                         + p.stderr.strip())
    return json.loads(p.stdout)


def camera_from_contract(path):
    """계약의 calibration.undistort → 스튜디오의 camera 블록."""
    c = load_yaml(path) or {}
    u = ((c.get("calibration") or {}).get("undistort")) or {}
    if u.get("file"):
        #  경로가 상대면 계약 파일 기준 — tb.geometry.undistorter 와 같은 규칙
        f = Path(u["file"])
        f = f if f.is_absolute() else (Path(path).parent / f)
        size, K, D = load_camera_yaml(f)
    else:
        size, K, D = u.get("size"), u.get("K"), u.get("D")
    if not (size and K and D):
        return None
    return {"size": [int(size[0]), int(size[1])],
            "K": [float(v) for v in K], "D": [float(v) for v in D],
            "alpha": float(u.get("alpha") or 0.0)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", default="contracts/white_camera.yaml",
                    help="카메라 내부값(K·D)을 가져올 계약")
    ap.add_argument("--out", default="my_tuning.json")
    a = ap.parse_args()

    tun = read_tuning()
    local = load_yaml(ROOT / "local.yaml") or {}
    params = local.get("params") or {}

    cam = camera_from_contract(ROOT / a.contract)
    if cam:
        tun["camera"] = cam
    else:
        print(f"알림: {a.contract} 에 calibration.undistort 가 없어 카메라 값은 기본값 그대로 둡니다")

    found, missing = [], []
    for t in tun["targets"]:
        got = False
        for p in (t.get("params") or []):
            node, name = p[0], p[1]
            idx = p[2] if len(p) > 2 else None
            v = (params.get(node) or {}).get(name)
            if v is None:
                continue
            if idx is None:
                t["value"] = v
            else:
                #  ROI·BEV 크기처럼 값 하나가 파라미터 넷으로 흩어진 것
                if not isinstance(t["value"], list):
                    continue
                t["value"][idx] = v
            got = True
        (found if got else missing).append(t["label"])

    out = ROOT / a.out
    out.write_text(json.dumps(tun, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"썼다: {out}")
    print("  가져온 것 : " + (", ".join(found) or "없음"))
    if missing:
        print("  local.yaml 에 없어 기본값인 것 : " + ", ".join(missing))
    print("  → 스튜디오에서 «불러오기» 로 이 파일을 연다")


if __name__ == "__main__":
    main()
