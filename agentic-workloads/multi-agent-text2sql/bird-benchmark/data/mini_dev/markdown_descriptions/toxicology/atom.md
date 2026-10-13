# Table: atom

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| atom_id | atom id | text | the unique id of atoms |
| molecule_id | molecule id | text | identifying the molecule to which the atom belongs |
| element |  | text | the element of the toxicology |

## Business Logic & Value Descriptions

### molecule_id (molecule id)

- commonsense evidence:
- TRXXX_i represents ith atom of molecule TRXXX

### element

-  cl: chlorine
-  c: carbon
-  h: hydrogen
-  o: oxygen
-  s: sulfur
-  n: nitrogen
-  p: phosphorus
-  na: sodium
-  br: bromine
-  f: fluorine
-  i: iodine
-  sn: Tin
-  pb: lead
-  te: tellurium
-  ca: Calcium
