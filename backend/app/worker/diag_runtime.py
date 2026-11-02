"""生成流程工程的运行时。发布时复制进生成目录，和 flow.py 一起运行。

它不碰数据库：节点执行、人工等待、分叉、回退全部通过 stdout 的 JSON 行交给后端，
人工输入从 stdin 读回来。业务函数的 print 会被挪到 stderr，否则一行日志就能冲掉协议。

一个进程只负责一条路径：跑完当前节点链，遇到分叉就上报 fork 事件然后退出，
由后端为每个分支另起进程。人工节点就停在 input() 上等人。
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

MAX_DEPTH = 5

HERE = Path(__file__).resolve().parent

# 节点函数返回它表示「到此为止，成功收尾」，与「没有后继」区分开
STOP = "__terminal__"

YES_VALUES = {"yes", "y", "true", "是"}
NO_VALUES = {"no", "n", "false", "否"}

_MISSING = object()
_protocol = None


class Rollback(Exception):
    """人工选择回退：交回后端，由它作废这一代并另起进程。"""

    def __init__(self, target: str, branch: str = "default", reason: str = "") -> None:
        self.target = target
        self.branch = branch
        self.reason = reason
        super().__init__(f"回退到 {target}（{branch}）")


class NodeFailed(Exception):
    """节点执行失败，run_flow 会把它变成一条 error 事件。

    reported=True 表示失败已经以 error/call 事件上报过，run_flow 不要再发第二条。
    """

    def __init__(self, code: str, message: str, reported: bool = False) -> None:
        self.code = code
        self.reported = reported
        super().__init__(message)


class Context:
    """节点函数能看到的全部外部状态。"""

    def __init__(self, args_table: dict) -> None:
        self.snapshot = _load_json("context.json", {})
        self._external = _load_json("runs.json", {})
        self._args = args_table or {}
        self._runs: dict[str, object] = {}
        self.instance_id = os.environ.get("DIAG_INSTANCE", "")

    # ------------------------------------------------------------ 参数

    def args(self, node_id: str, function: str = "") -> dict:
        """按编译期绑定的来源表达式求值；optional 缺值就省略，交给函数默认值。"""
        specs = self._args.get(node_id) or {}
        values: dict[str, object] = {}
        for name, expr in specs.items():
            if not isinstance(expr, dict):
                raise NodeFailed("execution/argument", f"参数 {name} 的表达式不合法")
            optional = bool(expr.get("optional"))
            try:
                if "lit" in expr:
                    values[name] = expr["lit"]
                elif "snapshot" in expr:
                    found, value = _snapshot_value(self.snapshot, function, str(expr["snapshot"]))
                    if not found:
                        if optional:
                            continue
                        raise NodeFailed(
                            "execution/argument", f"启动上下文里没有字段 {expr['snapshot']}"
                        )
                    values[name] = value
                elif "run" in expr:
                    found, value = self.prev(str(expr["run"]), str(expr.get("path") or ""))
                    if not found:
                        if optional:
                            continue
                        raise NodeFailed(
                            "execution/argument",
                            f"找不到节点 {expr['run']} 的输出 {expr.get('path')}",
                        )
                    values[name] = value
                else:
                    raise NodeFailed("execution/argument", f"参数 {name} 的表达式不合法")
            except NodeFailed:
                raise
            except Exception as exc:  # noqa: BLE001 - 取数失败按参数错误归类
                raise NodeFailed("execution/argument", f"参数 {name} 求值失败：{exc}") from exc
        return values

    def prev(self, node_id: str, path: str = "") -> tuple[bool, object]:
        """取上游节点的输出。本进程跑过的用原值，其余用后端写下的 JSON 投影。"""
        if node_id in self._runs:
            root = self._runs[node_id]
        elif node_id in self._external:
            root = self._external[node_id]
        else:
            return False, None
        parts = [part for part in str(path).split(".") if part]
        if parts and parts[0] == "output":
            parts = parts[1:]
        return _lookup(root, ".".join(parts))

    # ------------------------------------------------------------ 执行

    def call(self, node_id: str, func, function: str = ""):
        """调业务函数并上报。业务异常一律记 execution/exception，不重试。"""
        kwargs = self.args(node_id, function)
        started = time.perf_counter()
        try:
            value = func(**kwargs)
        except BaseException as exc:  # noqa: BLE001 - 业务代码什么都能抛
            # 业务异常本身记一条 error 事件（带实参，方便排查），不再单独发 call 失败事件
            _emit(
                {
                    "t": "error",
                    "node": node_id,
                    "code": "execution/exception",
                    "message": str(exc) or exc.__class__.__name__,
                    "args": _json_safe(kwargs),
                    "traceback": traceback.format_exc()[-2000:],
                    "ms": int((time.perf_counter() - started) * 1000),
                }
            )
            raise NodeFailed("execution/exception", str(exc) or exc.__class__.__name__, reported=True) from exc
        self._runs[node_id] = value
        _emit(
            {
                "t": "call",
                "node": node_id,
                "args": _json_safe(kwargs),
                "ok": True,
                "value": _json_safe(value),
                "ms": int((time.perf_counter() - started) * 1000),
            }
        )
        return value

    def branch(self, node_id: str, value) -> str:
        """排查节点的分支判定，只认是/否两种；其他值属于执行错误。"""
        if value is True:
            return "yes"
        if value is False:
            return "no"
        if isinstance(value, str):
            text = value.strip().lower()
            if text in YES_VALUES:
                return "yes"
            if text in NO_VALUES:
                return "no"
        _emit(
            {
                "t": "error",
                "node": node_id,
                "code": "route/no-outgoing",
                "message": f"返回值 {value!r} 无法判定为是/否",
            }
        )
        raise NodeFailed("route/no-outgoing", f"返回值 {value!r} 无法判定为是/否")

    def ask(self, node_id: str, prompt: str = "", kind: str = "decision") -> str:
        """人工待办：发事件后停在 input() 上，直到后端往 stdin 写一行。

        回退不是一个分支键，是控制流中断，所以这里直接抛异常交给 run_flow。
        """
        _emit({"t": "ask", "node": node_id, "prompt": prompt, "kind": kind})
        raw = input()
        decision = _parse_decision(raw)
        if decision.get("result") == "rollback":
            raise Rollback(
                str(decision.get("target") or node_id),
                str(decision.get("branch") or "default"),
                str(decision.get("reason") or ""),
            )
        return str(decision.get("result") or "")

    def finish(self, node_id: str, kind: str) -> None:
        _emit({"t": "finish", "node": node_id, "kind": kind})

    def fork(self, node_id: str, targets: list[str], branch: str) -> None:
        _emit({"t": "fork", "node": node_id, "targets": list(targets), "branch": branch})


def run_flow(nodes: dict, nxt: dict, entry: str, terminals: dict, args: dict) -> int:
    """派发循环。只负责把 current 往下推，真正的逻辑都在节点函数里。"""
    _claim_stdout()
    ctx = Context(args)
    current = os.environ.get("DIAG_START") or entry

    while current:
        if current in terminals:
            _emit({"t": "enter", "node": current})
            ctx.finish(current, terminals[current])
            return 0

        step = nodes.get(current)
        if step is None:
            _emit(
                {
                    "t": "error",
                    "node": current,
                    "code": "plan/step-missing",
                    "message": f"生成的工程里没有节点 {current}",
                }
            )
            return 1

        _emit({"t": "enter", "node": current})
        try:
            branch = step(ctx)
        except Rollback as exc:
            _emit(
                {
                    "t": "rollback",
                    "node": current,
                    "target": exc.target,
                    "branch": exc.branch,
                    "reason": exc.reason,
                }
            )
            return 0
        except NodeFailed as exc:
            if not exc.reported:
                _emit(
                    {"t": "error", "node": current, "code": exc.code, "message": str(exc)}
                )
            return 1
        except Exception as exc:  # noqa: BLE001 - 生成代码自身出错，不能静默退出
            _emit(
                {
                    "t": "error",
                    "node": current,
                    "code": "execution/exception",
                    "message": f"{exc}\n{traceback.format_exc()[-2000:]}",
                }
            )
            return 1

        # 节点函数正常返回即算完成；call 事件只负责落 run，不负责收尾令牌
        _emit({"t": "leave", "node": current, "branch": branch})

        targets = (nxt.get(current) or {}).get(branch) or []
        if not targets:
            _emit(
                {
                    "t": "error",
                    "node": current,
                    "code": "route/no-outgoing",
                    "message": f"分支 {branch} 没有后继节点",
                }
            )
            return 1
        if len(targets) > 1:
            # 分叉交给后端：它按分支逐个起进程，各自带自己的 stdin
            ctx.fork(current, targets, branch)
            return 0
        current = targets[0]

    return 0


# ---------------------------------------------------------------- 协议


def _claim_stdout() -> None:
    """独占真正的 stdout 说协议，把 1 号描述符让给业务代码的 print。"""
    global _protocol
    if _protocol is not None:
        return
    _protocol = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr


def _emit(payload: dict) -> None:
    if _protocol is None:
        _claim_stdout()
    _protocol.write(json.dumps(payload, ensure_ascii=False, default=repr) + "\n")
    _protocol.flush()


def _parse_decision(raw: str) -> dict:
    """后端写回一行 JSON；人在终端手敲时退化成纯文本。"""
    text = (raw or "").strip()
    if not text:
        return {"result": ""}
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            pass
    return {"result": text}


# ---------------------------------------------------------------- 小工具


def _load_json(name: str, default):
    path = HERE / name
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _snapshot_value(snapshot: dict, function: str, name: str) -> tuple[bool, object]:
    """启动参数取值：该函数的作用域 > 全局 > 旧扁平载荷。

    新快照形如 {"global": {...}, "scope": {"judge_voltage": {...}}}，
    旧快照是扁平 {"voltage": 12}，两种都要能取到。
    """
    scope = snapshot.get("scope")
    if function and isinstance(scope, dict):
        section = scope.get(function)
        if isinstance(section, dict) and name in section:
            return True, section[name]

    globals_ = snapshot.get("global")
    if isinstance(globals_, dict):
        found, value = _lookup(globals_, name)
        if found:
            return True, value

    layered = isinstance(globals_, dict) or isinstance(scope, dict)
    if layered and name.split(".", 1)[0] in ("global", "scope"):
        return False, None  # 分层载荷里这两层不是字段名，避免整层被打包传进函数
    return _lookup(snapshot, name)


def _lookup(root, path: str) -> tuple[bool, object]:
    current = root
    if not path:
        return True, current
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            if part not in current:
                return False, None
            current = current[part]
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return False, None
        else:
            return False, None
    return True, current


def _json_safe(value, depth: int = 0):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else repr(value)
    if depth >= MAX_DEPTH:
        return _opaque(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, depth + 1) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_json_safe(item, depth + 1) for item in value]
    return _opaque(value)


def _opaque(value) -> dict:
    return {"__type__": type(value).__name__, "repr": repr(value)}
