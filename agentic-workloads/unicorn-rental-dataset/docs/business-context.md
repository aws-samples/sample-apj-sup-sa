# Timely-Unicorn Rental Management System - Business Context

## Multi-Tenant Model

The Timely-Unicorn platform follows a multi-tenant architecture where:

1. **Unicorn Rental Businesses** (Accounts) - These are the SaaS customers who subscribe to the platform to manage their unicorn rental operations
2. **Renters** (Customers) - These are the end users who rent unicorns from the rental businesses
3. **SaaS Provider** - Timely-Unicorn platform itself, which is not represented in the database

Each unicorn rental business (account) operates in isolation with their own data, customers, unicorns, bookings, and transactions.

## Accounts Table (Unicorn Rental Businesses)

### Business Context
The accounts table represents the unicorn rental businesses that are customers of the Timely-Unicorn SaaS platform. Each account corresponds to a unique rental business that subscribes to the platform's services. This table tracks subscription status, billing information, and resource utilization for each rental business.

Accounts are the primary entity for billing and resource allocation. They link to chosen subscription plans and track usage against plan limits. Each rental business has multiple users (staff members) associated with their account.

The table also manages account lifecycle including activation, suspension, and termination. Storage usage tracking is critical for enforcing plan limits.

This table now also includes organization details that were previously in a separate organizations table, providing a unified view of the rental business.

### Relationships
- Linked to **subscription_plans** table to determine plan features and limits
- Linked to **users** table to associate staff members with rental businesses
- Referenced by **transactions** table for billing records
- Referenced by **bookings** table to associate rentals with rental businesses
- Referenced by all data plane tables to ensure tenant isolation

### Schema
```yaml
table: accounts
cols:
  - account_id: {type: UUID, desc: Unique identifier for rental business account, cons: PK}
  - plan_id: {type: UUID, desc: Reference to subscription plan, cons: FK, NOT NULL}
  - account_name: {type: VARCHAR(255), desc: Display name for the rental business, cons: NOT NULL}
  - status: {type: ENUM('active', 'suspended', 'terminated'), desc: Current account status, cons: NOT NULL}
  - current_storage_usage_gb: {type: DECIMAL(10,2), desc: Current storage usage in GB, cons: NOT NULL, DEFAULT 0}
  - current_user_count: {type: INTEGER, desc: Current number of active users, cons: NOT NULL, DEFAULT 0}
  - billing_email: {type: VARCHAR(255), desc: Email for billing communications, cons: NOT NULL}
  - billing_address_line1: {type: VARCHAR(255), desc: Billing address line 1, cons: NULL}
  - billing_address_line2: {type: VARCHAR(255), desc: Billing address line 2, cons: NULL}
  - billing_city: {type: VARCHAR(100), desc: Billing city, cons: NULL}
  - billing_state_province: {type: VARCHAR(100), desc: Billing state or province, cons: NULL}
  - billing_postal_code: {type: VARCHAR(20), desc: Billing ZIP or postal code, cons: NULL}
  - billing_country: {type: VARCHAR(100), desc: Billing country, cons: NULL}
  - next_billing_date: {type: DATE, desc: Date of next billing cycle, cons: NULL}
  - trial_end_date: {type: DATE, desc: End date of trial period, cons: NULL}
  - activated_at: {type: TIMESTAMP, desc: When account became active, cons: NULL}
  - suspended_at: {type: TIMESTAMP, desc: When account was suspended, cons: NULL}
  - terminated_at: {type: TIMESTAMP, desc: When account was terminated, cons: NULL}
  - industry: {type: VARCHAR(100), desc: Industry sector of organization, cons: NULL}
  - employee_count: {type: INTEGER, desc: Number of employees, cons: NULL}
  - website: {type: VARCHAR(255), desc: Organization website URL, cons: NULL}
  - billing_cycle: {type: ENUM('monthly', 'quarterly', 'annual'), desc: Preferred billing cycle, cons: DEFAULT 'monthly'}
  - headquarters_address_line1: {type: VARCHAR(255), desc: HQ street address line 1, cons: NULL}
  - headquarters_address_line2: {type: VARCHAR(255), desc: HQ street address line 2, cons: NULL}
  - headquarters_city: {type: VARCHAR(100), desc: HQ city, cons: NULL}
  - headquarters_state_province: {type: VARCHAR(100), desc: HQ state or province, cons: NULL}
  - headquarters_postal_code: {type: VARCHAR(20), desc: HQ ZIP or postal code, cons: NULL}
  - headquarters_country: {type: VARCHAR(100), desc: HQ country, cons: NULL}
  - created_at: {type: TIMESTAMP, desc: Record creation timestamp, cons: NOT NULL, DEFAULT CURRENT_TIMESTAMP}
  - updated_at: {type: TIMESTAMP, desc: Last record update timestamp, cons: NOT NULL, DEFAULT CURRENT_TIMESTAMP}
```

### SQL Examples
```sql
-- Get account details with subscription plan info
SELECT a.account_id, a.account_name, a.status, sp.plan_name, sp.monthly_price,
       a.current_user_count, sp.user_limit, a.current_storage_usage_gb, sp.storage_limit_gb
FROM accounts a
JOIN subscription_plans sp ON a.plan_id = sp.plan_id
WHERE a.account_id = :account_id;

-- Check account subscription usage percentage
SELECT * FROM account_subscription_status WHERE account_id = :account_id;
```

## Subscription Plans Table

### Business Context
The subscription_plans table defines the various pricing tiers available for unicorn rental businesses. Each plan offers different levels of service, user limits, and storage capacity. The platform offers five distinct plans: Free, Starter, Small Business, Enterprise, and Custom.

This table serves as the foundation for the platform's monetization strategy, allowing rental businesses to select the plan that best fits their needs and budget. The Free plan is designed to attract new businesses with basic functionality, while higher-tier plans unlock advanced features and increased capacity.

Custom plans allow for negotiation of specific terms for large businesses with unique requirements. Storage limits are particularly important as they directly impact the platform's infrastructure costs and help differentiate the value proposition of each tier.

### Relationships
- Linked to **accounts** table to assign plans to rental business accounts
- Referenced by **accounts** table to enforce plan limits (user count, storage)

### Schema
```yaml
table: subscription_plans
cols:
  - plan_id: {type: UUID, desc: Unique identifier for subscription plan, cons: PK}
  - plan_name: {type: VARCHAR(50), desc: Name of the plan (Free, Starter, etc.), cons: NOT NULL, UNIQUE}
  - user_limit: {type: INTEGER, desc: Maximum number of users allowed, cons: NOT NULL}
  - storage_limit_gb: {type: DECIMAL(10,2), desc: Storage limit in gigabytes, cons: NOT NULL}
  - monthly_price: {type: DECIMAL(10,2), desc: Monthly price in USD, cons: NOT NULL}
  - is_custom: {type: BOOLEAN, desc: Whether plan requires custom negotiation, cons: NOT NULL, DEFAULT FALSE}
  - description: {type: TEXT, desc: Detailed description of plan features, cons: NULL}
  - is_active: {type: BOOLEAN, desc: Whether plan is currently available, cons: NOT NULL, DEFAULT TRUE}
  - created_at: {type: TIMESTAMP, desc: Record creation timestamp, cons: NOT NULL, DEFAULT CURRENT_TIMESTAMP}
  - updated_at: {type: TIMESTAMP, desc: Last record update timestamp, cons: NOT NULL, DEFAULT CURRENT_TIMESTAMP}
```

### SQL Examples
```sql
-- Get all active subscription plans
SELECT plan_id, plan_name, monthly_price, user_limit, storage_limit_gb, description
FROM subscription_plans
WHERE is_active = TRUE
ORDER BY monthly_price;
```

## Customers Table (Renters)

### Business Context
The customers table stores information about all entities that rent unicorns from the unicorn rental businesses using the timely-unicorn platform. These are the end users of the platform, not the SaaS customers. Customers can be of two types: individual persons or organizations (B2B/B2G). This distinction is important because organizational customers often have different billing arrangements.

Individual customers represent end-users who rent unicorns for personal use from a rental business. Organizational customers represent businesses or government entities that rent unicorns, possibly for multiple employees or departments. For organizational customers, department and title information can be stored to better understand the customer's role within their organization.

The customer table serves as a central repository for all customer information for each rental business, ensuring consistent communication and service delivery across all interactions.

### Relationships
- Linked to **bookings** table through customer_id to track rental history
- Linked to **transactions** table through customer_id for billing purposes
- All records are implicitly linked to an **account** through the multi-tenant model

### Schema
```yaml
table: customers
cols:
  - customer_id: {type: UUID, desc: Unique identifier for customer, cons: PK}
  - account_id: {type: UUID, desc: Reference to rental business account, cons: FK, NOT NULL}
  - customer_type: {type: ENUM('individual', 'organization'), desc: Type of customer (person or org), cons: NOT NULL}
  - first_name: {type: VARCHAR(100), desc: First name of customer or contact, cons: NOT NULL}
  - last_name: {type: VARCHAR(100), desc: Last name of customer or contact, cons: NULL}
  - organization_name: {type: VARCHAR(255), desc: Name of organization (org customers), cons: NULL}
  - email: {type: VARCHAR(255), desc: Primary email for communications, cons: NOT NULL}
  - phone_number: {type: VARCHAR(20), desc: Primary phone number, cons: NULL}
  - address_line1: {type: VARCHAR(255), desc: First line of street address, cons: NULL}
  - address_line2: {type: VARCHAR(255), desc: Second line of address (apt/suite), cons: NULL}
  - city: {type: VARCHAR(100), desc: City name, cons: NULL}
  - state_province: {type: VARCHAR(100), desc: State or province, cons: NULL}
  - postal_code: {type: VARCHAR(20), desc: ZIP or postal code, cons: NULL}
  - country: {type: VARCHAR(100), desc: Country name, cons: NULL}
  - billing_preference: {type: ENUM('email', 'mail', 'both'), desc: Preferred invoice delivery method, cons: DEFAULT 'email'}
  - department: {type: VARCHAR(100), desc: Department of organizational customer, cons: NULL}
  - title: {type: VARCHAR(100), desc: Job title of customer, cons: NULL}
  - created_at: {type: TIMESTAMP, desc: Record creation timestamp, cons: NOT NULL, DEFAULT CURRENT_TIMESTAMP}
  - updated_at: {type: TIMESTAMP, desc: Last record update timestamp, cons: NOT NULL, DEFAULT CURRENT_TIMESTAMP}
```

### SQL Examples
```sql
-- Search customers by name or email
SELECT customer_id, first_name, last_name, organization_name, email, customer_type
FROM customers
WHERE account_id = :account_id
  AND (LOWER(first_name) LIKE LOWER(:search) OR LOWER(last_name) LIKE LOWER(:search) 
       OR LOWER(email) LIKE LOWER(:search) OR LOWER(organization_name) LIKE LOWER(:search))
LIMIT 20;

-- Get top customers by revenue
SELECT * FROM top_revenue_customers WHERE account_id = :account_id LIMIT 10;

-- Get customer lifetime value metrics
SELECT * FROM customer_lifetime_value WHERE account_id = :account_id ORDER BY total_revenue DESC LIMIT 10;

-- Get customer segmentation breakdown (VIP, Premium, Standard, Basic)
SELECT * FROM customer_segmentation_by_revenue WHERE account_id = :account_id;

-- Get customers at risk of churning
SELECT * FROM customer_retention_metrics 
WHERE retention_segment = 'at_risk';
```

## Unicorns Table

### Business Context
The unicorns table represents the fleet of unicorns available for rental by each unicorn rental business. Each unicorn is owned by a specific rental business (account) and has unique characteristics such as breed, color, horn material, and magical abilities. 

Unicorns are the core assets of the rental business, and their availability status is critical for managing bookings and customer satisfaction. The platform tracks detailed information about each unicorn including maintenance schedules, rental rates, and specifications.

With the new availability tracking approach, each unicorn now has a direct boolean indicator of its current availability status. This simplifies queries for available unicorns and improves performance by avoiding complex joins and subqueries. The availability status is automatically maintained by a database trigger that monitors the insert-only unicorn_availability table.

### Relationships
- Linked to **accounts** table to associate unicorns with rental businesses
- Referenced by **bookings** table to track rental history
- Referenced by **unicorn_availability** table to maintain detailed availability history
- Referenced by **current_unicorn_availability** view to provide real-time availability status

### Schema
```yaml
table: unicorns
cols:
  - unicorn_id: {type: UUID, desc: Unique identifier for unicorn, cons: PK}
  - account_id: {type: UUID, desc: Reference to rental business account, cons: FK, NOT NULL}
  - unicorn_uid: {type: VARCHAR(50), desc: Unique identifier within account, cons: NOT NULL, UNIQUE}
  - name: {type: VARCHAR(100), desc: Official name of unicorn, cons: NOT NULL}
  - friendly_name: {type: VARCHAR(100), desc: Nickname for unicorn, cons: NULL}
  - year_of_making: {type: INTEGER, desc: Year unicorn was created, cons: NOT NULL}
  - breed: {type: VARCHAR(100), desc: Unicorn breed classification, cons: NULL}
  - color: {type: VARCHAR(50), desc: Primary color of unicorn, cons: NULL}
  - horn_length_cm: {type: DECIMAL(5,2), desc: Length of horn in centimeters, cons: NULL}
  - horn_material: {type: VARCHAR(50), desc: Material composition of horn, cons: NULL}
  - seat_capacity: {type: INTEGER, desc: Number of passengers that can ride, cons: NOT NULL}
  - magic_abilities: {type: TEXT, desc: Description of magical abilities, cons: NULL}
  - max_speed_kmh: {type: DECIMAL(5,2), desc: Maximum speed in kilometers per hour, cons: NULL}
  - fuel_type: {type: VARCHAR(50), desc: Type of energy source, cons: NULL}
  - fuel_capacity: {type: DECIMAL(10,2), desc: Energy capacity measurement, cons: NULL}
  - hourly_rate: {type: DECIMAL(10,2), desc: Rental rate per hour in USD, cons: NOT NULL, DEFAULT 0}
  - last_service_date: {type: DATE, desc: Date of last maintenance service, cons: NULL}
  - next_service_due: {type: DATE, desc: Date of next scheduled maintenance, cons: NULL}
  - purchase_date: {type: DATE, desc: Date unicorn was acquired, cons: NULL}
  - purchase_price: {type: DECIMAL(10,2), desc: Original purchase price in USD, cons: NULL}
  - is_active: {type: BOOLEAN, desc: Whether unicorn is active in fleet, cons: NOT NULL, DEFAULT TRUE}
  - is_available: {type: BOOLEAN, desc: Current availability status (real-time), cons: NOT NULL, DEFAULT TRUE}
  - created_at: {type: TIMESTAMP, desc: Record creation timestamp, cons: NOT NULL, DEFAULT CURRENT_TIMESTAMP}
  - updated_at: {type: TIMESTAMP, desc: Last record update timestamp, cons: NOT NULL, DEFAULT CURRENT_TIMESTAMP}
```

### SQL Examples
```sql
-- Get all unicorns for an account with availability
SELECT unicorn_id, name, friendly_name, breed, color, hourly_rate, is_available
FROM unicorns
WHERE account_id = :account_id AND is_active = TRUE
ORDER BY name;

-- Get current unicorn availability with status details
SELECT * FROM current_unicorn_availability WHERE account_id = :account_id;

-- Search unicorns by name, breed, or color
SELECT unicorn_id, name, breed, color, hourly_rate, is_available
FROM unicorns
WHERE account_id = :account_id AND is_active = TRUE
  AND (LOWER(name) LIKE LOWER(:search) OR LOWER(breed) LIKE LOWER(:search) OR LOWER(color) LIKE LOWER(:search));

-- Get unicorns due for maintenance
SELECT * FROM unicorns_due_for_maintenance 
WHERE account_id = :account_id AND maintenance_urgency IN ('overdue', 'due_this_week')
ORDER BY next_service_due;

-- Get top revenue-generating unicorn breeds
SELECT * FROM top_revenue_unicorn_breeds LIMIT 10;

-- Get unicorn utilization rates
SELECT * FROM unicorn_utilization_rates;

-- Compare unicorn performance
SELECT * FROM unicorn_performance_comparison;
```

## Unicorn Availability Tracking

### Business Context
The unicorn availability tracking system uses a hybrid approach combining an insert-only history table with a real-time status column in the unicorns table. This approach balances the need for detailed historical tracking with performance requirements for real-time availability queries.

The **unicorn_availability** table maintains a complete history of all availability status changes using an insert-only pattern. Each entry represents a point-in-time status change with reasons and expected availability dates. This table is never updated or deleted, ensuring auditability and enabling trend analysis.

The **unicorns** table now includes a direct **is_available** boolean column that reflects the current availability status. This column is automatically synchronized by a database trigger that fires whenever new records are inserted into the unicorn_availability table. The trigger determines the current status based on the most recent availability record for each unicorn.

The **current_unicorn_availability** view combines real-time availability from the unicorns table with additional contextual information from related tables. This view provides a simplified interface for applications to check unicorn availability without complex queries.

This approach provides several benefits:
1. Improved query performance for availability checks
2. Maintained audit trail of all status changes
3. Simplified application logic
4. Automatic synchronization between systems

### Schema
```yaml
table: unicorn_availability
cols:
  - availability_id: {type: UUID, desc: Unique identifier for availability record, cons: PK}
  - unicorn_id: {type: UUID, desc: Reference to unicorn, cons: FK, NOT NULL}
  - account_id: {type: UUID, desc: Reference to account (denormalized for RLS), cons: FK, NOT NULL}
  - status: {type: unicorn_availability_status_enum, desc: Current availability status, cons: NOT NULL}
  - reason: {type: VARCHAR(255), desc: Reason for status change, cons: NULL}
  - expected_available_at: {type: TIMESTAMP, desc: Expected time when unicorn will be available, cons: NULL}
  - updated_by: {type: UUID, desc: Reference to user who made change, cons: FK, NOT NULL}
  - created_at: {type: TIMESTAMP, desc: Record creation timestamp, cons: NOT NULL, DEFAULT CURRENT_TIMESTAMP}
  - updated_at: {type: TIMESTAMP, desc: Last record update timestamp, cons: NOT NULL, DEFAULT CURRENT_TIMESTAMP}
```

## Bookings Table

### Business Context
The bookings table represents confirmed rental agreements between customers and unicorns for specific time periods. With the simplified booking process, all bookings in the system are considered confirmed upon creation, eliminating the need for complex status tracking.

Bookings are created when a customer makes a reservation through direct communication with rental staff (in-person, phone, or other channels). Once entered into the system, a booking is immediately confirmed and cannot be unconfirmed. If changes are needed, staff can modify the booking details directly or mark it as completed when the rental period ends.

This simplified approach reduces complexity in the data model while maintaining essential booking information including customer details, unicorn assignment, time period, pricing, and special requests. The absence of a separate booking status tracking system streamlines operations and reduces data overhead.

### Relationships
- Linked to **customers** table to associate bookings with renters
- Linked to **unicorns** table to associate bookings with rental inventory
- Linked to **users** table to track which staff member created the booking
- Linked to **accounts** table to ensure proper tenant isolation
- Referenced by **transactions** table to associate payments with bookings

### Schema
```yaml
table: bookings
cols:
  - booking_id: {type: UUID, desc: Unique identifier for booking, cons: PK}
  - customer_id: {type: UUID, desc: Reference to customer, cons: FK, NOT NULL}
  - unicorn_id: {type: UUID, desc: Reference to unicorn, cons: FK, NOT NULL}
  - user_id: {type: UUID, desc: Reference to staff member, cons: FK, NOT NULL}
  - account_id: {type: UUID, desc: Reference to rental business, cons: FK, NOT NULL}
  - booking_reference: {type: VARCHAR(50), desc: Unique booking reference number, cons: NOT NULL, UNIQUE}
  - start_datetime: {type: TIMESTAMP, desc: Scheduled start time of rental, cons: NOT NULL}
  - end_datetime: {type: TIMESTAMP, desc: Scheduled end time of rental, cons: NOT NULL}
  - actual_start_datetime: {type: TIMESTAMP, desc: Actual start time of rental, cons: NULL}
  - actual_end_datetime: {type: TIMESTAMP, desc: Actual end time of rental, cons: NULL}
  - base_hourly_rate: {type: DECIMAL(10,2), desc: Base hourly rate in USD, cons: NOT NULL}
  - total_cost: {type: DECIMAL(10,2), desc: Total cost of booking in USD, cons: NOT NULL}
  - special_requests: {type: TEXT, desc: Customer special requests, cons: NULL}
  - pickup_location: {type: VARCHAR(255), desc: Pickup location details, cons: NULL}
  - dropoff_location: {type: VARCHAR(255), desc: Dropoff location details, cons: NULL}
  - cancellation_reason: {type: TEXT, desc: Reason for cancellation, cons: NULL}
  - is_recurring: {type: BOOLEAN, desc: Whether booking is recurring, cons: NOT NULL, DEFAULT FALSE}
  - recurrence_pattern: {type: VARCHAR(100), desc: Pattern for recurring bookings, cons: NULL}
  - return_inspection_notes: {type: TEXT, desc: Notes from return inspection, cons: NULL}
  - late_return_hours: {type: DECIMAL(5,2), desc: Hours unicorn was returned late, cons: NULL}
  - damage_assessment: {type: TEXT, desc: Description of any damage, cons: NULL}
  - damage_cost_estimate: {type: DECIMAL(10,2), desc: Estimated cost of damage repairs, cons: NULL}
  - is_completed: {type: BOOLEAN, desc: Whether booking is completed, cons: NOT NULL, DEFAULT FALSE}
  - created_at: {type: TIMESTAMP, desc: Record creation timestamp, cons: NOT NULL, DEFAULT CURRENT_TIMESTAMP}
  - updated_at: {type: TIMESTAMP, desc: Last record update timestamp, cons: NOT NULL, DEFAULT CURRENT_TIMESTAMP}
```

### SQL Examples
```sql
-- Get today's bookings
SELECT * FROM daily_bookings_summary 
WHERE DATE(start_datetime) = CURRENT_DATE
ORDER BY start_datetime;

-- Get bookings for calendar view (date range)
SELECT * FROM calendar_bookings 
WHERE start_date BETWEEN :start_date AND :end_date
ORDER BY start_date, start_time;

-- Get upcoming bookings for a customer
SELECT b.booking_id, b.booking_reference, u.name AS unicorn_name, 
       b.start_datetime, b.end_datetime, b.total_cost
FROM bookings b
JOIN unicorns u ON b.unicorn_id = u.unicorn_id
WHERE b.customer_id = :customer_id AND b.start_datetime > NOW()
ORDER BY b.start_datetime;

-- Get booking history for a unicorn
SELECT b.booking_id, b.booking_reference, 
       CONCAT(c.first_name, ' ', c.last_name) AS customer_name,
       b.start_datetime, b.end_datetime, b.total_cost, b.is_completed
FROM bookings b
JOIN customers c ON b.customer_id = c.customer_id
WHERE b.unicorn_id = :unicorn_id
ORDER BY b.start_datetime DESC
LIMIT 20;

-- Get peak booking periods
SELECT * FROM peak_periods ORDER BY total_bookings DESC LIMIT 10;

-- Get bookings created by a specific staff member
SELECT b.booking_id, b.booking_reference, 
       CONCAT(c.first_name, ' ', c.last_name) AS customer_name,
       u.name AS unicorn_name, b.total_cost, b.created_at
FROM bookings b
JOIN customers c ON b.customer_id = c.customer_id
JOIN unicorns u ON b.unicorn_id = u.unicorn_id
WHERE b.user_id = :user_id
ORDER BY b.created_at DESC
LIMIT 20;

-- Average booking duration by unicorn breed (common custom SQL query)
SELECT u.breed, 
       COUNT(b.booking_id) AS total_bookings,
       ROUND(AVG(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0), 2) AS avg_duration_hours,
       ROUND(MIN(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0), 2) AS min_duration_hours,
       ROUND(MAX(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0), 2) AS max_duration_hours
FROM bookings b
JOIN unicorns u ON b.unicorn_id = u.unicorn_id
WHERE u.breed IS NOT NULL
GROUP BY u.breed
ORDER BY avg_duration_hours DESC;

-- A "full-day booking" is defined as 8 hours (start_datetime to end_datetime = 8 hours)
-- Booking duration in hours = EXTRACT(EPOCH FROM (end_datetime - start_datetime)) / 3600.0

-- Total revenue by customer for current month
SELECT CONCAT(c.first_name, ' ', c.last_name) AS customer_name,
       COUNT(b.booking_id) AS bookings_this_month,
       SUM(b.total_cost) AS total_spent
FROM bookings b
JOIN customers c ON b.customer_id = c.customer_id
WHERE DATE_TRUNC('month', b.start_datetime) = DATE_TRUNC('month', CURRENT_DATE)
GROUP BY c.customer_id, c.first_name, c.last_name
ORDER BY total_spent DESC;

-- Bookings with late returns
SELECT b.booking_reference, CONCAT(c.first_name, ' ', c.last_name) AS customer_name,
       u.name AS unicorn_name, b.late_return_hours, b.damage_assessment
FROM bookings b
JOIN customers c ON b.customer_id = c.customer_id
JOIN unicorns u ON b.unicorn_id = u.unicorn_id
WHERE b.late_return_hours > 0
ORDER BY b.late_return_hours DESC;

-- Cancellation rate
SELECT COUNT(*) AS total_bookings,
       SUM(CASE WHEN cancellation_reason IS NOT NULL THEN 1 ELSE 0 END) AS cancelled,
       ROUND(SUM(CASE WHEN cancellation_reason IS NOT NULL THEN 1 ELSE 0 END)::DECIMAL / COUNT(*) * 100, 2) AS cancellation_rate_pct
FROM bookings;
```

## Transactions Table

### Schema
```yaml
table: transactions
cols:
  - transaction_id: {type: UUID, desc: Unique identifier for transaction, cons: PK}
  - customer_id: {type: UUID, desc: Reference to customer, cons: FK, NOT NULL}
  - account_id: {type: UUID, desc: Reference to rental business, cons: FK, NOT NULL}
  - booking_id: {type: UUID, desc: Reference to booking (optional), cons: FK, NULL}
  - parent_transaction_id: {type: UUID, desc: Reference to parent transaction (for refunds), cons: FK, NULL}
  - transaction_type: {type: transaction_type_enum, desc: Type of transaction, cons: NOT NULL}
  - amount: {type: DECIMAL(10,2), desc: Transaction amount in specified currency, cons: NOT NULL}
  - currency: {type: VARCHAR(3), desc: Currency code (ISO 4217), cons: NOT NULL, DEFAULT 'USD'}
  - status: {type: transaction_status_enum, desc: Current status of transaction, cons: NOT NULL}
  - payment_method: {type: payment_method_enum, desc: Payment method used, cons: NULL}
  - payment_reference: {type: VARCHAR(255), desc: External payment reference, cons: NULL}
  - tax_amount: {type: DECIMAL(10,2), desc: Tax amount included in transaction, cons: NOT NULL, DEFAULT 0}
  - tax_rate: {type: DECIMAL(5,4), desc: Tax rate applied to transaction, cons: NOT NULL, DEFAULT 0}
  - description: {type: TEXT, desc: Description of transaction, cons: NOT NULL}
  - processed_at: {type: TIMESTAMP, desc: When transaction was processed, cons: NULL}
  - refunded_at: {type: TIMESTAMP, desc: When transaction was refunded, cons: NULL}
  - created_at: {type: TIMESTAMP, desc: Record creation timestamp, cons: NOT NULL, DEFAULT CURRENT_TIMESTAMP}
  - updated_at: {type: TIMESTAMP, desc: Last record update timestamp, cons: NOT NULL, DEFAULT CURRENT_TIMESTAMP}
```

### SQL Examples
```sql
-- Get monthly revenue summary
SELECT * FROM monthly_revenue_summary ORDER BY year_month DESC LIMIT 12;

-- Get revenue by day of week and hour (peak times)
SELECT * FROM revenue_by_time_and_day ORDER BY total_revenue DESC;

-- Get seasonal trends
SELECT * FROM seasonal_trends WHERE account_id = :account_id ORDER BY year DESC, month_number;

-- Get transactions for a booking
SELECT transaction_id, transaction_type, amount, status, payment_method, created_at
FROM transactions
WHERE booking_id = :booking_id
ORDER BY created_at;

-- Get recent transactions for an account
SELECT t.transaction_id, t.transaction_type, t.amount, t.status,
       CONCAT(c.first_name, ' ', c.last_name) AS customer_name, t.created_at
FROM transactions t
JOIN customers c ON t.customer_id = c.customer_id
WHERE t.account_id = :account_id
ORDER BY t.created_at DESC
LIMIT 50;

-- Revenue breakdown by transaction type
SELECT transaction_type, 
       COUNT(*) AS count,
       SUM(amount) AS total_amount,
       ROUND(AVG(amount), 2) AS avg_amount
FROM transactions
WHERE status = 'completed'
GROUP BY transaction_type
ORDER BY total_amount DESC;

-- Daily revenue trend for last 30 days
SELECT DATE(created_at) AS date,
       SUM(CASE WHEN transaction_type IN ('booking_fee', 'subscription') THEN amount ELSE 0 END) AS revenue,
       SUM(CASE WHEN transaction_type = 'refund' THEN amount ELSE 0 END) AS refunds
FROM transactions
WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY DATE(created_at)
ORDER BY date;
```

## Database Views

### Business Context
The Timely-Unicorn database includes several pre-defined views that provide aggregated and simplified access to common business queries. These views combine data from multiple tables to deliver meaningful business insights without requiring complex joins in application code.

Views are particularly important in a multi-tenant environment as they ensure proper data isolation while providing consistent interfaces for reporting and analytics. They also help optimize performance by pre-computing frequently accessed data combinations.

The views cover key business areas including booking summaries, financial reporting, asset utilization, customer retention, staff performance, and demand forecasting. Each view is designed to support specific operational or analytical use cases identified in the user stories.

### Key Views

#### Operational Views
1. **daily_bookings_summary** - Consolidated view of bookings with customer, unicorn, and status information
2. **calendar_bookings** - Booking data formatted for calendar visualization
3. **current_unicorn_availability** - Real-time availability status of all active unicorns
4. **unicorns_due_for_maintenance** - Unicorns requiring maintenance with urgency levels

#### Financial Views
5. **monthly_revenue_summary** - Monthly aggregated financial data for revenue tracking
6. **revenue_comparison** - Daily revenue with day-over-day and week-over-week comparisons
7. **top_revenue_unicorns_by_period** - Highest revenue-generating unicorns by time period
8. **top_revenue_unicorn_breeds** - Revenue aggregated by unicorn breed
9. **bottom_revenue_unicorns** - Lowest performing unicorns
10. **bottom_revenue_breeds** - Lowest performing breeds
11. **top_revenue_customers** - Customers ranked by total revenue contribution
12. **revenue_by_time_and_day** - Revenue patterns by day of week and hour

#### Analytics Views
13. **unicorn_utilization_rates** - Usage statistics for each unicorn
14. **unicorn_performance_comparison** - Comprehensive unicorn metrics with performance categorization
15. **customer_retention_metrics** - Customer behavior analysis for retention opportunities
16. **customer_lifetime_value** - Customer value metrics over their relationship
17. **customer_segmentation_by_revenue** - Customers grouped into value tiers (VIP, Premium, Standard, Basic)
18. **staff_performance_metrics** - Staff productivity and revenue generation
19. **peak_periods** - High-demand time periods for resource planning
20. **seasonal_trends** - Monthly booking and revenue patterns
21. **subscription_tracker_summary** - Hourly subscription status tracking for revenue analysis

#### Administrative Views
22. **account_subscription_status** - Subscription plan usage and limits for each account

## Subscription Tracker Table

### Business Context
The subscription_tracker table tracks the hourly subscription status for all unicorn rental businesses (accounts) in the Timely-Unicorn platform. This table provides a time-series view of which subscription plan each account was on at any given hour, along with the associated pricing information.

This table is crucial for accurate revenue tracking and reporting, as it captures the subscription status at each point in time rather than just the current status. Since accounts can change their subscription plans during their usage of the platform, this table ensures that revenue calculations reflect the actual plan the account was on during each hour.

Each row represents a combination of an account and an hour, storing the plan ID, monthly price, and calculated hourly price for that specific hour. This allows the SaaS startup to accurately calculate revenue based on the actual subscription status during each time period.

The table covers the period from January 1, 2025, to December 31, 2025, with 24/7 coverage for all accounts. For each hour, there will be one row per account in the system.

### Relationships
- Linked to **accounts** table to associate tracking records with rental businesses
- Linked to **subscription_plans** table to store plan information at the time of tracking

### Schema
```yaml
table: subscription_tracker
cols:
  - tracker_id: {type: UUID, desc: Unique identifier for tracking record, cons: PK}
  - account_id: {type: UUID, desc: Reference to rental business account, cons: FK, NOT NULL}
  - datetime: {type: TIMESTAMP, desc: The hour being tracked, cons: NOT NULL}
  - plan_id: {type: UUID, desc: Reference to subscription plan at this time, cons: FK, NOT NULL}
  - monthly_price: {type: DECIMAL(10,2), desc: Monthly price of plan at this time, cons: NOT NULL}
  - hourly_price: {type: DECIMAL(10,6), desc: Hourly prorated price (monthly_price / hours_in_month), cons: NOT NULL}
  - created_at: {type: TIMESTAMP, desc: Record creation timestamp, cons: NOT NULL, DEFAULT CURRENT_TIMESTAMP}
  - updated_at: {type: TIMESTAMP, desc: Last record update timestamp, cons: NOT NULL, DEFAULT CURRENT_TIMESTAMP}
```

### SQL Examples
```sql
-- Get hourly subscription revenue for a date range
SELECT DATE(datetime) AS date, SUM(hourly_price) AS daily_revenue
FROM subscription_tracker
WHERE datetime BETWEEN :start_date AND :end_date
GROUP BY DATE(datetime)
ORDER BY date;

-- Get subscription status history for an account
SELECT datetime, sp.plan_name, monthly_price, hourly_price
FROM subscription_tracker st
JOIN subscription_plans sp ON st.plan_id = sp.plan_id
WHERE st.account_id = :account_id
ORDER BY datetime DESC
LIMIT 100;
```


## Business Rules and Clarifications

### Accounts and Subscriptions
- When a rental business exceeds the `user_limit` of their subscription plan, they must upgrade to a higher plan before adding more users. The platform blocks new user creation when the limit is reached.
- The Custom plan has `user_limit = 0` and `storage_limit_gb = 0`, which means these limits are negotiated separately and not enforced by the platform.
- A terminated account cannot be reactivated. The rental business must create a new account. Suspended accounts can be reactivated by resolving the suspension reason (e.g., payment failure).
- `current_user_count` includes only active users (`is_active = TRUE`). Deactivated users do not count toward the plan limit.

### Customers
- A customer belongs to exactly one account (rental business). The same person renting from two different businesses would be two separate customer records.

### Unicorns
- `seat_capacity` includes the rider/driver. A unicorn with `seat_capacity = 4` can carry 1 rider + 3 passengers.
- `is_active` means the unicorn is part of the fleet (not retired/decommissioned). `is_available` means the unicorn is currently ready for booking (not in maintenance, repair, or reserved). An active unicorn can be unavailable (e.g., during maintenance). An inactive unicorn is permanently removed from the fleet.
- `max_speed_kmh` is ground speed. Flight-capable unicorns have their flight speed noted in `magic_abilities`.
- There are no discounts, surcharges, or peak pricing in the current system. `total_cost = base_hourly_rate × duration_hours` where `duration_hours = EXTRACT(EPOCH FROM (end_datetime - start_datetime)) / 3600.0`.

### Unicorn Availability Statuses
- `available`: Ready for booking
- `maintenance`: Scheduled routine maintenance (e.g., horn polishing, hoof trimming)
- `repair`: Unscheduled repair due to damage or malfunction
- `cleaning`: Post-rental cleaning and grooming
- `reserved`: Held for a specific upcoming booking
- `out_of_service`: Temporarily removed from fleet for extended period

### Transactions
- The `amount` field is always positive. For refunds, `transaction_type = 'refund'` indicates the direction. To calculate net revenue: `SUM(amount WHERE type IN ('booking_fee', 'subscription')) - SUM(amount WHERE type = 'refund')`.
- `tax_amount` is included within the `amount` field (not additional). The `tax_rate` field records the rate applied.

### Customer Segmentation Thresholds
- VIP: total_revenue >= $5,000
- Premium: total_revenue >= $2,000
- Standard: total_revenue >= $500
- Basic: total_revenue < $500


### Address and Location Fields
- All address fields (billing_address, headquarters_address, customer address) use free-text format. Country fields are NOT standardized ISO codes — they contain full country names (e.g., "Elven Dominion", "Celestial Realm"). City and state/province are also free-text.
- `pickup_location` and `dropoff_location` in bookings are free-text descriptions of where the unicorn is collected and returned.

### Customer Fields
- `customer_type` is either 'individual' (a person) or 'organization' (a company/group). For organizations, `organization_name` is the company name while `first_name`/`last_name` is the primary contact person.
- `department` and `title` describe the contact person's role within their organization (e.g., "Magical Transportation", "Fleet Operations Manager").
- `email` is the primary contact email. `phone_number` uses free-text format (no standardization).
- `billing_preference` controls how invoices are delivered: 'email' (digital), 'mail' (physical), or 'both'.

### User Roles and Authentication
- `role` determines system permissions: `saas_admin` (platform-wide access), `rental_admin` (full access to own account), `staff` (day-to-day operations for own account), `analyst` (read-only analytics for own account).
- `username` follows the format `firstname.lastname` (e.g., "lyra.starwhisper").
- Account lockout: after 5 `failed_login_attempts`, the account is locked until `locked_until` timestamp (typically 30 minutes).

### Unicorn Details
- `unicorn_uid` follows the format `{ACCOUNT_PREFIX}-{NUMBER}` (e.g., "MYTHICAL-005", "MYTHIC-018").
- `name` is the formal registered name (e.g., "Sheratan Autunite"). `friendly_name` is the nickname used by staff (e.g., "Aurora", "Starry").
- `year_of_making` is the year the unicorn was born/created.
- `horn_material` examples: "Starlight Crystal", "Moonstone Crystal", "Rainbow Quartz", "Aetherium Crystal". This affects the unicorn's magical properties.
- `magic_abilities` is free-text describing special powers (e.g., "Flight, Weather Control, Healing Aura" or "Healing aura, Teleportation to enchanted locations").
- `fuel_type` describes what the unicorn consumes for energy (e.g., "Astral Energy", "Ethereal Essence", "Lunar Essence", "Moonbeam Essence"). `fuel_capacity` is measured in liters of magical essence (typical range: 80-150).
- `hourly_rate` is the base rental rate per hour in USD. This is the rate at the time of the unicorn's current configuration — it may change over time. Bookings lock in the rate at `base_hourly_rate`.
- `purchase_price` is the original acquisition cost of the unicorn.
- Service schedule: `last_service_date` is when the unicorn was last serviced. `next_service_due` is the scheduled next service. Service includes horn polishing, hoof trimming, magical calibration, and health check.

### Booking Details
- `booking_reference` format: "BK{6DIGITS}" (e.g., "BK860916", "BK117878").
- `start_datetime`/`end_datetime` are the scheduled times. `actual_start_datetime`/`actual_end_datetime` are populated when the unicorn is actually picked up and returned. These may differ from scheduled times.
- `base_hourly_rate` is the rate locked at booking creation time (copied from the unicorn's `hourly_rate` at that moment).
- `is_completed` is set to TRUE when the unicorn is returned and the return inspection is done.
- `late_return_hours` = `actual_end_datetime - end_datetime` in hours (only positive values, 0 if returned on time or early).
- `damage_assessment` is free-text notes from the return inspection. `damage_cost_estimate` is the estimated repair cost, which may be charged to the customer separately.
- `cancellation_reason` is populated when a booking is cancelled. There is no separate "cancelled" status — a cancelled booking has `cancellation_reason IS NOT NULL` and `is_completed = FALSE`.
- `is_recurring` and `recurrence_pattern` are for future use. Currently no recurring bookings exist in the data.

### Transaction Details
- `currency` is always 'USD' in the current dataset.
- `payment_method` options: 'credit_card', 'bank_transfer', 'paypal', 'invoice'.
- `payment_reference` is the external payment gateway reference (e.g., Stripe charge ID).
- `processed_at` is when the payment was actually processed (may differ from `created_at` for pending transactions).
- `refunded_at` is populated only for refund transactions.
- `parent_transaction_id` links a refund to its original transaction.

### Subscription Tracker
- The `subscription_tracker` table records hourly snapshots of each account's subscription status. `datetime` is the snapshot timestamp.
- `hourly_price` is calculated as `monthly_price / hours_in_month` (approximately monthly_price / 720).
- This table is used for precise revenue attribution and billing reconciliation.


### Current Dataset Characteristics
- The dataset contains 2 rental businesses: Mythical Unicorns (account 0330c2ef) and Mythic Unicorns (account d667a552).
- There are 100 unicorns (split between the two accounts), ~500 customers, ~13,900 bookings, and ~13,900 transactions.
- Subscription plans: Free ($0/mo, 3 users), Starter ($49.99/mo, 5 users), Small Business ($99.99/mo, 15 users), Enterprise ($299.99/mo, 100 users), Custom (negotiated).
- Mythical Unicorns is on the Starter plan; Mythic Unicorns is on the Enterprise plan.
- All bookings in the current dataset are completed (`is_completed = TRUE`). There are no cancelled bookings, no late returns, and no damage records in the sample data.
- All transactions are of type `booking_fee`. There are no subscription, refund, or adjustment transactions in the sample data.
- The `base_hourly_rate` in bookings matches the unicorn's `hourly_rate` at booking time ($295-$400/hr). `total_cost = base_hourly_rate × duration_hours`.
- Unicorn breeds are Celestial variants (e.g., "Celestial Nebula", "Celestial Pegasus").
- Seat capacity is either 2 or 3 in the current fleet.
- Unicorn years of making: 2021 or 2022.
- All account address fields (billing, headquarters) are empty in the sample data.


### Field-Level Data Details (Verified Against Actual Dataset)

**Accounts:**
- Both accounts show `current_storage_usage_gb = 12.45`. All accounts use `billing_cycle = 'monthly'`.

**Customers:**
- `phone_number` values are simplified 6-digit numbers (e.g., "123456", "789012") — not real phone formats.
- Customer cities are fantasy names: "Arcane Valley", "Arcanevale", "Celestial Haven", "Eldergrove", "Starfall Haven".
- `organization_name` follows the pattern "Example {Fantasy Theme}" (e.g., "Example Celestial Logistics", "Example Enchanted Creatures", "Example Mythical Transport Solutions").

**Unicorns:**
- `name` combines star names with gemstone/mineral names (e.g., "Achernar Jade", "Aldebaran Topaz", "Spica Pearl", "Rigel Azure").
- `friendly_name` is a short nickname used by staff (e.g., "Aurora", "Starry", "Rory").
- `color` describes the unicorn's coat (e.g., "Iridescent White", "Stardust Silver", "Starry Blue").
- `fuel_capacity` ranges from ~75 to ~150 liters of magical essence.

**Bookings:**
- `total_cost` = `base_hourly_rate` × duration in hours. Example: a 3-hour booking of a $375/hr unicorn costs $1,125. Range: ~$295 (1hr cheapest) to ~$9,600 (24hr most expensive).
- `actual_end_datetime` IS populated in the data (records when the unicorn was actually returned). `actual_start_datetime` is empty (not tracked in current data).

**Transactions:**
- `description` follows the pattern: "Booking fee for unicorn rental {booking_reference}" (e.g., "Booking fee for unicorn rental BK860916").
