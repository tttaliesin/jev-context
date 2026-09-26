"""Install the reviewed repository skill and MCP entry in this configured project only."""

import argparse
import json
import re
import tomllib
from pathlib import Path

from jev_context.cli import codex_entry
from jev_context.policy import Config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--update",
        action="store_true",
        help="Replace the reviewed Jev entry and skill with backups",
    )
    args = parser.parse_args()
    path = Path(args.config).resolve(strict=True)
    config = Config.load(path)
    root = config.project_root
    source = Path(__file__).resolve().parent.parent / "skills" / "jev-context" / "SKILL.md"
    skill = root / ".agents" / "skills" / "jev-context" / "SKILL.md"
    settings = root / ".codex" / "config.toml"
    for target in (skill, settings):
        if not target.resolve().is_relative_to(root):
            raise ValueError("Installation target escaped project root")
    entry = codex_entry(path)
    old = settings.read_text(encoding="utf-8") if settings.exists() else ""
    parsed = tomllib.loads(old)
    existing = parsed.get("mcp_servers", {}).get("jev_context")
    if existing is not None and existing != entry and not args.update:
        raise ValueError("Existing jev_context MCP entry differs; review manually")
    content = source.read_bytes()
    if skill.exists() and skill.read_bytes() != content and not args.update:
        raise ValueError("Existing skill differs; review manually")
    addition = (
        "\n[mcp_servers.jev_context]\n"
        + "\n".join(f"{key} = {json.dumps(value)}" for key, value in entry.items())
        + "\n"
    )
    updated = old + addition if existing is None else old
    if existing is not None and existing != entry:
        pattern = r"(?ms)^\[mcp_servers\.jev_context\][^\n]*\n.*?(?=^\[|\Z)"
        updated, count = re.subn(pattern, lambda _: addition.lstrip("\n") + "\n", old)
        expected = tomllib.loads(old)
        expected["mcp_servers"]["jev_context"] = entry
        if count != 1 or tomllib.loads(updated) != expected:
            raise ValueError("Cannot preserve unrelated settings; update this entry manually")
    tomllib.loads(updated)
    if args.update:
        from datetime import datetime

        backup = root / ".local" / "install-backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup.mkdir(parents=True)
        if skill.exists():
            (backup / "SKILL.md").write_bytes(skill.read_bytes())
        if settings.exists():
            (backup / "config.toml").write_bytes(settings.read_bytes())
    skill.parent.mkdir(parents=True, exist_ok=True)
    if not skill.exists() or args.update:
        skill.write_bytes(content)
    settings.parent.mkdir(parents=True, exist_ok=True)
    if updated != old:
        settings.write_text(updated, encoding="utf-8", newline="\n")
    print(
        "Installed project MCP configuration and matching skill; restart/reload the host to verify discovery"
    )


if __name__ == "__main__":
    main()
