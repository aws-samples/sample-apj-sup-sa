# Table: users

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| Id |  | integer | the user id |
| Reputation |  | integer | the user's reputation |
| CreationDate | Creation Date | datetime | the creation date of the user account |
| DisplayName | Display Name | text | the user's display name |
| LastAccessDate | Last Access Date | datetime | the last access date of the user account |
| WebsiteUrl | Website Url | text | the website url of the user account |
| Location |  | text | user's location |
| AboutMe | About Me | text | the self introduction of the user |
| Views |  | integer | the number of views |
| UpVotes |  | integer | the number of upvotes |
| DownVotes |  | integer | the number of downvotes |
| AccountId | Account Id | integer | the unique id of the account |
| Age |  | integer | user's age |
| ProfileImageUrl | Profile Image Url | text | the profile image url |

## Business Logic & Value Descriptions

### Reputation

- commonsense evidence:
- The user with higher reputation has more influence.

### Age

-  teenager: 13-18
-  adult: 19-65
-  elder: > 65
