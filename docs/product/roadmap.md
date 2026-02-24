# AgentMesh Roadmap

## Current State (v0.1.0)

AgentMesh 当前是一个 macOS 专用的多 Claude Code 实例通信平台，通过 tmux + PTY + Unix Domain Socket + MCP 实现 agent 间协作。

**已实现能力**：
- Agent 注册/发现/通信（Unix Domain Socket）
- 同步请求-响应消息模型
- MCP 工具（list_agents / send_message）
- tmux 消息注入 + 系统提示词管理
- 基础安装脚本（macOS only）

**当前限制**：
- 仅支持 Claude Code 作为 agent runtime
- 配置全靠手动（启动时交互式输入名字和指令）
- 安装脚本仅支持 macOS，无环境自检测
- 单一系统平台

---

## Roadmap Overview

| 版本 | 主题 | 核心能力 |
|------|------|----------|
| v0.2 | Multi-Model | 支持多种 LLM CLI 作为 agent runtime |
| v0.3 | Shell Configurator | 提供 shell 配置工具，简化配置和管理 |
| v0.4 | Smart Installer | 环境自检测、依赖自动安装、安装向导 |
| v0.5 | Cross-Platform | 支持 Linux / Windows (WSL) |

> 版本可根据实际情况调整合并或重排优先级。

---

## v0.2 — Multi-Model Support

### 目标

让 AgentMesh 不仅能跑 Claude Code，还能接入其他 LLM CLI（如 Gemini CLI、Codex CLI、aider 等），实现**异构模型间协作**。

### 核心问题

当前 AgentMesh 深度绑定 Claude Code：
1. **启动方式**：`pty.fork()` 直接启动 `claude` 命令
2. **消息注入**：通过 tmux send-keys 注入到 Ink TUI，格式假定为 Claude Code 的输入框
3. **MCP 通信**：依赖 Claude Code 原生的 MCP stdio 支持
4. **系统提示词**：通过 `--append-system-prompt` 注入，是 Claude Code 专有参数
5. **压缩恢复**：依赖 Claude Code 的 SessionStart hook 机制

### 设计思路

#### Provider 抽象层

引入 **Provider** 概念，将 LLM CLI 的差异封装在 provider 中：

```
src/agentmesh/
├── providers/
│   ├── base.py           # Provider 抽象基类
│   ├── claude.py         # Claude Code provider（当前逻辑迁移）
│   ├── gemini.py         # Gemini CLI provider
│   └── ...
```

**Provider 需要定义的接口**：

| 接口 | 说明 |
|------|------|
| `get_launch_command()` | 返回启动 CLI 的命令和参数 |
| `inject_system_prompt()` | 如何注入系统提示词（各 CLI 方式不同） |
| `format_inject_message()` | 消息注入格式（可能各 CLI 输入方式不同） |
| `setup_mcp()` | MCP 配置方式（原生支持 / 需要 adapter） |
| `get_submit_keys()` | 提交消息的按键序列（Enter / Ctrl+Enter 等） |
| `supports_hooks()` | 是否支持压缩恢复 hook 等高级特性 |

#### MCP Adapter

对于不原生支持 MCP 的 LLM CLI，需要提供一个 adapter 层：
- **原生 MCP**：Claude Code → 直接使用
- **非原生 MCP**：通过 "MCP proxy" 将 MCP 工具调用转换为 CLI 能理解的格式（如 function calling / tool use 的文本注入）

> 这是最大的挑战。不支持 MCP 的 CLI 可能需要通过"提示词引导 + 特殊格式解析"来模拟 MCP 调用。

#### 启动时选择 Provider

```
$ amesh
Select provider:
  1. Claude Code (default)
  2. Gemini CLI
  3. Codex CLI
  4. Custom
Agent name: architect
```

### 需要调研的问题

- [ ] Gemini CLI / Codex CLI 等是否支持 MCP？
- [ ] 各 CLI 的系统提示词注入方式
- [ ] 各 CLI 的输入提交方式（tmux send-keys 是否通用）
- [ ] 不支持 MCP 的 CLI 如何实现 agent 间通信

### 风险

- 不同 LLM CLI 差异巨大，完全统一可能不现实
- MCP 非原生支持的 CLI 通信可靠性存疑
- 维护多 provider 的成本

---

## v0.3 — Shell Configurator

### 目标

提供一个交互式 shell 配置工具 `amesh config`，让用户可以方便地管理 AgentMesh 的配置，替代当前每次启动都要手动输入的方式。

### 功能设计

#### `amesh config` — 交互式配置管理

```bash
$ amesh config
AgentMesh Configuration
────────────────────────
1. Manage agent profiles     # 管理 agent 预设
2. Default provider          # 默认 LLM provider
3. Runtime settings          # 运行时设置
4. MCP settings              # MCP 相关配置
5. View current config       # 查看当前配置
6. Reset to defaults         # 恢复默认
```

#### Agent Profiles（预设）

用户可以预定义常用的 agent 配置，启动时一键选择：

```bash
$ amesh config profiles
Saved profiles:
  1. architect  — Claude Code, "你是系统架构师，负责技术选型和架构设计"
  2. developer  — Claude Code, "你是后端开发者，负责实现功能"
  3. reviewer   — Gemini CLI, "你是代码审查者"

[a]dd / [e]dit / [d]elete / [q]uit:
```

启动时可以直接选择 profile：

```bash
$ amesh --profile architect
# 或交互式选择
$ amesh
Select profile (or create new):
  1. architect
  2. developer
  3. reviewer
  4. [New agent]
```

#### 配置文件

```
~/agentmesh/
├── config.toml              # 全局配置
├── profiles/                # Agent profiles
│   ├── architect.toml
│   ├── developer.toml
│   └── reviewer.toml
```

**config.toml 示例**：
```toml
[default]
provider = "claude"

[runtime]
sock_dir = "~/agentmesh/sock"
registry_file = "~/agentmesh/registry.json"
prompt_reinject_interval = 20

[mcp]
auto_discover = true
```

**profile 示例**（architect.toml）：
```toml
name = "architect"
provider = "claude"
custom_prompt = "你是系统架构师，负责技术选型和架构设计"
```

#### 快速启动命令

```bash
amesh                          # 交互式（当前行为）
amesh --profile architect      # 使用预设 profile
amesh --name "dev" --prompt "你是开发者"  # 命令行参数直接启动
amesh config                   # 进入配置管理
amesh list                     # 查看当前活跃 agents（等同于 list_agents）
amesh status                   # 查看 AgentMesh 状态
```

### 实现要点

- 配置文件格式使用 TOML（Python 3.12 内置 `tomllib`）
- 交互式菜单使用 `questionary` 或 `InquirerPy` 库
- 向后兼容：没有配置文件时回退到当前交互式行为

---

## v0.4 — Smart Installer

### 目标

安装时自动检测环境、缺什么装什么，用户不需要手动折腾依赖。

### 当前安装痛点

- 手动检查 Python 版本，找不到就报错
- tmux 版本不够需要用户自己升级
- Claude CLI 没装只是警告，用户可能忽略
- 没有安装后验证
- 仅支持 macOS（brew）

### 设计方案

#### 环境检测器

```bash
$ bash install.sh

🔍 Checking environment...

  OS:       macOS 14.2 (arm64)              ✓
  Shell:    zsh 5.9                         ✓
  Python:   3.12.1 (/usr/bin/python3)       ✓
  tmux:     3.4 (/opt/homebrew/bin/tmux)    ✓
  Claude:   1.0.3 (/usr/local/bin/claude)   ✓
  Homebrew: 4.2.0                           ✓

All dependencies satisfied!
```

#### 自动安装能力

当检测到缺失依赖时，提供自动安装选项：

```bash
$ bash install.sh

🔍 Checking environment...

  OS:       macOS 14.2 (arm64)              ✓
  Shell:    zsh 5.9                         ✓
  Python:   3.11.4                          ✗ Need >= 3.12
  tmux:     not found                       ✗ Need >= 3.3
  Claude:   not found                       ✗ Required
  Homebrew: 4.2.0                           ✓

Fix automatically? [Y/n]
  → Installing Python 3.12 via Homebrew...
  → Installing tmux via Homebrew...
  → Claude Code must be installed manually:
    npm install -g @anthropic-ai/claude-code
```

#### 安装后验证

```bash
✅ Installation complete!

Running verification...
  amesh command:     ✓ found in PATH
  amesh-mcp command: ✓ found in PATH
  Python venv:       ✓ ~/agentmesh/venv/
  Socket directory:  ✓ ~/agentmesh/sock/

Try it: amesh
```

#### 包管理器检测

| 系统 | 包管理器 | 检测方式 |
|------|----------|----------|
| macOS | Homebrew | `which brew` |
| Ubuntu/Debian | apt | `which apt-get` |
| RHEL/CentOS | yum/dnf | `which yum` / `which dnf` |
| Arch | pacman | `which pacman` |

#### 升级支持

```bash
$ amesh upgrade              # 检查并升级到最新版本
$ amesh doctor               # 诊断环境问题
```

---

## v0.5 — Cross-Platform

### 目标

支持 Linux 和 Windows (WSL)，让 AgentMesh 不再局限于 macOS。

### 平台差异分析

| 组件 | macOS | Linux | Windows (WSL) |
|------|-------|-------|---------------|
| Unix Socket | ✓ | ✓ | ✓ (WSL 中) |
| tmux | brew install | apt/yum install | WSL 中 apt install |
| pty.fork() | ✓ | ✓ | WSL 中 ✓ |
| flock | fcntl.flock ✓ | fcntl.flock ✓ | WSL 中 ✓ |
| Python 3.12 | brew/官方 | apt/pyenv | WSL 中 apt |
| Claude CLI | npm install | npm install | WSL 中 npm install |
| PATH 配置 | .zshrc | .bashrc/.zshrc | .bashrc (WSL) |

### 核心工作

1. **安装脚本多平台适配**
   - 检测 OS 类型（`uname -s`）
   - 按系统选择包管理器
   - PATH 配置适配不同 shell 配置文件

2. **tmux 版本处理**
   - Linux 发行版自带 tmux 可能版本较低
   - 需要提供从源码编译 tmux 3.3+ 的选项
   - 或提供 tmux 替代方案（如 screen + 其他注入方式）

3. **Windows 策略**
   - 仅支持 WSL（不支持原生 Windows）
   - 检测 WSL 环境并按 Linux 流程处理
   - 提示用户需要在 WSL 中运行

4. **文件路径处理**
   - 统一使用 `pathlib.Path.home()` 而非硬编码 `~`
   - Socket 路径长度限制（Unix Domain Socket 路径有 108 字符限制，Linux 上更短）

5. **CI/CD 多平台测试**
   - GitHub Actions matrix: macOS + Ubuntu + WSL

### 风险

- Linux tmux 版本碎片化
- WSL 网络和文件系统性能差异
- 不同 Linux 发行版包管理器差异

---

## 优先级建议

**推荐顺序**：v0.4 (Smart Installer) → v0.5 (Cross-Platform) → v0.3 (Shell Configurator) → v0.2 (Multi-Model)

**理由**：
- **Smart Installer + Cross-Platform** 是降低用户门槛的基础，直接影响用户能否用起来
- **Shell Configurator** 是用户体验提升，当前交互式输入虽然简陋但能用
- **Multi-Model** 技术挑战最大（MCP 兼容性），但价值也最高，建议持续调研与其他版本并行

> 最终顺序由产品决策确定，以上仅为建议。
