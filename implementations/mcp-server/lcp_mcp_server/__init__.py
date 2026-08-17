"""LCP MCP Server package."""


def main() -> None:
    """Load and run the MCP server entry point on demand."""
    from .server import main as server_main

    server_main()


__all__ = ["main"]
