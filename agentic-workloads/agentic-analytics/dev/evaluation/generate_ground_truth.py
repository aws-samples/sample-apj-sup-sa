#!/usr/bin/env python3
"""Generate ground truth answers from local PostgreSQL for evaluation.

Queries the timely_unicorn database with explicit account_id filters
(since local superuser bypasses RLS) and saves results to JSON.

Usage:
    python3 generate_ground_truth.py
"""

import json
import psycopg2
from decimal import Decimal

DB_CONFIG = {"dbname": "timely_unicorn", "user": "diponego", "host": "localhost"}
MYTHICAL_ID = "0330c2ef-f3be-4fc0-ae00-6edb9621e092"
MYTHIC_ID = "d667a552-4b25-4a45-9d86-31d901fe30c2"

QUERIES = {
    "mythical_top_revenue_customers": f"""
        SELECT CASE WHEN c.customer_type='individual' THEN concat(c.first_name,' ',c.last_name) ELSE c.organization_name END as customer_name,
            SUM(t.amount)::numeric(10,2) as total_revenue, COUNT(b.booking_id) as total_bookings
        FROM customers c JOIN bookings b ON c.customer_id=b.customer_id JOIN transactions t ON b.booking_id=t.booking_id
        WHERE c.account_id = '{MYTHICAL_ID}'
        GROUP BY c.customer_id, c.customer_type, c.first_name, c.last_name, c.organization_name
        ORDER BY total_revenue DESC LIMIT 5""",
    "mythical_top_revenue_breeds": f"""
        SELECT u.breed, SUM(t.amount)::numeric(10,2) as total_revenue, COUNT(b.booking_id) as total_bookings
        FROM unicorns u JOIN bookings b ON u.unicorn_id=b.unicorn_id JOIN transactions t ON b.booking_id=t.booking_id
        WHERE u.account_id = '{MYTHICAL_ID}'
        GROUP BY u.breed ORDER BY total_revenue DESC LIMIT 5""",
    "mythical_customer_segmentation": f"""
        SELECT customer_segment, customer_count, total_revenue::numeric(10,2)
        FROM customer_segmentation_by_revenue WHERE account_id = '{MYTHICAL_ID}'""",
    "mythical_maintenance_counts": f"""
        SELECT m.maintenance_urgency, count(*)
        FROM unicorns_due_for_maintenance m JOIN unicorns u ON m.unicorn_id=u.unicorn_id
        WHERE u.account_id = '{MYTHICAL_ID}' GROUP BY m.maintenance_urgency""",
    "mythical_counts": f"""
        SELECT 'customers' as entity, count(*) FROM customers WHERE account_id = '{MYTHICAL_ID}'
        UNION ALL SELECT 'unicorns', count(*) FROM unicorns WHERE account_id = '{MYTHICAL_ID}'
        UNION ALL SELECT 'bookings', count(*) FROM bookings WHERE account_id = '{MYTHICAL_ID}'
        UNION ALL SELECT 'transactions', count(*) FROM transactions WHERE account_id = '{MYTHICAL_ID}'""",
    "mythical_total_revenue": f"""
        SELECT SUM(amount)::numeric(12,2) as total_revenue FROM transactions WHERE account_id = '{MYTHICAL_ID}'""",
    "mythical_subscription": f"""
        SELECT ass.account_name, ass.plan_name, ass.account_status, ass.monthly_price::numeric(10,2)
        FROM account_subscription_status ass WHERE ass.account_id = '{MYTHICAL_ID}'""",
    "mythical_clv_top5": f"""
        SELECT clv.customer_name, clv.total_revenue::numeric(10,2) as lifetime_value, clv.total_bookings
        FROM customer_lifetime_value clv WHERE clv.account_id = '{MYTHICAL_ID}'
        ORDER BY clv.total_revenue DESC LIMIT 5""",
    "mythical_unicorn_performance_top5": f"""
        SELECT upc.unicorn_name, upc.breed, upc.total_revenue::numeric(10,2), upc.total_bookings
        FROM unicorn_performance_comparison upc JOIN unicorns u ON upc.unicorn_id=u.unicorn_id
        WHERE u.account_id = '{MYTHICAL_ID}' ORDER BY upc.total_revenue DESC LIMIT 5""",
    "mythical_avg_duration_by_breed": f"""
        SELECT u.breed, AVG(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600)::numeric(10,2) as avg_hours, count(*) as total
        FROM bookings b JOIN unicorns u ON b.unicorn_id = u.unicorn_id
        WHERE b.account_id = '{MYTHICAL_ID}' GROUP BY u.breed ORDER BY avg_hours DESC""",
    "mythical_search_mfaranwe": f"""
        SELECT customer_id, first_name, last_name FROM customers
        WHERE account_id = '{MYTHICAL_ID}' AND (first_name ILIKE '%mfaranwe%' OR last_name ILIKE '%mfaranwe%')""",
    "mythical_search_starlight": f"""
        SELECT unicorn_id, friendly_name, name, breed, hourly_rate::numeric(10,2)
        FROM unicorns WHERE account_id = '{MYTHICAL_ID}' AND (friendly_name ILIKE '%starlight%' OR name ILIKE '%starlight%')""",
    # Mythic Unicorns for RLS comparison
    "mythic_top_revenue_customers": f"""
        SELECT CASE WHEN c.customer_type='individual' THEN concat(c.first_name,' ',c.last_name) ELSE c.organization_name END as customer_name,
            SUM(t.amount)::numeric(10,2) as total_revenue, COUNT(b.booking_id) as total_bookings
        FROM customers c JOIN bookings b ON c.customer_id=b.customer_id JOIN transactions t ON b.booking_id=t.booking_id
        WHERE c.account_id = '{MYTHIC_ID}'
        GROUP BY c.customer_id, c.customer_type, c.first_name, c.last_name, c.organization_name
        ORDER BY total_revenue DESC LIMIT 5""",
    "mythic_total_revenue": f"""
        SELECT SUM(amount)::numeric(12,2) as total_revenue FROM transactions WHERE account_id = '{MYTHIC_ID}'""",
    "mythic_counts": f"""
        SELECT 'customers' as entity, count(*) FROM customers WHERE account_id = '{MYTHIC_ID}'
        UNION ALL SELECT 'unicorns', count(*) FROM unicorns WHERE account_id = '{MYTHIC_ID}'
        UNION ALL SELECT 'bookings', count(*) FROM bookings WHERE account_id = '{MYTHIC_ID}'""",
}


def decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    results = {}
    for name, sql in QUERIES.items():
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            results[name] = rows
            print(f"[OK] {name}: {len(rows)} rows")
    conn.close()

    out_path = "dataset/validation/ground_truth.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=decimal_default)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
