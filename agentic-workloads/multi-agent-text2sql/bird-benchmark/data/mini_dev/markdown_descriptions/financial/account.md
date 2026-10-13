# Table: account

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| account_id | account id | integer | the id of the account |
| district_id | location of branch | integer | location of branch |
| frequency | frequency | text | frequency of the acount |
| date | date | date | the creation date of the account |

## Business Logic & Value Descriptions

### frequency (frequency)

- "POPLATEK MESICNE" stands for monthly issuance
- "POPLATEK TYDNE" stands for weekly issuance
- "POPLATEK PO OBRATU" stands for issuance after transaction

### date (date)

- in the form YYMMDD

## Relationships

- account.district_id → district.district_id (계좌 소재 지역)
- account.account_id → disp.account_id → disp.client_id → client.client_id (계좌 소유자)
- account.account_id → loan.account_id (대출)
- account.account_id → trans.account_id (거래 내역)
- account.account_id → order.account_id (상시 이체)
