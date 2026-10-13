# Table: trans

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| trans_id | transaction id | integer | transaction id |
| account_id |  | integer | - |
| date | date of transaction | date | date of transaction |
| type | +/- transaction | text | +/- transaction |
| operation | mode of transaction | text | mode of transaction |
| amount | amount of money | integer | amount of money |
| balance | balance after transaction | integer | balance after transaction |
| k_symbol | characterization of the transaction | text | - |
| bank | bank of the partner | text | - |
| account | account of the partner | integer | - |

## Business Logic & Value Descriptions

### type (+/- transaction)

- "PRIJEM" stands for credit
- "VYDAJ" stands for withdrawal

### operation (mode of transaction)

- "VYBER KARTOU": credit card withdrawal
- "VKLAD": credit in cash
- "PREVOD Z UCTU" :collection from another bank
- "VYBER": withdrawal in cash
- "PREVOD NA UCET": remittance to another bank

### amount (amount of money)

- Unit：USD

### balance (balance after transaction)

- Unit：USD

### k_symbol (characterization of the transaction)

- "POJISTNE": stands for insurrance payment
- "SLUZBY": stands for payment for statement
- "UROK": stands for interest credited
- "SANKC. UROK": sanction interest if negative balance
- "SIPO": stands for household
- "DUCHOD": stands for old-age pension
- "UVER": stands for loan payment

### bank (bank of the partner)

- each bank has unique two-letter code
