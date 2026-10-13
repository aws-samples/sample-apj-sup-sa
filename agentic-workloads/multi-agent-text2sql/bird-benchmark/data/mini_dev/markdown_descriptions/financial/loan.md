# Table: loan

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| loan_id |  | integer | the id number identifying the loan data |
| account_id |  | integer | the id number identifying the account |
| date |  | date | the date when the loan is approved |
| amount |  | integer | approved amount |
| duration |  | integer | loan duration |
| payments | monthly payments | real | monthly payments |
| status |  | text | repayment status |

## Business Logic & Value Descriptions

### amount

- unit：US dollar

### duration

- unit：month

### payments (monthly payments)

- unit：month

### status

- 'A' stands for contract finished, no problems;
- 'B' stands for contract finished, loan not paid;
- 'C' stands for running contract, OK so far;
- 'D' stands for running contract, client in debt

## Relationships

- loan.account_id → account.account_id
- account.account_id → disp.account_id → disp.client_id → client.client_id (대출의 고객 조회 시)
- account.district_id → district.district_id (대출 계좌의 지역 정보)

## Sample Query

### 10만달러 미만 대출 중 정상 진행(running OK) 비율
```sql
SELECT CAST(SUM(CASE WHEN status = 'C' THEN 1 ELSE 0 END) AS DOUBLE) * 100 / COUNT(account_id)
FROM loan
WHERE amount < 100000
```

### 특정 날짜 대출 계좌의 잔액 변화율 계산
```sql
SELECT
  CAST((SUM(CASE WHEN t.date = '1998-12-27' THEN t.balance ELSE 0 END)
      - SUM(CASE WHEN t.date = '1993-03-22' THEN t.balance ELSE 0 END)) AS DOUBLE)
  * 100 / SUM(CASE WHEN t.date = '1993-03-22' THEN t.balance ELSE 0 END)
FROM loan l
INNER JOIN account a ON l.account_id = a.account_id
INNER JOIN trans t ON t.account_id = a.account_id
WHERE l.date = '1993-07-05'
```
