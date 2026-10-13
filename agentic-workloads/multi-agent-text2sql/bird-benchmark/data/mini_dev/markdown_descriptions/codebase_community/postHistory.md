# Table: postHistory

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| Id |  | integer | the post history id |
| PostHistoryTypeId | Post History Type Id | integer | the id of the post history type |
| PostId | Post Id | integer | the unique id of the post |
| RevisionGUID | Revision GUID | integer | the revision globally unique id of the post |
| CreationDate | Creation Date | datetime | the creation date of the post |
| UserId | User Id | integer | the user who post the post |
| Text |  | text | the detailed content of the post |
| Comment |  | text | comments of the post |
| UserDisplayName | User Display Name | text | user's display name |