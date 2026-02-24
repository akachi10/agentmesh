# AgentMesh

Agent 间通信枢纽 —— 让多个 Claude Code 实例通过 Unix Domain Socket 互相交流。

[English](README.md)

## 背景

Claude Code 的 subagent（Task tool）是"委托模式"：
- 人看不到中间过程
- agent 之间无法直接通信
- 无法控制 agent 生命周期

AgentMesh 解决这些问题，让每个 Claude Code 运行在独立 tmux 伪终端中：
- 人通过伪终端直接和 AI 交互（正常使用 Claude Code）
- AI 之间通过 Unix Domain Socket + MCP 通信
- 人可以随时切换终端、观察、干预任何 agent

## 架构概览

```
┌──────────────┐                        ┌──────────────┐
│  tmux pane A  │                        │  tmux pane B  │
│              │                        │              │
│ 人 ←stdin→ AI │                        │ 人 ←stdin→ AI │
│              │                        │              │
│ MCP Server   │                        │ MCP Server   │
│  ↓ write     │                        │  ↓ write     │
└──┬───────────┘                        └──┬───────────┘
   │                                       │
   │  ┌─────────────────────────────┐      │
   └──→       ~/agentmesh/          ←──────┘
      │                             │
      │  registry.json              │
      │  sock/                      │
      │    agent-<pid-1>.sock       │
      │    agent-<pid-2>.sock       │
      └─────────────────────────────┘
```

## 安装

前提条件：Python >= 3.12、tmux >= 3.3、[Claude Code](https://docs.anthropic.com/en/docs/claude-code)

```bash
git clone https://github.com/akachi10/agentmesh.git
cd agentmesh
bash install.sh
```

## 使用

```bash
# 启动一个 agent
amesh

# 在另一个终端启动第二个 agent
amesh
```

按提示输入名字（如"架构师"、"开发者"）和自定义指令，即可进入 Claude Code 会话。

AI 会自动通过 MCP 工具 `list_agents()` 和 `send_message()` 与其他 agent 通信。

## 卸载

```bash
bash uninstall.sh
```

## 文档

- [快速上手](docs/product/quick-start.md) | [Quick Start](docs/product/quick-start-en.md)
- [产品需求文档](docs/product/PRD.md)

## 技术栈

| 项 | 选择 |
|----|------|
| 语言 | Python 3.12+ |
| IPC | Unix Domain Socket |
| 终端托管 | tmux >= 3.3 |
| AI 工具协议 | MCP (stdio) |
| 注册表 | JSON + flock |

## License

MIT
