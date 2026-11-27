# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Sample data: a small, linked graph and a radar on top of it, so a new deployment has something to show.

The market is fictional (AnyCompany and friends selling CRM software). Loading is idempotent:
entities are matched by name, other nodes have fixed ids (a second load finds them and skips
them), edges are upserts, and the radar is only created when its address is free.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from insights_radar.core.errors import ConflictError, ValidationError
from insights_radar.graph.model import Node
from insights_radar.graph.registry import EdgeType, NodeType
from insights_radar.graph.repository import GraphRepository
from insights_radar.identity.principal import Principal
from insights_radar.radars.model import RadarEntry
from insights_radar.radars.service import RadarService

RADAR_SLUG = "sample-crm-competitors"
RADAR_NAME = "Sample: CRM competitors"
RADAR_DESCRIPTION = "Sample data. Each entry links to its organization in the knowledge graph."
_URL = "https://example.com/sample/"

# (type, title, summary)
ENTITIES: tuple[tuple[NodeType, str, str], ...] = (
    (NodeType.ORGANIZATION, "AnyCompany", "Us: a CRM vendor for mid-market sales teams."),
    (NodeType.ORGANIZATION, "Example Corp", "The market leader in mid-market CRM."),
    (NodeType.ORGANIZATION, "Octank", "A fast-growing challenger with an AI-first CRM."),
    (NodeType.ORGANIZATION, "Northwind Traders", "A spreadsheet-style tool many small teams use instead of a CRM."),
    (NodeType.ORGANIZATION, "Contoso Labs", "A start-up building agentic sales assistants."),
    (NodeType.PRODUCT, "AnyCompany CRM", "Our CRM product."),
    (NodeType.PRODUCT, "Example Sales Cloud", "Example Corp's flagship CRM."),
    (NodeType.PRODUCT, "Octank Copilot", "Octank's AI assistant that drafts follow-up emails."),
    (NodeType.TOPIC, "AI sales assistants", "Assistants that draft, summarise and log sales activity."),
    (NodeType.TOPIC, "Usage-based pricing", "Charging per active seat or per action instead of per licence."),
    (NodeType.TOPIC, "Data residency", "Keeping customer data in a chosen country or Region."),
    (NodeType.SEGMENT, "Mid-market sales teams", "Companies with 50 to 500 sellers."),
)

# (id, type, title, summary, about)
EVIDENCE: tuple[tuple[str, NodeType, str, str, tuple[str, ...]], ...] = (
    (
        "fact/sample-01",
        NodeType.FACT,
        "Octank doubled its customer count in a year",
        "Octank says it now has twice as many paying customers as a year ago.",
        ("Octank",),
    ),
    (
        "event/sample-02",
        NodeType.EVENT,
        "Example Corp launched an AI assistant",
        "Example Sales Cloud now drafts follow-ups and summarises calls.",
        ("Example Corp", "AI sales assistants"),
    ),
    (
        "fact/sample-03",
        NodeType.FACT,
        "Octank moved to usage-based pricing",
        "Octank charges per active seat each month instead of annual licences.",
        ("Octank", "Usage-based pricing"),
    ),
    (
        "opinion/sample-04",
        NodeType.OPINION,
        "Sellers want fewer manual CRM updates",
        "In a community thread, sales managers said data entry is their main complaint.",
        ("Mid-market sales teams",),
    ),
    (
        "fact/sample-05",
        NodeType.FACT,
        "Contoso Labs raised a seed round",
        "Contoso Labs raised funding to build agentic sales assistants.",
        ("Contoso Labs", "AI sales assistants"),
    ),
    (
        "event/sample-06",
        NodeType.EVENT,
        "New data-residency rules for customer data",
        "A regulator published guidance on where customer records may be stored.",
        ("Data residency",),
    ),
    (
        "fact/sample-07",
        NodeType.FACT,
        "Small teams keep using spreadsheets",
        "A survey found many small sales teams track deals in spreadsheet tools.",
        ("Northwind Traders",),
    ),
    (
        "opinion/sample-08",
        NodeType.OPINION,
        "Buyers ask for AI features in RFPs",
        "Analysts note AI assistance now appears in most CRM requests for proposal.",
        ("AI sales assistants",),
    ),
)

# (id, type, title, summary, about, supported_by, addresses)
OUTPUTS: tuple[tuple[str, NodeType, str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "signal/sample-11",
        NodeType.SIGNAL,
        "Octank is gaining quickly",
        "Customer growth and simpler pricing put Octank on a fast climb.",
        ("Octank",),
        ("fact/sample-01", "fact/sample-03"),
        (),
    ),
    (
        "signal/sample-12",
        NodeType.SIGNAL,
        "Agentic start-ups are entering the market",
        "New funding for assistants that act on the CRM, not just in it.",
        ("Contoso Labs",),
        ("fact/sample-05",),
        (),
    ),
    (
        "trend/sample-13",
        NodeType.TREND,
        "AI assistants become table stakes",
        "The leader and challengers ship assistants; buyers now expect them.",
        ("AI sales assistants",),
        ("event/sample-02", "opinion/sample-08"),
        (),
    ),
    (
        "trend/sample-14",
        NodeType.TREND,
        "Pricing moves to usage",
        "Seat-based annual licences give way to monthly usage pricing.",
        ("Usage-based pricing",),
        ("fact/sample-03",),
        (),
    ),
    (
        "gap/sample-15",
        NodeType.GAP,
        "AnyCompany CRM has no AI assistant",
        "Competitors draft follow-ups for sellers; we do not.",
        ("AnyCompany CRM", "AI sales assistants"),
        ("opinion/sample-04",),
        ("trend/sample-13",),
    ),
    (
        "content_idea/sample-16",
        NodeType.CONTENT_IDEA,
        "Guide: cutting CRM data entry in half",
        "A how-to for sales managers, answering the most common complaint.",
        ("Mid-market sales teams",),
        ("opinion/sample-04",),
        ("gap/sample-15",),
    ),
)

# (source, edge, target) between entities, by title
RELATIONS: tuple[tuple[str, EdgeType, str], ...] = (
    ("AnyCompany", EdgeType.OFFERS, "AnyCompany CRM"),
    ("Example Corp", EdgeType.OFFERS, "Example Sales Cloud"),
    ("Octank", EdgeType.OFFERS, "Octank Copilot"),
    ("Octank", EdgeType.COMPETES_WITH, "AnyCompany"),
    ("Example Corp", EdgeType.COMPETES_WITH, "AnyCompany"),
    ("AnyCompany CRM", EdgeType.TARGETS, "Mid-market sales teams"),
    ("Example Sales Cloud", EdgeType.TARGETS, "Mid-market sales teams"),
    ("Octank Copilot", EdgeType.USES, "AI sales assistants"),
)

# (title, sector, ring, marker, description) for the sample radar; titles are organizations above
RADAR_ENTRIES: tuple[tuple[str, str, str, str, str], ...] = (
    ("Example Corp", "Direct", "Leader", "Steady", "The market leader; just shipped an AI assistant."),
    ("Octank", "Direct", "Challenger", "Gaining", "Doubled customers in a year on usage-based pricing."),
    ("Contoso Labs", "Emerging", "Watch", "Gaining", "Seed-funded agentic sales assistant."),
    ("Northwind Traders", "Substitutes", "Niche", "Losing", "Spreadsheets that small teams use instead of a CRM."),
)


@dataclass
class SampleResult:
    nodes_created: int = 0
    edges_created: int = 0
    radar: str | None = None
    skipped: list[str] = field(default_factory=list)

    def to_public(self) -> dict[str, object]:
        return {
            "nodes_created": self.nodes_created,
            "edges_created": self.edges_created,
            "radar": self.radar,
            "skipped": self.skipped,
        }


class SampleData:
    """Loads the sample graph and radar. Safe to run more than once."""

    def __init__(self, graph: GraphRepository, radars: RadarService) -> None:
        self._graph = graph
        self._radars = radars

    @staticmethod
    def is_sample(node_id: str) -> bool:
        """Whether ``node_id`` is one of the sample graph's fixed ids (``fact/sample-01``). They are
        published here, so anyone can cite them; a real node's id is a uuid7, never ``sample-``."""
        return node_id.partition("/")[2].startswith("sample-")

    def load(self, principal: Principal) -> SampleResult:
        actor = principal.actor
        result = SampleResult()
        ids: dict[str, str] = {}
        for node_type, title, summary in ENTITIES:
            node, created = self._graph.upsert_entity(Node(id="", type=node_type, title=title, summary=summary), actor)
            ids[title] = node.id
            result.nodes_created += int(created)
        drafts = [
            Node(id=i, type=t, title=title, summary=summary, evidence_url=f"{_URL}{i.split('/')[1]}")
            for i, t, title, summary, _ in EVIDENCE
        ] + [Node(id=o[0], type=o[1], title=o[2], summary=o[3], confidence=0.7) for o in OUTPUTS]
        for draft in drafts:
            try:
                self._graph.create(draft, actor)
                result.nodes_created += 1
            except ConflictError:
                result.skipped.append(draft.id)
        links: list[tuple[str, EdgeType, str]] = [(ids[s], e, ids[t]) for s, e, t in RELATIONS]
        for node_id, _, _, _, about in EVIDENCE:
            links += [(node_id, EdgeType.ABOUT, ids[a]) for a in about]
        for node_id, _, _, _, about, support, addresses in OUTPUTS:
            links += [(node_id, EdgeType.ABOUT, ids[a]) for a in about]
            links += [(node_id, EdgeType.SUPPORTS, s) for s in support]
            links += [(node_id, EdgeType.ADDRESSES, a) for a in addresses]
        for source, edge, target in links:
            _, created = self._graph.link(source, edge, target, actor)
            result.edges_created += int(created)
        result.radar = self._radar(principal, ids, result)
        return result

    def _radar(self, principal: Principal, ids: dict[str, str], result: SampleResult) -> str | None:
        """Create the sample radar, or finish one whose entries were never saved.

        A radar at the sample address that this loader did not create (someone claimed the
        address) is left alone and not reported as the sample.
        """
        try:
            radar = self._radars.create(
                principal, RADAR_SLUG, RADAR_NAME, template="competitor_landscape", description=RADAR_DESCRIPTION
            )
        except ValidationError as err:  # the radar cap: the graph sample stands, and the report says so
            result.skipped.append(f"radar/{RADAR_SLUG} ({err.message})")
            return None
        except ConflictError:
            radar = self._radars.get(RADAR_SLUG)
            if radar.name != RADAR_NAME or radar.description != RADAR_DESCRIPTION:
                result.skipped.append(f"radar/{RADAR_SLUG} (address taken by another radar)")
                return None
            if radar.entry_count:
                result.skipped.append(f"radar/{RADAR_SLUG}")
                return RADAR_SLUG
        entries = [
            RadarEntry(key=f"sample-{i}", title=t, sector=s, ring=r, marker=m, description=d, node_id=ids[t])
            for i, (t, s, r, m, d) in enumerate(RADAR_ENTRIES, start=1)
        ]
        self._radars.save(principal, RADAR_SLUG, entries, radar.current_version, note="sample data")
        return RADAR_SLUG
