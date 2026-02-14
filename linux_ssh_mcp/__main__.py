from linux_ssh_mcp.config_manager import ConfigManager
from linux_ssh_mcp.logger import setup_logger
from linux_ssh_mcp.mcp_server import create_mcp_server


def main() -> int:
    """
    Linux SSH MCP 服务器主入口

    使用 FastMCP 标准启动方式 (mcp.run(transport="stdio"))
    """
    # 1. 加载配置
    config_manager = ConfigManager.load()

    # 2. 设置日志
    setup_logger(config_manager.settings)

    # 3. 创建 MCP 服务器
    mcp = create_mcp_server(settings=config_manager.settings)

    # 4. 使用 FastMCP 标准启动方式
    mcp.run(transport="stdio")

    return 0


if __name__ == "__main__":
    import sys

    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n服务器已停止")
        sys.exit(0)
    except Exception as e:
        print(f"启动失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
