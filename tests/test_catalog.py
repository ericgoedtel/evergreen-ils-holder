import pytest

from evergreen_holder.catalog import find_orgs, org_names, parse_isbns, search_bibs


def test_find_orgs_matches_name_case_insensitively_with_ancestors(fake_client):
    hits = find_orgs(fake_client, "hocutt")
    assert len(hits) == 1
    h = hits[0]
    assert h["id"] == 501
    assert h["name"] == "Hocutt-Ellington Memorial Library"
    assert [a["id"] for a in h["ancestors"]] == [1, 500]
    assert h["ancestors"][0]["name"] == "NC Cardinal"
    assert h["ancestors"][1]["name"] == "Clayton Library System"


def test_find_orgs_matches_shortname(fake_client):
    hits = find_orgs(fake_client, "FONTANA_HQ")
    assert hits and hits[0]["shortname"] == "FONTANA_HQ"


def test_find_orgs_no_match(fake_client):
    assert find_orgs(fake_client, "zzzznotalibrary") == []


def test_org_names(fake_client):
    names = org_names(fake_client, [501, 500, 1])
    assert names == {501: "Hocutt-Ellington Memorial Library", 500: "Clayton Library System", 1: "NC Cardinal"}


MARC = """<record xmlns="http://www.loc.gov/MARC21/slim">
<datafield tag="020" ind1=" " ind2=" "><subfield code="a">039335668X</subfield><subfield code="q">(paperback)</subfield></datafield>
<datafield tag="020" ind1=" " ind2=" "><subfield code="a">9780393635522 (hardcover)</subfield></datafield>
<datafield tag="020" ind1=" " ind2=" "><subfield code="a">1234567890</subfield></datafield>
<datafield tag="020" ind1=" " ind2=" "><subfield code="z">0000000000</subfield></datafield>
</record>"""


def test_parse_isbns_reads_a_and_q_subfields():
    assert parse_isbns(MARC) == [
        {"isbn": "039335668X", "label": "paperback"},
        {"isbn": "9780393635522", "label": ""},
        {"isbn": "1234567890", "label": ""},
    ]


@pytest.fixture
def trimmed_client(fake_client):
    k = fake_client.key("open-ils.search.biblio.multiclass.query",
                        ({"limit": 25, "org_unit": 1}, "the overstory powers search_format(book) -item_form(d)", 1))
    res = dict(fake_client.canned[k][0])
    res["ids"] = [row for row in res["ids"] if row[0] in (12547531, 12834206)]
    fake_client.canned[k] = [res]
    return fake_client


def test_search_bibs_shapes_results(trimmed_client):
    results = search_bibs(trimmed_client, "the overstory powers", 501, 500, 1)
    by_id = {r["bib_id"]: r for r in results}
    main = by_id[12547531]
    assert main["title"].lower().startswith("the overstory")
    assert main["author"].startswith("Powers")
    assert main["year"] == "2018"
    assert {"isbn": "039335668X", "label": "paperback"} in main["isbns"]
    assert {"isbn": "039363552X", "label": "hardcover"} in main["isbns"]
    assert main["formats"] == ["hardcover", "paperback"]
    assert main["large_print"] is False
    assert set(main["copies"]) == {"branch", "system", "consortium"}
    assert main["copies"]["consortium"]["total"] > main["copies"]["system"]["total"] >= main["copies"]["branch"]["total"]

    pb = by_id[12834206]
    assert pb["formats"] == ["paperback"]
    assert pb["copies"]["branch"] == {"available": 0, "total": 0}


def test_search_bibs_uses_format_filters_and_result_cap(trimmed_client):
    search_bibs(trimmed_client, "the overstory powers", 501, 500, 1)
    _, method, params = trimmed_client.calls[0]
    assert method == "open-ils.search.biblio.multiclass.query"
    assert params[0] == {"limit": 25, "org_unit": 1}
    assert params[1] == "the overstory powers search_format(book) -item_form(d)"


def test_search_bibs_empty(fake_client):
    fake_client.canned[fake_client.key("open-ils.search.biblio.multiclass.query",
                                       ({"limit": 25, "org_unit": 1}, "nothing search_format(book) -item_form(d)", 1))] = [
        {"count": 0, "ids": []}]
    assert search_bibs(fake_client, "nothing", 501, 500, 1) == []
