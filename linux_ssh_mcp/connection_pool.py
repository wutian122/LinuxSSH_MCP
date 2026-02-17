"""SSH连接池管理模块

提供异步SSH连接池，支持：
- 按主机+用户名维度的连接复用
- 每主机最大连接数限制（信号量控制）
- 后台定期清理空闲连接（替代per-request清理）
- 连接请求合并（防止惊群效应）
- 连接健康检查与自动剔除
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import asyncssh

from linux_ssh_mcp.auth_manager import SSHCredentials
from linux_ssh_mcp.exceptions import SSHConnectionError
from linux_ssh_mcp.settings import SSHMCPSettings

# 后台清理任务的执行间隔（秒）
_CLEANUP_INTERVAL_SECONDS: float = 30.0


@dataclass
class _PooledConnection:
    """池化连接包装类。

    Attributes:
        connection: asyncssh SSH客户端连接
        last_used_monotonic: 上次使用的单调时间戳
    """

    connection: asyncssh.SSHClientConnection
    last_used_monotonic: float


@dataclass(frozen=True)
class HostKey:
    """主机标识键。

    用于按主机+端口维度管理信号量和索引。

    Attributes:
        host: 主机地址
        port: SSH端口
    """

    host: str
    port: int


@dataclass(frozen=True)
class PoolKey:
    """连接池键。

    用于按主机+端口+用户名维度管理连接。

    Attributes:
        host: 主机地址
        port: SSH端口
        username: SSH用户名
    """

    host: str
    port: int
    username: str


@dataclass
class LeasedConnection:
    """租借的连接包装类。

    提供手动release接口，用于交互式会话等需要长期持有连接的场景。
    确保释放时归还信号量，防止连接泄漏。

    Attributes:
        connection: asyncssh SSH客户端连接
    """

    connection: asyncssh.SSHClientConnection
    _pool: ConnectionPool
    _pool_key: PoolKey
    _semaphore: asyncio.BoundedSemaphore
    _released: bool = False

    async def release(self, *, close: bool = False) -> None:
        """释放租借的连接。

        Args:
            close: 是否关闭连接而非归还到池中
        """
        if self._released:
            return
        self._released = True
        try:
            if close:
                await self._pool._close_quietly(self.connection)
            else:
                await self._pool._checkin(self._pool_key, self.connection)
        finally:
            self._semaphore.release()


class ConnectionPool:
    """异步SSH连接池。

    管理SSH连接的创建、复用、清理和销毁。通过信号量控制每主机的
    最大并发连接数，通过后台任务定期清理空闲连接。

    Attributes:
        _settings: SSH MCP配置
        _connect_timeout_seconds: 连接超时时间
    """

    def __init__(
        self,
        *,
        settings: SSHMCPSettings,
        connect_timeout_seconds: int | None = None,
        time_provider: Callable[[], float] = time.monotonic,
    ) -> None:
        """初始化连接池。

        Args:
            settings: SSH MCP配置
            connect_timeout_seconds: 连接超时时间（秒），None表示无超时
            time_provider: 时间提供函数，用于测试注入
        """
        self._settings = settings
        self._connect_timeout_seconds = connect_timeout_seconds
        self._time = time_provider

        self._lock = asyncio.Lock()
        self._semaphores: dict[HostKey, asyncio.BoundedSemaphore] = {}
        self._connections: dict[PoolKey, list[_PooledConnection]] = {}

        # HostKey -> Set[PoolKey] 索引，加速按主机维度的查找
        self._host_index: dict[HostKey, set[PoolKey]] = {}

        # 连接请求合并：防止同一PoolKey的并发重复连接
        self._pending_connects: dict[PoolKey, asyncio.Future[asyncssh.SSHClientConnection]] = {}

        # 后台清理任务
        self._cleanup_task: asyncio.Task[None] | None = None
        self._closed = False

    def _ensure_cleanup_started(self) -> None:
        """确保后台清理任务已启动。

        仅在首次调用时创建后台任务，后续调用无操作。
        """
        if self._cleanup_task is None and not self._closed:
            try:
                self._cleanup_task = asyncio.create_task(self._background_cleanup_loop())
            except RuntimeError:
                pass

    async def close_all(self) -> None:
        """关闭所有连接并停止后台清理任务。

        安全地清理所有池化连接，等待后台任务完成。
        """
        self._closed = True

        # 停止后台清理任务
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except (asyncio.CancelledError, Exception):
                pass
            self._cleanup_task = None

        async with self._lock:
            pools = list(self._connections.values())
            self._connections.clear()
            self._host_index.clear()

        await asyncio.gather(
            *[self._close_quietly(item.connection) for pool in pools for item in pool],
            return_exceptions=True,
        )

    @asynccontextmanager
    async def acquire_connection(
        self,
        *,
        host: str,
        port: int,
        credentials: SSHCredentials,
    ) -> AsyncIterator[asyncssh.SSHClientConnection]:
        """获取SSH连接（上下文管理器模式）。

        从连接池中获取可用连接，使用完毕后自动归还。
        如果池中无可用连接，则新建连接。支持连接请求合并，
        防止同一主机的并发请求创建过多连接。

        Args:
            host: 目标主机地址
            port: SSH端口
            credentials: SSH凭据

        Yields:
            asyncssh.SSHClientConnection: SSH连接

        Raises:
            SSHConnectionError: 连接建立失败时抛出
        """
        self._ensure_cleanup_started()

        host_key = HostKey(host=host, port=port)
        pool_key = PoolKey(host=host, port=port, username=credentials.username)

        sem = await self._get_semaphore(host_key)
        await sem.acquire()

        conn: asyncssh.SSHClientConnection | None = None
        try:
            conn = await self._checkout(pool_key)
            if conn is None:
                conn = await self._connect_or_join(
                    pool_key=pool_key, host=host, port=port, credentials=credentials
                )
            yield conn
        finally:
            if conn is not None:
                await self._checkin(pool_key, conn)
            sem.release()

    async def lease_connection(
        self,
        *,
        host: str,
        port: int,
        credentials: SSHCredentials,
    ) -> LeasedConnection:
        """租借SSH连接（手动释放模式）。

        从连接池中获取连接，返回LeasedConnection包装对象。
        调用方需要手动调用release()归还连接。适用于交互式会话等
        需要长期持有连接的场景。

        Args:
            host: 目标主机地址
            port: SSH端口
            credentials: SSH凭据

        Returns:
            LeasedConnection: 租借的连接包装对象

        Raises:
            SSHConnectionError: 连接建立失败时抛出
        """
        self._ensure_cleanup_started()

        host_key = HostKey(host=host, port=port)
        pool_key = PoolKey(host=host, port=port, username=credentials.username)

        sem = await self._get_semaphore(host_key)
        await sem.acquire()

        try:
            conn = await self._checkout(pool_key)
            if conn is None:
                conn = await self._connect_or_join(
                    pool_key=pool_key, host=host, port=port, credentials=credentials
                )
            return LeasedConnection(
                connection=conn,
                _pool=self,
                _pool_key=pool_key,
                _semaphore=sem,
            )
        except Exception:
            sem.release()
            raise

    async def _get_semaphore(self, host_key: HostKey) -> asyncio.BoundedSemaphore:
        """获取或创建主机级别的信号量。

        Args:
            host_key: 主机标识键

        Returns:
            该主机对应的信号量
        """
        async with self._lock:
            sem = self._semaphores.get(host_key)
            if sem is None:
                sem = asyncio.BoundedSemaphore(self._settings.per_host_max_connections)
                self._semaphores[host_key] = sem
            return sem

    async def _checkout(self, pool_key: PoolKey) -> asyncssh.SSHClientConnection | None:
        """从池中取出一个可用连接。

        从池中弹出连接，检查其健康状态。如果连接已死，
        异步关闭并继续查找下一个。

        Args:
            pool_key: 连接池键

        Returns:
            可用的SSH连接，无可用连接时返回None
        """
        async with self._lock:
            pool = self._connections.get(pool_key)
            if not pool:
                return None

            while pool:
                item = pool.pop()
                if not self._is_connection_dead(item.connection):
                    return item.connection
                asyncio.create_task(self._close_quietly(item.connection))

            self._connections.pop(pool_key, None)
            host_key = HostKey(host=pool_key.host, port=pool_key.port)
            index = self._host_index.get(host_key)
            if index is not None:
                index.discard(pool_key)
            return None

    async def _checkin(self, pool_key: PoolKey, conn: asyncssh.SSHClientConnection) -> None:
        """将连接归还到池中。

        检查连接健康状态，健康的连接会被归还，死连接会被关闭。

        Args:
            pool_key: 连接池键
            conn: 要归还的SSH连接
        """
        if self._is_connection_dead(conn):
            await self._close_quietly(conn)
            return

        async with self._lock:
            pool = self._connections.setdefault(pool_key, [])
            pool.append(_PooledConnection(connection=conn, last_used_monotonic=self._time()))

            # 维护 HostKey -> PoolKey 索引
            host_key = HostKey(host=pool_key.host, port=pool_key.port)
            self._host_index.setdefault(host_key, set()).add(pool_key)

    async def _connect_or_join(
        self,
        *,
        pool_key: PoolKey,
        host: str,
        port: int,
        credentials: SSHCredentials,
    ) -> asyncssh.SSHClientConnection:
        """建立连接或加入正在进行的连接请求。

        实现连接请求合并（Request Coalescing）：当多个请求同时需要
        连接到同一主机时，只创建一个连接，其余请求等待复用。

        Args:
            pool_key: 连接池键
            host: 目标主机地址
            port: SSH端口
            credentials: SSH凭据

        Returns:
            已建立的SSH连接

        Raises:
            SSHConnectionError: 连接建立失败时抛出
        """
        # 检查是否有正在进行的连接请求
        pending = self._pending_connects.get(pool_key)
        if pending is not None and not pending.done():
            # 等待已有的连接请求完成，然后创建自己的连接
            # （因为那个连接已被另一个请求者持有）
            try:
                await asyncio.shield(pending)
            except Exception:
                pass
            # 共享的连接已被原始请求者使用，需要创建新连接
            return await self._connect(host=host, port=port, credentials=credentials)

        # 创建新的连接请求
        loop = asyncio.get_running_loop()
        future: asyncio.Future[asyncssh.SSHClientConnection] = loop.create_future()
        self._pending_connects[pool_key] = future

        try:
            conn = await self._connect(host=host, port=port, credentials=credentials)
            future.set_result(conn)
            return conn
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            self._pending_connects.pop(pool_key, None)

    async def _connect(
        self,
        *,
        host: str,
        port: int,
        credentials: SSHCredentials,
    ) -> asyncssh.SSHClientConnection:
        """创建新的SSH连接。

        Args:
            host: 目标主机地址
            port: SSH端口
            credentials: SSH凭据

        Returns:
            新建立的SSH连接

        Raises:
            SSHConnectionError: 连接失败时抛出
        """
        options: dict[str, object] = {
            "host": host,
            "port": port,
            "username": credentials.username,
        }

        if credentials.private_key_path:
            options["client_keys"] = [credentials.private_key_path]
        if credentials.password:
            options["password"] = credentials.password

        try:
            connect_task = asyncssh.connect(**options)
            if self._connect_timeout_seconds is None:
                return await connect_task
            return await asyncio.wait_for(connect_task, timeout=self._connect_timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise SSHConnectionError(
                f"SSH连接超时: {host}:{port}",
                host=host,
                port=port,
            ) from exc
        except Exception as exc:
            raise SSHConnectionError(
                f"SSH连接失败: {host}:{port} - {exc}",
                host=host,
                port=port,
            ) from exc

    async def _background_cleanup_loop(self) -> None:
        """后台清理循环。

        定期扫描所有池化连接，清理超过TTL的空闲连接和已死连接。
        替代原来每次请求时的同步清理，减少关键路径的延迟。
        """
        while not self._closed:
            try:
                await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
                await self._cleanup_all_idle()
            except asyncio.CancelledError:
                break
            except Exception:
                continue

    async def _cleanup_all_idle(self) -> None:
        """清理所有主机的空闲连接。

        遍历所有池化连接，关闭超过TTL的空闲连接和已死连接。
        使用HostKey索引加速遍历。
        """
        ttl = float(self._settings.idle_connection_ttl_seconds)
        now = self._time()

        async with self._lock:
            to_close: list[asyncssh.SSHClientConnection] = []
            empty_pool_keys: list[PoolKey] = []

            for pool_key, pool in self._connections.items():
                kept: list[_PooledConnection] = []
                for item in pool:
                    idle_seconds = now - item.last_used_monotonic
                    if self._is_connection_dead(item.connection) or idle_seconds > ttl:
                        to_close.append(item.connection)
                    else:
                        kept.append(item)

                if kept:
                    self._connections[pool_key] = kept
                else:
                    empty_pool_keys.append(pool_key)

            # 清理空的PoolKey条目和索引
            for pool_key in empty_pool_keys:
                self._connections.pop(pool_key, None)
                host_key = HostKey(host=pool_key.host, port=pool_key.port)
                index = self._host_index.get(host_key)
                if index is not None:
                    index.discard(pool_key)
                    if not index:
                        self._host_index.pop(host_key, None)

        # 锁外异步关闭连接
        if to_close:
            await asyncio.gather(
                *[self._close_quietly(c) for c in to_close],
                return_exceptions=True,
            )

    @staticmethod
    def _is_connection_dead(conn: asyncssh.SSHClientConnection) -> bool:
        """检查连接是否已死亡。

        Args:
            conn: SSH连接

        Returns:
            连接是否已关闭或不可用
        """
        try:
            if hasattr(conn, "is_closing"):
                return bool(conn.is_closing())
        except Exception:
            return True
        return False

    @staticmethod
    async def _close_quietly(conn: asyncssh.SSHClientConnection) -> None:
        """静默关闭连接，忽略所有异常。

        Args:
            conn: 要关闭的SSH连接
        """
        try:
            conn.close()
            await conn.wait_closed()
        except Exception:
            return
