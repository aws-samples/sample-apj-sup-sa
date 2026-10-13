# Table: Income

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| income_id | income id | text | A unique identifier for each record of income |
| date_received | date received | text | the date that the fund received |
| amount |  | integer | amount of funds |
| source |  | text | A value indicating where the funds come from such as dues, or the annual university allocation |
| notes |  | text | A free-text value giving any needed details about the receipt of funds |
| link_to_member | link to member | text | link to member |

## Business Logic & Value Descriptions

### amount

- the unit is dollar
