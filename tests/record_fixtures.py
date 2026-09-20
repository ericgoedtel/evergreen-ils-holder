"""Re-record gateway fixtures from a live Evergreen. Run manually: uv run python tests/record_fixtures.py
Requires network. Uses NC Cardinal; adjust BASE/ORGS if recording elsewhere."""
from __future__ import annotations

import json
from pathlib import Path

from evergreen_holder.gateway import GatewayClient
from evergreen_holder.idl import fetch_idl, load_field_map

BASE = "https://johnston.nccardinal.org"
BRANCH = 501
HERE = Path(__file__).parent
FIX = HERE / "fixtures"
IDL = HERE / "fixtures" / "fm_IDL.xml"

CALLS = [
    ("open-ils.actor", "open-ils.actor.org_tree.retrieve"),
    ("open-ils.search", "open-ils.search.biblio.multiclass.query",
     {"limit": 25, "org_unit": 1}, "the overstory powers search_format(book) -item_form(d)", 1),
    ("open-ils.search", "open-ils.search.biblio.record.mods_slim.retrieve", 12547531),
    ("open-ils.search", "open-ils.search.biblio.record.mods_slim.retrieve", 12834206),
    ("open-ils.supercat", "open-ils.supercat.record.object.retrieve", 12547531),
    ("open-ils.supercat", "open-ils.supercat.record.object.retrieve", 12834206),
    ("open-ils.search", "open-ils.search.biblio.record.copy_count", BRANCH, 12547531),
    ("open-ils.search", "open-ils.search.biblio.record.copy_count", BRANCH, 12834206),
]


def key(method: str, params: tuple) -> str:
    return f"{method}|{json.dumps(list(params))}"


def main() -> None:
    FIX.mkdir(exist_ok=True)
    if not IDL.exists():
        fetch_idl(BASE, IDL)
    client = GatewayClient(BASE, load_field_map(IDL))
    out: dict[str, list] = {}
    for service, method, *params in CALLS:
        out[key(method, tuple(params))] = client.call(service, method, *params)
        print("recorded", method, params)
    (FIX / "catalog.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
