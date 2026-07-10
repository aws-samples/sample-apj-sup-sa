# Timely-Unicorn Rental Management System - User Stories

## Overview
Timely-Unicorn is a multi-tenant SaaS platform for unicorn rental management serving multiple unicorn rental businesses. Each rental business (tenant) uses the platform to manage their unicorn inventory, customer bookings, and business operations.

## User Roles
1. **SaaS Admin** - Manages the entire platform, including all tenant accounts and platform-wide settings
2. **Rental Admin** - Manages account settings, billing, and overall system configuration for their rental business
3. **Rental Staff** - Handles day-to-day operations including unicorn management, bookings, and customer service for their rental business
4. **Analyst** - Views analytics and reporting data for their rental business with read-only access

## Detailed User Stories

### Account and Subscription Management

#### As a Rental Admin, I want to:
1. Set up my rental business account with a subscription plan (Free, Starter, Small Business, Enterprise, or Custom) so that I can determine the features available to my business
2. Configure billing details including payment methods and invoicing preferences so that I can manage payments seamlessly
3. Update my rental business profile information including company name, logo, and contact details so that customers can easily identify my business
4. View my current plan details, usage statistics, and upcoming charges so that I can monitor my subscription
5. Upgrade or downgrade my subscription plan based on changing business needs so that I only pay for what I need
6. Add additional storage when I exceed my plan's allocated storage so that I won't lose data

### Unicorn Management

#### As Rental Staff, I want to:
1. Add new unicorns to the system with complete details (ID, name, year of making, features, seat capacity, machine number) so that I can maintain an accurate inventory for my rental business
2. Edit unicorn details when information changes so that our records remain up-to-date
3. Set and update price-per-hour for each unicorn so that customers are charged appropriately
4. Mark unicorns as unavailable due to maintenance, repairs, or other reasons so that customers cannot book them
5. View the availability status of all unicorns at a glance so that I can quickly respond to customer inquiries
6. Search and filter unicorns by various criteria (availability, features, pricing) so that I can efficiently find suitable options for customers
7. Archive retired unicorns so that they're preserved for historical records but don't clutter active inventory
8. See real-time availability status updates when unicorns are returned or become unavailable so that I have immediate visibility into inventory changes

### Booking and Customer Management

#### As Rental Staff, I want to:
1. Create new rental bookings by selecting a unicorn, customer, and time period so that I can fulfill customer requests (bookings are confirmed upon creation)
2. View all current and upcoming bookings in a calendar format so that I can manage scheduling effectively
3. Modify existing bookings (extend, shorten, change unicorn) when customers request changes so that we can accommodate their needs
4. Mark bookings as completed when unicorns are returned so that they become available for new rentals
5. Add new customers (renters) to the system with their complete details so that we can serve them properly
6. Maintain separate records for individual customers and organizational customers so that we can tailor our communications appropriately
7. Update customer information when it changes so that our records remain accurate
8. View customer booking history and payment records so that I can provide personalized service

### Transaction and Reporting

#### As a Rental Admin, I want to:
1. Generate reports on transaction volumes, revenue, and unicorn utilization so that I can analyze business performance
2. View detailed financial reports including income by period, customer, and unicorn so that I can make informed business decisions
3. Export transaction data in various formats for accounting purposes so that I can integrate with my financial systems
4. Track which unicorns generate the most revenue so that I can optimize my inventory
5. Identify peak booking periods so that I can adjust staffing and pricing strategies
6. Monitor customer retention and satisfaction metrics so that I can improve service quality
7. View customer lifetime value metrics so that I can identify and prioritize high-value customers
8. Analyze seasonal trends in bookings and revenue so that I can plan for peak and off-peak periods

#### As Rental Staff, I want to:
1. Access real-time transaction data for my shifts so that I can reconcile daily activities
2. Generate quick reports on daily bookings and revenue so that I can provide updates to management

### System Administration

#### As a Rental Admin, I want to:
1. Manage user permissions and roles for staff members so that I can control access to sensitive functions
2. Configure system preferences and defaults so that the platform works according to my business processes
3. Integrate with third-party systems (accounting, CRM) so that I can streamline operations

### Subscription and Account Management

#### As a Rental Admin, I want to:
1. View my current subscription plan details including user limits and storage quotas so that I can monitor usage
2. See percentage of plan limits used (users, storage) so that I can plan for upgrades before hitting limits
3. Track my billing cycle and next billing date so that I can manage cash flow

### Subscription Tracking and Revenue Analysis

#### As a Rental Admin, I want to:
1. View hourly subscription status history for my account so that I can understand my plan usage over time
2. See revenue generated from my subscription on an hourly basis so that I can track my spending
3. Analyze my subscription plan changes over time so that I can evaluate the impact of plan upgrades or downgrades
4. Export subscription tracking data for accounting purposes so that I can reconcile billing statements
5. View projected monthly costs based on current subscription status so that I can budget effectively

#### As a SaaS Platform Operator, I want to:
1. Track subscription status for all accounts on an hourly basis so that I can accurately calculate revenue
2. Generate revenue reports by subscription plan and time period so that I can analyze business performance
3. Identify accounts that frequently change subscription plans so that I can improve retention strategies
4. Monitor overall platform revenue trends so that I can forecast future earnings
5. Analyze the impact of subscription plan changes on revenue so that I can optimize pricing strategies

### Maintenance Planning

#### As Rental Staff, I want to:
1. View unicorns that are due or overdue for maintenance so that I can schedule service without disrupting bookings
2. See maintenance urgency levels (overdue, due this week, due this month) so that I can prioritize service scheduling

### Business Analytics and Reporting

#### As an Analyst, I want to:
1. View detailed financial reports including income by period, customer, and unicorn so that I can analyze business performance
2. Generate reports on transaction volumes, revenue, and unicorn utilization so that I can identify trends
3. Access real-time transaction data for my shifts so that I can reconcile daily activities
4. Generate quick reports on daily bookings and revenue so that I can provide updates to management
5. Review and approve generated SQL queries before execution so that I maintain control over data access
6. Edit suggested SQL queries when needed so that I can refine the analysis
7. Use semantic search to discover relevant data sources so that I can find information even with unfamiliar terminology
8. Ask custom analytics questions that aren't covered by standard reports so that I can explore data flexibly

### Additional Crucial Capabilities

#### As any User, I want to:
1. Access the system through both web and mobile applications so that I can work from anywhere
2. Have a responsive and intuitive interface that works on different devices so that I can be productive

#### As Rental Staff, I want to:
1. Quickly check unicorn availability for specific time slots so that I can provide instant responses to customer inquiries
2. Add notes to bookings for special instructions or customer preferences so that service quality is maintained
3. Transfer bookings between staff members when needed so that coverage gaps are avoided


### AI-Powered Analytics (Natural Language Interface)

#### As a Business User, I want to:
1. Ask questions about my business data in natural language so that I can get insights without writing SQL queries
2. Get formatted responses with tables and charts so that I can easily understand the data
3. Have the system understand my intent even when I don't use exact column names so that I can query data naturally
4. Create bookings through conversation by specifying customer, unicorn, and time so that I can serve customers quickly

#### As a Rental Admin, I want to:
1. Ask custom analytics questions that aren't covered by standard reports so that I can explore data flexibly
2. Review and approve generated SQL queries before execution so that I maintain control over data access
3. Edit suggested SQL queries when needed so that I can refine the analysis
4. Use semantic search to discover relevant data sources so that I can find information even with unfamiliar terminology
