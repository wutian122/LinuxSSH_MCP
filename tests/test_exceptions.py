"""自定义异常模块单元测试

覆盖以下场景：
- 异常层次结构的继承关系
- to_error_dict() 序列化输出
- 各异常类的属性携带
- details 字典的合并逻辑
"""
import pytest

from linux_ssh_mcp.exceptions import (
    CommandBlockedError,
    CommandExecutionError,
    CredentialError,
    FileTransferError,
    SessionError,
    SSHConnectionError,
    SSHMCPError,
)


class TestExceptionHierarchy:
    """异常继承关系测试组。"""

    def test_all_exceptions_inherit_from_base(self) -> None:
        """所有自定义异常都应继承自 SSHMCPError。"""
        exceptions = [
            SSHConnectionError("test"),
            CommandExecutionError("test"),
            CommandBlockedError("test"),
            FileTransferError("test"),
            SessionError("test"),
            CredentialError("test"),
        ]
        for exc in exceptions:
            assert isinstance(exc, SSHMCPError)
            assert isinstance(exc, Exception)

    def test_base_error_is_exception(self) -> None:
        """SSHMCPError 应继承自 Exception。"""
        assert issubclass(SSHMCPError, Exception)


class TestSSHMCPErrorBase:
    """基础异常测试组。"""

    def test_message_attribute(self) -> None:
        """应正确保存 message 属性。"""
        err = SSHMCPError("测试错误消息")
        assert err.message == "测试错误消息"
        assert str(err) == "测试错误消息"

    def test_default_details_empty(self) -> None:
        """未传入 details 时默认为空字典。"""
        err = SSHMCPError("test")
        assert err.details == {}

    def test_custom_details(self) -> None:
        """应正确保存自定义 details。"""
        err = SSHMCPError("test", details={"key": "value"})
        assert err.details == {"key": "value"}

    def test_to_error_dict(self) -> None:
        """to_error_dict() 应返回结构化字典。"""
        err = SSHMCPError("测试", details={"ctx": 123})
        d = err.to_error_dict()
        assert d["error_type"] == "SSHMCPError"
        assert d["message"] == "测试"
        assert d["details"] == {"ctx": 123}


class TestSSHConnectionError:
    """SSH连接错误测试组。"""

    def test_host_and_port_in_details(self) -> None:
        """host 和 port 应合并到 details 中。"""
        err = SSHConnectionError("连接失败", host="10.0.0.1", port=2222)
        assert err.host == "10.0.0.1"
        assert err.port == 2222
        assert err.details["host"] == "10.0.0.1"
        assert err.details["port"] == 2222

    def test_to_error_dict_type(self) -> None:
        """error_type 应为 SSHConnectionError。"""
        err = SSHConnectionError("timeout", host="h")
        d = err.to_error_dict()
        assert d["error_type"] == "SSHConnectionError"

    def test_additional_details_merged(self) -> None:
        """额外的 details 应与 host/port 合并。"""
        err = SSHConnectionError("fail", host="h", port=22, details={"retry": 3})
        assert err.details["host"] == "h"
        assert err.details["retry"] == 3


class TestCommandExecutionError:
    """命令执行错误测试组。"""

    def test_command_and_status_in_details(self) -> None:
        """command、exit_status、stderr 应合并到 details。"""
        err = CommandExecutionError(
            "执行失败",
            command="ls /nonexist",
            exit_status=2,
            stderr="No such file",
        )
        assert err.command == "ls /nonexist"
        assert err.exit_status == 2
        assert err.stderr == "No such file"
        assert err.details["command"] == "ls /nonexist"
        assert err.details["exit_status"] == 2

    def test_default_values(self) -> None:
        """未传入可选参数时应使用默认值。"""
        err = CommandExecutionError("fail")
        assert err.command == ""
        assert err.exit_status == -1
        assert err.stderr == ""


class TestCommandBlockedError:
    """命令拦截错误测试组。"""

    def test_command_and_reason(self) -> None:
        """应正确携带被拦截的命令和原因。"""
        err = CommandBlockedError(
            "拦截",
            command="rm -rf /",
            reason="blacklist_match",
        )
        assert err.command == "rm -rf /"
        assert err.reason == "blacklist_match"
        assert err.details["reason"] == "blacklist_match"


class TestFileTransferError:
    """文件传输错误测试组。"""

    def test_paths_in_details(self) -> None:
        """local_path 和 remote_path 应合并到 details。"""
        err = FileTransferError(
            "传输失败",
            local_path="/tmp/a.txt",
            remote_path="/home/user/a.txt",
        )
        assert err.local_path == "/tmp/a.txt"
        assert err.remote_path == "/home/user/a.txt"
        assert err.details["local_path"] == "/tmp/a.txt"


class TestSessionError:
    """会话错误测试组。"""

    def test_session_id_in_details(self) -> None:
        """session_id 应合并到 details。"""
        err = SessionError("会话超时", session_id="abc123")
        assert err.session_id == "abc123"
        assert err.details["session_id"] == "abc123"


class TestCredentialError:
    """凭据错误测试组。"""

    def test_host_and_username_in_details(self) -> None:
        """host 和 username 应合并到 details。"""
        err = CredentialError("凭据缺失", host="10.0.0.1", username="root")
        assert err.host == "10.0.0.1"
        assert err.username == "root"
        assert err.details["host"] == "10.0.0.1"
        assert err.details["username"] == "root"
