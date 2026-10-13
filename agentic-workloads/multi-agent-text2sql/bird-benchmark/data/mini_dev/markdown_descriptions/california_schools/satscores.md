# Table: satscores

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| cds |  | text | California Department Schools |
| rtype |  | text | rtype |
| sname | school name | text | school name |
| dname | district name | text | district segment |
| cname | county name | text | county name |
| enroll12 | enrollment (1st-12nd grade) | integer | enrollment (1st-12nd grade) |
| NumTstTakr | Number of Test Takers | integer | Number of Test Takers in this school |
| AvgScrRead | average scores in Reading | integer | average scores in Reading |
| AvgScrMath | average scores in Math | integer | average scores in Math |
| AvgScrWrite | average scores in writing | integer | average scores in writing |
| NumGE1500 | Number of Test Takers Whose Total SAT Scores Are Greater or Equal to 1500 | integer | Number of Test Takers Whose Total SAT Scores Are Greater or Equal to 1500 |

## Business Logic & Value Descriptions

### rtype

- unuseful

### NumTstTakr (Number of Test Takers)

- number of test takers in each school

### AvgScrRead (average scores in Reading)

- average scores in Reading

### AvgScrMath (average scores in Math)

- average scores in Math

### AvgScrWrite (average scores in writing)

- average scores in writing

### NumGE1500 (Number of Test Takers Whose Total SAT Scores Are Greater or Equal to 1500)

- Number of Test Takers Whose Total SAT Scores Are Greater or Equal to 1500
- commonsense evidence:
- Excellence Rate = NumGE1500 / NumTstTakr

## Relationships

- satscores.cds → schools.CDSCode (학교 상세 정보)
- satscores.cds → frpm.CDSCode (학교 급식/등록 데이터)

**조인 키**: satscores의 조인 키는 `cds`이고, schools/frpm의 조인 키는 `CDSCode`입니다.
