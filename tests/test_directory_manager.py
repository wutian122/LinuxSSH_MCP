import asyncio

import pytest

from linux_ssh_mcp.auth_manager import SSHCredentials
from linux_ssh_mcp.directory_manager import DirectoryManager
from linux_ssh_mcp.settings import SSHMCPSettings


class _FakeStdin:
    def __init__(self, process: "_FakeProcess") -> None:
        self._process = process

    def write(self, data: str) -> None:
        self._process.on_input(data)

    async def drain(self) -> None:
        return


class _FakeStream:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    def push(self, data: str) -> None:
        self._queue.put_nowait(data)

    async def read(self, _n: int) -> str:
        if self._queue.empty():
            await asyncio.sleep(0)
            return ""
        return await self._queue.get()


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = _FakeStdin(self)
        self.stdout = _FakeStream()
        self.stderr = _FakeStream()
        self._terminated = False

    def on_input(self, data: str) -> None:
        m = None
        for line in data.splitlines():
            if line.startswith("echo __MCP_DONE_"):
                m = line.split("echo ", 1)[1]
        if m is None:
            return
        marker = m.split("$?", 1)[0]
        self.stdout.push("out\n")
        self.stdout.push(f"{marker}0{marker}")

    def terminate(self) -> None:
        self._terminated = True


class _FakeSFTP:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def listdir(self, _path: str):
        return list(self._names)


class _FakeConn:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def start_sftp_client(self):
        return _FakeSFTP(self._names)

    async def create_process(self, *_args, **_kwargs):
        return _FakeProcess()


class _FakeLeased:
    def __init__(self, conn: _FakeConn) -> None:
        self.connection = conn
        self.released = False
        self.closed = False

    async def release(self, *, close: bool = False) -> None:
        self.released = True
        self.closed = close


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn
        self.leased: _FakeLeased | None = None

    def acquire_connection(self, *, host: str, port: int, credentials: SSHCredentials):
        class _CM:
            async def __aenter__(_self):
                return self._conn

            async def __aexit__(_self, exc_type, exc, tb):
                return False

        return _CM()

    async def lease_connection(self, *, host: str, port: int, credentials: SSHCredentials):
        self.leased = _FakeLeased(self._conn)
        return self.leased


@pytest.mark.asyncio
async def test_list_directory_paging_and_filter() -> None:
    conn = _FakeConn(["a.txt", "b.log", "c.txt", "d.txt"])
    pool = _FakePool(conn)
    mgr = DirectoryManager(settings=SSHMCPSettings(), pool=pool)  # type: ignore[arg-type]
    creds = SSHCredentials(host="1.2.3.4", username="root", password=None, private_key_path=None)

    res = await mgr.list_directory(
        host="1.2.3.4",
        port=22,
        credentials=creds,
        path="/tmp",
        page=1,
        page_size=2,
        filter_pattern=r"\.txt$",
    )
    assert res["total"] == 3
    assert res["items"] == ["a.txt", "c.txt"]


@pytest.mark.asyncio
async def test_execute_interactive_reuses_session_and_can_close() -> None:
    conn = _FakeConn([])
    pool = _FakePool(conn)
    mgr = DirectoryManager(settings=SSHMCPSettings(command_timeout_seconds=30), pool=pool)  # type: ignore[arg-type]
    creds = SSHCredentials(host="1.2.3.4", username="root", password=None, private_key_path=None)

    r1 = await mgr.execute_interactive(
        host="1.2.3.4",
        port=22,
        credentials=creds,
        command="pwd",
    )
    sid = r1["session_id"]
    assert r1["stdout"].startswith("out")

    r2 = await mgr.execute_interactive(
        host="1.2.3.4",
        port=22,
        credentials=creds,
        command="ls",
        session_id=sid,
    )
    assert r2["session_id"] == sid

    r3 = await mgr.execute_interactive(
        host="1.2.3.4",
        port=22,
        credentials=creds,
        command="",
        session_id=sid,
        close_session=True,
    )
    assert r3["closed"] is True
    assert pool.leased is not None
    assert pool.leased.released is True

