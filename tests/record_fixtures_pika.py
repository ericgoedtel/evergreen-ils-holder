"""Re-record Pika fixtures from the live Wake County catalog.
Run manually: uv run python tests/record_fixtures_pika.py
Requires network. Uses catalog.wake.gov; adjust BASE/ids if recording elsewhere."""
from __future__ import annotations

from pathlib import Path

import httpx

from evergreen_holder.pika import HEADERS

BASE = "https://catalog.wake.gov"
HERE = Path(__file__).parent
FIX = HERE / "fixtures" / "pika"

CRYPTONOMICON_GW = "dfe46b1a-9917-0768-bc0e-e6dfad241b5f"
DUNE_GW = "ddb713e8-646d-8b2b-8ee6-02d926507b73"
# Austen: one grouped work with a Large Print record plus two Book records.
PRIDE_GW = "ef610de7-c7e1-b9e8-7e40-92f2254e2d35"
AVAIL_IDS = ("428175", "495749")


def _get(client: httpx.Client, path: str, **kwargs) -> httpx.Response:
    r = client.get(path, **kwargs)
    r.raise_for_status()
    return r


def main() -> None:
    FIX.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(base_url=BASE, headers=HEADERS, timeout=30, follow_redirects=True)

    for name, query in (("cryptonomicon", "Cryptonomicon"), ("dune", "Dune")):
        r = _get(client, "/Search/Results", params={
            "lookfor": query, "basicType": "Title", "view": "rss",
            "searchSource": "local", "filter[]": "format_category:Books",
        })
        (FIX / f"search_{name}.xml").write_text(r.text, encoding="utf-8")
        print("recorded search", name, len(r.text), "bytes")

    for name, gw in (("cryptonomicon", CRYPTONOMICON_GW), ("dune", DUNE_GW), ("pride", PRIDE_GW)):
        r = _get(client, f"/GroupedWork/{gw}/Home")
        (FIX / f"work_{name}.html").write_text(r.text, encoding="utf-8")
        print("recorded work", name, len(r.text), "bytes")

    for rec_id in AVAIL_IDS:
        r = _get(client, "/API/ItemAPI", params={"method": "getItemAvailability", "id": f"ils:{rec_id}"})
        (FIX / f"availability_{rec_id}.json").write_text(r.text, encoding="utf-8")
        print("recorded availability", rec_id, len(r.text), "bytes")


if __name__ == "__main__":
    main()
