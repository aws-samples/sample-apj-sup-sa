# Unicorn Rental Dataset

## Overview

Synthetic dataset for the Unicorn Rental rental management system - a multi-tenant SaaS platform for unicorn rental businesses. Contains ~14,000 bookings, transactions, and 30,000+ availability records across 3 rental business accounts.

## Multi-Tenant Model

| Tier | Entity | Description |
|------|--------|-------------|
| Platform | Timely-Unicorn | SaaS provider |
| Tenant | Accounts | Unicorn rental businesses subscribing to the platform |
| End User | Customers | Renters who book unicorns from rental businesses |

Most data tables include `account_id` for tenant isolation.

## Database Schema

### Core Tables

| Table | Records | Description |
|-------|---------|-------------|
| `accounts` | 3 | Rental business accounts with subscription info |
| `subscription_plans` | 5 | Plan tiers (Free, Starter, Small Business, Enterprise, Custom) |
| `users` | 10 | Staff members (admin, staff roles) |
| `customers` | 500 | Renters (individual and organization types) |
| `unicorns` | 100 | Rental inventory with specs and hourly rates |
| `unicorn_availability` | 30,735 | Insert-only availability history |
| `bookings` | 13,912 | Rental bookings |
| `transactions` | 13,912 | Financial records |
| `subscription_tracker` | 17,520 | Hourly subscription status tracking |

### Key Features

- **PostgreSQL ENUM types** for constrained values (customer_type, status, etc.)
- **UUID primary keys** for all entities
- **Automatic availability sync** via trigger - inserting into `unicorn_availability` updates `unicorns.is_available`
- **On-the-spot booking model** - bookings start immediately upon creation
- **Hourly subscription tracking** - tracks plan changes and revenue on an hourly basis

### Views

#### Operational
- `daily_bookings_summary` - Bookings with customer/unicorn details
- `calendar_bookings` - Calendar-formatted booking data
- `current_unicorn_availability` - Real-time unicorn status

#### Financial
- `monthly_revenue_summary` - Monthly aggregated revenue
- `top_revenue_unicorns_by_period` - Revenue by unicorn/period
- `top_revenue_unicorn_breeds` - Revenue by breed
- `bottom_revenue_unicorns` / `bottom_revenue_breeds` - Lowest performers
- `top_revenue_customers` - Customer revenue rankings
- `revenue_by_time_and_day` - Revenue patterns by day/hour

#### Analytics
- `unicorn_utilization_rates` - Usage statistics per unicorn
- `unicorn_performance_comparison` - Comprehensive performance metrics
- `customer_retention_metrics` - Retention analysis
- `customer_segmentation_by_revenue` - Customer tiers (VIP, Premium, Standard, Basic)
- `staff_performance_metrics` - Staff productivity
- `peak_periods` - High-demand time identification
- `subscription_tracker_summary` - Hourly subscription status tracking for revenue analysis

## Data Files

```
data/
├── accounts.csv
├── bookings.csv
├── customers.csv
├── subscription_plans.csv
├── subscription_tracker.csv
├── transactions.csv
├── unicorns.csv
├── unicorn_availability.csv
├── unicorn_availability_initial.csv
└── users.csv
```

## Directory Structure

```
├── schema/
│   └── schema.sql              # Complete DDL with tables, views, triggers
├── data/                       # CSV data files
├── docs/
│   ├── business-context.md     # Detailed table schemas and relationships
│   └── user-story.md           # User stories for the system
├── cube_models/
│   ├── initial_model/          # Initial Cube semantic model
│   └── final_model/            # Final Cube semantic model
├── validation/                 # Ground truth and experiment data
├── tests/
│   └── test-schema-creation.sh
├── LICENSE
└── README.md
```

## Documentation

- **[business-context.md](docs/business-context.md)** - Table schemas, relationships, and business rules
- **[user-story.md](docs/user-story.md)** - User stories for Rental Admin and Staff roles


## License

This dataset is licensed under the CC0 1.0 Universal (CC0-1.0) License. See the [LICENSE](LICENSE) file for details.

## Testing

```bash
# Test schema creation
./tests/test-schema-creation.sh
```
