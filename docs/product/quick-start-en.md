# AgentMesh Quick Start

## Install

```bash
cd /path/to/agentmesh
bash install.sh
```

The `amesh` command is available after installation.

## Start an Agent

```bash
amesh
```

Follow the prompts:
1. Enter a name (e.g. `architect`, `product-manager`)
2. Enter custom instructions (e.g. `You are a system architect responsible for tech decisions`), or press Enter to skip
3. Enter the Claude Code session

## Multi-Agent Collaboration

Open multiple terminal windows and run `amesh` in each with different names:

```
# Terminal 1
amesh  →  Name: architect

# Terminal 2
amesh  →  Name: developer

# Terminal 3
amesh  →  Name: tester
```

## Communication Between Agents

Each agent's Claude session comes with MCP tools pre-loaded. AI can use them directly:

```
# See who's online
list_agents()

# Send a message to another agent
send_message("architect", "What database should we use?")
```

You can also tell the AI naturally: `Ask the architect what database to use` — it will call the MCP tools automatically.

## Receiving Messages

Messages between agents are injected directly into Claude Code's input via tmux and submitted automatically — no manual action needed.

## Uninstall

```bash
bash /path/to/agentmesh/uninstall.sh
```
