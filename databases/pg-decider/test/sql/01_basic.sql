-- Regression tests run against test/mock_decider.py (deterministic answers), never a real model.
\set VERBOSITY terse
CREATE EXTENSION decider;
SET decider.api_url = 'http://127.0.0.1:8766/v1/systemone';

SELECT decider_version();
SELECT decider_health()->>'max_length' AS window;

CREATE TABLE tickets (id int PRIMARY KEY, subject text, body text);
INSERT INTO tickets VALUES
  (1, 'Payouts failing', 'My payouts have failed for 3 days, this is urgent and about billing'),
  (2, 'Love the product', 'Just wanted to say thanks, everything works'),
  (3, 'App crashes', 'The app crashes on login, a technical problem'),
  (4, 'Pricing question', 'What does the enterprise plan cost? Asking sales');

-- Noul predicate (mock: 0.9 when the last word of the statement appears in the row)
SELECT id FROM tickets WHERE decide(tickets, 'the message is urgent') ORDER BY id;
SELECT id, decide_prob(tickets, 'the message is urgent') AS p FROM tickets ORDER BY id;

-- Threshold: explicit argument, then the decider.threshold setting
SELECT count(*) AS above_095 FROM tickets WHERE decide(tickets, 'the message is urgent', 0.95);
SET decider.threshold = 0.05;
SELECT count(*) AS above_005 FROM tickets WHERE decide(tickets, 'the message is urgent');
RESET decider.threshold;

-- Each row is one request; repeating a question is answered from the session cache
SELECT (decider_stats()->>'requests')::int        AS requests,
       (decider_stats()->>'rows_evaluated')::int  AS rows_evaluated,
       (decider_stats()->>'cache_hits')::int > 0  AS had_cache_hits,
       (decider_stats()->>'errors')::int          AS errors;

-- A single column is sent as plain text instead of the whole row
SELECT id FROM tickets t WHERE decide(t.body, 'this mentions billing') ORDER BY id;

-- when_true / when_false criteria are part of the question (a different cache entry)
SELECT id, decide_prob(tickets, 'the message is urgent',
                       when_true => 'the writer needs help now',
                       when_false => 'nothing is time critical') AS p
FROM tickets ORDER BY id;

-- Choice, with and without option descriptions
SELECT id, decide_choice(tickets, 'Which team should handle this?', ARRAY['billing', 'technical', 'sales']) AS team
FROM tickets ORDER BY id;
SELECT id, decide_choice(tickets, 'Which team should handle this?', ARRAY['billing', 'technical', 'sales'],
                         ARRAY['payments and invoices', 'bugs and outages', 'pricing and plans']) AS team
FROM tickets ORDER BY id;

-- Score and its normalised form
SELECT id, decide_score(tickets, 'How frustrated is the writer?', ARRAY['calm', 'frustrated', 'angry']) AS score,
       decide_score_norm(tickets, 'How frustrated is the writer?', ARRAY['calm', 'frustrated', 'angry']) AS norm
FROM tickets ORDER BY id;

-- Raw answers and confidence
SELECT decide_eval(tickets, 'Which team?', 'choice', ARRAY['billing', 'technical', 'sales']) AS raw
FROM tickets WHERE id = 1;
SELECT decide_confidence(tickets, 'Which team?', 'choice', ARRAY['billing', 'technical', 'sales']) AS choice_conf,
       decide_confidence(tickets, 'the message is urgent', 'noul') AS noul_conf
FROM tickets WHERE id = 1;

-- Several questions about one row in one request
SELECT (decider_stats()->>'requests')::int AS requests_before \gset
SELECT id,
       a->'urgent'->>'noul' AS urgent,
       a->'team'->>'choice' AS team,
       a->'mood'->>'score'  AS mood
FROM (SELECT id, decide_ask(tickets, json_build_object(
        'urgent', json_build_object('type', 'noul', 'instructions', 'the message is urgent'),
        'team',   json_build_object('type', 'choice', 'instructions', 'Which team?',
                                    'criteria', json_build_object('billing', '', 'technical', '', 'sales', '')),
        'mood',   json_build_object('type', 'score', 'instructions', 'How frustrated?',
                                    'criteria', json_build_array('calm', 'angry')))) AS a
      FROM tickets) s
ORDER BY id;
SELECT (decider_stats()->>'requests')::int - :requests_before AS requests_for_12_answers;

-- Subqueries (anonymous records) work too
SELECT s.subject FROM (SELECT subject, body FROM tickets WHERE id <= 3) s WHERE decide(s, 'something crashes');

-- Composes with ordinary SQL; NULL in, NULL out
SELECT id FROM tickets WHERE id > 1 AND decide(tickets, 'the message is urgent');
SELECT decide_prob(NULL::text, 'anything') IS NULL AS null_in_null_out;

-- Cache can be cleared, or switched off
SELECT decider_cache_clear();
SELECT (decider_stats()->>'cached_answers')::int AS cached_after_clear;
SET decider.cache = off;
SELECT (decider_stats()->>'requests')::int AS requests_before \gset
SELECT count(*) FROM tickets WHERE decide(tickets, 'the message is urgent');
SELECT count(*) FROM tickets WHERE decide(tickets, 'the message is urgent');
SELECT (decider_stats()->>'requests')::int - :requests_before AS requests_without_cache;
RESET decider.cache;

-- Idle keep-alive connections are pooled per session
SELECT (decider_stats()->>'connections')::int BETWEEN 1 AND 4 AS pooled_connection;
