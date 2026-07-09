# Table: results

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| resultId | Result ID | integer | the unique identification number identifying race result |
| raceId | race ID | integer | the identification number identifying the race |
| driverId | driver ID | integer | the identification number identifying the driver |
| constructorId | constructor Id | integer | the identification number identifying which constructors |
| number |  | integer | number |
| grid |  | integer | the number identifying the area where cars are set into a grid formation in order to start the race. |
| position |  | integer | The finishing position or track of circuits |
| positionText | position text | text | - |
| positionOrder | position order | integer | the finishing order of positions |
| points |  | real | points |
| laps |  | integer | lap number |
| time |  | text | finish time |
| milliseconds |  | integer | the actual finishing time of drivers in milliseconds |
| fastestLap | fastest lap | integer | fastest lap number |
| rank |  | integer | starting rank positioned by fastest lap speed |
| fastestLapTime | fastest Lap Time | text | fastest Lap Time |
| fastestLapSpeed | fastest Lap Speed | text | fastest Lap Speed |
| statusId | status Id | integer | status ID |

## Business Logic & Value Descriptions

### positionText (position text)

- not quite useful

### time

- commonsense evidence:
- 1. if the value exists, it means the driver finished the race.
- 2. Only the time of the champion shows in the format of "minutes: seconds.millionsecond", the time of the other drivers shows as "seconds.millionsecond" , which means their actual time is the time of the champion adding the value in this cell.

### milliseconds

- the actual finishing time of drivers

### fastestLapTime (fastest Lap Time)

- faster (smaller in the value) "fastestLapTime" leads to higher rank (smaller is higher rank)

### fastestLapSpeed (fastest Lap Speed)

- (km / h)

### statusId (status Id)

- its category description appear in the table status
