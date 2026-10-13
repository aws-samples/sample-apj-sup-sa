# Table: card

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| card_id | credit card id | integer | id number of credit card |
| disp_id | disposition id | integer | disposition id |
| type |  | text | type of credit card |
| issued |  | date | the date when the credit card issued |

## Business Logic & Value Descriptions

### type

- "junior": junior class of credit card;
- "classic": standard class of credit card;
- "gold": high-level credit card

### issued

- in the form YYMMDD
