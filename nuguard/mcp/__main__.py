"""Entry point: python -m nuguard.mcp"""

from nuguard.mcp.server import mcp


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
