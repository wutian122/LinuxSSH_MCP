from linux_ssh_mcp.config_manager import ConfigManager
from linux_ssh_mcp.logger import setup_logger
from linux_ssh_mcp.mcp_server import create_mcp_server, run_stdio_server


def main() -> int:
    config_manager = ConfigManager.load()
    setup_logger(config_manager.settings)
    server = create_mcp_server(settings=config_manager.settings)
    run_stdio_server(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
