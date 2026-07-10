-- Timely-Unicorn Rental Management System - Database Schema (PostgreSQL Compatible with Native ENUM Types)
-- This SQL script creates tables and views for the unicorn rental application with a simplified structure

-- Define ENUM types
CREATE TYPE customer_type_enum AS ENUM ('individual', 'organization');
CREATE TYPE billing_preference_enum AS ENUM ('email', 'mail', 'both');
CREATE TYPE billing_cycle_enum AS ENUM ('monthly', 'quarterly', 'annual');
CREATE TYPE account_status_enum AS ENUM ('active', 'suspended', 'terminated');
CREATE TYPE user_role_enum AS ENUM ('saas_admin', 'rental_admin', 'staff', 'analyst');
CREATE TYPE unicorn_availability_status_enum AS ENUM ('available', 'maintenance', 'repair', 'cleaning', 'reserved', 'out_of_service');
CREATE TYPE transaction_type_enum AS ENUM ('booking_fee', 'subscription', 'storage_overage', 'refund', 'adjustment');
CREATE TYPE transaction_status_enum AS ENUM ('pending', 'processing', 'completed', 'failed', 'refunded');
CREATE TYPE payment_method_enum AS ENUM ('credit_card', 'bank_transfer', 'paypal', 'invoice');

-- 1. Subscription Plans Table
CREATE TABLE subscription_plans (
    plan_id UUID PRIMARY KEY,
    plan_name VARCHAR(50) NOT NULL UNIQUE,
    user_limit INTEGER NOT NULL,
    storage_limit_gb DECIMAL(10,2) NOT NULL,
    monthly_price DECIMAL(10,2) NOT NULL,
    is_custom BOOLEAN NOT NULL DEFAULT FALSE,
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 2. Accounts Table (Unicorn Rental Businesses)
CREATE TABLE accounts (
    account_id UUID PRIMARY KEY,
    plan_id UUID NOT NULL,
    account_name VARCHAR(255) NOT NULL,
    status account_status_enum NOT NULL,
    current_storage_usage_gb DECIMAL(10,2) NOT NULL DEFAULT 0,
    current_user_count INTEGER NOT NULL DEFAULT 0,
    billing_email VARCHAR(255) NOT NULL,
    billing_address_line1 VARCHAR(255),
    billing_address_line2 VARCHAR(255),
    billing_city VARCHAR(100),
    billing_state_province VARCHAR(100),
    billing_postal_code VARCHAR(20),
    billing_country VARCHAR(100),
    next_billing_date DATE,
    trial_end_date DATE,
    activated_at TIMESTAMP,
    suspended_at TIMESTAMP,
    terminated_at TIMESTAMP,
    industry VARCHAR(100),
    employee_count INTEGER,
    website VARCHAR(255),
    billing_cycle billing_cycle_enum DEFAULT 'monthly',
    headquarters_address_line1 VARCHAR(255),
    headquarters_address_line2 VARCHAR(255),
    headquarters_city VARCHAR(100),
    headquarters_state_province VARCHAR(100),
    headquarters_postal_code VARCHAR(20),
    headquarters_country VARCHAR(100),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (plan_id) REFERENCES subscription_plans(plan_id)
);

-- Indexes for accounts table
CREATE INDEX idx_accounts_plan_id ON accounts(plan_id);
CREATE INDEX idx_accounts_status ON accounts(status);

-- 3. Customers Table (Renters)
CREATE TABLE customers (
    customer_id UUID PRIMARY KEY,
    account_id UUID NOT NULL,
    customer_type customer_type_enum NOT NULL,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100),
    organization_name VARCHAR(255),
    email VARCHAR(255) NOT NULL,
    phone_number VARCHAR(20),
    address_line1 VARCHAR(255),
    address_line2 VARCHAR(255),
    city VARCHAR(100),
    state_province VARCHAR(100),
    postal_code VARCHAR(20),
    country VARCHAR(100),
    billing_preference billing_preference_enum DEFAULT 'email',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    department VARCHAR(100),
    title VARCHAR(100),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

-- Indexes for customers table
CREATE INDEX idx_customers_account_id ON customers(account_id);
CREATE INDEX idx_customers_customer_type ON customers(customer_type);

-- 4. Users Table
CREATE TABLE users (
    user_id UUID PRIMARY KEY,
    account_id UUID NOT NULL,
    username VARCHAR(50) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    role user_role_enum NOT NULL,
    phone_number VARCHAR(20),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_login_at TIMESTAMP,
    failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

-- Indexes for users table
CREATE INDEX idx_users_account_id ON users(account_id);
CREATE INDEX idx_users_role ON users(role);
CREATE INDEX idx_users_is_active ON users(is_active);

-- 5. Unicorns Table
CREATE TABLE unicorns (
    unicorn_id UUID PRIMARY KEY,
    account_id UUID NOT NULL,
    unicorn_uid VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    friendly_name VARCHAR(100),
    year_of_making INTEGER NOT NULL,
    breed VARCHAR(100),
    color VARCHAR(50),
    horn_length_cm DECIMAL(5,2),
    horn_material VARCHAR(50),
    seat_capacity INTEGER NOT NULL,
    magic_abilities TEXT,
    max_speed_kmh DECIMAL(5,2),
    fuel_type VARCHAR(50),
    fuel_capacity DECIMAL(10,2),
    hourly_rate DECIMAL(10,2) NOT NULL DEFAULT 0,
    last_service_date DATE,
    next_service_due DATE,
    purchase_date DATE,
    purchase_price DECIMAL(10,2),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_available BOOLEAN NOT NULL DEFAULT TRUE,
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

-- Indexes for unicorns table
CREATE INDEX idx_unicorns_account_id ON unicorns(account_id);
CREATE INDEX idx_unicorns_is_available ON unicorns(is_available);
CREATE INDEX idx_unicorns_account_available ON unicorns(account_id, is_available);
CREATE INDEX idx_unicorns_breed ON unicorns(breed);

-- 6. Unicorn Availability Table
CREATE TABLE unicorn_availability (
    availability_id UUID PRIMARY KEY,
    unicorn_id UUID NOT NULL,
    account_id UUID NOT NULL,
    status unicorn_availability_status_enum NOT NULL,
    reason VARCHAR(255),
    expected_available_at TIMESTAMP,
    updated_by UUID NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (unicorn_id) REFERENCES unicorns(unicorn_id),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id),
    FOREIGN KEY (updated_by) REFERENCES users(user_id)
);

-- Indexes for unicorn_availability table
CREATE INDEX idx_unicorn_availability_unicorn_id ON unicorn_availability(unicorn_id);
CREATE INDEX idx_unicorn_availability_account_id ON unicorn_availability(account_id);
CREATE INDEX idx_unicorn_availability_created_at ON unicorn_availability(created_at);
CREATE INDEX idx_unicorn_availability_status ON unicorn_availability(status);
CREATE INDEX idx_unicorn_availability_unicorn_created ON unicorn_availability(unicorn_id, created_at DESC);

-- Trigger function to update unicorn's is_available status based on latest availability record
CREATE OR REPLACE FUNCTION update_unicorn_availability()
RETURNS TRIGGER AS $$
BEGIN
    -- Update the is_available column in unicorns table based on the latest availability record
    UPDATE unicorns 
    SET is_available = (
        SELECT CASE 
            WHEN status = 'available' THEN TRUE 
            ELSE FALSE 
        END
        FROM unicorn_availability 
        WHERE unicorn_id = NEW.unicorn_id 
        ORDER BY created_at DESC 
        LIMIT 1
    )
    WHERE unicorn_id = NEW.unicorn_id;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to automatically update unicorn availability when new records are inserted
CREATE TRIGGER unicorn_availability_trigger
    AFTER INSERT ON unicorn_availability
    FOR EACH ROW
    EXECUTE FUNCTION update_unicorn_availability();

-- 7. Bookings Table
CREATE TABLE bookings (
    booking_id UUID PRIMARY KEY,
    customer_id UUID NOT NULL,
    unicorn_id UUID NOT NULL,
    user_id UUID NOT NULL,
    account_id UUID NOT NULL,
    booking_reference VARCHAR(50) NOT NULL UNIQUE,
    start_datetime TIMESTAMP NOT NULL,
    end_datetime TIMESTAMP NOT NULL,
    actual_start_datetime TIMESTAMP,
    actual_end_datetime TIMESTAMP,
    base_hourly_rate DECIMAL(10,2) NOT NULL,
    total_cost DECIMAL(10,2) NOT NULL,
    special_requests TEXT,
    pickup_location VARCHAR(255),
    dropoff_location VARCHAR(255),
    cancellation_reason TEXT,
    is_recurring BOOLEAN NOT NULL DEFAULT FALSE,
    recurrence_pattern VARCHAR(100),
    return_inspection_notes TEXT,
    late_return_hours DECIMAL(5,2),
    damage_assessment TEXT,
    damage_cost_estimate DECIMAL(10,2),
    is_completed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
    FOREIGN KEY (unicorn_id) REFERENCES unicorns(unicorn_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

-- Indexes for bookings table
CREATE INDEX idx_bookings_account_id ON bookings(account_id);
CREATE INDEX idx_bookings_customer_id ON bookings(customer_id);
CREATE INDEX idx_bookings_unicorn_id ON bookings(unicorn_id);
CREATE INDEX idx_bookings_start_datetime ON bookings(start_datetime);
CREATE INDEX idx_bookings_account_datetime ON bookings(account_id, start_datetime);
CREATE INDEX idx_bookings_is_completed ON bookings(is_completed);
CREATE INDEX idx_bookings_user_id ON bookings(user_id);

-- 9. Booking Status Table
-- REMOVED: Simplified booking process - all bookings are confirmed upon creation

-- 10. Transactions Table
CREATE TABLE transactions (
    transaction_id UUID PRIMARY KEY,
    customer_id UUID NOT NULL,
    account_id UUID NOT NULL,
    booking_id UUID,
    parent_transaction_id UUID,
    transaction_type transaction_type_enum NOT NULL,
    amount DECIMAL(10,2) NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    status transaction_status_enum NOT NULL,
    payment_method payment_method_enum,
    payment_reference VARCHAR(255),
    tax_amount DECIMAL(10,2) NOT NULL DEFAULT 0,
    tax_rate DECIMAL(5,4) NOT NULL DEFAULT 0,
    description TEXT NOT NULL,
    processed_at TIMESTAMP,
    refunded_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id),
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id),
    FOREIGN KEY (parent_transaction_id) REFERENCES transactions(transaction_id)
);

-- Indexes for transactions table
CREATE INDEX idx_transactions_account_id ON transactions(account_id);
CREATE INDEX idx_transactions_customer_id ON transactions(customer_id);
CREATE INDEX idx_transactions_booking_id ON transactions(booking_id);
CREATE INDEX idx_transactions_created_at ON transactions(created_at);
CREATE INDEX idx_transactions_account_created_at ON transactions(account_id, created_at);
CREATE INDEX idx_transactions_type ON transactions(transaction_type);
CREATE INDEX idx_transactions_status ON transactions(status);

-- 11. Subscription Tracker Table
CREATE TABLE subscription_tracker (
    tracker_id UUID PRIMARY KEY,
    account_id UUID NOT NULL,
    datetime TIMESTAMP NOT NULL,
    plan_id UUID NOT NULL,
    monthly_price DECIMAL(10,2) NOT NULL,
    hourly_price DECIMAL(10,6) NOT NULL, -- Calculated as monthly_price / hours_in_month
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES accounts(account_id),
    FOREIGN KEY (plan_id) REFERENCES subscription_plans(plan_id)
);

-- Indexes for subscription_tracker table
CREATE INDEX idx_subscription_tracker_account_id ON subscription_tracker(account_id);
CREATE INDEX idx_subscription_tracker_datetime ON subscription_tracker(datetime);
CREATE INDEX idx_subscription_tracker_account_datetime ON subscription_tracker(account_id, datetime);
CREATE INDEX idx_subscription_tracker_plan_id ON subscription_tracker(plan_id);

-- VIEWS

-- 1. Daily Bookings Summary View
CREATE VIEW daily_bookings_summary WITH (security_invoker = true) AS
SELECT 
    b.booking_id,
    b.booking_reference,
    CONCAT(c.first_name, ' ', c.last_name) AS customer_name,
    u.name AS unicorn_name,
    b.start_datetime,
    b.end_datetime,
    ROUND(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0, 2) AS duration_hours,
    b.special_requests,
    CONCAT(us.first_name, ' ', us.last_name) AS staff_member,
    CASE 
        WHEN b.is_completed THEN 'completed'
        ELSE 'confirmed'
    END AS status,
    b.pickup_location,
    b.created_at
FROM bookings b
JOIN customers c ON b.customer_id = c.customer_id
JOIN unicorns u ON b.unicorn_id = u.unicorn_id
JOIN users us ON b.user_id = us.user_id;

-- 2. Monthly Revenue Summary View
CREATE VIEW monthly_revenue_summary WITH (security_invoker = true) AS
SELECT 
    TO_CHAR(t.created_at, 'YYYY-MM') AS year_month,
    SUM(CASE WHEN t.transaction_type IN ('booking_fee', 'subscription', 'storage_overage') THEN t.amount ELSE 0 END) AS total_revenue,
    SUM(CASE WHEN t.transaction_type = 'booking_fee' THEN t.amount ELSE 0 END) AS booking_fees,
    SUM(CASE WHEN t.transaction_type = 'subscription' THEN t.amount ELSE 0 END) AS subscription_fees,
    SUM(CASE WHEN t.transaction_type = 'storage_overage' THEN t.amount ELSE 0 END) AS storage_overages,
    COUNT(*) AS total_transactions,
    COUNT(DISTINCT t.customer_id) AS unique_customers,
    COUNT(DISTINCT b.unicorn_id) AS active_unicorns,
    AVG(CASE WHEN t.transaction_type IN ('booking_fee', 'subscription', 'storage_overage') THEN t.amount ELSE NULL END) AS avg_booking_value,
    SUM(CASE WHEN t.transaction_type = 'refund' THEN t.amount ELSE 0 END) AS refund_amount,
    SUM(CASE WHEN t.transaction_type IN ('booking_fee', 'subscription', 'storage_overage') THEN t.amount ELSE 0 END) - SUM(CASE WHEN t.transaction_type = 'refund' THEN t.amount ELSE 0 END) AS net_revenue
FROM transactions t
LEFT JOIN bookings b ON t.booking_id = b.booking_id
GROUP BY TO_CHAR(t.created_at, 'YYYY-MM');

-- 3. Unicorn Utilization Rates View
CREATE VIEW unicorn_utilization_rates WITH (security_invoker = true) AS
SELECT 
    u.unicorn_id,
    u.name AS unicorn_name,
    CURRENT_DATE - INTERVAL '30 days' AS period_start,
    CURRENT_DATE AS period_end,
    720 AS total_hours_in_period, -- 30 days * 24 hours
    COALESCE(SUM(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0), 0) AS booked_hours,
    COALESCE(SUM(CASE WHEN ua.status IN ('maintenance', 'repair', 'cleaning') THEN EXTRACT(EPOCH FROM (COALESCE(ua.updated_at, NOW()) - ua.created_at))/3600.0 ELSE 0 END), 0) AS maintenance_hours,
    720 - COALESCE(SUM(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0), 0) - COALESCE(SUM(CASE WHEN ua.status IN ('maintenance', 'repair', 'cleaning') THEN EXTRACT(EPOCH FROM (COALESCE(ua.updated_at, NOW()) - ua.created_at))/3600.0 ELSE 0 END), 0) AS available_hours,
    COALESCE(SUM(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0) / (720 - COALESCE(SUM(CASE WHEN ua.status IN ('maintenance', 'repair', 'cleaning') THEN EXTRACT(EPOCH FROM (COALESCE(ua.updated_at, NOW()) - ua.created_at))/3600.0 ELSE 0 END), 0)), 0) AS utilization_rate,
    COUNT(b.booking_id) AS bookings_count,
    COALESCE(AVG(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0), 0) AS avg_booking_duration,
    COALESCE(SUM(t.amount), 0) AS total_revenue,
    COALESCE(SUM(t.amount) / NULLIF(SUM(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0), 0), 0) AS revenue_per_booked_hour,
    COUNT(CASE WHEN ua.status IN ('maintenance', 'repair') THEN 1 END) AS maintenance_frequency,
    MAX(u.last_service_date) AS last_maintenance_date
FROM unicorns u
LEFT JOIN bookings b ON u.unicorn_id = b.unicorn_id AND b.start_datetime >= CURRENT_DATE - INTERVAL '30 days'
LEFT JOIN unicorn_availability ua ON u.unicorn_id = ua.unicorn_id AND ua.created_at >= CURRENT_DATE - INTERVAL '30 days'
LEFT JOIN transactions t ON b.booking_id = t.booking_id
GROUP BY u.unicorn_id, u.name;

-- 4. Current Unicorn Availability View
CREATE VIEW current_unicorn_availability WITH (security_invoker = true) AS
SELECT 
    u.unicorn_id,
    u.name AS unicorn_name,
    u.friendly_name,
    u.account_id,
    CASE 
        WHEN u.is_available THEN 'available'::TEXT
        ELSE COALESCE(ua.status::TEXT, 'out_of_service')
    END AS status,
    ua.reason AS status_reason,
    ua.expected_available_at AS available_from,
    u.hourly_rate,
    u.color,
    u.breed,
    u.horn_length_cm,
    u.seat_capacity,
    u.magic_abilities,
    u.max_speed_kmh,
    b.booking_id AS current_booking_id,
    CONCAT(c.first_name, ' ', c.last_name) AS current_customer,
    b.end_datetime AS booking_end_time
FROM unicorns u
LEFT JOIN LATERAL (
    SELECT * FROM unicorn_availability 
    WHERE unicorn_id = u.unicorn_id 
    ORDER BY created_at DESC 
    LIMIT 1
) ua ON TRUE
LEFT JOIN bookings b ON u.unicorn_id = b.unicorn_id 
    AND b.start_datetime <= NOW() 
    AND b.end_datetime >= NOW()
    AND b.is_completed = FALSE
LEFT JOIN customers c ON b.customer_id = c.customer_id
WHERE u.is_active = TRUE;

-- 5. Calendar Bookings View
CREATE VIEW calendar_bookings WITH (security_invoker = true) AS
SELECT 
    b.booking_id,
    b.booking_reference,
    DATE(b.start_datetime) AS start_date,
    b.start_datetime::TIME AS start_time,
    b.end_datetime::TIME AS end_time,
    CONCAT(c.first_name, ' ', c.last_name) AS customer_name,
    u.name AS unicorn_name,
    CONCAT(us.first_name, ' ', us.last_name) AS staff_member,
    ROUND(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0, 2) AS duration_hours,
    b.special_requests,
    CASE 
        WHEN b.is_completed THEN 'completed'
        ELSE 'confirmed'
    END AS status
FROM bookings b
JOIN customers c ON b.customer_id = c.customer_id
JOIN unicorns u ON b.unicorn_id = u.unicorn_id
JOIN users us ON b.user_id = us.user_id;

-- 6. Customer Retention Metrics View
CREATE VIEW customer_retention_metrics WITH (security_invoker = true) AS
SELECT 
    c.customer_id,
    CASE 
        WHEN c.customer_type = 'individual' THEN CONCAT(c.first_name, ' ', c.last_name)
        ELSE c.organization_name
    END AS customer_name,
    c.customer_type,
    MIN(b.created_at) AS first_booking_date,
    MAX(b.created_at) AS last_booking_date,
    COUNT(b.booking_id) AS total_bookings,
    SUM(t.amount) AS total_spent,
    AVG(t.amount) AS avg_booking_value,
    EXTRACT(DAY FROM (NOW() - MAX(b.created_at))) AS days_since_last_booking,
    EXTRACT(DAY FROM (MAX(b.created_at) - MIN(b.created_at))) / NULLIF(COUNT(b.booking_id) - 1, 0) AS booking_frequency,
    CASE 
        WHEN COUNT(b.booking_id) >= 5 THEN 0.9
        WHEN COUNT(b.booking_id) >= 3 THEN 0.7
        WHEN COUNT(b.booking_id) >= 1 THEN 0.5
        ELSE 0.1
    END AS satisfaction_score,
    CASE 
        WHEN EXTRACT(DAY FROM (NOW() - MAX(b.created_at))) > 90 THEN TRUE
        ELSE FALSE
    END AS is_at_risk,
    CASE 
        WHEN EXTRACT(DAY FROM (NOW() - MAX(b.created_at))) > 90 THEN 'churned'
        WHEN EXTRACT(DAY FROM (NOW() - MAX(b.created_at))) > 30 THEN 'at_risk'
        WHEN COUNT(b.booking_id) >= 3 THEN 'active'
        ELSE 'new'
    END AS retention_segment
FROM customers c
LEFT JOIN bookings b ON c.customer_id = b.customer_id
LEFT JOIN transactions t ON b.booking_id = t.booking_id
GROUP BY c.customer_id, c.customer_type, c.first_name, c.last_name, c.organization_name;

-- 7. Staff Performance Metrics View
CREATE VIEW staff_performance_metrics WITH (security_invoker = true) AS
SELECT 
    u.user_id,
    CONCAT(u.first_name, ' ', u.last_name) AS staff_name,
    u.role,
    u.account_id,
    CURRENT_DATE - INTERVAL '30 days' AS period_start,
    CURRENT_DATE AS period_end,
    COUNT(DISTINCT CASE WHEN b.created_at >= CURRENT_DATE - INTERVAL '30 days' THEN b.booking_id END) AS bookings_created,
    COUNT(DISTINCT CASE WHEN b.updated_at >= CURRENT_DATE - INTERVAL '30 days' AND b.created_at < CURRENT_DATE - INTERVAL '30 days' THEN b.booking_id END) AS bookings_modified,
    COALESCE(SUM(CASE WHEN t.created_at >= CURRENT_DATE - INTERVAL '30 days' THEN t.amount END), 0) AS total_revenue_generated,
    COALESCE(AVG(CASE WHEN t.created_at >= CURRENT_DATE - INTERVAL '30 days' THEN t.amount END), 0) AS avg_booking_value,
    COUNT(DISTINCT CASE WHEN b.created_at >= CURRENT_DATE - INTERVAL '30 days' THEN b.customer_id END) AS unique_customers_served,
    COALESCE(SUM(CASE WHEN t.created_at >= CURRENT_DATE - INTERVAL '30 days' THEN t.amount END) * 0.05, 0) AS commission_earned
FROM users u
LEFT JOIN bookings b ON u.user_id = b.user_id
LEFT JOIN transactions t ON b.booking_id = t.booking_id
WHERE u.role IN ('rental_admin', 'staff')
GROUP BY u.user_id, u.first_name, u.last_name, u.role, u.account_id;

-- 8. Peak Periods View
CREATE VIEW peak_periods WITH (security_invoker = true) AS
SELECT 
    'hourly' AS period_type,
    EXTRACT(HOUR FROM b.start_datetime)::TEXT AS period_identifier,
    CURRENT_DATE - INTERVAL '30 days' AS start_datetime,
    CURRENT_DATE AS end_datetime,
    COUNT(b.booking_id) AS total_bookings,
    COALESCE(SUM(t.amount), 0) AS total_revenue,
    COALESCE(AVG(t.amount), 0) AS avg_booking_value,
    COUNT(b.booking_id) / 30.0 AS booking_rate, -- Average bookings per day for this hour
    COALESCE(COUNT(b.booking_id) / (SELECT COUNT(*) FROM unicorns WHERE is_active = TRUE) / 30.0, 0) AS unicorn_utilization_rate,
    ROW_NUMBER() OVER (ORDER BY COUNT(b.booking_id) DESC) AS peak_rank,
    CASE WHEN ROW_NUMBER() OVER (ORDER BY COUNT(b.booking_id) DESC) <= 6 THEN TRUE ELSE FALSE END AS is_peak_period, -- Top 6 hours
    0.05 AS yoy_growth_rate, -- Placeholder value
    json_build_object('individual', SUM(CASE WHEN c.customer_type = 'individual' THEN 1 ELSE 0 END), 'organization', SUM(CASE WHEN c.customer_type = 'organization' THEN 1 ELSE 0 END)) AS customer_segments,
    json_build_array('standard', 'premium', 'luxury') AS popular_unicorn_types, -- Placeholder values
    NOW() AS last_updated
FROM bookings b
LEFT JOIN transactions t ON b.booking_id = t.booking_id
LEFT JOIN customers c ON b.customer_id = c.customer_id
WHERE b.start_datetime >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY EXTRACT(HOUR FROM b.start_datetime)

UNION ALL

SELECT 
    'daily' AS period_type,
    TRIM(TO_CHAR(b.start_datetime, 'Day')) AS period_identifier,
    CURRENT_DATE - INTERVAL '30 days' AS start_datetime,
    CURRENT_DATE AS end_datetime,
    COUNT(b.booking_id) AS total_bookings,
    COALESCE(SUM(t.amount), 0) AS total_revenue,
    COALESCE(AVG(t.amount), 0) AS avg_booking_value,
    COUNT(b.booking_id) / 30.0 * 7 AS booking_rate, -- Average bookings per day
    COALESCE(COUNT(b.booking_id) / (SELECT COUNT(*) FROM unicorns WHERE is_active = TRUE) / 30.0 * 7, 0) AS unicorn_utilization_rate,
    ROW_NUMBER() OVER (ORDER BY COUNT(b.booking_id) DESC) AS peak_rank,
    CASE WHEN ROW_NUMBER() OVER (ORDER BY COUNT(b.booking_id) DESC) <= 2 THEN TRUE ELSE FALSE END AS is_peak_period, -- Top 2 days
    0.08 AS yoy_growth_rate, -- Placeholder value
    json_build_object('individual', SUM(CASE WHEN c.customer_type = 'individual' THEN 1 ELSE 0 END), 'organization', SUM(CASE WHEN c.customer_type = 'organization' THEN 1 ELSE 0 END)) AS customer_segments,
    json_build_array('standard', 'premium', 'luxury') AS popular_unicorn_types, -- Placeholder values
    NOW() AS last_updated
FROM bookings b
LEFT JOIN transactions t ON b.booking_id = t.booking_id
LEFT JOIN customers c ON b.customer_id = c.customer_id
WHERE b.start_datetime >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY TO_CHAR(b.start_datetime, 'Day')

ORDER BY period_type, peak_rank;

-- 9. Top Revenue Generating Unicorns by Time Window
CREATE VIEW top_revenue_unicorns_by_period WITH (security_invoker = true) AS
SELECT 
    u.unicorn_id,
    u.name AS unicorn_name,
    u.breed,
    u.color,
    DATE_TRUNC('month', b.start_datetime) AS period,
    COUNT(b.booking_id) AS total_bookings,
    SUM(t.amount) AS total_revenue,
    AVG(t.amount) AS avg_booking_value,
    SUM(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0) AS total_booked_hours,
    SUM(t.amount) / NULLIF(SUM(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0), 0) AS revenue_per_hour
FROM unicorns u
JOIN bookings b ON u.unicorn_id = b.unicorn_id
JOIN transactions t ON b.booking_id = t.booking_id
WHERE t.transaction_type IN ('booking_fee', 'subscription', 'storage_overage')
GROUP BY u.unicorn_id, u.name, u.breed, u.color, DATE_TRUNC('month', b.start_datetime)
ORDER BY total_revenue DESC;

-- 10. Top Revenue Generating Unicorn Breeds/Models
CREATE VIEW top_revenue_unicorn_breeds WITH (security_invoker = true) AS
SELECT 
    u.breed,
    COUNT(DISTINCT u.unicorn_id) AS unicorn_count,
    COUNT(b.booking_id) AS total_bookings,
    SUM(t.amount) AS total_revenue,
    AVG(t.amount) AS avg_booking_value,
    SUM(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0) AS total_booked_hours,
    SUM(t.amount) / NULLIF(SUM(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0), 0) AS revenue_per_hour
FROM unicorns u
JOIN bookings b ON u.unicorn_id = b.unicorn_id
JOIN transactions t ON b.booking_id = t.booking_id
WHERE t.transaction_type IN ('booking_fee', 'subscription', 'storage_overage')
  AND u.breed IS NOT NULL
GROUP BY u.breed
ORDER BY total_revenue DESC;

-- 11. Bottom Revenue Generating Unicorns
CREATE VIEW bottom_revenue_unicorns WITH (security_invoker = true) AS
SELECT 
    'unicorn' AS entity_type,
    u.name AS entity_name,
    u.breed AS breed,
    COUNT(b.booking_id) AS total_bookings,
    SUM(t.amount) AS total_revenue,
    AVG(t.amount) AS avg_booking_value
FROM unicorns u
JOIN bookings b ON u.unicorn_id = b.unicorn_id
JOIN transactions t ON b.booking_id = t.booking_id
WHERE t.transaction_type IN ('booking_fee', 'subscription', 'storage_overage')
GROUP BY u.unicorn_id, u.name, u.breed
HAVING SUM(t.amount) > 0
ORDER BY total_revenue ASC
LIMIT 10;

-- 12. Bottom Revenue Generating Breeds
CREATE VIEW bottom_revenue_breeds WITH (security_invoker = true) AS
SELECT 
    'breed' AS entity_type,
    u.breed AS entity_name,
    COUNT(b.booking_id) AS total_bookings,
    SUM(t.amount) AS total_revenue,
    AVG(t.amount) AS avg_booking_value
FROM unicorns u
JOIN bookings b ON u.unicorn_id = b.unicorn_id
JOIN transactions t ON b.booking_id = t.booking_id
WHERE t.transaction_type IN ('booking_fee', 'subscription', 'storage_overage')
  AND u.breed IS NOT NULL
GROUP BY u.breed
ORDER BY total_revenue ASC
LIMIT 10;

-- 13. Top Revenue Generating Customers
CREATE VIEW top_revenue_customers WITH (security_invoker = true) AS
SELECT 
    c.customer_id,
    CASE 
        WHEN c.customer_type = 'individual' THEN CONCAT(c.first_name, ' ', c.last_name)
        ELSE c.organization_name
    END AS customer_name,
    c.customer_type,
    COUNT(b.booking_id) AS total_bookings,
    SUM(t.amount) AS total_revenue,
    AVG(t.amount) AS avg_booking_value,
    MIN(b.start_datetime) AS first_booking_date,
    MAX(b.start_datetime) AS last_booking_date,
    COUNT(DISTINCT u.unicorn_id) AS unique_unicorns_booked
FROM customers c
JOIN bookings b ON c.customer_id = b.customer_id
JOIN transactions t ON b.booking_id = t.booking_id
JOIN unicorns u ON b.unicorn_id = u.unicorn_id
WHERE t.transaction_type IN ('booking_fee', 'subscription', 'storage_overage')
GROUP BY c.customer_id, c.customer_type, c.first_name, c.last_name, c.organization_name
ORDER BY total_revenue DESC;

-- 14. Revenue by Time of Day and Day of Week
CREATE VIEW revenue_by_time_and_day WITH (security_invoker = true) AS
SELECT 
    EXTRACT(DOW FROM b.start_datetime) AS day_of_week,
    CASE EXTRACT(DOW FROM b.start_datetime)
        WHEN 0 THEN 'Sunday'
        WHEN 1 THEN 'Monday'
        WHEN 2 THEN 'Tuesday'
        WHEN 3 THEN 'Wednesday'
        WHEN 4 THEN 'Thursday'
        WHEN 5 THEN 'Friday'
        WHEN 6 THEN 'Saturday'
    END AS day_name,
    EXTRACT(HOUR FROM b.start_datetime) AS hour_of_day,
    COUNT(b.booking_id) AS total_bookings,
    SUM(t.amount) AS total_revenue,
    AVG(t.amount) AS avg_booking_value,
    COUNT(DISTINCT b.customer_id) AS unique_customers,
    COUNT(DISTINCT b.unicorn_id) AS unique_unicorns_booked
FROM bookings b
JOIN transactions t ON b.booking_id = t.booking_id
WHERE t.transaction_type IN ('booking_fee', 'subscription', 'storage_overage')
GROUP BY EXTRACT(DOW FROM b.start_datetime), EXTRACT(HOUR FROM b.start_datetime)
ORDER BY total_revenue DESC;

-- 15. Unicorn Performance Comparison (Combines multiple metrics)
CREATE VIEW unicorn_performance_comparison WITH (security_invoker = true) AS
SELECT 
    u.unicorn_id,
    u.name AS unicorn_name,
    u.breed,
    u.color,
    u.hourly_rate,
    COUNT(b.booking_id) AS total_bookings,
    SUM(t.amount) AS total_revenue,
    AVG(t.amount) AS avg_booking_value,
    SUM(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0) AS total_booked_hours,
    SUM(t.amount) / NULLIF(SUM(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0), 0) AS revenue_per_booked_hour,
    COUNT(CASE WHEN ua.status IN ('maintenance', 'repair') THEN 1 END) AS maintenance_events,
    MAX(u.last_service_date) AS last_maintenance_date,
    CASE 
        WHEN SUM(t.amount) / NULLIF(SUM(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0), 0) >= (
            SELECT AVG(revenue_per_booked_hour) 
            FROM (
                SELECT SUM(t2.amount) / NULLIF(SUM(EXTRACT(EPOCH FROM (b2.end_datetime - b2.start_datetime))/3600.0), 0) AS revenue_per_booked_hour
                FROM unicorns u2
                JOIN bookings b2 ON u2.unicorn_id = b2.unicorn_id
                JOIN transactions t2 ON b2.booking_id = t2.booking_id
                WHERE t2.transaction_type IN ('booking_fee', 'subscription', 'storage_overage')
                GROUP BY u2.unicorn_id
                HAVING SUM(EXTRACT(EPOCH FROM (b2.end_datetime - b2.start_datetime))/3600.0) > 0
            ) sub
        ) THEN 'high_performer'
        ELSE 'low_performer'
    END AS performance_category
FROM unicorns u
LEFT JOIN bookings b ON u.unicorn_id = b.unicorn_id
LEFT JOIN transactions t ON b.booking_id = t.booking_id
LEFT JOIN unicorn_availability ua ON u.unicorn_id = ua.unicorn_id AND ua.status IN ('maintenance', 'repair')
WHERE t.transaction_type IN ('booking_fee', 'subscription', 'storage_overage')
GROUP BY u.unicorn_id, u.name, u.breed, u.color, u.hourly_rate, u.last_service_date
HAVING SUM(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0) > 0
ORDER BY total_revenue DESC;

-- 16. Customer Segmentation by Revenue
CREATE VIEW customer_segmentation_by_revenue WITH (security_invoker = true) AS
WITH customer_totals AS (
    SELECT 
        c.customer_id,
        c.account_id,
        CASE 
            WHEN c.customer_type = 'individual' THEN CONCAT(c.first_name, ' ', c.last_name)
            ELSE c.organization_name
        END AS customer_name,
        c.customer_type,
        COUNT(b.booking_id) AS total_bookings,
        COALESCE(SUM(t.amount), 0) AS total_revenue,
        COALESCE(AVG(t.amount), 0) AS avg_booking_value
    FROM customers c
    LEFT JOIN bookings b ON c.customer_id = b.customer_id
    LEFT JOIN transactions t ON b.booking_id = t.booking_id AND t.transaction_type = 'booking_fee'
    GROUP BY c.customer_id, c.account_id, c.customer_type, c.first_name, c.last_name, c.organization_name
)
SELECT 
    account_id,
    CASE 
        WHEN total_revenue >= 5000 THEN 'VIP'
        WHEN total_revenue >= 2000 THEN 'Premium'
        WHEN total_revenue >= 500 THEN 'Standard'
        ELSE 'Basic'
    END AS customer_segment,
    COUNT(*) AS customer_count,
    SUM(total_bookings) AS total_bookings,
    SUM(total_revenue) AS total_revenue,
    AVG(avg_booking_value) AS avg_booking_value
FROM customer_totals
GROUP BY account_id, 
    CASE 
        WHEN total_revenue >= 5000 THEN 'VIP'
        WHEN total_revenue >= 2000 THEN 'Premium'
        WHEN total_revenue >= 500 THEN 'Standard'
        ELSE 'Basic'
    END;

-- 17. Unicorns Due for Maintenance View
CREATE VIEW unicorns_due_for_maintenance WITH (security_invoker = true) AS
SELECT 
    u.unicorn_id,
    u.account_id,
    u.name AS unicorn_name,
    u.breed,
    u.last_service_date,
    u.next_service_due,
    u.is_available,
    CASE 
        WHEN u.next_service_due <= CURRENT_DATE THEN 'overdue'
        WHEN u.next_service_due <= CURRENT_DATE + INTERVAL '7 days' THEN 'due_this_week'
        WHEN u.next_service_due <= CURRENT_DATE + INTERVAL '30 days' THEN 'due_this_month'
        ELSE 'scheduled'
    END AS maintenance_urgency,
    u.next_service_due - CURRENT_DATE AS days_until_due
FROM unicorns u
WHERE u.is_active = TRUE AND u.next_service_due IS NOT NULL
ORDER BY u.next_service_due;

-- 18. Account Subscription Status View
CREATE VIEW account_subscription_status WITH (security_invoker = true) AS
SELECT 
    a.account_id,
    a.account_name,
    a.status AS account_status,
    sp.plan_name,
    sp.monthly_price,
    sp.user_limit,
    sp.storage_limit_gb,
    a.current_user_count,
    a.current_storage_usage_gb,
    ROUND((a.current_user_count::DECIMAL / NULLIF(sp.user_limit, 0)) * 100, 2) AS user_limit_percent_used,
    ROUND((a.current_storage_usage_gb / NULLIF(sp.storage_limit_gb, 0)) * 100, 2) AS storage_percent_used,
    a.billing_cycle,
    a.next_billing_date,
    a.trial_end_date,
    a.activated_at
FROM accounts a
JOIN subscription_plans sp ON a.plan_id = sp.plan_id;

-- 19. Revenue Comparison View (Daily with comparisons)
CREATE VIEW revenue_comparison WITH (security_invoker = true) AS
WITH daily_revenue AS (
    SELECT 
        account_id,
        DATE(created_at) AS revenue_date,
        SUM(CASE WHEN transaction_type IN ('booking_fee', 'subscription', 'storage_overage') THEN amount ELSE 0 END) AS revenue,
        SUM(CASE WHEN transaction_type = 'refund' THEN amount ELSE 0 END) AS refunds,
        COUNT(*) AS transaction_count
    FROM transactions
    GROUP BY account_id, DATE(created_at)
)
SELECT 
    account_id,
    revenue_date,
    revenue,
    refunds,
    revenue - refunds AS net_revenue,
    transaction_count,
    LAG(revenue, 1) OVER (PARTITION BY account_id ORDER BY revenue_date) AS prev_day_revenue,
    LAG(revenue, 7) OVER (PARTITION BY account_id ORDER BY revenue_date) AS same_day_last_week_revenue,
    SUM(revenue) OVER (PARTITION BY account_id, DATE_TRUNC('month', revenue_date)) AS month_to_date_revenue
FROM daily_revenue
ORDER BY account_id, revenue_date DESC;

-- 20. Customer Lifetime Value View
CREATE VIEW customer_lifetime_value WITH (security_invoker = true) AS
SELECT 
    c.customer_id,
    c.account_id,
    CASE 
        WHEN c.customer_type = 'individual' THEN CONCAT(c.first_name, ' ', c.last_name)
        ELSE c.organization_name
    END AS customer_name,
    c.customer_type,
    c.created_at AS customer_since,
    COUNT(DISTINCT b.booking_id) AS total_bookings,
    COALESCE(SUM(t.amount), 0) AS total_revenue,
    COALESCE(AVG(t.amount), 0) AS avg_transaction_value,
    MAX(b.created_at) AS last_booking_date
FROM customers c
LEFT JOIN bookings b ON c.customer_id = b.customer_id
LEFT JOIN transactions t ON b.booking_id = t.booking_id AND t.transaction_type = 'booking_fee'
GROUP BY c.customer_id, c.account_id, c.customer_type, c.first_name, c.last_name, c.organization_name, c.created_at;

-- 21. Seasonal Trends View
CREATE VIEW seasonal_trends WITH (security_invoker = true) AS
SELECT 
    b.account_id,
    EXTRACT(MONTH FROM b.start_datetime) AS month_number,
    TO_CHAR(b.start_datetime, 'Month') AS month_name,
    EXTRACT(YEAR FROM b.start_datetime) AS year,
    COUNT(*) AS total_bookings,
    SUM(t.amount) AS total_revenue,
    AVG(t.amount) AS avg_booking_value,
    COUNT(DISTINCT b.customer_id) AS unique_customers
FROM bookings b
LEFT JOIN transactions t ON b.booking_id = t.booking_id AND t.transaction_type = 'booking_fee'
GROUP BY b.account_id, EXTRACT(MONTH FROM b.start_datetime), TO_CHAR(b.start_datetime, 'Month'), EXTRACT(YEAR FROM b.start_datetime)
ORDER BY b.account_id, year, month_number;

-- 22. Subscription Tracker View
CREATE VIEW subscription_tracker_summary WITH (security_invoker = true) AS
SELECT 
    st.tracker_id,
    st.account_id,
    a.account_name,
    st.datetime,
    st.plan_id,
    sp.plan_name,
    st.monthly_price,
    st.hourly_price,
    EXTRACT(YEAR FROM st.datetime) AS year,
    EXTRACT(MONTH FROM st.datetime) AS month,
    EXTRACT(DAY FROM st.datetime) AS day,
    EXTRACT(HOUR FROM st.datetime) AS hour
FROM subscription_tracker st
JOIN accounts a ON st.account_id = a.account_id
JOIN subscription_plans sp ON st.plan_id = sp.plan_id
ORDER BY st.account_id, st.datetime;

-- End of Timely-Unicorn Rental Management System Schema (PostgreSQL Compatible with Native ENUM Types)


-- ============================================================================
-- ROW-LEVEL SECURITY (RLS) - Helper Functions, Enable RLS, and Policies
-- ============================================================================

-- Helper Functions for Session Context
-- The application must SET app.current_account_id and app.current_user_role from JWT claims before queries

CREATE OR REPLACE FUNCTION get_current_account_id() RETURNS UUID AS $$
BEGIN
  RETURN current_setting('app.current_account_id', true)::UUID;
EXCEPTION WHEN OTHERS THEN
  RETURN NULL;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE OR REPLACE FUNCTION get_current_user_role() RETURNS user_role_enum AS $$
BEGIN
  RETURN current_setting('app.current_user_role', true)::user_role_enum;
EXCEPTION WHEN OTHERS THEN
  RETURN NULL;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Enable RLS on All Tables
ALTER TABLE subscription_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE unicorns ENABLE ROW LEVEL SECURITY;
ALTER TABLE unicorn_availability ENABLE ROW LEVEL SECURITY;
ALTER TABLE bookings ENABLE ROW LEVEL SECURITY;
ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscription_tracker ENABLE ROW LEVEL SECURITY;


-- ---- subscription_plans Policies ----
CREATE POLICY saas_admin_all_plans ON subscription_plans
  FOR ALL USING (get_current_user_role() = 'saas_admin');

CREATE POLICY tenant_read_plans ON subscription_plans
  FOR SELECT USING (get_current_user_role() IN ('rental_admin', 'staff', 'analyst'));

-- ---- accounts Policies ----
CREATE POLICY saas_admin_all_accounts ON accounts
  FOR ALL USING (get_current_user_role() = 'saas_admin');

CREATE POLICY rental_admin_accounts ON accounts
  FOR ALL USING (get_current_user_role() = 'rental_admin' AND account_id = get_current_account_id());

CREATE POLICY staff_read_accounts ON accounts
  FOR SELECT USING (get_current_user_role() = 'staff' AND account_id = get_current_account_id());

CREATE POLICY analyst_read_accounts ON accounts
  FOR SELECT USING (get_current_user_role() = 'analyst' AND account_id = get_current_account_id());

-- ---- customers Policies ----
CREATE POLICY saas_admin_all_customers ON customers
  FOR ALL USING (get_current_user_role() = 'saas_admin');

CREATE POLICY rental_admin_customers ON customers
  FOR ALL USING (get_current_user_role() = 'rental_admin' AND account_id = get_current_account_id());

CREATE POLICY staff_customers ON customers
  FOR ALL USING (get_current_user_role() = 'staff' AND account_id = get_current_account_id());

CREATE POLICY analyst_read_customers ON customers
  FOR SELECT USING (get_current_user_role() = 'analyst' AND account_id = get_current_account_id());

-- ---- users Policies ----
CREATE POLICY saas_admin_all_users ON users
  FOR ALL USING (get_current_user_role() = 'saas_admin');

CREATE POLICY rental_admin_users ON users
  FOR ALL USING (get_current_user_role() = 'rental_admin' AND account_id = get_current_account_id());

CREATE POLICY staff_read_users ON users
  FOR SELECT USING (get_current_user_role() = 'staff' AND account_id = get_current_account_id());

CREATE POLICY analyst_read_users ON users
  FOR SELECT USING (get_current_user_role() = 'analyst' AND account_id = get_current_account_id());


-- ---- unicorns Policies ----
CREATE POLICY saas_admin_all_unicorns ON unicorns
  FOR ALL USING (get_current_user_role() = 'saas_admin');

CREATE POLICY rental_admin_unicorns ON unicorns
  FOR ALL USING (get_current_user_role() = 'rental_admin' AND account_id = get_current_account_id());

CREATE POLICY staff_unicorns ON unicorns
  FOR ALL USING (get_current_user_role() = 'staff' AND account_id = get_current_account_id());

CREATE POLICY analyst_read_unicorns ON unicorns
  FOR SELECT USING (get_current_user_role() = 'analyst' AND account_id = get_current_account_id());

-- ---- unicorn_availability Policies (scoped via unicorns table join) ----
CREATE POLICY saas_admin_all_availability ON unicorn_availability
  FOR ALL USING (get_current_user_role() = 'saas_admin');

CREATE POLICY rental_admin_availability ON unicorn_availability
  FOR ALL USING (get_current_user_role() = 'rental_admin' AND account_id = get_current_account_id());

CREATE POLICY staff_availability ON unicorn_availability
  FOR ALL USING (get_current_user_role() = 'staff' AND account_id = get_current_account_id());

CREATE POLICY analyst_read_availability ON unicorn_availability
  FOR SELECT USING (get_current_user_role() = 'analyst' AND account_id = get_current_account_id());

-- ---- bookings Policies ----
CREATE POLICY saas_admin_all_bookings ON bookings
  FOR ALL USING (get_current_user_role() = 'saas_admin');

CREATE POLICY rental_admin_bookings ON bookings
  FOR ALL USING (get_current_user_role() = 'rental_admin' AND account_id = get_current_account_id());

CREATE POLICY staff_bookings ON bookings
  FOR ALL USING (get_current_user_role() = 'staff' AND account_id = get_current_account_id());

CREATE POLICY analyst_read_bookings ON bookings
  FOR SELECT USING (get_current_user_role() = 'analyst' AND account_id = get_current_account_id());

-- ---- transactions Policies ----
CREATE POLICY saas_admin_all_transactions ON transactions
  FOR ALL USING (get_current_user_role() = 'saas_admin');

CREATE POLICY rental_admin_transactions ON transactions
  FOR ALL USING (get_current_user_role() = 'rental_admin' AND account_id = get_current_account_id());

CREATE POLICY staff_read_transactions ON transactions
  FOR SELECT USING (get_current_user_role() = 'staff' AND account_id = get_current_account_id());

CREATE POLICY analyst_read_transactions ON transactions
  FOR SELECT USING (get_current_user_role() = 'analyst' AND account_id = get_current_account_id());

-- ---- subscription_tracker Policies ----
CREATE POLICY saas_admin_all_tracker ON subscription_tracker
  FOR ALL USING (get_current_user_role() = 'saas_admin');

CREATE POLICY rental_admin_tracker ON subscription_tracker
  FOR ALL USING (get_current_user_role() = 'rental_admin' AND account_id = get_current_account_id());

CREATE POLICY analyst_read_tracker ON subscription_tracker
  FOR SELECT USING (get_current_user_role() = 'analyst' AND account_id = get_current_account_id());

-- End of Row-Level Security Configuration
