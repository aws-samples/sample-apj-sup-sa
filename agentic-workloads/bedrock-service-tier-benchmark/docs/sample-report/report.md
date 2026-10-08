# Amazon Bedrock service-tier benchmark

Run `run-20261008-104744` · 2026-10-08T10:47:44+00:00 → 2026-10-08T11:00:27+00:00 · version 1.0.0

Δ = tier p50 − default p50 in the same context. (n.s.) = the 95% bootstrap confidence interval includes 0. Lower is better for TTFT/TTFAT/E2E/ITL.

| Model | Endpoint | API | Scope | Region | Size | Cache | Tier | ΔTTFT | ΔTTFAT | ΔE2E | ΔITL |
|---|---|---|---|---|---|---|---|---|---|---|---|
| GLM 5.3 | runtime | converse_stream | geo | us-east-1 | small | cold | flex | +24252 ms +421% | +24252 ms +421% | +23638 ms +364% | -6 ms -87% (n.s.) |
| GLM 5.3 | runtime | chat_completions | geo | us-east-1 | small | cold | flex | +26378 ms +841% | +26378 ms +841% | +25595 ms +634% | -6 ms -89% |

## Cells

| Cell | n | TTFT p50 | TTFT p90 | E2E p50 | E2E p90 | ITL p50 | Cache hit | Excluded | Errors |
|---|---|---|---|---|---|---|---|---|---|
| zai.glm-5.3|runtime|converse_stream|geo|us-east-1|small|cold|default | 5 | 5767 | 6018 | 6494 | 7392 | 7 | 0% |  |  |
| zai.glm-5.3|runtime|converse_stream|geo|us-east-1|small|cold|flex | 5 | 30019 | 198099 | 30133 | 198565 | 1 | 0% |  |  |
| zai.glm-5.3|runtime|chat_completions|geo|us-east-1|small|cold|default | 5 | 3136 | 7046 | 4037 | 8009 | 7 | 0% |  |  |
| zai.glm-5.3|runtime|chat_completions|geo|us-east-1|small|cold|flex | 4 | 29514 | 59031 | 29632 | 59286 | 1 | 0% |  | {'server': 1} |
