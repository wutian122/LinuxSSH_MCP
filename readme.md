# Linux SSH MCP 远程命令执行工具

基于 MCP (Model Context Protocol) 的 Linux SSH 远程运维工具集，支持命令执行、文件传输、目录管理、交互式会话等功能。

## 功能特性

| 功能模块 | 描述 |
|---------|------|
| SSH连接池 | 每主机最大5并发，连接复用，空闲300秒清理 |
| 缓存系统 | TTL+LRU，静态/动态分层，最大128条 |
| 命令执行 | 单命令/批量/脚本执行，危险命令拦截 |
| Token优化 | 全量/正则过滤/按Token截断三种模式 |
| 文件传输 | SFTP上传/下载，MD5/SHA256校验，分块传输 |
| 交互式会话 | 会话复用，超时清理 |
| 凭据管理 | keyring存储密码/私钥路径 |

## 快速开始

### 1. 安装

```powershell
cd "E:\code\python-use\项目\LinuxSSH_MCP远程命令执行工具"
python -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -e .
```

### 2. 启动

```powershell
.\.venv\Scripts\python main.py
# 或使用模块方式
.\.venv\Scripts\python -m linux_ssh_mcp
```

## MCP 客户端配置

### Claude Desktop / Antigravity 配置

在 MCP 配置文件中添加以下内容：

```json
{
  "mcpServers": {
    "linux-ssh-mcp": {
      "command": "PATH//LinuxSSH_MCP//.venv//Scripts//python.exe",
      "args": [
        "PATH//LinuxSSH_MCP//main.py"
      ]
    }
  }
}
```

### 模块方式配置
	
```json
{
  "mcpServers": {
    "linux-ssh-mcp": {
      "command": "PATH//LinuxSSH_MCP//.venv//Scripts//python.exe",
      "args": ["-m", "linux_ssh_mcp"]
    }
  }
}
```

## 环境变量配置

支持通过环境变量覆盖默认配置（前缀：`SSH_MCP_`）：

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| SSH_MCP_LOG_LEVEL | INFO | 日志级别 |
| SSH_MCP_PER_HOST_MAX_CONNECTIONS | 5 | 每主机最大连接数 |
| SSH_MCP_COMMAND_TIMEOUT_SECONDS | 30 | 命令超时时间(秒) |
| SSH_MCP_IDLE_CONNECTION_TTL_SECONDS | 300 | 空闲连接TTL(秒) |
| SSH_MCP_CONNECTION_RETRY_COUNT | 3 | 连接重试次数 |
| SSH_MCP_KNOWN_HOSTS_POLICY | ignore | known_hosts策略 |
| SSH_MCP_HASH_ALGORITHM | md5 | 哈希算法(md5/sha256/both) |

## 工具清单 (14个)

### 凭据管理
- `auth_store_credentials` - 存储SSH凭据到keyring

### 命令执行
- `ssh_execute` - 执行单条命令
- `ssh_execute_batch` - 批量执行命令
- `ssh_execute_script` - 执行Shell脚本
- `ssh_system_info` - 获取系统信息
- `ssh_search_content` - 远端grep搜索
- `ssh_health_check` - SSH连接健康检查

### 缓存管理
- `ssh_session_info` - 查看缓存状态
- `ssh_clear_cache` - 清理缓存

### 文件传输
- `file_upload` - 上传文件
- `file_download` - 下载文件
- `file_info` - 获取文件信息

### 目录操作
- `dir_list` - 目录列表(分页)
- `dir_interactive` - 交互式会话

## 项目结构

```
LinuxSSH_MCP远程命令执行工具/
├── linux_ssh_mcp/          # 核心模块
│   ├── mcp_server.py       # MCP协议层，14个工具接口
│   ├── ssh_manager.py      # SSH命令执行核心
│   ├── connection_pool.py  # 连接池管理
│   ├── cache_manager.py    # TTL+LRU缓存管理
│   ├── directory_manager.py # 目录管理与交互式会话
│   ├── file_transfer_manager.py # 文件上传下载
│   ├── auth_manager.py     # 凭据管理(keyring)
│   ├── settings.py         # Pydantic配置
│   ├── constants.py        # 常量定义
│   └── token_optimizer.py  # Token优化器
├── tests/                  # 测试用例
├── main.py                 # 入口文件
├── pyproject.toml          # 项目配置
└── requirements.txt        # 依赖列表
```

## 文档

- [项目使用文档](./项目使用文档.md) - 详细使用说明
- [项目解析文档](./项目解析文档.md) - 架构解析
