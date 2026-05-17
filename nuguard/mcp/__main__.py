"""Entry point: python -m nuguard.mcp"""

from nuguard.mcp.server import mcp


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
