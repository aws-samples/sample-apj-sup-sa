# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Rendering a digest for Slack (Block Kit) and email (HTML and plain text).

Everything a digest lists was gathered from the public web by agents, so every title and summary is untrusted
text: folded (no zero-width or bidi tricks), escaped for where it lands (Slack's ``& < >``, HTML's ``& < > " '``),
and never used as a link. The only links are to this app's own record pages, built here from record ids.
Both renderers are pure functions of the digest; they cut it to fit Slack's limits (50 blocks, 3,000 characters a
section) and keep an email to a readable size, always saying how much more the app holds.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, NamedTuple
from urllib.parse import quote

from insights_radar.core.clock import parse_iso
from insights_radar.core.defang import Defang

MAX_BLOCKS = 50
SECTION_MAX = 3000
HEADER_MAX = 150
TITLE_MAX = 150
FALLBACK_MAX = 300
EMAIL_SUMMARY_MAX = 300
SLACK_SUMMARY_MAX = 220  # about two lines on a laptop, three on a phone
LIST_SECTIONS = 2  # sections one group (a list, or the watched records) may take in Slack
# Every Slack line is bounded where it is built, so packing never needs to cut (scan cycle 4): a record link longer
# than LINK_MAX is shown as the bare title, an escaped summary or bare title is at most TEXT_MAX, and the tail drops
# its link when the app's address is longer than TAIL_LINK_MAX. Longest heading + line + tail stays under a section.
LINK_MAX = 800
TEXT_MAX = 500
TAIL_LINK_MAX = 500
ENVELOPE = 256  # chat.postMessage's channel, unfurl_links and unfurl_media around the message (scan cycle 9)
SAFE_PAYLOAD = 40_000  # bytes of the message as sent (ASCII-escaped JSON): a hard bound, well under Slack's refusals
_MARKS = ">\u2022"  # what untrusted text may not start with: a quote or a bullet (emphasis is mapped away)
TOO_LONG = "(a part of this digest too long to show here; open the digest in the app)"
EMAIL_WATCHED_MAX = 25
DISCLAIMER = "Found and summarised from public sources by AI agents. Check the sources before you act on it."
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
# What a mail client or Slack would turn into a link by itself: a scheme's "://", a UNC path's "\\", a host name
# and a bare IPv4 address. Every dot (ASCII, CJK or fullwidth) between a label character and two non-digit label
# characters is broken, which takes every host name apart; a label character is anything but space and ASCII
# punctuation other than "-" and "_", so every script (combining marks included) and wrapped hosts are caught.
# Summaries and bare titles are untrusted text, so all of these are broken up before they are shown (scan cycles 1
# and 2). Breaking a dot that only looked like a host ("file.txt") costs nothing.
_SCHEME = re.compile(r"://")
_UNC = re.compile(r"\\\\(?=\S)")
_DOTS = ".\u3002\uff0e\uff61"  # ASCII, CJK, fullwidth and halfwidth full stops
_PUNCT = r"\s!-,./:-@\[\\\]^`{-~" + _DOTS
# Fixed-width lookarounds only, so matching is linear in the text (no nested quantifier: scan cycle 3, Holmes).
_HOST = re.compile(rf"(?<=[^{_PUNCT}])[{_DOTS}](?=[^{_PUNCT}\d]{{2}})")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\.?\d)")


def plain(text: Any, max_len: int) -> str:
    """Untrusted text as one clean line: folded, without control characters, capped."""
    # Stripped again: a zero-width character Defang removes can leave a space in front (scan cycles 9 and 10).
    return Defang.neutralize(_CONTROL.sub(" ", str(text or "")), max_len=max_len).strip()


def unlinked(text: str) -> str:
    """Untrusted text that no client can turn into a link: ``https://x.example`` reads ``https[:]//x[.]example``.
    Used on summaries everywhere and on titles wherever they are bare text (not a link label)."""

    def dots(m: re.Match[str]) -> str:
        return re.sub(f"[{_DOTS}]", "[.]", m.group(0))

    return _IPV4.sub(dots, _HOST.sub("[.]", _UNC.sub("[\\\\]", _SCHEME.sub("[:]//", text))))


def short(text: Any, max_len: int) -> str:
    """``plain`` cut on a word where one is near the limit, so a summary does not end mid-word."""
    return _cut(plain(text, max_len), max_len)


def _cut(folded: str, max_len: int) -> str:
    """Already folded text cut to max_len on a word where one is near the limit, ending in an ellipsis."""
    # Folding writes a real ellipsis as "...", so one that is left was put there by Defang's cut (scan cycle 10).
    if len(folded) <= max_len and not folded.endswith("\u2026"):
        return folded
    head = folded[: max_len - 1] if len(folded) > max_len else folded[:-1]
    cut = head.rfind(" ")
    return (head[:cut] if cut >= max_len // 2 else head).rstrip(" ,;:") + "\u2026"


def slack_escape(text: str) -> str:
    """Slack's three control characters (https://api.slack.com/reference/surfaces/formatting#escaping): with
    ``<`` escaped no ``<!channel>``, ``<@U...>`` or ``<url|label>`` can be formed from a title."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@dataclass(frozen=True, slots=True)
class DigestContent:
    """What a renderer needs: the digest (``DigestService.digest``), the app's name and address, the label of
    each list, and the order the lists are shown in."""

    app_name: str
    app_url: str
    digest: dict[str, Any]
    labels: dict[str, str] = field(default_factory=dict)
    order: tuple[str, ...] = ()

    def record_url(self, node_id: str) -> str:
        if not self.app_url or "/" not in node_id:
            return ""
        kind, _, uid = node_id.partition("/")
        return f"{self.app_url}/insights/nodes/{quote(kind, safe='')}/{quote(uid, safe='')}"

    @property
    def digest_url(self) -> str:
        return f"{self.app_url}/whats-new" if self.app_url else ""

    @property
    def total(self) -> int:
        return int(self.digest.get("total", 0))

    def lists(self) -> list[tuple[str, str, list[dict[str, Any]], bool]]:
        """(key, label, rows, more) for each list with something new, in display order."""
        new: dict[str, list[dict[str, Any]]] = self.digest.get("new") or {}
        more: dict[str, bool] = self.digest.get("more") or {}
        keys = [k for k in self.order if k in new] + sorted(k for k in new if k not in self.order)
        return [(k, self._label(k), new[k], bool(more.get(k))) for k in keys if new[k]]

    def _label(self, key: str) -> str:
        return self.labels.get(key) or key.removeprefix("insight:").replace("_", " ").capitalize()

    def watched(self) -> list[dict[str, Any]]:
        return [w for w in self.digest.get("watched") or [] if int(w.get("count", 0)) > 0]

    @property
    def since_label(self) -> str:
        try:
            when: datetime = parse_iso(str(self.digest.get("since", "")))
        except ValueError:
            return ""
        return when.strftime("%d %b %Y %H:%M UTC")

    @property
    def name(self) -> str:
        return plain(self.app_name, 60) or "Insights Radar"


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    text: str
    html: str


def _count(n: int, more: bool) -> str:
    return f"{n}+" if more else str(n)


def _u16(text: str) -> int:
    """A length as Slack counts it: UTF-16 code units, so an emoji or a CJK Extension B character counts two
    (scan cycle 5). Every limit below is in these units."""
    return len(text.encode("utf-16-le")) // 2


def _mrkdwn(text: str) -> dict[str, Any]:
    """A mrkdwn text object Slack takes as written: verbatim, so it auto-links nothing, link-ifies no channel name
    and parses no mention in untrusted text (scan cycle 3); only the app's own <url|label> links are links."""
    return {"type": "mrkdwn", "text": text, "verbatim": True}


_EMPHASIS = str.maketrans({"*": "\u2217", "~": "\u223c", "`": "'", "_": "\uff3f"})
# Every :code: whose colons Slack would read as an emoji, found with a lookahead so a skipped one consumes nothing
# ("10:30:rocket:" still maps ":rocket:"); only an all-digit code right after a digit is a time and is left as
# written ("10:30:45"), while ":100:", ":+1:", ":8ball:" and "3:fire:" are mapped (scan cycles 7 to 9).
_SHORTCODE = re.compile(r"(?=:([A-Za-z0-9+\-][A-Za-z0-9_+\-\uff3f]*):)")


def _shortcodes(s: str) -> str:
    out, end = list(s), 0
    for m in _SHORTCODE.finditer(s):
        i, code = m.start(), m.group(1)
        if i < end or (code.isdigit() and i and s[i - 1].isdigit()):
            continue
        j = i + len(code) + 1
        out[i] = out[j] = "\u2236"
        end = j + 1
    return "".join(out)


def _neutral(text: str, *, line_start: bool = False) -> str:
    """Untrusted text that restyles nothing, with nothing taken out of it (scan cycles 4 and 5): "*" and "~" become
    look-alikes, a backtick an apostrophe, every underscore a fullwidth one and the colons of a :shortcode: ratio
    signs, so nothing can bold, strike, italicise, open a code block or show an emoji (scan cycle 6); a leading
    quote or bullet mark gets a no-break space first."""
    s = _shortcodes(text.translate(_EMPHASIS))
    # Only where the text starts a line: inside a label or a bold heading the space would break it (scan cycle 7).
    return f"\u00a0{s}" if line_start and s[:1] and s[0] in _MARKS else s


def _bounded(text: str, max_len: int, shape: Any) -> str:
    """``shape`` of the text cut on a word to the largest n (at least 1) that keeps the result within TEXT_MAX:
    escaping and unlinking can make text longer, so the bound is on the result. Text that cannot fit is dropped,
    never cut, so no escape is ever split (scan cycle 5). The text is folded once (scan cycle 6)."""
    folded = plain(text, max_len)
    n = max_len
    while n >= 1:
        out = str(shape(_cut(folded, n)))
        if _u16(out) <= TEXT_MAX:
            return out
        n = min(n - 1, n * TEXT_MAX // _u16(out))
    return ""


def _slack_text(text: str, max_len: int, *, line_start: bool = False) -> str:
    return _bounded(text, max_len, lambda s: slack_escape(unlinked(_neutral(s, line_start=line_start))))


def _wire(value: Any) -> int:
    """Bytes as the transport sends them: ``json.dumps`` with its default ASCII escapes, so a non-ASCII character
    counts six bytes and an astral one twelve (scan cycle 8)."""
    return len(json.dumps(value))


class _Group(NamedTuple):
    """A Slack group (scan cycles 14 to 16): its heading, lines in display order, whether the app has more,
    whether the tail counts what is left, the order its lines are taken in and how many sections it may take; for
    the watched records also the record each line belongs to and the line's place in that record (0 for the
    record's first unit)."""

    head: str
    lines: list[str]
    more: bool
    numbered: bool
    order: list[int]
    sections: int
    node_of: list[int] | None = None
    rows: list[int] | None = None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


class SlackRenderer:
    """Block Kit for chat.postMessage or an incoming webhook: ``{"text", "blocks"}``."""

    def render(self, c: DigestContent) -> dict[str, Any]:
        """One message per send (scan cycle 7), never larger than SAFE_PAYLOAD on the wire (scan cycle 8): the full
        form, or the compact form (no summaries) when the byte budget left records out of the full form and the
        compact form shows more of them (scan cycles 10, 13 and 14). Blocks and sections alone never strip
        summaries."""
        message, shown, cut = self._render(c, compact=False)
        if cut:
            compact, more_shown, _ = self._render(c, compact=True)
            if more_shown > shown:
                return compact
        return message

    def _render(self, c: DigestContent, *, compact: bool) -> tuple[dict[str, Any], int, bool]:
        """The message, how many records it shows, and whether the byte limit left records out."""
        name = c.name
        blocks: list[dict[str, Any]] = [
            {"type": "header", "text": {"type": "plain_text", "text": plain(f"{name} digest", HEADER_MAX)}},
        ]
        since = c.since_label
        summary = f"{c.total} new " + ("record" if c.total == 1 else "records") + (f" since {since}" if since else "")
        blocks.append({"type": "context", "elements": [_mrkdwn(slack_escape(summary))]})
        footer = self._footer(c)
        groups: list[_Group] = []
        for _key, label, rows, more in c.lists():
            head = f"*{slack_escape(plain(label, 60))}* ({_count(len(rows), more)})"
            lines = [self._line(c, r, compact) for r in rows]
            groups.append(_Group(head, lines, more, True, list(range(len(rows))), LIST_SECTIONS))
        watched = c.watched()
        if watched:
            # One group whose heading never stands apart from its first record (scan cycle 4), and each record's
            # heading travels with its first row: the two are one unit that is never split (scan cycle 5). Units are
            # shown in order but taken every record's first unit before any record's second (scan cycle 14), so
            # one record with many rows never crowds the others out.
            units: list[str] = []
            rank: list[tuple[int, int]] = []
            node_of: list[int] = []
            places: list[int] = []
            watched_more = False
            for node, w in enumerate(watched):
                found = [self._line(c, r, compact) for r in w.get("new") or []]
                heading = f"*{self._link(c, w.get('node') or {})}*: {int(w.get('count', 0))} new"
                units.append("\n".join([heading, *found[:1]]))
                units += found[1:]
                rank += [(row, node) for row in range(max(len(found), 1))]
                node_of += [node] * max(len(found), 1)
                places += list(range(max(len(found), 1)))
                watched_more = watched_more or int(w.get("count", 0)) > len(found)
            order = sorted(range(len(units)), key=lambda i: rank[i])
            # As many sections as watched records, as on main where each had its own (scan cycle 14); bytes and
            # blocks still bound it.
            sections = max(LIST_SECTIONS, len(watched))
            groups.append(_Group("*Watched records*", units, watched_more, False, order, sections, node_of, places))
        room = MAX_BLOCKS - len(blocks) - len(footer) - 1  # one left for "More in the app"
        fallback = slack_escape(plain(f"{name}: {summary}", FALLBACK_MAX))
        lists = sum(1 for group in groups if group.node_of is None)
        largest = self._more_block(c, lists, bool(watched))  # the longest it can be, set aside before packing
        fixed = ENVELOPE + _wire({"text": fallback, "blocks": [*blocks, largest, *footer]})
        packs, counts, cut = self._admit(groups, c, room, SAFE_PAYLOAD - fixed)
        for texts in packs:
            for t in texts:
                blocks.append({"type": "section", "text": _mrkdwn(t if _u16(t) <= SECTION_MAX else TOO_LONG)})
        left_out = [group for group, texts in zip(groups, packs, strict=True) if group.lines and not texts]
        if left_out:
            missing = sum(1 for group in left_out if group.node_of is None)
            blocks.append(self._more_block(c, missing, any(group.node_of is not None for group in left_out)))
        blocks += footer
        return {"text": fallback, "blocks": blocks}, sum(counts), cut

    def _more_block(self, c: DigestContent, lists: int, watched: bool) -> dict[str, Any]:
        """What a message that leaves whole groups out ends with: how many lists, and whether the watched records,
        are only in the app (scan cycle 16)."""
        parts = ([_plural(lists, "more list")] if lists else []) + (["the watched records"] if watched else [])
        words = "\u2026 and " + " and ".join(parts) + " in the app" if parts else "More in the app"
        return {"type": "section", "text": _mrkdwn(self._tail_link(c, words) if c.digest_url else words + ".")}

    def _admit(
        self, groups: list[_Group], c: DigestContent, room: int, limit: int
    ) -> tuple[list[list[str]], list[int], bool]:
        """Each group's section texts, its record count, and whether the byte limit left records out: one exact
        loop (scan cycle 13) in place of the shares, floors and cuts of cycles 5 to 12. A record goes in only while
        its group still packs into its sections and the whole message, measured as sent, stays within ``limit``
        bytes and the block room. In round r each list takes its next record, and the watched records, which the
        reader chose, take every record's r-th unit, so a watched record's first unit comes before any list's
        second record (scan cycle 15). A list whose next record does not fit is tried whole once, since dropping
        its tail can make it fit (scan cycle 14), and then takes no more; a watched unit that does not fit is
        skipped, with the rest of its record, and the next ones are still tried (scan cycle 15). A group that
        shows nothing is left to "More in the app". Costs are exact section blocks."""
        chosen: list[list[int]] = [[] for _ in groups]
        packs: list[list[str]] = [[] for _ in groups]
        costs = [0] * len(groups)
        size = blocks = 0
        cut = False

        def take(g: int, picked: list[int]) -> bool:
            nonlocal size, blocks, cut
            texts = self._pack(groups[g], picked, c)
            if texts is None or blocks - len(packs[g]) + len(texts) > room:
                return False
            cost = sum(_wire({"type": "section", "text": _mrkdwn(t)}) + 2 for t in texts)
            if size - costs[g] + cost > limit:
                cut = True
                return False
            size, blocks = size - costs[g] + cost, blocks - len(packs[g]) + len(texts)
            chosen[g], packs[g], costs[g] = picked, texts, cost
            return True

        open_ = [bool(group.lines) for group in groups]
        dropped: set[int] = set()  # watched records whose unit did not fit: their later rows are not tried

        def list_next(g: int) -> None:
            group = groups[g]
            nxt = group.order[len(chosen[g])]
            # Dropping the tail can make the whole list fit (scan cycle 14), so the whole list is tried once.
            whole = len(chosen[g]) + 1 < len(group.lines)
            if not take(g, [*chosen[g], nxt]) and not (whole and take(g, list(group.order))):
                open_[g] = False
            open_[g] = open_[g] and len(chosen[g]) < len(group.lines)

        def watched_unit(g: int, i: int) -> None:
            group = groups[g]
            assert group.node_of is not None
            if group.node_of[i] not in dropped and not take(g, [*chosen[g], i]):
                dropped.add(group.node_of[i])

        listed = [g for g, group in enumerate(groups) if group.node_of is None]
        watched = [g for g, group in enumerate(groups) if group.node_of is not None]
        r = 0
        while any(open_):
            for g in watched:
                group = groups[g]
                assert group.rows is not None
                units = [i for i in group.order if group.rows[i] == r]
                if r == 0:
                    # Round 0 alternates a watched record's first unit with a list's first record (scan cycle 16),
                    # so neither the watched records nor the lists can take the whole message first.
                    for k in range(max(len(units), len(listed))):
                        if k < len(units):
                            watched_unit(g, units[k])
                        if k < len(listed) and open_[listed[k]]:
                            list_next(listed[k])
                else:
                    for i in units:
                        watched_unit(g, i)
                open_[g] = any(row > r for row in group.rows)
            for g in listed:
                if open_[g] and not (r == 0 and watched):
                    list_next(g)
            r += 1
        return packs, [len(picked) for picked in chosen], cut

    @staticmethod
    def _tail_link(c: DigestContent, words: str, sep: str = "") -> str:
        url = c.digest_url
        if url and _u16(url) <= TAIL_LINK_MAX:
            return f"{sep}<{url}|{words}>"
        return "" if sep else words  # with a separator the words only make sense as a link

    @staticmethod
    def _link(c: DigestContent, row: dict[str, Any]) -> str:
        """The record's title as a link to its page, or as bare text when there is no address or the address is
        too long to show (scan cycles 2 and 4). A link label is never auto-linked; bare text is, so it is unlinked."""
        title = plain(row.get("title"), TITLE_MAX) or "(untitled)"
        url = c.record_url(str(row.get("id", "")))
        if url:
            label = slack_escape(_neutral(title))
            if _u16(url) + _u16(label) + 3 <= LINK_MAX:
                return f"<{url}|{label}>"
        return _slack_text(title, TITLE_MAX) or "(untitled)"

    def _line(self, c: DigestContent, row: dict[str, Any], compact: bool) -> str:
        """A bullet with the record's title and, under it, up to about three lines of its summary."""
        summary = "" if compact else _slack_text(str(row.get("summary") or ""), SLACK_SUMMARY_MAX, line_start=True)
        return f"\u2022 {self._link(c, row)}" + (f"\n{summary}" if summary else "")

    def _pack(self, group: _Group, picked: list[int], c: DigestContent) -> list[str] | None:
        """The ``picked`` lines in display order as section texts with a tail for the rest; None when they need
        more than the group's sections. A list's tail counts its records left; the watched records' tail counts the
        watched records left out, as email does, or says only that more is in the app (scan cycle 15). Lines go on
        in the next section rather than being cut, and the tail is reserved only where it goes (scan cycle 14).
        Every line is bounded where it is built, so a heading, a line and the tail fit one section together."""
        head, lines, more, numbered, _order, sections, node_of, _rows = group
        left = len(lines) - len(picked)
        if node_of is None:
            tail = self._tail(c, left if numbered else 0) if left or more else ""
        else:
            missing = len(set(node_of)) - len({node_of[i] for i in picked})
            if missing:
                tail = self._tail_link(c, f"\u2026 and {_plural(missing, 'more watched record')} in the app")
            else:
                tail = self._tail(c, 0) if left or more else ""
        texts: list[str] = []
        text, size = head, _u16(head)
        for i in sorted(picked):
            line = lines[i]
            n = _u16(line)
            last = len(texts) + 1 == sections
            reserve = 1 + _u16(tail) if tail and last else 0  # the newline before the tail too (scan cycle 5)
            if size + 1 + n + reserve > SECTION_MAX:
                if last:
                    return None
                texts.append(text)
                text, size = line, n
            else:
                text, size = f"{text}\n{line}", size + 1 + n
        if tail:
            if size + 1 + _u16(tail) > SECTION_MAX:
                if len(texts) + 1 == sections:
                    return None
                texts.append(text)
                text = tail
            else:
                text = f"{text}\n{tail}"
        texts.append(text)
        return texts

    def _tail(self, c: DigestContent, left: int) -> str:
        words = f"\u2026 and {left} more in the app" if left else "\u2026 and more in the app"
        return self._tail_link(c, words)

    def _footer(self, c: DigestContent) -> list[dict[str, Any]]:
        link = self._tail_link(c, "Open the digest", " ").strip()
        text = " \u00b7 ".join([*([link] if link else []), slack_escape(DISCLAIMER)])
        return [{"type": "divider"}, {"type": "context", "elements": [_mrkdwn(text)]}]


class EmailRenderer:
    """An HTML part and a plain-text part with the same content; every untrusted string HTML-escaped (quotes too,
    so nothing can leave an attribute), every link one this app built."""

    def render(self, c: DigestContent, unsubscribe_url: str, manage_url: str) -> RenderedEmail:
        name = c.name
        since = c.since_label
        subject = plain(f"{name}: {c.total} new " + ("record" if c.total == 1 else "records"), 150)
        text: list[str] = [f"{name} digest", f"{c.total} new since {since}" if since else f"{c.total} new", ""]
        parts: list[str] = [
            f'<h1 style="font-size:20px">{_h(name)} digest</h1>',
            f"<p>{_h(f'{c.total} new since {since}' if since else f'{c.total} new')}</p>",
        ]
        for _key, label, rows, more in c.lists():
            title = f"{plain(label, 60)} ({_count(len(rows), more)})"
            text.append(title)
            parts.append(f'<h2 style="font-size:16px">{_h(title)}</h2><ul>')
            for r in rows:
                text.append(self._text_line(c, r))
                parts.append(self._html_item(c, r))
            parts.append("</ul>")
            if more:
                text.append("  ... and more in the app" + (f": {c.digest_url}" if c.digest_url else ""))
                parts.append(self._more(c))
            text.append("")
        watched = c.watched()
        if watched:
            text.append("Watched records")
            parts.append('<h2 style="font-size:16px">Watched records</h2>')
            for w in watched[:EMAIL_WATCHED_MAX]:
                node = w.get("node") or {}
                count = int(w.get("count", 0))
                node_title = plain(node.get("title"), TITLE_MAX) or "(untitled)"
                url = c.record_url(str(node.get("id", "")))
                text.append(f"{unlinked(node_title)}: {count} new" + (f" <{url}>" if url else ""))
                parts.append(f'<h3 style="font-size:14px">{self._anchor(url, node_title)}: {count} new</h3><ul>')
                rows = list(w.get("new") or [])
                for r in rows:
                    text.append(self._text_line(c, r))
                    parts.append(self._html_item(c, r))
                parts.append("</ul>")
                if count > len(rows):
                    # Both parts say so (scan cycle 9).
                    text.append("  ... and more in the app" + (f": {c.digest_url}" if c.digest_url else ""))
                    parts.append(self._more(c))
            if len(watched) > EMAIL_WATCHED_MAX:
                rest = len(watched) - EMAIL_WATCHED_MAX
                text.append(f"... and {_plural(rest, 'more watched record')} in the app")
                parts.append(f"<p>\u2026 and {_plural(rest, 'more watched record')} in the app.</p>")
            text.append("")
        if c.digest_url:
            text.append(f"Open the digest: {c.digest_url}")
            parts.append(f"<p>{self._anchor(c.digest_url, 'Open the digest')}</p>")
        footer = f"{DISCLAIMER} You get this because you set up this digest delivery."
        text += ["", "--", footer, f"Change or stop it: {manage_url}", f"Unsubscribe: {unsubscribe_url}"]
        parts.append(
            f'<hr><p style="font-size:12px;color:#555">{_h(footer)} '
            f"{self._anchor(manage_url, 'Change or stop it')} \u00b7 {self._anchor(unsubscribe_url, 'Unsubscribe')}</p>"
        )
        doc = (
            '<!DOCTYPE html><html><head><meta charset="utf-8"><title>'
            + _h(subject)
            + '</title></head><body style="font-family:sans-serif;max-width:640px">'
            + "".join(parts)
            + "</body></html>"
        )
        return RenderedEmail(subject, "\n".join(text) + "\n", doc)

    @staticmethod
    def _anchor(url: str, label: str) -> str:
        """A link to this app, or the label as bare text, unlinked, when there is no address (scan cycle 1)."""
        return f'<a href="{_h(url)}">{_h(label)}</a>' if url else _h(unlinked(label))

    def _html_item(self, c: DigestContent, row: dict[str, Any]) -> str:
        title = plain(row.get("title"), TITLE_MAX) or "(untitled)"
        summary = _email_summary(row)
        tail = f'<br><span style="color:#555">{_h(summary)}</span>' if summary else ""
        return f"<li>{self._anchor(c.record_url(str(row.get('id', ''))), title)}{tail}</li>"

    @staticmethod
    def _text_line(c: DigestContent, row: dict[str, Any]) -> str:
        title = plain(row.get("title"), TITLE_MAX) or "(untitled)"
        url = c.record_url(str(row.get("id", "")))
        summary = _email_summary(row)
        return f"- {unlinked(title)}" + (f" <{url}>" if url else "") + (f"\n  {summary}" if summary else "")

    def _more(self, c: DigestContent) -> str:
        return f"<p>{self._anchor(c.digest_url, '\u2026 and more in the app')}</p>"


def _email_summary(row: dict[str, Any]) -> str:
    """A record's summary for both email parts: cut on a word, unlinked (scan cycle 4: one helper, not two copies)."""
    return unlinked(short(row.get("summary"), EMAIL_SUMMARY_MAX))


def _h(text: str) -> str:
    return html.escape(text, quote=True)
