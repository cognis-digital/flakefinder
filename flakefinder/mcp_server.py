"""FLAKEFINDER MCP server — exposes scan() as an MCP tool for Cognis.Studio."""
from __future__ import annotations
from flakefinder.core import scan, to_json

def serve() -> int:
    """Start an MCP stdio server. Requires the optional 'mcp' extra:
        pip install "cognis-flakefinder[mcp]"
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        print("Install the MCP extra: pip install 'cognis-flakefinder[mcp]'")
        return 1
    app = FastMCP("flakefinder")

    @app.tool()
    def flakefinder_scan(target: str) -> str:
        """Flaky-test detector from CI history with quarantine suggestions. Returns JSON findings."""
        return to_json(scan(target))

    app.run()
    return 0
