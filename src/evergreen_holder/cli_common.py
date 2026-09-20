from __future__ import annotations

import json
import sys
from typing import NoReturn

from .config import ConfigError, idl_path
from .gateway import GatewayClient
from .idl import load_field_map


def emit(obj) -> None:
    print(json.dumps(obj, indent=1, ensure_ascii=False))


def fail(error: str, desc: str, code: int) -> NoReturn:
    emit({"error": error, "desc": desc})
    sys.exit(code)


def build_client(cfg: dict) -> GatewayClient:
    p = idl_path()
    if not p.exists():
        raise ConfigError(f"IDL not cached at {p}; run `evergreen-config set base_url <url>` to fetch it")
    return GatewayClient(cfg["base_url"], load_field_map(p))
