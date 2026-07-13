# Table: Team_Attributes

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| id |  | integer | the unique id for teams |
| team_fifa_api_id | team federation international football association api id | integer | the id of the team fifa api |
| team_api_id | team api id | integer | the id of the team api |
| date |  | text | Date |
| buildUpPlaySpeed | build Up Play Speed | integer | the speed in which attacks are put together |
| buildUpPlaySpeedClass | build Up Play Speed Class | text | the speed class |
| buildUpPlayDribbling | build Up Play Dribbling | integer | the tendency/ frequency of dribbling |
| buildUpPlayDribblingClass | build Up Play Dribbling Class | text | the dribbling class |
| buildUpPlayPassing | build Up Play Passing | integer | affects passing distance and support from teammates |
| buildUpPlayPassingClass | build Up Play Passing Class | text | the passing class |
| buildUpPlayPositioningClass | build Up Play Positioning Class | text | A team's freedom of movement in the 1st two thirds of the pitch |
| chanceCreationPassing | chance Creation Passing | integer | Amount of risk in pass decision and run support |
| chanceCreationPassingClass | chance Creation Passing Class | text | the chance creation passing class |
| chanceCreationCrossing | chance Creation Crossing | integer | The tendency / frequency of crosses into the box |
| chanceCreationCrossingClass | chance Creation Crossing Class | text | the chance creation crossing class |
| chanceCreationShooting | chance Creation Shooting | integer | The tendency / frequency of shots taken |
| chanceCreationShootingClass | chance Creation Shooting Class | text | the chance creation shooting class |
| chanceCreationPositioningClass | chance Creation Positioning Class | text | A team's freedom of movement in the final third of the pitch |
| defencePressure | defence Pressure | integer | Affects how high up the pitch the team will start pressuring |
| defencePressureClass | defence Pressure Class | text | the defence pressure class |
| defenceAggression | defence Aggression | integer | Affect the team's approach to tackling the ball possessor |
| defenceAggressionClass | defence Aggression Class | text | the defence aggression class |
| defenceTeamWidth | defence Team Width | integer | Affects how much the team will shift to the ball side |
| defenceTeamWidthClass | defence Team Width Class | text | the defence team width class |
| defenceDefenderLineClass | defence Defender Line Class | text | Affects the shape and strategy of the defence |

## Business Logic & Value Descriptions

### date

- e.g. 2010-02-22 00:00:00

### buildUpPlaySpeed (build Up Play Speed)

- the score which is between 1-00 to measure the team's attack speed

### buildUpPlaySpeedClass (build Up Play Speed Class)

- commonsense reasoning:
- - Slow: 1-33
- - Balanced: 34-66
- - Fast: 66-100

### buildUpPlayDribblingClass (build Up Play Dribbling Class)

- commonsense reasoning:
- - Little: 1-33
- - Normal: 34-66
- - Lots: 66-100

### buildUpPlayPassingClass (build Up Play Passing Class)

- commonsense reasoning:
- - Short: 1-33
- - Mixed: 34-66
- - Long: 66-100

### buildUpPlayPositioningClass (build Up Play Positioning Class)

- Organised / Free Form

### chanceCreationPassingClass (chance Creation Passing Class)

- commonsense reasoning:
- - Safe: 1-33
- - Normal: 34-66
- - Risky: 66-100

### chanceCreationCrossingClass (chance Creation Crossing Class)

- commonsense reasoning:
- - Little: 1-33
- - Normal: 34-66
- - Lots: 66-100

### chanceCreationShootingClass (chance Creation Shooting Class)

- commonsense reasoning:
- - Little: 1-33
- - Normal: 34-66
- - Lots: 66-100

### chanceCreationPositioningClass (chance Creation Positioning Class)

- Organised / Free Form

### defencePressureClass (defence Pressure Class)

- commonsense reasoning:
- - Deep: 1-33
- - Medium: 34-66
- - High: 66-100

### defenceAggressionClass (defence Aggression Class)

- commonsense reasoning:
- - Contain: 1-33
- - Press: 34-66
- - Double: 66-100

### defenceTeamWidthClass (defence Team Width Class)

- commonsense reasoning:
- - Narrow: 1-33
- - Normal: 34-66
- - Wide: 66-100

### defenceDefenderLineClass (defence Defender Line Class)

- Cover/ Offside Trap
