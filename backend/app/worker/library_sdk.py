"""能力配方运行时（library/v1）：在一个业务进程里按声明顺序调用现成库。

设计边界（为什么要这份文件）：
- 业务库不需要继承任何基类、不需要写适配器，也不需要把取数/绘图/判断拼成一个新类；
- 平台只在**节点内部**持有对象（Dataset、JudgeEngine 这类），**节点之间只传 JSON**，
  因此跨工程传递不需要序列化业务对象，也不会把某个库的生命周期带进另一个库；
- 展示由平台按返回值物化，业务库不必知道浏览器、附件或运行记录的存在。

只依赖标准库和业务库自身；由 worker/session.py 以 __newdiag_library__ 调用。
"""

from __future__ import annotations

import importlib
import inspect
import io
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

if __package__:
    from . import node_sdk
else:
    import node_sdk

logger = logging.getLogger(__name__)

PROTOCOL = "library/v1"

MAX_BLOCKS = 12
MAX_TABLE_ROWS = 200
MAX_TABLE_COLUMNS = 12
MAX_METRICS = 12
MAX_TEXT = 4000

YES_VALUES = {"yes", "y", "true", "是"}
NO_VALUES = {"no", "n", "false", "否"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# 「一步一个表达式」：表达式对象的第一个键必须是下面之一，其余键是选项（path/cast/label）
EXPRESSIONS = ("$snapshot", "$run", "$var", "$prev", "$file", "$value")

_MISS = object()
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_FILE = re.compile(r"^[A-Za-z0-9._-]+$")


class RecipeError(ValueError):
    """配方本身或运行期调用不成立，消息直接面向作者。"""


# ---------------------------------------------------------------- 名字解析


def resolve(target, dotted: str):
    """把点分名解析成可调用对象。

    顺序：先用 target 的属性（含字典键）逐级下钻；失败再把前缀当模块 import。
    这样 `Dataset.load`、`plot_window.save_plot`、`judgekit.JudgeEngine` 都无需额外登记，
    业务库也不必为了被平台调用而改 `__init__.py` 的导出。
    """
    if not isinstance(dotted, str) or not dotted.strip():
        raise RecipeError("调用名不能为空")
    parts = [part for part in dotted.split(".") if part]
    found = _walk(target, parts)
    if found is not _MISS:
        return found
    # root 上没有这个属性时，按最长前缀尝试 import 子模块
    for cut in range(len(parts) - 1, 0, -1):
        try:
            module = importlib.import_module(".".join(parts[:cut]))
        except Exception:  # noqa: BLE001 - 不是模块就继续缩短前缀
            continue
        found = _walk(module, parts[cut:])
        if found is not _MISS:
            return found
    raise RecipeError(f"工程里找不到 {dotted}")


def _walk(obj, parts):
    for part in parts:
        if isinstance(obj, dict):
            if part not in obj:
                return _MISS
            obj = obj[part]
            continue
        try:
            obj = getattr(obj, part)
        except AttributeError:
            return _MISS
    return obj


# ---------------------------------------------------------------- 上下文


class _Context:
    def __init__(self, root, request: dict) -> None:
        self.root = root
        self.project = str(request.get("project") or "")
        self.vars: dict[str, object] = {}
        self.prev = None
        self.steps: list[dict] = []
        self.notes: list[str] = []
        self.blocks: list[dict] = []
        self._seen_images: set[str] = set()
        base = request.get("artifactRoot")
        self.scratch = (Path(base) / "library" / uuid.uuid4().hex) if base else None
        self.files: list[Path] = []

    def get_var(self, name: str):
        if name not in self.vars:
            known = "、".join(self.vars) or "无"
            raise RecipeError(f"步骤引用了未产生的对象 {name!r}（当前可用：{known}）")
        return self.vars[name]

    def allocate(self, name) -> str:
        """给业务库一个可写路径；调用结束后图片会自动进入展示块。"""
        if not isinstance(name, str) or not _SAFE_FILE.fullmatch(name):
            raise RecipeError(f"文件名 {name!r} 不合法：只允许字母、数字、点、下划线和短横线")
        if self.scratch is None:
            raise RecipeError("当前调用没有可写目录，无法分配文件路径")
        self.scratch.mkdir(parents=True, exist_ok=True)
        path = self.scratch / name
        self.files.append(path)
        return str(path)


# ---------------------------------------------------------------- 执行


def execute(root, request: dict) -> dict:
    """执行一个节点的全部步骤，返回运行记录需要的全部内容。"""
    ctx = _Context(root, request)
    steps = request.get("steps")
    if not isinstance(steps, list) or not steps:
        raise RecipeError("配方没有可执行的步骤")

    for index, step in enumerate(steps):
        started = time.perf_counter()
        record = _run_step(ctx, index, step)
        record["durationMs"] = round((time.perf_counter() - started) * 1000)
        ctx.steps.append(record)

    output = _output(ctx, request.get("out"))
    result, reason, human = _decision(ctx, request)
    return {
        "status": "judgment" if human else "complete",
        "output": output,
        "presentation": node_sdk.presentation({"blocks": ctx.blocks[:MAX_BLOCKS]}),
        "result": result,
        "reason": reason,
        "steps": ctx.steps,
        "notes": ctx.notes,
        "project": ctx.project,
    }


def _run_step(ctx: _Context, index: int, step: dict) -> dict:
    if not isinstance(step, dict):
        raise RecipeError(f"第 {index + 1} 步格式不正确")
    name = str(step.get("call") or "").strip()
    on = step.get("on")
    target = ctx.get_var(str(on)) if on else ctx.root
    function = resolve(target, name)

    args = evaluate(step.get("args") or {}, ctx)
    if not isinstance(args, dict):
        raise RecipeError(f"{name} 的参数必须是对象")
    _check_arguments(function, args, name)

    before = len(ctx.files)
    value = function(**args)
    _collect_files(ctx, before, step.get("label"))

    ctx.prev = value
    slot = step.get("as")
    if slot:
        slot = str(slot)
        if not _NAME.fullmatch(slot):
            raise RecipeError(f"保存名 {slot!r} 不合法")
        ctx.vars[slot] = value

    for block in materialize(value, str(step.get("show") or "auto"), step.get("label")):
        _append_block(ctx, block)

    return {
        "index": index + 1, "call": name, "on": str(on or ""),
        "constructor": inspect.isclass(function),
        "saved": str(slot or ""), "returned": _summary(value),
    }


def _check_arguments(function, args: dict, name: str) -> None:
    """调用前先按真实签名试绑一次：错误信息比 TypeError 更贴近作者视角。"""
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return
    try:
        signature.bind(**args)
    except TypeError as exc:
        kind = "构造参数" if inspect.isclass(function) else "参数"
        raise RecipeError(f"{name} 的{kind}不匹配：{exc}") from exc


# ---------------------------------------------------------------- 表达式


def evaluate(value, ctx: _Context):
    """递归求值：带 $ 表达式首键的对象求值，其余容器逐项下钻，标量原样返回。"""
    if isinstance(value, dict):
        keys = list(value)
        if keys and keys[0] in EXPRESSIONS:
            return _expression(keys[0], value, ctx)
        return {key: evaluate(item, ctx) for key, item in value.items()}
    if isinstance(value, list):
        return [evaluate(item, ctx) for item in value]
    return value


def _expression(kind: str, spec: dict, ctx: _Context):
    payload = spec.get(kind)
    if kind == "$value":
        return payload
    if kind == "$var":
        name, path = _name_and_path(payload, spec)
        return _select(ctx.get_var(name), path)
    if kind == "$prev":
        return _select(ctx.prev, str(spec.get("path") or ""))
    if kind == "$file":
        return ctx.allocate(payload)
    # $snapshot / $run 由平台在调度侧求值，进入进程时已经变成 $value
    raise RecipeError(f"运行期不支持的表达式 {kind}")


def _name_and_path(payload, spec) -> tuple[str, str]:
    if isinstance(payload, str):
        return payload, str(spec.get("path") or "")
    if isinstance(payload, dict):
        return str(payload.get("name") or ""), str(payload.get("path") or "")
    raise RecipeError(f"$var 需要对象名，收到 {payload!r}")


def _select(value, path: str):
    current = value
    for part in [item for item in str(path).split(".") if item]:
        if isinstance(current, dict):
            if part not in current:
                raise RecipeError(f"字段 {path} 不存在：{part} 不在字典里")
            current = current[part]
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise RecipeError(f"字段 {path} 的数组下标无效：{part}") from exc
        else:
            try:
                current = getattr(current, part)
            except AttributeError as exc:
                raise RecipeError(f"字段 {path} 不存在：{type(current).__name__} 没有 {part}") from exc
    return current


# ---------------------------------------------------------------- 产物与判定


def _output(ctx: _Context, spec) -> dict:
    output: dict = {}
    for name, source in (spec or {}).items():
        if not isinstance(source, dict) or "from" not in source:
            raise RecipeError(f"输出 {name} 必须写成 {{from: 步骤保存名}}")
        value = _select(ctx.get_var(str(source["from"])), str(source.get("path") or ""))
        output[str(name)] = _json_ready(value, name)
    return output


def _json_ready(value, name: str):
    if isinstance(value, os.PathLike):
        return str(value)
    try:
        return node_sdk.json_value(value)
    except (TypeError, ValueError) as exc:
        raise RecipeError(
            f"输出 {name} 不能转成 JSON（{type(value).__name__}）；"
            f"请挑标量字段（例如 {name}.label）或保存为文件后传路径"
        ) from exc


def _decision(ctx: _Context, request: dict) -> tuple[str | None, str, bool]:
    reason = _reason(ctx, request.get("reason"))
    spec = request.get("result")
    if not spec:
        return None, reason, True
    if not isinstance(spec, dict) or "from" not in spec:
        raise RecipeError("result 必须写成 {from: 步骤保存名, path?: 字段}")
    value = _select(ctx.get_var(str(spec["from"])), str(spec.get("path") or ""))
    if value is None:
        return None, reason, True
    if value is True:
        return "yes", reason, False
    if value is False:
        return "no", reason, False
    text = str(value).strip().lower()
    if text in YES_VALUES:
        return "yes", reason, False
    if text in NO_VALUES:
        return "no", reason, False
    raise RecipeError(f"判定值 {value!r} 不是是/否；result 应指向 overall 这类字段")


def _reason(ctx: _Context, spec) -> str:
    if not spec:
        return ""
    if isinstance(spec, str):
        return spec
    if not isinstance(spec, dict):
        raise RecipeError("reason 必须是字符串或 {template}")
    template = spec.get("template")
    if template is None and "from" in spec:
        value = _select(ctx.get_var(str(spec["from"])), str(spec.get("path") or ""))
        return str(value)
    if not isinstance(template, str):
        raise RecipeError("reason.template 必须是字符串")

    def replace(match):
        name, _, path = match.group(1).partition(".")
        try:
            value = _select(ctx.get_var(name), path)
        except (RecipeError, KeyError):
            ctx.notes.append(f"依据模板引用了取不到的值 {match.group(1)}")
            return "?"
        return str(value)

    return re.sub(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}", replace, template)


# ---------------------------------------------------------------- 展示物化


def _collect_files(ctx: _Context, start: int, label) -> None:
    """本次步骤分配过的可写路径，落盘成图片就补一张图。"""
    for path in ctx.files[start:]:
        if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
            continue
        block = _image_block(path.read_bytes(), label or path.name)
        if block:
            _append_block(ctx, block)


def _append_block(ctx: _Context, block: dict) -> None:
    for key in ("title", "unit", "caption"):
        if key in block and not block[key]:
            block.pop(key)
    if block.get("type") == "image":
        src = block.get("src")
        if src in ctx._seen_images:
            return
        ctx._seen_images.add(src)
    ctx.blocks.append(block)


def materialize(value, mode: str = "auto", label=None) -> list[dict]:
    """把返回值变成平台展示块；auto 只出结构化内容和图片，不把路径/对象塞进页面。"""
    if mode == "none" or value is None:
        return []
    if isinstance(value, dict) and isinstance(value.get("blocks"), list):
        return [block for block in value["blocks"] if isinstance(block, dict)]

    # matplotlib 图：无窗口存成 PNG
    if hasattr(value, "savefig"):
        buffer = io.BytesIO()
        value.savefig(buffer, format="png", dpi=150, bbox_inches="tight")
        block = _image_block(buffer.getvalue(), label)
        return [block] if block else []

    if isinstance(value, bytes):
        block = _image_block(value, label)
        return [block] if block else []

    path = _image_path(value)
    if path is not None:
        block = _image_block(path.read_bytes(), label or path.name)
        return [block] if block else []

    if isinstance(value, dict) and _series_xy(value):
        x, y = _series_xy(value)
        title = label or value.get("label")
        return [{"type": "line", "values": y, "title": title, "unit": value.get("unit", "")}]

    if isinstance(value, (list, tuple)) and value and all(
        isinstance(item, (int, float)) and not isinstance(item, bool) for item in value
    ):
        if mode in ("auto", "line"):
            return [{"type": "line", "values": list(value), "title": label}]

    if isinstance(value, dict) and _scalars(value) and mode in ("auto", "metrics"):
        items = [{"label": key, "value": _plain(item)} for key, item in value.items()]
        if len(items) <= MAX_METRICS:
            return [{"type": "metrics", "items": items, "title": label}]

    if isinstance(value, (list, tuple)) and value and mode in ("auto", "table"):
        table = _table(value, label)
        if table:
            return [table]

    if isinstance(value, dict) and mode == "table":
        table = _table([value], label)
        if table:
            return [table]

    if mode in ("auto", "text"):
        text = _text(value)
        if text:
            return [{"type": "text", "text": text, "title": label}]
    return []


def _series_xy(value: dict):
    x, y = value.get("x"), value.get("y")
    if not isinstance(y, (list, tuple)) or not y:
        return None
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in y):
        return None
    if x is not None and (not isinstance(x, (list, tuple)) or len(x) != len(y)):
        return None
    return x, list(y)


def _scalars(value: dict) -> bool:
    if not value:
        return False
    return all(
        isinstance(item, (str, int, float, bool)) or item is None for item in value.values()
    )


def _plain(value):
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return str(value)
    return value


def _table(rows, label=None):
    rows = list(rows)[:MAX_TABLE_ROWS]
    if not rows:
        return None
    # 业务对象自带 as_dict() 时按字典展示（判断结论这类结构就是这样暴露字段的）
    if not any(isinstance(row, (dict, list, tuple)) for row in rows):
        converted = []
        for row in rows:
            method = getattr(row, "as_dict", None)
            if not callable(method):
                converted = []
                break
            converted.append(method())
        rows = converted or rows
    if all(isinstance(row, dict) for row in rows):
        columns: list[str] = []
        for row in rows:
            for key in row:
                if str(key) not in columns:
                    columns.append(str(key))
        columns = columns[:MAX_TABLE_COLUMNS]
        cells = [[_cell(row.get(column)) for column in columns] for row in rows]
    elif all(isinstance(row, (list, tuple)) for row in rows):
        width = len(rows[0])
        if width == 0 or any(len(row) != width for row in rows):
            return None
        columns = [str(index + 1) for index in range(min(width, MAX_TABLE_COLUMNS))]
        cells = [[_cell(item) for item in row[:MAX_TABLE_COLUMNS]] for row in rows]
    else:
        return None
    return {"type": "table", "columns": columns, "rows": cells, "title": label}


def _cell(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _plain(value)
    if isinstance(value, os.PathLike):
        return str(value)
    return json.dumps(value, ensure_ascii=False, default=str)[:200]


def _text(value) -> str:
    if isinstance(value, str):
        return value[:MAX_TEXT]
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, os.PathLike):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return ""
        return text[:MAX_TEXT]
    return ""


def _image_path(value) -> Path | None:
    if isinstance(value, os.PathLike):
        candidate = Path(value)
    elif isinstance(value, str) and "\n" not in value and len(value) < 512:
        candidate = Path(value)
    else:
        return None
    if candidate.suffix.lower() not in IMAGE_SUFFIXES or not candidate.is_file():
        return None
    return candidate


def _image_block(data: bytes, label=None):
    try:
        digest = node_sdk.save_file(data)
    except (ValueError, OSError) as exc:
        logger.warning("图片没有登记为附件：%s", exc)
        return None
    block = {"type": "image", "src": digest}
    if label:
        block["caption"] = str(label)[:200]
    return block


def _summary(value) -> str:
    if value is None:
        return "None"
    if isinstance(value, os.PathLike):
        return f"{type(value).__name__}: {value}"
    if isinstance(value, (str, int, float, bool)):
        text = str(value)
        return text if len(text) <= 80 else text[:77] + "..."
    if isinstance(value, (list, tuple, dict, set)):
        return f"{type(value).__name__}[{len(value)}]"
    return type(value).__name__
