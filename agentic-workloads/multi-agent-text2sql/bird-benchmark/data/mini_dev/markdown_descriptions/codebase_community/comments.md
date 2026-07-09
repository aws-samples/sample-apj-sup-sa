# Table: comments

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| Id |  | integer | the comment Id |
| PostId | Post Id | integer | the unique id of the post |
| Score |  | integer | rating score |
| Text |  | text | the detailed content of the comment |
| CreationDate | Creation Date | datetime | the creation date of the comment |
| UserId | User Id | integer | the id of the user who post the comment |
| UserDisplayName | User Display Name | text | user's display name |

## Business Logic & Value Descriptions

### Score

- commonsense evidence:
- The score is from 0 to 100. The score more than 60 refers that the comment is a positive comment. The score less than 60 refers that the comment is a negative comment.
