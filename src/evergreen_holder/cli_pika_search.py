"""pika-search "<title words>" -- read-only search of a Pika (VuFind) catalog."""
from __future__ import annotations

import argparse
import sys

from . import config
from .cli_common import emit
from .pika import (
    MAX_WORKS,
    PikaError,
    build_client,
    on_shelf_at,
    record_availability,
    search_works,
    summarize_availability,
    work_records,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pika-search")
    p.add_argument("query", help="title words")
    a = p.parse_args(argv)
    try:
        cfg = config.load_pika()
        client = build_client(cfg["base_url"])
        total_found, hits = search_works(client, a.query)
        results = []
        for hit in hits[:MAX_WORKS]:
            records, wait_list = work_records(client, hit["grouped_work_id"])
            rec_out = []
            for rec in records:
                copies = record_availability(client, rec["record_id"])
                rec_out.append({
                    "record_id": rec["record_id"],
                    "format": rec["format"],
                    "large_print": "large print" in rec["format"].lower(),
                    "copies": summarize_availability(copies, cfg["pickup_branch"]),
                    "on_shelf_at": on_shelf_at(copies),
                    "record_url": f"{cfg['base_url']}/Record/{rec['record_id']}",
                })
            results.append({
                "grouped_work_id": hit["grouped_work_id"],
                "title": hit["title"],
                "author": hit["author"],
                "url": f"{cfg['base_url']}/GroupedWork/{hit['grouped_work_id']}/Home",
                "wait_list": wait_list,
                "records": rec_out,
            })
        emit({
            "system": "pika",
            "base_url": cfg["base_url"],
            "pickup_branch": cfg["pickup_branch"],
            "total_found": total_found,
            "results": results,
        })
        return 0
    except config.ConfigError as e:
        emit({"error": "config", "desc": str(e)})
        return 1
    except PikaError as e:
        emit({"error": "pika", "desc": str(e)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
