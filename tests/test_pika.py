import json
from pathlib import Path

import httpx
import pytest

from evergreen_holder.pika import (
    PikaError,
    build_client,
    on_shelf_at,
    parse_holdings,
    parse_rss,
    parse_total_found,
    parse_work_page,
    record_availability,
    search_works,
    summarize_availability,
    work_records,
)

FIX = Path(__file__).parent / "fixtures" / "pika"


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ---- parse_total_found ----

def test_parse_total_found():
    assert parse_total_found("Displaying the first 50 search results of 1 found.") == 1
    assert parse_total_found("Displaying the first 50 search results of 219 found.") == 219
    assert parse_total_found("") == 0
    assert parse_total_found("no match here") == 0


# ---- parse_rss ----

def test_parse_rss_cryptonomicon_single_hit():
    desc, items = parse_rss(fixture("search_cryptonomicon.xml"))
    assert "1 found" in desc
    assert len(items) == 1
    assert items[0] == {
        "grouped_work_id": "dfe46b1a-9917-0768-bc0e-e6dfad241b5f",
        "title": "Cryptonomicon",
        "author": "Stephenson, Neal",
    }


def test_parse_rss_dune_many_hits():
    desc, items = parse_rss(fixture("search_dune.xml"))
    assert parse_total_found(desc) >= 50
    assert len(items) == 50
    assert all(len(i["grouped_work_id"]) == 36 for i in items)


RSS_WITH_LIST_ITEM = """<?xml version="1.0"?><rss version="2.0"><channel>
<title>t</title><description>Displaying the first 50 search results of 2 found.</description>
<item><title>A Real Book</title><link>https://catalog.wake.gov/GroupedWork/dfe46b1a-9917-0768-bc0e-e6dfad241b5f/Home</link><author>Someone</author></item>
<item><title>Somebody's List</title><link>https://catalog.wake.gov/MyResearch/MyList/12345</link></item>
</channel></rss>"""


def test_parse_rss_drops_non_grouped_work_items():
    desc, items = parse_rss(RSS_WITH_LIST_ITEM)
    assert len(items) == 1
    assert items[0]["title"] == "A Real Book"


def test_parse_rss_item_without_author():
    rss = """<?xml version="1.0"?><rss version="2.0"><channel><description>d</description>
    <item><title>No Author Book</title>
    <link>https://catalog.wake.gov/GroupedWork/aaaaaaaa-1111-2222-3333-444444444444/Home</link></item>
    </channel></rss>"""
    _, items = parse_rss(rss)
    assert items[0]["author"] == ""


# ---- parse_work_page ----

def test_parse_work_page_cryptonomicon_single_record_no_wait_list():
    records, wait = parse_work_page(fixture("work_cryptonomicon.html"))
    assert records == [{"record_id": "428175", "format": "Book"}]
    assert wait is None


def test_parse_work_page_dune_has_wait_list():
    records, wait = parse_work_page(fixture("work_dune.html"))
    assert records == [{"record_id": "495749", "format": "Book"}]
    assert wait == {"copies": 9, "holds": 57}


def test_parse_work_page_multiple_ils_records_book_and_large_print():
    # Real page: Austen's Pride and Prejudice groups a Large Print record with two Book records
    # (and an OverDrive eBook, which must be dropped).
    records, wait = parse_work_page(fixture("work_pride.html"))
    assert records == [
        {"record_id": "716955", "format": "Large Print"},
        {"record_id": "416013", "format": "Book"},
        {"record_id": "709832", "format": "Book"},
    ]
    assert wait == {"copies": 17, "holds": 5}


def test_parse_work_page_wait_list_singular_copy():
    text = "blah blah 1 copy, 3 people are on the wait list blah"
    _, wait = parse_work_page(text)
    assert wait == {"copies": 1, "holds": 3}


# ---- parse_holdings ----

def test_parse_holdings_flattens_lists_and_splits_location():
    data = json.loads(fixture("availability_428175.json"))
    copies = parse_holdings(data)
    assert len(copies) == 2
    branches = {c["branch"] for c in copies}
    assert branches == {"Fuquay-Varina Community", "Middlecreek Community"}
    on_shelf = [c for c in copies if c["available"]]
    assert len(on_shelf) == 1
    assert on_shelf[0]["branch"] == "Middlecreek Community"
    assert on_shelf[0]["status"] == "On Shelf"


def test_parse_holdings_empty_result():
    assert parse_holdings({"result": {"holdings": {}}}) == []
    assert parse_holdings({"result": {}}) == []
    assert parse_holdings({}) == []


def test_parse_holdings_accepts_dict_value_not_just_list():
    data = {"result": {"holdings": {"slug": {"location": "Wendell Community - New Books",
                                              "statusFull": "On Shelf", "availability": True,
                                              "holdable": 1, "callnumber": "X", "dueDate": ""}}}}
    copies = parse_holdings(data)
    assert len(copies) == 1
    assert copies[0]["branch"] == "Wendell Community"


# ---- summarize_availability / on_shelf_at: the Express - Fayetteville St. quirk ----

def test_summarize_availability_pickup_branch_and_system_totals():
    data = json.loads(fixture("availability_495749.json"))
    copies = parse_holdings(data)
    summary = summarize_availability(copies, "East Regional")
    assert summary["branch"] == {"available": 0, "total": 1}
    assert summary["system"]["total"] == len(copies)


def test_pickup_matching_uses_startswith_so_express_branch_is_unaffected():
    # "Express - Fayetteville St." itself contains " - "; a naive split-based
    # pickup match would misparse it, but startswith(pickup + " - ") does not
    # accidentally match it when pickup_branch is something else.
    copies = [
        {"location": "Express - Fayetteville St. - New Books", "branch": "Express", "available": True},
        {"location": "Wendell Community - New Books", "branch": "Wendell Community", "available": False},
    ]
    summary = summarize_availability(copies, "Wendell Community")
    assert summary["branch"] == {"available": 0, "total": 1}
    summary_express = summarize_availability(copies, "Express - Fayetteville St.")
    assert summary_express["branch"] == {"available": 1, "total": 1}


def test_on_shelf_at_sorted_unique_branch_names():
    copies = [
        {"branch": "Zebulon Community", "available": True},
        {"branch": "Cary", "available": True},
        {"branch": "Cary", "available": True},
        {"branch": "Apex", "available": False},
    ]
    assert on_shelf_at(copies) == ["Cary", "Zebulon Community"]


# ---- httpx.MockTransport-backed client functions ----

def _transport():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        if path == "/Search/Results":
            q = params.get("lookfor")
            if q == "Cryptonomicon":
                return httpx.Response(200, text=fixture("search_cryptonomicon.xml"),
                                       headers={"content-type": "application/rss+xml"})
            if q == "Dune":
                return httpx.Response(200, text=fixture("search_dune.xml"))
            return httpx.Response(200, text='<?xml version="1.0"?><rss version="2.0"><channel><description>Displaying the first 0 search results of 0 found.</description></channel></rss>')
        if path == "/GroupedWork/dfe46b1a-9917-0768-bc0e-e6dfad241b5f/Home":
            return httpx.Response(200, text=fixture("work_cryptonomicon.html"))
        if path == "/GroupedWork/ddb713e8-646d-8b2b-8ee6-02d926507b73/Home":
            return httpx.Response(200, text=fixture("work_dune.html"))
        if path == "/API/ItemAPI":
            rec_id = params.get("id")
            if rec_id == "ils:428175":
                return httpx.Response(200, text=fixture("availability_428175.json"))
            if rec_id == "ils:495749":
                return httpx.Response(200, text=fixture("availability_495749.json"))
        if path == "/broken":
            return httpx.Response(500, text="boom")
        return httpx.Response(404, text="not found")
    return httpx.MockTransport(handler)


@pytest.fixture
def client():
    return build_client("https://catalog.wake.gov", transport=_transport())


def test_search_works_end_to_end(client):
    total, items = search_works(client, "Cryptonomicon")
    assert total == 1
    assert items[0]["grouped_work_id"] == "dfe46b1a-9917-0768-bc0e-e6dfad241b5f"


def test_work_records_end_to_end(client):
    records, wait = work_records(client, "ddb713e8-646d-8b2b-8ee6-02d926507b73")
    assert records == [{"record_id": "495749", "format": "Book"}]
    assert wait == {"copies": 9, "holds": 57}


def test_record_availability_end_to_end(client):
    copies = record_availability(client, "428175")
    assert len(copies) == 2


def test_http_error_raises_pika_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")
    c = build_client("https://catalog.wake.gov", transport=httpx.MockTransport(handler))
    with pytest.raises(PikaError):
        search_works(c, "anything")


def test_never_sends_head_and_uses_browser_headers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["headers"] = request.headers
        return httpx.Response(200, text=fixture("search_cryptonomicon.xml"))

    c = build_client("https://catalog.wake.gov", transport=httpx.MockTransport(handler))
    search_works(c, "Cryptonomicon")
    assert seen["method"] == "GET"
    assert "Mozilla" in seen["headers"]["user-agent"]
