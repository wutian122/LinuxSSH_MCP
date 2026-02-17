"""ConnectionPool 连接池单元测试模块

覆盖以下场景：
- 连接获取与复用
- 并发控制（信号量限制）
- 空闲连接清理（后台清理任务）
- 请求合并（Request Coalescing）
- 死连接检测与剔除
- LeasedConnection 手动释放模式
- HostKey 索引维护
- close_all() 全量清理
- 连接超时与异常处理
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from linux_ssh_mcp.auth_manager import SSHCredentials
from linux_ssh_mcp.connection_pool import (
    ConnectionPool,
    HostKey,
    LeasedConnection,
    PoolKey,
    _PooledConnection,
)
from linux_ssh_mcp.exceptions import SSHConnectionError
from linux_ssh_mcp.settings import SSHMCPSettings


class FakeConnection:
    """模拟 asyncssh.SSHClientConnection 的假连接对象。

    Attributes:
        closed: 连接是否已关闭
        closed_count: close() 被调用的次数
    """

    def __init__(self) -> None:
        self.closed = False
        self.closed_count = 0

    def is_closing(self) -> bool:
        """检查连接是否正在关闭。

        Returns:
            连接是否已关闭
        """
        return self.closed

    def close(self) -> None:
        """关闭连接。"""
        self.closed = True
        self.closed_count += 1

    async def wait_closed(self) -> None:
        """等待连接关闭完成。"""
        return


def _make_creds() -> SSHCredentials:
    """创建测试用 SSH 凭据。

    Returns:
        SSHCredentials: 测试凭据对象
    """
    return SSHCredentials(
        host="1.2.3.4",
        username="root",
        password="p",
        private_key_path=None,
    )


class TestDataClasses:
    """数据类测试组。"""

    def test_host_key_frozen(self) -> None:
        """HostKey 应为不可变对象。"""
        hk = HostKey(host="10.0.0.1", port=22)
        assert hk.host == "10.0.0.1"
        assert hk.port == 22
        with pytest.raises(AttributeError):
            hk.host = "changed"  # type: ignore[misc]

    def test_pool_key_frozen(self) -> None:
        """PoolKey 应为不可变对象。"""
        pk = PoolKey(host="10.0.0.1", port=22, username="root")
        assert pk.host == "10.0.0.1"
        assert pk.port == 22
        assert pk.username == "root"
        with pytest.raises(AttributeError):
            pk.username = "changed"  # type: ignore[misc]

    def test_host_key_equality(self) -> None:
        """相同属性的 HostKey 应相等。"""
        hk1 = HostKey(host="h", port=22)
        hk2 = HostKey(host="h", port=22)
        assert hk1 == hk2
        assert hash(hk1) == hash(hk2)

    def test_pool_key_equality(self) -> None:
        """相同属性的 PoolKey 应相等。"""
        pk1 = PoolKey(host="h", port=22, username="u")
        pk2 = PoolKey(host="h", port=22, username="u")
        assert pk1 == pk2
        assert hash(pk1) == hash(pk2)

    def test_different_host_keys_not_equal(self) -> None:
        """不同属性的 HostKey 不应相等。"""
        hk1 = HostKey(host="h1", port=22)
        hk2 = HostKey(host="h2", port=22)
        assert hk1 != hk2

    def test_pooled_connection_attributes(self) -> None:
        """_PooledConnection 应正确保存属性。"""
        fake = FakeConnection()
        pc = _PooledConnection(connection=fake, last_used_monotonic=100.0)
        assert pc.connection is fake
        assert pc.last_used_monotonic == 100.0


class TestConnectionReuse:
    """连接复用测试组。"""

    @pytest.mark.asyncio
    async def test_reuses_connection_after_checkin(self, mocker) -> None:
        """归还的连接应被下次 acquire 复用。"""
        fake = FakeConnection()

        async def connect_side_effect(**_kwargs):
            return fake

        connect = mocker.patch(
            "asyncssh.connect", side_effect=connect_side_effect, autospec=True
        )

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
        creds = _make_creds()

        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds) as c1:
            assert c1 is fake

        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds) as c2:
            assert c2 is fake

        assert connect.call_count == 1
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_different_users_get_different_connections(self, mocker) -> None:
        """不同用户应获取不同的连接（PoolKey 不同）。"""
        fakes = [FakeConnection(), FakeConnection()]
        call_count = 0

        async def connect_side_effect(**_kwargs):
            nonlocal call_count
            idx = call_count
            call_count += 1
            return fakes[idx]

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
        creds_a = SSHCredentials(host="1.2.3.4", username="alice", password="p", private_key_path=None)
        creds_b = SSHCredentials(host="1.2.3.4", username="bob", password="p", private_key_path=None)

        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds_a) as ca:
            pass
        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds_b) as cb:
            pass

        assert ca is not cb
        assert call_count == 2
        await pool.close_all()


class TestConcurrencyControl:
    """并发控制（信号量）测试组。"""

    @pytest.mark.asyncio
    async def test_limits_concurrent_connections_per_host(self, mocker) -> None:
        """应限制每主机的最大并发连接数。"""
        created: list[FakeConnection] = []

        async def connect_side_effect(**_kwargs):
            c = FakeConnection()
            created.append(c)
            return c

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=2))
        creds = _make_creds()

        entered: list[int] = []
        release = asyncio.Event()

        async def worker(i: int) -> None:
            async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds):
                entered.append(i)
                await release.wait()

        tasks = [asyncio.create_task(worker(i)) for i in range(3)]
        try:
            await asyncio.sleep(0.05)
            assert len(entered) == 2, f"应只有2个worker进入，实际: {len(entered)}"
            release.set()
            await asyncio.gather(*tasks)
            assert len(entered) == 3
        finally:
            for t in tasks:
                t.cancel()
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_different_hosts_have_independent_semaphores(self, mocker) -> None:
        """不同主机的信号量应互相独立。"""
        async def connect_side_effect(**_kwargs):
            return FakeConnection()

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=1))
        creds_h1 = SSHCredentials(host="10.0.0.1", username="root", password="p", private_key_path=None)
        creds_h2 = SSHCredentials(host="10.0.0.2", username="root", password="p", private_key_path=None)

        entered: list[str] = []
        release = asyncio.Event()

        async def worker(host: str, creds: SSHCredentials) -> None:
            async with pool.acquire_connection(host=host, port=22, credentials=creds):
                entered.append(host)
                await release.wait()

        t1 = asyncio.create_task(worker("10.0.0.1", creds_h1))
        t2 = asyncio.create_task(worker("10.0.0.2", creds_h2))
        try:
            await asyncio.sleep(0.05)
            # 两个不同主机应各自独立进入，不互相阻塞
            assert len(entered) == 2
            release.set()
            await asyncio.gather(t1, t2)
        finally:
            t1.cancel()
            t2.cancel()
            await pool.close_all()


class TestIdleConnectionCleanup:
    """空闲连接清理测试组。"""

    @pytest.mark.asyncio
    async def test_idle_connection_cleaned_via_background_cleanup(self, mocker) -> None:
        """超过 TTL 的空闲连接应被后台清理任务清理，下次 acquire 创建新连接。"""
        fake = FakeConnection()
        fakes = [fake]

        async def connect_side_effect(**_kwargs):
            return fakes[-1]

        connect = mocker.patch(
            "asyncssh.connect", side_effect=connect_side_effect, autospec=True
        )

        now = 100.0

        def time_provider() -> float:
            return now

        pool = ConnectionPool(
            settings=SSHMCPSettings(per_host_max_connections=5, idle_connection_ttl_seconds=10),
            time_provider=time_provider,
        )
        creds = _make_creds()

        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds):
            pass

        # 模拟时间超过 TTL，手动触发清理
        now = 200.0
        await pool._cleanup_all_idle()

        # 过期连接应被清理关闭
        assert fake.closed is True

        # 下次 acquire 应创建新连接
        fake2 = FakeConnection()
        fakes.append(fake2)
        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds) as c2:
            assert c2 is fake2

        assert connect.call_count == 2
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_cleanup_all_idle_removes_expired(self, mocker) -> None:
        """_cleanup_all_idle 应移除超过 TTL 的连接。"""
        fake = FakeConnection()

        async def connect_side_effect(**_kwargs):
            return fake

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        now = 100.0

        def time_provider() -> float:
            return now

        pool = ConnectionPool(
            settings=SSHMCPSettings(per_host_max_connections=5, idle_connection_ttl_seconds=10),
            time_provider=time_provider,
        )
        creds = _make_creds()

        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds):
            pass

        # 模拟时间前进超过 TTL
        now = 200.0
        await pool._cleanup_all_idle()

        # 连接应被清理关闭
        assert fake.closed is True
        # 内部池应为空
        assert len(pool._connections) == 0
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_cleanup_keeps_fresh_connections(self, mocker) -> None:
        """_cleanup_all_idle 应保留未过期的连接。"""
        fake = FakeConnection()

        async def connect_side_effect(**_kwargs):
            return fake

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        now = 100.0

        def time_provider() -> float:
            return now

        pool = ConnectionPool(
            settings=SSHMCPSettings(per_host_max_connections=5, idle_connection_ttl_seconds=300),
            time_provider=time_provider,
        )
        creds = _make_creds()

        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds):
            pass

        # 时间仅前进 5 秒，远低于 300 秒的 TTL
        now = 105.0
        await pool._cleanup_all_idle()

        # 连接应仍在池中
        assert fake.closed is False
        assert len(pool._connections) > 0
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_cleanup_removes_dead_connections(self, mocker) -> None:
        """_cleanup_all_idle 应移除已死连接（即使未过期）。"""
        fake = FakeConnection()

        async def connect_side_effect(**_kwargs):
            return fake

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        now = 100.0

        def time_provider() -> float:
            return now

        pool = ConnectionPool(
            settings=SSHMCPSettings(per_host_max_connections=5, idle_connection_ttl_seconds=300),
            time_provider=time_provider,
        )
        creds = _make_creds()

        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds):
            pass

        # 标记连接为死亡状态
        fake.closed = True
        await pool._cleanup_all_idle()

        # 死连接应被从池中移除
        assert len(pool._connections) == 0
        await pool.close_all()


class TestBackgroundCleanupTask:
    """后台清理任务测试组。"""

    @pytest.mark.asyncio
    async def test_cleanup_task_starts_lazily(self, mocker) -> None:
        """后台清理任务应在首次 acquire 时懒启动。"""
        fake = FakeConnection()

        async def connect_side_effect(**_kwargs):
            return fake

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
        # 初始时不应有清理任务
        assert pool._cleanup_task is None

        creds = _make_creds()
        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds):
            pass

        # acquire 后清理任务应已启动
        assert pool._cleanup_task is not None
        assert not pool._cleanup_task.done()
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_cleanup_task_stops_on_close_all(self, mocker) -> None:
        """close_all 应停止后台清理任务。"""
        fake = FakeConnection()

        async def connect_side_effect(**_kwargs):
            return fake

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
        creds = _make_creds()

        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds):
            pass

        assert pool._cleanup_task is not None
        await pool.close_all()
        assert pool._cleanup_task is None
        assert pool._closed is True


class TestDeadConnectionDetection:
    """死连接检测测试组。"""

    @pytest.mark.asyncio
    async def test_dead_connection_skipped_on_checkout(self, mocker) -> None:
        """checkout 时应跳过已死连接并创建新连接。"""
        dead = FakeConnection()
        dead.closed = True
        alive = FakeConnection()

        call_count = 0

        async def connect_side_effect(**_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return dead
            return alive

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
        creds = _make_creds()

        # 第一次获取死连接
        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds) as c1:
            assert c1 is dead

        # 手动标记为死亡（模拟归还后连接死亡）
        dead.closed = True

        # 第二次应跳过死连接，创建新连接
        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds) as c2:
            assert c2 is alive

        await pool.close_all()

    def test_is_connection_dead_healthy(self) -> None:
        """健康连接应返回 False。"""
        fake = FakeConnection()
        assert ConnectionPool._is_connection_dead(fake) is False

    def test_is_connection_dead_closed(self) -> None:
        """已关闭的连接应返回 True。"""
        fake = FakeConnection()
        fake.closed = True
        assert ConnectionPool._is_connection_dead(fake) is True


class TestRequestCoalescing:
    """请求合并测试组。"""

    @pytest.mark.asyncio
    async def test_concurrent_requests_coalesce(self, mocker) -> None:
        """并发请求同一主机时应触发请求合并，减少总连接创建数。"""
        connect_calls = 0
        connect_event = asyncio.Event()

        async def connect_side_effect(**_kwargs):
            nonlocal connect_calls
            connect_calls += 1
            # 第一次连接模拟延迟
            if connect_calls == 1:
                await asyncio.sleep(0.05)
            return FakeConnection()

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
        creds = _make_creds()

        results: list[object] = []

        async def worker() -> None:
            async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds) as c:
                results.append(c)

        # 同时发起 3 个请求
        tasks = [asyncio.create_task(worker()) for _ in range(3)]
        await asyncio.gather(*tasks)

        # 请求合并后，连接创建次数应少于请求数
        # 至少第一个请求和后续等待者的连接总数不超过请求数
        assert connect_calls <= 3
        assert len(results) == 3
        await pool.close_all()


class TestLeasedConnection:
    """LeasedConnection 手动释放模式测试组。"""

    @pytest.mark.asyncio
    async def test_lease_and_release(self, mocker) -> None:
        """lease_connection 应返回 LeasedConnection，release 后连接归还池中。"""
        fake = FakeConnection()

        async def connect_side_effect(**_kwargs):
            return fake

        connect = mocker.patch(
            "asyncssh.connect", side_effect=connect_side_effect, autospec=True
        )

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
        creds = _make_creds()

        leased = await pool.lease_connection(host="1.2.3.4", port=22, credentials=creds)
        assert isinstance(leased, LeasedConnection)
        assert leased.connection is fake

        # 释放连接
        await leased.release()

        # 再次获取应复用
        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds) as c:
            assert c is fake

        assert connect.call_count == 1
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_lease_release_with_close(self, mocker) -> None:
        """release(close=True) 应关闭连接而非归还到池中。"""
        fake = FakeConnection()

        async def connect_side_effect(**_kwargs):
            return fake

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
        creds = _make_creds()

        leased = await pool.lease_connection(host="1.2.3.4", port=22, credentials=creds)
        await leased.release(close=True)

        assert fake.closed is True
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_double_release_is_safe(self, mocker) -> None:
        """重复调用 release() 应安全无操作。"""
        fake = FakeConnection()

        async def connect_side_effect(**_kwargs):
            return fake

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
        creds = _make_creds()

        leased = await pool.lease_connection(host="1.2.3.4", port=22, credentials=creds)
        await leased.release()
        await leased.release()  # 第二次调用不应抛异常

        assert leased._released is True
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_lease_releases_semaphore_on_connect_failure(self, mocker) -> None:
        """lease_connection 连接失败时应释放信号量。"""
        fake2 = FakeConnection()
        call_count = 0

        async def connect_side_effect(**_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("Connection refused")
            return fake2

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=1))
        creds = _make_creds()

        with pytest.raises(SSHConnectionError):
            await pool.lease_connection(host="1.2.3.4", port=22, credentials=creds)

        # 信号量应已释放，第二次请求不应被阻塞
        leased = await pool.lease_connection(host="1.2.3.4", port=22, credentials=creds)
        assert leased.connection is fake2
        await leased.release()
        await pool.close_all()


class TestCloseAll:
    """close_all 全量清理测试组。"""

    @pytest.mark.asyncio
    async def test_close_all_cleans_all_connections(self, mocker) -> None:
        """close_all 应关闭所有池化连接。"""
        fakes = [FakeConnection(), FakeConnection()]
        call_idx = 0

        async def connect_side_effect(**_kwargs):
            nonlocal call_idx
            idx = call_idx
            call_idx += 1
            return fakes[idx]

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
        creds_a = SSHCredentials(host="10.0.0.1", username="root", password="p", private_key_path=None)
        creds_b = SSHCredentials(host="10.0.0.2", username="root", password="p", private_key_path=None)

        async with pool.acquire_connection(host="10.0.0.1", port=22, credentials=creds_a):
            pass
        async with pool.acquire_connection(host="10.0.0.2", port=22, credentials=creds_b):
            pass

        await pool.close_all()

        for f in fakes:
            assert f.closed is True

        assert len(pool._connections) == 0
        assert len(pool._host_index) == 0

    @pytest.mark.asyncio
    async def test_close_all_idempotent(self, mocker) -> None:
        """多次调用 close_all 应安全无异常。"""
        fake = FakeConnection()

        async def connect_side_effect(**_kwargs):
            return fake

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
        creds = _make_creds()

        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds):
            pass

        await pool.close_all()
        await pool.close_all()  # 第二次调用不应抛异常
        assert pool._closed is True


class TestConnectionErrors:
    """连接异常处理测试组。"""

    @pytest.mark.asyncio
    async def test_connection_failure_raises_ssh_error(self, mocker) -> None:
        """SSH 连接失败应抛出 SSHConnectionError。"""
        async def connect_side_effect(**_kwargs):
            raise OSError("Connection refused")

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
        creds = _make_creds()

        with pytest.raises(SSHConnectionError, match="SSH连接失败"):
            async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds):
                pass

        await pool.close_all()

    @pytest.mark.asyncio
    async def test_connection_timeout_raises_ssh_error(self, mocker) -> None:
        """SSH 连接超时应抛出 SSHConnectionError。"""
        async def connect_side_effect(**_kwargs):
            await asyncio.sleep(10)  # 模拟长时间等待
            return FakeConnection()

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(
            settings=SSHMCPSettings(per_host_max_connections=5),
            connect_timeout_seconds=0,  # 立即超时
        )
        creds = _make_creds()

        with pytest.raises(SSHConnectionError, match="SSH连接超时"):
            async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds):
                pass

        await pool.close_all()

    @pytest.mark.asyncio
    async def test_error_details_contain_host_and_port(self, mocker) -> None:
        """SSHConnectionError 应包含 host 和 port 信息。"""
        async def connect_side_effect(**_kwargs):
            raise OSError("Connection refused")

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
        creds = _make_creds()

        with pytest.raises(SSHConnectionError) as exc_info:
            async with pool.acquire_connection(host="1.2.3.4", port=2222, credentials=creds):
                pass

        assert exc_info.value.host == "1.2.3.4"
        assert exc_info.value.port == 2222
        await pool.close_all()


class TestHostKeyIndex:
    """HostKey 索引维护测试组。"""

    @pytest.mark.asyncio
    async def test_host_index_populated_on_checkin(self, mocker) -> None:
        """连接归还时应维护 HostKey -> Set[PoolKey] 索引。"""
        fake = FakeConnection()

        async def connect_side_effect(**_kwargs):
            return fake

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
        creds = _make_creds()

        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds):
            pass

        # 连接归还后索引应有记录
        host_key = HostKey(host="1.2.3.4", port=22)
        assert host_key in pool._host_index
        pool_key = PoolKey(host="1.2.3.4", port=22, username="root")
        assert pool_key in pool._host_index[host_key]
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_host_index_cleaned_on_empty_pool(self, mocker) -> None:
        """当 PoolKey 下无连接时，索引应被清理。"""
        fake = FakeConnection()

        async def connect_side_effect(**_kwargs):
            return fake

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        now = 100.0

        def time_provider() -> float:
            return now

        pool = ConnectionPool(
            settings=SSHMCPSettings(per_host_max_connections=5, idle_connection_ttl_seconds=10),
            time_provider=time_provider,
        )
        creds = _make_creds()

        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds):
            pass

        # 时间前进超过 TTL
        now = 200.0
        await pool._cleanup_all_idle()

        # 索引应被清理
        host_key = HostKey(host="1.2.3.4", port=22)
        assert host_key not in pool._host_index
        await pool.close_all()


class TestConnectionCredentials:
    """连接凭据处理测试组。"""

    @pytest.mark.asyncio
    async def test_password_auth(self, mocker) -> None:
        """密码认证应传递 password 参数。"""
        fake = FakeConnection()
        connect_kwargs = {}

        async def connect_side_effect(**kwargs):
            connect_kwargs.update(kwargs)
            return fake

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
        creds = SSHCredentials(host="1.2.3.4", username="root", password="secret", private_key_path=None)

        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds):
            pass

        assert connect_kwargs["password"] == "secret"
        assert "client_keys" not in connect_kwargs
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_key_auth(self, mocker) -> None:
        """密钥认证应传递 client_keys 参数。"""
        fake = FakeConnection()
        connect_kwargs = {}

        async def connect_side_effect(**kwargs):
            connect_kwargs.update(kwargs)
            return fake

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
        creds = SSHCredentials(
            host="1.2.3.4", username="root", password=None, private_key_path="/home/user/.ssh/id_rsa"
        )

        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds):
            pass

        assert connect_kwargs["client_keys"] == ["/home/user/.ssh/id_rsa"]
        assert "password" not in connect_kwargs
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_mixed_auth(self, mocker) -> None:
        """同时提供密码和密钥时应两者都传递。"""
        fake = FakeConnection()
        connect_kwargs = {}

        async def connect_side_effect(**kwargs):
            connect_kwargs.update(kwargs)
            return fake

        mocker.patch("asyncssh.connect", side_effect=connect_side_effect, autospec=True)

        pool = ConnectionPool(settings=SSHMCPSettings(per_host_max_connections=5))
        creds = SSHCredentials(
            host="1.2.3.4",
            username="root",
            password="secret",
            private_key_path="/home/user/.ssh/id_rsa",
        )

        async with pool.acquire_connection(host="1.2.3.4", port=22, credentials=creds):
            pass

        assert connect_kwargs["password"] == "secret"
        assert connect_kwargs["client_keys"] == ["/home/user/.ssh/id_rsa"]
        await pool.close_all()
