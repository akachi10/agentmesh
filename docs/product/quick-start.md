# AgentMesh 快速上手

## 安装

```bash
cd /path/to/agentmesh
bash install.sh
```

安装完成后 `amesh` 命令可直接使用。

## 启动一个 Agent

```bash
amesh
```

按提示操作：
1. 输入名字（如 `架构师`、`产品经理`）
2. 输入自定义指令（如 `你是系统架构师，负责技术选型和架构设计`），也可以直接回车跳过
3. 进入 Claude Code 会话

## 启动多个 Agent 协作

打开多个终端窗口，每个窗口运行 `amesh` 并取不同的名字：

```
# 终端 1
amesh  →  名字: 架构师

# 终端 2
amesh  →  名字: 开发者

# 终端 3
amesh  →  名字: 测试
```

## Agent 之间通信

每个 Agent 的 Claude 会话内自动加载了 MCP 工具，AI 可以直接使用：

```
# 查看谁在线
list_agents()

# 发消息给另一个 Agent
send_message("架构师", "数据库用什么？")
```

你也可以直接告诉 AI：`帮我问一下架构师数据库用什么`，AI 会自动调用 MCP 工具。

## 收到消息时

Agent 之间的消息通过 tmux 直接注入到 Claude Code 输入框并自动提交，无需人工操作。

## 卸载

```bash
bash /path/to/agentmesh/uninstall.sh
```
