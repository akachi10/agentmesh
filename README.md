# AgentMesh

Communication hub for agents — multiple Claude Code instances collaborate via Unix Domain Sockets.

[中文文档](README-cn.md)

## Why

Claude Code's subagent (Task tool) is a "delegation model":
- Humans can't see intermediate steps
- Agents can't communicate directly with each other
- No control over agent lifecycle

AgentMesh solves these problems by running each Claude Code in its own tmux pseudo-terminal:
- Humans interact with AI directly through the terminal (normal Claude Code usage)
- AIs communicate with each other via Unix Domain Socket + MCP
- Humans can switch terminals at any time to observe or intervene with any agent

## Architecture

```
┌──────────────┐                        ┌──────────────┐
│  tmux pane A  │                        │  tmux pane B  │
│              │                        │              │
│ Human ↔ AI   │                        │ Human ↔ AI   │
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

## Requirements

- **Platform**: macOS (Linux support planned)
- **AI**: [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
- **Runtime**: Python >= 3.12, tmux >= 3.3

## Install

```bash
git clone https://github.com/akachi10/agentmesh.git
cd agentmesh
bash install.sh
```

## Usage

```bash
# Start an agent
amesh

# Open another terminal and start a second agent
amesh
```

Enter a name (e.g. "architect", "developer") and optional custom instructions, then you're in a Claude Code session.

AI automatically communicates with other agents via MCP tools `list_agents()` and `send_message()`.

## Uninstall

```bash
bash uninstall.sh
```

## Docs

- [Quick Start](docs/product/quick-start.md) | [快速上手](docs/product/quick-start-cn.md)
- [PRD (中文)](docs/product/PRD.md)

## Tech Stack

| Component | Choice |
|-----------|--------|
| Language | Python 3.12+ |
| IPC | Unix Domain Socket |
| Terminal | tmux >= 3.3 |
| AI Tool Protocol | MCP (stdio) |
| Registry | JSON + flock |

## License

MIT
