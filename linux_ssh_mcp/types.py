from __future__ import annotations

from typing import Any, Literal, TypedDict

TokenMode = Literal["full", "filter", "truncate"]
CacheCategory = Literal["static", "dynamic"]


class SSHAuthResultDict(TypedDict):
    ok: bool
    host: str
    username: str


class SSHCommandResultDict(TypedDict):
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


class SSHHealthCheckResultDict(TypedDict):
    ok: bool
    stdout: str
    stderr: str


class TransferResultDict(TypedDict, total=False):
    host: str
    port: int
    local_path: str
    remote_path: str
    bytes_transferred: int
    total_bytes: int
    md5_local: str | None
    md5_remote: str | None
    md5_match: bool | None
    sha256_local: str | None
    sha256_remote: str | None
    sha256_match: bool | None
    resumed: bool


class FileInfoResultDict(TypedDict):
    path: str
    size: int
    permissions: int
    mtime: int
    atime: int


class DirListResultDict(TypedDict):
    host: str
    port: int
    path: str
    page: int
    page_size: int
    total: int
    items: list[str]
    filter_pattern: str | None


class InteractiveResultDict(TypedDict):
    session_id: str
    closed: bool
    exit_status: int | None
    stdout: str | None
    stderr: str | None


class SystemInfoResultDict(TypedDict):
    hostname: dict[str, Any]
    kernel: dict[str, Any]
    uptime: dict[str, Any]
    whoami: dict[str, Any]
    os_release: dict[str, Any]


class CacheStatsDict(TypedDict):
    maxsize: int
    size: int
    keys: list[str]


class SessionInfoResultDict(TypedDict):
    cache: CacheStatsDict


class CacheClearResultDict(TypedDict):
    removed: int
    cache: CacheStatsDict
