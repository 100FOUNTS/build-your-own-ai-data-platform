"""Store and display the learning mode for each module and challenge."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MODES = ("solo", "guided", "walkthrough")
KEYS = tuple(str(n) for n in range(19)) + tuple(f"R{n}" for n in range(1, 10))
MODES_FILE = Path(__file__).resolve().parents[2] / "modes.json"


def _key(key: str | int, *, allow_all: bool = False) -> str:
    value = str(key).strip().upper()
    if allow_all and value == "ALL":
        return "all"
    prefix = "R" if value.startswith("R") else ""
    number = value[1:] if prefix else value
    if number.isascii() and number.isdecimal():
        normalized = f"{prefix}{int(number)}"
        if normalized in KEYS:
            return normalized
    raise ValueError(f"Invalid key {key!r}: use 0–18 or R1–R9" + (", or all." if allow_all else "."))


def _mode(mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"Invalid mode {mode!r}: choose solo, guided or walkthrough.")
    return mode


def load() -> dict:
    """Read saved preferences; an absent file uses guided mode."""
    if not MODES_FILE.exists():
        return {"default": "guided", "modules": {}}
    try:
        data = json.loads(MODES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {MODES_FILE.name}: {exc.msg}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("modules", {}), dict):
        raise ValueError("modes.json must contain an object with a modules object.")
    default = _mode(data.get("default", "guided"))
    modules = {_key(key): _mode(mode) for key, mode in data.get("modules", {}).items()}
    return {"default": default, "modules": modules}


def get_mode(key: str | int) -> str:
    """Return an override or the saved default for a module or challenge."""
    normalized = _key(key)
    data = load()
    return data["modules"].get(normalized, data["default"])


def set_mode(key: str | int, mode: str) -> None:
    """Set a module override, or change the default with the key all."""
    normalized = _key(key, allow_all=True)
    _mode(mode)
    data = load()
    if normalized == "all":
        data["default"] = mode
    else:
        data["modules"][normalized] = mode
    MODES_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    set_parser = commands.add_parser("set", help="Set a module, challenge or default mode")
    set_parser.add_argument("key")
    set_parser.add_argument("mode")
    get_parser = commands.add_parser("get", help="Get a module or challenge mode")
    get_parser.add_argument("key")
    commands.add_parser("show", help="Show all module and challenge modes")
    args = parser.parse_args()
    try:
        if args.command == "set":
            set_mode(args.key, args.mode)
            print(f"{_key(args.key, allow_all=True)}: {args.mode}")
        elif args.command == "get":
            print(get_mode(args.key))
        else:
            data = load()
            print(f"{'Module / challenge':<20} Mode")
            for key in KEYS:
                print(f"{key:<20} {data['modules'].get(key, data['default'])}")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
