"""계약에 적는 조건식의 안전한 평가기.

계약은 신뢰할 수 있는 로컬 파일이지만, 그래도 `eval` 을 그대로 열어 두지 않는다.
AST 를 훑어 ★비교·산술·논리·이름·상수★ 만 허용하고 나머지는 거부한다.
함수 호출·속성 접근·구독은 전부 막는다(abs/min/max 만 예외).

    ok = evaluate("conf_eff >= 0.35 and abs(theta_deg) <= 15", row)
"""
from __future__ import annotations

import ast
import math

_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.UnaryOp, ast.BinOp, ast.Compare,
    ast.Name, ast.Load, ast.Constant, ast.And, ast.Or, ast.Not,
    ast.USub, ast.UAdd, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
    ast.FloorDiv, ast.Pow, ast.BitAnd, ast.BitOr, ast.BitXor,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Call,
    ast.IfExp,
)
_ALLOWED_CALLS = {"abs", "min", "max", "int", "float", "round", "isnan"}
_FUNCS = {"abs": abs, "min": min, "max": max, "int": int, "float": float,
          "round": round, "isnan": lambda x: x != x or math.isnan(float(x))}

_cache = {}


def compile_expr(src):
    """조건식을 검증하고 코드 객체로. 문법·허용 위반이면 SyntaxError."""
    if src in _cache:
        return _cache[src]
    tree = ast.parse(src, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise SyntaxError(f"허용되지 않은 표현: {type(node).__name__} — `{src}`")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_CALLS:
                raise SyntaxError(f"허용되지 않은 함수 호출 — `{src}` "
                                  f"(가능: {', '.join(sorted(_ALLOWED_CALLS))})")
    code = compile(tree, "<contract>", "eval")
    _cache[src] = code
    return code


def names(src):
    """식이 참조하는 신호 이름들 — 계약 검증에 쓴다."""
    return {n.id for n in ast.walk(ast.parse(src, mode="eval"))
            if isinstance(n, ast.Name) and n.id not in _ALLOWED_CALLS}


def evaluate(src, row):
    """행 하나에 식을 적용. 값이 없거나 계산 불가면 None(=판정 보류)."""
    code = compile_expr(src)
    env = dict(_FUNCS)
    for k, v in row.items():
        if isinstance(v, (int, float)) and not (isinstance(v, float) and v != v):
            env[k] = v
        elif isinstance(v, str):
            env[k] = v
    try:
        return bool(eval(code, {"__builtins__": {}}, env))   # noqa: S307
    except (NameError, TypeError, ZeroDivisionError, ValueError):
        return None
