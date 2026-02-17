import pytest

from linux_ssh_mcp.auth_manager import SSHCredentials
from linux_ssh_mcp.cache_manager import CacheManager
from linux_ssh_mcp.connection_pool import ConnectionPool
from linux_ssh_mcp.settings import SSHMCPSettings
from linux_ssh_mcp.ssh_manager import SSHManager
from linux_ssh_mcp.token_optimizer import TokenOptimizer


class FakeCompleted:
    def __init__(self, *, stdout: str, stderr: str, exit_status: int) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_status = exit_status


class FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def run(self, command: str, *, input=None, **_kwargs):
        self.calls.append((command, input))
        if "echo" in command:
            return FakeCompleted(stdout="ok\nerror: bad\n", stderr="", exit_status=0)
        if "hostname" in command:
            return FakeCompleted(stdout="host1\n", stderr="", exit_status=0)
        return FakeCompleted(stdout="out\n", stderr="", exit_status=0)


class FakePool(ConnectionPool):
    def __init__(self, *, settings: SSHMCPSettings, conn: FakeConn) -> None:
        self._settings = settings
        self._conn = conn

    def acquire_connection(self, *, host: str, port: int, credentials: SSHCredentials):
        class _CM:
            async def __aenter__(_self):
                return self._conn

            async def __aexit__(_self, exc_type, exc, tb):
                return False

        return _CM()


@pytest.mark.asyncio
async def test_execute_command_caches_output() -> None:
    settings = SSHMCPSettings(command_timeout_seconds=30)
    conn = FakeConn()
    pool = FakePool(settings=settings, conn=conn)
    cache = CacheManager(settings=SSHMCPSettings(cache_maxsize=128))
    mgr = SSHManager(
        settings=settings,
        pool=pool,
        cache=cache,
        token_optimizer=TokenOptimizer(),
    )
    creds = SSHCredentials(host="1.2.3.4", username="root", password=None, private_key_path=None)

    r1 = await mgr.execute_command(host="1.2.3.4", port=22, credentials=creds, command="echo hi")
    r2 = await mgr.execute_command(host="1.2.3.4", port=22, credentials=creds, command="echo hi")

    assert r1.stdout == r2.stdout
    assert len(conn.calls) == 1


@pytest.mark.asyncio
async def test_execute_command_filter_mode() -> None:
    settings = SSHMCPSettings(command_timeout_seconds=30)
    conn = FakeConn()
    pool = FakePool(settings=settings, conn=conn)
    cache = CacheManager(settings=SSHMCPSettings(cache_maxsize=128))
    mgr = SSHManager(
        settings=settings,
        pool=pool,
        cache=cache,
        token_optimizer=TokenOptimizer(),
    )
    creds = SSHCredentials(host="1.2.3.4", username="root", password=None, private_key_path=None)

    r = await mgr.execute_command(
        host="1.2.3.4",
        port=22,
        credentials=creds,
        command="echo hi",
        token_mode="filter",
        filter_pattern=r"^error:",
        use_cache=False,
    )
    assert r.stdout.strip() == "error: bad"


@pytest.mark.asyncio
async def test_get_system_info_caches() -> None:
    settings = SSHMCPSettings(command_timeout_seconds=30, static_ttl_max_seconds=3600)
    conn = FakeConn()
    pool = FakePool(settings=settings, conn=conn)
    cache = CacheManager(settings=SSHMCPSettings(cache_maxsize=128))
    mgr = SSHManager(
        settings=settings,
        pool=pool,
        cache=cache,
        token_optimizer=TokenOptimizer(),
    )
    creds = SSHCredentials(host="1.2.3.4", username="root", password=None, private_key_path=None)

    a = await mgr.get_system_info(host="1.2.3.4", port=22, credentials=creds)
    b = await mgr.get_system_info(host="1.2.3.4", port=22, credentials=creds)
    assert a["hostname"]["stdout"] == "host1"
    assert a == b

