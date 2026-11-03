"""平台内置四阶段编排；业务函数无须继承 SDK 或使用固定方法名。"""

import json
import time

from .node_sdk import json_value, presentation, validate_fields

STAGES = ("acquire", "parse", "present", "judge")
SOURCES = {"raw", "parsed", "decision", "inputs"}


class PipelineFailure(Exception):
    def __init__(self, failure):
        self.failure = failure
        super().__init__(f"{failure[0]}: {failure[1]}")


def default_argument(stage: str, index: int):
    if index == 0:
        return {"source": "raw" if stage == "parse" else "parsed"}
    if stage == "present" and index == 1:
        return {"source": "decision"}
    return None


def map_arguments(binding, state):
    result = {}
    for name, expression in binding.get("arguments", {}).items():
        if "lit" in expression:
            result[name] = expression["lit"]
            continue
        value = (state.get("inputContext", state.get("inputs"))
                 if expression["source"] == "inputs" else state.get(expression["source"]))
        for part in expression.get("path", "").split("."):
            if not part:
                continue
            if isinstance(value, dict) and part in value:
                value = value[part]
            elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                value = value[int(part)]
            else:
                raise ValueError(f"参数 {name} 的来源路径不存在：{expression['source']}.{expression['path']}")
        result[name] = value
    return result


def normalize_presentation(value):
    if value is None:
        raise ValueError("展示函数必须返回文本、JSON 数据或 blocks 描述，不能只操作桌面窗口")
    if isinstance(value, dict) and "blocks" in value:
        return presentation(value)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    return presentation({"blocks": [{"type": "text", "text": text}]})


def normalize_decision(value, function):
    if isinstance(value, dict):
        result = value.get("result")
        reason = value.get("reason")
    else:
        result = value
        reason = f"函数 {function} 返回 {value!r}"
    if result is True:
        result = "yes"
    elif result is False:
        result = "no"
    if result not in ("yes", "no") or not isinstance(reason, str) or not reason.strip():
        raise ValueError("判断函数须返回布尔值、yes/no，或包含 result 和非空 reason 的对象")
    return {"result": result, "reason": reason}


def execute_pipeline(pipeline, state, operation, invoke, *, data=None, decision=None):
    json_value(state)

    def run(stage, arguments=None):
        binding = pipeline[stage]
        args = arguments if arguments is not None else map_arguments(binding, state)
        started = time.perf_counter()
        try:
            value = json_value(invoke(binding, args))
        except PipelineFailure as exc:
            code, message, attempts = exc.failure
            state["stages"].append({"stage": stage, "arguments": args, "error": message})
            raise PipelineFailure((code, f"{stage}: {message}", attempts)) from exc
        except Exception as exc:
            state["stages"].append({"stage": stage, "arguments": args, "error": str(exc)})
            raise ValueError(f"{stage}: {exc}") from exc
        state["stages"].append({
            "stage": stage, "project": binding["project"], "function": binding["function"],
            "arguments": args, "durationMs": round((time.perf_counter() - started) * 1000),
        })
        return value

    if operation == "start" and not pipeline.get("acquire"):
        return {"status": "acquisition", "state": state, "fields": pipeline["manualFields"]}
    if operation in ("start", "acquisition"):
        if operation == "start":
            state["raw"] = run("acquire", state["inputs"])
        else:
            state["raw"] = validate_fields(pipeline["manualFields"], data)
            state["stages"].append({"stage": "manualAcquisition"})
        state["parsed"] = run("parse")
        state["decision"] = None
        state["presentation"] = normalize_presentation(run("present"))
        if not pipeline.get("judge"):
            return {"status": "judgment", "state": state}
        decision = normalize_decision(run("judge"), pipeline["judge"]["function"])
    elif operation == "judgment":
        decision = normalize_decision(decision, "人工判断")
        state["stages"].append({"stage": "manualJudgment"})
    else:
        raise ValueError("未知四阶段执行操作")
    state["decision"] = decision
    state["presentation"] = normalize_presentation(run("present"))
    return {"status": "complete", "state": state}
