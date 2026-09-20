from evergreen_holder.catalog import find_orgs, org_names


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
