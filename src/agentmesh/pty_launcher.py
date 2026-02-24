"""PTY launcher — start Claude Code in a pseudo-terminal with MCP integration.

Architecture: The PTY wrapper is responsible for:
- Registry registration / unregistration (with tmux_pane)
- Creating and managing the SocketServer (wired to tmux injection)
- Building the system prompt and MCP config
- Generating the compact hook config for system prompt reinjection
- Spawning Claude Code in a PTY with manual I/O
"""

from __future__ import annotations

import json
import logging
import os
import pty
import shlex
import shutil
import tempfile
from pathlib import Path

from agentmesh import registry
from agentmesh.registry import SOCK_DIR
from agentmesh.socket_server import SocketServer
from agentmesh.tmux_injector import inject_agent_message

logger = logging.getLogger(__name__)


def _build_system_prompt(agent_name: str, agent_id: str,
                         custom_prompt: str | None = None) -> str:
    """Build the system prompt injected via --append-system-prompt."""
    lines = [
        f"你是 Agent '{agent_name}'（进程 PID={agent_id}），运行在 AgentMesh 中。",
        "",
        "## MCP 工具",
        "你可以使用 'amesh' MCP server 提供的以下工具：",
        "- list_agents()：列出所有可用的 agent，检查存活状态。",
        "- send_message(to_name, content, response_id=None)：向另一个 agent 发送消息。",
        "",
        "## 规则",
        "- 不要使用 Task tool 来拉起子 agent。你已经是一个独立的 agent。",
        "- 在需要协作时，先调用 list_agents() 查看是否有可用的 agent，",
        "  然后直接通过 send_message() 联系它们。",
        "- 如果 list_agents() 没有找到需要的 agent，告知人类当前无可用协作者，由人类决定。",
        "",
        "## 消息回复规则",
        "- 当你收到 [Agent Message] 格式的消息时，其中包含 msg_id。",
        "- 回复时必须调用 send_message(to_name, content, response_id=msg_id)，",
        "  将对方的 msg_id 作为 response_id 传入。",
        "- 不传 response_id 会导致对方 MCP 一直阻塞等待回复。",
        "",
        "## 长任务处理",
        '- 如果任务工作量大，先快速回复确认（如"收到，我去处理"），释放对方阻塞。',
        "- 完成后再通过新的 send_message（不传 response_id）主动通知对方结果。",
    ]
    if custom_prompt:
        lines.extend(["", "## 自定义指令", custom_prompt])
    return "\n".join(lines)


def _build_hook_config(system_prompt: str) -> dict:
    """Build the hook config for compact reinjection.

    When Claude Code compresses context (SessionStart with compact matcher),
    the hook re-echoes the full system prompt so the agent doesn't lose
    its identity and rules.
    """
    # Use printf with shlex.quote to avoid shell injection
    full_text = (
        f"[上下文压缩恢复] 以下是你的身份和规则的重复注入，"
        f"无需回复确认。\n{system_prompt}"
    )
    echo_cmd = f"printf '%s' {shlex.quote(full_text)}"
    return {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "compact",
                    "hooks": [
                        {
                            "type": "command",
                            "command": echo_cmd,
                        }
                    ],
                }
            ]
        }
    }


def launch(agent_name: str, agent_id: str,
           custom_prompt: str | None = None) -> None:
    """Launch Claude Code in a PTY with the AgentMesh MCP server configured.

    Must be called from within a tmux session (TMUX_PANE must be set).
    """
    from agentmesh.pty_io import run_pty_loop

    tmux_pane = os.environ.get("TMUX_PANE", "")
    if not tmux_pane:
        raise RuntimeError(
            "TMUX_PANE not set — launch() must be called from within a tmux session."
        )

    sock_path = str(SOCK_DIR / f"agent-{agent_id}.sock")

    # Resolve the MCP server command
    installed_mcp = registry.REGISTRY_DIR / "bin" / "amesh-mcp"
    if installed_mcp.exists():
        mcp_command = str(installed_mcp)
    else:
        import sys
        mcp_command = str(Path(sys.executable).parent / "amesh-mcp")

    # Build system prompt
    system_prompt = _build_system_prompt(agent_name, agent_id, custom_prompt)

    # MCP config
    mcp_config = {
        "mcpServers": {
            "amesh": {
                "command": mcp_command,
                "args": [
                    "--agent-id", agent_id,
                    "--agent-name", agent_name,
                ],
            }
        }
    }

    # SocketServer will be created in setup_fn
    socket_server: SocketServer | None = None
    config_dir: Path | None = None

    def on_new_message(msg: dict) -> None:
        """Callback when a new message arrives — inject via tmux."""
        inject_agent_message(tmux_pane, msg, system_prompt=system_prompt)

    def setup_fn() -> None:
        nonlocal socket_server
        socket_server = SocketServer(sock_path, on_new_message=on_new_message)
        socket_server.start()

    try:
        # Create temp config directory for MCP + hooks
        config_dir = Path(tempfile.mkdtemp(prefix="amesh-"))
        config_file = config_dir / "mcp.json"
        config_file.write_text(json.dumps(mcp_config, indent=2))

        # Hook config for compact reinjection
        hook_config = _build_hook_config(system_prompt)
        hook_file = config_dir / "settings.json"
        hook_file.write_text(json.dumps(hook_config, indent=2))

        # Register in registry (with tmux_pane)
        registry.register(agent_name, agent_id, sock_path, tmux_pane=tmux_pane)
        cmd = [
            "claude",
            "--mcp-config", str(config_file),
            "--append-system-prompt", system_prompt,
        ]

        print(f"\n  Agent: {agent_name} (id={agent_id})")
        print(f"  tmux pane: {tmux_pane}")
        print(f"  MCP config: {config_file}")
        print(f"  Starting Claude Code...\n")

        pid, master_fd = pty.fork()

        if pid == 0:
            # Child process — exec Claude Code
            os.execvp(cmd[0], cmd)
        else:
            # Parent process — run I/O loop
            run_pty_loop(master_fd, pid, setup_fn=setup_fn)

    except FileNotFoundError:
        print("Error: 'claude' command not found. Make sure Claude Code is installed.")
        print("Install with: npm install -g @anthropic-ai/claude-code")
    except Exception as e:
        print(f"Error launching Claude Code: {e}")
    finally:
        # Stop SocketServer
        if socket_server:
            socket_server.stop()
        # Unregister from registry
        registry.unregister(agent_id)
        # Clean up temp config
        if config_dir:
            shutil.rmtree(config_dir, ignore_errors=True)
        # Clean up sock file if still present
        if os.path.exists(sock_path):
            os.unlink(sock_path)
        print(f"\nAgent '{agent_name}' session ended.")
