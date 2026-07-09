# Table: Zip_Code

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| zip_code | zip code | integer | The ZIP code itself. A five-digit number identifying a US post office. |
| type |  | text | The kind of ZIP code |
| city |  | text | The city to which the ZIP pertains |
| county |  | text | The county to which the ZIP pertains |
| state |  | text | The name of the state to which the ZIP pertains |
| short_state | short state | text | The abbreviation of the state to which the ZIP pertains |

## Business Logic & Value Descriptions

### type

- commonsense evidence:
- � Standard: the normal codes with which most people are familiar
- � PO Box: zip codes have post office boxes
- � Unique: zip codes that are assigned to individual organizations.
