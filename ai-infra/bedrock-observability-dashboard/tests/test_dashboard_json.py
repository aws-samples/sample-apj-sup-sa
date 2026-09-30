import itertools
import json

from scripts.render_dashboard import render


def _ct_only_body():
    return json.loads(render(3, {"ModelInvocationLogGroup": "",
                                  "CloudTrailLogGroup": "/aws/cloudtrail/test"}))


def _rects(body):
    return [(w["x"], w["y"], w["width"], w["height"]) for w in body["widgets"]]


def _overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


def _assert_no_overlap(body):
    for a, b in itertools.combinations(_rects(body), 2):
        assert not _overlap(a, b), f"overlap {a} {b}"


def test_no_widget_overlap_all_tiers(body_tier1, body_tier2, body_tier3):
    for body in (body_tier1, body_tier2, body_tier3, _ct_only_body()):
        _assert_no_overlap(body)


def test_widgets_within_grid(body_tier1, body_tier2, body_tier3):
    for body in (body_tier1, body_tier2, body_tier3, _ct_only_body()):
        for w in body["widgets"]:
            assert 0 <= w["x"] and w["x"] + w["width"] <= 24, w


def test_cloudtrail_only_no_layout_gap():
    # Tier 3 params but NO invocation logs: Section 7 must start at y=47 (no gap)
    body = json.loads(render(3, {"ModelInvocationLogGroup": "",
                                  "CloudTrailLogGroup": "/aws/cloudtrail/test"}))
    s7 = [w for w in body["widgets"]
          if w["type"] == "text" and "7. Identity" in w["properties"].get("markdown", "")]
    assert s7 and s7[0]["y"] == 47, "Section 7 should start at 47 when no Section 6"
