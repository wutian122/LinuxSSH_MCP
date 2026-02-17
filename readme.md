# Linux SSH MCP 远程命令执行工具

基于 MCP (Model Context Protocol) 的 Linux SSH 远程运维工具集，支持命令执行、文件传输、目录管理、交互式会话等功能。

## 功能特性

| 功能模块 | 描述 |
|---------|------|
| SSH连接池 | 每主机最大5并发，连接复用，后台清理空闲连接，请求合并防惊群 |
| 安全校验 | 黑名单拦截（rm -rf /、mkfs等）、危险命令警告、可扩展白名单 |
| 异常体系 | 6级结构化异常层次，统一 `to_error_dict()` 序列化 |
| 类型安全 | TypedDict 强类型返回值，mypy strict 模式 |
| 缓存系统 | TTL+LRU，静态/动态分层，最大128条 |
| 命令执行 | 单命令/批量/脚本执行，危险命令拦截与警告 |
| Token优化 | 全量/正则过滤/按Token截断三种模式 |
| 文件传输 | SFTP上传/下载，MD5/SHA256校验，分块传输 |
| 交互式会话 | 会话复用，超时清理 |
| 凭据管理 | keyring存储密码/私钥路径 |

## 快速开始

### 1. 安装

```bash
cd LinuxSSH_MCP
python -m venv .venv

# Windows
.\.venv\Scripts\python -m pip install -e ".[dev]"

# Linux/macOS
.venv/bin/pip install -e ".[dev]"
```

### 2. 启动

```bash
# Windows
.\.venv\Scripts\python main.py

# Linux/macOS
.venv/bin/python main.py
```

## MCP 客户端配置

### Claude Desktop / Claude Code 配置

在 MCP 配置文件中添加以下内容（将 `PATH` 替换为实际路径）：

```json
{
  "mcpServers": {
    "linux-ssh-mcp": {
      "command": "PATH/LinuxSSH_MCP/.venv/Scripts/python.exe",
      "args": [
        "PATH/LinuxSSH_MCP/main.py"
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
      "command": "PATH/LinuxSSH_MCP/.venv/Scripts/python.exe",
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
| SSH_MCP_CONNECTION_RETRY_DELAY_SECONDS | 1.0 | 连接重试间隔(秒) |
| SSH_MCP_KNOWN_HOSTS_POLICY | ignore | known_hosts策略(ignore/warn/reject) |
| SSH_MCP_HASH_ALGORITHM | md5 | 哈希算法(md5/sha256/both) |
| SSH_MCP_CACHE_MAXSIZE | 128 | 缓存最大容量 |

## MCP 工具清单 (14个)

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
LinuxSSH_MCP/
├── linux_ssh_mcp/              # 核心模块
│   ├── __init__.py             # 包初始化
│   ├── __main__.py             # 模块入口
│   ├── mcp_server.py           # MCP协议层，14个工具接口
│   ├── ssh_manager.py          # SSH命令执行核心
│   ├── connection_pool.py      # 连接池（后台清理+请求合并+索引）
│   ├── security.py             # 命令安全校验（黑名单/警告/白名单）
│   ├── exceptions.py           # 6级结构化异常层次
│   ├── types.py                # TypedDict 类型定义
│   ├── cache_manager.py        # TTL+LRU缓存管理
│   ├── directory_manager.py    # 目录管理与交互式会话
│   ├── file_transfer_manager.py # 文件上传下载
│   ├── auth_manager.py         # 凭据管理(keyring)
│   ├── config_manager.py       # 配置文件管理
│   ├── settings.py             # Pydantic配置
│   ├── constants.py            # 常量定义
│   ├── token_optimizer.py      # Token优化器
│   └── logger.py               # 日志配置(loguru)
├── tests/                      # 测试用例 (76个测试)
│   ├── test_connection_pool.py # 连接池测试 (34个)
│   ├── test_security.py        # 安全校验测试 (27个)
│   ├── test_exceptions.py      # 异常体系测试 (15个)
│   ├── test_ssh_manager.py     # SSH管理测试
│   ├── test_auth_manager.py    # 凭据管理测试
│   ├── test_cache_manager.py   # 缓存管理测试
│   └── ...                     # 其他模块测试
├── main.py                     # 入口文件
├── pyproject.toml              # 项目配置(构建/mypy/ruff/pytest)
├── requirements.txt            # 依赖列表
├── .env.example                # 环境变量示例
└── .gitignore                  # Git忽略规则
```

## 技术栈

| 技术 | 用途 |
|------|------|
| Python 3.10+ | 运行时 |
| asyncssh | SSH异步连接 |
| MCP (FastMCP) | Model Context Protocol 服务端 |
| Pydantic v2 | 配置验证 |
| keyring | 凭据安全存储 |
| loguru | 结构化日志 |
| pytest + pytest-asyncio | 异步测试 |
| mypy (strict) | 静态类型检查 |
| ruff | 代码风格检查 |

## 开发

### 运行测试

```bash
# 运行全部测试
.venv/Scripts/python -m pytest tests/ -v

# 运行特定模块测试
.venv/Scripts/python -m pytest tests/test_connection_pool.py -v

# 查看覆盖率
.venv/Scripts/python -m pytest tests/ --cov=linux_ssh_mcp --cov-report=term-missing
```

### 代码质量检查

```bash
# 类型检查
.venv/Scripts/python -m mypy linux_ssh_mcp/

# 代码风格检查
.venv/Scripts/python -m ruff check linux_ssh_mcp/

# 自动修复
.venv/Scripts/python -m ruff check --fix linux_ssh_mcp/
```

## 许可证

MIT License
