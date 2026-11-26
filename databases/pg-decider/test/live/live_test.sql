-- Live end-to-end check against a real Strands Decider server (not part of `make installcheck`).
--   psql -v url="'http://host.docker.internal:8099/v1/systemone'" -f test/live/live_test.sql
\timing off
SET decider.api_url = :url;
SET client_min_messages = warning;

DROP TABLE IF EXISTS tickets;
CREATE TABLE tickets (id int PRIMARY KEY, body text, team text, urgent bool, angry bool);
INSERT INTO tickets VALUES
 ( 1, 'I was charged twice for my subscription this month. Please refund the duplicate payment.',          'billing',   false, false),
 ( 2, 'Your app crashes every time I open the settings page on Android 14.',                               'technical', false, false),
 ( 3, 'What does the Enterprise plan cost for 200 seats, and do you offer annual discounts?',              'sales',     false, false),
 ( 4, 'URGENT: our production checkout is down and customers cannot pay. Fix this NOW.',                   'technical', true,  true),
 ( 5, 'This is the third time I am writing. You keep billing my card after I cancelled. Unacceptable!',    'billing',   true,  true),
 ( 6, 'Can I get a demo of the analytics add-on for my team next week?',                                   'sales',     false, false),
 ( 7, 'The API returns 500 errors on every request since your deploy an hour ago, all our jobs fail.',     'technical', true,  false),
 ( 8, 'Where can I download last year''s invoices for our accounting?',                                    'billing',   false, false),
 ( 9, 'Thanks for the quick fix yesterday, everything works perfectly now!',                               'technical', false, false),
 (10, 'I want to upgrade from Basic to Pro, who should I talk to about pricing?',                          'sales',     false, false),
 (11, 'Your incompetent support has wasted my entire week. I am furious and cancelling everything.',       'billing',   false, true),
 (12, 'Password reset emails never arrive, I am locked out and have a client presentation in 30 minutes.', 'technical', true,  false),
 (13, 'Is there a nonprofit discount on your plans?',                                                      'sales',     false, false),
 (14, 'My refund was promised two weeks ago and still has not arrived. Where is my money?',                'billing',   false, true),
 (15, 'The export to CSV button does nothing in Safari.',                                                  'technical', false, false),
 (16, 'We are evaluating vendors and need a quote by Friday for a 3-year contract.',                       'sales',     false, false);

\echo '== 1. decide() as a WHERE predicate: urgent tickets'
SELECT id, left(body, 70) AS body, urgent AS truth FROM tickets t
WHERE decide(t.body, 'The writer needs a response immediately because something critical is blocked') ORDER BY id;

\echo '== 2. decide_prob() distribution vs labels (urgent)'
SELECT id, round(decide_prob(t.body, 'The writer needs a response immediately because something critical is blocked')::numeric, 3) AS p, urgent AS truth
FROM tickets t ORDER BY p DESC;

\echo '== 3. decide_choice() routing accuracy (with option descriptions)'
SELECT id, team AS truth,
       decide_choice(t.body, 'Which team should handle this ticket?', ARRAY['billing','technical','sales'],
                     ARRAY['payments, charges, refunds, invoices', 'bugs, crashes, outages, errors', 'pricing, plans, quotes, demos']) AS predicted,
       round(decide_confidence(t.body, 'Which team should handle this ticket?', 'choice', ARRAY['billing','technical','sales'])::numeric, 3) AS conf_no_desc
FROM tickets t ORDER BY id;

\echo '== 4. decide_score(): anger level vs angry label'
SELECT id, angry AS truth,
       round(decide_score_norm(t.body, 'How angry is the writer?', ARRAY['calm', 'annoyed', 'angry', 'furious'])::numeric, 3) AS anger
FROM tickets t ORDER BY anger DESC;

\echo '== 5. Accuracy summary'
WITH p AS (
  SELECT t.*,
    decide(t.body, 'The writer needs a response immediately because something critical is blocked') AS p_urgent,
    decide_choice(t.body, 'Which team should handle this ticket?', ARRAY['billing','technical','sales'],
                  ARRAY['payments, charges, refunds, invoices', 'bugs, crashes, outages, errors', 'pricing, plans, quotes, demos']) AS p_team,
    decide_score_norm(t.body, 'How angry is the writer?', ARRAY['calm', 'annoyed', 'angry', 'furious']) >= 0.5 AS p_angry
  FROM tickets t)
SELECT count(*) FILTER (WHERE p_team = team)     || '/' || count(*) AS team_correct,
       count(*) FILTER (WHERE p_urgent = urgent) || '/' || count(*) AS urgent_correct,
       count(*) FILTER (WHERE p_angry = angry)   || '/' || count(*) AS angry_correct
FROM p;

\echo '== 6. decide_ask(): 3 questions per row in one request, vs 3 separate requests'
SELECT decider_cache_clear();
SELECT clock_timestamp() AS t0 \gset
SELECT sum(length(a::text)) > 0 AS ok FROM (SELECT decide_ask(t.body, '{
   "u": {"type": "noul",   "instructions": "The writer mentions money"},
   "t": {"type": "choice", "instructions": "Which product area?", "criteria": {"mobile": null, "web": null, "api": null, "account": null}},
   "s": {"type": "score",  "instructions": "How polite is the writer?", "criteria": ["rude", "neutral", "polite"]}}') AS a FROM tickets t) s;
SELECT round(extract(epoch FROM clock_timestamp() - :'t0'::timestamptz)::numeric * 1000) AS ms_ask_3q_16rows \gset
SELECT clock_timestamp() AS t0 \gset
SELECT sum(a + length(b) + c) > 0 AS ok FROM (SELECT decide_prob(t.body, 'The writer mentions money!') AS a,
                             decide_choice(t.body, 'Which product area?!', ARRAY['mobile','web','api','account']) AS b,
                             decide_score(t.body, 'How polite is the writer?!', ARRAY['rude','neutral','polite']) AS c FROM tickets t) s;
SELECT :ms_ask_3q_16rows AS ms_one_request_per_row,
       round(extract(epoch FROM clock_timestamp() - :'t0'::timestamptz)::numeric * 1000) AS ms_three_requests_per_row;

\echo '== 7. Cache: rerunning a query does not touch the server'
SELECT clock_timestamp() AS t0 \gset
SELECT count(*) FROM tickets t WHERE decide(t.body, 'The writer mentions money');
SELECT round(extract(epoch FROM clock_timestamp() - :'t0'::timestamptz)::numeric * 1000) AS ms_first \gset
SELECT clock_timestamp() AS t0 \gset
SELECT count(*) FROM tickets t WHERE decide(t.body, 'The writer mentions money');
SELECT :ms_first AS ms_first, round(extract(epoch FROM clock_timestamp() - :'t0'::timestamptz)::numeric * 1000) AS ms_cached;

\echo '== 8. Whole row (to_json) instead of one column'
SELECT id, decide_choice(tickets, 'Which team should handle this ticket?', ARRAY['billing','technical','sales']) AS predicted, team
FROM tickets WHERE id IN (1, 2, 3);

\echo '== 9. Truncation detection on a row longer than the 4096-token window'
SET client_min_messages = warning;
SELECT decide(repeat('The quick brown fox jumps over the lazy dog. ', 1500) || ' Refund my money.', 'The writer asks for a refund') AS long_row;
SELECT (decider_stats()->>'truncated')::int AS truncated;

\echo '== 10. statement_timeout interrupts a long request'
SET statement_timeout = '50ms';
SELECT decide(repeat('Long text to make the model work. ', 800) || ' x', 'The text is repetitive, timeout test');
RESET statement_timeout;

\echo '== 11. Session stats'
SELECT decider_stats();
