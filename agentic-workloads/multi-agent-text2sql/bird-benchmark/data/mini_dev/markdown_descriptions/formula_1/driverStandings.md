# Table: driverStandings

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| driverStandingsId | driver Standings Id | integer | the unique identification number identifying driver standing records |
| raceId | constructor Reference name | integer | id number identifying which races |
| driverId |  | integer | id number identifying which drivers |
| points |  | real | how many points acquired in each race |
| position |  | integer | position or track of circuits |
| positionText | position text | text | - |
| wins |  | integer | wins |

## Business Logic & Value Descriptions

### positionText (position text)

- same with position, not quite useful
