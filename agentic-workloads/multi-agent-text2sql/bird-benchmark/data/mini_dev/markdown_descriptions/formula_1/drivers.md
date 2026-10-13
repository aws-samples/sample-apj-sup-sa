# Table: drivers

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| driverId | driver ID | integer | the unique identification number identifying each driver |
| driverRef | driver reference name | text | driver reference name |
| number |  | integer | number |
| code |  | text | abbreviated code for drivers |
| forename |  | text | forename |
| surname |  | text | surname |
| dob | date of birth | date | date of birth |
| nationality |  | text | nationality of drivers |
| url |  | text | the introduction website of the drivers |

## Business Logic & Value Descriptions

### code

- if "null" or empty, it means it doesn't have code
