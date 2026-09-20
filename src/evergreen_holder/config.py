"""XDG config file: paths, permission enforcement, reading, writing, doctor."""
from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path

APP = "evergreen-holder"
REQUIRED_KEYS = ("base_url", "branch_id", "system_id", "consortium_id", "username", "preferred_format")
INT_KEYS = ("branch_id", "system_id", "consortium_id")
FORMATS = ("hardcover", "paperback")
PIKA_KEYS = ("base_url", "pickup_branch")
PIKA_SETTABLE_KEYS = tuple(f"pika.{k}" for k in PIKA_KEYS)
SETTABLE_KEYS = REQUIRED_KEYS + PIKA_SETTABLE_KEYS  # password is deliberately excluded


class ConfigError(Exception):
    pass


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP / "config.toml"


def idl_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / APP / "fm_IDL.xml"


def _mode_ok(p: Path) -> bool:
    return stat.S_IMODE(p.stat().st_mode) == 0o600


def read_raw() -> dict:
    p = config_path()
    if not p.exists():
        return {}
    if not _mode_ok(p):
        raise ConfigError(f"{p} must be mode 0600 (run: chmod 600 {p})")
    try:
        with p.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{p} is not valid TOML: {e}") from e


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def write_raw(data: dict) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    flat = {k: v for k, v in data.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    body = "".join(f"{k} = {_toml_value(v)}\n" for k, v in flat.items())
    for name, table in tables.items():
        body += f"\n[{name}]\n"
        body += "".join(f"{k} = {_toml_value(v)}\n" for k, v in table.items())
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    os.chmod(p, 0o600)


def _has_control_chars(s: str) -> bool:
    return any(ord(c) < 0x20 or ord(c) == 0x7f for c in s)


def set_value(key: str, value: str) -> dict:
    if key == "password":
        raise ConfigError("password can only be set with `evergreen-config set-password`")
    if key not in SETTABLE_KEYS:
        raise ConfigError(f"unknown key {key!r}; valid keys: {', '.join(SETTABLE_KEYS)}")
    if _has_control_chars(value):
        raise ConfigError(f"{key} must not contain control characters")
    if key.startswith("pika."):
        subkey = key.split(".", 1)[1]
        if subkey == "base_url" and not value.startswith("https://"):
            raise ConfigError("pika.base_url must start with https://")
        data = read_raw()
        pika = dict(data.get("pika") or {})
        pika[subkey] = value
        data["pika"] = pika
        write_raw(data)
        return data
    coerced: int | str = value
    if key in INT_KEYS:
        try:
            coerced = int(value)
        except ValueError:
            raise ConfigError(f"{key} must be an integer org unit id, got {value!r}") from None
    if key == "preferred_format" and value not in FORMATS:
        raise ConfigError(f"preferred_format must be one of {', '.join(FORMATS)}")
    if key == "base_url" and not value.startswith("https://"):
        raise ConfigError("base_url must start with https://")
    data = read_raw()
    if key == "base_url" and "base_url" in data and data["base_url"] != coerced and "password" in data:
        del data["password"]
    data[key] = coerced
    write_raw(data)
    return data


def set_password(password: str) -> None:
    if _has_control_chars(password):
        raise ConfigError("password must not contain control characters")
    data = read_raw()
    data["password"] = password
    write_raw(data)


def doctor() -> dict:
    p = config_path()
    exists = p.exists()
    mode_ok = exists and _mode_ok(p)
    data: dict = {}
    parse_error = None
    if mode_ok:
        try:
            with p.open("rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            parse_error = str(e)
    missing = [k for k in REQUIRED_KEYS if k not in data]
    password_set = bool(data.get("password"))
    idl_cached = idl_path().exists()
    pika_data = data.get("pika") or {}
    pika_missing = [k for k in PIKA_KEYS if k not in pika_data]
    result = {
        "config_path": str(p),
        "exists": exists,
        "mode_ok": bool(mode_ok),
        "idl_cached": idl_cached,
        "missing": missing,
        "password_set": password_set,
        "ok": bool(exists and mode_ok and not missing and password_set and idl_cached and parse_error is None),
        "pika": {"configured": not pika_missing, "missing": pika_missing},
    }
    if parse_error is not None:
        result["parse_error"] = parse_error
    return result


def load() -> dict:
    data = read_raw()
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ConfigError("config is missing: " + ", ".join(missing) + " (run `evergreen-config doctor`)")
    return data


def load_pika() -> dict:
    data = read_raw()
    pika = data.get("pika") or {}
    missing = [k for k in PIKA_KEYS if k not in pika]
    if missing:
        raise ConfigError("pika config is missing: " + ", ".join(f"pika.{k}" for k in missing) +
                           " (run `evergreen-config set pika.<key> <value>`)")
    return pika
