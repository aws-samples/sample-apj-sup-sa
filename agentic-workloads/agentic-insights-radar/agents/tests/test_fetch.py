# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from typing import Any

from insights_radar.core.fetch import FetchError

from insights_radar_agents.fetch import MAX_LINKS, fetch_tool


class Fetcher:
    def __init__(self, page: dict[str, Any] | Exception) -> None:
        self.page = page

    def fetch(self, url: str) -> dict[str, Any]:
        if isinstance(self.page, Exception):
            raise self.page
        return {**self.page, "url": url}


def call(fetcher: Any, args: Any) -> Any:
    return fetch_tool(fetcher)._tool_func({"toolUseId": "1", "name": "http_fetch", "input": args})


def test_tool_returns_page_with_capped_links() -> None:
    ok = call(Fetcher({"text": "hello", "links": [f"https://a/{i}" for i in range(500)]}), {"url": "https://a"})
    assert ok["status"] == "success" and ok["content"][0]["json"]["text"] == "hello"
    assert len(ok["content"][0]["json"]["links"]) == MAX_LINKS


def test_tool_errors() -> None:
    assert "url or source_id is required" in call(Fetcher({}), {})["content"][0]["text"]
    assert call(Fetcher({}), "x")["status"] == "error"
    refused = call(Fetcher(FetchError("refused https://10.0.0.1")), {"url": "https://10.0.0.1"})
    assert refused["status"] == "error" and "refused" in refused["content"][0]["text"]


# ---- CR-01: a keyed source is fetched by id, and the key never reaches the model ----------------

KEYED = "https://newsapi.example.com/v2/everything?q=ai&apiKey=SECRETKEY123"


class Recorder:
    def __init__(self, error: Exception | None = None, echo: bool = True) -> None:
        self.urls: list[str] = []
        self.error = error
        self.echo = echo

    def close(self) -> None:
        pass

    def fetch(self, url: str) -> dict[str, Any]:
        self.urls.append(url)
        if self.error:
            raise self.error
        return {
            "url": url,
            "status": 200,
            "title": "t",
            "text": f"request was {url}" if self.echo else "ok",
            "links": [url + "&page=2"],
        }


def call_with(fetcher: Any, args: Any, resolve: Any) -> Any:
    return fetch_tool(fetcher, resolve)._tool_func({"toolUseId": "1", "name": "http_fetch", "input": args})


def test_cr01_a_source_is_fetched_with_its_stored_key_and_the_key_is_scrubbed() -> None:
    fetcher = Recorder()
    out = call_with(fetcher, {"source_id": "src123", "query": "page=2&author=jdoe"}, lambda sid: KEYED)
    assert fetcher.urls == ["https://newsapi.example.com/v2/everything?q=ai&apiKey=SECRETKEY123&page=2&author=jdoe"]
    page = out["content"][0]["json"]
    assert out["status"] == "success" and "SECRETKEY123" not in str(page)
    # A link adding fields to the stored query is another page: it keeps its address, the key scrubbed (r15).
    assert page["url"] == "source:src123" and page["links"][0].startswith(
        "https://newsapi.example.com/v2/everything?REDACTED&page=2"
    )
    assert call_with(Recorder(), {"source_id": "src123"}, lambda sid: KEYED)["status"] == "success"


def test_cr01_a_failed_source_fetch_does_not_echo_its_address() -> None:
    cut = FetchError(f"{KEYED[:60]} returned HTTP 401")  # the fetcher cuts addresses, possibly mid-key
    out = call_with(Recorder(cut), {"source_id": "src123"}, lambda sid: KEYED)
    assert out["status"] == "error" and out["content"][0]["text"] == "<source address> returned HTTP 401"


def test_a_source_fetch_stamps_one_attempt_per_outcome_per_run() -> None:
    marked: list[tuple[str, bool]] = []
    mark = lambda sid, ok: marked.append((sid, ok))  # noqa: E731
    tool = fetch_tool(Recorder(), lambda sid: KEYED, mark)  # type: ignore[arg-type]
    for n in range(3):  # paging through a feed: one stamp, not one per page
        out = tool._tool_func({"toolUseId": str(n), "name": "http_fetch", "input": {"source_id": "src123"}})
        assert out["status"] == "success"
    assert marked == [("src123", True)]
    failing = fetch_tool(Recorder(FetchError("HTTP 500")), lambda sid: KEYED, mark)  # type: ignore[arg-type]
    for n in range(2):
        assert (
            failing._tool_func({"toolUseId": str(n), "name": "http_fetch", "input": {"source_id": "s2"}})["status"]
            == "error"
        )
    assert marked == [("src123", True), ("s2", False)]  # tried, not read; once
    # A plain url fetch is not a source attempt.
    fetch_tool(Recorder(), None, mark)._tool_func(  # type: ignore[arg-type]
        {"toolUseId": "3", "name": "http_fetch", "input": {"url": "https://a.example.com/"}}
    )
    assert len(marked) == 2


def test_a_read_after_a_failed_attempt_in_the_same_run_is_stamped() -> None:
    marked: list[tuple[str, bool]] = []
    flaky = Recorder(FetchError("timeout"))
    tool = fetch_tool(flaky, lambda sid: KEYED, lambda sid, ok: marked.append((sid, ok)))  # type: ignore[arg-type]
    tool._tool_func({"toolUseId": "1", "name": "http_fetch", "input": {"source_id": "s1"}})
    flaky.error = None
    tool._tool_func({"toolUseId": "2", "name": "http_fetch", "input": {"source_id": "s1"}})
    assert marked == [("s1", False), ("s1", True)]


def test_a_failed_read_stamp_never_fails_the_fetch(caplog: Any) -> None:
    def broken(sid: str, ok: bool) -> None:
        raise RuntimeError("backend down")

    tool = fetch_tool(Recorder(), lambda sid: KEYED, broken)  # type: ignore[arg-type]
    out = tool._tool_func({"toolUseId": "1", "name": "http_fetch", "input": {"source_id": "src123"}})
    assert out["status"] == "success" and "source_mark_read failed: RuntimeError" in caplog.text


def test_cr01_source_errors() -> None:
    def missing(sid: str) -> str:
        raise RuntimeError("source nope not found")

    assert (
        "is unavailable: source nope not found"
        in call_with(Recorder(), {"source_id": "nope"}, missing)["content"][0]["text"]
    )
    assert "no configured sources" in call_with(Recorder(), {"source_id": "x"}, None)["content"][0]["text"]
    typed = call_with(Recorder(), {"url": "https://newsapi.example.com/v2?apiKey=REDACTED"}, lambda s: KEYED)
    assert typed["status"] == "error" and "source_id" in typed["content"][0]["text"]
    plain = Recorder(FetchError("https://a.example.com/x returned HTTP 404"))
    assert "https://a.example.com/x" in call_with(plain, {"url": "https://a.example.com/x"}, None)["content"][0]["text"]


def test_sr04_http_fetch_refuses_an_address_that_packs_data_into_its_path() -> None:
    fetcher = Recorder()
    out = call_with(fetcher, {"url": "https://attacker.example/" + "aGVsbG8" * 100}, None)
    assert out["status"] == "error" and "too long" in out["content"][0]["text"] and fetcher.urls == []


def test_p2077_a_source_fetch_passes_the_same_address_budget() -> None:
    """The extra query is up to 1,000 characters of model-chosen text. Without the budget, a
    configured source host is an unmetered way out: "refresh source 3 with q=<summary>" (P2-077)."""
    fetcher = Recorder()
    out = call_with(fetcher, {"source_id": "src123", "query": "q=" + "a" * 600}, lambda sid: KEYED)
    assert out["status"] == "error"
    assert "http_fetch refused for source 'src123'" in out["content"][0]["text"]
    assert "too long" in out["content"][0]["text"]
    assert fetcher.urls == []  # nothing was requested
    assert "SECRETKEY123" not in out["content"][0]["text"]
    short = call_with(Recorder(), {"source_id": "src123", "query": "page=2"}, lambda sid: KEYED)
    assert short["status"] == "success"


# ---- backend r3 B3-016: a path credential never reaches the model ---------------------------------

WEBHOOK = "https://hooks.example.com/services/T000/B000/XSECRETWEBHOOK"
FEED = "https://feeds.example.com/feeds/9f3cPRIVATEFEEDTOKEN/rss"


def test_b3_016_a_source_fetch_result_names_the_source_not_its_address() -> None:
    for address in (WEBHOOK, FEED, "https://api.example.com/v1?appid=K7x9Q2a"):
        out = call_with(Recorder(), {"source_id": "src123"}, lambda sid, a=address: a)
        page = out["content"][0]["json"]
        assert out["status"] == "success" and page["url"] == "source:src123"
        assert "XSECRETWEBHOOK" not in str(page) and "PRIVATEFEEDTOKEN" not in str(page) and "K7x9Q2a" not in str(page)


def test_b3_016_a_bare_path_token_echoed_by_the_page_is_scrubbed() -> None:
    class Echo(Recorder):
        def fetch(self, url: str) -> dict[str, Any]:
            return {"url": url, "status": 200, "title": "t", "text": "your token is 9f3cPRIVATEFEEDTOKEN", "links": []}

    page = call_with(Echo(), {"source_id": "src123"}, lambda sid: FEED)["content"][0]["json"]
    assert "PRIVATEFEEDTOKEN" not in page["text"]


def test_p3_018_a_source_fetch_never_hands_back_a_credential_in_the_path() -> None:
    from insights_radar.core.urls import UrlGuard

    source_path_secrets = UrlGuard.source_path_pairs

    private_feed = "https://feeds.example.com/private/a1b2c3d4e5f6g7h8i9/rss"

    class Echo(Recorder):
        def fetch(self, url: str) -> dict[str, Any]:
            self.urls.append(url)
            return {
                "url": url,
                "text": f"feed {url}",
                "links": [f"{private_feed.rsplit('/', 1)[0]}/item1", "https://news.example.com/a"],
            }

    fetcher = Echo()
    tool = fetch_tool(fetcher, lambda sid: private_feed)  # type: ignore[arg-type]
    result = tool._tool_func({"toolUseId": "1", "input": {"source_id": "s1"}})  # type: ignore[attr-defined]
    page = result["content"][0]["json"]
    assert fetcher.urls == [private_feed] and "a1b2c3d4e5f6g7h8i9" not in str(page)
    assert page["url"] == "source:s1"  # B3-016: a source fetch names the source, never its address
    # The token is gone from every link (scrubbed by name or replaced as the feed's path); other links stay.
    assert page["links"][1] == "https://news.example.com/a"
    assert page["links"][0].startswith("https://feeds.example.com/") and "a1b2c3d4e5f6g7h8i9" not in page["links"][0]

    # A public feed's short path is no secret, nor its directory, where its articles are (r14).
    def replace_all(value: list[str] | str, pairs: list[Any]) -> Any:
        def one(text: str) -> str:
            for pattern, stand_in in pairs:
                text = pattern.sub(stand_in, text) if hasattr(pattern, "sub") else text.replace(pattern, stand_in)
            return text

        return [one(v) for v in value] if isinstance(value, list) else one(value)

    assert source_path_secrets("https://www.reuters.com/technology/rss", "s2") == []
    # A deep listing's articles stay fetchable: its path is hidden only where it stands alone.
    listing = source_path_secrets("https://aws.amazon.com/blogs/machine-learning/en/", "s4")
    links = [
        "https://aws.amazon.com/blogs/machine-learning/en/post-x/",
        "https://aws.amazon.com/blogs/machine-learning/en",
        "https://aws.amazon.com/blogs/machine-learning/en?page=2",
    ]
    assert replace_all(links, listing) == [
        links[0],
        "https://aws.amazon.com/[source s4 path]",
        links[2],  # addressed by query: another page, which must stay fetchable (code review r16)
    ]
    assert source_path_secrets("https://x.example.com/", "s3") == []


def test_a_source_fetch_hides_its_address_even_with_a_credential_name_the_guard_does_not_know() -> None:
    keyed = "https://api.example.gov/notices?accessKey=Fake0Key0000"

    class Page(Recorder):
        def fetch(self, url: str) -> dict[str, Any]:
            self.urls.append(url)
            return {"url": url, "text": "list", "links": [f"{keyed}#main", "https://api.example.gov/notices?page=2"]}

    tool = fetch_tool(Page(), lambda sid: keyed)  # type: ignore[arg-type]
    page = tool._tool_func({"toolUseId": "1", "input": {"source_id": "s1"}})["content"][0]["json"]  # type: ignore[attr-defined]
    # The source's own pages stay reachable by id (http_fetch with source_id and query), never by address.
    assert page["links"] == ["source:s1#main", "https://api.example.gov/notices?page=2"]
    assert "Fake0Key0000" not in str(page)


def test_r14_a_credential_the_guard_does_not_know_is_hidden_when_the_page_echoes_it_reordered() -> None:
    keyed = "https://api.example.gov/notices?lang=en&blob=Ab%2Fc%2BK9zQ7rT2wX5"

    class Page(Recorder):
        def fetch(self, url: str) -> dict[str, Any]:
            self.urls.append(url)
            return {
                "url": url,
                "text": "next: ?blob=Ab/c+K9zQ7rT2wX5&lang=en",
                "links": ["https://api.example.gov/notices?blob=Ab%2Fc%2BK9zQ7rT2wX5&lang=en&page=2"],
            }

    tool = fetch_tool(Page(), lambda sid: keyed)  # type: ignore[arg-type]
    page = tool._tool_func({"toolUseId": "1", "input": {"source_id": "s1"}})["content"][0]["json"]  # type: ignore[attr-defined]
    assert "K9zQ7rT2wX5" not in str(page)


def test_r15_plain_query_values_are_no_credentials_and_bare_tokens_are() -> None:
    """Code review r15: ?category=technology must not garble the page or its article links; ?9f3cTOKEN and a
    short mixed value under an unknown name (k=Ab3x9Q2) are credentials."""

    class Page(Recorder):
        def fetch(self, url: str) -> dict[str, Any]:
            self.urls.append(url)
            return {
                "url": url,
                "text": "Latest technology news. Subscribe: /feed/page/2?9f3cPRIVATEFEEDTOKEN12 or ?page=2&k=Ab3x9Q2",
                "links": [
                    "https://example.com/technology/chips-2026",
                    "https://example.com/news?category=technology&article=5",
                ],
            }

    plain = fetch_tool(Page(), lambda sid: "https://example.com/news?category=technology")  # type: ignore[arg-type]
    page = plain._tool_func({"toolUseId": "1", "input": {"source_id": "s1"}})["content"][0]["json"]  # type: ignore[attr-defined]
    assert page["links"] == [
        "https://example.com/technology/chips-2026",
        "https://example.com/news?category=technology&article=5",
    ]
    assert page["text"].startswith("Latest technology news.")
    for address, secret in (
        ("https://host.example/feed.xml?9f3cPRIVATEFEEDTOKEN12", "9f3cPRIVATEFEEDTOKEN12"),
        ("https://host.example/feed?page=1&k=Ab3x9Q2", "Ab3x9Q2"),
    ):
        tool = fetch_tool(Page(), lambda sid, a=address: a)  # type: ignore[arg-type]
        out = tool._tool_func({"toolUseId": "1", "input": {"source_id": "s1"}})["content"][0]["json"]  # type: ignore[attr-defined]
        assert secret not in str(out), address
