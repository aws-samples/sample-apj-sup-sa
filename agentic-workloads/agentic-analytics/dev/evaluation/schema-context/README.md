# Schema context benchmark

How much schema should `text_to_sql_tool` send to the model? This test compared three ways of loading schema context, on the Timely-Unicorn schema and on the same schema padded with unrelated tables.

## Setup

- **Schemas.** *Base*: the 31 Timely-Unicorn tables and views, with their Glue comments. *Enlarged*: base plus 150 generated distractor tables (HR, finance, marketing, IoT, support, inventory) with about 1,500 columns, 181 objects in all. Some distractor columns are near-synonyms of real ones (client vs customer, reservation vs booking) to make table linking harder.
- **Questions.** The 60 questions in `questions.json`, from single-table counts to 3-way joins with time windows. Each has gold SQL against the base tables.
- **Model.** Claude Sonnet 5.5 (`global.anthropic.claude-sonnet-5-5`) writes the SQL from each arm's context.
- **Score.** Execution accuracy (EX): the generated SQL runs on Aurora and returns the same rows as the gold SQL.

## Arms

| Arm | Context sent |
|-----|--------------|
| A | The full schema (the previous behaviour) |
| C | Top-K table cards from a Bedrock Knowledge Base search, plus tables reachable by foreign key |
| G | A one-line-per-table index; the model picks tables and calls again with `tables` to get their full columns |

## Results

| Arm | Schema | EX | Avg input tokens | Avg seconds |
|-----|--------|----|------------------|-------------|
| A | base | 88.3% | 4,848 | 2.4 |
| C | base | 85.0% | 2,201 | 2.6 |
| G | base | 91.7% | 3,502 | 5.0 |
| A | enlarged | 85.0% | 50,927 | 3.9 |
| C | enlarged | 81.7% | 2,384 | 2.4 |
| G | enlarged | 90.0% | 11,591 | 5.4 |

`results.json` has the same numbers and the ids of the questions each arm got wrong.

## Decision

`text_to_sql_tool` sends the full schema while it is under `SCHEMA_FULL_MAX_CHARS` (32,000 characters) and switches to the index (arm G) above that. On a small schema the full schema is cheap and the extra round trip of G adds latency; on a large one G keeps accuracy and cuts input tokens by about 77%.

Most misses in every arm came from vague table and column descriptions, not from the retrieval method, so the schema change pipeline (workshop Step 11) keeps those descriptions current.

## Limits

These are results from one synthetic schema, one question set and one model. Expect different numbers on other schemas; rerun the questions against your own data before relying on the threshold.
