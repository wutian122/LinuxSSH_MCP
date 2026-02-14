from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import asyncssh

from linux_ssh_mcp.auth_manager import SSHCredentials
from linux_ssh_mcp.settings import SSHMCPSettings


@dataclass
class _PooledConnection:
    connection: asyncssh.SSHClientConnection
    last_used_monotonic: float


@dataclass(frozen=True)
class HostKey:
    host: str
    port: int


@dataclass(frozen=True)
class PoolKey:
    host: str
    port: int
    username: str


@dataclass
class LeasedConnection:
    connection: asyncssh.SSHClientConnection
    _pool: ConnectionPool
    _pool_key: PoolKey
    _semaphore: asyncio.BoundedSemaphore
    _released: bool = False

    async def release(self, *, close: bool = False) -> None:
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
    def __init__(
        self,
        *,
        settings: SSHMCPSettings,
        connect_timeout_seconds: int | None = None,
        time_provider: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._connect_timeout_seconds = connect_timeout_seconds
        self._time = time_provider

        self._lock = asyncio.Lock()
        self._semaphores: dict[HostKey, asyncio.BoundedSemaphore] = {}
        self._connections: dict[PoolKey, list[_PooledConnection]] = {}

    async def close_all(self) -> None:
        async with self._lock:
            pools = list(self._connections.values())
            self._connections.clear()

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
        host_key = HostKey(host=host, port=port)
        pool_key = PoolKey(host=host, port=port, username=credentials.username)

        sem = await self._get_semaphore(host_key)
        await sem.acquire()

        conn: asyncssh.SSHClientConnection | None = None
        try:
            await self._cleanup_idle_for_host(host_key)
            conn = await self._checkout(pool_key)
            if conn is None:
                conn = await self._connect(host=host, port=port, credentials=credentials)
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
        host_key = HostKey(host=host, port=port)
        pool_key = PoolKey(host=host, port=port, username=credentials.username)

        sem = await self._get_semaphore(host_key)
        await sem.acquire()

        try:
            await self._cleanup_idle_for_host(host_key)
            conn = await self._checkout(pool_key)
            if conn is None:
                conn = await self._connect(host=host, port=port, credentials=credentials)
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
        async with self._lock:
            sem = self._semaphores.get(host_key)
            if sem is None:
                sem = asyncio.BoundedSemaphore(self._settings.per_host_max_connections)
                self._semaphores[host_key] = sem
            return sem

    async def _checkout(self, pool_key: PoolKey) -> asyncssh.SSHClientConnection | None:
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
            return None

    async def _checkin(self, pool_key: PoolKey, conn: asyncssh.SSHClientConnection) -> None:
        if self._is_connection_dead(conn):
            await self._close_quietly(conn)
            return

        async with self._lock:
            pool = self._connections.setdefault(pool_key, [])
            pool.append(_PooledConnection(connection=conn, last_used_monotonic=self._time()))

    async def _cleanup_idle_for_host(self, host_key: HostKey) -> None:
        ttl = float(self._settings.idle_connection_ttl_seconds)
        now = self._time()

        async with self._lock:
            to_close: list[asyncssh.SSHClientConnection] = []

            keys_for_host: list[PoolKey] = []
            for key in self._connections.keys():
                if key.host == host_key.host and key.port == host_key.port:
                    keys_for_host.append(key)

            for key in keys_for_host:
                pool = self._connections.get(key, [])
                kept: list[_PooledConnection] = []
                for item in pool:
                    idle_seconds = now - item.last_used_monotonic
                    if self._is_connection_dead(item.connection) or idle_seconds > ttl:
                        to_close.append(item.connection)
                    else:
                        kept.append(item)
                if kept:
                    self._connections[key] = kept
                else:
                    self._connections.pop(key, None)

        await asyncio.gather(*[self._close_quietly(c) for c in to_close], return_exceptions=True)

    async def _connect(
        self,
        *,
        host: str,
        port: int,
        credentials: SSHCredentials,
    ) -> asyncssh.SSHClientConnection:
        options: dict[str, object] = {
            "host": host,
            "port": port,
            "username": credentials.username,
        }

        if credentials.private_key_path:
            options["client_keys"] = [credentials.private_key_path]
        if credentials.password:
            options["password"] = credentials.password

        connect_task = asyncssh.connect(**options)
        if self._connect_timeout_seconds is None:
            return await connect_task
        return await asyncio.wait_for(connect_task, timeout=self._connect_timeout_seconds)

    @staticmethod
    def _is_connection_dead(conn: asyncssh.SSHClientConnection) -> bool:
        try:
            if hasattr(conn, "is_closing"):
                return bool(conn.is_closing())
        except Exception:
            return True
        return False

    @staticmethod
    async def _close_quietly(conn: asyncssh.SSHClientConnection) -> None:
        try:
            conn.close()
            await conn.wait_closed()
        except Exception:
            return
