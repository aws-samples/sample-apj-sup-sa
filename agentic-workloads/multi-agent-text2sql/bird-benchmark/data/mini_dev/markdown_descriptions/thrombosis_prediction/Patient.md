# Table: Patient

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| ID |  | integer | identification of the patient |
| SEX |  | text | Sex |
| Birthday |  | date | Birthday |
| Description |  | date | the first date when a patient data was recorded |
| First Date |  | date | the date when a patient came to the hospital |
| Admission |  | text | patient was admitted to the hospital (+) or followed at the outpatient clinic (-) |
| Diagnosis |  | text | disease names |

## Business Logic & Value Descriptions

### SEX

- F: female; M: male

### Description

- null or empty: not recorded

### Admission

- patient was admitted to the hospital (+) or followed at the outpatient clinic (-)

## Sample Query

### Q: Are there more in-patient or outpatient who were male? What is the deviation in percentage?
- 패턴: "deviation in percentage" → A * 100 / B 단일 값 반환
- 핵심: admission = '+' (입원), '-' (외래)
```sql
SELECT CAST(SUM(CASE WHEN admission = '+' THEN 1 ELSE 0 END) AS DOUBLE) * 100
     / SUM(CASE WHEN admission = '-' THEN 1 ELSE 0 END)
FROM patient
WHERE sex = 'M'
- 주의: "deviation/percentage 비교" 질문은 GROUP BY 분포가 아니라 A/B 나눗셈 단일 값