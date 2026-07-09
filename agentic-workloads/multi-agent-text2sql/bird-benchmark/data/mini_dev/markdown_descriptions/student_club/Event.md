# Table: Event

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| event_id | event id | text | A unique identifier for the event |
| event_name | event name | text | event name |
| event_date | event date | text | The date the event took place or is scheduled to take place |
| type |  | text | The kind of event, such as game, social, election |
| notes |  | text | A free text field for any notes about the event |
| location |  | text | Address where the event was held or is to be held or the name of such a location |
| status |  | text | One of three values indicating if the event is in planning, is opened, or is closed |

## Business Logic & Value Descriptions

### event_date (event date)

- e.g. 2020-03-10T12:00:00

### status

- Open/ Closed/ Planning
