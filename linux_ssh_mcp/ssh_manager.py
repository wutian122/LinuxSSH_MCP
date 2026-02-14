from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any, Literal

import asyncssh

from linux_ssh_mcp.auth_manager import SSHCredentials
from linux_ssh_mcp.cache_manager import CacheCategory, CacheManager
from linux_ssh_mcp.connection_pool import ConnectionPool
from linux_ssh_mcp.settings import SSHMCPSettings
from linux_ssh_mcp.token_optimizer import TokenOptimizer

TokenMode = Literal["full", "filter", "truncate"]


def _to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _to_int(value: int | None) -> int:
    return int(value or 0)


@dataclass(frozen=True)
class SSHCommandResult:
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

    def to_dict(self) -> dict[str, Any]:
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


_BLACKLIST_RE = re.compile(
    r"(?ix)(\brm\s+-rf\s+/\b|\bmkfs\b|\bdd\b\s+if=|:\(\)\s*\{\s*:\s*\|\s*:\s*;\s*\}\s*;|\bshutdown\b|\breboot\b)"
)

_DANGEROUS_RE = re.compile(
    r"(?ix)\b("
    r"rm|rmdir|mv|cp|dd|truncate|chmod|chown|chgrp|"
    r"sed|perl|python|tee|"
    r"apt|apt-get|yum|dnf|pacman|systemctl|service|"
    r"useradd|userdel|usermod|groupadd|groupdel|groupmod|"
    r"iptables|ufw|firewall-cmd"
    r")\b"
)


class SSHManager:
    def __init__(
        self,
        *,
        settings: SSHMCPSettings,
        pool: ConnectionPool,
        cache: CacheManager,
        token_optimizer: TokenOptimizer,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._cache = cache
        self._token = token_optimizer

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
        cmd = command.strip()
        if not cmd:
            raise ValueError("command不能为空")

        if _BLACKLIST_RE.search(cmd) is not None:
            raise PermissionError("命令命中黑名单，已拦截")

        warnings: list[str] = []
        if _DANGEROUS_RE.search(cmd) is not None:
            warnings.append("检测到高风险命令，请确认执行意图")

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
                if _BLACKLIST_RE.search(cmd) is not None:
                    raise PermissionError("命令命中黑名单，已拦截")

                warnings: list[str] = []
                if _DANGEROUS_RE.search(cmd) is not None:
                    warnings.append("检测到高风险命令，请确认执行意图")

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
        if not script.strip():
            raise ValueError("script不能为空")

        cmd = f"{shlex.quote(shell)} -s"
        if _BLACKLIST_RE.search(cmd) is not None:
            raise PermissionError("命令命中黑名单，已拦截")

        warnings: list[str] = []
        if _DANGEROUS_RE.search(script) is not None:
            warnings.append("脚本包含潜在高风险命令，请确认执行意图")

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
    ) -> dict[str, Any]:
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

        await self._cache.set(
            cache_key,
            values,
            category="static",
            ttl_seconds=int(self._settings.static_ttl_max_seconds),
            tags=["system", host],
        )
        return values

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
