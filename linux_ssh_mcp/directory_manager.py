from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import asyncssh

from linux_ssh_mcp.auth_manager import SSHCredentials
from linux_ssh_mcp.connection_pool import ConnectionPool, LeasedConnection
from linux_ssh_mcp.settings import SSHMCPSettings


@dataclass
class _Session:
    session_id: str
    leased: LeasedConnection
    process: asyncssh.SSHClientProcess
    last_used_monotonic: float


class DirectoryManager:
    def __init__(
        self,
        *,
        settings: SSHMCPSettings,
        pool: ConnectionPool,
        loop_provider: Callable[[], asyncio.AbstractEventLoop] = asyncio.get_running_loop,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._time: Callable[[], float] | None = None
        self._loop_provider = loop_provider
        self._lock = asyncio.Lock()
        self._sessions: dict[str, _Session] = {}

    def _now(self) -> float:
        if self._time is None:
            self._time = self._loop_provider().time
        return float(self._time())

    async def list_directory(
        self,
        *,
        host: str,
        port: int,
        credentials: SSHCredentials,
        path: str,
        page: int = 1,
        page_size: int = 100,
        filter_pattern: str | None = None,
    ) -> dict[str, Any]:
        if page <= 0:
            raise ValueError("page必须>=1")
        if page_size <= 0:
            raise ValueError("page_size必须>=1")
        if not path.strip():
            raise ValueError("path不能为空")

        compiled: re.Pattern[str] | None = None
        if filter_pattern:
            compiled = re.compile(filter_pattern)

        async with self._pool.acquire_connection(
            host=host,
            port=port,
            credentials=credentials,
        ) as conn:
            async with conn.start_sftp_client() as sftp:
                names = await sftp.listdir(path)

        names = sorted(str(n) for n in names)
        if compiled is not None:
            names = [n for n in names if compiled.search(n) is not None]

        total = len(names)
        start = (page - 1) * page_size
        end = min(start + page_size, total)
        page_items = names[start:end] if start < total else []

        return {
            "host": host,
            "port": port,
            "path": path,
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": page_items,
            "filter_pattern": filter_pattern,
        }

    async def execute_interactive(
        self,
        *,
        host: str,
        port: int,
        credentials: SSHCredentials,
        command: str,
        session_id: str | None = None,
        close_session: bool = False,
    ) -> dict[str, Any]:
        if not command.strip() and not close_session:
            raise ValueError("command不能为空")

        await self._cleanup_expired_sessions()

        sess = await self._get_or_create_session(
            host=host,
            port=port,
            credentials=credentials,
            session_id=session_id,
        )

        if close_session:
            await self._close_session(sess.session_id)
            return {"session_id": sess.session_id, "closed": True}

        marker = f"__MCP_DONE_{uuid.uuid4().hex}__"
        wrapped = f"{command}\necho {marker}$?{marker}\n"

        sess.last_used_monotonic = self._now()
        sess.process.stdin.write(wrapped)
        await sess.process.stdin.drain()

        stdout, exit_status, stderr = await self._read_until_marker(
            process=sess.process,
            marker=marker,
            timeout_seconds=float(self._settings.command_timeout_seconds),
        )

        sess.last_used_monotonic = self._now()
        return {
            "session_id": sess.session_id,
            "closed": False,
            "exit_status": exit_status,
            "stdout": stdout,
            "stderr": stderr,
        }

    async def close_all_sessions(self) -> int:
        async with self._lock:
            session_ids = list(self._sessions.keys())

        for sid in session_ids:
            await self._close_session(sid)

        return len(session_ids)

    async def _get_or_create_session(
        self,
        *,
        host: str,
        port: int,
        credentials: SSHCredentials,
        session_id: str | None,
    ) -> _Session:
        async with self._lock:
            if session_id and session_id in self._sessions:
                sess = self._sessions[session_id]
                sess.last_used_monotonic = self._now()
                return sess

        leased = await self._pool.lease_connection(host=host, port=port, credentials=credentials)
        try:
            process = await leased.connection.create_process(
                "/bin/bash",
                term_type="xterm",
                encoding="utf-8",
            )
        except Exception:
            await leased.release(close=True)
            raise

        new_id = session_id or uuid.uuid4().hex
        sess = _Session(
            session_id=new_id,
            leased=leased,
            process=process,
            last_used_monotonic=self._now(),
        )
        async with self._lock:
            self._sessions[new_id] = sess
        return sess

    async def _cleanup_expired_sessions(self) -> None:
        ttl = float(self._settings.idle_connection_ttl_seconds)
        now = self._now()

        async with self._lock:
            expired: list[str] = []
            for sid, s in self._sessions.items():
                if (now - s.last_used_monotonic) > ttl:
                    expired.append(sid)

        for sid in expired:
            await self._close_session(sid)

    async def _close_session(self, session_id: str) -> None:
        async with self._lock:
            sess = self._sessions.pop(session_id, None)

        if sess is None:
            return

        try:
            sess.process.stdin.write("exit\n")
            await sess.process.stdin.drain()
        except Exception:
            pass

        try:
            sess.process.terminate()
        except Exception:
            pass

        await sess.leased.release(close=False)

    async def _read_until_marker(
        self,
        *,
        process: asyncssh.SSHClientProcess,
        marker: str,
        timeout_seconds: float,
    ) -> tuple[str, int, str]:
        stdout_buf: list[str] = []
        stderr_buf: list[str] = []
        combined = ""

        async def try_read_stderr() -> None:
            try:
                chunk = await asyncio.wait_for(process.stderr.read(4096), timeout=0.01)
                if chunk:
                    stderr_buf.append(chunk)
            except Exception:
                return

        async def read_loop() -> tuple[str, int]:
            nonlocal combined
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    break
                stdout_buf.append(chunk)
                combined += chunk
                if marker in combined:
                    break
            idx = combined.find(marker)
            if idx < 0:
                return (combined, 0)
            before = combined[:idx]
            after = combined[idx + len(marker) :]
            idx2 = after.find(marker)
            if idx2 < 0:
                return (before, 0)
            code_text = after[:idx2]
            try:
                code = int(code_text)
            except Exception:
                code = 0
            return (before, code)

        try:
            task = asyncio.create_task(read_loop())
            while not task.done():
                await try_read_stderr()
                await asyncio.sleep(0.01)
            stdout_text, code = await asyncio.wait_for(task, timeout=timeout_seconds)
        except Exception as err:
            raise TimeoutError("交互命令执行超时") from err
        finally:
            await try_read_stderr()

        return (stdout_text, int(code), "".join(stderr_buf))
