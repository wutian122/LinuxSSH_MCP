import asyncio

import pytest

from linux_ssh_mcp.auth_manager import SSHCredentials
from linux_ssh_mcp.connection_pool import ConnectionPool
from linux_ssh_mcp.settings import SSHMCPSettings


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False
        self.closed_count = 0

    def is_closing(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True
        self.closed_count += 1

    async def wait_closed(self) -> None:
        return


@pytest.mark.asyncio
async def test_connection_pool_reuses_connection(mocker) -> None:
    fake = FakeConnection()

    async def connect_side_effect(**_kwargs):
        return fake

    connect = mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

    pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
    creds = SSHCredentials(host="1.2.3.4", username="root", password="p", private_key_path=None)

    async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds) as c1:
        assert c1 is fake

    async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds) as c2:
        assert c2 is fake

    assert connect.call_count == 1


@pytest.mark.asyncio
async def test_connection_pool_limits_concurrency(mocker) -> None:
    created: list[FakeConnection] = []

    async def connect_side_effect(**_kwargs):
        c = FakeConnection()
        created.append(c)
        return c

    mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

    pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=2))
    creds = SSHCredentials(host="1.2.3.4", username="root", password="p", private_key_path=None)

    entered: list[int] = []
    release = asyncio.Event()

    async def worker(i: int) -> None:
        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds):
            entered.append(i)
            await release.wait()

    tasks = [asyncio.create_task(worker(i)) for i in range(3)]
    try:
        await asyncio.sleep(0.05)
        assert len(entered) == 2
        release.set()
        await asyncio.gather(*tasks)
        assert len(entered) == 3
    finally:
        for t in tasks:
            t.cancel()


@pytest.mark.asyncio
async def test_connection_pool_cleans_idle_connections(mocker) -> None:
    fake = FakeConnection()
    to_return = [fake]

    async def connect_side_effect(**_kwargs):
        return to_return.pop(0)

    mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

    now = 100.0

    def time_provider() -> float:
        return now

    pool = ConnectionPool(
        settings=SSHMCPSettings(per_host_max_connections=5, idle_connection_ttl_seconds=10),
        time_provider=time_provider,
    )
    creds = SSHCredentials(host="1.2.3.4", username="root", password="p", private_key_path=None)

    async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds):
        pass

    now = 200.0

    fake2 = FakeConnection()
    to_return.append(fake2)
    async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds) as c2:
        assert c2 is fake2

    assert fake.closed is True
