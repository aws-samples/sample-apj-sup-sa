# Table: circuits

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| circuitId | circuit Id | integer | unique identification number of the circuit |
| circuitRef | circuit reference name | text | circuit reference name |
| name |  | text | full name of circuit |
| location |  | text | location of circuit |
| country |  | text | country of circuit |
| lat | latitude | real | latitude of location of circuit |
| lng | longitude | real | longitude of location of circuit |
| alt |  | integer | - |
| url |  | text | url |

## Business Logic & Value Descriptions

### lng (longitude)

- commonsense evidence:
- Location coordinates: (lat, lng)
