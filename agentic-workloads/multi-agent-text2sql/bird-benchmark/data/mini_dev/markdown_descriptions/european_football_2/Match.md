# Table: Match

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| id |  | integer | the unique id for matches |
| country_id | country id | integer | country id |
| league_id | league id | integer | league id |
| season |  | text | the season of the match |
| stage |  | integer | the stage of the match |
| date |  | text | the date of the match |
| match_api_id | match api id | integer | the id of the match api |
| home_team_api_id | home team api id | integer | the id of the home team api |
| away_team_api_id | away team api id | integer | the id of the away team api |
| home_team_goal | home team goal | integer | the goal of the home team |
| away_team_goal | away team goal | integer | the goal of the away team |
| home_player_X1 |  | - | - |
| home_player_X2 |  | - | - |
| home_player_X3 |  | - | - |
| home_player_X4 |  | - | - |
| home_player_X5 |  | - | - |
| home_player_X6 |  | - | - |
| home_player_X7 |  | - | - |
| home_player_X8 |  | - | - |
| home_player_X9 |  | - | - |
| home_player_X10 |  | - | - |
| home_player_X11 |  | - | - |
| away_player_X1 |  | - | - |
| away_player_X2 |  | - | - |
| away_player_X3 |  | - | - |
| away_player_X4 |  | - | - |
| away_player_X5 |  | - | - |
| away_player_X6 |  | - | - |
| away_player_X7 |  | - | - |
| away_player_X8 |  | - | - |
| away_player_X9 |  | - | - |
| away_player_X10 |  | - | - |
| away_player_X11 |  | - | - |
| home_player_Y1 |  | - | - |
| home_player_Y2 |  | - | - |
| home_player_Y3 |  | - | - |
| home_player_Y4 |  | - | - |
| home_player_Y5 |  | - | - |
| home_player_Y6 |  | - | - |
| home_player_Y7 |  | - | - |
| home_player_Y8 |  | - | - |
| home_player_Y9 |  | - | - |
| home_player_Y10 |  | - | - |
| home_player_Y11 |  | - | - |
| away_player_Y1 |  | - | - |
| away_player_Y2 |  | - | - |
| away_player_Y3 |  | - | - |
| away_player_Y4 |  | - | - |
| away_player_Y5 |  | - | - |
| away_player_Y6 |  | - | - |
| away_player_Y7 |  | - | - |
| away_player_Y8 |  | - | - |
| away_player_Y9 |  | - | - |
| away_player_Y10 |  | - | - |
| away_player_Y11 |  | - | - |
| home_player_1 |  | - | - |
| home_player_2 |  | - | - |
| home_player_3 |  | - | - |
| home_player_4 |  | - | - |
| home_player_5 |  | - | - |
| home_player_6 |  | - | - |
| home_player_7 |  | - | - |
| home_player_8 |  | - | - |
| home_player_9 |  | - | - |
| home_player_10 |  | - | - |
| home_player_11 |  | - | - |
| away_player_1 |  | - | - |
| away_player_2 |  | - | - |
| away_player_3 |  | - | - |
| away_player_4 |  | - | - |
| away_player_5 |  | - | - |
| away_player_6 |  | - | - |
| away_player_7 |  | - | - |
| away_player_8 |  | - | - |
| away_player_9 |  | - | - |
| away_player_10 |  | - | - |
| away_player_11 |  | - | - |
| goal |  | text | the goal of the match |
| shoton | shot on | text | the shot on goal of the match |
| shotoff | shot off | text | the shot off goal of the match, which is the opposite of shot on |
| foulcommit | foul commit | text | the fouls occurred in the match |
| card |  | text | the cards given in the match |
| cross |  | text | Balls sent into the opposition team's area from a wide position in the match |
| corner |  | text | Ball goes out of play for a corner kick in the match |
| possession |  | text | The duration from a player taking over the ball in the match |
| B365H |  | - | - |
| B365D |  | - | - |
| B365A |  | - | - |
| BWH |  | - | - |
| BWD |  | - | - |
| BWA |  | - | - |
| IWH |  | - | - |
| IWD |  | - | - |
| IWA |  | - | - |
| LBH |  | - | - |
| LBD |  | - | - |
| LBA |  | - | - |
| PSH |  | - | - |
| PSD |  | - | - |
| PSA |  | - | - |
| WHH |  | - | - |
| WHD |  | - | - |
| WHA |  | - | - |
| SJH |  | - | - |
| SJD |  | - | - |
| SJA |  | - | - |
| VCH |  | - | - |
| VCD |  | - | - |
| VCA |  | - | - |
| GBH |  | - | - |
| GBD |  | - | - |
| GBA |  | - | - |
| BSH |  | - | - |
| BSD |  | - | - |
| BSA |  | - | - |

## Business Logic & Value Descriptions

### date

- e.g. 2008-08-17 00:00:00

### shoton (shot on)

- commonsense reasoning:
- A shot on goal is a shot that enters the goal or would have entered the goal if it had not been blocked by the goalkeeper or another defensive player.
