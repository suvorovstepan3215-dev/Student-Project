"""Print a local MCP client configuration; no writes or embedded credentials.

Invoke with the environment's Python, e.g. .venv/bin/python tools/mcp_config.py
or .venv\\Scripts\\python.exe tools/mcp_config.py on Windows.
"""

import json
import sys
from pathlib import Path


def build_config() -> dict:
    directory = Path(__file__).resolve().parents[1]
    # Do NOT resolve the interpreter symlink: that would select the system
    # Python instead of the virtual environment on POSIX.
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


if __name__ == "__main__":
    print(json.dumps(build_config(), ensure_ascii=False, indent=2))
