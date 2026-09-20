"""Read-only catalog operations: org lookup and bib search."""
from __future__ import annotations

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
