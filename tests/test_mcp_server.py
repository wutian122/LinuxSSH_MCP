import pytest

from linux_ssh_mcp.mcp_server import create_mcp_server
from linux_ssh_mcp.settings import SSHMCPSettings


@pytest.mark.asyncio
async def test_mcp_server_registers_expected_tools() -> None:
    server = create_mcp_server(settings=SSHMCPSettings())
    tools = await server.list_tools()
    names = sorted(t.name for t in tools)

    assert names == sorted(
        [
            "auth_store_credentials",
            "dir_interactive",
            "dir_list",
            "file_download",
            "file_info",
            "file_upload",
            "ssh_clear_cache",
            "ssh_execute",
            "ssh_execute_batch",
            "ssh_execute_script",
            "ssh_health_check",
            "ssh_search_content",
            "ssh_session_info",
            "ssh_system_info",
        ]
    )
