# Table: client

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| client_id |  | integer | the unique number |
| gender |  | text | - |
| birth_date |  | date | birth date |
| district_id | location of branch | integer | location of branch |

## Business Logic & Value Descriptions

### gender

- F：female
- M：male

## Relationships

- client.district_id → district.district_id (고객 거주 지역)
- client.client_id → disp.client_id → disp.account_id → account.account_id (고객↔계좌 연결은 반드시 disp 테이블 경유)

**중요**: client와 account를 직접 JOIN할 수 없습니다. 반드시 disp 테이블을 거쳐야 합니다.
- client → disp (client_id) → account (account_id) → loan, trans, order

## Sample Query

### 북보헤미아 지역 남성 고객 중 평균 급여 8000 초과
```sql
SELECT COUNT(c.client_id)
FROM client c
INNER JOIN district d ON c.district_id = d.district_id
WHERE c.gender = 'M' AND d.A3 = 'north Bohemia' AND d.A11 > 8000
```

### 주간 명세서를 요청하는 남성 고객 비율
```sql
SELECT CAST(SUM(CASE WHEN c.gender = 'M' THEN 1 ELSE 0 END) AS DOUBLE) * 100 / COUNT(c.client_id)
FROM client c
INNER JOIN disp dp ON c.client_id = dp.client_id
INNER JOIN account a ON dp.account_id = a.account_id
WHERE a.frequency = 'POPLATEK TYDNE'
```
