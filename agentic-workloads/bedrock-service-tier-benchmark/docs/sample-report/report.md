# Bedrock Service-Tier Latency Benchmark — flex &amp; priority vs default

- **Run:** `run-example`
- **Account:** 123456789012  
- **Profile:** `my-aws-profile`  •  **Regions (pref):** us-west-2, us-east-1
- **Samples/cell (n):** 30  •  **Interval:** 60s  •  **max_tokens:** 200
- **Started:** 2026-06-17T14:12:04.301519+00:00  •  **Finished:** 2026-06-17T15:44:25.677715+00:00

All latencies in **milliseconds**. Each row shows **default p20/p50/p90**, then **flex** and **priority** p20/p50/p90, each with **Δp50** vs default (negative ⇒ that tier is faster). `NA` = the model does not serve that tier on this transport.

## Amazon Nova

### Amazon Nova Pro — `invoke` (us-east-1, `amazon.nova-pro-v1:0`)
default: 30/30 ok, served[(unreported):30]  •  flex: 28/30 ok, served[flex:28], 2 fail (TimeoutError×2)  •  priority: 29/30 ok, served[priority:29], 1 fail (TimeoutError×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 533 | 549 | 663 | 542 | 579 | 691 | +5% | 523 | 597 | 841 | +9% |
| Total | 724 | 818 | 1063 | 786 | 850 | 1014 | +4% | 725 | 843 | 1127 | +3% |

## DeepSeek

### DeepSeek V3 — `invoke` (us-west-2, `deepseek.v3-v1:0`)
default: 30/30 ok, served[(unreported):30]  •  flex: 28/30 ok, served[flex:28], 2 fail (TimeoutError×2)  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 517 | 553 | 605 | 528 | 580 | 751 | +5% | 531 | 553 | 1057 | +0% |
| Total | 517 | 553 | 607 | 528 | 580 | 751 | +5% | 531 | 553 | 1151 | +0% |

### DeepSeek V3.1 — `mantle` (us-west-2, `deepseek.v3.1`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 815 | 844 | 1054 | 812 | 872 | 1098 | +3% | 809 | 840 | 1066 | -0% |
| Total | 815 | 844 | 1087 | 812 | 872 | 1098 | +3% | 809 | 840 | 1137 | -0% |

### DeepSeek V3.2 — `invoke` (us-west-2, `deepseek.v3.2`)
default: 29/30 ok, served[(unreported):29], 1 fail (TimeoutError×1)  •  flex: 21/30 ok, served[flex:21], 9 fail (TimeoutError×8, Could not connect to the endpoint URL×1)  •  priority: 29/30 ok, served[priority:29], 1 fail (Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 865 | 936 | 2448 | 1122 | 1219 | 1352 | +30% | 859 | 890 | 1083 | -5% |
| Total | 1080 | 1286 | 2723 | 1122 | 1219 | 1352 | -5% | 1081 | 1190 | 1559 | -7% |

### DeepSeek V3.2 — `mantle` (us-west-2, `deepseek.v3.2`)
default: 29/30 ok, served[default:29], 1 fail (TimeoutError×1)  •  flex: 21/30 ok, served[flex:21], 9 fail (TimeoutError×9)  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 964 | 1041 | 4448 | 1387 | 1488 | 1663 | +43% | 963 | 988 | 1243 | -5% |
| Total | 1471 | 1737 | 5097 | 1387 | 1488 | 1663 | -14% | 1338 | 1606 | 1913 | -8% |

## GLM (Z.AI)

### Z.AI GLM 4.6 — `mantle` (us-west-2, `zai.glm-4.6`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 840 | 849 | 957 | 979 | 1014 | 1217 | +19% | 836 | 854 | 1398 | +1% |
| Total | 963 | 1020 | 1106 | 979 | 1014 | 1217 | -1% | 982 | 1024 | 1529 | +0% |

### Z.AI GLM 4.7 — `invoke` (us-west-2, `zai.glm-4.7`)
default: 30/30 ok, served[(unreported):30]  •  flex: 29/30 ok, served[flex:29], 1 fail (TimeoutError×1)  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 663 | 718 | 792 | 678 | 738 | 977 | +3% | 668 | 705 | 1203 | -2% |
| Total | 673 | 727 | 921 | 678 | 738 | 977 | +1% | 678 | 716 | 1288 | -2% |

### Z.AI GLM 4.7 — `mantle` (us-west-2, `zai.glm-4.7`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 833 | 847 | 983 | 954 | 998 | 2044 | +18% | 837 | 851 | 960 | +0% |
| Total | 952 | 989 | 1161 | 954 | 998 | 2044 | +1% | 935 | 980 | 1136 | -1% |

### Z.AI GLM 4.7 Flash — `invoke` (us-west-2, `zai.glm-4.7-flash`)
default: 30/30 ok, served[(unreported):30]  •  flex: 29/30 ok, served[flex:29], 1 fail (TimeoutError×1)  •  priority: 29/30 ok, served[priority:29], 1 fail (Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 495 | 579 | 760 | 504 | 554 | 1002 | -4% | 470 | 550 | 765 | -5% |
| Total | 495 | 592 | 770 | 504 | 554 | 1002 | -6% | 470 | 550 | 765 | -7% |

### Z.AI GLM 4.7 Flash — `mantle` (us-west-2, `zai.glm-4.7-flash`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 774 | 820 | 983 | 755 | 833 | 1417 | +2% | 763 | 816 | 1229 | -1% |
| Total | 774 | 833 | 1040 | 755 | 833 | 1417 | -0% | 763 | 816 | 1242 | -2% |

### Z.AI GLM 5 — `invoke` (us-west-2, `zai.glm-5`)
default: 29/30 ok, served[(unreported):29], 1 fail (TimeoutError×1)  •  flex: 29/30 ok, served[flex:29], 1 fail (Could not connect to the endpoint URL×1)  •  priority: 29/30 ok, served[priority:29], 1 fail (Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 892 | 937 | 1464 | 989 | 1113 | 1586 | +19% | 855 | 919 | 1111 | -2% |
| Total | 936 | 1023 | 1599 | 989 | 1113 | 1586 | +9% | 923 | 984 | 1632 | -4% |

### Z.AI GLM 5 — `mantle` (us-west-2, `zai.glm-5`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 999 | 1025 | 1221 | 1218 | 1288 | 1619 | +26% | 997 | 1016 | 1161 | -1% |
| Total | 1246 | 1290 | 1757 | 1218 | 1288 | 1619 | -0% | 1202 | 1255 | 2066 | -3% |

## Google Gemma

### Google Gemma 3 12B — `invoke` (us-west-2, `google.gemma-3-12b-it`)
default: 30/30 ok, served[(unreported):30]  •  flex: 29/30 ok, served[flex:29], 1 fail (TimeoutError×1)  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 580 | 609 | 853 | 609 | 631 | 760 | +4% | 587 | 633 | 834 | +4% |
| Total | 592 | 628 | 854 | 609 | 631 | 760 | +1% | 602 | 643 | 846 | +2% |

### Google Gemma 3 12B — `mantle` (us-west-2, `google.gemma-3-12b-it`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 816 | 829 | 917 | 861 | 908 | 1094 | +10% | 816 | 836 | 884 | +1% |
| Total | 855 | 872 | 1023 | 861 | 908 | 1094 | +4% | 872 | 891 | 1045 | +2% |

### Google Gemma 3 27B — `invoke` (us-west-2, `google.gemma-3-27b-it`)
default: 30/30 ok, served[(unreported):30]  •  flex: 29/30 ok, served[flex:29], 1 fail (TimeoutError×1)  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 709 | 752 | 1027 | 829 | 911 | 1665 | +21% | 712 | 740 | 886 | -1% |
| Total | 767 | 897 | 1323 | 829 | 911 | 1665 | +2% | 755 | 884 | 1218 | -1% |

### Google Gemma 3 27B — `mantle` (us-west-2, `google.gemma-3-27b-it`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 814 | 845 | 1347 | 1045 | 1202 | 1376 | +42% | 812 | 849 | 1095 | +1% |
| Total | 1031 | 1183 | 1975 | 1045 | 1202 | 1376 | +2% | 1053 | 1151 | 1384 | -3% |

### Google Gemma 3 4B — `invoke` (us-west-2, `google.gemma-3-4b-it`)
default: 30/30 ok, served[(unreported):30]  •  flex: 29/30 ok, served[flex:29], 1 fail (TimeoutError×1)  •  priority: 29/30 ok, served[priority:29], 1 fail (TimeoutError×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 464 | 496 | 547 | 441 | 461 | 535 | -7% | 462 | 503 | 558 | +1% |
| Total | 464 | 496 | 547 | 441 | 461 | 535 | -7% | 462 | 503 | 558 | +1% |

### Google Gemma 3 4B — `mantle` (us-west-2, `google.gemma-3-4b-it`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 713 | 733 | 841 | 710 | 748 | 813 | +2% | 700 | 721 | 810 | -2% |
| Total | 713 | 733 | 841 | 710 | 748 | 813 | +2% | 700 | 721 | 810 | -2% |

## Kimi (Moonshot)

### Kimi K2 Thinking — `invoke` (us-west-2, `moonshot.kimi-k2-thinking`)
default: 30/30 ok, served[(unreported):30]  •  flex: 28/30 ok, served[flex:28], 2 fail (TimeoutError×2)  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 713 | 756 | 869 | 1363 | 1560 | 1873 | +106% | 724 | 763 | 871 | +1% |
| Total | 1418 | 1609 | 1914 | 1379 | 1565 | 1892 | -3% | 1363 | 1638 | 1920 | +2% |

### Kimi K2 Thinking — `mantle` (us-west-2, `moonshotai.kimi-k2-thinking`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 1392 | 1722 | 1870 | 1605 | 1752 | 2195 | +2% | 1408 | 1694 | 1938 | -2% |
| Total | 1670 | 1854 | 2218 | 1725 | 2114 | 2220 | +14% | 1793 | 1850 | 2374 | -0% |

### Kimi K2.5 — `invoke` (us-west-2, `moonshotai.kimi-k2.5`)
default: 30/30 ok, served[(unreported):30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 29/30 ok, served[priority:29], 1 fail (Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 552 | 639 | 828 | 583 | 700 | 3633 | +10% | 560 | 619 | 715 | -3% |
| Total | 552 | 642 | 844 | 583 | 700 | 3806 | +9% | 564 | 619 | 731 | -3% |

### Kimi K2.5 — `mantle` (us-west-2, `moonshotai.kimi-k2.5`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 811 | 896 | 1148 | 828 | 917 | 1328 | +2% | 808 | 843 | 980 | -6% |
| Total | 811 | 928 | 1193 | 828 | 917 | 1328 | -1% | 808 | 887 | 1054 | -4% |

## MiniMax

### MiniMax M2 — `invoke` (us-west-2, `minimax.minimax-m2`)
default: 29/30 ok, served[(unreported):29], 1 fail (TimeoutError×1)  •  flex: 30/30 ok, served[flex:30]  •  priority: 29/30 ok, served[priority:29], 1 fail (Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 739 | 763 | 906 | 1912 | 2170 | 2933 | +185% | 757 | 778 | 811 | +2% |
| Total | 1921 | 2532 | 2953 | 1923 | 2170 | 2953 | -14% | 1937 | 2304 | 2902 | -9% |

### MiniMax M2 — `mantle` (us-west-2, `minimax.minimax-m2`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 1967 | 2342 | 3145 | 2212 | 2430 | 2692 | +4% | 1745 | 2114 | 4319 | -10% |
| Total | 2198 | 2671 | 3270 | 2215 | 2600 | 3173 | -3% | 2173 | 2655 | 3197 | -1% |

### MiniMax M2.1 — `invoke` (us-west-2, `minimax.minimax-m2.1`)
default: 29/30 ok, served[(unreported):29], 1 fail (TimeoutError×1)  •  flex: 30/30 ok, served[flex:30]  •  priority: 29/30 ok, served[priority:29], 1 fail (Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 734 | 757 | 843 | 1014 | 1161 | 1764 | +53% | 735 | 745 | 767 | -2% |
| Total | 1055 | 1236 | 1753 | 1025 | 1182 | 1779 | -4% | 895 | 1092 | 1567 | -12% |

### MiniMax M2.1 — `mantle` (us-west-2, `minimax.minimax-m2.1`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 1045 | 1230 | 1507 | 1302 | 1481 | 1875 | +20% | 1043 | 1235 | 1734 | +0% |
| Total | 1224 | 1431 | 2049 | 1302 | 1484 | 1934 | +4% | 1228 | 1384 | 2058 | -3% |

### MiniMax M2.5 — `invoke` (us-west-2, `minimax.minimax-m2.5`)
default: 27/30 ok, served[(unreported):27], 3 fail (An error occurred (ServiceUnavailableException) when calling×1, TimeoutError×2)  •  flex: 28/30 ok, served[flex:28], 2 fail (TimeoutError×1, Could not connect to the endpoint URL×1)  •  priority: 29/30 ok, served[priority:29], 1 fail (Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 724 | 746 | 1379 | 1923 | 2173 | 2554 | +191% | 714 | 740 | 910 | -1% |
| Total | 1772 | 2238 | 3809 | 1923 | 2188 | 2692 | -2% | 2071 | 2404 | 3611 | +7% |

### MiniMax M2.5 — `mantle` (us-west-2, `minimax.minimax-m2.5`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 2038 | 2103 | 2893 | 2035 | 2421 | 6968 | +15% | 2058 | 2388 | 4602 | +14% |
| Total | 2281 | 2528 | 3073 | 2040 | 2392 | 6965 | -5% | 2384 | 2734 | 4130 | +8% |

## Mistral

### Mistral Devstral 2 123B — `invoke` (us-west-2, `mistral.devstral-2-123b`)
default: 30/30 ok, served[(unreported):30]  •  flex: 29/30 ok, served[flex:29], 1 fail (Read timeout on endpoint URL×1)  •  priority: 29/30 ok, served[priority:29], 1 fail (Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 717 | 742 | 863 | 762 | 850 | 956 | +15% | 717 | 750 | 789 | +1% |
| Total | 776 | 855 | 995 | 762 | 850 | 956 | -1% | 797 | 837 | 949 | -2% |

### Mistral Devstral 2 123B — `mantle` (us-west-2, `mistral.devstral-2-123b`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 798 | 827 | 983 | 1049 | 1135 | 1480 | +37% | 807 | 831 | 1194 | +1% |
| Total | 1031 | 1095 | 1225 | 1049 | 1135 | 1480 | +4% | 1027 | 1097 | 1459 | +0% |

### Mistral Magistral Small 2509 — `invoke` (us-west-2, `mistral.magistral-small-2509`)
default: 29/30 ok, served[(unreported):29], 1 fail (TimeoutError×1)  •  flex: 30/30 ok, served[flex:30]  •  priority: 28/30 ok, served[priority:28], 2 fail (TimeoutError×1, Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 723 | 748 | 795 | 770 | 840 | 968 | +12% | 733 | 754 | 791 | +1% |
| Total | 754 | 831 | 933 | 770 | 840 | 968 | +1% | 763 | 819 | 894 | -1% |

### Mistral Magistral Small 2509 — `mantle` (us-west-2, `mistral.magistral-small-2509`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 814 | 837 | 1000 | 1008 | 1071 | 1193 | +28% | 821 | 841 | 1146 | +0% |
| Total | 1011 | 1079 | 1269 | 1008 | 1071 | 1193 | -1% | 1050 | 1098 | 1398 | +2% |

### Mistral Ministral 3 14B — `invoke` (us-west-2, `mistral.ministral-3-14b-instruct`)
default: 29/30 ok, served[(unreported):29], 1 fail (TimeoutError×1)  •  flex: 29/30 ok, served[flex:29], 1 fail (TimeoutError×1)  •  priority: 29/30 ok, served[priority:29], 1 fail (Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 460 | 490 | 555 | 457 | 480 | 517 | -2% | 459 | 492 | 606 | +0% |
| Total | 460 | 490 | 566 | 457 | 480 | 517 | -2% | 459 | 492 | 606 | +0% |

### Mistral Ministral 3 14B — `mantle` (us-west-2, `mistral.ministral-3-14b-instruct`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 722 | 760 | 891 | 726 | 745 | 789 | -2% | 724 | 752 | 1729 | -1% |
| Total | 722 | 760 | 891 | 726 | 745 | 789 | -2% | 724 | 752 | 1729 | -1% |

### Mistral Ministral 3 3B — `invoke` (us-west-2, `mistral.ministral-3-3b-instruct`)
default: 30/30 ok, served[(unreported):30]  •  flex: 27/30 ok, served[flex:27], 3 fail (TimeoutError×2, Could not connect to the endpoint URL×1)  •  priority: 29/30 ok, served[priority:29], 1 fail (TimeoutError×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 394 | 414 | 487 | 386 | 401 | 477 | -3% | 396 | 404 | 466 | -2% |
| Total | 394 | 414 | 487 | 386 | 401 | 477 | -3% | 396 | 404 | 466 | -2% |

### Mistral Ministral 3 3B — `mantle` (us-west-2, `mistral.ministral-3-3b-instruct`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 648 | 665 | 782 | 654 | 666 | 740 | +0% | 642 | 668 | 812 | +0% |
| Total | 648 | 665 | 782 | 654 | 666 | 740 | +0% | 642 | 668 | 812 | +0% |

### Mistral Ministral 3 8B — `invoke` (us-west-2, `mistral.ministral-3-8b-instruct`)
default: 29/30 ok, served[(unreported):29], 1 fail (TimeoutError×1)  •  flex: 30/30 ok, served[flex:30]  •  priority: 28/30 ok, served[priority:28], 2 fail (Could not connect to the endpoint URL×2)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 475 | 520 | 668 | 489 | 541 | 873 | +4% | 494 | 518 | 565 | -0% |
| Total | 475 | 520 | 673 | 489 | 541 | 873 | +4% | 494 | 518 | 568 | -0% |

### Mistral Ministral 3 8B — `mantle` (us-west-2, `mistral.ministral-3-8b-instruct`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 751 | 790 | 900 | 727 | 766 | 901 | -3% | 738 | 791 | 883 | +0% |
| Total | 751 | 790 | 913 | 727 | 766 | 901 | -3% | 738 | 791 | 883 | +0% |

### Mistral Large 3 675B — `invoke` (us-west-2, `mistral.mistral-large-3-675b-instruct`)
default: 29/30 ok, served[(unreported):29], 1 fail (TimeoutError×1)  •  flex: 30/30 ok, served[flex:30]  •  priority: 29/30 ok, served[priority:29], 1 fail (Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 539 | 595 | 887 | 563 | 604 | 849 | +2% | 546 | 622 | 786 | +5% |
| Total | 555 | 602 | 928 | 563 | 604 | 849 | +0% | 567 | 637 | 810 | +6% |

### Mistral Large 3 675B — `mantle` (us-west-2, `mistral.mistral-large-3-675b-instruct`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 791 | 824 | 1016 | 840 | 895 | 1128 | +9% | 799 | 835 | 926 | +1% |
| Total | 839 | 894 | 1175 | 840 | 895 | 1128 | +0% | 837 | 890 | 989 | -0% |

## NVIDIA Nemotron

### NVIDIA Nemotron Nano 12B — `invoke` (us-west-2, `nvidia.nemotron-nano-12b-v2`)
default: 29/30 ok, served[(unreported):29], 1 fail (TimeoutError×1)  •  flex: 30/30 ok, served[flex:30]  •  priority: 29/30 ok, served[priority:29], 1 fail (Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 531 | 577 | 679 | 529 | 587 | 737 | +2% | 534 | 564 | 635 | -2% |
| Total | 534 | 578 | 693 | 529 | 587 | 737 | +2% | 534 | 564 | 650 | -3% |

### NVIDIA Nemotron Nano 12B — `mantle` (us-west-2, `nvidia.nemotron-nano-12b-v2`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 794 | 813 | 984 | 785 | 819 | 969 | +1% | 788 | 825 | 911 | +1% |
| Total | 798 | 840 | 1000 | 785 | 819 | 969 | -3% | 788 | 841 | 941 | +0% |

### NVIDIA Nemotron Nano 3 30B — `invoke` (us-west-2, `nvidia.nemotron-nano-3-30b`)
default: 29/30 ok, served[(unreported):29], 1 fail (TimeoutError×1)  •  flex: 30/30 ok, served[flex:30]  •  priority: 29/30 ok, served[priority:29], 1 fail (Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 463 | 480 | 579 | 441 | 486 | 629 | +1% | 448 | 488 | 517 | +2% |
| Total | 463 | 480 | 579 | 441 | 486 | 629 | +1% | 448 | 488 | 517 | +2% |

### NVIDIA Nemotron Nano 3 30B — `mantle` (us-west-2, `nvidia.nemotron-nano-3-30b`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 725 | 759 | 879 | 725 | 746 | 1014 | -2% | 728 | 750 | 852 | -1% |
| Total | 725 | 759 | 879 | 725 | 746 | 1014 | -2% | 728 | 750 | 852 | -1% |

### NVIDIA Nemotron Nano 9B — `invoke` (us-west-2, `nvidia.nemotron-nano-9b-v2`)
default: 29/30 ok, served[(unreported):29], 1 fail (TimeoutError×1)  •  flex: 29/30 ok, served[flex:29], 1 fail (Read timeout on endpoint URL×1)  •  priority: 29/30 ok, served[priority:29], 1 fail (Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 696 | 720 | 833 | 1192 | 1396 | 1495 | +94% | 696 | 719 | 791 | -0% |
| Total | 1137 | 1449 | 1574 | 1192 | 1396 | 1495 | -4% | 1272 | 1451 | 1517 | +0% |

### NVIDIA Nemotron Nano 9B — `mantle` (us-west-2, `nvidia.nemotron-nano-9b-v2`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 790 | 809 | 974 | 1537 | 1715 | 1796 | +112% | 798 | 803 | 869 | -1% |
| Total | 1684 | 1723 | 1909 | 1537 | 1715 | 1796 | -0% | 1509 | 1707 | 1807 | -1% |

### NVIDIA Nemotron Super 3 120B — `invoke` (us-west-2, `nvidia.nemotron-super-3-120b`)
default: 30/30 ok, served[(unreported):30]  •  flex: 29/30 ok, served[flex:29], 1 fail (Could not connect to the endpoint URL×1)  •  priority: 29/30 ok, served[priority:29], 1 fail (Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 682 | 755 | 1129 | 686 | 752 | 1340 | -0% | 597 | 632 | 742 | -16% |
| Total | 694 | 762 | 1374 | 686 | 752 | 1340 | -1% | 603 | 644 | 755 | -15% |

### NVIDIA Nemotron Super 3 120B — `mantle` (us-west-2, `nvidia.nemotron-super-3-120b`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 873 | 879 | 1189 | 944 | 995 | 1450 | +13% | 859 | 884 | 1873 | +1% |
| Total | 946 | 988 | 1468 | 944 | 995 | 1450 | +1% | 908 | 941 | 2055 | -5% |

## OpenAI GPT-OSS

### OpenAI GPT OSS 120B — `invoke` (us-west-2, `openai.gpt-oss-120b-1:0`)
default: 29/30 ok, served[(unreported):29], 1 fail (Read timeout on endpoint URL×1)  •  flex: 29/30 ok, served[flex:29], 1 fail (Could not connect to the endpoint URL×1)  •  priority: 28/30 ok, served[priority:28], 2 fail (TimeoutError×1, Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 558 | 602 | 977 | 666 | 785 | 1637 | +30% | 534 | 545 | 689 | -10% |
| Total | 634 | 715 | 1145 | 699 | 812 | 1819 | +14% | 580 | 609 | 758 | -15% |

### OpenAI GPT OSS 120B — `mantle` (us-west-2, `openai.gpt-oss-120b`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 986 | 1026 | 1332 | 1087 | 1144 | 2658 | +12% | 978 | 990 | 1986 | -4% |
| Total | 987 | 1050 | 1413 | 1087 | 1144 | 2658 | +9% | 979 | 990 | 2004 | -6% |

### OpenAI GPT OSS 20B — `invoke` (us-west-2, `openai.gpt-oss-20b-1:0`)
default: 30/30 ok, served[(unreported):30]  •  flex: 28/30 ok, served[flex:28], 2 fail (TimeoutError×2)  •  priority: 29/30 ok, served[priority:29], 1 fail (TimeoutError×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 633 | 767 | 891 | 520 | 778 | 2105 | +2% | 510 | 608 | 882 | -21% |
| Total | 696 | 1589 | 2737 | 560 | 871 | 2127 | -45% | 556 | 652 | 1517 | -59% |

### OpenAI GPT OSS 20B — `mantle` (us-west-2, `openai.gpt-oss-20b`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 978 | 1212 | 1966 | 1053 | 1877 | 3115 | +55% | 938 | 955 | 1454 | -21% |
| Total | 982 | 1294 | 2388 | 1053 | 1877 | 3115 | +45% | 939 | 955 | 1478 | -26% |

### OpenAI GPT OSS Safeguard 120B — `invoke` (us-west-2, `openai.gpt-oss-safeguard-120b`)
default: 30/30 ok, served[(unreported):30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 29/30 ok, served[priority:29], 1 fail (Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 734 | 749 | 792 | 2331 | 2358 | 4342 | +215% | 711 | 738 | 785 | -1% |
| Total | 2812 | 3224 | 4068 | 2496 | 3159 | 4834 | -2% | 2658 | 3537 | 4141 | +10% |

### OpenAI GPT OSS Safeguard 120B — `mantle` (us-west-2, `openai.gpt-oss-safeguard-120b`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 2229 | 2811 | 3889 | 2767 | 2803 | 4435 | -0% | 2047 | 2931 | 4085 | +4% |
| Total | 2984 | 3526 | 4802 | 2812 | 3304 | 4435 | -6% | 2793 | 3678 | 4696 | +4% |

### OpenAI GPT OSS Safeguard 20B — `invoke` (us-west-2, `openai.gpt-oss-safeguard-20b`)
flex: 21/30 ok, served[flex:21], 9 fail (TimeoutError×8, An error occurred (ServiceUnavailableException) when calling×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | NA | NA | NA | 613 | 655 | 1366 | — | NA | NA | NA | — |
| Total | NA | NA | NA | 656 | 711 | 1389 | — | NA | NA | NA | — |

### OpenAI GPT OSS Safeguard 20B — `mantle` (us-west-2, `openai.gpt-oss-safeguard-20b`)
default: 19/30 ok, served[default:19], 11 fail (TimeoutError×11)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 953 | 991 | 7245 | NA | NA | NA | — | NA | NA | NA | — |
| Total | 953 | 991 | 7245 | NA | NA | NA | — | NA | NA | NA | — |

## Qwen

### Qwen3 235B A22B 2507 — `invoke` (us-west-2, `qwen.qwen3-235b-a22b-2507-v1:0`)
default: 30/30 ok, served[(unreported):30]  •  flex: 28/30 ok, served[flex:28], 2 fail (TimeoutError×2)  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 652 | 738 | 1057 | 637 | 792 | 1289 | +7% | 536 | 663 | 842 | -10% |
| Total | 667 | 779 | 1332 | 637 | 792 | 1289 | +2% | 547 | 686 | 1013 | -12% |

### Qwen3 235B A22B 2507 — `mantle` (us-west-2, `qwen.qwen3-235b-a22b-2507`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 801 | 846 | 1045 | 940 | 1047 | 1877 | +24% | 790 | 832 | 1266 | -2% |
| Total | 950 | 1020 | 1262 | 940 | 1047 | 1877 | +3% | 869 | 1013 | 1738 | -1% |

### Qwen3 32B — `invoke` (us-west-2, `qwen.qwen3-32b-v1:0`)
default: 30/30 ok, served[(unreported):30]  •  flex: 29/30 ok, served[flex:29], 1 fail (TimeoutError×1)  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 462 | 509 | 670 | 433 | 493 | 809 | -3% | 441 | 474 | 581 | -7% |
| Total | 462 | 514 | 672 | 433 | 493 | 809 | -4% | 441 | 474 | 582 | -8% |

### Qwen3 32B — `mantle` (us-west-2, `qwen.qwen3-32b`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 726 | 762 | 868 | 704 | 747 | 895 | -2% | 727 | 763 | 1052 | +0% |
| Total | 726 | 762 | 868 | 704 | 747 | 895 | -2% | 727 | 767 | 1052 | +1% |

### Qwen3 Coder 30B A3B — `invoke` (us-west-2, `qwen.qwen3-coder-30b-a3b-v1:0`)
default: 30/30 ok, served[(unreported):30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 29/30 ok, served[priority:29], 1 fail (Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 524 | 646 | 867 | 503 | 562 | 952 | -13% | 488 | 576 | 846 | -11% |
| Total | 524 | 657 | 1232 | 503 | 562 | 952 | -14% | 488 | 576 | 1077 | -12% |

### Qwen3 Coder 30B A3B — `mantle` (us-west-2, `qwen.qwen3-coder-30b-a3b-instruct`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 774 | 850 | 1046 | 772 | 842 | 1943 | -1% | 778 | 843 | 1330 | -1% |
| Total | 774 | 1003 | 1342 | 772 | 842 | 1943 | -16% | 778 | 1025 | 1409 | +2% |

### Qwen3 Coder 480B A35B — `invoke` (us-west-2, `qwen.qwen3-coder-480b-a35b-v1:0`)
default: 30/30 ok, served[(unreported):30]  •  flex: 29/30 ok, served[flex:29], 1 fail (TimeoutError×1)  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 550 | 620 | 862 | 569 | 647 | 1094 | +4% | 548 | 587 | 772 | -5% |
| Total | 550 | 628 | 880 | 569 | 647 | 1094 | +3% | 548 | 593 | 893 | -6% |

### Qwen3 Coder 480B A35B — `mantle` (us-west-2, `qwen.qwen3-coder-480b-a35b-instruct`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 793 | 826 | 921 | 845 | 909 | 1163 | +10% | 793 | 824 | 979 | -0% |
| Total | 850 | 915 | 1127 | 845 | 909 | 1163 | -1% | 820 | 891 | 1116 | -3% |

### Qwen3 Coder Next — `invoke` (us-east-1, `qwen.qwen3-coder-next`)
default: 28/30 ok, served[(unreported):28], 2 fail (An error occurred (ServiceUnavailableException) when calling×1, TimeoutError×1)  •  flex: 27/30 ok, served[flex:27], 3 fail (An error occurred (InternalServerException) when calling the×1, TimeoutError×2)  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 1760 | 3132 | 9011 | 985 | 6272 | 18191 | +100% | 2565 | 5102 | 13175 | +63% |
| Total | 1866 | 3578 | 12799 | 985 | 8821 | 20386 | +147% | 3330 | 5492 | 15819 | +53% |

### Qwen3 Coder Next — `mantle` (us-west-2, `qwen.qwen3-coder-next`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 817 | 838 | 923 | 821 | 837 | 906 | -0% | 811 | 836 | 921 | -0% |
| Total | 817 | 841 | 923 | 821 | 837 | 906 | -0% | 811 | 839 | 928 | -0% |

### Qwen3 Next 80B A3B — `invoke` (us-west-2, `qwen.qwen3-next-80b-a3b`)
default: 30/30 ok, served[(unreported):30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 28/30 ok, served[priority:28], 2 fail (Could not connect to the endpoint URL×2)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 632 | 699 | 785 | 647 | 706 | 806 | +1% | 642 | 702 | 742 | +1% |
| Total | 643 | 711 | 832 | 647 | 706 | 806 | -1% | 652 | 715 | 794 | +1% |

### Qwen3 Next 80B A3B — `mantle` (us-west-2, `qwen.qwen3-next-80b-a3b-instruct`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 806 | 816 | 943 | 937 | 1020 | 1130 | +25% | 809 | 828 | 977 | +1% |
| Total | 960 | 1013 | 1114 | 937 | 1020 | 1130 | +1% | 925 | 1016 | 1156 | +0% |

### Qwen3 VL 235B A22B — `invoke` (us-west-2, `qwen.qwen3-vl-235b-a22b`)
default: 30/30 ok, served[(unreported):30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 29/30 ok, served[priority:29], 1 fail (Could not connect to the endpoint URL×1)

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 847 | 1042 | 2277 | 952 | 1434 | 4987 | +38% | 737 | 810 | 1193 | -22% |
| Total | 935 | 1352 | 2921 | 952 | 1434 | 4987 | +6% | 795 | 883 | 1223 | -35% |

### Qwen3 VL 235B A22B — `mantle` (us-west-2, `qwen.qwen3-vl-235b-a22b-instruct`)
default: 30/30 ok, served[default:30]  •  flex: 30/30 ok, served[flex:30]  •  priority: 30/30 ok, served[priority:30]

| metric | d·p20 | d·p50 | d·p90 | f·p20 | f·p50 | f·p90 | Δp50(flex) | p·p20 | p·p50 | p·p90 | Δp50(priority) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TTFT | 929 | 1389 | 3545 | 1238 | 1702 | 3207 | +23% | 806 | 897 | 1536 | -35% |
| Total | 1186 | 1700 | 4243 | 1238 | 1702 | 3207 | +0% | 1042 | 1161 | 1991 | -32% |
