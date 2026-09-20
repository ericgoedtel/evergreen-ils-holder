"""evergreen-search "<title> <author>" | --orgs <name fragment>"""
from __future__ import annotations

import argparse
import sys

from . import config
from .catalog import find_orgs, org_names, search_bibs
from .cli_common import build_client, emit
from .gateway import GatewayError, IlsEvent


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="evergreen-search")
    p.add_argument("query", nargs="?", help="title and author words")
    p.add_argument("--orgs", metavar="NAME", help="find org units whose name contains NAME")
    a = p.parse_args(argv)
    if not a.query and not a.orgs:
        p.error("give a query or --orgs")
    try:
        cfg = config.read_raw() if a.orgs else config.load()
        if "base_url" not in cfg:
            raise config.ConfigError("base_url is not set")
        client = build_client(cfg)
        if a.orgs:
            emit({"orgs": find_orgs(client, a.orgs)})
            return 0
        names = org_names(client, [cfg["branch_id"], cfg["system_id"], cfg["consortium_id"]])
        results = search_bibs(client, a.query, cfg["branch_id"], cfg["system_id"], cfg["consortium_id"])
        emit({
            "preferred_format": cfg["preferred_format"],
            "branch": {"id": cfg["branch_id"], "name": names.get(cfg["branch_id"], "")},
            "system": {"id": cfg["system_id"], "name": names.get(cfg["system_id"], "")},
            "consortium": {"id": cfg["consortium_id"], "name": names.get(cfg["consortium_id"], "")},
            "results": results,
        })
        return 0
    except config.ConfigError as e:
        emit({"error": "config", "desc": str(e)})
        return 1
    except IlsEvent as e:
        emit({"error": e.textcode, "desc": e.desc})
        return 2
    except GatewayError as e:
        emit({"error": "gateway", "desc": str(e)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
