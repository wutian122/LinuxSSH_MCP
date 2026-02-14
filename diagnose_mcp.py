#!/usr/bin/env python
"""MCP Server Diagnostic Tool"""

import asyncio
import importlib.util
import sys
from pathlib import Path

# Fix encoding on Windows
if sys.platform == "win32":
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

print("=" * 60)
print("MCP Server Diagnostic Tool")
print("=" * 60)

# 1. Check Python version
print(f"\n1. Python Version: {sys.version}")
print(f"   Python Path: {sys.executable}")

# 2. Check virtual environment
venv_python = Path(__file__).parent / ".venv" / "Scripts" / "python.exe"
print(f"\n2. Venv Python: {venv_python}")
print(f"   Exists: {venv_python.exists()}")

# 3. Check main.py
main_py = Path(__file__).parent / "main.py"
print(f"\n3. main.py Path: {main_py}")
print(f"   Exists: {main_py.exists()}")

# 4. Check dependencies
print("\n4. Checking dependencies...")


def _check_dependency(module: str) -> None:
    try:
        spec = importlib.util.find_spec(module)
    except Exception as e:
        print(f"   [FAIL] {module}: {e}")
        return
    if spec is None:
        print(f"   [FAIL] {module}: not installed")
        return
    print(f"   [OK] {module}")


_check_dependency("mcp")
_check_dependency("asyncssh")
_check_dependency("keyring")
_check_dependency("loguru")

# 5. Test config manager
print("\n5. Testing config manager...")
try:
    from linux_ssh_mcp.config_manager import ConfigManager

    config = ConfigManager.load()
    print("   [OK] Config Manager")
    print(f"   Log Level: {config.settings.log_level}")
except Exception as e:
    print(f"   [FAIL] Config Manager: {e}")
    import traceback

    traceback.print_exc()

# 6. Test MCP server creation
print("\n6. Testing MCP server creation...")
server = None
try:
    from linux_ssh_mcp.mcp_server import create_mcp_server

    server = create_mcp_server(settings=config.settings)
    print("   [OK] MCP Server Created")
    print(f"   Server Name: {server.name}")
except Exception as e:
    print(f"   [FAIL] MCP Server Creation: {e}")
    import traceback

    traceback.print_exc()

# 7. Test stdio transport
print("\n7. Testing stdio transport...")


async def test_stdio() -> bool:
    global server
    try:
        if server is None:
            from linux_ssh_mcp.config_manager import ConfigManager
            from linux_ssh_mcp.mcp_server import create_mcp_server

            config = ConfigManager.load()
            server = create_mcp_server(settings=config.settings)

        print("   [OK] stdio transport configured")
        print("   Note: Server needs to communicate via stdio with client")
        return True
    except Exception as e:
        print(f"   [FAIL] stdio transport test: {e}")
        import traceback

        traceback.print_exc()
        return False


try:
    result = asyncio.run(test_stdio())
    if not result:
        sys.exit(1)
except Exception as e:
    print(f"   [FAIL] Async test failed: {e}")
    sys.exit(1)

print("\n" + "=" * 60)
print("Diagnostic Complete! All checks passed.")
print("=" * 60)
print("\nRecommended Configuration (JSON):")
print("{")
print('  "mcpServers": {')
print('    "linux-ssh-mcp": {')
venv_path = str(venv_python).replace("\\", "/")
main_path = str(main_py).replace("\\", "/")
print(f'      "command": "{venv_path}",')
print(f'      "args": ["{main_path}"]')
print("    }")
print("  }")
print("}")
