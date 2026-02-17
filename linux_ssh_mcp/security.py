"""SSH命令安全校验模块

提供命令安全校验功能，包括：
- 黑名单拦截：匹配到的命令将被直接拦截（如 rm -rf /、mkfs 等）
- 危险命令警告：匹配到的命令将附加警告信息（如 rm、chmod 等）
- 可扩展的白名单机制：支持配置放行特定命令模式
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from linux_ssh_mcp.exceptions import CommandBlockedError

# 黑名单正则：匹配到则直接拦截
_BLACKLIST_RE = re.compile(
    r"(?ix)"
    r"("
    r"\brm\s+-rf\s+/"              # rm -rf /
    r"|\bmkfs\b"                   # 格式化文件系统
    r"|\bdd\b\s+if="              # dd 磁盘写入
    r"|:\(\)\s*\{\s*:\s*\|\s*:\s*;\s*\}\s*;"  # fork 炸弹
    r"|\bshutdown\b"              # 关机
    r"|\breboot\b"                # 重启
    r")"
)

# 危险命令正则：匹配到则附加警告
_DANGEROUS_RE = re.compile(
    r"(?ix)\b("
    r"rm|rmdir|mv|cp|dd|truncate|chmod|chown|chgrp|"
    r"sed|perl|python|tee|"
    r"apt|apt-get|yum|dnf|pacman|systemctl|service|"
    r"useradd|userdel|usermod|groupadd|groupdel|groupmod|"
    r"iptables|ufw|firewall-cmd"
    r")\b"
)


@dataclass(frozen=True)
class SecurityCheckResult:
    """安全校验结果。

    Attributes:
        allowed: 命令是否被允许执行
        warnings: 安全警告信息列表
    """

    allowed: bool
    warnings: list[str]


class CommandSecurityValidator:
    """命令安全校验器。

    负责对SSH命令进行安全检查，包括黑名单拦截和危险命令警告。
    支持可选的白名单配置，白名单中的命令模式将跳过所有安全检查。

    Attributes:
        _whitelist_patterns: 白名单正则模式列表
    """

    def __init__(self, *, whitelist_patterns: list[str] | None = None) -> None:
        """初始化命令安全校验器。

        Args:
            whitelist_patterns: 白名单正则模式列表，匹配的命令将跳过安全检查
        """
        self._whitelist_patterns: list[re.Pattern[str]] = []
        if whitelist_patterns:
            for pattern in whitelist_patterns:
                self._whitelist_patterns.append(re.compile(pattern, re.IGNORECASE))

    def validate_command(self, command: str) -> SecurityCheckResult:
        """校验单条命令的安全性。

        对命令进行黑名单和危险命令检查。如果命令匹配白名单，
        则跳过所有检查直接放行。

        Args:
            command: 待校验的命令字符串

        Returns:
            SecurityCheckResult: 包含是否允许和警告信息的校验结果

        Raises:
            CommandBlockedError: 当命令命中黑名单时抛出
        """
        cmd = command.strip()
        if not cmd:
            return SecurityCheckResult(allowed=True, warnings=[])

        # 白名单优先放行
        if self._is_whitelisted(cmd):
            return SecurityCheckResult(allowed=True, warnings=[])

        # 黑名单拦截
        if _BLACKLIST_RE.search(cmd) is not None:
            raise CommandBlockedError(
                "命令命中黑名单，已拦截",
                command=cmd,
                reason="blacklist_match",
            )

        # 危险命令警告
        warnings: list[str] = []
        if _DANGEROUS_RE.search(cmd) is not None:
            warnings.append("检测到高风险命令，请确认执行意图")

        return SecurityCheckResult(allowed=True, warnings=warnings)

    def validate_script(self, script: str) -> SecurityCheckResult:
        """校验脚本内容的安全性。

        对整个脚本内容进行危险命令检查。脚本不做黑名单拦截
        （因为脚本中的命令可能被条件语句包裹），仅做警告提示。

        Args:
            script: 待校验的脚本内容

        Returns:
            SecurityCheckResult: 包含是否允许和警告信息的校验结果
        """
        if not script.strip():
            return SecurityCheckResult(allowed=True, warnings=[])

        warnings: list[str] = []
        if _DANGEROUS_RE.search(script) is not None:
            warnings.append("脚本包含潜在高风险命令，请确认执行意图")

        return SecurityCheckResult(allowed=True, warnings=warnings)

    def _is_whitelisted(self, command: str) -> bool:
        """检查命令是否匹配白名单。

        Args:
            command: 待检查的命令

        Returns:
            是否匹配白名单中的任一模式
        """
        return any(pattern.search(command) is not None for pattern in self._whitelist_patterns)
