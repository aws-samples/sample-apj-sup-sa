# Table: Member

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| member_id | member id | text | unique id of member |
| first_name | first name | text | member's first name |
| last_name | last name | text | member's last name |
| email |  | text | member's email |
| position |  | text | The position the member holds in the club |
| t_shirt_size |  | text | The size of tee shirt that member wants when shirts are ordered |
| phone |  | text | The best telephone at which to contact the member |
| zip |  | integer | the zip code of the member's hometown |
| link_to_major | link to major | text | The unique identifier of the major of the member. References the Major table |

## Business Logic & Value Descriptions

### last_name (last name)

- commonsense evidence:
- full name is first_name + last_name. e.g. A member's first name is Angela and last name is Sanders. Thus, his/her full name is Angela Sanders.

### t_shirt_size

- commonsense evidence: usually the student ordered t-shirt with lager size has bigger body shape
