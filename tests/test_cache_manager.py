import pytest

from linux_ssh_mcp.cache_manager import CacheManager
from linux_ssh_mcp.settings import SSHMCPSettings


@pytest.mark.asyncio
async def test_cache_manager_set_get_and_expire() -> None:
    now = 100.0

    def time_provider() -> float:
        return now

    cache = CacheManager(settings=SSHMCPSettings(cache_maxsize=128), time_provider=time_provider)
    await cache.set("k", "v", ttl_seconds=10)
    assert await cache.get("k") == "v"

    now = 111.0
    assert await cache.get("k") is None


@pytest.mark.asyncio
async def test_cache_manager_lru_eviction() -> None:
    cache = CacheManager(settings=SSHMCPSettings(cache_maxsize=2))
    await cache.set("a", 1)
    await cache.set("b", 2)
    assert await cache.get("a") == 1
    await cache.set("c", 3)
    assert await cache.get("b") is None
    assert await cache.get("a") == 1
    assert await cache.get("c") == 3


@pytest.mark.asyncio
async def test_cache_manager_clear_by_tag_and_category() -> None:
    cache = CacheManager(settings=SSHMCPSettings(cache_maxsize=10))
    await cache.set("s1", "x", category="static", tags=["sys"]) 
    await cache.set("d1", "y", category="dynamic", tags=["proc"]) 
    await cache.set("s2", "z", category="static", tags=["sys", "host"]) 

    removed = await cache.clear(tag="sys", category="static")
    assert removed == 2
    assert await cache.get("s1") is None
    assert await cache.get("s2") is None
    assert await cache.get("d1") == "y"


def test_cache_manager_should_cache_for_command() -> None:
    cache = CacheManager(settings=SSHMCPSettings())
    assert cache.should_cache_for_command("uname -a") is True
    assert cache.should_cache_for_command("rm -rf /tmp/x") is False
    assert cache.should_cache_for_command("echo hi > /tmp/a") is False
    assert cache.should_cache_for_command("sed -i 's/a/b/' file") is False

