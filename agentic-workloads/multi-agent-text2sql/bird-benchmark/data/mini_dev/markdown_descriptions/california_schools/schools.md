# Table: schools

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| CDSCode |  | text | CDSCode |
| NCESDist | National Center for Educational Statistics school district identification number | text | This field represents the 7-digit National Center for Educational Statistics (NCES) school district identification number. The first 2 digits identify the state and the last 5 digits identify the school district. Combined, they make a unique 7-digit ID for each school district. |
| NCESSchool | National Center for Educational Statistics school identification number | text | This field represents the 5-digit NCES school identification number. The NCESSchool combined with the NCESDist form a unique 12-digit ID for each school. |
| StatusType |  | text | This field identifies the status of the district. |
| County |  | text | County name |
| District |  | text | District |
| School |  | text | School |
| Street |  | text | Street |
| StreetAbr | street address | text | The abbreviated street address of the school, district, or administrative authority's physical location. |
| City |  | text | City |
| Zip |  | text | Zip |
| State |  | text | State |
| MailStreet |  | text | MailStreet |
| MailStrAbr | mailing street address | text | - |
| MailCity | mailing city | text | - |
| MailZip | mailing zip | text | - |
| MailState | mailing state | text | - |
| Phone |  | text | Phone |
| Ext | extension | text | The phone number extension of the school, district, or administrative authority. |
| Website |  | text | The website address of the school, district, or administrative authority. |
| OpenDate |  | date | The date the school opened. |
| ClosedDate |  | date | The date the school closed. |
| Charter |  | integer | This field identifies a charter school. |
| CharterNum |  | text | The charter school number, |
| FundingType |  | text | Indicates the charter school funding type |
| DOC | District Ownership Code | text | District Ownership Code |
| DOCType | The District Ownership Code Type | text | The District Ownership Code Type is the text description of the DOC category. |
| SOC | School Ownership Code | text | The School Ownership Code is a numeric code used to identify the type of school. |
| SOCType | School Ownership Code Type | text | The School Ownership Code Type is the text description of the type of school. |
| EdOpsCode | Education Option Code | text | The Education Option Code is a short text description of the type of education offered. |
| EdOpsName | Educational Option Name | text | Educational Option Name |
| EILCode | Educational Instruction Level Code | text | The Educational Instruction Level Code is a short text description of the institution's type relative to the grade range served. |
| EILName | Educational Instruction Level Name | text | The Educational Instruction Level Name is the long text description of the institution's type relative to the grade range served. |
| GSoffered | grade span offered | text | The grade span offered is the lowest grade and the highest grade offered or supported by the school, district, or administrative authority. This field might differ from the grade span served as reported in the most recent certified California Longitudinal Pupil Achievement (CALPADS) Fall 1 data collection. |
| GSserved | grade span served. | text | It is the lowest grade and the highest grade of student enrollment as reported in the most recent certified CALPADS Fall 1 data collection. Only K-12 enrollment is reported through CALPADS. This field may differ from the grade span offered. |
| Virtual |  | text | This field identifies the type of virtual instruction offered by the school. Virtual instruction is instruction in which students and teachers are separated by time and/or location, and interaction occurs via computers and/or telecommunications technologies. |
| Magnet |  | integer | This field identifies whether a school is a magnet school and/or provides a magnet program. |
| Latitude |  | real | The angular distance (expressed in degrees) between the location of the school, district, or administrative authority and the equator measured north to south. |
| Longitude |  | real | The angular distance (expressed in degrees) between the location of the school, district, or administrative authority and the prime meridian (Greenwich, England) measured from west to east. |
| AdmFName1 | administrator's first name | text | administrator's first name |
| AdmLName1 | administrator's last name | text | administrator's last name |
| AdmEmail1 | administrator's email address | text | administrator's email address |
| AdmFName2 |  | text | - |
| AdmLName2 |  | text | - |
| AdmEmail2 |  | text | - |
| AdmFName3 |  | text | - |
| AdmLName3 |  | text | - |
| AdmEmail3 |  | text | - |
| LastUpdate |  | date | - |

## Business Logic & Value Descriptions

### StatusType

- Definitions of the valid status types are listed below:
- -       Active: The district is in operation and providing instructional services.
- -       Closed: The district is not in operation and no longer providing instructional services.
- -       Merged: The district has combined with another district or districts.
- -       Pending: The district has not opened for operation and instructional services yet, but plans to open within the next 9-12 months.

### StreetAbr (street address)

- The abbreviated street address of the school, district, or administrative authority's physical location. Note: Some records (primarily records of closed or retired schools) may not have data in this field.

### MailStreet

- The unabbreviated mailing address of the school, district, or administrative authority. Note: 1) Some entities (primarily closed or retired schools) may not have data in this field; 2) Many active entities have not provided a mailing street address. For your convenience we have filled the unpopulated MailStreet cells with Street data.

### MailStrAbr (mailing street address)

- the abbreviated mailing street address of the school, district, or administrative authority.Note: Many active entities have not provided a mailing street address. For your convenience we have filled the unpopulated MailStrAbr cells with StreetAbr data.

### MailCity (mailing city)

- The city associated with the mailing address of the school, district, or administrative authority. Note: Many entities have not provided a mailing address city. For your convenience we have filled the unpopulated MailCity cells with City data.

### MailZip (mailing zip)

- The zip code associated with the mailing address of the school, district, or administrative authority. Note: Many entities have not provided a mailing address zip code. For your convenience we have filled the unpopulated MailZip cells with Zip data.

### MailState (mailing state)

- The state within the mailing address. For your convenience we have filled the unpopulated MailState cells with State data.

### Ext (extension)

- The phone number extension of the school, district, or administrative authority.

### Website

- The website address of the school, district, or administrative authority.

### Charter

- The field is coded as follows:
- -       1 = The school is a charter
- -       0 = The school is not a charter

### CharterNum

- 4-digit number assigned to a charter school.

### FundingType

- Values are as follows:
- -       Not in CS (California School) funding model
- -       Locally funded
- -       Directly funded

### DOC (District Ownership Code)

- The District Ownership Code (DOC) is the numeric code used to identify the category of the Administrative Authority.
- -       00 - County Office of Education
- -       02 - State Board of Education
- -       03 - Statewide Benefit Charter
- -       31 - State Special Schools
- -       34 - Non-school Location*
- -       52 - Elementary School District
- -       54 - Unified School District
- -       56 - High School District
- -       98 - Regional Occupational Center/Program (ROC/P)
- commonsense evidence:
- *Only the California Education Authority has been included in the non-school location category.

### DOCType (The District Ownership Code Type)

- (See text values in DOC field description above)

### SOC (School Ownership Code)

- -      08 - Preschool
- -       09 - Special Education Schools (Public)
- -      11 - Youth Authority Facilities (CEA)
- -       13 - Opportunity Schools
- -       14 - Juvenile Court Schools
- -       15 - Other County or District Programs
- -       31 - State Special Schools
- -       60 - Elementary School (Public)
- -       61 - Elementary School in 1 School District (Public)
- -       62 - Intermediate/Middle Schools (Public)
- -       63 - Alternative Schools of Choice
- -       64 - Junior High Schools (Public)
- -       65 - K-12 Schools (Public)
- -       66 - High Schools (Public)
- -       67 - High Schools in 1 School District (Public)
- -       68 - Continuation High Schools
- -       69 - District Community Day Schools
- -       70 - Adult Education Centers
- -       98 - Regional Occupational Center/Program (ROC/P)

### SOCType (School Ownership Code Type)

- The School Ownership Code Type is the text description of the type of school.

### EdOpsCode (Education Option Code)

- -      ALTSOC - Alternative School of Choice
- -      COMM - County Community School
- -       COMMDAY - Community Day School
- -       CON - Continuation School
- -       JUV - Juvenile Court School
- -       OPP - Opportunity School
- -       YTH - Youth Authority School
- -       SSS - State Special School
- -       SPEC - Special Education School
- -       TRAD - Traditional
- -       ROP - Regional Occupational Program
- -       HOMHOS - Home and Hospital
- -       SPECON - District Consortia Special Education School

### EdOpsName (Educational Option Name)

- The Educational Option Name is the long text description of the type of education being offered.

### EILCode (Educational Instruction Level Code)

- -       A - Adult
- -       ELEM - Elementary
- -       ELEMHIGH - Elementary-High Combination
- -       HS - High School
- -       INTMIDJR - Intermediate/Middle/Junior High
- -       PS - Preschool
- -       UG - Ungraded

### EILName (Educational Instruction Level Name)

- The Educational Instruction Level Name is the long text description of the institution's type relative to the grade range served.

### GSoffered (grade span offered)

- For example XYZ School might display the following data:
- GSoffered = P-Adult
- GSserved = K-12

### GSserved (grade span served.)

- commonsense evidence:
- 1.     Only K-12 enrollment is reported through CALPADS
- 2.     Note: Special programs at independent study, alternative education, and special education schools will often exceed the typical grade span for schools of that type

### Virtual

- The field is coded as follows:
- -       F = Exclusively Virtual - The school has no physical building where students meet with each other or with teachers, all instruction is virtual.
- -       V = Primarily Virtual - The school focuses on a systematic program of virtual instruction but includes some physical meetings among students or with teachers.
- -       C = Primarily Classroom - The school offers virtual courses but virtual instruction is not the primary means of instruction.
- -       N = Not Virtual - The school does not offer any virtual instruction.
- -       P = Partial Virtual - The school offers some, but not all, instruction through virtual instruction. Note: This value was retired and replaced with the Primarily Virtual and Primarily Classroom values beginning with the 2016-17 school year.

### Magnet

- The field is coded as follows:
- -       1 = Magnet - The school is a magnet school and/or offers a magnet program.
- -       0 = Not Magnet - The school is not a magnet school and/or does not offer a magnet program.
- commonsense evidence:
- Note: Preschools and adult education centers do not contain a magnet school indicator.

### Latitude

- The angular distance (expressed in degrees) between the location of the school, district, or administrative authority and the equator measured north to south.

### Longitude

- The angular distance (expressed in degrees) between the location of the school, district, or administrative authority and the prime meridian (Greenwich, England) measured from west to east.

### AdmFName1 (administrator's first name)

- The superintendent's or principal's first name.
- commonsense evidence:
- Only active and pending districts and schools will display administrator information, if applicable.

### AdmLName1 (administrator's last name)

- The superintendent's or principal's last name.
- commonsense evidence:
- Only active and pending districts and schools will display administrator information, if applicable.

### AdmEmail1 (administrator's email address)

- The superintendent's or principal's email address.
- commonsense evidence:
- Only active and pending districts and schools will display administrator information, if applicable.

### AdmFName2

- SAME as 1

### LastUpdate

- when is this record updated last time

## Relationships

- schools.CDSCode → frpm.CDSCode (학교별 무상/감면 급식 데이터)
- schools.CDSCode → satscores.cds (학교별 SAT 점수)

**중요 조인 키**: 세 테이블 모두 CDSCode(또는 cds)로 연결됩니다.
- schools.CDSCode = frpm.CDSCode = satscores.cds

**DOC vs SOC 구분**:
- DOC (District Ownership Code): 학군 유형 (31 = State Special Schools)
- SOC (School Ownership Code): 학교 유형
- "State Special Schools"를 조회할 때는 DOC = 31 사용

## Sample Query

### State Special Schools 중 K-12 등록자 수가 가장 많은 학교
```sql
SELECT sc.School
FROM frpm f
INNER JOIN schools sc ON f.CDSCode = sc.CDSCode
WHERE sc.DOC = 31
ORDER BY f."Enrollment (K-12)" DESC
LIMIT 1
```

### Riverside 지역 학교 중 SAT 수학 평균 400 초과인 학교와 펀딩 타입
```sql
SELECT s.sname, f."Charter Funding Type"
FROM satscores s
INNER JOIN frpm f ON s.cds = f.CDSCode
WHERE f."District Name" LIKE 'Riverside%'
GROUP BY s.sname, f."Charter Funding Type"
HAVING CAST(SUM(s.AvgScrMath) AS DOUBLE) / COUNT(s.cds) > 400
```
