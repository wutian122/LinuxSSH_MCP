import hashlib
import re
from pathlib import Path

import pytest

from linux_ssh_mcp.auth_manager import SSHCredentials
from linux_ssh_mcp.file_transfer_manager import FileTransferManager
from linux_ssh_mcp.settings import SSHMCPSettings


class _FakeAttrs:
    def __init__(self, *, size: int) -> None:
        self.size = size
        self.permissions = 0
        self.mtime = 0
        self.atime = 0


class _FakeRemoteFile:
    def __init__(self, store: dict[str, bytearray], path: str, mode: str) -> None:
        self._store = store
        self._path = path
        self._mode = mode
        if "w" in mode:
            self._store[path] = bytearray()
        if "a" in mode and path not in self._store:
            self._store[path] = bytearray()
        self._pos = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def write(self, data: bytes) -> None:
        buf = self._store[self._path]
        buf.extend(data)

    async def read(self, n: int) -> bytes:
        buf = bytes(self._store[self._path])
        if self._pos >= len(buf):
            return b""
        chunk = buf[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    async def seek(self, offset: int) -> None:
        self._pos = offset


class _FakeSFTP:
    def __init__(self, store: dict[str, bytearray]) -> None:
        self._store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def stat(self, path: str):
        if path not in self._store:
            raise FileNotFoundError(path)
        return _FakeAttrs(size=len(self._store[path]))

    def open(self, path: str, mode: str):
        return _FakeRemoteFile(self._store, path, mode)


class _FakeCompleted:
    def __init__(self, stdout: str, exit_status: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.exit_status = exit_status


class _FakeConn:
    def __init__(self, store: dict[str, bytearray]) -> None:
        self._store = store

    def start_sftp_client(self):
        return _FakeSFTP(self._store)

    async def run(self, command: str, **_kwargs):
        if "md5sum" in command:
            m = re.search(r"(['\"])(/[^'\"]+)\1", command)
            if m:
                path = m.group(2)
            else:
                path = next((p for p in command.split() if p.startswith("/")), "")
            data = bytes(self._store.get(path, b""))
            h = hashlib.md5(data).hexdigest()
            return _FakeCompleted(stdout=f"{h}  {path}\n")
        return _FakeCompleted(stdout="")


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire_connection(self, *, host: str, port: int, credentials: SSHCredentials):
        class _CM:
            async def __aenter__(_self):
                return self._conn

            async def __aexit__(_self, exc_type, exc, tb):
                return False

        return _CM()


@pytest.mark.asyncio
async def test_upload_file_and_md5(tmp_path: Path) -> None:
    store: dict[str, bytearray] = {}
    conn = _FakeConn(store)
    pool = _FakePool(conn)
    mgr = FileTransferManager(settings=SSHMCPSettings(command_timeout_seconds=30), pool=pool)  # type: ignore[arg-type]

    local = tmp_path / "a.bin"
    local.write_bytes(b"hello world")
    creds = SSHCredentials(host="1.2.3.4", username="root", password=None, private_key_path=None)

    res = await mgr.upload_file(
        host="1.2.3.4",
        port=22,
        credentials=creds,
        local_path=str(local),
        remote_path="/tmp/a.bin",
        verify_md5=True,
        chunk_size=4,
    )
    assert bytes(store["/tmp/a.bin"]) == b"hello world"
    assert res.md5_match is True


@pytest.mark.asyncio
async def test_download_file(tmp_path: Path) -> None:
    store: dict[str, bytearray] = {"/tmp/a.bin": bytearray(b"hello")}
    conn = _FakeConn(store)
    pool = _FakePool(conn)
    mgr = FileTransferManager(settings=SSHMCPSettings(command_timeout_seconds=30), pool=pool)  # type: ignore[arg-type]

    local = tmp_path / "b.bin"
    creds = SSHCredentials(host="1.2.3.4", username="root", password=None, private_key_path=None)

    res = await mgr.download_file(
        host="1.2.3.4",
        port=22,
        credentials=creds,
        remote_path="/tmp/a.bin",
        local_path=str(local),
        verify_md5=True,
        chunk_size=2,
    )
    assert local.read_bytes() == b"hello"
    assert res.md5_match is True


@pytest.mark.asyncio
async def test_get_file_info() -> None:
    store: dict[str, bytearray] = {"/tmp/a.bin": bytearray(b"hello")}
    conn = _FakeConn(store)
    pool = _FakePool(conn)
    mgr = FileTransferManager(settings=SSHMCPSettings(command_timeout_seconds=30), pool=pool)  # type: ignore[arg-type]
    creds = SSHCredentials(host="1.2.3.4", username="root", password=None, private_key_path=None)

    info = await mgr.get_file_info(host="1.2.3.4", port=22, credentials=creds, path="/tmp/a.bin")
    assert info["size"] == 5
