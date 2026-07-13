# Table: tags

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| Id |  | integer | the tag id |
| TagName | Tag Name | text | the name of the tag |
| Count |  | integer | the count of posts that contain this tag |
| ExcerptPostId | Excerpt Post Id | integer | the excerpt post id of the tag |
| WikiPostId | Wiki Post Id | integer | the wiki post id of the tag |

## Business Logic & Value Descriptions

### Count

- more counts --> this tag is more popular
