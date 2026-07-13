# Table: Expense

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| expense_id | expense id | text | unique id of income |
| expense_description | expense description | text | A textual description of what the money was spend for |
| expense_date | expense date | text | The date the expense was incurred |
| cost |  | real | The dollar amount of the expense |
| approved |  | text | A true or false value indicating if the expense was approved |
| link_to_member | link to member | text | The member who incurred the expense |
| link_to_budget | link to budget | text | The unique identifier of the record in the Budget table that indicates the expected total expenditure for a given category and event. |

## Business Logic & Value Descriptions

### expense_date (expense date)

- e.g. YYYY-MM-DD

### cost

- the unit is dollar

### approved

- true/ false

### link_to_budget (link to budget)

- References the Budget table
