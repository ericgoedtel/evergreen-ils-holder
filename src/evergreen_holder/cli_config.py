"""evergreen-config: doctor | set <key> <value> | set-password"""
from __future__ import annotations

import argparse
import sys

from . import config
from .cli_common import emit
from .idl import fetch_idl


def cmd_doctor() -> int:
    d = config.doctor()
    emit(d)
    return 0 if d["ok"] else 1


def cmd_set(key: str, value: str) -> int:
    try:
        config.set_value(key, value)
        if key == "base_url":
            fetch_idl(value, config.idl_path())
    except config.ConfigError as e:
        emit({"error": "config", "desc": str(e)})
        return 1
    except Exception as e:  # network failure fetching IDL
        emit({"error": "idl_fetch", "desc": str(e)})
        return 1
    emit({"ok": True, "key": key})
    return 0


def cmd_set_password() -> int:
    emit({"error": "not_implemented", "desc": "set-password arrives in a later task"})
    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="evergreen-config")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="report config state as JSON; exit 0 only when complete")
    s = sub.add_parser("set", help="set one config key")
    s.add_argument("key")
    s.add_argument("value")
    sub.add_parser("set-password", help="interactively set the password (never via Claude)")
    a = p.parse_args(argv)
    if a.cmd == "doctor":
        return cmd_doctor()
    if a.cmd == "set":
        return cmd_set(a.key, a.value)
    return cmd_set_password()


if __name__ == "__main__":
    sys.exit(main())
