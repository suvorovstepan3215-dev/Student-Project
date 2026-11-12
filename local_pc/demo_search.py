"""Optional LIVE source check through MCP; requires Internet, not a chat model."""

import argparse
import json
import sys
from datetime import date

from mcp_client import call_tool_sync


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("europepmc", "minzdrav", "both"), default="europepmc")
    parser.add_argument("--topic", action="append", help="Repeat for multiple topics")
    parser.add_argument("--rows", type=int, default=3)
    parser.add_argument("--date-from", default="2020-01-01")
    parser.add_argument("--date-to", default=date.today().isoformat())
    args = parser.parse_args(argv)
    topics = args.topic or (["diabetes", "сахарный диабет"] if args.source == "both" else
                            ["сахарный диабет"] if args.source == "minzdrav" else ["diabetes"])
    arguments = {"topics": topics, "rows": args.rows, "date_from": args.date_from, "date_to": args.date_to}
    tool = "search_" + args.source
    if args.source == "both":
        tool = "search_publications"
        arguments["sources"] = ["europepmc", "minzdrav"]
    try:
        result = call_tool_sync(tool, arguments)
    except Exception as exc:
        print(f"MCP check failed ({type(exc).__name__}). Check arguments and server logs.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # Partial successes remain useful. A failed source is not an empty success.
    return 1 if result.get("errors") and not result.get("items") else 0


if __name__ == "__main__":
    raise SystemExit(main())
