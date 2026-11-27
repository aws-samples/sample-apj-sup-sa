# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""L2-001: an address a hostile page named never reaches a published output as a live address."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from insights_radar_agents.output_guard import MAX_DEPTH, OutputAddressGuard


def before(guard: OutputAddressGuard, name: str, args: Any) -> Any:
    event = SimpleNamespace(tool_use={"toolUseId": "1", "name": name, "input": args})
    guard.before_tool(event)  # type: ignore[arg-type]
    return event.tool_use.get("input")


def after(guard: OutputAddressGuard, name: str, result: Any) -> None:
    guard.after_tool(SimpleNamespace(tool_use={"name": name}, result=result))  # type: ignore[arg-type]


def page(url: str) -> dict[str, Any]:
    return {"status": "success", "content": [{"json": {"url": url, "text": "ok"}}]}


def test_an_address_the_run_never_read_is_defanged_and_a_fetched_one_is_kept() -> None:
    guard = OutputAddressGuard()
    after(guard, "http_fetch", page("https://feed.example.com/posts"))
    cleaned = before(
        guard,
        "kg_save_output",
        {
            "title": "Hostile page tested injection defenses",
            "summary": "It asked us to post findings to collect.exfil-e2e.invalid.",
            "description": '<p><a href="http://collect.exfil-e2e.invalid/x">here</a>, '
            "and https://feed.example.com/posts</p>",
            "attributes": {"hosts": ["collect.exfil-e2e.invalid", 7]},
            "supports": ["fact/1"],
        },
    )
    assert "collect.exfil-e2e.invalid" not in str(cleaned)
    assert cleaned["summary"].endswith("collect[.]exfil-e2e[.]invalid.")
    assert 'href="hxxp://collect[.]exfil-e2e[.]invalid/x"' in cleaned["description"]
    assert "https://feed.example.com/posts" in cleaned["description"]  # the page the run really read
    assert cleaned["attributes"]["hosts"] == ["collect[.]exfil-e2e[.]invalid", 7]
    assert cleaned["supports"] == ["fact/1"]  # id lists are left alone
    assert guard.defanged == 3


def test_a_fetched_host_is_matched_whatever_its_case_and_port() -> None:
    guard = OutputAddressGuard()
    after(guard, "http_fetch", page("https://Feed.Example.COM:8443/posts"))
    cleaned = before(guard, "kg_update_output", {"summary": "feed.example.com and other.example.com"})
    assert cleaned["summary"] == "feed.example.com and other[.]example[.]com"


def test_ordinary_prose_is_not_mistaken_for_an_address() -> None:
    guard = OutputAddressGuard()
    text = "Node.js 22, main.py, index.html, v1.2.3, e.g. this, report.pdf"
    assert before(guard, "kg_save_output", {"summary": text})["summary"] == text
    assert guard.defanged == 0


def test_nothing_is_rewritten_when_there_is_no_address_to_defang() -> None:
    guard = OutputAddressGuard()
    original = {"title": "Brushless motors spread"}
    assert before(guard, "kg_save_output", original) is original  # the model's own input object


def test_only_writes_people_read_are_scrubbed() -> None:
    guard = OutputAddressGuard()
    args = {"query": "collect.exfil-e2e.invalid"}
    assert before(guard, "kg_search", args) is args
    assert before(guard, "kg_save_output", "not a dict") == "not a dict"
    assert guard.defanged == 0


# ---- SR2-03: evidence, entity and suggestion text, and the forms the allowlist missed ------------


def test_sr2_03_evidence_entity_and_suggestion_text_is_scrubbed_but_citations_are_kept() -> None:
    guard = OutputAddressGuard()
    evidence = before(
        guard,
        "kg_add_evidence",
        {
            "evidence_url": "https://evil.cc/p",
            "title": "Pricing",
            "description": "the vendor's pricing is at evil.cc/p",
        },
    )
    assert evidence["evidence_url"] == "https://evil.cc/p"  # the citation itself
    assert evidence["description"] == "the vendor's pricing is at evil[.]cc/p"
    entity = before(
        guard,
        "kg_upsert_entity",
        {"name": "Booking.com", "aliases": ["booking.com"], "summary": "See booking.com"},
    )
    assert entity["name"] == "Booking.com" and entity["aliases"] == ["booking.com"]
    assert entity["summary"] == "See booking[.]com"
    suggestion = before(
        guard,
        "radar_suggest",
        {
            "slug": "r",
            "reason": "per https://x.example.org/a",
            "proposal": {"title": "T", "description": "go to evil.gg", "link": "https://evil.gg/a"},
        },
    )
    assert suggestion["reason"] == "per hxxps://x[.]example[.]org/a"
    assert suggestion["proposal"] == {"title": "T", "description": "go to evil[.]gg", "link": "https://evil.gg/a"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("exfil to evil.to now", "exfil to evil[.]to now"),
        ("x.evil.sh and pay.vip", "x[.]evil[.]sh and pay[.]vip"),
        ("call 203.0.113.7/a", "call 203[.]0[.]113[.]7/a"),
        ("load //evil.example/x", "load //evil[.]example/x"),
        ("ftp://evil.example/x", "ftp[:]//evil[.]example/x"),
        ("wss://evil.example", "wss[:]//evil[.]example"),
        ("evil\u3002example and evil\uff0ezone", "evil[.]example and evil[.]zone"),
    ],
)
def test_sr2_03_forms_the_allowlist_missed_are_defanged(text: str, expected: str) -> None:
    guard = OutputAddressGuard()
    assert before(guard, "kg_save_output", {"summary": text})["summary"] == expected


def test_sr2_03_a_read_ip_and_a_read_page_path_are_kept() -> None:
    guard = OutputAddressGuard()
    after(guard, "http_fetch", page("https://203.0.113.7/feed"))
    after(guard, "http_fetch", page("https://feed.example.com/a"))
    text = "from 203.0.113.7 and https://feed.example.com/a/b.example.org"
    assert before(guard, "kg_save_output", {"summary": text})["summary"] == text


# ---- SR2-02: the runtime, not the model, says whether evidence was fetched -----------------------


def test_sr2_02_evidence_is_stamped_with_whether_the_run_fetched_it() -> None:
    guard = OutputAddressGuard()
    after(guard, "http_fetch", page("https://www.reuters.com/x"))
    fetched = before(guard, "kg_add_evidence", {"evidence_url": "https://www.reuters.com/x", "fetched": False})
    assert fetched["fetched"] is True
    forged = before(guard, "kg_add_evidence", {"evidence_url": "https://techcrunch.com/y", "fetched": True})
    assert forged["fetched"] is False  # what the model passed is replaced
    for bad in (None, "not a url", "https://[::1"):
        assert before(guard, "kg_add_evidence", {"evidence_url": bad})["fetched"] is False


def test_a_deeply_nested_attribute_is_left_alone_past_the_depth_limit() -> None:
    guard = OutputAddressGuard()
    deep: Any = "collect.exfil-e2e.invalid"
    for _ in range(MAX_DEPTH + 2):
        deep = {"inner": deep}
    cleaned = before(guard, "kg_save_output", {"summary": "collect.exfil-e2e.invalid", "attributes": deep})
    assert guard.defanged == 1  # only the summary
    assert "collect.exfil-e2e.invalid" in str(cleaned["attributes"])


def test_only_a_successful_fetch_of_a_real_address_counts_as_read() -> None:
    guard = OutputAddressGuard()
    after(guard, "kg_search", page("https://ignored.example.com/x"))  # not a fetch
    after(guard, "http_fetch", {"status": "error", "content": [{"text": "refused"}]})
    after(guard, "http_fetch", {"status": "success", "content": [{"text": "fenced"}, "not a block"]})
    after(guard, "http_fetch", {"status": "success", "content": [{"json": {"text": "no url"}}]})
    after(guard, "http_fetch", "not a result")
    guard.allow("not-an-address")  # no host: nothing to record
    assert guard.read_hosts == set()


def graph_read(guard: OutputAddressGuard, name: str, result: Any, cut: bool = False) -> None:
    text = json.dumps(result)
    if cut:
        text = text[: len(text) // 2] + " [truncated]"
    after(guard, name, {"status": "success", "content": [{"text": text}]})


# ---- A3-001: an address written with character references is still an address -------------------


@pytest.mark.parametrize(
    "description",
    [
        '<a href="https&#58;//collect&#46;evil&#46;example/x?d=1">the source</a>',
        '<a href="https&colon;//collect&period;evil&period;example/x?d=1">the source</a>',
        '<a href="ht\ttps://collect.evil.example/x?d=1">the source</a>',  # a browser drops the tab
        '<a href="https:\\\\collect.evil.example/x?d=1">the source</a>',  # and reads \\ as //
        '<a href="http:/collect.evil.example/x?d=1">the source</a>',
        '<a href="http:collect.evil.example/x?d=1">the source</a>',
    ],
)
def test_a3_001_encoded_and_malformed_links_are_defanged_as_a_browser_would_read_them(description: str) -> None:
    from insights_radar.core.html import HtmlSanitizer

    guard = OutputAddressGuard()
    cleaned = before(guard, "kg_save_output", {"description": description})
    stored = HtmlSanitizer.sanitize(cleaned["description"])  # what the backend keeps and the app shows
    assert "collect.evil.example" not in stored and "href" not in stored, stored
    assert "collect[.]evil[.]example" in cleaned["description"]
    assert guard.defanged == 1 and guard.defanged_links == 1


def test_a3_001_references_in_plain_text_are_decoded_but_markup_ones_are_kept() -> None:
    guard = OutputAddressGuard()
    cleaned = before(
        guard,
        "kg_add_evidence",
        {"evidence_url": "https://a.example.com/", "summary": "at evil&#46;example &amp; &lt;b&gt;"},
    )
    assert cleaned["summary"] == "at evil[.]example &amp; &lt;b&gt;"


# ---- A3-002: citing evidence a graph read returned is not an injection ------------------------------


def test_a3_002_the_exact_evidence_address_a_graph_read_returned_may_be_cited() -> None:
    guard = OutputAddressGuard()
    graph_read(
        guard,
        "kg_neighbors",
        {"nodes": [{"id": "fact/1", "type": "fact", "evidence_url": "https://www.reuters.com/tech/x"}]},
    )
    cleaned = before(
        guard,
        "kg_save_output",
        {
            "description": '<p>Reported by <a href="https://www.reuters.com/tech/x">Reuters</a> '
            "(www.reuters.com), not https://www.reuters.com/tech/x?d=private-gap-text</p>",
        },
    )
    assert '<a href="https://www.reuters.com/tech/x">Reuters</a>' in cleaned["description"]
    assert "(www.reuters.com)" in cleaned["description"]  # its host, named in prose
    assert "hxxps://www[.]reuters[.]com/tech/x?d=private-gap-text" in cleaned["description"]  # never a variant
    assert guard.defanged == 1


def test_a3_002_citations_survive_a_result_cut_at_its_cap_and_bad_ones_are_ignored() -> None:
    guard = OutputAddressGuard()
    nodes = [{"type": "event", "evidence_url": f"https://news{i}.example.com/a"} for i in range(40)]
    graph_read(guard, "kg_search", {"items": [{"node": n} for n in nodes]}, cut=True)
    graph_read(guard, "kg_get", {"type": "fact", "evidence_url": "not an address"})
    graph_read(
        guard, "radar_get", [[[[[[[[[[[[[[[[[[[[[[{"evidence_url": "https://deep.example.com/"}]]]]]]]]]]]]]]]]]]]]]]
    )
    after(guard, "kg_recent", {"status": "error", "content": [{"text": "{}"}]})
    assert "https://news0.example.com/a" in guard.cited_urls and len(guard.cited_urls) < 40
    assert "https://deep.example.com/" not in guard.cited_urls  # past the walk's depth limit


# ---- P3-017: citation keys are exempt only where they belong ----------------------------------------


def test_p3_017_a_link_key_inside_free_form_attributes_is_scrubbed() -> None:
    guard = OutputAddressGuard()
    cleaned = before(
        guard,
        "kg_save_output",
        {
            "attributes": {
                "link": "https://evil.example/c?d=SECRET",
                "sources": [{"evidence_url": "https://evil2.example/x"}],
            }
        },
    )
    assert cleaned["attributes"] == {
        "link": "hxxps://evil[.]example/c?d=SECRET",
        "sources": [{"evidence_url": "hxxps://evil2[.]example/x"}],
    }
    proposal = before(guard, "radar_suggest", {"proposal": {"link": "https://vendor.example/p", "node_id": "fact/1"}})
    assert proposal["proposal"] == {"link": "https://vendor.example/p", "node_id": "fact/1"}  # the proposal's own


# ---- P3-020: what was read is recorded once it was read, and evidence is stamped per page ----------


def test_p3_020_a_browser_page_counts_once_it_opened() -> None:
    guard = OutputAddressGuard()

    def navigate(url: str, status: str) -> None:
        event = SimpleNamespace(
            tool_use={"name": "browser", "input": {"browser_input": {"action": {"type": "navigate", "url": url}}}},
            result={"status": status, "content": []},
        )
        guard.after_tool(event)  # type: ignore[arg-type]

    navigate("https://opened.example.com/p", "success")
    navigate("https://refused.example.com/p", "error")
    guard.after_tool(SimpleNamespace(tool_use={"name": "browser", "input": "x"}, result={"status": "success"}))  # type: ignore[arg-type]
    assert guard.read_hosts == {"opened.example.com"}


def test_p3_020_evidence_is_fetched_only_for_the_exact_page_the_run_read() -> None:
    guard = OutputAddressGuard()
    after(guard, "http_fetch", page("https://www.reuters.com/feed"))
    stamped = before(guard, "kg_add_evidence", {"evidence_url": "https://www.reuters.com/fabricated"})
    assert stamped["fetched"] is False  # same host, never read
    again = before(guard, "kg_add_evidence", {"evidence_url": "https://WWW.reuters.com:443/feed#top"})
    assert again["fetched"] is True


# ---- P3-021 and A3-015: names that look like hosts, and links in entity names ------------------------


@pytest.mark.parametrize(
    "text", ["Character.AI", "ASP.NET", "os.path.join", "press@vendor.com", "Node.JS", "EXAMPLE.COM support"]
)
def test_p3_021_names_and_identifiers_are_not_defanged(text: str) -> None:
    guard = OutputAddressGuard()
    assert before(guard, "kg_save_output", {"summary": text})["summary"] == text


def test_p3_021_an_entity_the_run_read_may_be_named() -> None:
    guard = OutputAddressGuard()
    graph_read(
        guard,
        "kg_search",
        {"items": [{"node": {"type": "organization", "title": "Booking.com", "aliases": ["Kayak.com"]}}]},
    )
    cleaned = before(guard, "kg_save_output", {"summary": "Booking.com and kayak.com, not evil.com"})
    assert cleaned["summary"] == "Booking.com and kayak.com, not evil[.]com"


def test_a3_015_links_in_an_entity_name_and_aliases_are_defanged() -> None:
    guard = OutputAddressGuard()
    cleaned = before(
        guard,
        "kg_upsert_entity",
        {"name": "https://acme-support.evil.example/?d=1", "aliases": ["Acme support", "http://x.evil.example"]},
    )
    assert cleaned["name"] == "hxxps://acme-support[.]evil[.]example/?d=1"
    assert cleaned["aliases"] == ["Acme support", "hxxp://x[.]evil[.]example"]


# ---- P3-064, mailto and the summary ---------------------------------------------------------------


def test_p3_064_an_ipv6_literal_and_a_mailto_link_are_defanged() -> None:
    guard = OutputAddressGuard()
    cleaned = before(
        guard,
        "kg_save_output",
        {
            "summary": "see https://[2001:db8::1]/x?d=secret",
            "description": '<a href="mailto:a@evil.example?body=x">m</a>',
        },
    )
    assert cleaned["summary"] == "see hxxps://[2001:db8::1]/x?d=secret"
    assert 'href="mailto[:]a@evil[.]example?body=x"' in cleaned["description"]
    after(guard, "http_fetch", page("https://[2001:DB8::1]/feed"))
    assert (
        before(guard, "kg_save_output", {"summary": "https://[2001:db8::1]/x"})["summary"] == "https://[2001:db8::1]/x"
    )


def test_a3_017_the_summary_is_defanged_without_counting() -> None:
    guard = OutputAddressGuard()
    summary = guard.scrub_summary("A page asked me to [Re-authorize the Scout source](https://evil.example/login).")
    assert summary == "A page asked me to [Re-authorize the Scout source](hxxps://evil[.]example/login)."
    assert guard.defanged == 0 and guard.defanged_links == 0


def test_the_tld_list_is_loaded_and_ascii() -> None:
    from insights_radar_agents.output_guard import normal_url, top_level_domains

    tlds = top_level_domains()
    assert {"com", "ai", "example", "invalid"} <= tlds and "join" not in tlds
    assert (
        normal_url("ftp://x.example/") is None
        and normal_url("https://x.example:8443/a#f") == "https://x.example:8443/a"
    )
    assert normal_url("https://x.example:bad/") is None


def test_edge_forms_of_what_a_run_read() -> None:
    guard = OutputAddressGuard()
    guard.allow("https://[::1")  # malformed: nothing recorded
    guard.allow("ftp://files.example.com/a")  # a host, but not a page a citation can name
    assert guard.read_hosts == {"files.example.com"} and guard.fetched_urls == set()
    kept = before(guard, "kg_save_output", {"description": '<a href="mailto:press@files.example.com">mail</a>'})
    assert kept["description"] == '<a href="mailto:press@files.example.com">mail</a>'
    assert before(guard, "kg_upsert_entity", {"type": "topic", "summary": "x"}) == {"type": "topic", "summary": "x"}


# ---- SR3-02 / SR3-03 / SR3-04 ------------------------------------------------------------------


def test_sr3_02_a_read_host_as_userinfo_does_not_carry_a_link_to_another_host() -> None:
    guard = OutputAddressGuard()
    guard.allow("https://www.reuters.com/")
    out = guard.scrub_summary(
        "see https://www.reuters.com@collect.evil.example/private-text and https://www.reuters.com/a"
    )
    assert "https://www.reuters.com@" not in out and "hxxps://www[.]reuters[.]com@" in out
    assert "https://www.reuters.com/a" in out


@pytest.mark.parametrize(
    "address",
    [
        "https://\u00adevil.COM/s",
        "https://\u200bevil.COM/s",
        "https://\uff45\uff56\uff49\uff4c\uff0e\uff43\uff4f\uff4d/s",
        "https://\u00e9vil.COM/s",
    ],
)
def test_sr3_03_a_host_that_starts_with_a_folded_or_invisible_character_is_defanged(address: str) -> None:
    guard = OutputAddressGuard()
    out = guard.scrub_summary(f"report: {address}")
    assert "https://" not in out and "hxxps://" in out


def test_sr3_03_a_read_host_written_with_a_soft_hyphen_is_still_the_read_host() -> None:
    guard = OutputAddressGuard()
    guard.allow("https://www.reuters.com/")
    assert guard.scrub_summary("https://\u00adwww.reuters.com/a") == "https://\u00adwww.reuters.com/a"


def test_sr3_04_a_cited_address_with_userinfo_is_not_kept() -> None:
    guard = OutputAddressGuard()
    guard.cited_urls.add("https://notes.evil.example/r")
    guard.cited_hosts.add("notes.evil.example")
    out = guard.scrub_summary("[full](https://Gap-no-SSO:x@notes.evil.example/r) and https://notes.evil.example/r")
    assert "(https://Gap-no-SSO" not in out and "(hxxps://Gap-no-SSO" in out
    assert out.endswith(" and https://notes.evil.example/r")


def test_sr3_05_a_mailto_to_a_cited_host_is_defanged_and_one_to_a_read_host_kept() -> None:
    guard = OutputAddressGuard()
    guard.allow("https://www.reuters.com/")
    guard.cited_hosts.add("notes.evil.example")
    out = guard.scrub_summary("mailto:DATA@notes.evil.example and mailto:desk@www.reuters.com")
    assert "mailto[:]DATA@" in out and "mailto:desk@www.reuters.com" in out
