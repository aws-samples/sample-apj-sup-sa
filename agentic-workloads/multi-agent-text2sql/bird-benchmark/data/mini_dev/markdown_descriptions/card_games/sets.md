# Table: sets

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| id |  | integer | unique id identifying this set |
| baseSetSize | base Set Size | integer | The number of cards in the set. |
| block |  | text | The block name the set was in. |
| booster |  | text | A breakdown of possibilities and weights of cards in a booster pack. |
| code |  | text | The set code for the set. |
| isFoilOnly | is Foil Only | integer | If the set is only available in foil. |
| isForeignOnly | is Foreign Only | integer | If the set is available only outside the United States of America. |
| isNonFoilOnly | is Non Foil Only | integer | If the set is only available in non-foil. |
| isOnlineOnly | is Online Only | integer | If the set is only available in online game variations. |
| isPartialPreview | is Partial Preview | integer | If the set is still in preview (spoiled). Preview sets do not have complete data. |
| keyruneCode | keyrune Code | text | The matching Keyrune code for set image icons. |
| mcmId | magic card market id | integer | The Magic Card Marketset identifier. |
| mcmIdExtras | magic card market ID Extras | integer | The split Magic Card Market set identifier if a set is printed in two sets. This identifier represents the second set's identifier. |
| mcmName | magic card market name | text | - |
| mtgoCode | magic the gathering online code | text | The set code for the set as it appears on Magic: The Gathering Online |
| name |  | text | The name of the set. |
| parentCode | parent Code | text | The parent set code for set variations like promotions, guild kits, etc. |
| releaseDate | release Date | date | The release date in ISO 8601 format for the set. |
| tcgplayerGroupId | tcg player Group Id | integer | The group identifier of the set on TCGplayer |
| totalSetSize | total Set Size | integer | The total number of cards in the set, including promotional and related supplemental products but excluding Alchemy modifications - however those cards are included in the set itself. |
| type |  | text | The expansion type of the set. |

## Business Logic & Value Descriptions

### mtgoCode (magic the gathering online code)

- commonsense evidence:
- if the value is null or empty, then it doesn't appear on Magic: The Gathering Online

### type

- "alchemy", "archenemy", "arsenal", "box", "commander", "core", "draft_innovation", "duel_deck", "expansion", "from_the_vault", "funny", "masterpiece", "masters", "memorabilia", "planechase", "premium_deck", "promo", "spellbook", "starter", "token", "treasure_chest", "vanguard"
