"""SSH MCP 配置设置模块

使用 Pydantic Settings 管理配置，支持以下配置方式（优先级从高到低）：
1. 环境变量（前缀：SSH_MCP_）
2. .env 文件
3. 默认值

示例环境变量：
    SSH_MCP_LOG_LEVEL=DEBUG
    SSH_MCP_PER_HOST_MAX_CONNECTIONS=10
    SSH_MCP_HASH_ALGORITHM=sha256
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# 支持的哈希算法类型
HashAlgorithm = Literal["md5", "sha256", "both"]

# Known hosts 策略类型
KnownHostsPolicy = Literal["ignore", "warn", "reject"]


class SSHMCPSettings(BaseSettings):
    """SSH MCP 服务器配置类。

    支持通过环境变量、.env文件或默认值进行配置。
    环境变量前缀为 SSH_MCP_。
    """

    model_config = SettingsConfigDict(env_prefix="SSH_MCP_", extra="ignore")

    # 配置文件路径
    config_file: Path = Field(default=Path("ssh_mcp_config.json"))

    # 日志配置
    log_level: str = Field(default="INFO", description="日志级别")
    log_dir: Path = Field(default=Path("logs"), description="日志目录")
    log_rotation: str = Field(default="10 MB", description="日志轮转大小")
    log_retention: str = Field(default="30 days", description="日志保留时间")

    # 连接池配置
    per_host_max_connections: int = Field(
        default=5, ge=1, description="每主机最大连接数"
    )
    command_timeout_seconds: int = Field(
        default=30, ge=1, description="命令执行超时时间(秒)"
    )
    idle_connection_ttl_seconds: int = Field(
        default=300, ge=1, description="空闲连接TTL(秒)"
    )
    connection_retry_count: int = Field(
        default=3, ge=0, description="连接失败重试次数"
    )
    connection_retry_delay_seconds: float = Field(
        default=1.0, ge=0, description="连接重试间隔(秒)"
    )

    # SSH 安全配置
    known_hosts_policy: KnownHostsPolicy = Field(
        default="ignore",
        description="Known hosts 策略: ignore(忽略), warn(警告), reject(拒绝)"
    )

    # 缓存配置
    cache_maxsize: int = Field(default=128, ge=1, description="缓存最大容量")
    static_ttl_min_seconds: int = Field(
        default=300, ge=1, description="静态缓存最小TTL(秒)"
    )
    static_ttl_max_seconds: int = Field(
        default=3600, ge=1, description="静态缓存最大TTL(秒)"
    )
    dynamic_ttl_min_seconds: int = Field(
        default=30, ge=1, description="动态缓存最小TTL(秒)"
    )
    dynamic_ttl_max_seconds: int = Field(
        default=120, ge=1, description="动态缓存最大TTL(秒)"
    )

    # 文件传输配置
    hash_algorithm: HashAlgorithm = Field(
        default="md5",
        description="文件校验哈希算法: md5, sha256, both"
    )
    default_chunk_size: int = Field(
        default=8192, ge=1024, description="默认分块大小(字节)"
    )
