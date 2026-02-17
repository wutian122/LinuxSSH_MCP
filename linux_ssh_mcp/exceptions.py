"""Linux SSH MCP 自定义异常模块

定义项目中使用的所有自定义异常类，提供结构化的错误处理。
异常层次结构：
    SSHMCPError (基类)
    ├── SSHConnectionError     - SSH连接相关错误
    ├── CommandExecutionError   - 命令执行相关错误
    ├── CommandBlockedError     - 命令被安全策略拦截
    ├── FileTransferError       - 文件传输相关错误
    ├── SessionError            - 交互式会话相关错误
    └── CredentialError         - 凭据相关错误
"""
from __future__ import annotations


class SSHMCPError(Exception):
    """SSH MCP 基础异常类。

    所有自定义异常的基类，提供统一的错误消息格式。

    Attributes:
        message: 用户友好的错误描述信息
        details: 可选的附加错误详情字典
    """

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        """初始化基础异常。

        Args:
            message: 用户友好的错误描述信息
            details: 可选的附加错误详情
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_error_dict(self) -> dict[str, object]:
        """将异常转换为结构化的错误字典。

        Returns:
            包含error_type、message和details的字典
        """
        return {
            "error_type": type(self).__name__,
            "message": self.message,
            "details": self.details,
        }


class SSHConnectionError(SSHMCPError):
    """SSH连接错误。

    当SSH连接建立失败、超时或连接被拒绝时抛出。

    Attributes:
        host: 目标主机地址
        port: 目标SSH端口
    """

    def __init__(
        self,
        message: str,
        *,
        host: str = "",
        port: int = 22,
        details: dict[str, object] | None = None,
    ) -> None:
        """初始化SSH连接错误。

        Args:
            message: 错误描述信息
            host: 目标主机地址
            port: 目标SSH端口
            details: 附加错误详情
        """
        merged_details = {"host": host, "port": port, **(details or {})}
        super().__init__(message, details=merged_details)
        self.host = host
        self.port = port


class CommandExecutionError(SSHMCPError):
    """命令执行错误。

    当远程命令执行失败（非零退出码）或执行超时时抛出。

    Attributes:
        command: 执行失败的命令
        exit_status: 命令退出状态码
        stderr: 标准错误输出
    """

    def __init__(
        self,
        message: str,
        *,
        command: str = "",
        exit_status: int = -1,
        stderr: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        """初始化命令执行错误。

        Args:
            message: 错误描述信息
            command: 执行失败的命令
            exit_status: 命令退出状态码
            stderr: 标准错误输出
            details: 附加错误详情
        """
        merged_details = {
            "command": command,
            "exit_status": exit_status,
            "stderr": stderr,
            **(details or {}),
        }
        super().__init__(message, details=merged_details)
        self.command = command
        self.exit_status = exit_status
        self.stderr = stderr


class CommandBlockedError(SSHMCPError):
    """命令被安全策略拦截错误。

    当命令命中黑名单正则或被安全校验器拦截时抛出。

    Attributes:
        command: 被拦截的命令
        reason: 拦截原因
    """

    def __init__(
        self,
        message: str,
        *,
        command: str = "",
        reason: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        """初始化命令拦截错误。

        Args:
            message: 错误描述信息
            command: 被拦截的命令
            reason: 拦截原因
            details: 附加错误详情
        """
        merged_details = {"command": command, "reason": reason, **(details or {})}
        super().__init__(message, details=merged_details)
        self.command = command
        self.reason = reason


class FileTransferError(SSHMCPError):
    """文件传输错误。

    当文件上传/下载失败、校验不通过时抛出。

    Attributes:
        local_path: 本地文件路径
        remote_path: 远程文件路径
    """

    def __init__(
        self,
        message: str,
        *,
        local_path: str = "",
        remote_path: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        """初始化文件传输错误。

        Args:
            message: 错误描述信息
            local_path: 本地文件路径
            remote_path: 远程文件路径
            details: 附加错误详情
        """
        merged_details = {
            "local_path": local_path,
            "remote_path": remote_path,
            **(details or {}),
        }
        super().__init__(message, details=merged_details)
        self.local_path = local_path
        self.remote_path = remote_path


class SessionError(SSHMCPError):
    """交互式会话错误。

    当会话创建、命令执行或会话清理失败时抛出。

    Attributes:
        session_id: 出错的会话ID
    """

    def __init__(
        self,
        message: str,
        *,
        session_id: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        """初始化会话错误。

        Args:
            message: 错误描述信息
            session_id: 出错的会话ID
            details: 附加错误详情
        """
        merged_details = {"session_id": session_id, **(details or {})}
        super().__init__(message, details=merged_details)
        self.session_id = session_id


class CredentialError(SSHMCPError):
    """凭据错误。

    当凭据缺失、无效或keyring操作失败时抛出。

    Attributes:
        host: 关联的主机地址
        username: 关联的用户名
    """

    def __init__(
        self,
        message: str,
        *,
        host: str = "",
        username: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        """初始化凭据错误。

        Args:
            message: 错误描述信息
            host: 关联的主机地址
            username: 关联的用户名
            details: 附加错误详情
        """
        merged_details = {"host": host, "username": username, **(details or {})}
        super().__init__(message, details=merged_details)
        self.host = host
        self.username = username
