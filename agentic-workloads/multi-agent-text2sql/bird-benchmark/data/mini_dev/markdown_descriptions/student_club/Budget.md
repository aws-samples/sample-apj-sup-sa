# Table: Budget

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| budget_id | budget id | text | A unique identifier for the budget entry |
| category |  | text | The area for which the amount is budgeted, such as, advertisement, food, parking |
| spent |  | real | The total amount spent in the budgeted category for an event. |
| remaining |  | real | A value calculated as the amount budgeted minus the amount spent |
| amount |  | integer | The amount budgeted for the specified category and event |
| event_status | event status | text | the status of the event |
| link_to_event | link to event | text | The unique identifier of the event to which the budget line applies. |

## Business Logic & Value Descriptions

### spent

- the unit is dollar. This is summarized from the Expense table

### remaining

- the unit is dollar
- commonsense evidence: If the remaining < 0, it means that the cost has exceeded the budget.

### amount

- the unit is dollar
- commonsense evidence:
- some computation like: amount = spent + remaining

### event_status (event status)

- Closed / Open/ Planning
- commonsense evidence:
- - Closed: It means that the event is closed. The spent and the remaining won't change anymore.
- - Open: It means that the event is already opened. The spent and the remaining will change with new expenses.
- - Planning: The event is not started yet but is planning. The spent and the remaining won't change at this stage.

### link_to_event (link to event)

- References the Event table
