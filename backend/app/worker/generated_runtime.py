"""生成流程的运行库；人工输入可由终端或平台接管。"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import artifacts, node_sdk
from .slot_runtime import SlotRuntime

sys.modules.setdefault("node_sdk", node_sdk)


def stamp():
    return datetime.now(timezone.utc).isoformat()


class Context:
    def __init__(self, configuration, initial, platform=False):
        self.config = configuration
        self.platform = platform
        self.channel = sys.stdout
        self.input = sys.stdin
        self.history = initial.get("history", {})
        self.states = initial.get("states", {})
        self.token = initial.get("token")
        self.generation = initial.get("generation", 1)
        self.workers = {}
        self.node = None
        self.output = None
        self.evidence = {}
        self.arguments = {}
        self.started = None
        self.pending = initial.get("pending", [])
        self.branch = "default"
        self.root = Path(configuration["artifactRoot"])
        node_sdk.configure_files(configuration["fileRoot"])

    def exchange(self, kind, **payload):
        event = {"type": kind, "token": self.token, "node": self.node,
                 "generation": self.generation, **payload}
        if not self.platform:
            if kind == "enter":
                print(f"进入节点 {self.node}", file=sys.stderr)
                return {"token": self.token or uuid.uuid4().hex}
            if kind == "complete":
                print(json.dumps(payload.get("output"), ensure_ascii=False), file=sys.stderr)
            elif kind == "error":
                print(payload["message"], file=sys.stderr)
            return {}
        self.channel.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.channel.flush()
        line = self.input.readline()
        if not line:
            raise RuntimeError("平台通信已经关闭")
        response = json.loads(line)
        if response.get("error"):
            raise RuntimeError(response["error"])
        return response

    def ask(self, request):
        if self.platform:
            return self.exchange("ask", request=request, states=self.states)
        print(request.get("title", self.node), file=sys.stderr)
        if request.get("error"):
            print(request["error"], file=sys.stderr)
        if request.get("presentation"):
            print(json.dumps(request["presentation"], ensure_ascii=False), file=sys.stderr)
        fields = request.get("fields", [])
        if fields:
            while True:
                print("请输入 JSON 对象：", file=sys.stderr)
                line = self.input.readline()
                if not line:
                    raise EOFError("终端输入已经关闭")
                try:
                    value = json.loads(line)
                    data = node_sdk.validate_fields(fields, value)
                    for field in fields:
                        if field["type"] == "file" and field["name"] in data:
                            item = data[field["name"]]
                            digest = item["hash"].removeprefix("sha256:")
                            path = Path(self.config["fileRoot"]) / digest[:2] / digest
                            if not path.is_file():
                                raise ValueError(f"文件不存在：{digest}")
                            data[field["name"]] = str(path) if request["type"] == "parameters" else {**item, "path": str(path)}
                    return {"result": "done", "data": data}
                except (ValueError, TypeError) as exc:
                    print(str(exc), file=sys.stderr)
        choices = ("yes", "no") if request.get("type") in ("decision", "judgment") else ("done",)
        if request.get("type") == "after_success":
            choices = ("continue", "done")
        while True:
            print(" / ".join(choices), file=sys.stderr)
            line = self.input.readline()
            if not line:
                raise EOFError("终端输入已经关闭")
            result = line.strip()
            if result in choices:
                return {"result": result, "reason": "终端人工确认"}

    def human_step(self, request, operation):
        while True:
            answer = self.ask(request)
            try:
                return operation(answer)
            except (ValueError, RuntimeError) as exc:
                request = {**request, "retry": True, "error": str(exc)}

    def invoke(self, project, function, arguments):
        if project in self.workers and self.workers[project].poll() is not None:
            self.workers.pop(project)
        if project not in self.workers:
            spec = self.config["projects"][project]
            directory = Path(sys.argv[0]).resolve().parent / "module-configs"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{uuid.uuid4().hex}.json"
            path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            self.workers[project] = subprocess.Popen(
                [spec.get("python") or sys.executable, str(Path(__file__).with_name("session.py")), str(path)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
                text=True, encoding="utf-8", cwd=spec["path"],
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
            )
        process = self.workers[project]
        request = {"id": uuid.uuid4().hex, "function": function, "args": arguments}
        process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
        if not line:
            raise RuntimeError(f"模块进程 {project} 已退出")
        response = json.loads(line)
        self.exchange("files", files=response.get("files", []))
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "模块调用失败"))
        return response["value"]

    def close(self):
        for process in self.workers.values():
            if process.poll() is None:
                try:
                    process.stdin.write('{"op":"shutdown"}\n')
                    process.stdin.flush()
                    process.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()

    def execute(self, node, step):
        self.node, self.output, self.evidence = node, None, {}
        self.arguments, self.started = {}, stamp()
        self.token = self.exchange("enter")["token"]
        meta = self.config.get("meta", {}).get(node, {})
        self.prompt = {"title": meta.get("title", node), "detail": meta.get("detail", ""),
                       "attachments": meta.get("attachments", []), "nodeKind": step["kind"]}
        if step.get("terminal"):
            self.branch = "default"
        elif step.get("slots"):
            self.branch = self.slots(step)
        elif step.get("human"):
            kind = "decision" if step["kind"] == "investigation" else "action"
            answer = self.ask({**self.prompt, "type": kind})
            self.output = answer
            self.branch = answer["result"] if kind == "decision" else "default"
        else:
            self.branch = "default"
        return self.branch

    def decision(self, result):
        if isinstance(result, dict):
            result = result.get("result")
        if isinstance(result, str):
            result = result.strip().lower()
        if result is True or isinstance(result, str) and result in ("yes", "y", "true", "是"):
            return "yes"
        if result is False or isinstance(result, str) and result in ("no", "n", "false", "否"):
            return "no"
        raise ValueError("判断结果必须为布尔值或 yes/no")

    def slots(self, step):
        """按固定链执行节点槽位；人工输入由平台或终端接管。"""
        def invoke(project, request):
            return self.invoke(project, "__newdiag_slot__", {**request, "fileRoot": self.config["fileRoot"]})

        runtime = SlotRuntime(self.root, invoke, self.config.get("fileRoot"), self.states.get("$slots"))
        runtime.inputs = self.config["input"].get("global", self.config["input"])
        try:
            fields = step.get("runtimeInputs") or []

            def execute(answer=None):
                parameters = dict(answer["data"]) if answer else {}
                return runtime.run(step, parameters)

            value = self.human_step({**self.prompt, "type": "parameters", "fields": fields}, execute) if fields else execute()
        finally:
            self.states["$slots"] = runtime.save()
        if step["kind"] == "start":
            self.output = {"initialized": True}
            return "default"
        blocks = value["blocks"]
        self.evidence = {"presentation": {"blocks": blocks}, "slots": {"stages": runtime.stages}}
        presentation_payload = {"blocks": blocks}
        if value["human"]:
            answer = self.ask({**self.prompt, "type": "decision", "presentation": presentation_payload})
            self.output = {"presentation": presentation_payload, "result": answer["result"],
                           "reason": answer.get("reason", "人工判断")}
            return self.decision(answer["result"])
        if value["result"] is None:
            answer = self.ask({**self.prompt, "type": "judgment", "presentation": presentation_payload})
            value["result"], value["reason"] = answer["result"], answer.get("reason", "人工判断")
        self.output = {"presentation": presentation_payload, "result": value["result"],
                       "reason": value["reason"]}
        return self.decision(value["result"])

    def run(self, nodes, entry):
        node = entry
        pending = [(item, item.get("history", {})) for item in reversed(self.pending)]
        solved = False
        while node:
            try:
                started = time.monotonic()
                targets = nodes[node](self)
                print(f"节点 {node} 完成 branch={self.branch} 用时={round((time.monotonic() - started) * 1000)}ms",
                      file=sys.stderr)
                self.history[node] = artifacts.save(self.root, {"input": self.arguments, "output": self.output})
                terminal = self.prompt["nodeKind"] in ("problemSolved", "escalation")
                solved = solved or self.prompt["nodeKind"] == "problemSolved"
                failed = not targets and not terminal
                response = self.exchange("complete", output=self.output, evidence=self.evidence,
                                         arguments=self.arguments, startedAt=self.started,
                                         branch=self.branch, targets=targets, states=self.states,
                                         history=self.history, failed=failed, files=node_sdk.take_files())
                if targets:
                    entries = response.get("entries") or [{"node": item, "token": uuid.uuid4().hex} for item in targets]
                    if self.config["runtime"]["mode"] == "serial":
                        pending.extend((item, copy.deepcopy(self.history)) for item in reversed(entries[1:]))
                    elif not self.platform:
                        raise RuntimeError("并行命令行执行须使用控制进程入口")
                    node, self.token = entries[0]["node"], entries[0]["token"]
                    continue
                if terminal and self.prompt["nodeKind"] == "problemSolved" and pending:
                    answer = self.ask({"type": "after_success", "title": "已找到问题，继续排查剩余思路？"})
                    if answer["result"] != "continue":
                        pending.clear()
            except Exception:
                self.exchange("error", message="execution/exception: " + traceback.format_exc(), states=self.states,
                              startedAt=self.started, arguments=self.arguments)
            if pending:
                item, self.history = pending.pop()
                node, self.token = item["node"], item["token"]
            else:
                node = None
        self.exchange("finish")
        return 0 if solved else 1


def main(nodes, entry):
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", help="平台启动配置文件")
    parser.add_argument("--mode", choices=("serial", "parallel"), default="serial")
    parser.add_argument("--max-parallel", type=int, default=4)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    directory = Path(sys.argv[0]).resolve().parent
    configuration = json.loads((directory / "configuration.json").read_text(encoding="utf-8"))
    if args.worker:
        initial = json.loads(Path(args.worker).read_text(encoding="utf-8"))
        configuration = initial.get("configuration", configuration)
        context = Context(configuration, initial, platform=True)
        try:
            return context.run(nodes, initial["node"])
        finally:
            context.close()
    else:
        configuration["runtime"] = {"mode": args.mode, "maxParallel": args.max_parallel}
        for spec in configuration["projects"].values():
            spec["path"] = str(directory / "modules" / Path(spec["path"]).name)
            if spec.get("python") and Path(spec["python"]).is_absolute() and not Path(spec["python"]).is_file():
                spec["python"] = None
        configuration["artifactRoot"] = str(directory / "local-artifacts")
        if (directory / "files").is_dir() or not Path(configuration["fileRoot"]).is_dir():
            configuration["fileRoot"] = str(directory / "files")
        if args.mode == "parallel":
            from .terminal_runner import run
            return run(directory, configuration, entry, args.max_parallel)
        else:
            context = Context(configuration, {})
            try:
                return context.run(nodes, entry)
            finally:
                context.close()
