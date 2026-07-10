"""Travel-as-coding prompt + seed for Qwen3-Coder-Next benchmarking.

The travel sample-data is reused as INPUT, but the SYSTEM_PROMPT asks for
**code generation against the JSON** (e.g., a Python function that parses
the email into a dataclass). This adds coverage of a coding-specialist
model on the same plumbing.
"""
from __future__ import annotations


SYSTEM_PROMPT = (
    "You are an expert software engineer. Given a travel booking "
    "confirmation email, return ONLY a single Python code block (no prose, "
    "no markdown headers) that defines:\n"
    "1. A `Booking` Python @dataclass mirroring the booking schema:\n"
    "   booking_reference, provider, travelers (list[str]), segments\n"
    "   (list of `Segment` with mode, origin, destination, depart, arrive,\n"
    "   carrier, travel_class), total_price (TotalPrice with amount,\n"
    "   currency), payment_method, cancellation_policy.\n"
    "2. A `Segment` and `TotalPrice` @dataclass.\n"
    "3. A `parse_email(text: str) -> Booking` function that returns a fully\n"
    "   populated Booking from the email text. Use `re` only; no external\n"
    "   libraries. Datetimes must be `datetime.datetime` ISO-parsed. Return\n"
    "   None for fields not present in the email — never invent values.\n"
    "Do NOT include `import` statements other than `re`, `dataclasses`, and\n"
    "`datetime`. Do NOT include test code or examples; just the parser."
)
"""System prompt — code generation against travel JSON."""


SEED_INPUT = (
    "Subject: Your Delta Air Lines Flight is Confirmed - PNR: XYZ789\n\n"
    "From: no_reply@delta.com\n\n"
    "Dear Ms. Emily Johnson,\n\n"
    "We are pleased to confirm your round-trip ticket with Delta Air Lines.\n\n"
    "Booking Reference: XYZ789\n"
    "Traveler: Emily Johnson\n\n"
    "Itinerary:\n"
    "Outbound: New York (LGA) to Los Angeles (LAX) on 2025-04-10 at 08:15 AM, "
    "Flight DL142, Economy Class\n"
    "Return: Los Angeles (LAX) to New York (LGA) on 2025-04-17 at 05:30 PM, "
    "Flight DL143, Economy Class\n\n"
    "Total Price: $455.00\n\n"
    "Payment Method: Visa ending in **** 4321\n\n"
    "Cancellation/Change Policy:\n"
    "Cancellations made at least 14 days before departure will incur a $50 fee."
)


__all__ = ["SYSTEM_PROMPT", "SEED_INPUT"]
