"""plan → 可运行 Python 工程（spec 第 5 节「生成」）。

发布时生成一次，产物随发布版本冻结。生成的目录里只有四样东西：
flow.py（流程图本身）、diag_runtime.py（派发器与参数求值）、
diag_projects.py（按登记信息加载业务工程）、以及运行期由后端写入的
context.json / runs.json。

不做常量折叠，也不做死代码消除：生成物要能被人读懂、能直接在终端里跑。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent / "worker"
RUNTIME_FILE = "diag_runtime.py"
PROJECTS_FILE = "diag_projects.py"
FLOW_FILE = "flow.py"

PROJECTS_SOURCE = '''"""按登记信息加载业务工程。

多个工程都塞进 sys.path，但不 chdir：一个进程只有一个当前目录，谁后 chdir 谁赢，
会让先加载的工程读相对路径失败。需要固定工作目录的工程应在启动实例时单独跑。
"""

from __future__ import annotations

import importlib
import inspect
import sys


def load_one(spec: dict):
    path = str(spec.get("path") or "")
    if path and path not in sys.path:
        sys.path.insert(0, path)
    module_name = str(spec.get("module") or "")
    if not module_name:
        raise ValueError("工程没有登记模块名")
    target = importlib.import_module(module_name)
    factory = str(spec.get("factory") or "")
    if factory:
        for part in factory.split("."):
            target = getattr(target, part)
        if inspect.isclass(target) or callable(target):
            target = target()
    return target


def resolve(target, name: str):
    """函数名允许点号路径，和会话子进程的口径一致。"""
    value = target
    for part in str(name).split("."):
        if isinstance(value, dict):
            value = value[part]
        else:
            value = getattr(value, part)
    if not callable(value):
        raise TypeError(f"{name} 不是可调用对象")
    return value
'''

HUMAN_PROMPTS = {
    "investigation": "按节点的判定依据选择「是」或「否」",
    "investigationAction": "完成动作后点「完成」；没做成请回退",
    "remediation": "完成动作后点「完成」；没做成请回退",
}


def generate(
    plan: dict,
    title: str,
    release_no: int,
    projects: dict[str, dict] | None = None,
) -> dict[str, str]:
    """返回 {文件名: 源码}。projects 是 {工程名: {path, module, factory}}。"""
    steps: dict = plan.get("steps") or {}
    entry = str(plan.get("entry") or "")
    if not entry:
        raise ValueError("步骤表缺少入口节点")

    projects = projects or {}
    used: set[str] = set()
    names: dict[str, str] = {}
    for node_id in sorted(steps):
        names[node_id] = _func_name(node_id, used)

    terminals = {
        node_id: str(step.get("kind"))
        for node_id, step in steps.items()
        if step.get("terminal")
    }
    args_table = {
        node_id: step["call"].get("args") or {}
        for node_id, step in steps.items()
        if step.get("call")
    }

    lines: list[str] = []
    lines.append(f'"""自动生成：流程「{title}」发布版本 v{release_no}。')
    lines.append("")
    lines.append("节点函数按 doc 里的节点顺序排列，改流程请回设计页改图后重新发布；")
    lines.append("直接改这个文件只影响手里这份副本，下一次发布会覆盖。")
    lines.append('"""')
    lines.append("")
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append("import diag_projects")
    lines.append("from diag_runtime import Rollback, run_flow")
    lines.append("")
    if projects:
        lines.extend(_project_block(projects))
    else:
        lines.append("PROJECTS: dict = {}")
        lines.append("")
    lines.extend(_args_block(args_table))
    lines.extend(_node_functions(steps, names))
    lines.extend(_tables(steps, names, terminals, entry))
    lines.append("")
    lines.append('if __name__ == "__main__":')
    lines.append("    raise SystemExit(run_flow(NODES, NEXT, ENTRY, TERMINAL, ARGS))")
    lines.append("")

    return {
        FLOW_FILE: "\n".join(lines),
        PROJECTS_FILE: PROJECTS_SOURCE,
        RUNTIME_FILE: (RUNTIME / RUNTIME_FILE).read_text(encoding="utf-8"),
    }


def write(directory: Path, files: dict[str, str]) -> Path:
    """把生成物写到磁盘，返回 flow.py 的路径。"""
    directory.mkdir(parents=True, exist_ok=True)
    for name, source in files.items():
        (directory / name).write_text(source, encoding="utf-8", newline="\n")
    return directory / FLOW_FILE


# ---------------------------------------------------------------- 源码片段


def _project_block(projects: dict[str, dict]) -> list[str]:
    specs = {
        name: {
            "path": str(spec.get("path") or ""),
            "module": str(spec.get("module") or ""),
            "factory": str(spec.get("factory") or ""),
        }
        for name, spec in projects.items()
    }
    body = json.dumps(specs, ensure_ascii=False, indent=4, sort_keys=True)
    return [
        "# 节点绑定的业务工程，按登记信息加载",
        f"_PROJECT_SPECS = {body}",
        "PROJECTS = {name: diag_projects.load_one(spec) for name, spec in _PROJECT_SPECS.items()}",
        "",
    ]


def _args_block(args_table: dict[str, dict]) -> list[str]:
    if not args_table:
        return ["ARGS: dict = {}", ""]
    # 不能用 json.dumps：它会把 True/False/None 写成 true/false/null，那是非法 Python。
    body = _py_literal(args_table)
    return [
        "# 参数来源在编译期就绑定了：lit 是常量，snapshot 取启动上下文，run 取上游节点输出",
        f"ARGS = {body}",
        "",
    ]


def _py_literal(value: object, indent: int = 0) -> str:
    """把 JSON 兼容的 Python 值序列化成合法的 Python 字面量源码。"""
    pad = "    " * indent
    inner = "    " * (indent + 1)
    if value is None:
        return "None"
    if value is True:
        return "True"
    if value is False:
        return "False"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [
            f"{inner}{json.dumps(str(key), ensure_ascii=False)}: {_py_literal(item, indent + 1)}"
            for key, item in value.items()
        ]
        return "{\n" + ",\n".join(items) + f"\n{pad}}}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        items = [f"{inner}{_py_literal(item, indent + 1)}" for item in value]
        return "[\n" + ",\n".join(items) + f"\n{pad}]"
    return repr(value)


def _node_functions(steps: dict, names: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for node_id in sorted(steps, key=lambda item: (steps[item].get("kind") != "start", item)):
        step = steps[node_id]
        if step.get("terminal"):
            continue
        lines.extend(_one_function(node_id, names[node_id], step))
    return lines


def _one_function(node_id: str, name: str, step: dict) -> list[str]:
    kind = str(step.get("kind") or "")
    lines = [f"def {name}(ctx):"]
    lines.append(f'    """{_doc(step, kind)}"""')

    if step.get("human"):
        prompt = HUMAN_PROMPTS.get(kind, "处理完成后继续")
        review_kind = "decision" if kind == "investigation" else "action"
        if review_kind == "decision":
            lines.append(f'    return ctx.ask("{node_id}", "{prompt}", kind="decision")')
        else:
            lines.append(f'    ctx.ask("{node_id}", "{prompt}", kind="action")')
            lines.append('    return "default"')
        lines.append("")
        return lines

    call = step.get("call")
    if call:
        project = str(call.get("project") or "")
        function = str(call.get("function") or "")
        target = _call_target(project, function)
        lines.append(f'    value = ctx.call("{node_id}", {target}, function={json.dumps(function, ensure_ascii=False)})')
        lines.append(f'    return ctx.branch("{node_id}", value)')
        lines.append("")
        return lines

    if kind == "conclusion" and step.get("text"):
        lines.append(f"    # 结论：{_text(step.get('text'))}")
    lines.append('    return "default"')
    lines.append("")
    return lines


def _tables(steps: dict, names: dict[str, str], terminals: dict, entry: str) -> list[str]:
    lines = ["NODES = {"]
    for node_id in sorted(steps):
        if node_id in terminals:
            continue
        lines.append(f'    "{node_id}": {names[node_id]},')
    lines.append("}")
    lines.append("")

    lines.append("NEXT = {")
    for node_id in sorted(steps):
        next_map = steps[node_id].get("next") or {}
        if not next_map:
            continue
        rendered = json.dumps(next_map, ensure_ascii=False, sort_keys=True)
        lines.append(f'    "{node_id}": {rendered},')
    lines.append("}")
    lines.append("")

    body = json.dumps(terminals, ensure_ascii=False, sort_keys=True)
    lines.append(f"TERMINAL = {body}")
    lines.append("")
    lines.append(f'ENTRY = "{entry}"')
    return lines


def _call_target(project: str, function: str) -> str:
    base = f'PROJECTS["{project}"]'
    if not function:
        return f'diag_projects.resolve({base}, "")'
    if "." in function:
        return f'diag_projects.resolve({base}, "{function}")'
    return f"{base}.{function}"


def _func_name(node_id: str, used: set[str]) -> str:
    base = re.sub(r"\W", "_", node_id, flags=re.UNICODE).strip("_") or "node"
    if base[0].isdigit():
        base = "n_" + base
    name = "step_" + base
    index = 2
    while name in used:
        name = f"step_{base}_{index}"
        index += 1
    used.add(name)
    return name


def _doc(step: dict, kind: str) -> str:
    label = {
        "start": "开始",
        "investigation": "排查",
        "investigationAction": "排查动作",
        "conclusion": "结论",
        "remediation": "处置措施",
    }.get(kind, kind)
    call = step.get("call")
    if call:
        return _quote(f"{label}：调用 {call.get('project')}.{call.get('function')}")
    return label


def _quote(value: object) -> str:
    return str(value).replace("\\", "/").replace('"', "'").replace("\n", " ")


def _text(value: object) -> str:
    """把任意字符串塞进单行注释：换行会截断注释。"""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text[:120]
