# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Digest rendering for Slack and email: escaping of untrusted text, links to the app only, Slack's limits."""

from __future__ import annotations

import json
from typing import Any

import pytest

from insights_radar.insights.delivery_render import (
    LINK_MAX,
    LIST_SECTIONS,
    MAX_BLOCKS,
    SAFE_PAYLOAD,
    SECTION_MAX,
    SLACK_SUMMARY_MAX,
    DigestContent,
    EmailRenderer,
    SlackRenderer,
    _Group,
    plain,
    short,
    slack_escape,
    unlinked,
)

UID = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"


def row(i: int, title: str = "Title", kind: str = "signal", summary: str = "A summary") -> dict[str, Any]:
    return {"id": f"{kind}/{UID[:-2]}{i:02d}", "type": kind, "title": f"{title} {i}", "summary": summary}


def content(digest: dict[str, Any], app_url: str = "https://app.example.com", **kw: Any) -> DigestContent:
    base = {
        "since": "2026-10-01T08:00:00.000Z",
        "until": "2026-10-02T08:00:00.000Z",
        "new": {},
        "more": {},
        "labels": {},
        "watched": [],
    }
    d = base | digest
    if "total" not in d:
        d["total"] = sum(len(v) for v in d["new"].values()) + sum(w["count"] for w in d["watched"])
    return DigestContent(
        kw.pop("app_name", "Example Radar"),
        app_url,
        d,
        kw.pop("labels", {"signal": "Signals"}),
        kw.pop("order", ("signal", "trend")),
    )


EVIL = "<!channel> <@U0123456789> <https://evil.example|click> & \"quote\" 'x' <script>alert(1)</script>\u202e"


def test_slack_escapes_titles_and_links_only_to_the_app() -> None:
    msg = SlackRenderer().render(content({"new": {"signal": [row(1, EVIL)]}}))
    text = json.dumps(msg)
    assert "<!channel>" not in text and "<@U0123456789>" not in text and "<https://evil" not in text
    assert "&lt;!channel&gt;" in text and "&amp;" in text and "\u202e" not in text
    section = msg["blocks"][2]["text"]["text"]
    assert section.startswith("*Signals* (1)\n\u2022 <https://app.example.com/insights/nodes/signal/")
    assert "|&lt;!channel&gt;" in section
    assert msg["blocks"][0] == {"type": "header", "text": {"type": "plain_text", "text": "Example Radar digest"}}
    assert "1 new record since 01 Oct 2026 08:00 UTC" in msg["blocks"][1]["elements"][0]["text"]
    assert msg["text"] == "Example Radar: 1 new record since 01 Oct 2026 08:00 UTC"
    assert msg["blocks"][-1]["elements"][0]["text"].startswith("<https://app.example.com/whats-new|Open the digest>")


def test_slack_without_an_app_url_has_no_links() -> None:
    msg = SlackRenderer().render(
        content({"new": {"signal": [row(1)]}, "more": {"signal": True}, "since": "bad"}, app_url="")
    )
    text = json.dumps(msg)
    assert "<http" not in text and "Title 1" in text and "and more in the app" in text
    assert "since" not in msg["blocks"][1]["elements"][0]["text"]


def sections(msg: dict[str, Any]) -> list[str]:
    return [b["text"]["text"] for b in msg["blocks"] if b["type"] == "section"]


def test_bare_titles_are_unlinked_where_no_client_shows_a_label() -> None:
    """Scan cycle 1, finding 7: a title is only safe inside a link label; bare, it is unlinked like a summary."""
    r = row(1, "Go to https://evil.example now", summary="")
    slack = json.dumps(SlackRenderer().render(content({"new": {"signal": [r]}}, app_url="")))
    assert "https[:]//evil[.]example" in slack and "evil.example" not in slack
    linked = json.dumps(SlackRenderer().render(content({"new": {"signal": [r]}})))
    assert "|Go to https://evil.example now 1>" in linked  # a label: shown as written, never clickable itself
    mail = EmailRenderer().render(content({"new": {"signal": [r]}}), "https://u", "https://m")
    assert "- Go to https[:]//evil[.]example now 1 <https://app.example.com/" in mail.text
    assert ">Go to https://evil.example now 1</a>" in mail.html
    bare = EmailRenderer().render(content({"new": {"signal": [r]}}, app_url=""), "https://u", "https://m")
    assert "evil.example" not in bare.html and "evil.example" not in bare.text
    w = {"node": {"id": "x", "title": "Watch evil.example"}, "new": [], "count": 1}
    watched = EmailRenderer().render(content({"watched": [w]}), "https://u", "https://m")
    assert "Watch evil[.]example: 1 new" in watched.text


def test_slack_never_cuts_a_link_with_a_long_app_url() -> None:
    """Scan cycle 2, finding 9: the tail with the app's own (long) link always fits, so the 3,000-character limit
    never lands inside a link or an entity."""
    app = "https://insights-radar.analytics.example-company.com/" + "deep/" * 30
    for title_len in range(100, 150, 7):
        rows = [row(i, "&" * title_len, summary="&" * 300) for i in range(40)]
        msg = SlackRenderer().render(content({"new": {"signal": rows}, "more": {"signal": True}}, app_url=app))
        for section in sections(msg)[:-1] if len(msg["blocks"]) == MAX_BLOCKS else sections(msg):
            assert len(section) <= SECTION_MAX
            assert section.count("<") == section.count(">") and not section.rstrip(">").endswith("&am")
        assert len(msg["blocks"]) <= MAX_BLOCKS


def test_slack_shows_a_short_unlinked_summary_under_each_title() -> None:
    long = "The ministry issued rule 12/2026 on import duties for steel. " * 10
    rows = [row(1, summary=long), row(2, summary=""), row(3, summary=f"See https://evil.example/x and {EVIL}")]
    texts = sections(SlackRenderer().render(content({"new": {"signal": rows}})))
    lines = texts[0].split("\n")
    assert lines[1].startswith("\u2022 <https://app.example.com/insights/nodes/signal/")
    assert lines[1].endswith("|Title 1>")
    assert lines[2].startswith("The ministry issued") and lines[2].endswith("\u2026")
    assert len(lines[2]) <= SLACK_SUMMARY_MAX and not lines[2][:-1].endswith(" ")
    assert lines[3].endswith("|Title 2>") and lines[4].startswith("\u2022 ")  # no summary, no empty line
    evil = lines[5]
    assert "https[:]//evil[.]example/x" in evil and "evil.example" not in evil
    assert "<!channel>" not in evil and "<@U" not in evil and "&lt;https[:]//evil[.]example|click&gt;" in evil
    assert evil.count("<") == 0 and "\u202e" not in evil


def test_unlinked_and_short() -> None:
    assert unlinked("mail a@gov.sg or http://x.co.id/p, U.S. rules, e.g. 1.5bn, v2.0, Rp1.500.000") == (
        "mail a@gov[.]sg or http[:]//x[.]co[.]id/p, U.S. rules, e.g. 1.5bn, v2.0, Rp1.500.000"
    )
    # Scan cycle 1, finding 3: any script, CJK full stops, punycode and bare IPv4 addresses.
    probe = (
        "b\u00fccher.de, \u043f\u0440\u0438\u043c\u0435\u0440.\u0440\u0444, p\u0430ypal.com, "
        "evil\u3002com, a\uff0eb.cd, xn--p1ai.xn--p1ai, 1.2.3.4/x"
    )
    assert unlinked(probe) == (
        "b\u00fccher[.]de, \u043f\u0440\u0438\u043c\u0435\u0440[.]\u0440\u0444, p\u0430ypal[.]com, "
        "evil[.]com, a\uff0eb[.]cd, xn--p1ai[.]xn--p1ai, 1[.]2[.]3[.]4/x"
    )
    assert unlinked("ftp://10.0.0.1") == "ftp[:]//10[.]0[.]0[.]1"
    assert unlinked("version 1.2.3.4.5 and 999.1.1") == "version 1.2.3.4.5 and 999.1.1"
    assert short("word " * 100, 50).endswith("word\u2026") and len(short("word " * 100, 50)) <= 50
    assert short("x" * 100, 50) == "x" * 49 + "\u2026"  # no space to cut on
    assert short("fits", 50) == "fits" and short("", 50) == "" and short(None, 50) == ""


def test_lists_order_labels_and_custom_kinds() -> None:
    c = content(
        {
            "new": {
                "insight:pricing_move": [row(1, kind="insight")],
                "trend": [row(2, kind="trend")],
                "signal": [row(3)],
                "gap": [],
            }
        },
        labels={"trend": "Shifts"},
    )
    assert [(k, label) for k, label, _, _ in c.lists()] == [
        ("signal", "Signal"),
        ("trend", "Shifts"),
        ("insight:pricing_move", "Pricing move"),
    ]
    assert (
        c.record_url("nonsense") == ""
        and c.record_url("a b/c?d") == "https://app.example.com/insights/nodes/a%20b/c%3Fd"
    )
    assert DigestContent("", "", {"total": 0}).name == "Insights Radar"


def test_email_escapes_everything_untrusted() -> None:
    c = content({"new": {"signal": [row(1, EVIL, summary=EVIL)]}, "more": {"signal": True}}, app_name='A<b>"')
    mail = EmailRenderer().render(c, "https://app.example.com/unsubscribe?t=a&b", "https://app.example.com/whats-new")
    assert "<script>" not in mail.html and "&lt;script&gt;" in mail.html
    assert "&quot;quote&quot;" in mail.html and "&#x27;x&#x27;" in mail.html
    assert 'href="https://app.example.com/unsubscribe?t=a&amp;b"' in mail.html
    assert 'href="https://evil' not in mail.html
    assert "A&lt;b&gt;&quot; digest" in mail.html
    assert mail.subject == 'A<b>": 1 new record' and "\n" not in mail.subject
    assert "\u202e" not in mail.text and "Unsubscribe: https://app.example.com/unsubscribe?t=a&b" in mail.text
    assert "... and more in the app: https://app.example.com/whats-new" in mail.text
    assert "- A<b>" not in mail.text and "Title" not in mail.text  # the evil title replaced the default
    assert mail.html.startswith("<!DOCTYPE html>")


def test_email_watched_records_are_capped() -> None:
    watched = [
        {
            "node": {"id": f"organization/{UID}", "title": f"Org {i}"},
            "new": [row(1, kind="fact", summary="")],
            "count": 2,
        }
        for i in range(30)
    ] + [{"node": {"id": "organization/x", "title": "Quiet"}, "new": [], "count": 0}]
    exact = [{"node": {"id": f"organization/{UID}", "title": "Exact"}, "new": [row(1, kind="fact")], "count": 1}]
    one = EmailRenderer().render(content({"watched": exact}), "https://u", "https://m")
    assert "and more in the app" not in one.html
    mail = EmailRenderer().render(content({"watched": watched}), "https://u", "https://m")
    assert mail.html.count("<h3") == 25 and "5 more watched records" in mail.html and "Quiet" not in mail.html
    assert "Org 0: 2 new <https://app.example.com/insights/nodes/organization/" in mail.text
    assert mail.subject == "Example Radar: 60 new records"
    bare = EmailRenderer().render(
        content(
            {"watched": watched[:1], "new": {"signal": [row(1)]}, "more": {"signal": True}, "since": ""}, app_url=""
        ),
        "https://u",
        "https://m",
    )
    assert "href" not in bare.html.replace('href="https://u"', "").replace('href="https://m"', "")
    assert "Open the digest" not in bare.text and "  ... and more in the app\n" in bare.text
    assert "2 new\n" in bare.html or "Org 0: 2 new" in bare.text


def test_helpers() -> None:
    assert slack_escape("a&<>") == "a&amp;&lt;&gt;"
    assert plain("a\x00\nb\tc", 10) == "a b c"
    assert plain(None, 5) == ""


def test_unlinked_cycle_2_gaps() -> None:
    """Scan cycle 2, findings 4, 7, 8 and the UNC note: wrapped hosts, sentence-final IPv4, combining marks, UNC."""
    assert (
        unlinked("Claim at _evil-refunds.com_ or x-evil.com now") == "Claim at _evil-refunds[.]com_ or x-evil[.]com now"
    )
    assert unlinked("see evil.com_") == "see evil[.]com_"
    assert unlinked("exposed at 203.0.113.7.") == "exposed at 203[.]0[.]113[.]7."
    assert unlinked("example.\u092d\u093e\u0930\u0924") == "example[.]\u092d\u093e\u0930\u0924"  # Devanagari TLD
    assert unlinked("open \\\\host\\share") == "open [\\]host\\share"  # no longer starts with two
    assert unlinked("U.S. rules, e.g. 1.5bn, v2.0, Rp1.500.000, 1.2.3.4.5") == (
        "U.S. rules, e.g. 1.5bn, v2.0, Rp1.500.000, 1.2.3.4.5"
    )


def test_slack_text_is_verbatim_everywhere() -> None:
    """Scan cycle 3, finding 3: Slack auto-links, link-ifies channel names and parses mentions only in non-verbatim
    text, so every mrkdwn object the digest sends is verbatim."""
    w = {"node": {"id": f"organization/{UID}", "title": "Org"}, "new": [row(1, kind="fact")], "count": 3}
    msg = SlackRenderer().render(
        content({"new": {"signal": [row(1, summary="#general @here evil.example")]}, "watched": [w]})
    )
    objects = [b["text"] for b in msg["blocks"] if b["type"] == "section"]
    objects += [e for b in msg["blocks"] if b["type"] == "context" for e in b["elements"]]
    assert objects and all(o == o | {"type": "mrkdwn", "verbatim": True} for o in objects)


def big_rows(n: int = 20, kind: str = "signal") -> list[dict[str, Any]]:
    return [row(i, "T" * 140, kind=kind, summary="S " * 150) for i in range(n)]


def test_slack_a_long_list_continues_then_counts_the_rest() -> None:
    texts = sections(SlackRenderer().render(content({"new": {"signal": big_rows()}, "more": {"signal": True}})))
    assert 1 < len(texts) <= LIST_SECTIONS and all(len(t) <= SECTION_MAX for t in texts)
    assert texts[0].startswith("*Signals* (20+)\n") and not texts[1].startswith("*Signals*")
    shown = sum(t.count("\u2022 ") for t in texts)
    assert 0 < shown < 20 and texts[-1].endswith(f"|\u2026 and {20 - shown} more in the app>")


def test_slack_every_group_gets_its_share_and_the_watched_records_always_show() -> None:
    """Scan cycle 4, findings 1 and 6: busy lists cannot crowd out later lists or the watched records, and the
    watched heading travels with its first record."""
    kinds = ("signal", "trend", "gap", "content", "a", "b", "c")
    w = {"node": {"id": f"organization/{UID}", "title": "Org"}, "new": [row(1, kind="fact")], "count": 2}
    msg = SlackRenderer().render(content({"new": {k: big_rows(kind=k) for k in kinds}, "watched": [w]}, order=kinds))
    texts = sections(msg)
    heads = [t.split("\n", 1)[0] for t in texts]
    for k in ("Signals", "Trend", "Gap", "Content", "A", "B", "C"):
        assert any(h.startswith(f"*{k}*") for h in heads), k
    watched = [t for t in texts if t.startswith("*Watched records*")]
    assert len(watched) == 1 and "\n*<https://app.example.com/insights/nodes/organization/" in watched[0]
    assert len(json.dumps(msg)) + 256 <= SAFE_PAYLOAD and len(msg["blocks"]) <= MAX_BLOCKS


def test_slack_stays_under_fifty_blocks() -> None:
    kinds = tuple(f"k{i}" for i in range(60))
    msg = SlackRenderer().render(content({"new": {k: [row(1, kind=k)] for k in kinds}}, order=kinds))
    assert len(msg["blocks"]) == MAX_BLOCKS
    # Scan cycle 16: the closing block says how many lists are only in the app.
    closing = msg["blocks"][-3]["text"]["text"]
    assert closing == "<https://app.example.com/whats-new|\u2026 and 15 more lists in the app>"
    bare = SlackRenderer().render(content({"new": {k: [row(1, kind=k)] for k in kinds}}, order=kinds, app_url=""))
    assert bare["blocks"][-3]["text"]["text"] == "\u2026 and 15 more lists in the app."


@pytest.mark.parametrize(
    "app",
    ["https://app.example.com", "https://e.example.com/" + "p/" * 400, "https://e.example.com/" + "q/" * 1600],
)
def test_slack_lines_are_bounded_so_no_section_is_ever_cut(app: str) -> None:
    """Scan cycle 4, finding 8: a long link falls back to the bare title, escaped text is capped, a long app
    address drops the tail link; every section fits whatever the input."""
    rows = [row(i, "&" * 150, summary="&" * 300) for i in range(40)]
    w = {"node": {"id": "organization/" + "x" * 2500, "title": "&" * 150}, "new": rows[:5], "count": 9}
    digest = {"new": {"signal": rows}, "more": {"signal": True}, "watched": [w]}
    for t in sections(SlackRenderer().render(content(digest, app_url=app))):
        assert len(t) <= SECTION_MAX and t.count("<") == t.count(">") and "&am\n" not in t


def test_slack_a_long_link_shows_the_title_not_a_placeholder() -> None:
    """Scan cycle 4, finding 4: a record id too long for a link keeps its short title, as bare text."""
    r = {"id": "k/" + "\u4e2d" * 400, "type": "k", "title": "Short title", "summary": "s"}
    texts = sections(SlackRenderer().render(content({"new": {"k": [r]}}, order=("k",))))
    assert "\u2022 Short title\ns" in texts[0]
    assert len(SlackRenderer._link(content({}), {"id": "a/b", "title": "x"})) <= LINK_MAX


@pytest.mark.parametrize(
    ("summary", "shown"),
    [
        ("```code block``` swallows", "'''code block''' swallows"),
        ("> quoted", "\u00a0&gt; quoted"),
        ("\u2022 Urgent: rotate your keys", "\u00a0\u2022 Urgent: rotate your keys"),
        ("*bold* start", "\u2217bold\u2217 start"),
        ("-40% YoY revenue", "-40% YoY revenue"),  # nothing taken out (scan cycle 4, finding 2)
        ("~30 startups", "\u223c30 startups"),
        ("#1 in market", "#1 in market"),
        ("The patch is ~not~ *needed*", "The patch is \u223cnot\u223c \u2217needed\u2217"),
    ],
)
def test_slack_untrusted_text_cannot_restyle_the_digest_and_loses_nothing(summary: str, shown: str) -> None:
    """Scan cycles 3 and 4: no backtick, no leading quote, emphasis or bullet mark, and no character deleted."""
    lines = sections(SlackRenderer().render(content({"new": {"signal": [row(1, summary=summary)]}})))[0].split("\n")
    assert lines[2] == shown


def test_bare_titles_get_the_same_treatment() -> None:
    """Scan cycle 4, finding 5: bare titles cannot open a code block either."""
    rows = [row(1, "```a", summary="s"), row(2, "b```", summary="t")]
    text = sections(SlackRenderer().render(content({"new": {"signal": rows}}, app_url="")))[0]
    assert "`" not in text and "'''a 1" in text


def test_slack_compact_render_leaves_summaries_out() -> None:
    """Scan cycle 4, finding 3: what the runner posts after Slack refused the full message as too long."""
    full = SlackRenderer().render(content({"new": {"signal": big_rows()}}))
    compact = SlackRenderer()._render(content({"new": {"signal": big_rows()}}), compact=True)[0]
    assert "S S" in json.dumps(full) and "S S" not in json.dumps(compact)
    assert sum(len(t) for t in sections(compact)) < sum(len(t) for t in sections(full))


def test_slack_lengths_count_utf16_units_and_no_section_goes_over() -> None:
    """Scan cycle 5, findings 1 and 2: emoji and Extension B characters count two in Slack, and the newline
    before the tail is reserved; a section is never over the limit as Slack measures it."""
    astral = "\U0001f600\U00020000" * 75
    rows = [row(i, astral, summary=astral * 2) for i in range(40)]
    msg = SlackRenderer().render(content({"new": {"signal": rows}, "more": {"signal": True}}))
    for t in sections(msg):
        assert len(t.encode("utf-16-le")) // 2 <= SECTION_MAX
    for size in range(900, 1300, 37):
        lines = ["x" * 2900, "y" * 1000, "z" * size, "w" * 1200, "w" * 1200]
        for k in range(1, len(lines) + 1):
            group = _Group("*H*", lines, True, True, list(range(len(lines))), LIST_SECTIONS)
            for t in SlackRenderer()._pack(group, list(range(k)), content({})) or []:
                assert len(t) <= SECTION_MAX


def test_blank_titles_read_untitled_everywhere() -> None:
    """Scan cycle 5, finding 3."""
    w = {"node": {"id": "x", "title": "\u200b"}, "new": [row(1, kind="fact")], "count": 1}
    for app in ("", "https://app.example.com"):
        r = {"id": "signal/a", "type": "signal", "title": "   ", "summary": ""}
        texts = sections(SlackRenderer().render(content({"new": {"signal": [r]}, "watched": [w]}, app_url=app)))
        joined = "\n".join(texts)
        assert "(untitled)" in joined and "**:" not in joined and "\u2022 \n" not in joined + "\n"


def test_a_watched_heading_never_ends_a_section_without_its_first_row() -> None:
    """Scan cycle 5, finding 4: the heading and its first row are one unit."""
    ws = [
        {"node": {"id": f"organization/{UID[:-2]}{i:02d}", "title": f"Org {i}"}, "new": big_rows(3, "fact"), "count": 5}
        for i in range(6)
    ]
    for t in sections(SlackRenderer().render(content({"new": {"signal": big_rows()}, "watched": ws}))):
        last = t.split("\n")[-1]
        assert not (last.startswith("*") and last.endswith(" new")), t[-200:]


def test_unused_shares_go_to_the_busy_group() -> None:
    """Scan cycle 5, finding 8: seven one-row lists and one busy list: the busy list uses the budget left over."""
    kinds = ("a", "b", "c", "d", "e", "f", "g", "busy")
    new = {k: [row(1, kind=k)] for k in kinds[:-1]} | {"busy": big_rows(50, "busy")}
    texts = sections(SlackRenderer().render(content({"new": new}, order=kinds)))
    busy = [t for t in texts if t.startswith("*Busy*")]
    idx = texts.index(busy[0])
    shown = sum(t.count("\u2022 ") for t in texts[idx : idx + LIST_SECTIONS])
    assert shown >= 10


def test_bounded_drops_rather_than_cuts() -> None:
    """Scan cycle 5, finding 9: text that cannot fit even at one character is dropped, so no escape is split."""
    from insights_radar.insights.delivery_render import _bounded

    assert _bounded("&&&&", 4, lambda s: "&amp;" * 200) == ""
    assert _bounded("abc", 3, lambda s: s) == "abc"


def test_a_two_section_group_keeps_its_last_record() -> None:
    """Scan cycle 6, finding 1: need and share are counted the same way, so a group under its share loses nothing."""
    rows = [row(i, "T" * 140, summary="S " * 110) for i in range(12)]
    texts = sections(SlackRenderer().render(content({"new": {"signal": rows, "trend": big_rows(25, "trend")}})))
    signal = [t for t in texts if "/signal/" in t]
    assert sum(t.count("\u2022 ") for t in signal) == 12 and not any("more in the app" in t for t in signal)


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("see __Important__ now", "see \uff3f\uff3fImportant\uff3f\uff3f now"),
        ("snake_case stays readable", "snake\uff3fcase stays readable"),
        ("Breach :rotating_light: confirmed", "Breach \u2236rotating\uff3flight\u2236 confirmed"),
        ("meet at 10:30, ratio 3:1", "meet at 10:30, ratio 3:1"),
        ("Launch at 10:30:45 UTC", "Launch at 10:30:45 UTC"),
        ("scored :100: and :+1:", "scored \u2236100\u2236 and \u2236+1\u2236"),
        ("an :8ball: and :-1: here", "an \u22368ball\u2236 and \u2236-1\u2236 here"),
        ("Top 3:fire: picks", "Top 3\u2236fire\u2236 picks"),
        ("at 10:30:rocket: launch", "at 10:30\u2236rocket\u2236 launch"),
    ],
)
def test_untrusted_text_cannot_italicise_or_show_emoji(text: str, shown: str) -> None:
    """Scan cycle 6, findings 2 and 7: every underscore and the colons of a :shortcode: are mapped."""
    lines = sections(SlackRenderer().render(content({"new": {"signal": [row(1, summary=text)]}})))[0].split("\n")
    assert lines[2] == shown


def test_a_message_too_large_to_send_whole_is_rendered_compact_up_front(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scan cycles 7 and 8: the renderer, not a second post, decides the compact form, when the full form would
    have to leave sections out to stay within SAFE_PAYLOAD as the transport sends it (ASCII-escaped). The body
    budget keeps real digests well under SAFE_PAYLOAD, so the threshold is lowered to reach the branch."""
    import insights_radar.insights.delivery_render as render

    monkeypatch.setattr(render, "SAFE_PAYLOAD", 8_000)
    kinds = tuple(f"k{i}" for i in range(12))
    digest = {"new": {k: big_rows(20, k) for k in kinds}}
    _full, _shown, cut = SlackRenderer()._render(content(digest, order=kinds), compact=False)
    assert cut  # the byte budget left records out
    sent = SlackRenderer().render(content(digest, order=kinds))
    assert "S S" not in json.dumps(sent)  # the compact form, with no summaries
    assert len(json.dumps(sent)) <= 8_000
    small = SlackRenderer().render(content({"new": {"signal": big_rows(3)}}))
    assert "S S" in json.dumps(small)


def test_the_message_is_never_larger_than_safe_payload_on_the_wire() -> None:
    """Scan cycle 8, findings 3 and 4: measured as the transport sends it, with every non-ASCII character escaped,
    and bounded however many groups there are; what does not fit ends in "More in the app"."""
    from insights_radar.insights.delivery_render import SAFE_PAYLOAD

    kinds = tuple(f"k{i}" for i in range(45))
    cjk = "\u6f22\u5b57\u2217\u223c" * 30
    digest = {"new": {k: [row(i, f"{cjk} {i}", summary=cjk * 3, kind=k) for i in range(10)] for k in kinds}}
    for app in ("https://app.example.com/" + "p" * 400, "https://app.example.com"):
        msg = SlackRenderer().render(content(digest, order=kinds, app_url=app))
        assert len(json.dumps(msg)) + 256 <= SAFE_PAYLOAD  # and the chat.postMessage envelope (scan cycle 9)
        assert any("more lists in the app" in t or "more in the app" in t for t in sections(msg))
        assert len(msg["blocks"]) <= 50


def test_every_group_shows_in_a_non_ascii_digest() -> None:
    """Scan cycle 9, finding 1: the shares are in wire bytes, so CJK lists and the watched records are not crowded
    out by the SAFE_PAYLOAD cut; each group keeps its heading and shows at least one record."""
    from insights_radar.insights.delivery_render import SAFE_PAYLOAD

    cjk = "\u6f22\u5b57" * 60
    for n in (3, 6):
        kinds = tuple(f"l{i}" for i in range(n))
        new = {k: [row(i, f"{cjk} {i}", summary="", kind=k) for i in range(40)] for k in kinds}
        w = {"node": {"id": f"organization/{UID}", "title": "Org"}, "new": [row(1, cjk, kind="fact")], "count": 1}
        msg = SlackRenderer().render(content({"new": new, "watched": [w]}, order=kinds))
        texts = sections(msg)
        for k in kinds:
            assert any(t.startswith(f"*{k.capitalize()}*") for t in texts), k
        assert any(t.startswith("*Watched records*") for t in texts)
        assert len(json.dumps(msg)) + 256 <= SAFE_PAYLOAD


def test_compact_is_sent_only_when_it_shows_more_records() -> None:
    """Scan cycles 9 and 10, findings 2 to 5: the counts come from the renderer, not from the text, and a tie keeps
    the summaries."""
    kinds = tuple(f"k{i}" for i in range(12))
    digest = content({"new": {k: big_rows(20, k) for k in kinds}}, order=kinds)
    _full, shown, cut = SlackRenderer()._render(digest, compact=False)
    compact, compact_shown, _ = SlackRenderer()._render(digest, compact=True)
    assert cut and compact_shown > shown
    assert SlackRenderer().render(digest) == compact
    small = content({"new": {"signal": big_rows(3)}})
    assert SlackRenderer()._render(small, compact=False)[1:] == (3, False)
    assert "S S" in json.dumps(SlackRenderer().render(small))
    bulleted = content({"new": {"signal": [row(i, summary="\u2022 a \u2022 b") for i in range(3)]}})
    assert SlackRenderer()._render(bulleted, compact=False)[1] == 3


def test_a_zero_width_lead_does_not_skip_the_line_start_guard() -> None:
    """Scan cycle 9, finding 4: the space a zero-width character leaves in front is stripped first."""
    lines = sections(SlackRenderer().render(content({"new": {"signal": [row(1, summary="\u200b >quote")]}})))[0]
    assert "\n\u00a0&gt;quote" in lines
    w = {"node": {"id": "x", "title": "\u200b Acme"}, "new": [row(1, kind="fact")], "count": 1}
    texts = sections(SlackRenderer().render(content({"watched": [w]}, app_url="")))
    assert any("*Acme*: 1 new" in t for t in texts)


def test_a_real_ellipsis_that_fits_is_kept_whole() -> None:
    """Scan cycle 9, finding 5: folding writes a real ellipsis as "...", and the text is kept whole."""
    from insights_radar.insights.delivery_render import short

    assert short("The ministry lists new duties and more\u2026", 220) == "The ministry lists new duties and more..."
    assert short("word " * 100, 50).endswith("\u2026") and len(short("word " * 100, 50)) <= 50


def test_the_email_text_part_says_a_watched_record_has_more() -> None:
    """Scan cycle 9, finding 7: both parts carry the same content."""
    w = {"node": {"id": "x", "title": "Org"}, "new": [row(1, kind="fact"), row(2, kind="fact")], "count": 5}
    mail = EmailRenderer().render(content({"watched": [w]}), "https://u", "https://m")
    assert "... and more in the app" in mail.text


def test_titles_that_escape_large_still_leave_the_watched_records() -> None:
    """Scan cycle 10, finding 1: when even each list's first record passes the budget, the last lists go to
    "More in the app" and the watched records stay."""
    rocket = "\U0001f680" * 150
    for n in (15, 20, 40):
        kinds = tuple(f"k{i}" for i in range(n))
        new = {k: [row(i, rocket, kind=k) for i in range(3)] for k in kinds}
        ws = [
            {
                "node": {"id": f"organization/{UID[:-2]}{i:02d}", "title": "Org"},
                "new": [row(1, kind="fact")],
                "count": 1,
            }
            for i in range(3)
        ]
        msg = SlackRenderer().render(content({"new": new, "watched": ws}, order=kinds))
        texts = sections(msg)
        assert any(t.startswith("*Watched records*") for t in texts), n
        assert len(json.dumps(msg)) + 256 <= SAFE_PAYLOAD and len(msg["blocks"]) <= MAX_BLOCKS


def test_a_non_ascii_digest_uses_the_byte_budget() -> None:
    """Scan cycle 10, findings 2, 3 and 7: no UTF-16 cap on top of the byte budget, no blanket reserve per group."""
    cjk = "\u6f22\u5b57" * 40
    kinds = ("l0", "l1", "l2")
    new = {k: [row(i, f"{cjk} {i}", summary=cjk, kind=k) for i in range(40)] for k in kinds}
    msg = SlackRenderer().render(content({"new": new}, order=kinds))
    assert len(json.dumps(msg)) > 30_000  # most of the 40 kB is used, not a sixth of it
    singles = tuple(f"s{i}" for i in range(23))
    one = {k: [row(1, kind=k)] for k in singles} | {"busy": big_rows(40, "busy")}
    texts = sections(SlackRenderer().render(content({"new": one}, order=(*singles, "busy"))))
    assert sum(t.count("\u2022 ") for t in texts if "/busy/" in t) >= 10


def test_a_zero_width_lead_does_not_shorten_the_cut_or_the_email_summary() -> None:
    """Scan cycle 10, findings 6 and 8: folding strips the space a zero-width character leaves."""
    text = "\u200b " + "wordy " * 30 + "a" * 78
    assert short(text, 220).endswith("wordy\u2026")
    mail = EmailRenderer().render(
        content({"new": {"signal": [row(1, summary="\u200b x y")]}}), "https://u", "https://m"
    )
    assert "   x y" not in mail.text and "> x y" not in mail.html


def test_a_small_digest_keeps_every_record() -> None:
    """Scan cycle 11, finding 1: a share equal to a group's need keeps every record."""
    new = {
        "signal": [row(1, "a", summary=""), row(2, "b", summary="")],
        "trend": [row(i, t, kind="trend", summary="") for i, t in enumerate(("Trend one", "Trend two", "x"))],
        "gap": [row(i, f"Gap number {i} long title here", kind="gap", summary="a summary " * 10) for i in range(8)],
    }
    msg, shown, cut = SlackRenderer()._render(content({"new": new}, order=("signal", "trend", "gap")), compact=False)
    assert shown == 13 and not cut and not any("more in the app" in t for t in sections(msg))


def test_lists_dropped_for_bytes_count_as_left_out() -> None:
    """Scan cycle 11, finding 2: the compact form is tried when whole lists were dropped."""
    astral = "\U0001f680" * 120
    kinds = tuple(f"k{i}" for i in range(16))
    new = {k: [row(1, astral, summary=astral, kind=k)] for k in kinds}
    w = {"node": {"id": f"organization/{UID}", "title": "Org"}, "new": [row(1, kind="fact")], "count": 1}
    digest = content({"new": new, "watched": [w]}, order=kinds)
    _msg, _shown, cut = SlackRenderer()._render(digest, compact=False)
    assert cut
    assert "S S" not in json.dumps(SlackRenderer().render(digest))


def test_the_watched_records_stay_with_more_lists_than_blocks() -> None:
    """Scan cycle 11, finding 3: each group takes at least one block, so lists go before the watched records."""
    kinds = tuple(f"m{i}" for i in range(50))
    new = {k: [row(1, kind=k)] for k in kinds}
    ws = [
        {"node": {"id": f"organization/{UID[:-2]}{i:02d}", "title": "Org"}, "new": [row(1, kind="fact")], "count": 1}
        for i in range(2)
    ]
    msg = SlackRenderer().render(content({"new": new, "watched": ws}, order=kinds))
    texts = sections(msg)
    assert any(t.startswith("*Watched records*") for t in texts) and len(msg["blocks"]) <= MAX_BLOCKS
    assert any("more lists in the app" in t for t in texts)


def test_the_block_count_never_strips_the_summaries() -> None:
    """Scan cycle 12, findings 1 and 2: a second section goes to a group only while blocks are left, so a list cut
    by the block count still ends in its tail, and the compact form is not sent for it."""
    singles = tuple(f"s{i}" for i in range(44))
    for order in ((*singles, "long"), ("long", *singles)):
        new = {k: [row(1, kind=k)] for k in singles} | {"long": big_rows(20, "long")}
        msg = SlackRenderer().render(content({"new": new}, order=order))
        texts = sections(msg)
        assert "S S" in json.dumps(msg) and len(msg["blocks"]) <= MAX_BLOCKS
        long = [t for t in texts if "/long/" in t]
        assert long and "more in the app" in long[-1]


def test_share_a_cut_group_leaves_goes_to_the_others() -> None:
    """Scan cycle 12, finding 3: the bytes a re-packed group leaves are handed on, so a cut message uses the budget."""
    cjk = "\u6f22\u5b57" * 90
    kinds = tuple(f"l{i}" for i in range(12))
    new = {k: [row(i, f"{k} {i}", summary=cjk, kind=k) for i in range(6)] for k in kinds}
    full, _shown, _cut = SlackRenderer()._render(content({"new": new}, order=kinds), compact=False)
    assert len(json.dumps(full)) + 256 > SAFE_PAYLOAD - 2_000


def test_the_watched_records_are_admitted_first() -> None:
    """Scan cycle 13, finding 1: with few spare blocks the watched records are not the group held to one section."""
    singles = tuple(f"s{i:02d}" for i in range(40))
    new = {k: [row(1, kind=k)] for k in singles} | {k: big_rows(14, k) for k in ("a00", "a01")}
    ws = [
        {
            "node": {"id": f"organization/{UID[:-2]}{i:02d}", "title": "Org"},
            "new": [row(1, kind="fact"), row(2, kind="fact")],
            "count": 2,
        }
        for i in range(8)
    ]
    digest = content({"new": new, "watched": ws}, order=("a00", "a01", *singles))
    msg = SlackRenderer().render(digest)
    watched = "\n".join(t for t in sections(msg) if "/organization/" in t)
    assert watched.count("\u2022 ") == 16 and len(msg["blocks"]) <= MAX_BLOCKS


def test_a_message_never_passes_the_byte_limit_or_drops_a_tail() -> None:
    """Scan cycle 13, findings 2 and 3: every group's cost is exact, so nothing overshoots, and a cut list always
    ends in its own tail."""
    kinds = tuple(f"k{i}" for i in range(20))
    new = {k: [row(i, "a" * 140, summary="b " * 110, kind=k) for i in range(20)] for k in kinds}
    msg = SlackRenderer().render(content({"new": new}, order=kinds))
    assert len(json.dumps(msg)) + 256 <= SAFE_PAYLOAD
    for t in sections(msg):
        if t.startswith("*K"):
            assert "more in the app" in t.split("\n")[-1] or t.count("\u2022 ") == 20


def test_one_watched_record_with_many_rows_leaves_room_for_the_others() -> None:
    """Scan cycle 14, finding 1: every watched record's first unit goes in before any record's second row."""
    ws = [
        {
            "node": {"id": f"organization/{UID[:-2]}{i:02d}", "title": f"Node {i}"},
            "new": big_rows(12 if i == 0 else 3, "fact"),
            "count": 12 if i == 0 else 3,
        }
        for i in range(20)
    ]
    texts = sections(SlackRenderer().render(content({"watched": ws})))
    joined = "\n".join(texts)
    for i in range(20):
        assert f"|Node {i}>*:" in joined, i


def test_a_tail_that_does_not_fit_the_last_section_refuses_the_pack() -> None:
    """Scan cycle 17: a heading that leaves no room for the tail in the group's only section is refused, not cut."""
    group = _Group("*" + "h" * (SECTION_MAX - 4) + "*", ["\u2022 a"], True, True, [0], 1)
    assert SlackRenderer()._pack(group, [], content({})) is None


def test_a_group_that_fits_is_never_refused_for_its_tail() -> None:
    """Scan cycle 14, findings 2 and 3: the tail is reserved only where it goes, and a group whose last record
    fits once its tail is gone is shown whole."""
    lines = ["\u2022 " + "a" * 1297] * 4
    group = _Group("*L* (4)", lines, False, True, list(range(4)), LIST_SECTIONS)
    assert SlackRenderer()._pack(group, list(range(4)), content({})) is not None
    rows = [row(i, "T" * 140, summary="S " * 110) for i in range(10)]
    _msg, shown, cut = SlackRenderer()._render(content({"new": {"signal": rows}}), compact=False)
    assert shown == 10 and not cut


def test_watched_records_come_before_list_rows_under_the_byte_limit() -> None:
    """Scan cycle 15, findings 1 and 3: every watched record's first unit before any list's second record, and the
    watched records left out are counted."""
    kinds = tuple(f"k{i:02d}" for i in range(20))
    new = {k: [row(i, f"Title {i}", summary="word " * 44, kind=k) for i in range(20)] for k in kinds}
    ws = [
        {
            "node": {"id": f"organization/{UID[:-2]}{i:02d}", "title": f"Node {i:02d}"},
            "new": [row(j, f"W {j}", summary="word " * 44, kind="fact") for j in range(3)],
            "count": 3,
        }
        for i in range(30)
    ]
    texts = sections(SlackRenderer().render(content({"new": new, "watched": ws}, order=kinds)))
    joined = "\n".join(texts)
    shown = sum(1 for i in range(30) if f"|Node {i:02d}>*:" in joined)
    assert shown == 30 or f"{30 - shown} more watched record" in joined


def test_a_watched_unit_that_does_not_fit_does_not_close_the_others() -> None:
    """Scan cycle 15, finding 4: a long second row is skipped and the later records' short second rows still go in."""
    long_row = row(9, "L" * 140, summary="S " * 110, kind="fact")
    ws = [
        {
            "node": {"id": f"organization/{UID[:-2]}{i:02d}", "title": f"N{i}"},
            "count": 2,
            "new": [row(1, kind="fact"), long_row if i == 0 else row(2, kind="fact")],
        }
        for i in range(3)
    ]
    # A byte limit that holds every unit but N0's long second row: N1's and N2's short ones still go in.
    node_of = [0, 0, 1, 1, 2, 2]
    r = SlackRenderer()
    lines = []
    for w in ws:
        found = [r._line(content({}), x, False) for x in w["new"]]
        lines += [f"*{w['node']['title']}*: 2 new\n{found[0]}", found[1]]
    group = _Group("*Watched records*", lines, False, False, [0, 2, 4, 1, 3, 5], 1, node_of, [0, 1, 0, 1, 0, 1])
    from insights_radar.insights.delivery_render import _mrkdwn, _wire

    five = r._pack(group, [0, 2, 3, 4, 5], content({})) or []
    limit = sum(_wire({"type": "section", "text": _mrkdwn(t)}) + 2 for t in five) + 10
    packs, counts, _cut = r._admit([group], content({}), 45, limit)
    assert counts[0] == 5 and "L" * 140 not in "\n".join(packs[0])


def test_many_watched_records_never_crowd_out_every_list() -> None:
    """Scan cycle 16, findings 1 and 2: round 0 alternates watched first units with list first records, so every
    list keeps its first record even when watched records alone would fill the message; lists left out are counted."""
    cjk = "\u6f22\u5b57" * 75
    kinds = tuple(f"k{i}" for i in range(10))
    new = {k: [row(i, cjk, summary=cjk, kind=k) for i in range(5)] for k in kinds}
    ws = [
        {
            "node": {"id": f"organization/{UID[:-2]}{i:02d}", "title": cjk},
            "new": [row(1, cjk, summary=cjk, kind="fact")],
            "count": 1,
        }
        for i in range(30)
    ]
    texts = sections(SlackRenderer().render(content({"new": new, "watched": ws}, order=kinds)))
    heads = [t.split("\n", 1)[0] for t in texts]
    shown = sum(1 for k in kinds if any(h.startswith(f"*{k.capitalize()}*") for h in heads))
    assert shown == 10 or any(f"{10 - shown} more list" in t for t in texts)
    assert any(t.startswith("*Watched records*") for t in texts)


def test_one_watched_record_left_out_reads_in_the_singular() -> None:
    """Scan cycle 16, finding 3."""
    from insights_radar.insights.delivery_render import _plural

    assert _plural(1, "more watched record") == "1 more watched record"
    assert _plural(2, "more list") == "2 more lists"
