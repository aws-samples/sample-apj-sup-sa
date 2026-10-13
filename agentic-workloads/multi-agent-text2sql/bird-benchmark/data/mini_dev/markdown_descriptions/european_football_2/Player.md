# Table: Player

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| id |  | integer | the unique id for players |
| player_api_id | player api id | integer | the id of the player api |
| player_name | player name | text | player name |
| player_fifa_api_id | player federation international football association api id | integer | the id of the player fifa api |
| birthday |  | text | the player's birthday |
| height |  | integer | the player's height |
| weight |  | integer | the player's weight |

## Business Logic & Value Descriptions

### birthday

- e.g. 1992-02-29 00:00:00
- commonsense reasoning:
- Player A is older than player B means that A's birthday is earlier than B's
