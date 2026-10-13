# Table: order

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| order_id |  | integer | identifying the unique order |
| account_id |  | integer | id number of account |
| bank_to | bank of the recipient | text | bank of the recipient |
| account_to | account of the recipient | integer | account of the recipient |
| amount | debited amount | real | debited amount |
| k_symbol | characterization of the payment | text | purpose of the payment |

## Business Logic & Value Descriptions

### account_to (account of the recipient)

- each bank has unique two-letter code

### k_symbol (characterization of the payment)

- "POJISTNE" stands for insurance payment
- "SIPO" stands for household payment
- "LEASING" stands for leasing
- "UVER" stands for loan payment
