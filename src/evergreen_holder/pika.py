"""Read-only client for Pika (Marmot's open-source VuFind fork) catalogs, e.g. Wake
County Public Libraries. All requests are anonymous GETs. No holds support here."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

# Pika's WAF blocks HEAD requests and non-browser-looking clients; never send HEAD.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/128.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
MAX_WORKS = 10

GW_LINK_RE = re.compile(r"GroupedWork/([0-9a-f-]{36})")
TOTAL_FOUND_RE = re.compile(r"of ([\d,]+) found")
WAIT_RE = re.compile(r"(\d+) cop(?:y|ies), (\d+) people are on the wait list")
# Debugging table rows look like "ils:427787 Book Books English ..." or
# "ils:416441 Audiobook Audio Books ...". Only ils: records are matched (overdrive:
# records are skipped since this pattern requires the "ils:" prefix literally).
RECORD_RE = re.compile(r"ils:(\d+) (Book|Large Print|[A-Z][A-Za-z ]+?) (?:Books|Audio Books|eBook|Large Print) ")


class PikaError(Exception):
    """Transport, HTTP, or parse failure talking to a Pika catalog."""


def build_client(base_url: str, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(base_url=base_url.rstrip("/"), headers=HEADERS, timeout=30,
                         follow_redirects=True, transport=transport)


def _get(client: httpx.Client, path: str, **kwargs) -> httpx.Response:
    try:
        resp = client.get(path, **kwargs)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise PikaError(f"{path}: {e}") from e
    return resp


def parse_total_found(description: str) -> int:
    m = TOTAL_FOUND_RE.search(description or "")
    return int(m.group(1).replace(",", "")) if m else 0


def parse_rss(text: str) -> tuple[str, list[dict]]:
    """Returns (channel description, [{grouped_work_id, title, author}, ...]).
    Items whose link isn't a GroupedWork (e.g. user lists) are dropped."""
    channel = ET.fromstring(text).find("channel")
    if channel is None:
        return "", []
    items = []
    for it in channel.findall("item"):
        m = GW_LINK_RE.search(it.findtext("link") or "")
        if not m:
            continue
        items.append({
            "grouped_work_id": m.group(1),
            "title": (it.findtext("title") or "").strip(),
            "author": (it.findtext("author") or "").strip(),
        })
    return (channel.findtext("description") or "").strip(), items


def parse_work_page(text: str) -> tuple[list[dict], dict | None]:
    """Returns ([{record_id, format}, ...], {"copies", "holds"} | None)."""
    stripped = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))
    wait_match = WAIT_RE.search(stripped)
    wait_list = {"copies": int(wait_match.group(1)), "holds": int(wait_match.group(2))} if wait_match else None
    records = [{"record_id": m.group(1), "format": m.group(2)} for m in RECORD_RE.finditer(stripped)]
    return records, wait_list


def parse_holdings(data: dict) -> list[dict]:
    """Flattens getItemAvailability's holdings dict-of-lists into one list of copies."""
    holdings = ((data.get("result") or {}).get("holdings")) or {}
    copies = []
    for values in holdings.values():
        rows = values if isinstance(values, list) else [values]
        for v in rows:
            location = v.get("location") or ""
            # NOTE: splitting on the first " - " misparses the one Wake branch whose
            # own name contains " - " ("Express - Fayetteville St."). Pickup-branch
            # matching therefore uses location.startswith(pickup_branch + " - ")
            # rather than comparing this split-out branch name, so it's unaffected.
            branch, _, collection = location.partition(" - ")
            copies.append({
                "location": location,
                "branch": branch,
                "collection": collection,
                "status": v.get("statusFull") or v.get("status") or "",
                "available": bool(v.get("availability")),
                "holdable": bool(v.get("holdable")),
                "callnumber": v.get("callnumber"),
                "due_date": v.get("dueDate"),
            })
    return copies


def summarize_availability(copies: list[dict], pickup_branch: str) -> dict:
    prefix = pickup_branch + " - "
    branch_copies = [c for c in copies if c["location"].startswith(prefix)]
    return {
        "branch": {"available": sum(1 for c in branch_copies if c["available"]), "total": len(branch_copies)},
        "system": {"available": sum(1 for c in copies if c["available"]), "total": len(copies)},
    }


def on_shelf_at(copies: list[dict]) -> list[str]:
    return sorted({c["branch"] for c in copies if c["available"]})


def search_works(client: httpx.Client, query: str) -> tuple[int, list[dict]]:
    resp = _get(client, "/Search/Results", params={
        "lookfor": query, "basicType": "Title", "view": "rss",
        "searchSource": "local", "filter[]": "format_category:Books",
    })
    try:
        description, items = parse_rss(resp.text)
    except ET.ParseError as e:
        raise PikaError(f"could not parse search results: {e}") from e
    return parse_total_found(description), items


def work_records(client: httpx.Client, grouped_work_id: str) -> tuple[list[dict], dict | None]:
    resp = _get(client, f"/GroupedWork/{grouped_work_id}/Home")
    return parse_work_page(resp.text)


def record_availability(client: httpx.Client, record_id: str) -> list[dict]:
    resp = _get(client, "/API/ItemAPI", params={"method": "getItemAvailability", "id": f"ils:{record_id}"})
    try:
        data: Any = resp.json()
    except ValueError as e:
        raise PikaError(f"invalid JSON from ItemAPI: {e}") from e
    return parse_holdings(data)
