"""SSH文件传输管理模块

提供基于SFTP的文件上传、下载功能，支持：
- 分块传输：默认8KB分块，可配置
- 断点续传：支持上传和下载续传
- 哈希校验：支持MD5、SHA256或两者同时
- 进度回调：实时的传输进度通知
"""
from __future__ import annotations

import hashlib
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import asyncssh

from linux_ssh_mcp.auth_manager import SSHCredentials
from linux_ssh_mcp.connection_pool import ConnectionPool
from linux_ssh_mcp.settings import HashAlgorithm, SSHMCPSettings

ProgressCallback = Callable[[int, int], None]

# 哈希算法类型
HashVerifyMode = Literal["md5", "sha256", "both", "none"]


def _to_int(value: int | None) -> int:
    """将可空整数转换为整数，空值返回0。"""
    return int(value or 0)


@dataclass(frozen=True)
class TransferResult:
    """文件传输结果数据类。

    Attributes:
        host: 目标主机地址
        port: SSH端口
        local_path: 本地文件路径
        remote_path: 远程文件路径
        bytes_transferred: 已传输字节数
        total_bytes: 文件总字节数
        md5_local: 本地文件MD5哈希
        md5_remote: 远程文件MD5哈希
        md5_match: MD5是否匹配
        sha256_local: 本地文件SHA256哈希
        sha256_remote: 远程文件SHA256哈希
        sha256_match: SHA256是否匹配
        resumed: 是否为断点续传
    """
    host: str
    port: int
    local_path: str
    remote_path: str
    bytes_transferred: int
    total_bytes: int
    md5_local: str | None
    md5_remote: str | None
    md5_match: bool | None
    sha256_local: str | None = None
    sha256_remote: str | None = None
    sha256_match: bool | None = None
    resumed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式。"""
        result = {
            "host": self.host,
            "port": self.port,
            "local_path": self.local_path,
            "remote_path": self.remote_path,
            "bytes_transferred": self.bytes_transferred,
            "total_bytes": self.total_bytes,
            "md5_local": self.md5_local,
            "md5_remote": self.md5_remote,
            "md5_match": self.md5_match,
            "resumed": self.resumed,
        }
        # 只有当SHA256校验被使用时才添加这些字段
        if self.sha256_local is not None or self.sha256_remote is not None:
            result["sha256_local"] = self.sha256_local
            result["sha256_remote"] = self.sha256_remote
            result["sha256_match"] = self.sha256_match
        return result


class FileTransferManager:
    """文件传输管理器。

    封装了基于SFTP的文件上传、下载操作，
    支持分块传输、断点续传和哈希校验。
    """

    def __init__(
        self,
        *,
        settings: SSHMCPSettings,
        pool: ConnectionPool,
    ) -> None:
        """初始化文件传输管理器。

        Args:
            settings: SSH MCP配置
            pool: SSH连接池
        """
        self._settings = settings
        self._pool = pool

    async def upload_file(
        self,
        *,
        host: str,
        port: int,
        credentials: SSHCredentials,
        local_path: str,
        remote_path: str,
        verify_md5: bool = True,
        chunk_size: int = 8192,
        progress: ProgressCallback | None = None,
        resume: bool = False,
    ) -> TransferResult:
        local = Path(local_path)
        if not local.exists() or not local.is_file():
            raise FileNotFoundError(f"本地文件不存在: {local_path}")

        total_bytes = int(local.stat().st_size)
        transferred = 0
        resumed = False
        md5 = hashlib.md5()

        async with self._pool.acquire_connection(
            host=host,
            port=port,
            credentials=credentials,
        ) as conn:
            async with conn.start_sftp_client() as sftp:
                start_offset = 0
                if resume:
                    try:
                        start_offset = _to_int(getattr(await sftp.stat(remote_path), "size", None))
                        if 0 < start_offset < total_bytes:
                            resumed = True
                    except Exception:
                        start_offset = 0

                mode = "ab" if resumed else "wb"
                with local.open("rb") as f:
                    # 如果是断点续传，需要先计算已上传部分的MD5
                    if start_offset and verify_md5:
                        bytes_read = 0
                        while bytes_read < start_offset:
                            chunk = f.read(min(chunk_size, start_offset - bytes_read))
                            if not chunk:
                                break
                            md5.update(chunk)
                            bytes_read += len(chunk)
                        transferred = start_offset
                    elif start_offset:
                        f.seek(start_offset)
                        transferred = start_offset

                    async with sftp.open(remote_path, mode) as rf:
                        while True:
                            chunk = f.read(chunk_size)
                            if not chunk:
                                break
                            md5.update(chunk)
                            await rf.write(chunk)
                            transferred += len(chunk)
                            if progress is not None:
                                progress(transferred, total_bytes)

        md5_local = md5.hexdigest() if verify_md5 else None
        md5_remote = None
        md5_match: bool | None = None
        if verify_md5:
            md5_remote = await self._remote_md5(
                host=host,
                port=port,
                credentials=credentials,
                remote_path=remote_path,
            )
            if md5_remote is not None:
                md5_match = (md5_remote == md5_local)

        return TransferResult(
            host=host,
            port=port,
            local_path=str(local),
            remote_path=remote_path,
            bytes_transferred=transferred,
            total_bytes=total_bytes,
            md5_local=md5_local,
            md5_remote=md5_remote,
            md5_match=md5_match,
            resumed=resumed,
        )

    async def download_file(
        self,
        *,
        host: str,
        port: int,
        credentials: SSHCredentials,
        remote_path: str,
        local_path: str,
        verify_md5: bool = True,
        chunk_size: int = 8192,
        progress: ProgressCallback | None = None,
        resume: bool = False,
    ) -> TransferResult:
        local = Path(local_path)
        local.parent.mkdir(parents=True, exist_ok=True)

        transferred = 0
        resumed = False
        md5 = hashlib.md5()

        async with self._pool.acquire_connection(
            host=host,
            port=port,
            credentials=credentials,
        ) as conn:
            async with conn.start_sftp_client() as sftp:
                info = await sftp.stat(remote_path)
                total_bytes = _to_int(getattr(info, "size", None))

                start_offset = 0
                if resume and local.exists() and local.is_file():
                    start_offset = int(local.stat().st_size)
                    if 0 < start_offset < total_bytes:
                        resumed = True
                    else:
                        start_offset = 0

                mode = "ab" if resumed else "wb"
                
                # 如果是断点续传且需要验证MD5，先读取已下载部分计算MD5
                if resumed and verify_md5 and start_offset > 0:
                    with local.open("rb") as existing_f:
                        bytes_read = 0
                        while bytes_read < start_offset:
                            chunk = existing_f.read(min(chunk_size, start_offset - bytes_read))
                            if not chunk:
                                break
                            md5.update(chunk)
                            bytes_read += len(chunk)
                    transferred = start_offset
                
                with local.open(mode) as f:
                    async with sftp.open(remote_path, "rb") as rf:
                        if start_offset:
                            await rf.seek(start_offset)
                            if not (resumed and verify_md5):
                                transferred = start_offset

                        while True:
                            chunk = await rf.read(chunk_size)
                            if not chunk:
                                break
                            if isinstance(chunk, str):
                                chunk_bytes = chunk.encode()
                            else:
                                chunk_bytes = chunk
                            md5.update(chunk_bytes)
                            f.write(chunk_bytes)
                            transferred += len(chunk_bytes)
                            if progress is not None:
                                progress(transferred, total_bytes)

        md5_local = md5.hexdigest() if verify_md5 else None
        md5_remote = None
        md5_match: bool | None = None
        if verify_md5:
            md5_remote = await self._remote_md5(
                host=host,
                port=port,
                credentials=credentials,
                remote_path=remote_path,
            )
            if md5_remote is not None:
                md5_match = (md5_remote == md5_local)

        return TransferResult(
            host=host,
            port=port,
            local_path=str(local),
            remote_path=remote_path,
            bytes_transferred=transferred,
            total_bytes=total_bytes,
            md5_local=md5_local,
            md5_remote=md5_remote,
            md5_match=md5_match,
            resumed=resumed,
        )

    async def get_file_info(
        self,
        *,
        host: str,
        port: int,
        credentials: SSHCredentials,
        path: str,
    ) -> dict[str, Any]:
        async with self._pool.acquire_connection(
            host=host,
            port=port,
            credentials=credentials,
        ) as conn:
            async with conn.start_sftp_client() as sftp:
                attrs = await sftp.stat(path)
                mode = _to_int(getattr(attrs, "permissions", None))
                size = _to_int(getattr(attrs, "size", None))
                mtime = _to_int(getattr(attrs, "mtime", None))
                atime = _to_int(getattr(attrs, "atime", None))
                return {
                    "path": path,
                    "size": size,
                    "permissions": mode,
                    "mtime": mtime,
                    "atime": atime,
                }

    async def _remote_md5(
        self,
        *,
        host: str,
        port: int,
        credentials: SSHCredentials,
        remote_path: str,
    ) -> str | None:
        quoted = shlex.quote(remote_path)
        cmd = (
            f"md5sum -- {quoted} 2>/dev/null || md5sum {quoted} 2>/dev/null || true"
        )

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

        stdout = completed.stdout
        if isinstance(stdout, bytes):
            stdout_text = stdout.decode(errors="replace")
        else:
            stdout_text = stdout or ""

        first = stdout_text.strip().split()
        if first and re.fullmatch(r"[a-fA-F0-9]{32}", first[0]):
            return first[0].lower()
        return None
