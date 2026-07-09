# Table: Laboratory

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| ID |  | integer | identification of the patient |
| Date |  | date | Date of the laboratory tests (YYMMDD) |
| GOT | AST glutamic oxaloacetic transaminase | integer | AST glutamic oxaloacetic transaminase |
| GPT | ALT glutamic pyruvic transaminase | integer | ALT glutamic pyruvic transaminase |
| LDH | lactate dehydrogenase | integer | lactate dehydrogenase |
| ALP | alkaliphophatase | integer | alkaliphophatase |
| TP | total protein | real | total protein |
| ALB | albumin | real | albumin |
| UA | uric acid | real | uric acid |
| UN | urea nitrogen | integer | urea nitrogen |
| CRE | creatinine | real | creatinine |
| T-BIL | total bilirubin | real | total bilirubin |
| T-CHO | total cholesterol | integer | total cholesterol |
| TG | triglyceride | integer | triglyceride |
| CPK | creatinine phosphokinase | integer | creatinine phosphokinase |
| GLU | blood glucose | integer | blood glucose |
| WBC | White blood cell | real | White blood cell |
| RBC | Red blood cell | real | Red blood cell |
| HGB | Hemoglobin | real | Hemoglobin |
| HCT | Hematoclit | real | Hematoclit |
| PLT | platelet | integer | platelet |
| PT | prothrombin time | real | prothrombin time |
| APTT | activated partial prothrombin time | integer | activated partial prothrombin time |
| FG | fibrinogen | real | fibrinogen |
| PIC |  | - | - |
| TAT |  | - | - |
| TAT2 |  | - | - |
| U-PRO | proteinuria | text | proteinuria |
| IGG | Ig G | integer | Ig G |
| IGA | Ig A | integer | Ig A |
| IGM | Ig M | integer | Ig M |
| CRP | C-reactive protein | text | C-reactive protein |
| RA | Rhuematoid Factor | text | Rhuematoid Factor |
| RF | RAHA | text | RAHA |
| C3 | complement 3 | integer | complement 3 |
| C4 | complement 4 | integer | complement 4 |
| RNP | anti-ribonuclear protein | text | anti-ribonuclear protein |
| SM | anti-SM | text | anti-SM |
| SC170 | anti-scl70 | text | anti-scl70 |
| SSA | anti-SSA | text | anti-SSA |
| SSB | anti-SSB | text | anti-SSB |
| CENTROMEA | anti-centromere | text | anti-centromere |
| DNA | anti-DNA | text | anti-DNA |
| DNA-II | anti-DNA | integer | anti-DNA |

## Business Logic & Value Descriptions

### GOT (AST glutamic oxaloacetic transaminase)

- Commonsense evidence:
- Normal range: N < 60

### GPT (ALT glutamic pyruvic transaminase)

- Commonsense evidence:
- Normal range: N < 60

### LDH (lactate dehydrogenase)

- Commonsense evidence:
- Normal range: N < 500

### ALP (alkaliphophatase)

- Commonsense evidence:
- Normal range: N < 300

### TP (total protein)

- Commonsense evidence:
- Normal range: 6.0 < N < 8.5

### ALB (albumin)

- Commonsense evidence:
- Normal range: 3.5 < N < 5.5

### UA (uric acid)

- Commonsense evidence:
- Normal range: N > 8.0 (Male)N > 6.5 (Female)

### UN (urea nitrogen)

- Commonsense evidence:
- Normal range: N < 30

### CRE (creatinine)

- Commonsense evidence:
- Normal range: N < 1.5

### T-BIL (total bilirubin)

- Commonsense evidence:
- Normal range: N < 2.0

### T-CHO (total cholesterol)

- Commonsense evidence:
- Normal range: N < 250

### TG (triglyceride)

- Commonsense evidence:
- Normal range: N < 200

### CPK (creatinine phosphokinase)

- Commonsense evidence:
- Normal range: N < 250

### GLU (blood glucose)

- Commonsense evidence:
- Normal range: N < 180

### WBC (White blood cell)

- Commonsense evidence:
- Normal range: 3.5 < N < 9.0

### RBC (Red blood cell)

- Commonsense evidence:
- Normal range: 3.5 < N < 6.0

### HGB (Hemoglobin)

- Commonsense evidence:
- Normal range: 10 < N < 17

### HCT (Hematoclit)

- Commonsense evidence:
- Normal range: 29 < N < 52

### PLT (platelet)

- Commonsense evidence:
- Normal range: 100 < N < 400

### PT (prothrombin time)

- Commonsense evidence:
- Normal range: N < 14

### APTT (activated partial prothrombin time)

- Commonsense evidence:
- Normal range: N < 45

### FG (fibrinogen)

- Commonsense evidence:
- Normal range: 150 < N < 450

### U-PRO (proteinuria)

- Commonsense evidence:
- Normal range: 0 < N < 30

### IGG (Ig G)

- Commonsense evidence:
- Normal range: 900 < N < 2000

### IGA (Ig A)

- Commonsense evidence:
- Normal range: 80 < N < 500

### IGM (Ig M)

- Commonsense evidence:
- Normal range: 40 < N < 400

### CRP (C-reactive protein)

- Commonsense evidence:
- Normal range: N= -, +-, or N < 1.0

### RA (Rhuematoid Factor)

- Commonsense evidence:
- Normal range: N= -, +-

### RF (RAHA)

- Commonsense evidence:
- Normal range: N < 20

### C3 (complement 3)

- Commonsense evidence:
- Normal range: N > 35

### C4 (complement 4)

- Commonsense evidence:
- Normal range: N > 10

### RNP (anti-ribonuclear protein)

- Commonsense evidence:
- Normal range: N= -, +-

### SM (anti-SM)

- Commonsense evidence:
- Normal range: N= -, +-

### SC170 (anti-scl70)

- Commonsense evidence:
- Normal range: N= -, +-

### SSA (anti-SSA)

- Commonsense evidence:
- Normal range: N= -, +-

### SSB (anti-SSB)

- Commonsense evidence:
- Normal range: N= -, +-

### CENTROMEA (anti-centromere)

- Commonsense evidence:
- Normal range: N= -, +-

### DNA (anti-DNA)

- Commonsense evidence:
- Normal range: N < 8

### DNA-II (anti-DNA)

- Commonsense evidence:
- Normal range: N < 8


## Sample Query

### Q: What is the ratio of male to female patients among all those with abnormal uric acid counts?
- 패턴: "ratio of A to B" → SUM(CASE A) / SUM(CASE B) 단일 값 반환
- 핵심: 비정상 요산 기준이 성별마다 다름 (M: UA <= 8.0, F: UA <= 6.5)
```sql
SELECT CAST(SUM(CASE WHEN l.ua <= 8.0 AND p.sex = 'M' THEN 1 ELSE 0 END) AS DOUBLE)
     / SUM(CASE WHEN l.ua <= 6.5 AND p.sex = 'F' THEN 1 ELSE 0 END)
FROM patient p
INNER JOIN laboratory l ON p.id = l.id
- 주의: "ratio" 질문은 GROUP BY가 아니라 CASE WHEN 조건부 집계로 단일 숫자를 반환
