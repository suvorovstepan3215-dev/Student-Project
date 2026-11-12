"""Build and verify the two standalone coursework distributions.

From the repository root:

    python tools/build_variants.py            # update tracked streamlit_web/ and local_pc/
    python tools/build_variants.py --archives # ZIPs in downloads/ (built from the allowlist)
    python tools/build_variants.py --check    # fail if the tracked copies are stale/dirty

Copies are explicit allowlists of source and templates. Virtual environments,
logs, history, local MCP configs and secrets are never copied into either kit.
"""

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Лекция_3"
TEMPLATES = ROOT / "variant_templates"
COMMON_MODULES = (
    "archive_agent.py", "arxiv_agent.py", "europepmc_agent.py", "habr_agent.py",
    "minzdrav_agent.py", "openalex_agent.py", "network.py", "results.py",
    "scraper.py", "security.py", "summarizer.py", "utils.py", "validation.py", "validator.py",
)
COMMON_REQUIREMENTS = (
    "requests", "beautifulsoup4", "pyyaml", "sumy", "nltk", "numpy", "filelock", "defusedxml", "urllib3",
)
PROFILES = {
    "streamlit_web": {
        "source": ("main.py", "notifier.py", "streamlit_app.py", "diagr/agents.html", "diagr/agents.png"),
        "templates": ("README.md", "check_setup.py", "start.sh", "start.ps1"),
        "requirements": ("apscheduler", "streamlit", "pandas", "pillow"),
    },
    "local_pc": {
        "source": ("mcp_server.py", "mcp_service.py", "mcp_client.py", "tools/mcp_config.py"),
        "templates": ("README.md", "check_setup.py", "demo_search.py"),
        "requirements": ("mcp",),
    },
}

IGNORE = (
    ".venv/\n__pycache__/\n*.py[cod]\n.pytest_cache/\n.ruff_cache/\n*.log\n*.log.[0-9]*\nhistory.json\n"
    "history.json.lock\n*.tmp\n.env\n.env.*\n.streamlit/secrets.toml\natomic-chat.json\ntools/*.json\n"
)


def read_pins() -> dict[str, str]:
    pins = {}
    for line in (SOURCE / "requirements.txt").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_-]+)==([^\s#]+)", line.strip())
        if match:
            pins[match[1].lower()] = line.strip()
    return pins


def requirements(names: tuple[str, ...]) -> bytes:
    pins = read_pins()
    missing = set(names) - pins.keys()
    if missing:
        raise ValueError("Missing exact dependency pins: " + ", ".join(sorted(missing)))
    header = ("# Standalone kit built from the coursework tracker (Python 3.11+).\n"
              "# Exact pins: reproducible install and no dependency resolver surprises.\n")
    return (header + "\n".join(pins[name] for name in names) + "\n").encode("utf-8")


def payloads() -> dict[str, dict[str, bytes]]:
    result = {}
    for profile, settings in PROFILES.items():
        files = {name: (SOURCE / name).read_bytes() for name in (*COMMON_MODULES, *settings["source"])}
        files.update({name: (TEMPLATES / profile / name).read_bytes() for name in settings["templates"]})
        files.update({name: (TEMPLATES / name).read_bytes() for name in ("setup.sh", "setup.ps1")})
        files["requirements.txt"] = requirements(COMMON_REQUIREMENTS + settings["requirements"])
        files["config.yaml"] = (TEMPLATES / "config.public.yaml").read_bytes()
        files[".gitignore"] = IGNORE.encode("utf-8")
        if profile == "streamlit_web":
            files[".streamlit/config.toml"] = (
                b'[server]\nheadless = true\naddress = "0.0.0.0"\n\n[browser]\ngatherUsageStats = false\n\n'
                b'[theme]\nprimaryColor = "#8A6D4B"\nbackgroundColor = "#FAF7F0"\n'
                b'secondaryBackgroundColor = "#F2EEE3"\ntextColor = "#3F3A33"\nfont = "sans serif"\n'
            )
        digest = hashlib.sha256()
        for name, content in sorted(files.items()):
            digest.update(name.encode("utf-8") + b"\0" + content + b"\0")
        files["BUILD_INFO.json"] = (json.dumps({
            "profile": profile,
            "canonical_source": "Лекция_3 (maintained repository copy)",
            "payload_sha256": digest.hexdigest(),
            "regenerate": "python tools/build_variants.py",
        }, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        result[profile] = files
    return result


def write_variants(output_root: Path) -> None:
    for profile, files in payloads().items():
        for relative, content in files.items():
            path = output_root / profile / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.is_file() or path.read_bytes() != content:
                path.write_bytes(content)
                if relative.endswith(".sh"):
                    path.chmod(0o755)


def stale_files(output_root: Path) -> list[str]:
    stale = []
    for profile, files in payloads().items():
        for relative, content in files.items():
            path = output_root / profile / relative
            if not path.is_file() or path.read_bytes() != content:
                stale.append(f"{profile}/{relative}")
    return stale


def create_archives(output_root: Path) -> list[Path]:
    directory = output_root / "downloads"
    directory.mkdir(parents=True, exist_ok=True)
    archives = []
    for profile, files in payloads().items():
        path = directory / f"{profile}.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            # Pack the allowlist, never the on-disk folder: a local .venv, log,
            # edited config or generated MCP JSON can never leak into a release.
            for relative, content in sorted(files.items()):
                info = zipfile.ZipInfo(f"{profile}/{relative}", date_time=(2026, 11, 12, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (0o100755 if relative.endswith(".sh") else 0o100644) << 16
                archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        archives.append(path)
    return archives


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--archives", action="store_true")
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    if args.check and args.archives:
        parser.error("--check and --archives are separate operations")
    if args.check:
        stale = stale_files(args.output_root)
        if stale:
            print("Stale distribution files:\n" + "\n".join(stale), file=sys.stderr)
            return 1
        print("OK: both standalone copies match their sources and templates.")
        return 0
    write_variants(args.output_root)
    print(f"Updated: {args.output_root / 'streamlit_web'}")
    print(f"Updated: {args.output_root / 'local_pc'}")
    if args.archives:
        for path in create_archives(args.output_root):
            print(f"Archive: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
