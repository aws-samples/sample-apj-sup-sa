# Table: posts

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| Id |  | integer | the post id |
| PostTypeId | Post Type Id | integer | the id of the post type |
| AcceptedAnswerId | Accepted Answer Id | integer | the accepted answer id of the post |
| CreaionDate | Creation Date | datetime | the creation date of the post |
| Score |  | integer | the score of the post |
| ViewCount | View Count | integer | the view count of the post |
| Body |  | text | the body of the post |
| OwnerUserId | Owner User Id | integer | the id of the owner user |
| LasActivityDate | Last Activity Date | datetime | the last activity date |
| Title |  | text | the title of the post |
| Tags |  | text | the tag of the post |
| AnswerCount | Answer Count | integer | the total number of answers of the post |
| CommentCount | Comment Count | integer | the total number of comments of the post |
| FavoriteCount | Favorite Count | integer | the total number of favorites of the post |
| LastEditorUserId | Last Editor User Id | integer | the id of the last editor |
| LastEditDate | Last Edit Date | datetime | the last edit date |
| CommunityOwnedDate | Community Owned Date | datetime | the community owned date |
| ParentId |  | integer | the id of the parent post |
| ClosedDate | Closed Date | data_format | the closed date of the post |
| OwnerDisplayName | Owner Display Name | text | the display name of the post owner |
| LastEditorDisplayName | Last Editor Display Name | text | the display name of the last editor |

## Business Logic & Value Descriptions

### ViewCount (View Count)

- commonsense evidence:
- Higher view count means the post has higher popularity

### FavoriteCount (Favorite Count)

- commonsense evidence:
- more favorite count refers to more valuable posts.

### ParentId

- commonsense evidence:
- If the parent id is null, the post is the root post. Otherwise, the post is the child post of other post.

### ClosedDate (Closed Date)

- commonsense evidence:
- if ClosedDate is null or empty, it means this post is not well-finished
- if CloseDate is not null or empty, it means this post has well-finished.
