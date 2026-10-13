# Table: bond

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| bond_id |  | text | unique id representing bonds |
| molecule_id |  | text | identifying the molecule in which the bond appears |
| bond_type |  | text | type of the bond |

## Business Logic & Value Descriptions

### bond_id

- TRxxx_A1_A2:
- TRXXX refers to which molecule
- A1 and A2 refers to which atom

### bond_type

- commonsense evidence:
- -: single bond
- '=': double bond
- '#': triple bond
