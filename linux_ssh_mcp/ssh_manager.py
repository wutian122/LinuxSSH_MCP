from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, Literal, cast

import asyncssh

from linux_ssh_mcp.auth_manager import SSHCredentials
from linux_ssh_mcp.cache_manager import CacheCategory, CacheManager
from linux_ssh_mcp.connection_pool import ConnectionPool
from linux_ssh_mcp.security import CommandSecurityValidator
from linux_ssh_mcp.settings import SSHMCPSettings
from linux_ssh_mcp.token_optimizer import TokenOptimizer
from linux_ssh_mcp.types import (
    CacheClearResultDict,
    SSHCommandResultDict,
    SessionInfoResultDict,
    SystemInfoResultDict,
)

TokenMode = Literal["full", "filter", "truncate"]


def _to_text(value: str | bytes | None) -> str:
    """将可空的字符串或字节转换为字符串。

    Args:
        value: 字符串、字节或None值

    Returns:
        转换后的字符串，None返回空字符串
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _to_int(value: int | None) -> int:
    """将可空整数转换为整数，空值返回0。

    Args:
        value: 整数或None值

    Returns:
        转换后的整数
    """
    return int(value or 0)


@dataclass(frozen=True)
class SSHCommandResult:
    """SSH命令执行结果数据类。

    Attributes:
        host: 目标主机地址
        port: SSH端口
        command: 执行的命令
        exit_status: 退出状态码
        stdout: 标准输出
        stderr: 标准错误输出
        cached: 是否来自缓存
        warnings: 安全警告列表
        token_mode: Token优化模式
        token_estimate: 估算的Token数量
    """

    host: str
    port: int
    command: str
    exit_status: int
    stdout: str
    stderr: str
    cached: bool
    warnings: list[str]
    token_mode: TokenMode
    token_estimate: int

    def to_dict(self) -> SSHCommandResultDict:
        """转换为字典格式。

        Returns:
            包含所有字段的SSHCommandResultDict
        """
        return {
            "host": self.host,
            "port": self.port,
            "command": self.command,
            "exit_status": self.exit_status,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "cached": self.cached,
            "warnings": list(self.warnings),
            "token_mode": self.token_mode,
            "token_estimate": self.token_estimate,
        }


class SSHManager:
    """SSH命令执行管理器。

    封装了SSH命令执行、缓存、Token优化和安全校验等核心逻辑。
    通过CommandSecurityValidator进行命令安全检查，通过ConnectionPool
    管理SSH连接，通过CacheManager管理结果缓存。

    Attributes:
        _settings: SSH MCP配置
        _pool: SSH连接池
        _cache: 缓存管理器
        _token: Token优化器
        _security: 命令安全校验器
    """

    def __init__(
        self,
        *,
        settings: SSHMCPSettings,
        pool: ConnectionPool,
        cache: CacheManager,
        token_optimizer: TokenOptimizer,
        security_validator: CommandSecurityValidator | None = None,
    ) -> None:
        """初始化SSH命令执行管理器。

        Args:
            settings: SSH MCP配置
            pool: SSH连接池
            cache: 缓存管理器
            token_optimizer: Token优化器
            security_validator: 命令安全校验器，为None时使用默认校验器
        """
        self._settings = settings
        self._pool = pool
        self._cache = cache
        self._token = token_optimizer
        self._security = security_validator or CommandSecurityValidator()

    async def execute_command(
        self,
        *,
        host: str,
        port: int,
        credentials: SSHCredentials,
        command: str,
        token_mode: TokenMode = "full",
        filter_pattern: str | None = None,
        max_tokens: int | None = None,
        use_cache: bool = True,
        cache_category: CacheCategory = "dynamic",
        cache_ttl_seconds: int | None = None,
    ) -> SSHCommandResult:
        """执行单条SSH命令。

        在远程主机上执行指定命令，支持缓存、Token优化等特性。
        自动通过安全校验器拦截危险命令。

        Args:
            host: 目标主机地址
            port: SSH端口
            credentials: SSH凭据
            command: 要执行的命令
            token_mode: 输出模式 - full/filter/truncate
            filter_pattern: 正则过滤模式
            max_tokens: 最大Token数
            use_cache: 是否启用缓存
            cache_category: 缓存类别
            cache_ttl_seconds: 自定义缓存TTL

        Returns:
            SSHCommandResult: 命令执行结果

        Raises:
            ValueError: command为空时抛出
            CommandBlockedError: 命令命中黑名单时抛出
        """
        cmd = command.strip()
        if not cmd:
            raise ValueError("command不能为空")

        check_result = self._security.validate_command(cmd)
        warnings = list(check_result.warnings)

        cache_key = (
            "cmd",
            host,
            port,
            credentials.username,
            cmd,
            token_mode,
            filter_pattern,
            max_tokens,
        )
        if use_cache and self._cache.should_cache_for_command(cmd):
            cached = await self._cache.get(cache_key)
            if isinstance(cached, dict):
                return SSHCommandResult(**cached)

        async with self._pool.acquire_connection(
            host=host,
            port=port,
            credentials=credentials,
        ) as conn:
            completed: asyncssh.SSHCompletedProcess = await conn.run(
                cmd,
                check=False,
                timeout=float(self._settings.command_timeout_seconds),
            )

        stdout = _to_text(completed.stdout)
        stderr = _to_text(completed.stderr)
        processed_stdout = self._apply_token_mode(
            stdout,
            token_mode=token_mode,
            filter_pattern=filter_pattern,
            max_tokens=max_tokens,
        )
        token_estimate = self._token.estimate_tokens(processed_stdout)

        result = SSHCommandResult(
            host=host,
            port=port,
            command=cmd,
            exit_status=_to_int(completed.exit_status),
            stdout=processed_stdout,
            stderr=stderr,
            cached=False,
            warnings=warnings,
            token_mode=token_mode,
            token_estimate=token_estimate,
        )

        if use_cache and self._cache.should_cache_for_command(cmd):
            to_cache = {**result.to_dict(), "cached": True}
            await self._cache.set(
                cache_key,
                to_cache,
                category=cache_category,
                ttl_seconds=cache_ttl_seconds,
                tags=["command", host],
            )

        return result

    async def execute_batch(
        self,
        *,
        host: str,
        port: int,
        credentials: SSHCredentials,
        commands: list[str],
        token_mode: TokenMode = "full",
        filter_pattern: str | None = None,
        max_tokens: int | None = None,
    ) -> list[SSHCommandResult]:
        """批量执行多条SSH命令。

        在同一SSH连接内顺序执行多条命令，通过安全校验器检查每条命令。

        Args:
            host: 目标主机地址
            port: SSH端口
            credentials: SSH凭据
            commands: 命令列表
            token_mode: 输出模式
            filter_pattern: 正则过滤模式
            max_tokens: 最大Token数

        Returns:
            list[SSHCommandResult]: 各命令执行结果列表

        Raises:
            CommandBlockedError: 任一命令命中黑名单时抛出
        """
        if not commands:
            return []

        results: list[SSHCommandResult] = []
        async with self._pool.acquire_connection(
            host=host,
            port=port,
            credentials=credentials,
        ) as conn:
            for command in commands:
                cmd = command.strip()
                if not cmd:
                    continue

                check_result = self._security.validate_command(cmd)
                warnings = list(check_result.warnings)

                completed: asyncssh.SSHCompletedProcess = await conn.run(
                    cmd,
                    check=False,
                    timeout=float(self._settings.command_timeout_seconds),
                )
                stdout = _to_text(completed.stdout)
                stderr = _to_text(completed.stderr)
                processed_stdout = self._apply_token_mode(
                    stdout,
                    token_mode=token_mode,
                    filter_pattern=filter_pattern,
                    max_tokens=max_tokens,
                )
                token_estimate = self._token.estimate_tokens(processed_stdout)
                results.append(
                    SSHCommandResult(
                        host=host,
                        port=port,
                        command=cmd,
                        exit_status=_to_int(completed.exit_status),
                        stdout=processed_stdout,
                        stderr=stderr,
                        cached=False,
                        warnings=warnings,
                        token_mode=token_mode,
                        token_estimate=token_estimate,
                    )
                )

        return results

    async def execute_script(
        self,
        *,
        host: str,
        port: int,
        credentials: SSHCredentials,
        script: str,
        shell: str = "/bin/bash",
        token_mode: TokenMode = "full",
        filter_pattern: str | None = None,
        max_tokens: int | None = None,
    ) -> SSHCommandResult:
        """执行Shell脚本。

        通过stdin传入脚本内容执行，使用安全校验器检查脚本内容。

        Args:
            host: 目标主机地址
            port: SSH端口
            credentials: SSH凭据
            script: 脚本内容
            shell: Shell解释器路径
            token_mode: 输出模式
            filter_pattern: 正则过滤模式
            max_tokens: 最大Token数

        Returns:
            SSHCommandResult: 脚本执行结果

        Raises:
            ValueError: script为空时抛出
            CommandBlockedError: 命令命中黑名单时抛出
        """
        if not script.strip():
            raise ValueError("script不能为空")

        cmd = f"{shlex.quote(shell)} -s"
        self._security.validate_command(cmd)

        script_check = self._security.validate_script(script)
        warnings = list(script_check.warnings)

        async with self._pool.acquire_connection(
            host=host,
            port=port,
            credentials=credentials,
        ) as conn:
            completed: asyncssh.SSHCompletedProcess = await conn.run(
                cmd,
                input=script,
                check=False,
                timeout=float(self._settings.command_timeout_seconds),
            )

        stdout = _to_text(completed.stdout)
        stderr = _to_text(completed.stderr)
        processed_stdout = self._apply_token_mode(
            stdout,
            token_mode=token_mode,
            filter_pattern=filter_pattern,
            max_tokens=max_tokens,
        )
        token_estimate = self._token.estimate_tokens(processed_stdout)

        return SSHCommandResult(
            host=host,
            port=port,
            command=cmd,
            exit_status=_to_int(completed.exit_status),
            stdout=processed_stdout,
            stderr=stderr,
            cached=False,
            warnings=warnings,
            token_mode=token_mode,
            token_estimate=token_estimate,
        )

    async def get_system_info(
        self,
        *,
        host: str,
        port: int,
        credentials: SSHCredentials,
        force_refresh: bool = False,
    ) -> SystemInfoResultDict:
        cache_key = ("system_info", host, port, credentials.username)
        if not force_refresh:
            cached = await self._cache.get(cache_key)
            if isinstance(cached, dict):
                return cached

        cmds = {
            "hostname": "hostname",
            "kernel": "uname -a",
            "uptime": "uptime",
            "whoami": "whoami",
            "os_release": "cat /etc/os-release || true",
        }

        async with self._pool.acquire_connection(
            host=host,
            port=port,
            credentials=credentials,
        ) as conn:
            values: dict[str, Any] = {}
            for k, c in cmds.items():
                completed: asyncssh.SSHCompletedProcess = await conn.run(
                    c,
                    check=False,
                    timeout=float(self._settings.command_timeout_seconds),
                )
                values[k] = {
                    "exit_status": _to_int(completed.exit_status),
                    "stdout": _to_text(completed.stdout).strip(),
                    "stderr": _to_text(completed.stderr).strip(),
                }

        # Cast to SystemInfoResultDict since we know the keys match
        result = cast(SystemInfoResultDict, values)

        await self._cache.set(
            cache_key,
            result,
            category="static",
            ttl_seconds=int(self._settings.static_ttl_max_seconds),
            tags=["system", host],
        )
        return result

    async def get_session_info(self) -> dict[str, Any]:
        cache_info = await self._cache.get_info(head=50)
        return {
            "cache": {
                "maxsize": cache_info.maxsize,
                "size": cache_info.size,
                "keys": cache_info.keys,
            },
        }

    async def search_content(
        self,
        *,
        host: str,
        port: int,
        credentials: SSHCredentials,
        query: str,
        path: str,
        token_mode: TokenMode = "truncate",
        max_tokens: int | None = 800,
    ) -> SSHCommandResult:
        if not query.strip():
            raise ValueError("query不能为空")
        if not path.strip():
            raise ValueError("path不能为空")

        q = shlex.quote(query)
        p = shlex.quote(path)
        cmd = f"grep -R -n --binary-files=without-match -- {q} {p} || true"
        return await self.execute_command(
            host=host,
            port=port,
            credentials=credentials,
            command=cmd,
            token_mode=token_mode,
            max_tokens=max_tokens,
            use_cache=True,
            cache_category="dynamic",
        )

    async def clear_cache(
        self,
        *,
        keys: list[str] | None = None,
        tag: str | None = None,
        category: CacheCategory | None = None,
    ) -> dict[str, Any]:
        removed = await self._cache.clear(keys=keys, tag=tag, category=category)
        info = await self._cache.get_info(head=20)
        return {
            "removed": removed,
            "cache": {
                "maxsize": info.maxsize,
                "size": info.size,
                "keys": info.keys,
            },
        }

    def _apply_token_mode(
        self,
        text: str,
        *,
        token_mode: TokenMode,
        filter_pattern: str | None,
        max_tokens: int | None,
    ) -> str:
        if token_mode == "full":
            return text
        if token_mode == "filter":
            if not filter_pattern:
                raise ValueError("filter_pattern不能为空")
            return self._token.filter_by_pattern(text, pattern=filter_pattern)
        if token_mode == "truncate":
            if max_tokens is None:
                raise ValueError("max_tokens不能为空")
            return self._token.truncate_by_tokens(text, max_tokens=max_tokens)
        raise ValueError("不支持的token_mode")
