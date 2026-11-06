"""会话子进程管理：每个实例一个常驻进程，串行收发 JSON 行。"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

WORKER = Path(__file__).resolve().parent.parent / "worker" / "session.py"
DEFAULT_TIMEOUT = 120.0

logger = logging.getLogger(__name__)


class SessionError(Exception):
    """会话层错误基类。"""


class SessionTimeout(SessionError):
    """调用超时，进程已被 kill。"""


class SessionCrashed(SessionError):
    """子进程退出或协议中断。"""


@dataclass
class SessionHandle:
    instance_id: str
    proc: subprocess.Popen
    config_path: str
    inbox: "queue.Queue" = field(default_factory=queue.Queue)
    errors: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    seq: int = 0
    project_key: tuple = ()


class SessionManager:
    """进程内单例式管理器；线程安全，数据库无关。"""

    def __init__(self) -> None:
        self._handles: dict[str | tuple[str, str], SessionHandle] = {}
        self._lock = threading.Lock()
        self._cancelled: set[str] = set()

    def ensure(self, instance_id: str, project, *, isolated=False) -> SessionHandle:
        with self._lock:
            if instance_id in self._cancelled:
                raise SessionError("实例已终止，不能启动会话")
            key = (instance_id, getattr(project, 'name', None) or project.path) if isolated else instance_id
            handle = self._handles.get(key)
            project_key = tuple(getattr(project, key, None) for key in ("path", "module", "python", "factory"))
            if handle is not None and handle.proc.poll() is None and handle.project_key == project_key:
                return handle
            if handle is not None:
                self._dispose(handle)
            handle = self._spawn(instance_id, project, isolated=isolated)
            handle.project_key = project_key
            self._handles[key] = handle
            return handle

    def call(
        self,
        instance_id: str,
        project,
        function: str,
        args: dict,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        """返回 (value, files)；files 是本次调用新落盘的附件清单，可能为空。"""
        handle = self.ensure(instance_id, project, isolated=function == '__newdiag_composition__')
        with handle.lock:
            handle.seq += 1
            request_id = str(handle.seq)
            started = time.perf_counter()
            logger.debug(
                "实例 %s 会话调用 %s#%s 参数 %s",
                instance_id,
                function,
                request_id,
                sorted(args.keys()),
            )
            line = json.dumps(
                {"id": request_id, "function": function, "args": args}, ensure_ascii=False
            )
            try:
                assert handle.proc.stdin is not None
                handle.proc.stdin.write(line + "\n")
                handle.proc.stdin.flush()
            except (OSError, ValueError) as exc:
                self.close(instance_id)
                raise SessionCrashed(f"会话写入失败：{exc}") from exc

            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "实例 %s 调用 %s 超时（%.0fs），kill 会话", instance_id, function, timeout
                    )
                    self._kill(handle)
                    self.close(instance_id)
                    raise SessionTimeout(f"调用 {function} 超时（{timeout}s）")
                try:
                    message = handle.inbox.get(timeout=remaining)
                except queue.Empty:
                    logger.warning(
                        "实例 %s 调用 %s 等待响应超时（%.0fs），kill 会话",
                        instance_id,
                        function,
                        timeout,
                    )
                    self._kill(handle)
                    self.close(instance_id)
                    raise SessionTimeout(f"调用 {function} 超时（{timeout}s）") from None
                if message is None:
                    detail = "".join(handle.errors[-20:]).strip()
                    logger.warning(
                        "实例 %s 会话进程退出（函数 %s）：%s", instance_id, function, detail[-300:]
                    )
                    self.close(instance_id)
                    raise SessionCrashed(f"会话进程已退出：{detail}" if detail else "会话进程已退出")
                if message.get("id") is None and not message.get("ok"):
                    # 启动失败时子进程只能输出 id=null 的错误行
                    self.close(instance_id)
                    raise SessionError(str(message.get("error") or "会话启动失败"))
                if message.get("id") != request_id:
                    continue  # 迟到的响应，丢弃
                if message.get("ok"):
                    logger.info(
                        "实例 %s 调用 %s 完成 %.0fms",
                        instance_id,
                        function,
                        (time.perf_counter() - started) * 1000,
                    )
                    # files 是本次调用新落盘的附件清单；旧子进程不带这个字段
                    return message.get("value"), message.get("files") or []
                logger.warning(
                    "实例 %s 调用 %s 抛错：%s",
                    instance_id,
                    function,
                    str(message.get("error") or "调用失败")[:300],
                )
                raise SessionError(str(message.get("error") or "调用失败"))

    def cancel(self, instance_id: str) -> None:
        # 标记与摘除使用同一把锁，避免取消后并发 ensure 重建进程。
        with self._lock:
            self._cancelled.add(instance_id)
            handles = [self._handles.pop(key) for key in list(self._handles)
                       if key == instance_id or isinstance(key, tuple) and key[0] == instance_id]
        for handle in handles:
            logger.info("实例 %s 终止会话 pid=%s", instance_id, handle.proc.pid)
            self._kill(handle)
            handle.inbox.put(None)
            self._dispose(handle)

    def clear_cancellation(self, instance_id: str) -> None:
        with self._lock:
            self._cancelled.discard(instance_id)

    def close(self, instance_id: str) -> None:
        with self._lock:
            handles = [self._handles.pop(key) for key in list(self._handles)
                       if key == instance_id or isinstance(key, tuple) and key[0] == instance_id]
        for handle in handles:
            logger.info("实例 %s 会话关闭 pid=%s", instance_id, handle.proc.pid)
            self._dispose(handle)

    def shutdown(self) -> None:
        with self._lock:
            instances = {handle.instance_id for handle in self._handles.values()}
        for instance_id in instances:
            self.close(instance_id)

    # ------------------------------------------------------------ 内部

    def _spawn(self, instance_id: str, project, *, isolated=False) -> SessionHandle:
        base = {
            "path": getattr(project, "path", "") or "",
            "module": getattr(project, "module", "") or "",
            "factory": '' if isolated else getattr(project, "factory", "") or "",
        }
        fd, config_path = tempfile.mkstemp(prefix="diag-session-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(base, fh, ensure_ascii=False)

        python = getattr(project, "python", None) or sys.executable
        cwd = base["path"] or None
        # stdio 编码在解释器启动时就固定了，只能启动前指定；子进程里改环境变量不起作用。
        # 只动 stdio：PYTHONUTF8=1 会连 open() 的默认编码一起改成 UTF-8，打挂读 GBK 文件的业务脚本
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        try:
            proc = subprocess.Popen(
                [python, str(WORKER), config_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=env,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            _remove(config_path)
            raise SessionCrashed(f"无法启动会话子进程：{exc}") from exc

        handle = SessionHandle(instance_id=instance_id, proc=proc, config_path=config_path)
        logger.info(
            "实例 %s 会话启动 pid=%s python=%s cwd=%s",
            instance_id,
            proc.pid,
            python,
            cwd or "(默认)",
        )
        threading.Thread(target=_pump_stdout, args=(handle,), daemon=True).start()
        threading.Thread(target=_pump_stderr, args=(handle,), daemon=True).start()
        return handle

    def _kill(self, handle: SessionHandle) -> None:
        if handle.proc.poll() is None:
            try:
                handle.proc.kill()
            except OSError:
                pass

    def _dispose(self, handle: SessionHandle) -> None:
        proc = handle.proc
        try:
            if proc.poll() is None and proc.stdin is not None:
                proc.stdin.write('{"id":"0","op":"shutdown"}\n')
                proc.stdin.flush()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
        except (OSError, ValueError):
            self._kill(handle)
        finally:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
            _remove(handle.config_path)
            handle.inbox.put(None)


def _pump_stdout(handle: SessionHandle) -> None:
    stream = handle.proc.stdout
    if stream is None:
        handle.inbox.put(None)
        return
    try:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                handle.inbox.put(json.loads(line))
            except json.JSONDecodeError:
                handle.errors.append(f"无法解析的响应行：{line[:200]}\n")
                logger.warning("实例 %s 会话输出无法解析：%s", handle.instance_id, line[:200])
    except UnicodeDecodeError as exc:
        # 解码失败必须暴露，不能让坏字节被替换成 U+FFFD 落库
        handle.errors.append(f"会话输出不是 UTF-8：{exc}\n")
        logger.error("实例 %s 会话输出不是 UTF-8：%s", handle.instance_id, exc)
    finally:
        handle.inbox.put(None)


def _pump_stderr(handle: SessionHandle) -> None:
    stream = handle.proc.stderr
    if stream is None:
        return
    # 按字节读并逐行解码：业务 print 都被引到 stderr，一旦解码失败就停读，
    # 管道写满会把子进程阻塞到超时，所以坏字节只标记、不停读。
    source = getattr(stream, "buffer", None) or stream
    try:
        for chunk in source:
            line, decoded = _decode_stderr(chunk)
            if not decoded:
                handle.errors.append("会话错误输出不是 UTF-8，已按字节转义记录\n")
                logger.error("实例 %s 会话错误输出不是 UTF-8", handle.instance_id)
            handle.errors.append(line)
            logger.debug("实例 %s 会话 stderr：%s", handle.instance_id, line.rstrip()[:500])
            if len(handle.errors) > 200:
                del handle.errors[:100]
    except (OSError, ValueError):
        return


def _decode_stderr(chunk) -> tuple[str, bool]:
    """返回 (文本, 是否按 UTF-8 正常解出)；坏字节转义保留，供排障。"""
    if isinstance(chunk, str):
        return chunk, True
    try:
        return chunk.decode("utf-8"), True
    except UnicodeDecodeError:
        return chunk.decode("utf-8", "backslashreplace"), False


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
