# <Topic>.deck.md — the deck plan format

One markdown file = one deck. Front matter for deck-level metadata, then one
`## Slide N` section per slide. The user edits this file directly; the build
phase compiles it 1:1 — nothing in the deck that isn't in the plan.

## Front matter

Identity fields plus the **narrative contract** — `objective`, `audience`,
`desired_action` are REQUIRED (they are how the plan and the QA pass judge
whether a slide earns its place); `thesis` and `duration_minutes` optional.

```yaml
---
title: Claude Managed Agents on AWS     # doc.title + {{title}} field
author: Jungseob Shin                   # doc.meta.author
company: AWS                            # doc.meta.company
event: AWS Summit Seoul                 # doc.meta.event ({{event}} field)
language: ko                            # slide copy language
output: ~/Documents/claude_managed_agents.bento.html
audience: Startup CTOs and platform engineers        # REQUIRED
objective: Managed Agents 운영 모델의 차이를 이해시킨다   # REQUIRED — what changes in their head
desired_action: PoC 후보 워크로드를 한 개 선정한다        # REQUIRED — what they DO next
thesis: Agent 운영 복잡도는 모델보다 실행 인프라에서 발생한다  # optional one-line argument
duration_minutes: 25                                  # optional
---
```

## Slide section

```markdown
## Slide 1 · Cover
layout: layout-aws-cover-violet
transition: fade            # fade | morph | none (morph = shares ids w/ prev)
title: Claude Managed Agents on AWS
kicker: AWS × ANTHROPIC
name: Jungseob Shin
role: Sr. Solutions Architect, Startup · AWS
notes: |
  오프닝 30초. AWS와 Anthropic 파트너십 언급 후 오늘의 주제 소개.
```

Slot keys map to the layout's named slots (see layouts.md). Common keys:
`title` `kicker` `subtitle` `body` `name` `role` `contact` `quote` `attr`.
Layout-specific content uses structured lists:

```markdown
## Slide 6 · When to use
layout: layout-aws-bulleted
title: Claude Managed Agents를 사용해야 하는 경우
bullets:
  - **장시간 실행** : 몇 분–몇 시간 도구 호출과 함께 실행되는 작업
  - **클라우드 인프라** : 사전 설치 패키지와 네트워크를 갖춘 보안 샌드박스
notes: |
  각 항목을 실제 고객 사례와 연결해 설명.
```

```markdown
## Slide 7 · Who runs what
layout: custom               # nothing fits → describe the composition
description: |
  비교표: 좌측 행 라벨(Claude 모델/Agent loop/도구 실행…), 컬럼 3개
  (Messages API / Agent SDK / Managed Agents). 셀 값 "You build + run it"
  vs "Anthropic runs it"(indigo bold). 그라데이션 헤더, 지브라 행.
table:
  columns: [", Messages API, Agent SDK, Managed Agents]
  rows:
    - [Claude 모델, Anthropic*, Anthropic*, Anthropic*]
    - [도구 실행 + 샌드박스, You, You, Anthropic*]
notes: |
  단계가 올라갈수록 Anthropic 운영 레이어가 늘어난다는 점 강조.
```

```markdown
## Slide 3 · Messages API
layout: layout-aws-full-photo        # or pic-caption
image: ./assets/slide-messages-api.webp   # embedded as data URI at build
title: (이미지가 전체 — 텍스트 슬롯 비움)
notes: |
  모델은 제공되지만 나머지는 직접 구축해야 함을 설명.
```

Other per-slide keys:

- `purpose:` — one line: why this slide exists (which part of the
  objective it serves). Optional but recommended on content slides; a
  slide whose purpose you can't state is a cut candidate.
- `takeaway:` — the one sentence the audience should remember. Optional;
  when present, the slide TITLE should usually just BE this sentence.
- `stat:` / `stats:` — for stat-donut (`value`, `claim`, `source`) and
  dual-stat (list of two)
- `columns:` — list of `{head, body}` (three-col/four-col) or
  `{head, bullets}` (compare-cards)
- `agenda:` — list of row labels (agenda layout; numbering is automatic)
- `code:` — fenced block for the code layout
- `quote:` + `attr:` (+ `blurb:` for customer quote)
- `section:` — eyebrow text for section headers ("SECTION 02")
- `fx:` — hints like `stagger bullets`, `count-up`, `ken-burns`
- `morph-with-prev: true` — sugar for transition: morph + instruct the
  builder to align shared element ids with the previous slide
- `chart:` — for a data slide: `type` (bar|line|pie|scatter), `categories`,
  `series: [{name, data: [numbers]}]` — series data must be plain numbers.
  Pick type by the data's story: time → line, category magnitude → bar,
  composition → pie (≤5 slices), two-variable relation → scatter. Title =
  the chart's conclusion; include units/period; add `source:`.
- `state-of: <slide heading>` — this slide is a hidden drill-down variant
  of that slide, reached only by clicking; pair with `link:` below
- `link: <slot> -> <slide heading>` — clicking that element jumps to a slide
  (how state slides are reached)

## Rules

- `notes:` is REQUIRED on every slide — write real speaker notes, not
  placeholders.
- Slide count target and section breaks should be visible from the `##`
  headings alone.
- Anything ambiguous belongs in the plan as a comment (`> TODO:`), not
  silently invented at build time.
