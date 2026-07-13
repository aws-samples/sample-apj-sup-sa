# Table: transactions_1k

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| TransactionID | Transaction ID | integer | Transaction ID |
| Date |  | date | Date |
| Time |  | text | Time |
| CustomerID | Customer ID | integer | Customer ID |
| CardID | Card ID | integer | Card ID |
| GasStationID | Gas Station ID | integer | Gas Station ID |
| ProductID | Product ID | integer | Product ID |
| Amount |  | integer | Amount |
| Price |  | real | Price |

## Business Logic & Value Descriptions

### Price

- commonsense evidence:
- total price = Amount x Price
