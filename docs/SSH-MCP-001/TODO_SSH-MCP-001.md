# TODO_SSH-MCP-001（待办清单与操作指引）

## 1. 待办清单

### 1.1 建议补充的真实联调用例

- 准备一台可 SSH 登录的 Linux 目标机（推荐测试机）
- 覆盖用例：
  - ssh_health_check / ssh_execute / ssh_execute_batch / ssh_execute_script
  - dir_list / dir_interactive（会话复用、关闭、超时）
  - file_upload / file_download / file_info

### 1.2 MD5 兜底策略

- 当前远端校验依赖 md5sum
- 建议增加兜底：openssl/md5/busybox md5sum（按目标机环境选择）

### 1.3 安全策略强化

- 当前：黑名单拦截 + 高风险命令告警
- 建议：
  - 增加“允许列表模式”（只允许只读命令）
  - 增加多租户隔离（按调用方限流/审计）

### 1.4 交互会话输出与错误流

- 当前 stderr 读取为“尽力而为”方式
- 建议：对 stderr 做更稳健的并发读取与合并策略，避免长 stderr 堵塞

## 2. 使用指引（最短路径）

### 2.1 启动 MCP Server

```powershell
.\.venv\Scripts\python main.py
```

### 2.2 Claude Desktop / Cherry Studio 接入

- 直接参考：项目使用文档.md 的配置示例

### 2.3 首次使用凭据

- 若希望把密码存到 keyring，先调用：auth_store_credentials
- 若不想使用 keyring，工具调用时直接传入 password/private_key_path

## 3. 常见缺少的配置

- ssh_mcp_config.json（可选）
- .env（可选）
  - SSH_MCP_LOG_LEVEL
  - SSH_MCP_PER_HOST_MAX_CONNECTIONS
  - SSH_MCP_COMMAND_TIMEOUT_SECONDS
  - SSH_MCP_IDLE_CONNECTION_TTL_SECONDS

