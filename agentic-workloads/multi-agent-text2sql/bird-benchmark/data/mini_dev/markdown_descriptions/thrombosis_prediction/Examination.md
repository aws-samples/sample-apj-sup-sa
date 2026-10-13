# Table: Examination

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| ID |  | integer | identification of the patient |
| Examination Date |  | date | Examination Date |
| aCL IgG | anti-Cardiolipin antibody (IgG) | real | anti-Cardiolipin antibody (IgG) concentration |
| aCL IgM | anti-Cardiolipin antibody (IgM) | real | anti-Cardiolipin antibody (IgM) concentration |
| ANA | anti-nucleus antibody | integer | anti-nucleus antibody concentration |
| ANA Pattern | pattern observed in the sheet of ANA examination | text | pattern observed in the sheet of ANA examination |
| aCL IgA | anti-Cardiolipin antibody (IgA) concentration | integer | anti-Cardiolipin antibody (IgA) concentration |
| Diagnosis |  | text | disease names |
| KCT | measure of degree of coagulation | text | measure of degree of coagulation |
| RVVT | measure of degree of coagulation | text | measure of degree of coagulation |
| LAC | measure of degree of coagulation | text | measure of degree of coagulation |
| Symptoms |  | text | other symptoms observed |
| Thrombosis |  | integer | degree of thrombosis |

## Business Logic & Value Descriptions

### KCT (measure of degree of coagulation)

- +: positive
- -: negative

### RVVT (measure of degree of coagulation)

- +: positive
- -: negative

### LAC (measure of degree of coagulation)

- +: positive
- -: negative

### Thrombosis

- 0: negative (no thrombosis)
- 1: positive (the most severe one)
- 2: positive (severe)3: positive (mild)
