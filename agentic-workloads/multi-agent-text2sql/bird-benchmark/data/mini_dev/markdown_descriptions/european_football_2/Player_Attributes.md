# Table: Player_Attributes

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| id |  | integer | the unique id for players |
| player_fifa_api_id | player federation international football association api id | integer | the id of the player fifa api |
| player_api_id | player api id | integer | the id of the player api |
| date |  | text | date |
| overall_rating |  | integer | the overall rating of the player |
| potential |  | integer | potential of the player |
| preferred_foot | preferred foot | text | the player's preferred foot when attacking |
| attacking_work_rate | attacking work rate | text | the player's attacking work rate |
| defensive_work_rate |  | text | the player's defensive work rate |
| crossing |  | integer | the player's crossing score |
| finishing |  | integer | the player's finishing rate |
| heading_accuracy | heading accuracy | integer | the player's heading accuracy |
| short_passing | short passing | integer | the player's short passing score |
| volleys |  | integer | the player's volley score |
| dribbling |  | integer | the player's dribbling score |
| curve |  | integer | the player's curve score |
| free_kick_accuracy | free kick accuracy | integer | the player's free kick accuracy |
| long_passing | long passing | integer | the player's long passing score |
| ball_control | ball control | integer | the player's ball control score |
| acceleration |  | integer | the player's acceleration score |
| sprint_speed | sprint speed | integer | the player's sprint speed |
| agility |  | integer | the player's agility |
| reactions |  | integer | the player's reactions score |
| balance |  | integer | the player's balance score |
| shot_power | shot power | integer | the player's shot power |
| jumping |  | integer | the player's jumping score |
| stamina |  | integer | the player's stamina score |
| strength |  | integer | the player's strength score |
| long_shots | long shots | integer | the player's long shots score |
| aggression |  | integer | the player's aggression score |
| interceptions |  | integer | the player's interceptions score |
| positioning |  | integer | the player's 
positioning score |
| vision |  | integer | the player's vision score |
| penalties |  | integer | the player's penalties score |
| marking |  | integer | the player's markingscore |
| standing_tackle | standing tackle | integer | the player's standing tackle score |
| sliding_tackle | sliding tackle | integer | the player's sliding tackle score |
| gk_diving | goalkeep diving | integer | the player's goalkeep diving score |
| gk_handling | goalkeep handling | integer | the player's goalkeep diving score |
| gk_kicking | goalkeep kicking | integer | the player's goalkeep kicking score |
| gk_positioning | goalkeep positioning | integer | the player's goalkeep positioning score |
| gk_reflexes | goalkeep reflexes | integer | the player's goalkeep reflexes score |

## Business Logic & Value Descriptions

### date

- e.g. 2016-02-18 00:00:00

### overall_rating

- commonsense reasoning:
- The rating is between 0-100 which is calculated by FIFA.
- Higher overall rating means the player has a stronger overall strength.

### potential

- commonsense reasoning:
- The potential score is between 0-100 which is calculated by FIFA.
- Higher potential score means that the player has more potential

### preferred_foot (preferred foot)

- right/ left

### attacking_work_rate (attacking work rate)

- commonsense reasoning:
- - high: implies that the player is going to be in all of your attack moves
- - medium: implies that the player will select the attack actions he will join in
- - low: remain in his position while the team attacks

### defensive_work_rate

- commonsense reasoning:
- - high: remain in his position and defense while the team attacks
- - medium: implies that the player will select the defensive actions he will join in
- - low: implies that the player is going to be in all of your attack moves instead of defensing

### crossing

- commonsense reasoning:
- Cross is a long pass into the opponent's goal towards the header of sixth-yard teammate.
- The crossing score is between 0-100 which measures the tendency/frequency of crosses in the box.
- Higher potential score means that the player performs better in crossing actions.

### finishing

- 0-100 which is calculated by FIFA

### heading_accuracy (heading accuracy)

- 0-100 which is calculated by FIFA

### short_passing (short passing)

- 0-100 which is calculated by FIFA

### volleys

- 0-100 which is calculated by FIFA

### dribbling

- 0-100 which is calculated by FIFA

### curve

- 0-100 which is calculated by FIFA

### free_kick_accuracy (free kick accuracy)

- 0-100 which is calculated by FIFA

### long_passing (long passing)

- 0-100 which is calculated by FIFA

### ball_control (ball control)

- 0-100 which is calculated by FIFA

### acceleration

- 0-100 which is calculated by FIFA

### sprint_speed (sprint speed)

- 0-100 which is calculated by FIFA

### agility

- 0-100 which is calculated by FIFA

### reactions

- 0-100 which is calculated by FIFA

### balance

- 0-100 which is calculated by FIFA

### shot_power (shot power)

- 0-100 which is calculated by FIFA

### jumping

- 0-100 which is calculated by FIFA

### stamina

- 0-100 which is calculated by FIFA

### strength

- 0-100 which is calculated by FIFA

### long_shots (long shots)

- 0-100 which is calculated by FIFA

### aggression

- 0-100 which is calculated by FIFA

### interceptions

- 0-100 which is calculated by FIFA

### positioning

- 0-100 which is calculated by FIFA

### vision

- 0-100 which is calculated by FIFA

### penalties

- 0-100 which is calculated by FIFA

### marking

- 0-100 which is calculated by FIFA

### standing_tackle (standing tackle)

- 0-100 which is calculated by FIFA

### sliding_tackle (sliding tackle)

- 0-100 which is calculated by FIFA

### gk_diving (goalkeep diving)

- 0-100 which is calculated by FIFA

### gk_handling (goalkeep handling)

- 0-100 which is calculated by FIFA

### gk_kicking (goalkeep kicking)

- 0-100 which is calculated by FIFA

### gk_positioning (goalkeep positioning)

- 0-100 which is calculated by FIFA

### gk_reflexes (goalkeep reflexes)

- 0-100 which is calculated by FIFA
