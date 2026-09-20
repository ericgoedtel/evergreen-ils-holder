"""evergreen-hold <bib_id> [--dry-run]: place a title hold for pickup at the configured branch.

The password is read from the config file only. This tool is meant to be run only after a
human has confirmed the specific bib in the active session."""
from __future__ import annotations

import argparse
import sys

from . import config, holds
from .cli_common import build_client, emit
from .gateway import GatewayError, IlsEvent


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="evergreen-hold")
    p.add_argument("bib_id", type=int)
    p.add_argument("--dry-run", action="store_true", help="log in and build the payload, but do not place the hold")
    a = p.parse_args(argv)
    try:
        cfg = config.load()
        if not cfg.get("password"):
            raise config.ConfigError("password is not set; run `evergreen-config set-password` in a terminal")
        client = build_client(cfg)
    except config.ConfigError as e:
        emit({"error": "config", "desc": str(e)})
        return 1

    token = None
    try:
        token = holds.login(client, cfg["username"], cfg["password"])
        patron = holds.patron_id(client, token)
        pickup = cfg["branch_id"]
        notify = holds.notify_prefs(client, token, patron)
        if a.dry_run:
            emit({"dry_run": True, "bib_id": a.bib_id, "pickup_lib": pickup, "patron_id": patron,
                  "notify": notify, "payload": holds.hold_payload(patron, pickup, notify),
                  "would_call": "open-ils.circ.holds.test_and_create.batch"})
            return 0
        hold_id = holds.place_title_hold(client, token, patron, pickup, a.bib_id, notify)
        stats = holds.queue_stats(client, token, hold_id)
        base = cfg["base_url"].rstrip("/")
        emit({"hold_id": hold_id, "bib_id": a.bib_id, "pickup_lib": pickup,
              "queue_position": stats.get("queue_position"), "total_holds": stats.get("total_holds"),
              "potential_copies": stats.get("potential_copies"), "estimated_wait": stats.get("estimated_wait"),
              "status": stats.get("status"), "notify": notify,
              "record_url": f"{base}/eg/opac/record/{a.bib_id}",
              "holds_url": f"{base}/eg/opac/myopac/holds"})
        return 0
    except IlsEvent as e:
        emit({"error": e.textcode, "desc": e.desc})
        return 2
    except GatewayError as e:
        emit({"error": "gateway", "desc": str(e)})
        return 2
    finally:
        if token:
            holds.logout(client, token)


if __name__ == "__main__":
    sys.exit(main())
