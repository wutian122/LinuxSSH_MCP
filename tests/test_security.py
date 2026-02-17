"""CommandSecurityValidator 单元测试模块

覆盖以下场景：
- 黑名单命令拦截
- 危险命令警告
- 白名单放行
- 空命令处理
- 脚本内容校验
"""
import pytest

from linux_ssh_mcp.exceptions import CommandBlockedError
from linux_ssh_mcp.security import CommandSecurityValidator


class TestBlacklistBlocking:
    """黑名单拦截测试组。"""

    def test_rm_rf_root_is_blocked(self) -> None:
        """rm -rf / 应被拦截。"""
        validator = CommandSecurityValidator()
        with pytest.raises(CommandBlockedError, match="黑名单"):
            validator.validate_command("rm -rf /")

    def test_mkfs_is_blocked(self) -> None:
        """mkfs 命令应被拦截。"""
        validator = CommandSecurityValidator()
        with pytest.raises(CommandBlockedError, match="黑名单"):
            validator.validate_command("mkfs /dev/sda1")

    def test_dd_if_is_blocked(self) -> None:
        """dd if= 命令应被拦截。"""
        validator = CommandSecurityValidator()
        with pytest.raises(CommandBlockedError, match="黑名单"):
            validator.validate_command("dd if=/dev/zero of=/dev/sda")

    def test_fork_bomb_is_blocked(self) -> None:
        """fork 炸弹应被拦截。"""
        validator = CommandSecurityValidator()
        with pytest.raises(CommandBlockedError, match="黑名单"):
            validator.validate_command(":(){ :|:; };")

    def test_shutdown_is_blocked(self) -> None:
        """shutdown 命令应被拦截。"""
        validator = CommandSecurityValidator()
        with pytest.raises(CommandBlockedError, match="黑名单"):
            validator.validate_command("shutdown -h now")

    def test_reboot_is_blocked(self) -> None:
        """reboot 命令应被拦截。"""
        validator = CommandSecurityValidator()
        with pytest.raises(CommandBlockedError, match="黑名单"):
            validator.validate_command("reboot")

    def test_blocked_error_contains_command(self) -> None:
        """CommandBlockedError 应携带被拦截的命令信息。"""
        validator = CommandSecurityValidator()
        with pytest.raises(CommandBlockedError) as exc_info:
            validator.validate_command("rm -rf /")
        assert exc_info.value.command == "rm -rf /"
        assert exc_info.value.reason == "blacklist_match"


class TestDangerousWarnings:
    """危险命令警告测试组。"""

    @pytest.mark.parametrize(
        "command",
        [
            "rm file.txt",
            "chmod 777 /tmp/test",
            "chown root:root /etc/passwd",
            "apt-get install vim",
            "systemctl restart nginx",
            "useradd testuser",
            "iptables -F",
            "ufw disable",
        ],
    )
    def test_dangerous_commands_produce_warnings(self, command: str) -> None:
        """危险命令应产生警告但不拦截。"""
        validator = CommandSecurityValidator()
        result = validator.validate_command(command)
        assert result.allowed is True
        assert len(result.warnings) > 0
        assert "高风险" in result.warnings[0]

    def test_safe_commands_no_warnings(self) -> None:
        """安全命令不应产生警告。"""
        validator = CommandSecurityValidator()
        safe_commands = ["ls -la", "cat /etc/hostname", "whoami", "uptime", "df -h"]
        for cmd in safe_commands:
            result = validator.validate_command(cmd)
            assert result.allowed is True
            assert len(result.warnings) == 0, f"Unexpected warning for: {cmd}"


class TestWhitelist:
    """白名单放行测试组。"""

    def test_whitelisted_command_bypasses_dangerous_check(self) -> None:
        """白名单中的命令应跳过危险命令检查。"""
        validator = CommandSecurityValidator(whitelist_patterns=[r"^apt-get\s+install"])
        result = validator.validate_command("apt-get install vim")
        assert result.allowed is True
        assert len(result.warnings) == 0

    def test_whitelisted_command_bypasses_blacklist(self) -> None:
        """白名单中的命令应跳过黑名单检查。"""
        validator = CommandSecurityValidator(whitelist_patterns=[r"^reboot$"])
        result = validator.validate_command("reboot")
        assert result.allowed is True

    def test_non_matching_whitelist_still_checks(self) -> None:
        """不匹配白名单的命令仍然需要安全检查。"""
        validator = CommandSecurityValidator(whitelist_patterns=[r"^safe-cmd"])
        result = validator.validate_command("rm file.txt")
        assert result.allowed is True
        assert len(result.warnings) > 0

    def test_multiple_whitelist_patterns(self) -> None:
        """多个白名单模式应逐一匹配。"""
        validator = CommandSecurityValidator(
            whitelist_patterns=[r"^apt-get", r"^systemctl\s+status"]
        )
        r1 = validator.validate_command("apt-get update")
        r2 = validator.validate_command("systemctl status nginx")
        assert len(r1.warnings) == 0
        assert len(r2.warnings) == 0


class TestEmptyAndEdgeCases:
    """空值和边界情况测试组。"""

    def test_empty_command_is_allowed(self) -> None:
        """空命令应被允许（由上层校验）。"""
        validator = CommandSecurityValidator()
        result = validator.validate_command("")
        assert result.allowed is True
        assert len(result.warnings) == 0

    def test_whitespace_only_command_is_allowed(self) -> None:
        """纯空白命令应被允许。"""
        validator = CommandSecurityValidator()
        result = validator.validate_command("   ")
        assert result.allowed is True

    def test_default_validator_no_whitelist(self) -> None:
        """默认校验器无白名单。"""
        validator = CommandSecurityValidator()
        assert len(validator._whitelist_patterns) == 0


class TestScriptValidation:
    """脚本内容校验测试组。"""

    def test_safe_script_no_warnings(self) -> None:
        """安全脚本不应产生警告。"""
        validator = CommandSecurityValidator()
        script = "#!/bin/bash\necho hello\ndate\nwhoami\n"
        result = validator.validate_script(script)
        assert result.allowed is True
        assert len(result.warnings) == 0

    def test_dangerous_script_produces_warnings(self) -> None:
        """包含危险命令的脚本应产生警告。"""
        validator = CommandSecurityValidator()
        script = "#!/bin/bash\nrm -f /tmp/old_files/*\necho done\n"
        result = validator.validate_script(script)
        assert result.allowed is True
        assert len(result.warnings) > 0
        assert "高风险" in result.warnings[0]

    def test_empty_script_is_allowed(self) -> None:
        """空脚本应被允许。"""
        validator = CommandSecurityValidator()
        result = validator.validate_script("")
        assert result.allowed is True
        assert len(result.warnings) == 0

    def test_whitespace_script_is_allowed(self) -> None:
        """纯空白脚本应被允许。"""
        validator = CommandSecurityValidator()
        result = validator.validate_script("   \n   ")
        assert result.allowed is True
