# Table: frpm

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| CDSCode |  | integer | CDSCode |
| Academic Year |  | integer | Academic Year |
| County Code |  | integer | County Code |
| District Code |  | integer | District Code |
| School Code |  | integer | School Code |
| County Name |  | text | County Code |
| District Name |  | text | District Name |
| School Name |  | text | School Name |
| District Type |  | text | District Type |
| School Type |  | text | School Type |
| Educational Option Type |  | text | Educational Option Type |
| NSLP Provision Status |  | text | NSLP Provision Status |
| Charter School (Y/N) |  | integer | Charter School (Y/N) |
| Charter School Number |  | text | Charter School Number |
| Charter Funding Type |  | text | Charter Funding Type |
| IRC |  | integer | - |
| Low Grade |  | text | Low Grade |
| High Grade |  | text | High Grade |
| Enrollment (K-12) |  | real | Enrollment (K-12) |
| Free Meal Count (K-12) |  | real | Free Meal Count (K-12) |
| Percent (%) Eligible Free (K-12) |  | real | - |
| FRPM Count (K-12) |  | real | Free or Reduced Price Meal Count (K-12) |
| Percent (%) Eligible FRPM (K-12) |  | real | - |
| Enrollment (Ages 5-17) |  | real | Enrollment (Ages 5-17) |
| Free Meal Count (Ages 5-17) |  | real | Free Meal Count (Ages 5-17) |
| Percent (%) Eligible Free (Ages 5-17) |  | real | - |
| FRPM Count (Ages 5-17) |  | real | - |
| Percent (%) Eligible FRPM (Ages 5-17) |  | real | - |
| 2013-14 CALPADS Fall 1 Certification Status |  | integer | 2013-14 CALPADS Fall 1 Certification Status |

## Business Logic & Value Descriptions

### Charter School (Y/N)

- 0: N;
- 1: Y

### Enrollment (K-12)

- commonsense evidence:
- K-12: 1st grade - 12nd grade

### Free Meal Count (K-12)

- commonsense evidence:
- eligible free rate = Free Meal Count / Enrollment

### FRPM Count (K-12)

- commonsense evidence:
- eligible FRPM rate = FRPM / Enrollment

### Free Meal Count (Ages 5-17)

- commonsense evidence:
- eligible free rate = Free Meal Count / Enrollment

## Relationships

- frpm.CDSCode → schools.CDSCode (학교 상세 정보: DOC, SOC, FundingType 등)
- frpm.CDSCode → satscores.cds (학교별 SAT 점수)

**컬럼명 주의**: 이 테이블의 컬럼명에 공백과 특수문자가 포함되어 있어 따옴표로 감싸야 합니다.
예: "Enrollment (K-12)", "Charter Funding Type", "District Name"
