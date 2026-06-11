# agy-mcp

[English](README.md) | 简体中文

`agy-mcp` 是 Google Antigravity CLI (`agy`) 的本地 MCP server。

## 工具

当前只暴露一个 MCP 工具：

- `agy`：调用 Antigravity CLI，并返回可继续会话的 `SESSION_ID`。

公开参数：

- `PROMPT`：发送给 `agy` 的任务内容。
- `cd`：运行 `agy` 的工作目录。
- `SESSION_ID`：继续指定 Antigravity conversation；为空时创建新会话。
- `sandbox`：本次调用是否给 `agy` 传入 `--sandbox`，默认 `false`。
- `return_all_messages`：是否返回 MCP 事件格式的原始输出，默认 `false`。
- `model`：指定模型；只有用户明确要求时才应该传。

## 安装

可执行 wrapper 由 chezmoi 生成到 `$HOME/.local/bin/agy-mcp`。

MCP 客户端配置：

```toml
[mcp_servers.agy-mcp]
command = "agy-mcp"
startup_timeout_sec = 60
tool_timeout_sec = 1800
```

wrapper 固定从 `$HOME/.agent/mcp/agy-mcp` 运行项目，并执行：

```bash
uv run --directory "$HOME/.agent/mcp/agy-mcp" --locked python -m agy_mcp.cli
```

`agy` 必须已经安装并完成认证。本项目不读取、不复制凭据文件。

## 环境变量

```bash
AGY_BIN=agy
AGY_MCP_TIMEOUT_SECONDS=1200
```

## 会话

首次调用：

```json
{
  "PROMPT": "Inspect this repo and summarize the build system.",
  "cd": "/path/to/repo"
}
```

返回：

```json
{
  "success": true,
  "SESSION_ID": "c39b574d-1ba0-43a0-91bf-73848219b04f",
  "agent_messages": "..."
}
```

继续会话：

```json
{
  "SESSION_ID": "c39b574d-1ba0-43a0-91bf-73848219b04f",
  "PROMPT": "Continue from the previous analysis.",
  "cd": "/path/to/repo"
}
```

`SESSION_ID` 是真实的 Antigravity conversation UUID，不是本地 MCP 自建 ID。继续会话时会传给 `agy --conversation=<SESSION_ID>`。

## Sandbox

`sandbox` 是每次 MCP tool call 的参数，不是 MCP server 启动配置。

```json
{
  "PROMPT": "Run tests in this repo.",
  "cd": "/path/to/repo",
  "sandbox": true
}
```

传入 `sandbox: true` 时，实际命令会包含 `--sandbox`。

## 实现说明

- MCP stdio 没有交互式 TTY 审批流程，所以调用 `agy` 时固定传入 `--dangerously-skip-permissions`。
- 新会话通过 `agy --print` 执行后产生的 Antigravity conversation state 发现真实 `SESSION_ID`。
- 续会话使用官方支持的 `--conversation=<SESSION_ID>`。
