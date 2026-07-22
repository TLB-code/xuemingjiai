#!/usr/bin/env python3
"""Apply persistent account-specific overrides to generated reader data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def replace_exact(value: Any, replacements: dict[str, str]) -> tuple[Any, bool]:
    if isinstance(value, str):
        replacement = replacements.get(value, value)
        return replacement, replacement != value
    if isinstance(value, list):
        changed = False
        result = []
        for item in value:
            updated, item_changed = replace_exact(item, replacements)
            result.append(updated)
            changed = changed or item_changed
        return result, changed
    if isinstance(value, dict):
        changed = False
        result = {}
        for key, item in value.items():
            updated, item_changed = replace_exact(item, replacements)
            result[key] = updated
            changed = changed or item_changed
        return result, changed
    return value, False


def write_json(path: Path, data: Any) -> None:
    if path.name == "index.json":
        content = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        content = json.dumps(data, ensure_ascii=False, indent=2)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content + "\n", encoding="utf-8")
    temporary.replace(path)


def apply_account(account_dir: Path, check: bool = False) -> list[Path]:
    config_path = account_dir / "reader_overrides.json"
    config = load_json(config_path)
    replacements = config.get("replace_exact", {})
    profile_overrides = config.get("profile", {})
    snapshots = account_dir / "wayback_snapshots"
    targets = sorted((snapshots / "json").glob("*.json"))
    targets.append(snapshots / "index.json")
    targets.append(snapshots / "profile.json")

    changed_paths: list[Path] = []
    for path in targets:
        if not path.exists():
            continue
        original = load_json(path)
        updated, changed = replace_exact(original, replacements)
        if path.name == "profile.json" and isinstance(updated, dict):
            for key, value in profile_overrides.items():
                if updated.get(key) != value:
                    updated[key] = value
                    changed = True
        if changed:
            changed_paths.append(path)
            if not check:
                write_json(path, updated)
    return changed_paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report unapplied overrides without changing files.",
    )
    args = parser.parse_args()

    accounts_root = args.root.resolve() / "accounts"
    configs = sorted(accounts_root.glob("*/reader_overrides.json"))
    if not configs:
        print("No reader override configurations found.")
        return 0

    changed: list[Path] = []
    for config_path in configs:
        changed.extend(apply_account(config_path.parent, check=args.check))

    if args.check and changed:
        print("Reader overrides are not applied:")
        for path in changed:
            print(f"  {path.relative_to(args.root.resolve())}")
        return 1

    action = "Would update" if args.check else "Updated"
    print(f"{action} {len(changed)} reader data file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
