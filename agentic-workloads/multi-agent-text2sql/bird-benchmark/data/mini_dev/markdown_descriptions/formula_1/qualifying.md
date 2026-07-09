# Table: qualifying

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| qualifyId | qualify Id | integer | the unique identification number identifying qualifying |
| raceId | race Id | integer | the identification number identifying each race |
| driverId | driver Id | integer | the identification number identifying each driver |
| constructorId | constructor id | integer | constructor Id |
| number |  | integer | number |
| position |  | integer | position or track of circuit |
| q1 | qualifying 1 | text | time in qualifying 1 |
| q2 | qualifying 2 | text | time in qualifying 2 |
| q3 | qualifying 3 | text | time in qualifying 3 |

## Business Logic & Value Descriptions

### qualifyId (qualify Id)

- How does F1 Sprint qualifying work? Sprint qualifying is essentially a short-form Grand Prix - a race that is one-third the number of laps of the main event on Sunday. However, the drivers are battling for positions on the grid for the start of Sunday's race.

### q1 (qualifying 1)

- in minutes / seconds / ...
- commonsense evidence:
- Q1 lap times determine pole position and the order of the front 10 positions on the grid. The slowest driver in Q1 starts 10th, the next starts ninth and so on.
- All 20 F1 drivers participate in the first period, called Q1, with each trying to set the fastest time possible. Those in the top 15 move on to the next period of qualifying, called Q2. The five slowest drivers are eliminated and will start the race in the last five positions on the grid.

### q2 (qualifying 2)

- in minutes / seconds / ...
- commonsense evidence:
- only top 15 in the q1 has the record of q2
- Q2 is slightly shorter but follows the same format. Drivers try to put down their best times to move on to Q1 as one of the 10 fastest cars. The five outside of the top 10 are eliminated and start the race from 11th to 15th based on their best lap time.

### q3 (qualifying 3)

- in minutes / seconds / ...
- commonsense evidence:
- only top 10 in the q2 has the record of q3
