from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from linux_ssh_mcp.settings import SSHMCPSettings

CacheCategory = Literal["static", "dynamic"]


@dataclass
class CacheEntry:
    value: Any
    created_at_monotonic: float
    expires_at_monotonic: float
    category: CacheCategory
    tags: frozenset[str]


@dataclass(frozen=True)
class CacheInfo:
    maxsize: int
    size: int
    keys: list[str]


_WRITE_COMMAND_RE = re.compile(
    r"(?ix)\b("
    r"rm|rmdir|mv|cp|dd|truncate|touch|chmod|chown|chgrp|"
    r"sed|perl|python|tee|"
    r"apt|apt-get|yum|dnf|pacman|systemctl|service|"
    r"useradd|userdel|usermod|groupadd|groupdel|groupmod|"
    r"iptables|ufw|firewall-cmd|"
    r"reboot|shutdown"
    r")\b"
)

_SHELL_REDIRECT_RE = re.compile(r"[<>]{1,2}|\|\s*tee\b", flags=re.IGNORECASE)
_SED_INPLACE_RE = re.compile(r"\bsed\b.*\s-i(\s|$)", flags=re.IGNORECASE)


class CacheManager:
    def __init__(
        self,
        *,
        settings: SSHMCPSettings,
        time_provider: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._time = time_provider
        self._lock = asyncio.Lock()
        self._data: OrderedDict[Hashable, CacheEntry] = OrderedDict()

    def should_cache_for_command(self, command: str) -> bool:
        cmd = command.strip()
        if not cmd:
            return False
        if _SED_INPLACE_RE.search(cmd) is not None:
            return False
        if _SHELL_REDIRECT_RE.search(cmd) is not None:
            return False
        if _WRITE_COMMAND_RE.search(cmd) is not None:
            return False
        return True

    def _default_ttl_seconds(self, category: CacheCategory) -> int:
        if category == "static":
            return int(self._settings.static_ttl_max_seconds)
        return int(self._settings.dynamic_ttl_max_seconds)

    async def get(self, key: Hashable) -> Any | None:
        now = self._time()
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if now >= entry.expires_at_monotonic:
                self._data.pop(key, None)
                return None

            self._data.move_to_end(key)
            return entry.value

    async def set(
        self,
        key: Hashable,
        value: Any,
        *,
        category: CacheCategory = "dynamic",
        ttl_seconds: int | None = None,
        tags: Iterable[str] = (),
    ) -> None:
        ttl = int(ttl_seconds) if ttl_seconds is not None else self._default_ttl_seconds(category)
        if ttl <= 0:
            return

        now = self._time()
        entry = CacheEntry(
            value=value,
            created_at_monotonic=now,
            expires_at_monotonic=now + float(ttl),
            category=category,
            tags=frozenset(tags),
        )

        async with self._lock:
            self._data[key] = entry
            self._data.move_to_end(key)
            await self._evict_if_needed_unlocked()

    async def clear(
        self,
        *,
        keys: Iterable[Hashable] | None = None,
        tag: str | None = None,
        category: CacheCategory | None = None,
    ) -> int:
        async with self._lock:
            if keys is None and tag is None and category is None:
                removed = len(self._data)
                self._data.clear()
                return removed

            to_remove: set[Hashable] = set(keys or [])
            if tag is not None or category is not None:
                for k, v in self._data.items():
                    if tag is not None and tag not in v.tags:
                        continue
                    if category is not None and v.category != category:
                        continue
                    to_remove.add(k)

            removed = 0
            for k in to_remove:
                if k in self._data:
                    self._data.pop(k, None)
                    removed += 1
            return removed

    async def get_info(self, *, head: int = 50) -> CacheInfo:
        async with self._lock:
            keys = [str(k) for k in list(self._data.keys())[-head:]]
            return CacheInfo(
                maxsize=int(self._settings.cache_maxsize),
                size=len(self._data),
                keys=keys,
            )

    async def _evict_if_needed_unlocked(self) -> None:
        maxsize = int(self._settings.cache_maxsize)
        if maxsize <= 0:
            self._data.clear()
            return

        while len(self._data) > maxsize:
            self._data.popitem(last=False)
