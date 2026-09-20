"""Read-only catalog operations: org lookup and bib search."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

ACTOR = "open-ils.actor"
SEARCH = "open-ils.search"
SUPERCAT = "open-ils.supercat"


def _org_tree(client) -> dict:
    return client.call_one(ACTOR, "open-ils.actor.org_tree.retrieve")


def _walk(node: dict, ancestors: list[dict]):
    yield node, ancestors
    for child in node.get("children") or []:
        yield from _walk(child, ancestors + [{"id": node["id"], "name": node["name"]}])


def find_orgs(client, fragment: str) -> list[dict]:
    frag = fragment.lower()
    hits = []
    for node, ancestors in _walk(_org_tree(client), []):
        name = node.get("name") or ""
        short = node.get("shortname") or ""
        if frag in name.lower() or frag in short.lower():
            hits.append({"id": node["id"], "name": name, "shortname": short, "ancestors": ancestors})
    return sorted(hits, key=lambda h: h["id"])


def org_names(client, ids: list[int]) -> dict[int, str]:
    wanted = set(ids)
    return {node["id"]: node["name"] for node, _ in _walk(_org_tree(client), []) if node["id"] in wanted}


MARC_NS = "{http://www.loc.gov/MARC21/slim}"
SEARCH_FILTERS = "search_format(book) -item_form(d)"
MAX_RESULTS = 25


def parse_isbns(marcxml: str) -> list[dict]:
    root = ET.fromstring(marcxml)
    out = []
    for df in root.iter(f"{MARC_NS}datafield"):
        if df.get("tag") != "020":
            continue
        a = q = None
        for sf in df.iter(f"{MARC_NS}subfield"):
            if sf.get("code") == "a" and a is None:
                a = (sf.text or "").strip()
            elif sf.get("code") == "q" and q is None:
                q = (sf.text or "").strip()
        if not a:
            continue
        isbn = a.split()[0].upper()
        label = re.sub(r"[()\s:;.]", "", q).lower() if q else ""
        out.append({"isbn": isbn, "label": label})
    return out


def _tier(counts: list[dict], org_id: int) -> dict:
    for c in counts:
        if int(c.get("org_unit", -1)) == org_id:
            return {"available": int(c.get("available", 0)), "total": int(c.get("count", 0))}
    return {"available": 0, "total": 0}


def search_bibs(client, query: str, branch_id: int, system_id: int, consortium_id: int) -> list[dict]:
    res = client.call_one(SEARCH, "open-ils.search.biblio.multiclass.query",
                          {"limit": MAX_RESULTS, "org_unit": consortium_id}, f"{query} {SEARCH_FILTERS}", 1)
    ids = [int(row[0]) for row in (res or {}).get("ids", [])]
    results = []
    for bib_id in ids:
        mods = client.call_one(SEARCH, "open-ils.search.biblio.record.mods_slim.retrieve", bib_id) or {}
        bre_payload = client.call_one(SUPERCAT, "open-ils.supercat.record.object.retrieve", bib_id)
        bre = bre_payload[0] if isinstance(bre_payload, list) else bre_payload
        marc = (bre or {}).get("marc") or ""
        counts = client.call_one(SEARCH, "open-ils.search.biblio.record.copy_count", branch_id, bib_id) or []
        isbns = parse_isbns(marc) if marc else []
        physical = mods.get("physical_description") or ""
        results.append({
            "bib_id": bib_id,
            "title": mods.get("title") or "",
            "author": mods.get("author") or "",
            "year": str(mods.get("pubdate") or ""),
            "isbns": isbns,
            "formats": sorted({i["label"] for i in isbns if i["label"]}),
            "physical_description": physical,
            "large_print": "large print" in physical.lower(),
            "copies": {
                "branch": _tier(counts, branch_id),
                "system": _tier(counts, system_id),
                "consortium": _tier(counts, consortium_id),
            },
        })
    return results
