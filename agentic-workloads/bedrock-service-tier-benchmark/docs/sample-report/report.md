# Amazon Bedrock service-tier benchmark

Run `run-20261008-121212` · 2026-10-08T12:12:12+00:00 → 2026-10-08T17:03:47+00:00 · version 1.0.0

Δ = tier p50 − default p50 in the same context. (n.s.) = the 95% bootstrap confidence interval includes 0. Lower is better for TTFT/TTFAT/E2E/ITL.

| Model | Endpoint | API | Scope | Region | Size | Cache | Tier | ΔTTFT | ΔTTFAT | ΔE2E | ΔITL |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Kimi K3 | runtime | responses | geo | us-east-1 | small | cold | flex | +7091 ms +560% | +7091 ms +560% | +3994 ms +90% (n.s.) | -9 ms -92% |
| Kimi K3 | runtime | responses | geo | us-east-1 | small | cold | priority | -354 ms -28% | -354 ms -28% | -2023 ms -46% | +1 ms +13% (n.s.) |
| Kimi K3 | runtime | responses | geo | us-east-1 | small | warm_implicit | flex | — | — | — | — |
| Kimi K3 | runtime | responses | geo | us-east-1 | small | warm_implicit | priority | — | — | — | — |
| Kimi K3 | runtime | responses | geo | us-east-1 | medium | cold | flex | +1003 ms +67% | +1003 ms +67% | +216 ms +9% (n.s.) | -6 ms -89% |
| Kimi K3 | runtime | responses | geo | us-east-1 | medium | cold | priority | +12 ms +1% (n.s.) | +12 ms +1% (n.s.) | -439 ms -18% (n.s.) | -3 ms -37% (n.s.) |
| Kimi K3 | runtime | responses | geo | us-east-1 | medium | warm_implicit | flex | — | — | — | — |
| Kimi K3 | runtime | responses | geo | us-east-1 | medium | warm_implicit | priority | — | — | — | — |
| Kimi K3 | runtime | chat_completions | geo | us-east-1 | small | cold | flex | +1536 ms +124% | +1536 ms +124% | -8 ms -0% (n.s.) | -7 ms -90% |
| Kimi K3 | runtime | chat_completions | geo | us-east-1 | small | cold | priority | -324 ms -26% (n.s.) | -324 ms -26% (n.s.) | -1051 ms -32% (n.s.) | +1 ms +16% (n.s.) |
| Kimi K3 | runtime | chat_completions | geo | us-east-1 | small | warm_implicit | flex | — | — | — | — |
| Kimi K3 | runtime | chat_completions | geo | us-east-1 | small | warm_implicit | priority | — | — | — | — |
| Kimi K3 | runtime | chat_completions | geo | us-east-1 | medium | cold | flex | +1179 ms +75% | +1179 ms +75% | +216 ms +8% (n.s.) | -6 ms -86% |
| Kimi K3 | runtime | chat_completions | geo | us-east-1 | medium | cold | priority | -85 ms -5% | -85 ms -5% | -53 ms -2% (n.s.) | +2 ms +25% (n.s.) |
| Kimi K3 | runtime | chat_completions | geo | us-east-1 | medium | warm_implicit | flex | — | — | — | — |
| Kimi K3 | runtime | chat_completions | geo | us-east-1 | medium | warm_implicit | priority | — | — | — | — |
| Kimi K3 | runtime | converse_stream | geo | us-east-1 | small | cold | flex | +1552 ms +191% | +251 ms +6% (n.s.) | -248 ms -4% (n.s.) | -4 ms -40% |
| Kimi K3 | runtime | converse_stream | geo | us-east-1 | small | cold | priority | -47 ms -6% (n.s.) | -86 ms -2% (n.s.) | -657 ms -12% (n.s.) | -2 ms -16% (n.s.) |
| Kimi K3 | runtime | converse_stream | geo | us-east-1 | small | warm_implicit | flex | — | — | — | — |
| Kimi K3 | runtime | converse_stream | geo | us-east-1 | small | warm_implicit | priority | — | — | — | — |
| Kimi K3 | runtime | converse_stream | geo | us-east-1 | medium | cold | flex | +1198 ms +88% | +1028 ms +19% (n.s.) | +480 ms +8% (n.s.) | -1 ms -13% (n.s.) |
| Kimi K3 | runtime | converse_stream | geo | us-east-1 | medium | cold | priority | -197 ms -14% (n.s.) | +115 ms +2% (n.s.) | +398 ms +6% (n.s.) | +1 ms +9% (n.s.) |
| Kimi K3 | runtime | converse_stream | geo | us-east-1 | medium | warm_implicit | flex | — | — | — | — |
| Kimi K3 | runtime | converse_stream | geo | us-east-1 | medium | warm_implicit | priority | — | — | — | — |
| Kimi K3 | runtime | invoke_stream | geo | us-east-1 | small | cold | flex | +545 ms +51% | +545 ms +51% | -858 ms -35% (n.s.) | -8 ms -100% |
| Kimi K3 | runtime | invoke_stream | geo | us-east-1 | small | cold | priority | -246 ms -23% (n.s.) | -246 ms -23% (n.s.) | +277 ms +11% (n.s.) | +3 ms +32% (n.s.) |
| Kimi K3 | runtime | invoke_stream | geo | us-east-1 | small | warm_implicit | flex | — | — | — | — |
| Kimi K3 | runtime | invoke_stream | geo | us-east-1 | small | warm_implicit | priority | — | — | — | — |
| Kimi K3 | runtime | invoke_stream | geo | us-east-1 | medium | cold | flex | +1129 ms +81% | +1129 ms +81% | -210 ms -7% (n.s.) | -10 ms -95% |
| Kimi K3 | runtime | invoke_stream | geo | us-east-1 | medium | cold | priority | +12 ms +1% (n.s.) | +12 ms +1% (n.s.) | -228 ms -8% (n.s.) | -1 ms -11% (n.s.) |
| Kimi K3 | runtime | invoke_stream | geo | us-east-1 | medium | warm_implicit | flex | — | — | — | — |
| Kimi K3 | runtime | invoke_stream | geo | us-east-1 | medium | warm_implicit | priority | — | — | — | — |
| GLM 5 | runtime | chat_completions | in_region | us-east-1 | small | cold | flex | +2728 ms +266% | +2728 ms +266% | +1805 ms +83% (n.s.) | -6 ms -100% |
| GLM 5 | runtime | chat_completions | in_region | us-east-1 | small | cold | priority | +6 ms +1% (n.s.) | +6 ms +1% (n.s.) | -671 ms -31% (n.s.) | -4 ms -60% (n.s.) |
| GLM 5 | runtime | chat_completions | in_region | us-east-1 | medium | cold | flex | +1433 ms +91% (n.s.) | +1433 ms +91% (n.s.) | +106 ms +4% (n.s.) | -8 ms -100% |
| GLM 5 | runtime | chat_completions | in_region | us-east-1 | medium | cold | priority | -3 ms -0% (n.s.) | -3 ms -0% (n.s.) | -0 ms -0% (n.s.) | -3 ms -38% (n.s.) |
| GLM 5 | runtime | invoke_stream | in_region | us-east-1 | small | cold | flex | +1303 ms +130% | +1303 ms +130% | -888 ms -28% (n.s.) | -11 ms -100% |
| GLM 5 | runtime | invoke_stream | in_region | us-east-1 | small | cold | priority | -129 ms -13% (n.s.) | -129 ms -13% (n.s.) | -1995 ms -62% | -9 ms -81% |
| GLM 5 | runtime | invoke_stream | in_region | us-east-1 | medium | cold | flex | +2046 ms +138% | +2046 ms +138% | +546 ms +18% (n.s.) | -10 ms -100% |
| GLM 5 | runtime | invoke_stream | in_region | us-east-1 | medium | cold | priority | +142 ms +10% (n.s.) | +142 ms +10% (n.s.) | -247 ms -8% (n.s.) | +2 ms +17% (n.s.) |
| GLM 5 | runtime | converse_stream | in_region | us-east-1 | small | cold | flex | +1474 ms +221% | +1474 ms +221% | +554 ms +33% (n.s.) | -9 ms -100% |
| GLM 5 | runtime | converse_stream | in_region | us-east-1 | small | cold | priority | -9 ms -1% (n.s.) | -9 ms -1% (n.s.) | +248 ms +15% (n.s.) | -2 ms -21% (n.s.) |
| GLM 5 | runtime | converse_stream | in_region | us-east-1 | medium | cold | flex | +734 ms +41% (n.s.) | +734 ms +41% (n.s.) | -1233 ms -31% (n.s.) | -10 ms -100% |
| GLM 5 | runtime | converse_stream | in_region | us-east-1 | medium | cold | priority | -602 ms -34% | -602 ms -34% | -1513 ms -38% (n.s.) | +1 ms +11% (n.s.) |
| GLM 5 | mantle | chat_completions | in_region | us-east-1 | small | cold | flex | +717 ms +119% (n.s.) | +717 ms +119% (n.s.) | -826 ms -39% (n.s.) | -8 ms -100% |
| GLM 5 | mantle | chat_completions | in_region | us-east-1 | small | cold | priority | -45 ms -8% (n.s.) | -45 ms -8% (n.s.) | -732 ms -34% | -3 ms -41% |
| GLM 5 | mantle | chat_completions | in_region | us-east-1 | medium | cold | flex | +1014 ms +82% (n.s.) | +1014 ms +82% (n.s.) | -768 ms -25% (n.s.) | -9 ms -100% |
| GLM 5 | mantle | chat_completions | in_region | us-east-1 | medium | cold | priority | -206 ms -17% (n.s.) | -206 ms -17% (n.s.) | -727 ms -24% (n.s.) | -3 ms -35% (n.s.) |
| GLM 5.3 | runtime | responses | geo | us-east-1 | small | cold | flex | +17858 ms +509% | +17858 ms +509% | +16876 ms +350% | -5 ms -80% |
| GLM 5.3 | runtime | responses | geo | us-east-1 | small | cold | priority | -2335 ms -67% | -2335 ms -67% | -2499 ms -52% | +1 ms +20% (n.s.) |
| GLM 5.3 | runtime | responses | geo | us-east-1 | small | warm_implicit | flex | — | — | — | — |
| GLM 5.3 | runtime | responses | geo | us-east-1 | small | warm_implicit | priority | — | — | — | — |
| GLM 5.3 | runtime | responses | geo | us-east-1 | medium | cold | flex | +9752 ms +275% | +9752 ms +275% | +9433 ms +204% | -6 ms -80% (n.s.) |
| GLM 5.3 | runtime | responses | geo | us-east-1 | medium | cold | priority | -2175 ms -61% | -2175 ms -61% | -2017 ms -44% | -0 ms -3% (n.s.) |
| GLM 5.3 | runtime | responses | geo | us-east-1 | medium | warm_implicit | flex | — | — | — | — |
| GLM 5.3 | runtime | responses | geo | us-east-1 | medium | warm_implicit | priority | — | — | — | — |
| GLM 5.3 | runtime | chat_completions | geo | us-east-1 | small | cold | priority | -1765 ms -60% | -1765 ms -60% | -2036 ms -45% | +2 ms +43% (n.s.) |
| GLM 5.3 | runtime | chat_completions | geo | us-east-1 | small | warm_implicit | flex | — | — | — | — |
| GLM 5.3 | runtime | chat_completions | geo | us-east-1 | small | warm_implicit | priority | — | — | — | — |
| GLM 5.3 | runtime | chat_completions | geo | us-east-1 | medium | cold | flex | +22317 ms +605% | +22317 ms +605% | +22384 ms +462% | -8 ms -87% |
| GLM 5.3 | runtime | chat_completions | geo | us-east-1 | medium | cold | priority | -2188 ms -59% | -2188 ms -59% | -1809 ms -37% | +0 ms +1% (n.s.) |
| GLM 5.3 | runtime | chat_completions | geo | us-east-1 | medium | warm_implicit | flex | — | — | — | — |
| GLM 5.3 | runtime | chat_completions | geo | us-east-1 | medium | warm_implicit | priority | — | — | — | — |
| GLM 5.3 | runtime | converse_stream | geo | us-east-1 | small | cold | flex | +22864 ms +1243% | +22864 ms +1243% | +20959 ms +525% | -6 ms -88% |
| GLM 5.3 | runtime | converse_stream | geo | us-east-1 | small | cold | priority | -901 ms -49% | -901 ms -49% | -1974 ms -49% (n.s.) | +1 ms +18% (n.s.) |
| GLM 5.3 | runtime | converse_stream | geo | us-east-1 | small | warm_implicit | flex | — | — | — | — |
| GLM 5.3 | runtime | converse_stream | geo | us-east-1 | small | warm_implicit | priority | — | — | — | — |
| GLM 5.3 | runtime | converse_stream | geo | us-east-1 | medium | cold | flex | +2409 ms +53% (n.s.) | +2409 ms +53% (n.s.) | +1201 ms +20% (n.s.) | -7 ms -94% |
| GLM 5.3 | runtime | converse_stream | geo | us-east-1 | medium | cold | priority | -3262 ms -72% | -3262 ms -72% | -3337 ms -56% | -1 ms -10% (n.s.) |
| GLM 5.3 | runtime | converse_stream | geo | us-east-1 | medium | warm_implicit | flex | — | — | — | — |
| GLM 5.3 | runtime | converse_stream | geo | us-east-1 | medium | warm_implicit | priority | — | — | — | — |
| GLM 5.3 | runtime | invoke_stream | geo | us-east-1 | small | cold | flex | +13900 ms +349% | +13900 ms +349% | +12677 ms +239% | -6 ms -87% |
| GLM 5.3 | runtime | invoke_stream | geo | us-east-1 | small | cold | priority | -3019 ms -76% | -3019 ms -76% | -3405 ms -64% | +0 ms +5% (n.s.) |
| GLM 5.3 | runtime | invoke_stream | geo | us-east-1 | small | warm_implicit | flex | — | — | — | — |
| GLM 5.3 | runtime | invoke_stream | geo | us-east-1 | small | warm_implicit | priority | — | — | — | — |
| GLM 5.3 | runtime | invoke_stream | geo | us-east-1 | medium | cold | flex | +30147 ms +460% | +30147 ms +460% | +28603 ms +349% | -9 ms -95% |
| GLM 5.3 | runtime | invoke_stream | geo | us-east-1 | medium | cold | priority | -5346 ms -82% | -5346 ms -82% | -5777 ms -71% | -2 ms -18% (n.s.) |
| GLM 5.3 | runtime | invoke_stream | geo | us-east-1 | medium | warm_implicit | flex | — | — | — | — |
| GLM 5.3 | runtime | invoke_stream | geo | us-east-1 | medium | warm_implicit | priority | — | — | — | — |

## Cells

| Cell | n | TTFT p50 | TTFT p90 | E2E p50 | E2E p90 | ITL p50 | Cache hit | Excluded | Errors |
|---|---|---|---|---|---|---|---|---|---|
| moonshotai.kimi-k3 / runtime / responses / geo / us-east-1 / small / cold / default | 9 | 1266 | 4039 | 4427 | 6943 | 10 | 0% |  | {'server': 1} |
| moonshotai.kimi-k3 / runtime / responses / geo / us-east-1 / small / cold / flex | 9 | 8357 | 94517 | 8420 | 97294 | 1 | 0% |  | {'server': 1} |
| moonshotai.kimi-k3 / runtime / responses / geo / us-east-1 / small / cold / priority | 9 | 912 | 1014 | 2404 | 4638 | 11 | 0% |  | {'server': 1} |
| moonshotai.kimi-k3 / runtime / responses / geo / us-east-1 / small / warm_implicit / default | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / responses / geo / us-east-1 / small / warm_implicit / flex | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / responses / geo / us-east-1 / small / warm_implicit / priority | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / responses / geo / us-east-1 / medium / cold / default | 10 | 1491 | 1758 | 2395 | 2689 | 7 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / responses / geo / us-east-1 / medium / cold / flex | 10 | 2494 | 2722 | 2611 | 3303 | 1 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / responses / geo / us-east-1 / medium / cold / priority | 10 | 1503 | 1653 | 1956 | 2746 | 5 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / responses / geo / us-east-1 / medium / warm_implicit / default | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / responses / geo / us-east-1 / medium / warm_implicit / flex | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / responses / geo / us-east-1 / medium / warm_implicit / priority | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / chat_completions / geo / us-east-1 / small / cold / default | 10 | 1237 | 2239 | 3307 | 3851 | 8 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / chat_completions / geo / us-east-1 / small / cold / flex | 10 | 2773 | 16155 | 3299 | 16999 | 1 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / chat_completions / geo / us-east-1 / small / cold / priority | 10 | 913 | 2606 | 2256 | 6118 | 9 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / chat_completions / geo / us-east-1 / small / warm_implicit / default | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / chat_completions / geo / us-east-1 / small / warm_implicit / flex | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / chat_completions / geo / us-east-1 / small / warm_implicit / priority | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / chat_completions / geo / us-east-1 / medium / cold / default | 10 | 1567 | 1803 | 2673 | 3443 | 7 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / chat_completions / geo / us-east-1 / medium / cold / flex | 10 | 2746 | 3011 | 2889 | 4090 | 1 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / chat_completions / geo / us-east-1 / medium / cold / priority | 10 | 1481 | 2935 | 2620 | 6468 | 9 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / chat_completions / geo / us-east-1 / medium / warm_implicit / default | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / chat_completions / geo / us-east-1 / medium / warm_implicit / flex | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / chat_completions / geo / us-east-1 / medium / warm_implicit / priority | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / converse_stream / geo / us-east-1 / small / cold / default | 10 | 812 | 952 | 5577 | 6914 | 10 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / converse_stream / geo / us-east-1 / small / cold / flex | 10 | 2365 | 2406 | 5329 | 5937 | 6 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / converse_stream / geo / us-east-1 / small / cold / priority | 10 | 766 | 822 | 4919 | 7130 | 8 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / converse_stream / geo / us-east-1 / small / warm_implicit / default | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / converse_stream / geo / us-east-1 / small / warm_implicit / flex | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / converse_stream / geo / us-east-1 / small / warm_implicit / priority | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / converse_stream / geo / us-east-1 / medium / cold / default | 10 | 1369 | 1528 | 6254 | 10294 | 10 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / converse_stream / geo / us-east-1 / medium / cold / flex | 10 | 2567 | 2637 | 6734 | 8331 | 8 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / converse_stream / geo / us-east-1 / medium / cold / priority | 10 | 1172 | 1568 | 6652 | 8609 | 10 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / converse_stream / geo / us-east-1 / medium / warm_implicit / default | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / converse_stream / geo / us-east-1 / medium / warm_implicit / flex | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / converse_stream / geo / us-east-1 / medium / warm_implicit / priority | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / invoke_stream / geo / us-east-1 / small / cold / default | 10 | 1061 | 2795 | 2467 | 11390 | 8 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / invoke_stream / geo / us-east-1 / small / cold / flex | 10 | 1606 | 2227 | 1609 | 2279 | 0 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / invoke_stream / geo / us-east-1 / small / cold / priority | 10 | 816 | 881 | 2744 | 3699 | 11 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / invoke_stream / geo / us-east-1 / small / warm_implicit / default | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / invoke_stream / geo / us-east-1 / small / warm_implicit / flex | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / invoke_stream / geo / us-east-1 / small / warm_implicit / priority | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / invoke_stream / geo / us-east-1 / medium / cold / default | 10 | 1396 | 1608 | 2832 | 3389 | 11 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / invoke_stream / geo / us-east-1 / medium / cold / flex | 10 | 2524 | 2675 | 2623 | 3796 | 1 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / invoke_stream / geo / us-east-1 / medium / cold / priority | 10 | 1408 | 2096 | 2604 | 4003 | 10 | 0% |  |  |
| moonshotai.kimi-k3 / runtime / invoke_stream / geo / us-east-1 / medium / warm_implicit / default | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / invoke_stream / geo / us-east-1 / medium / warm_implicit / flex | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| moonshotai.kimi-k3 / runtime / invoke_stream / geo / us-east-1 / medium / warm_implicit / priority | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5 / runtime / chat_completions / in_region / us-east-1 / small / cold / default | 10 | 1027 | 2018 | 2162 | 3239 | 6 | 0% |  |  |
| zai.glm-5 / runtime / chat_completions / in_region / us-east-1 / small / cold / flex | 10 | 3755 | 10403 | 3968 | 10403 | 0 | 0% |  |  |
| zai.glm-5 / runtime / chat_completions / in_region / us-east-1 / small / cold / priority | 10 | 1033 | 2369 | 1491 | 7575 | 2 | 0% |  |  |
| zai.glm-5 / runtime / chat_completions / in_region / us-east-1 / medium / cold / default | 10 | 1578 | 1727 | 2905 | 3657 | 8 | 0% |  |  |
| zai.glm-5 / runtime / chat_completions / in_region / us-east-1 / medium / cold / flex | 10 | 3011 | 4714 | 3011 | 4715 | 0 | 0% |  |  |
| zai.glm-5 / runtime / chat_completions / in_region / us-east-1 / medium / cold / priority | 10 | 1575 | 3613 | 2904 | 4883 | 5 | 0% |  |  |
| zai.glm-5 / runtime / invoke_stream / in_region / us-east-1 / small / cold / default | 10 | 1004 | 1340 | 3197 | 4026 | 11 | 0% |  |  |
| zai.glm-5 / runtime / invoke_stream / in_region / us-east-1 / small / cold / flex | 10 | 2307 | 192626 | 2309 | 192626 | 0 | 0% |  |  |
| zai.glm-5 / runtime / invoke_stream / in_region / us-east-1 / small / cold / priority | 10 | 875 | 1220 | 1202 | 3475 | 2 | 0% |  |  |
| zai.glm-5 / runtime / invoke_stream / in_region / us-east-1 / medium / cold / default | 10 | 1483 | 1888 | 2983 | 3974 | 10 | 0% |  |  |
| zai.glm-5 / runtime / invoke_stream / in_region / us-east-1 / medium / cold / flex | 10 | 3529 | 4261 | 3529 | 4262 | 0 | 0% |  |  |
| zai.glm-5 / runtime / invoke_stream / in_region / us-east-1 / medium / cold / priority | 10 | 1626 | 2420 | 2736 | 3960 | 12 | 0% |  |  |
| zai.glm-5 / runtime / converse_stream / in_region / us-east-1 / small / cold / default | 10 | 667 | 2324 | 1674 | 6542 | 9 | 0% |  |  |
| zai.glm-5 / runtime / converse_stream / in_region / us-east-1 / small / cold / flex | 10 | 2141 | 2791 | 2228 | 5977 | 0 | 0% |  |  |
| zai.glm-5 / runtime / converse_stream / in_region / us-east-1 / small / cold / priority | 10 | 658 | 5564 | 1922 | 9934 | 7 | 0% |  |  |
| zai.glm-5 / runtime / converse_stream / in_region / us-east-1 / medium / cold / default | 10 | 1781 | 4799 | 4004 | 13217 | 10 | 0% |  |  |
| zai.glm-5 / runtime / converse_stream / in_region / us-east-1 / medium / cold / flex | 10 | 2515 | 6070 | 2770 | 6225 | 0 | 0% |  |  |
| zai.glm-5 / runtime / converse_stream / in_region / us-east-1 / medium / cold / priority | 10 | 1179 | 1499 | 2491 | 4343 | 11 | 0% |  |  |
| zai.glm-5 / mantle / chat_completions / in_region / us-east-1 / small / cold / default | 9 | 601 | 2843 | 2144 | 4957 | 8 | 0% |  | {'server': 1} |
| zai.glm-5 / mantle / chat_completions / in_region / us-east-1 / small / cold / flex | 10 | 1318 | 3028 | 1318 | 3028 | 0 | 0% |  |  |
| zai.glm-5 / mantle / chat_completions / in_region / us-east-1 / small / cold / priority | 10 | 556 | 1122 | 1412 | 1864 | 5 | 0% |  |  |
| zai.glm-5 / mantle / chat_completions / in_region / us-east-1 / medium / cold / default | 10 | 1240 | 2870 | 3022 | 5806 | 9 | 0% |  |  |
| zai.glm-5 / mantle / chat_completions / in_region / us-east-1 / medium / cold / flex | 10 | 2254 | 3866 | 2254 | 3866 | 0 | 0% |  |  |
| zai.glm-5 / mantle / chat_completions / in_region / us-east-1 / medium / cold / priority | 10 | 1034 | 1544 | 2296 | 4603 | 6 | 0% |  |  |
| zai.glm-5.3 / runtime / responses / geo / us-east-1 / small / cold / default | 10 | 3507 | 9448 | 4816 | 10116 | 6 | 0% |  |  |
| zai.glm-5.3 / runtime / responses / geo / us-east-1 / small / cold / flex | 10 | 21365 | 37406 | 21692 | 37484 | 1 | 0% |  |  |
| zai.glm-5.3 / runtime / responses / geo / us-east-1 / small / cold / priority | 10 | 1173 | 1242 | 2317 | 4724 | 7 | 0% |  |  |
| zai.glm-5.3 / runtime / responses / geo / us-east-1 / small / warm_implicit / default | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / responses / geo / us-east-1 / small / warm_implicit / flex | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / responses / geo / us-east-1 / small / warm_implicit / priority | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / responses / geo / us-east-1 / medium / cold / default | 9 | 3545 | 11394 | 4626 | 12317 | 8 | 0% |  | {'server': 1} |
| zai.glm-5.3 / runtime / responses / geo / us-east-1 / medium / cold / flex | 10 | 13297 | 60719 | 14059 | 61297 | 2 | 0% |  |  |
| zai.glm-5.3 / runtime / responses / geo / us-east-1 / medium / cold / priority | 10 | 1369 | 1643 | 2610 | 4806 | 7 | 0% |  |  |
| zai.glm-5.3 / runtime / responses / geo / us-east-1 / medium / warm_implicit / default | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / responses / geo / us-east-1 / medium / warm_implicit / flex | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / responses / geo / us-east-1 / medium / warm_implicit / priority | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / chat_completions / geo / us-east-1 / small / cold / default | 9 | 2931 | 4729 | 4506 | 5397 | 5 | 0% |  | {'server': 1} |
| zai.glm-5.3 / runtime / chat_completions / geo / us-east-1 / small / cold / priority | 10 | 1165 | 4442 | 2470 | 6852 | 8 | 0% |  |  |
| zai.glm-5.3 / runtime / chat_completions / geo / us-east-1 / small / warm_implicit / default | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / chat_completions / geo / us-east-1 / small / warm_implicit / flex | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / chat_completions / geo / us-east-1 / small / warm_implicit / priority | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / chat_completions / geo / us-east-1 / medium / cold / default | 10 | 3691 | 11721 | 4849 | 14239 | 9 | 0% |  |  |
| zai.glm-5.3 / runtime / chat_completions / geo / us-east-1 / medium / cold / flex | 10 | 26007 | 71128 | 27233 | 71261 | 1 | 0% |  |  |
| zai.glm-5.3 / runtime / chat_completions / geo / us-east-1 / medium / cold / priority | 10 | 1503 | 3247 | 3040 | 5345 | 9 | 0% |  |  |
| zai.glm-5.3 / runtime / chat_completions / geo / us-east-1 / medium / warm_implicit / default | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / chat_completions / geo / us-east-1 / medium / warm_implicit / flex | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / chat_completions / geo / us-east-1 / medium / warm_implicit / priority | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / converse_stream / geo / us-east-1 / small / cold / default | 10 | 1840 | 7176 | 3995 | 8278 | 6 | 0% |  |  |
| zai.glm-5.3 / runtime / converse_stream / geo / us-east-1 / small / cold / flex | 10 | 24704 | 59172 | 24954 | 62906 | 1 | 0% |  |  |
| zai.glm-5.3 / runtime / converse_stream / geo / us-east-1 / small / cold / priority | 10 | 939 | 1046 | 2021 | 3336 | 7 | 0% |  |  |
| zai.glm-5.3 / runtime / converse_stream / geo / us-east-1 / small / warm_implicit / default | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / converse_stream / geo / us-east-1 / small / warm_implicit / flex | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / converse_stream / geo / us-east-1 / small / warm_implicit / priority | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / converse_stream / geo / us-east-1 / medium / cold / default | 9 | 4512 | 9069 | 5999 | 11775 | 8 | 0% |  | {'server': 1} |
| zai.glm-5.3 / runtime / converse_stream / geo / us-east-1 / medium / cold / flex | 10 | 6921 | 39365 | 7200 | 40082 | 0 | 0% |  |  |
| zai.glm-5.3 / runtime / converse_stream / geo / us-east-1 / medium / cold / priority | 10 | 1250 | 3059 | 2662 | 4841 | 7 | 0% |  |  |
| zai.glm-5.3 / runtime / converse_stream / geo / us-east-1 / medium / warm_implicit / default | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / converse_stream / geo / us-east-1 / medium / warm_implicit / flex | 0 | — | — | — | — | — | 0% | {'warm_miss': 9} | {'server': 1} |
| zai.glm-5.3 / runtime / converse_stream / geo / us-east-1 / medium / warm_implicit / priority | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / invoke_stream / geo / us-east-1 / small / cold / default | 10 | 3986 | 12199 | 5300 | 13840 | 7 | 0% |  |  |
| zai.glm-5.3 / runtime / invoke_stream / geo / us-east-1 / small / cold / flex | 10 | 17886 | 27534 | 17977 | 27757 | 1 | 0% |  |  |
| zai.glm-5.3 / runtime / invoke_stream / geo / us-east-1 / small / cold / priority | 10 | 967 | 1020 | 1895 | 4021 | 7 | 0% |  |  |
| zai.glm-5.3 / runtime / invoke_stream / geo / us-east-1 / small / warm_implicit / default | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / invoke_stream / geo / us-east-1 / small / warm_implicit / flex | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / invoke_stream / geo / us-east-1 / small / warm_implicit / priority | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / invoke_stream / geo / us-east-1 / medium / cold / default | 8 | 6554 | 15623 | 8190 | 17541 | 9 | 0% |  | {'server': 2} |
| zai.glm-5.3 / runtime / invoke_stream / geo / us-east-1 / medium / cold / flex | 10 | 36702 | 78971 | 36793 | 78973 | 0 | 0% |  |  |
| zai.glm-5.3 / runtime / invoke_stream / geo / us-east-1 / medium / cold / priority | 10 | 1208 | 4114 | 2413 | 5828 | 8 | 0% |  |  |
| zai.glm-5.3 / runtime / invoke_stream / geo / us-east-1 / medium / warm_implicit / default | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
| zai.glm-5.3 / runtime / invoke_stream / geo / us-east-1 / medium / warm_implicit / flex | 0 | — | — | — | — | — | 0% | {'warm_miss': 9} | {'server': 1} |
| zai.glm-5.3 / runtime / invoke_stream / geo / us-east-1 / medium / warm_implicit / priority | 0 | — | — | — | — | — | 0% | {'warm_miss': 10} |  |
