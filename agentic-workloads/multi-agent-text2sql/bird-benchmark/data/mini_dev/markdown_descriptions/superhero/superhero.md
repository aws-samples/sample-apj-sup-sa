# Table: superhero

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| id |  | integer | the unique identifier of the superhero |
| superhero_name | superhero name | text | the name of the superhero |
| full_name | full name | text | the full name of the superhero |
| gender_id | gender id | integer | the id of the superhero's gender |
| eye_colour_id | eye colour id | integer | the id of the superhero's eye color |
| hair_colour_id | hair colour id | integer | the id of the superhero's hair color |
| skin_colour_id | skin colour id | integer | the id of the superhero's skin color |
| race_id | race id | integer | the id of the superhero's race |
| publisher_id | publisher id | integer | the id of the publisher |
| alignment_id | alignment id | integer | the id of the superhero's alignment |
| height_cm | height cm | integer | the height of the superhero |
| weight_kg | weight kg | integer | the weight of the superhero |

## Business Logic & Value Descriptions

### full_name (full name)

- commonsense evidence:
- The full name of a person typically consists of their given name, also known as their first name or personal name, and their surname, also known as their last name or family name. For example, if someone's given name is "John" and their surname is "Smith," their full name would be "John Smith."

### height_cm (height cm)

- commonsense evidence:
- The unit of height is centimeter. If the height_cm is NULL or 0, it means the height of the superhero is missing.

### weight_kg (weight kg)

- commonsense evidence:
- The unit of weight is kilogram. If the weight_kg is NULL or 0, it means the weight of the superhero is missing.

## Relationships

- superhero.gender_id → gender.id (값: 'Male', 'Female', 'N/A')
- superhero.alignment_id → alignment.id (값: 'Good', 'Bad', 'Neutral')
- superhero.eye_colour_id → colour.id
- superhero.hair_colour_id → colour.id
- superhero.skin_colour_id → colour.id
- superhero.race_id → race.id
- superhero.publisher_id → publisher.id
- hero_attribute.hero_id → superhero.id (능력치: Strength, Speed, Intelligence 등)
- hero_power.hero_id → superhero.id (초능력: hero_power → superpower 테이블)

**중요**: gender, alignment, colour 등은 lookup 테이블이므로 반드시 JOIN하여 텍스트 값을 가져와야 합니다. superhero 테이블의 _id 컬럼에는 숫자 ID만 저장되어 있고, 실제 텍스트 값(예: 'Female', 'Neutral', 'Blue')은 각 lookup 테이블에 있습니다.

## Sample Query

### neutral alignment인 히어로 이름 조회
```sql
SELECT s.superhero_name
FROM superhero s
INNER JOIN alignment a ON s.alignment_id = a.id
WHERE a.alignment = 'Neutral'
```

### 파란 눈에 갈색 머리인 히어로 조회 (colour 테이블 2번 JOIN)
```sql
SELECT s.superhero_name
FROM superhero s
INNER JOIN colour c1 ON s.eye_colour_id = c1.id
INNER JOIN colour c2 ON s.hair_colour_id = c2.id
WHERE c1.colour = 'Blue' AND c2.colour = 'Brown'
```

### 여성 히어로 중 Strength 100인 수
```sql
SELECT COUNT(s.id)
FROM superhero s
INNER JOIN hero_attribute ha ON s.id = ha.hero_id
INNER JOIN attribute a ON ha.attribute_id = a.id
INNER JOIN gender g ON s.gender_id = g.id
WHERE g.gender = 'Female' AND a.attribute_name = 'Strength' AND ha.attribute_value = 100
```

### Phoenix Force 능력을 가진 히어로의 성별 (hero_power 사용)
```sql
SELECT g.gender
FROM superhero s
INNER JOIN hero_power hp ON s.id = hp.hero_id
INNER JOIN superpower sp ON hp.power_id = sp.id
INNER JOIN gender g ON s.gender_id = g.id
WHERE sp.power_name = 'Phoenix Force'
```
