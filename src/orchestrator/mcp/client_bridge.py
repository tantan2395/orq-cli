"""Fast stdio MCP bridge executable connecting Codex/Agy to the Orchestrator Engine via IPC."""

import asyncio
from pathlib import Path
import sys
from orchestrator.mcp.ipc import IPCClient
from orchestrator.mcp.server import create_mcp_server


async def run_bridge(socket_path: Path = Path("/tmp/agent_orchestrator.sock")) -> None:
    """Runs the stdio MCP bridge server connected to the engine IPC socket."""
    client = IPCClient(socket_path=socket_path)
    server = create_mcp_server(ipc_client=client)
    await server.run_stdio_async()


def main():
    """Main CLI entry point for orchestrator-mcp-bridge."""
    try:
        asyncio.run(run_bridge())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
