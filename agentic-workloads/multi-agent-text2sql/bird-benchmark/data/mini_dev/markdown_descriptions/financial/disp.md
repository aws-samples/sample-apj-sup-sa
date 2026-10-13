# Table: disp

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| disp_id | disposition id | integer | unique number of identifying this row of record |
| client_id |  | integer | id number of client |
| account_id |  | integer | id number of account |
| type |  | text | type of disposition |

## Business Logic & Value Descriptions

### type

- "OWNER" : owner of the account, can issue orders and apply for loans
- "DISPONENT" : user with limited rights on the account

## Relationships

- disp.client_id → client.client_id
- disp.account_id → account.account_id
- disp.disp_id → card.disp_id (크레딧카드 연결)

**중요**: disp는 client와 account를 연결하는 브릿지 테이블입니다. 고객의 계좌/대출/거래를 조회하려면 반드시 disp를 경유해야 합니다.

## Sample Query

### 대출 대상이 아닌 계좌 유형 조회 (disp.type != 'OWNER')
```sql
SELECT d.type
FROM district dt
INNER JOIN account a ON dt.district_id = a.district_id
INNER JOIN disp d ON a.account_id = d.account_id
WHERE d.type != 'OWNER' AND dt.A11 BETWEEN 8000 AND 9000
```
