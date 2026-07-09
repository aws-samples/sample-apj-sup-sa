# Table: district

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| district_id | location of branch | integer | location of branch |
| A2 | district_name | text | district_name |
| A3 | region | text | region |
| A4 | number of inhabitants | text | - |
| A5 | no. of municipalities with inhabitants < 499 | text | municipality < district < region |
| A6 | no. of municipalities with inhabitants 500-1999 | text | municipality < district < region |
| A7 | no. of municipalities with inhabitants 2000-9999 | text | municipality < district < region |
| A8 | no. of municipalities with inhabitants > 10000 | integer | municipality < district < region |
| A9 |  | integer | - |
| A10 | ratio of urban inhabitants | real | ratio of urban inhabitants |
| A11 | average salary | integer | average salary |
| A12 | unemployment rate 1995 | real | unemployment rate 1995 |
| A13 | unemployment rate 1996 | real | unemployment rate 1996 |
| A14 | no. of entrepreneurs per 1000 inhabitants | integer | no. of entrepreneurs per 1000 inhabitants |
| A15 | no. of committed crimes 1995 | integer | no. of committed crimes 1995 |
| A16 | no. of committed crimes 1996 | integer | no. of committed crimes 1996 |

## Relationships

- district.district_id → account.district_id (지역 내 계좌)
- district.district_id → client.district_id (지역 내 고객)

**컬럼명 참고**: A2=지역명, A3=리전(예: 'north Bohemia'), A11=평균급여