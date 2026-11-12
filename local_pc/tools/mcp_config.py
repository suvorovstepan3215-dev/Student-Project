"""Print MCP client JSON, or explicitly save an Atomic Chat server definition.

Run with the environment's Python so the command points at that environment:
    .venv/bin/python tools/mcp_config.py --format atomic --output atomic-chat.json
No credentials are read. Existing files are preserved unless --force is given.
"""

import argparse
import json
import sys
from pathlib import Path


def build_config() -> dict:
    directory = Path(__file__).resolve().parents[1]
    # Retain the venv interpreter symlink instead of resolving it to system Python.
    return {
        "mcpServers": {
            "publication-tracker": {
                "command": str(Path(sys.executable).absolute()),
                "args": [str(directory / "mcp_server.py")],
                "cwd": str(directory),
                "env": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            }
        }
    }


def atomic_config() -> dict:
    server = build_config()["mcpServers"]["publication-tracker"]
    # The Atomic Chat per-server JSON editor takes this object, not mcpServers.
    # cwd is unnecessary: the server resolves its files relative to __file__.
    return {key: server[key] for key in ("command", "args", "env")}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("mcp", "atomic"), default="mcp")
    parser.add_argument("--output", type=Path, help="Save UTF-8 JSON instead of printing it")
    parser.add_argument("--force", action="store_true", help="Explicitly replace an existing output file")
    args = parser.parse_args(argv)
    config = atomic_config() if args.format == "atomic" else build_config()
    text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        try:
            with args.output.open("w" if args.force else "x", encoding="utf-8") as stream:
                stream.write(text)
        except FileExistsError:
            parser.error(
                "Output already exists. Keep it, or explicitly use --force after moving the project."
            )
        except OSError as exc:
            parser.error(f"Cannot save configuration ({type(exc).__name__}).")
        print(f"Saved: {args.output.absolute()}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
