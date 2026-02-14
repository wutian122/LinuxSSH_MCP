"""Linux SSH MCP Server 模块

本模块提供基于 MCP (Model Context Protocol) 的 Linux SSH 远程运维工具集。
支持以下功能：
- SSH命令执行（单条、批量、脚本）
- 文件传输（上传、下载、MD5/SHA256校验）
- 目录管理（分页列表、正则过滤）
- 交互式会话（会话复用、超时清理）
- 凭据管理（keyring存储）
- 缓存优化（TTL+LRU）
- Token优化（全量/过滤/截断）

使用方式：
    通过 stdio 启动 MCP 服务器，供 Claude Desktop 等客户端调用。
"""
from __future__ import annotations

import sys
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import TextIOWrapper
from pathlib import Path
from tempfile import gettempdir
from typing import Any, Literal, cast

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.stdio import stdio_server

from linux_ssh_mcp.auth_manager import AuthManager, SSHCredentials
from linux_ssh_mcp.cache_manager import CacheManager
from linux_ssh_mcp.connection_pool import ConnectionPool
from linux_ssh_mcp.constants import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MAX_TOKENS,
    DEFAULT_PAGE,
    DEFAULT_PAGE_SIZE,
    DEFAULT_SHELL,
    DEFAULT_SSH_PORT,
)
from linux_ssh_mcp.directory_manager import DirectoryManager
from linux_ssh_mcp.file_transfer_manager import FileTransferManager
from linux_ssh_mcp.settings import SSHMCPSettings
from linux_ssh_mcp.ssh_manager import SSHManager
from linux_ssh_mcp.token_optimizer import TokenOptimizer

TokenMode = Literal["full", "filter", "truncate"]
CacheCategory = Literal["static", "dynamic"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def run_stdio_server(server: FastMCP) -> None:
    async def _run() -> None:
        stdin = anyio.wrap_file(
            TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        )
        stdout = anyio.wrap_file(
            TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        )
        async with stdio_server(stdin=stdin, stdout=stdout) as (read_stream, write_stream):
            lowlevel = cast(Any, server)._mcp_server
            await lowlevel.run(
                read_stream,
                write_stream,
                lowlevel.create_initialization_options(),
            )

    try:
        anyio.run(_run)
    except BaseException:
        error_path = Path(gettempdir()) / "linux-ssh-mcp-startup-error.log"
        with error_path.open("a", encoding="utf-8") as f:
            f.write("\n" + "=" * 80 + "\n")
            f.write(traceback.format_exc())
        raise


def create_mcp_server(*, settings: SSHMCPSettings) -> FastMCP:
    auth = AuthManager()
    pool = ConnectionPool(settings=settings)
    cache = CacheManager(settings=settings)
    token = TokenOptimizer()
    ssh = SSHManager(settings=settings, pool=pool, cache=cache, token_optimizer=token)
    transfer = FileTransferManager(settings=settings, pool=pool)
    directory = DirectoryManager(settings=settings, pool=pool)

    @asynccontextmanager
    async def lifespan(_app: FastMCP) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await directory.close_all_sessions()
            await pool.close_all()

    mcp = FastMCP(
        name="linux-ssh-mcp",
        instructions="Linux SSH远程命令执行与文件/目录操作工具",
        log_level=cast(LogLevel, settings.log_level),
        lifespan=lifespan,
    )

    def resolve_credentials(
        *,
        host: str,
        username: str,
        password: str | None,
        private_key_path: str | None,
    ) -> SSHCredentials:
        if password or private_key_path:
            return SSHCredentials(
                host=host,
                username=username,
                password=password,
                private_key_path=private_key_path,
            )
        creds = auth.get_credentials(host=host, username=username)
        if not creds.password and not creds.private_key_path:
            raise ValueError("未提供凭据，且keyring中未找到")
        return creds

    @mcp.tool()
    def auth_store_credentials(
        *,
        host: str,
        username: str,
        password: str | None = None,
        private_key_path: str | None = None,
    ) -> dict[str, Any]:
        """存储SSH凭据到系统keyring。

        将SSH连接所需的密码或私钥路径安全存储到系统凭据管理器中，
        后续调用其他工具时可自动获取凭据，无需每次传入。

        Args:
            host: 目标主机地址（IP或域名）
            username: SSH用户名
            password: SSH密码（可选，与private_key_path二选一）
            private_key_path: SSH私钥文件路径（可选）

        Returns:
            dict: 包含ok状态、host和username的结果字典
        """
        auth.store_credentials(
            host=host,
            username=username,
            password=password,
            private_key_path=private_key_path,
        )
        return {"ok": True, "host": host, "username": username}

    @mcp.tool()
    async def ssh_execute(
        *,
        host: str,
        username: str,
        command: str,
        port: int = DEFAULT_SSH_PORT,
        password: str | None = None,
        private_key_path: str | None = None,
        token_mode: TokenMode = "full",
        filter_pattern: str | None = None,
        max_tokens: int | None = None,
        use_cache: bool = True,
        cache_category: CacheCategory = "dynamic",
        cache_ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        """执行单条SSH命令。

        在远程主机上执行指定命令，支持缓存、Token优化等特性。
        自动拦截危险命令（如rm -rf /）以防止误操作。

        Args:
            host: 目标主机地址
            username: SSH用户名
            command: 要执行的命令
            port: SSH端口，默认22
            password: SSH密码（可选）
            private_key_path: SSH私钥路径（可选）
            token_mode: 输出模式 - full(全量)/filter(正则过滤)/truncate(截断)
            filter_pattern: 正则过滤模式（token_mode=filter时必填）
            max_tokens: 最大Token数（token_mode=truncate时必填）
            use_cache: 是否启用缓存
            cache_category: 缓存类别 - static(长期)/dynamic(短期)
            cache_ttl_seconds: 自定义缓存TTL（秒）

        Returns:
            dict: 包含stdout、stderr、exit_status等执行结果
        """
        creds = resolve_credentials(
            host=host,
            username=username,
            password=password,
            private_key_path=private_key_path,
        )
        res = await ssh.execute_command(
            host=host,
            port=port,
            credentials=creds,
            command=command,
            token_mode=token_mode,
            filter_pattern=filter_pattern,
            max_tokens=max_tokens,
            use_cache=use_cache,
            cache_category=cache_category,
            cache_ttl_seconds=cache_ttl_seconds,
        )
        return res.to_dict()

    @mcp.tool()
    async def ssh_execute_batch(
        *,
        host: str,
        username: str,
        commands: list[str],
        port: int = DEFAULT_SSH_PORT,
        password: str | None = None,
        private_key_path: str | None = None,
        token_mode: TokenMode = "full",
        filter_pattern: str | None = None,
        max_tokens: int | None = None,
    ) -> list[dict[str, Any]]:
        """批量执行多条SSH命令。

        在同一SSH连接内顺序执行多条命令，提高效率。
        结果按命令顺序返回。

        Args:
            host: 目标主机地址
            username: SSH用户名
            commands: 要执行的命令列表
            port: SSH端口，默认22
            password: SSH密码（可选）
            private_key_path: SSH私钥路径（可选）
            token_mode: 输出模式
            filter_pattern: 正则过滤模式
            max_tokens: 最大Token数

        Returns:
            list[dict]: 各命令执行结果列表
        """
        creds = resolve_credentials(
            host=host,
            username=username,
            password=password,
            private_key_path=private_key_path,
        )
        results = await ssh.execute_batch(
            host=host,
            port=port,
            credentials=creds,
            commands=commands,
            token_mode=token_mode,
            filter_pattern=filter_pattern,
            max_tokens=max_tokens,
        )
        return [r.to_dict() for r in results]

    @mcp.tool()
    async def ssh_execute_script(
        *,
        host: str,
        username: str,
        script: str,
        port: int = DEFAULT_SSH_PORT,
        shell: str = DEFAULT_SHELL,
        password: str | None = None,
        private_key_path: str | None = None,
        token_mode: TokenMode = "full",
        filter_pattern: str | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """执行Shell脚本。

        通过stdin传入脚本内容执行，适合执行多行脚本。
        脚本通过指定的Shell解释器执行（默认bash -s）。

        Args:
            host: 目标主机地址
            username: SSH用户名
            script: 脚本内容（多行字符串）
            port: SSH端口，默认22
            shell: Shell解释器路径，默认/bin/bash
            password: SSH密码（可选）
            private_key_path: SSH私钥路径（可选）
            token_mode: 输出模式
            filter_pattern: 正则过滤模式
            max_tokens: 最大Token数

        Returns:
            dict: 脚本执行结果
        """
        creds = resolve_credentials(
            host=host,
            username=username,
            password=password,
            private_key_path=private_key_path,
        )
        res = await ssh.execute_script(
            host=host,
            port=port,
            credentials=creds,
            script=script,
            shell=shell,
            token_mode=token_mode,
            filter_pattern=filter_pattern,
            max_tokens=max_tokens,
        )
        return res.to_dict()

    @mcp.tool()
    async def ssh_system_info(
        *,
        host: str,
        username: str,
        port: int = DEFAULT_SSH_PORT,
        password: str | None = None,
        private_key_path: str | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """获取远程系统信息。

        获取目标主机的系统信息，包括主机名、内核版本、运行时间、
        当前用户、操作系统版本等。结果会被缓存（静态类别）。

        Args:
            host: 目标主机地址
            username: SSH用户名
            port: SSH端口，默认22
            password: SSH密码（可选）
            private_key_path: SSH私钥路径（可选）
            force_refresh: 是否强制刷新缓存

        Returns:
            dict: 包含hostname、kernel、uptime、whoami、os_release等信息
        """
        creds = resolve_credentials(
            host=host,
            username=username,
            password=password,
            private_key_path=private_key_path,
        )
        return await ssh.get_system_info(
            host=host,
            port=port,
            credentials=creds,
            force_refresh=force_refresh,
        )

    @mcp.tool()
    async def ssh_session_info() -> dict[str, Any]:
        """查看当前会话缓存状态。

        返回当前MCP会话的缓存使用情况，包括最大容量、
        当前大小、已缓存的键列表等信息。

        Returns:
            dict: 包含cache信息（maxsize、size、keys）
        """
        return await ssh.get_session_info()

    @mcp.tool()
    async def ssh_search_content(
        *,
        host: str,
        username: str,
        query: str,
        path: str,
        port: int = DEFAULT_SSH_PORT,
        password: str | None = None,
        private_key_path: str | None = None,
        token_mode: TokenMode = "truncate",
        max_tokens: int | None = DEFAULT_MAX_TOKENS,
    ) -> dict[str, Any]:
        """在远程主机上搜索文件内容。

        使用grep在指定路径下递归搜索包含指定内容的文件，
        返回匹配行及其行号。默认使用truncate模式限制输出Token数。

        Args:
            host: 目标主机地址
            username: SSH用户名
            query: 搜索关键词
            path: 搜索路径（支持目录递归搜索）
            port: SSH端口，默认22
            password: SSH密码（可选）
            private_key_path: SSH私钥路径（可选）
            token_mode: 输出模式，默认truncate
            max_tokens: 最大Token数，默认800

        Returns:
            dict: 搜索结果
        """
        creds = resolve_credentials(
            host=host,
            username=username,
            password=password,
            private_key_path=private_key_path,
        )
        res = await ssh.search_content(
            host=host,
            port=port,
            credentials=creds,
            query=query,
            path=path,
            token_mode=token_mode,
            max_tokens=max_tokens,
        )
        return res.to_dict()

    @mcp.tool()
    async def ssh_clear_cache(
        *,
        keys: list[str] | None = None,
        tag: str | None = None,
        category: CacheCategory | None = None,
    ) -> dict[str, Any]:
        """清理缓存。

        根据指定条件清理缓存，支持按键名、标签或类别清理。
        如果不指定任何条件，将清理所有缓存。

        Args:
            keys: 要清理的缓存键列表（可选）
            tag: 按标签清理（可选）
            category: 按类别清理 - static/dynamic（可选）

        Returns:
            dict: 包含removed数量和当前cache状态
        """
        return await ssh.clear_cache(keys=keys, tag=tag, category=category)

    @mcp.tool()
    async def ssh_health_check(
        *,
        host: str,
        username: str,
        port: int = DEFAULT_SSH_PORT,
        password: str | None = None,
        private_key_path: str | None = None,
    ) -> dict[str, Any]:
        """SSH连接健康检查。

        快速检测与目标主机的SSH连接是否正常，
        通过执行简单的echo命令验证连通性。

        Args:
            host: 目标主机地址
            username: SSH用户名
            port: SSH端口，默认22
            password: SSH密码（可选）
            private_key_path: SSH私钥路径（可选）

        Returns:
            dict: 包含ok状态、stdout、stderr
        """
        creds = resolve_credentials(
            host=host,
            username=username,
            password=password,
            private_key_path=private_key_path,
        )
        res = await ssh.execute_command(
            host=host,
            port=port,
            credentials=creds,
            command="echo ok",
            token_mode="full",
            use_cache=False,
        )
        return {
            "ok": res.exit_status == 0,
            "stdout": res.stdout.strip(),
            "stderr": res.stderr.strip(),
        }

    @mcp.tool()
    async def file_upload(
        *,
        host: str,
        username: str,
        local_path: str,
        remote_path: str,
        port: int = DEFAULT_SSH_PORT,
        password: str | None = None,
        private_key_path: str | None = None,
        verify_md5: bool = True,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        resume: bool = False,
    ) -> dict[str, Any]:
        """上传文件到远程主机。

        通过SFTP分块上传文件，支持断点续传和MD5校验。
        确保文件完整性和传输可靠性。

        Args:
            host: 目标主机地址
            username: SSH用户名
            local_path: 本地文件路径
            remote_path: 远程目标路径
            port: SSH端口，默认22
            password: SSH密码（可选）
            private_key_path: SSH私钥路径（可选）
            verify_md5: 是否进行MD5校验，默认True
            chunk_size: 分块大小（字节），默认8192
            resume: 是否断点续传，默认False

        Returns:
            dict: 包含传输结果、MD5校验结果等信息
        """
        creds = resolve_credentials(
            host=host,
            username=username,
            password=password,
            private_key_path=private_key_path,
        )
        res = await transfer.upload_file(
            host=host,
            port=port,
            credentials=creds,
            local_path=local_path,
            remote_path=remote_path,
            verify_md5=verify_md5,
            chunk_size=chunk_size,
            resume=resume,
        )
        return res.to_dict()

    @mcp.tool()
    async def file_download(
        *,
        host: str,
        username: str,
        remote_path: str,
        local_path: str,
        port: int = DEFAULT_SSH_PORT,
        password: str | None = None,
        private_key_path: str | None = None,
        verify_md5: bool = True,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        resume: bool = False,
    ) -> dict[str, Any]:
        """从远程主机下载文件。

        通过SFTP分块下载文件，支持断点续传和MD5校验。
        自动创建本地目标目录。

        Args:
            host: 目标主机地址
            username: SSH用户名
            remote_path: 远程文件路径
            local_path: 本地目标路径
            port: SSH端口，默认22
            password: SSH密码（可选）
            private_key_path: SSH私钥路径（可选）
            verify_md5: 是否进行MD5校验，默认True
            chunk_size: 分块大小（字节），默认8192
            resume: 是否断点续传，默认False

        Returns:
            dict: 包含传输结果、MD5校验结果等信息
        """
        creds = resolve_credentials(
            host=host,
            username=username,
            password=password,
            private_key_path=private_key_path,
        )
        res = await transfer.download_file(
            host=host,
            port=port,
            credentials=creds,
            remote_path=remote_path,
            local_path=local_path,
            verify_md5=verify_md5,
            chunk_size=chunk_size,
            resume=resume,
        )
        return res.to_dict()

    @mcp.tool()
    async def file_info(
        *,
        host: str,
        username: str,
        path: str,
        port: int = DEFAULT_SSH_PORT,
        password: str | None = None,
        private_key_path: str | None = None,
    ) -> dict[str, Any]:
        """获取远程文件信息。

        获取远程文件的详细信息，包括大小、权限、修改时间等。

        Args:
            host: 目标主机地址
            username: SSH用户名
            path: 远程文件路径
            port: SSH端口，默认22
            password: SSH密码（可选）
            private_key_path: SSH私钥路径（可选）

        Returns:
            dict: 包含path、size、permissions、mtime、atime等信息
        """
        creds = resolve_credentials(
            host=host,
            username=username,
            password=password,
            private_key_path=private_key_path,
        )
        return await transfer.get_file_info(
            host=host,
            port=port,
            credentials=creds,
            path=path,
        )

    @mcp.tool()
    async def dir_list(
        *,
        host: str,
        username: str,
        path: str,
        port: int = DEFAULT_SSH_PORT,
        password: str | None = None,
        private_key_path: str | None = None,
        page: int = DEFAULT_PAGE,
        page_size: int = DEFAULT_PAGE_SIZE,
        filter_pattern: str | None = None,
    ) -> dict[str, Any]:
        """获取远程目录列表。

        获取指定路径下的文件和子目录列表，支持分页和正则过滤。

        Args:
            host: 目标主机地址
            username: SSH用户名
            path: 远程目录路径
            port: SSH端口，默认22
            password: SSH密码（可选）
            private_key_path: SSH私钥路径（可选）
            page: 页码，默认1
            page_size: 每页大小，默认100
            filter_pattern: 正则过滤模式（可选）

        Returns:
            dict: 包含文件列表、分页信息等
        """
        creds = resolve_credentials(
            host=host,
            username=username,
            password=password,
            private_key_path=private_key_path,
        )
        return await directory.list_directory(
            host=host,
            port=port,
            credentials=creds,
            path=path,
            page=page,
            page_size=page_size,
            filter_pattern=filter_pattern,
        )

    @mcp.tool()
    async def dir_interactive(
        *,
        host: str,
        username: str,
        command: str,
        port: int = DEFAULT_SSH_PORT,
        password: str | None = None,
        private_key_path: str | None = None,
        session_id: str | None = None,
        close_session: bool = False,
    ) -> dict[str, Any]:
        """交互式会话执行命令。

        在持久化的交互式会话中执行命令，保持工作目录和环境变量。
        适合需要多次交互的场景（如cd后继续进行操作）。

        Args:
            host: 目标主机地址
            username: SSH用户名
            command: 要执行的命令
            port: SSH端口，默认22
            password: SSH密码（可选）
            private_key_path: SSH私钥路径（可选）
            session_id: 会话ID（复用现有会话，留空创建新会话）
            close_session: 是否关闭会话，默认False

        Returns:
            dict: 包含命令输出、session_id等信息
        """
        creds = resolve_credentials(
            host=host,
            username=username,
            password=password,
            private_key_path=private_key_path,
        )
        return await directory.execute_interactive(
            host=host,
            port=port,
            credentials=creds,
            command=command,
            session_id=session_id,
            close_session=close_session,
        )

    return mcp
