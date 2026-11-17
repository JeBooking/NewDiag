"""终端下的并行入口，使用与平台相同的生成代码和事件协议。"""

import json
import os
import queue
import subprocess
import sys
import threading
import uuid
from collections import deque

from .generated_runtime import Context
from .processes import process_options, stop_tree


def run(directory, configuration, entry, limit):
    if limit < 1:
        raise ValueError("并行上限必须大于零")
    events = queue.Queue()
    pending = deque([{"node": entry, "token": uuid.uuid4().hex}])
    active = set()
    results = []
    launch_dir = directory / "local-branches"
    launch_dir.mkdir(exist_ok=True)
    terminal = Context(configuration, {})
    questions = queue.Queue()

    def ask():
        while True:
            item = questions.get()
            if item is None:
                return
            process, request = item
            try:
                response = terminal.ask(request)
            except Exception as exc:
                response = {"error": str(exc)}
            events.put((process, {"type": "answer", "response": response}))

    threading.Thread(target=ask, daemon=True).start()

    def pump(process):
        try:
            for line in process.stdout:
                events.put((process, json.loads(line)))
        except Exception:
            stop_tree(process)
            raise
        finally:
            process.wait()
            events.put((process, None))

    try:
        while pending or active:
            while pending and len(active) < limit:
                initial = pending.popleft()
                initial["configuration"] = configuration
                path = launch_dir / f"{initial['token']}.json"
                path.write_text(json.dumps(initial, ensure_ascii=False), encoding="utf-8")
                process = subprocess.Popen(
                    [sys.executable, str(directory / "flow.py"), "--worker", str(path)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
                    text=True, encoding="utf-8", cwd=directory,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"}, **process_options(),
                )
                active.add(process)
                threading.Thread(target=pump, args=(process,), daemon=True).start()
            process, event = events.get()
            if event is None:
                active.remove(process)
                continue
            if process not in active:
                continue
            response = {}
            kind = event["type"]
            if kind == "enter":
                response = {"token": event["token"]}
                print(f"进入节点 {event['node']}", file=sys.stderr)
            elif kind == "ask":
                questions.put((process, event["request"]))
                continue
            elif kind == "answer":
                response = event["response"]
            elif kind == "complete":
                entries = [{"node": node, "token": uuid.uuid4().hex} for node in event["targets"]]
                response = {"entries": entries}
                for item in entries[1:]:
                    pending.append({**item, "history": event["history"], "states": event["states"]})
                meta = configuration["meta"].get(event["node"], {})
                if meta.get("kind") in ("problemSolved", "escalation"):
                    results.append(meta["kind"])
                print(json.dumps(event.get("output"), ensure_ascii=False), file=sys.stderr)
            elif kind == "error":
                print(event["message"], file=sys.stderr)
            process.stdin.write(json.dumps(response, ensure_ascii=False) + "\n")
            process.stdin.flush()
    finally:
        questions.put(None)
        for process in active:
            stop_tree(process)
    return 0 if "problemSolved" in results else 1
