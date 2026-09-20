"""evergreen-config: doctor | set <key> <value> | set-password"""
from __future__ import annotations

import argparse
import getpass
import sys

from . import config, holds
from .cli_common import build_client, emit
from .gateway import GatewayError, IlsEvent
from .idl import fetch_idl


def cmd_doctor() -> int:
    d = config.doctor()
    emit(d)
    return 0 if d["ok"] else 1


def cmd_set(key: str, value: str) -> int:
    try:
        had_password = "password" in config.read_raw()
        data = config.set_value(key, value)
        if key == "base_url":
            fetch_idl(value, config.idl_path())
    except config.ConfigError as e:
        emit({"error": "config", "desc": str(e)})
        return 1
    except Exception as e:  # network failure fetching IDL
        emit({"error": "idl_fetch", "desc": str(e)})
        return 1
    result = {"ok": True, "key": key}
    if had_password and "password" not in data:
        result["password_cleared"] = True
    emit(result)
    return 0


def cmd_set_password(prompt=getpass.getpass) -> int:
    if not sys.stdin.isatty():
        emit({"error": "not_a_tty", "desc": "set-password must be run by a person in an interactive terminal"})
        return 1
    try:
        cfg = config.load()
    except config.ConfigError as e:
        emit({"error": "config", "desc": str(e)})
        return 1
    first = prompt("Evergreen password: ")
    second = prompt("Again: ")
    if first != second or not first:
        emit({"error": "mismatch", "desc": "passwords did not match (or were empty); nothing written"})
        return 1
    try:
        client = build_client(cfg)
        token = holds.login(client, cfg["username"], first)
        holds.logout(client, token)
    except config.ConfigError as e:
        emit({"error": "config", "desc": str(e)})
        return 1
    except IlsEvent as e:
        emit({"error": e.textcode, "desc": e.desc})
        return 2
    except GatewayError as e:
        emit({"error": "gateway", "desc": str(e)})
        return 2
    config.set_password(first)
    emit({"ok": True, "key": "password", "verified": True})
    return 0


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
