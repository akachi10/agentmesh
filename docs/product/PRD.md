# AgentMesh 需求文档

## 一、概述

提供多个 Claude Code 实例之间的通信能力，让 AI agent 能够互相发现、互相交流，同时人类保持对每个 agent 的直接控制。

通过安装脚本一键部署到用户目录 `~/agentmesh/`，包含自包含 Python 虚拟环境、可执行入口和运行时数据，无需 sudo 权限。

### 关键技术决策：tmux + PTY 混合方案

Claude Code 使用 Ink（React for terminals）构建 TUI，其输入处理机制要求**真实的键盘事件**才能触发消息提交。通过 PTY `os.write(master_fd)` 写入的 `\r`/`\n` 不会被 Ink 识别为"提交"操作，导致注入的消息停留在输入框中，必须人类手动按回车。

**解决方案**：客户端自身运行在 tmux session 中，通过 `pty.fork()` 启动 Claude Code 作为子进程。消息注入通过 `tmux send-keys` 命令注入到客户端所在的 tmux pane，tmux 生成的键盘事件能被 Ink 正确识别为真实按键。

**进程关系**：
```
tmux session
  └── 客户端进程（amesh）    ← 人类在这里交互
       ├── Claude Code（子进程，通过 pty.fork）
       ├── 后台线程：sock 监听
       └── MCP Server（由 Claude Code 启动的子进程）
```

- 客户端退出 → Claude Code 子进程收到信号退出 → 干净清理
- 人类 `Ctrl+B D` detach → 客户端不受影响继续运行 → 人类 `tmux attach` 回来
- 人类 `tmux kill-session` → 客户端收到信号 → 执行退出流程

**人类体验不变**：
- 人类在 tmux 窗口中直接与 Claude Code 交互，和正常使用完全一样
- 随时 `Ctrl+B D` detach，切到其他 agent 窗口观察
- `tmux attach` 回来继续

## 二、核心组件

### 2.1 客户端（PTY + tmux 注入）

**职责**：在 tmux session 中运行，通过 `pty.fork()` 启动 Claude Code 作为子进程，管理 I/O 转发，并通过 tmux 命令注入 agent 间消息。

**前置依赖**：tmux >= 3.3（需要 `paste-buffer -p` 的 bracketed paste 支持）

**运行前提**：客户端必须在 tmux session 内运行。启动时检测环境变量 `$TMUX_PANE`，若不存在则自动创建 tmux session 并在其中重新启动自身。

**启动流程**：
1. 检测是否在 tmux 中运行（检查 `$TMUX_PANE`）
   - 不在 tmux 中 → 创建 tmux session `amesh-{pid}`，在其中重新执行自身，然后 attach
   - 已在 tmux 中 → 继续
2. 记录 `$TMUX_PANE` 作为 tmux 注入目标
3. 提示输入 agent 名称（如"架构师"、"开发者"）
4. 提示输入自定义指令（如"你是系统架构师，负责技术选型"），可为空
5. 分配进程 ID 作为唯一标识
6. 创建该 agent 的 Unix Domain Socket 文件
7. 向 registry 注册自身信息（含 tmux pane 标识）
8. 生成前置提示词（见下方"前置提示词"），将自定义指令合并进去
9. 通过 `pty.fork()` 启动 Claude Code 作为子进程（配置好 MCP Server + 前置提示词 + 压缩恢复 hook）
10. 启动后台线程监听自身 sock，接收其他 agent 的消息
11. 主线程进入 PTY I/O 循环：转发 stdin ↔ master_fd（人类直接与 Claude Code 交互）

**消息注入方式**：

通过 tmux 命令注入消息到客户端所在 pane 的 Claude Code 输入框并自动提交：

```python
import subprocess, time, threading, uuid

# 全局锁，防止多个消息并发注入时 tmux buffer 互相覆盖
_inject_lock = threading.Lock()

def inject_message(tmux_pane: str, text: str):
    buf_name = f"agent-msg-{uuid.uuid4().hex[:8]}"
    with _inject_lock:
        # 1. 将消息文本载入 tmux paste buffer（唯一名称避免覆盖）
        subprocess.run(["tmux", "set-buffer", "-b", buf_name, "--", text])
        # 2. 使用 bracketed paste 模式粘贴（-p 标志）
        subprocess.run(["tmux", "paste-buffer", "-p", "-b", buf_name, "-t", tmux_pane])
        # 3. 等待文本被 Ink 接收
        time.sleep(0.3)
        # 4. 发送 Enter 键触发提交
        subprocess.run(["tmux", "send-keys", "-t", tmux_pane, "Enter"])
        # 5. 清理 buffer
        subprocess.run(["tmux", "delete-buffer", "-b", buf_name])
```

**为什么这样能工作**：
- `paste-buffer -p` 会在文本前后包裹 bracketed paste 序列（`ESC[200~` ... `ESC[201~`），Ink 将其识别为粘贴内容并插入输入框
- `send-keys Enter` 生成真实的键盘事件，Ink 的 `onSubmit` 能正确触发

**消息注入格式**：

注入到 Claude Code 输入框的文本格式，包含发送方名字、PID 和 msg_id（大模型需要 msg_id 来回复）：
```
[Agent Message] from 架构师 (pid=12345, msg_id=550e8400-e29b-41d4-a716-446655440000): 消息内容
```

**前置提示词（`--append-system-prompt`）**：

启动 Claude Code 时通过 `--append-system-prompt` 追加系统提示，内容包括：
- 你的名字是 `{agent_name}`，进程 ID 是 `{agent_id}`
- 你是 AgentMesh 中的一个节点，通过 MCP 工具与其他 agent 协作
- 使用 `amesh` MCP Server 提供的 `list_agents`、`send_message` 工具
- 协作原则：需要协作时，**先调用 `list_agents()` 查看是否有可用的 agent**，找到目标后通过 `send_message()` 联系，**禁止使用 Task tool 拉起 subagent**
- 如果 `list_agents()` 没有找到需要的 agent，告知人类当前无可用协作者，由人类决定是否启动新 agent
- **回复规则**：当你收到 `[Agent Message]` 格式的消息时，其中包含 `msg_id`。回复时必须调用 `send_message(to_name, content, response_id=msg_id)`，将对方的 msg_id 作为 response_id 传入。不传 response_id 会导致对方 MCP 一直阻塞等待回复。
- **长任务处理**：如果任务工作量大，先快速回复确认（如"收到，我去处理"），完成后再通过新的 `send_message`（不传 response_id）主动通知对方结果。
- 用户自定义指令：`{user_custom_prompt}`（人类在启动时输入的角色描述，如"你是系统架构师"）

**压缩恢复：`SessionStart` hook（compact matcher）**

上下文压缩时 `--append-system-prompt` 的内容可能丢失。通过配置 hook，压缩后自动重新注入相同内容：
```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "compact",
        "hooks": [
          {
            "type": "command",
            "command": "echo '[上下文压缩恢复] 以下是你的身份和规则的重复注入，无需回复确认。\n{完整的提示词内容，与 --append-system-prompt 相同，含用户自定义指令}'"
          }
        ]
      }
    ]
  }
}
```

> 启动时由客户端动态生成 hook 配置文件（含实际的 agent 名称和 PID），通过 `--mcp-config` 同目录放置或 `settings.json` 写入。

**提示词定期重注入**：

客户端维护一个消息注入计数器（每次通过 tmux 注入 agent 消息时计 1 次）。

每累计 20 条注入消息后，客户端自动通过 tmux send-keys 注入完整的系统提示词（与 `--append-system-prompt` 内容相同），防止长对话中提示词被稀释或遗忘。注入内容以 `[提示词重注入]` 开头，提醒 AI 无需回复确认。

> 注意：人类手动输入的回车无法被计数（因为客户端不拦截 stdin），所以仅计 agent 消息注入次数。

**退出流程**：

客户端退出时（Claude Code 子进程结束、人类 Ctrl+C、tmux kill-session）：
1. 等待 Claude Code 子进程结束（如果还在运行）
2. 停止 sock 监听线程
3. 从 registry 中移除自身
4. 清理 sock 文件

### 2.2 Registry（注册表）

**文件路径**：`~/agentmesh/registry.json`

**职责**：记录当前所有活跃 agent 的信息。

**格式**：
```json
{
  "agents": [
    {
      "name": "架构师",
      "id": "12345",
      "sock": "~/agentmesh/sock/agent-12345.sock",
      "tmux_pane": "%5",
      "registered_at": "2026-02-22T10:00:00Z"
    },
    {
      "name": "开发者",
      "id": "12346",
      "sock": "~/agentmesh/sock/agent-12346.sock",
      "tmux_pane": "%8",
      "registered_at": "2026-02-22T10:01:00Z"
    }
  ]
}
```

**名称唯一性**：注册时检查是否已有同名 agent。若重名，提示用户当前已存在同名 agent，要求重新输入名称。

**并发控制**：使用文件锁（flock）保证多进程安全读写。

### 2.3 Unix Domain Socket（消息通道）

**文件路径**：`~/agentmesh/sock/agent-<id>.sock`

**职责**：每个 agent 拥有一个 sock，作为该 agent 的"收件箱"。其他 agent 向此 sock 写入消息（新消息或回复）。

**两类连接**：

sock 上存在两类连接，SocketServer 统一管理：

| 连接类型 | 发起方 | 生命周期 | 说明 |
|---------|--------|---------|------|
| 写入连接 | 其他 agent 的 MCP | 短连接，写完即关 | 发送消息（新消息或回复）到本 agent 的收件箱 |
| 订阅连接 | 本 agent 的 MCP | 长连接，等到回复才关 | MCP 发完新消息后连上自己的 sock，等待 response_id 匹配的回复 |

**消息分发流程**：SocketServer 收到一条消息后：
1. 检查是否有订阅者在等待匹配的 `response_id` → 有则转发给该订阅者
2. 若无匹配的订阅者且 `response_id` 为 null → 这是新消息 → 调用 tmux 注入
3. 若无匹配的订阅者但 `response_id` 有值 → 回复到达但 MCP 已断开 → 丢弃（记录日志）

**订阅协议**：MCP 连接自己的 sock 后，先发送一个订阅请求（包含等待的 msg_id），SocketServer 将其加入订阅者列表。当匹配的回复到达时，SocketServer 通过该连接将回复转发给 MCP，然后 MCP 关闭连接。

**传输协议**：使用长度前缀数据包模式（避免 JSON 截断问题）。

```
┌──────────────┬──────────────────────┐
│ 4 字节大端序  │  JSON 负载 (UTF-8)    │
│ 负载长度      │                      │
└──────────────┴──────────────────────┘
```

- 发送方：先写 4 字节表示后续 JSON 的字节长度，再写 JSON 数据
- 接收方：先读 4 字节得到长度 N，再精确读取 N 字节，解析为 JSON
- 避免了"读到 EOF"模式下的截断和粘包问题

**消息格式**（JSON 负载部分）：
```json
{
  "msg_id": "550e8400-e29b-41d4-a716-446655440000",
  "response_id": null,
  "from": "架构师",
  "from_id": "12345",
  "to": "开发者",
  "to_id": "12346",
  "timestamp": "2026-02-22T10:05:00Z",
  "content": "数据库用 PostgreSQL，Schema 在 docs/database/schema.md"
}
```

**字段说明**：

| 字段 | 必填 | 说明 |
|------|:----:|------|
| `msg_id` | 是 | 本条消息的 UUID，由 MCP 在发送时自动生成 |
| `response_id` | 否 | 回复目标的 msg_id。为 null 表示新消息，有值表示回复 |
| `from` / `from_id` | 是 | 发送方名字和 PID |
| `to` / `to_id` | 是 | 接收方名字和 PID |
| `timestamp` | 是 | 发送时间 |
| `content` | 是 | 消息内容 |

### 2.4 MCP Server

**职责**：作为 Claude Code 的 MCP 工具，让大模型能够发现其他 agent 并发送/接收消息。

**启动方式**：客户端通过 MCP config 指定 `amesh-mcp` 命令启动 MCP Server。该命令由 `pyproject.toml` 定义为独立入口（`[project.scripts]`），安装后可直接调用，无需依赖特定 Python 解释器路径。客户端启动 MCP Server 时通过命令行参数传入自身的 agent 名称和 ID（如 `amesh-mcp --name 架构师 --id 12345`），MCP Server 据此填充发送方信息。

**同步通讯模型**：

`send_message` 是**同步阻塞**的。大模型调用 `send_message()` 后，MCP 会一直等待对方回复才返回。这使得 agent 间的对话变成真正的请求-响应模式，大模型可以直接拿到对方的回答。

**核心时序**：

```
A 的大模型问 B：
  A 的 MCP send_message("B", "数据库用什么？")
    → MCP 内部生成 msg_id=uuid1
    → 写入 B 客户端的 sock（response_id=null，这是新消息）
    → MCP 连接 A 客户端自己的 sock，监听等待 response_id=uuid1 的回复
    → MCP 阻塞...

B 客户端收到消息：
  → response_id=null → 新消息 → tmux 注入给 agent
  → 注入文本包含 msg_id：[Agent Message] from A (pid=xxx, msg_id=uuid1): 数据库用什么？

B 的大模型处理后回复：
  B 的 MCP send_message("A", "用 PostgreSQL", response_id="uuid1")
    → MCP 内部生成 msg_id=uuid2
    → 写入 A 客户端的 sock（response_id=uuid1，这是回复）
    → 有 response_id → 立即返回"已发送"，不等回复

A 客户端的 sock 收到消息：
  → response_id=uuid1 → 这是回复 → 客户端忽略（不注入 tmux）
  → A 的 MCP 在 sock 上监听到 response_id=uuid1 匹配 → 返回内容给大模型
  → send_message() 调用结束，A 的大模型拿到回复继续工作
```

**MCP 同时监听自己的 sock**：MCP 发送新消息后，需要连接自己客户端的 sock 监听回复。MCP 和客户端都连接同一个 sock，各自按 `response_id` 过滤：
- **客户端**：只处理 `response_id=null` 的新消息 → tmux 注入
- **MCP**：只处理 `response_id` 匹配自己等待的 msg_id 的回复

**MCP 阻塞期间的活性检测**：

MCP 在等待回复期间，定期轮询检查目标 agent 的存活状态：
- 检查目标 PID 是否存在且为客户端程序
- 检查目标名字是否与发送时一致（防止 PID 被其他进程复用的极小概率情况）
- 目标死亡或名字变化 → 返回错误"agent [name] 断链，目标已下线"
- 目标存活 → 继续等待，**不设超时**（大模型思考时间不可预测）

**人类干预**：MCP 阻塞期间，大模型无法接收新输入（agent 在等 tool call 返回）。人类可以通过 `Ctrl+C` 中断当前操作，恢复 agent 到可交互状态。

**提供的 Tools**：

#### `list_agents()`
- 读取 registry.json
- 对每个 agent 检查其 PID 是否存活（进程存在且为客户端程序）
- PID 不存在或不是客户端程序 → 判定为死亡，清理注册信息并删除对应 sock 文件
- 返回当前可用 agent 列表（名称 + ID）

#### `send_message(to_name: string, content: string, response_id: string = null)`
- **参数**：
  - `to_name`：目标 agent 名称
  - `content`：消息内容
  - `response_id`：可选，回复目标的 msg_id。为空表示发新消息，有值表示回复
- **发新消息**（response_id 为空）：
  1. 在 registry 中查找目标 agent，找不到 → 返回错误
  2. MCP 内部生成 msg_id（UUID）
  3. 写入目标 agent 的 sock
  4. 连接自己客户端的 sock，阻塞监听 response_id 匹配的回复
  5. 等待期间定期检查目标存活状态
  6. 收到回复 → 返回回复内容给大模型
  7. 目标死亡 → 返回错误"agent [name] 断链"
- **发回复**（response_id 有值）：
  1. 在 registry 中查找目标 agent，找不到 → 返回错误
  2. MCP 内部生成 msg_id（UUID）
  3. 写入目标 agent 的 sock（携带 response_id）
  4. **立即返回**"已发送"，不等回复

**长任务处理策略**（通过提示词引导大模型行为）：

如果被问的大模型判断任务工作量大，可以：
1. 先快速回复确认（如"收到，我去做了"）→ 释放对方的阻塞
2. 完成后通过新的 `send_message`（无 response_id）主动通知对方结果

## 三、交互模型

### 3.1 人 ↔ AI

```
人类 → tmux 窗口 → 客户端（PTY I/O 循环）→ Claude Code
Claude Code → master_fd → 客户端 → stdout → 人类
```

- 人在 tmux 窗口中直接和 Claude Code 交互，和正常使用完全一样
- 客户端的 PTY I/O 循环透明转发 stdin/stdout，人类感知不到中间层
- 人可以同时开多个 tmux 窗口，每个窗口运行一个 amesh
- `Ctrl+B D` detach 后客户端和 Claude Code 继续后台运行
- `tmux attach` 回来继续

### 3.2 消息到达时的处理

客户端和 MCP 都连接自己的 sock 监听消息，按 `response_id` 字段分流：

| 消息类型 | 判断条件 | 处理方 | 处理方式 |
|---------|---------|--------|---------|
| 新消息 | `response_id` 为 null | 客户端 | tmux 注入给 agent |
| 回复 | `response_id` 有值 | MCP | MCP 匹配等待中的 msg_id，返回给大模型 |

**新消息的注入行为**：消息到达后**直接注入**，不做人机协调。

**原因**：tmux 方案下客户端不拦截 stdin/stdout，无法感知人类是否正在打字。消息直接注入是最简单可靠的方式。如果人类正在输入，tmux paste 会追加到当前输入内容之后——这可能会干扰人类输入，但这是 tmux 方案下的已知取舍。

### 3.3 AI ↔ AI 交互模型

#### AI 间消息传递（同步请求-响应）

```
A 的大模型发起对话：
  大模型A → MCP send_message("B", "数据库用什么？")
    → MCP 生成 msg_id, 写入 B 的 sock
    → MCP 连接 A 的 sock, 阻塞等 response_id 匹配的回复
    → (同时定期检查 B 是否存活)

B 收到并回复：
  B 客户端从 sock 收到消息 (response_id=null → 新消息)
    → tmux 注入: [Agent Message] from A (pid=xxx, msg_id=uuid1): 数据库用什么？
  大模型B 处理后 → MCP send_message("A", "用 PostgreSQL", response_id="uuid1")
    → MCP 写入 A 的 sock → 立即返回 (是回复，不等)

A 收到回复：
  A 客户端从 sock 收到消息 (response_id=uuid1 → 回复 → 忽略)
  A 的 MCP 从 sock 收到消息 (response_id=uuid1 匹配 → 返回给大模型)
  → send_message() 返回, A 的大模型拿到回复继续工作
```

#### 大模型需要协作时

```
大模型 (根据提示词规则)
  → 调用 MCP list_agents()
    → 找到目标 agent → 调用 MCP send_message() 发送消息 (同步等回复)
    → 没找到目标 agent → 告知人类当前无可用协作者，不拉起 subagent
```

> **关键规则**：大模型在需要协作时，必须先通过 `list_agents()` 查找已有 agent，通过 `send_message()` 联系，**禁止使用 Task tool 拉起 subagent**。

### 3.4 人的控制能力
- 在 tmux 窗口中直接打字与 Claude Code 交互
- `Ctrl+B D` detach 后 agent 继续后台运行
- `tmux attach` 回来继续
- 关闭 agent：在终端中正常退出 Claude Code，或 `Ctrl+C` 终止客户端，或 `tmux kill-session`
- 客户端退出后自动注销（清理 registry + sock）

## 四、健壮性要求

| 场景 | 处理方式 |
|------|----------|
| agent 进程崩溃未注销 | 调用 list_agents/send_message 时检查目标 PID 是否存活（进程存在且为客户端程序），不存活则清理注册信息**并删除对应 sock 文件** |
| registry 并发写入 | 使用 flock 文件锁 |
| sock 文件残留 | 启动时检查 ~/agentmesh/sock/ 清理无主 sock |
| 消息发送目标不存在 | 返回明确错误信息给大模型 |
| MCP 等待回复时目标死亡 | MCP 定期轮询目标 PID+名字，死亡或名字变化时返回"断链"错误 |
| MCP 阻塞期间人类需要干预 | 人类通过 `Ctrl+C` 中断 agent 当前操作，MCP 调用被终止 |
| PID 被其他进程复用（极小概率） | 同时检查 PID 存活 + 名字匹配，名字不一致视为断链 |
| 注入时 agent 正在思考/执行工具 | Claude Code 输入框可能不可用，注入的文本可能堆积在终端缓冲区。已知风险，需实际测试验证行为 |
| 并发注入竞争 | tmux 注入操作加线程锁 + 唯一 buffer 名，保证同一时刻只有一条消息在注入 |
| 大模型未回复导致 MCP 永久阻塞 | 仅在目标死亡时返回断链。大模型忘记回复（BUG）导致的阻塞，由人类 `Ctrl+C` 干预解决 |

## 五、技术选型

| 项 | 选择 | 理由 |
|----|------|------|
| 语言 | Python | socket.AF_UNIX 原生支持、MCP SDK 官方 Python 版、subprocess 调用 tmux |
| IPC | Unix Domain Socket | 本机通信，无需网络栈，快 |
| 注册表 | JSON 文件 + fcntl.flock | 简单，够用 |
| 消息格式 | 长度前缀 + JSON | 避免截断，Claude Code 友好 |
| MCP 协议 | stdio 模式 | Claude Code 原生支持 |
| 终端托管 | tmux >= 3.3 | Claude Code 的 Ink TUI 需要真实键盘事件才能提交输入，tmux send-keys 能生成真实按键事件；bracketed paste 需要 tmux >= 3.3 |

### 运行时数据目录

所有运行时数据存放在用户目录 `~/agentmesh/`（无需 sudo 权限）：

```
~/agentmesh/
├── registry.json              # 注册表
└── sock/                      # Socket 文件目录
    ├── agent-<pid-1>.sock     # Agent A 的消息通道
    ├── agent-<pid-2>.sock     # Agent B 的消息通道
    └── ...
```

> 路径常量统一在 `registry.py` 中定义（`REGISTRY_DIR`、`REGISTRY_FILE`、`SOCK_DIR`），其他模块从 registry 导入，不重复定义。首次启动时自动创建该目录。

## 六、文件结构（预期）

```
agentmesh/
├── README.md
├── pyproject.toml
├── install.sh
├── uninstall.sh
├── human/                     # 人类需求（只读）
├── docs/
│   └── product/
│       ├── PRD.md             # 本文件
│       └── quick-start.md
├── src/
│   └── agentmesh/
│       ├── __init__.py
│       ├── main.py            # 入口（amesh 命令）
│       ├── pty_launcher.py    # PTY 管理：pty.fork() 启动 Claude Code
│       ├── pty_io.py          # PTY I/O 循环：stdin/stdout 转发
│       ├── tmux_injector.py   # tmux 消息注入
│       ├── registry.py        # 注册表管理
│       ├── socket_server.py   # Socket 监听
│       ├── socket_client.py   # Socket 发送
│       └── mcp_server.py      # MCP Server（amesh-mcp 命令）
└── tests/
```

## 七、未来扩展（不在当前范围）

- Web UI 查看所有 agent 状态和消息历史
- 消息持久化（当前消息是临时的）
- agent 分组/房间
- 消息广播（群发）

## 八、安装与部署

### 设计目标

- **无需 sudo**：所有文件安装在用户目录 `~/agentmesh/`
- **自包含**：独立 venv，不污染系统 Python 环境
- **两个入口命令**：`amesh`（用户启动 agent）和 `amesh-mcp`（Claude Code 自动启动 MCP Server）

### 入口命令

| 命令 | 定义于 | 调用者 | 说明 |
|------|--------|--------|------|
| `amesh` | `pyproject.toml` → `agentmesh.main:main` | 用户 | 启动一个 AgentMesh 实例 |
| `amesh-mcp` | `pyproject.toml` → `agentmesh.mcp_server:main` | Claude Code（通过 MCP config） | 启动 MCP Server 子进程 |

> 客户端在 MCP config 中指定 `"command": "amesh-mcp"`，确保安装后命令可独立运行。

### 安装目录结构

```
~/agentmesh/
├── venv/                      # 自包含 Python 虚拟环境
├── bin/                       # 可执行入口脚本（wrapper → venv/bin/）
│   ├── amesh                   # 主入口
│   └── amesh-mcp               # MCP server 入口
├── registry.json              # 运行时注册表（自动生成）
└── sock/                      # 运行时 Socket 文件目录（自动生成）
```

### 安装方式

从源码目录运行安装脚本：

```bash
git clone <repo-url> && cd agentmesh
bash install.sh
```

安装脚本会：
1. 检查前提条件（Python >= 3.12、claude CLI、tmux >= 3.3）
2. 创建 `~/agentmesh/` 目录结构
3. 创建 Python venv 并从源码 `pip install .`
4. 生成 `~/agentmesh/bin/` 下的 wrapper 脚本（转发到 venv 中的实际入口）
5. 尝试创建 `/usr/local/bin/` 符号链接（失败则提示用户手动添加 PATH）

### 卸载

```bash
bash uninstall.sh
```

卸载脚本会：
1. 删除 `/usr/local/bin/amesh`、`/usr/local/bin/amesh-mcp` 符号链接
2. 删除 `~/agentmesh/` 整个目录（含 venv、运行时数据）

### 依赖前提

| 依赖 | 最低版本 | 说明 |
|------|----------|------|
| Python | >= 3.12 | 运行时 |
| claude CLI | - | Claude Code 命令行工具 |
| tmux | >= 3.3 | 终端托管，`paste-buffer -p`（bracketed paste）需要 3.3+ |
