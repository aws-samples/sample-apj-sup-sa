# Table: cards

**Database**: ods

## Columns

| Column | Alias | Type | Description |
|--------|-------|------|-------------|
| id | unique id number identifying the cards | integer | - |
| artist |  | text | The name of the artist that illustrated the card art. |
| asciiName | ascii Name | text | The ASCII(opens new window) (Basic/128) code formatted card name with no special unicode characters. |
| availability |  | text | A list of the card's available printing types. |
| borderColor | border Color | text | The color of the card border. |
| cardKingdomFoilId | card Kingdom Foil Id | text | card Kingdom Foil Id |
| cardKingdomId | card Kingdom Id | text | card Kingdom Id |
| colorIdentity | color Identity | text | A list of all the colors found in manaCost, colorIndicator, and text |
| colorIndicator | color Indicator | text | A list of all the colors in the color indicator (The symbol prefixed to a card's types). |
| colors |  | text | A list of all the colors in manaCost and colorIndicator. |
| convertedManaCost | converted Mana Cost | real | The converted mana cost of the card. Use the manaValue property. |
| duelDeck | duel Deck | text | The indicator for which duel deck the card is in. |
| edhrecRank | rec Rank in edh | integer | The card rank on EDHRec |
| faceConvertedManaCost | face Converted Mana Cost | real | The converted mana cost or mana value for the face for either half or part of the card. |
| faceName | face Name | text | The name on the face of the card. |
| flavorName | flavor Name | text | The promotional card name printed above the true card name on special cards that has no game function. |
| flavorText | flavor Text | text | The italicized text found below the rules text that has no game function. |
| frameEffects | frame Effects | text | The visual frame effects. |
| frameVersion | frame Version | text | The version of the card frame style. |
| hand |  | text | The starting maximum hand size total modifier. |
| hasAlternativeDeckLimit | has Alternative Deck Limit | integer | If the card allows a value other than 4 copies in a deck. |
| hasContentWarning | has Content Warning | integer | If the card marked by Wizards of the Coast (opens new window) for having sensitive content. See this official article (opens new window) for more information. |
| hasFoil | has Foil | integer | If the card can be found in foil |
| hasNonFoil | has Non Foil | integer | If the card can be found in non-foil |
| isAlternative | is Alternative | integer | If the card is an alternate variation to an original printing |
| isFullArt | is Full Art | integer | If the card has full artwork. |
| isOnlineOnly | is Online Only | integer | If the card is only available in online game variations. |
| isOversized | is Oversized | integer | If the card is oversized. |
| isPromo | is Promotion | integer | If the card is a promotional printing. |
| isReprint | is Reprint | integer | If the card has been reprinted. |
| isReserved | is Reserved | integer | If the card is on the Magic: The Gathering Reserved List (opens new window) |
| isStarter | is Starter | integer | If the card is found in a starter deck such as Planeswalker/Brawl decks. |
| isStorySpotlight | is Story Spotlight | integer | If the card is a Story Spotlight card. |
| isTextless | is Text less | integer | If the card does not have a text box. |
| isTimeshifted | is Time shifted | integer | If the card is time shifted |
| keywords |  | text | A list of keywords found on the card. |
| layout |  | text | The type of card layout. For a token card, this will be "token" |
| leadershipSkills | leadership Skills | text | A list of formats the card is legal to be a commander in |
| life |  | text | The starting life total modifier. A plus or minus character precedes an integer. |
| loyalty |  | text | The starting loyalty value of the card. |
| manaCost | mana Cost | text | The mana cost of the card wrapped in brackets for each value. |
| mcmId |  | text | - |
| mcmMetaId |  | text | - |
| mtgArenaId |  | text | - |
| mtgjsonV4Id |  | text | - |
| mtgoFoilId |  | text | - |
| mtgoId |  | text | - |
| multiverseId |  | text | - |
| name |  | text | The name of the card. |
| number |  | text | The number of the card |
| originalReleaseDate |  | text | original Release Date |
| originalText |  | text | original Text |
| originalType |  | text | original Type |
| otherFaceIds |  | text | other Face Ids |
| power |  | text | The power of the card. |
| printings |  | text | A list of set printing codes the card was printed in, formatted in uppercase. |
| promoTypes | promo Types | text | A list of promotional types for a card. |
| purchaseUrls | purchase Urls | text | Links that navigate to websites where the card can be purchased. |
| rarity |  | text | The card printing rarity. |
| scryfallId |  | text | - |
| scryfallIllustrationId |  | text | - |
| scryfallOracleId |  | text | - |
| setCode | Set Code | text | The set printing code that the card is from. |
| side |  | text | The identifier of the card side. |
| subtypes |  | text | A list of card subtypes found after em-dash. |
| supertypes | super types | text | A list of card supertypes found before em-dash. |
| tcgplayerProductId | tcg player ProductId | text | - |
| text |  | text | The rules text of the card. |
| toughness |  | text | The toughness of the card. |
| type |  | text | The type of the card as visible, including any supertypes and subtypes. |
| types |  | text | A list of all card types of the card, including Un‑sets and gameplay variants. |
| uuid |  | text | The universal unique identifier (v5) generated by MTGJSON. Each entry is unique. |
| variations |  | text | - |
| watermark |  | text | The name of the watermark on the card. |

## Business Logic & Value Descriptions

### availability

- "arena", "dreamcast", "mtgo", "paper", "shandalar"

### borderColor (border Color)

- "black", "borderless", "gold", "silver", "white"

### cardKingdomFoilId (card Kingdom Foil Id)

- commonsense evidence:
- cardKingdomFoilId, when paired with cardKingdomId that is not Null, is incredibly powerful.

### cardKingdomId (card Kingdom Id)

- A list of all the colors in the color indicator

### colors

- Some cards may not have values, such as cards with "Devoid" in its text.

### convertedManaCost (converted Mana Cost)

- if value is higher, it means that this card cost more converted mana

### faceConvertedManaCost (face Converted Mana Cost)

- if value is higher, it means that this card cost more converted mana for the face

### flavorName (flavor Name)

- The promotional card name printed above the true card name on special cards that has no game function.

### flavorText (flavor Text)

- The italicized text found below the rules text that has no game function.

### frameEffects (frame Effects)

- "colorshifted", "companion", "compasslanddfc", "devoid", "draft", "etched", "extendedart", "fullart", "inverted", "legendary", "lesson", "miracle", "mooneldrazidfc", "nyxtouched", "originpwdfc", "showcase", "snow", "sunmoondfc", "textless", "tombstone", "waxingandwaningmoondfc"

### frameVersion (frame Version)

- "1993", "1997", "2003", "2015", "future"

### hand

- A + or - character precedes an integer.
- commonsense evidence:
- positive maximum hand size: +1, +2, ....
- negative maximum hand size: -1, ....
- neural maximum hand size: 0....

### hasAlternativeDeckLimit (has Alternative Deck Limit)

- 0: disallow 1: allow

### hasContentWarning (has Content Warning)

- 0: doesn't have 1: has sensitve content or Wizards of the Coast
- commonsense evidence:
- Cards with this property may have missing or degraded properties and values.

### hasFoil (has Foil)

- 0: cannot be found 1: can be found

### hasNonFoil (has Non Foil)

- 0: cannot be found 1: can be found

### isAlternative (is Alternative)

- 0: is not 1: is

### isFullArt (is Full Art)

- 0: doesn't have, 1: has full artwork

### isOnlineOnly (is Online Only)

- 0: is not 1: is

### isOversized (is Oversized)

- 0: is not 1: is

### isPromo (is Promotion)

- 0: is not 1: is

### isReprint (is Reprint)

- 0: has not 1: has not been

### isReserved (is Reserved)

- If the card is on the Magic, it will appear in The Gathering Reserved List

### isStarter (is Starter)

- 0: is not 1: is

### isStorySpotlight (is Story Spotlight)

- 0: is not 1: is

### isTextless (is Text less)

- commonsense evidence:
- 0: has a text box;
- 1: doesn't have a text box;

### isTimeshifted (is Time shifted)

- commonsense evidence:
- If the card is "timeshifted", a feature of certain sets where a card will have a different frameVersion.

### loyalty

- Used only on cards with "Planeswalker" in its types. empty means unkown

### manaCost (mana Cost)

- commonsense evidence:
- manaCost is unconverted mana cost

### name

- Cards with multiple faces, like "Split" and "Meld" cards are given a delimiter.

### originalReleaseDate

- The original release date in ISO 8601(opens new window) format for a promotional card printed outside of a cycle window, such as Secret Lair Drop promotions.

### originalText

- The text on the card as originally printed.

### originalType

- The type of the card as originally printed. Includes any supertypes and subtypes.

### otherFaceIds

- A list of card UUID's to this card's counterparts, such as transformed or melded faces.

### power

- commonsense evidence:
- ∞ means infinite power
- null or * refers to unknown power

### promoTypes (promo Types)

- "arenaleague", "boosterfun", "boxtopper", "brawldeck", "bundle", "buyabox", "convention", "datestamped", "draculaseries", "draftweekend", "duels", "event", "fnm", "gameday", "gateway", "giftbox", "gilded", "godzillaseries", "instore", "intropack", "jpwalker", "judgegift", "league", "mediainsert", "neonink", "openhouse", "planeswalkerstamped", "playerrewards", "playpromo", "premiereshop", "prerelease", "promopack", "release", "setpromo", "stamped", "textured", "themepack", "thick", "tourney", "wizardsplaynetwork"

### side

- Used on cards with multiple faces on the same card.
- commonsense evidence:
- if this value is empty, then it means this card doesn't have multiple faces on the same card.

### supertypes (super types)

- commonsense evidence:
- list of all types should be the union of subtypes and supertypes

### type

- "Artifact", "Card", "Conspiracy", "Creature", "Dragon", "Dungeon", "Eaturecray", "Elemental", "Elite", "Emblem", "Enchantment", "Ever", "Goblin", "Hero", "Instant", "Jaguar", "Knights", "Land", "Phenomenon", "Plane", "Planeswalker", "Scariest", "Scheme", "See", "Sorcery", "Sticker", "Summon", "Token", "Tribal", "Vanguard", "Wolf", "You'll", "instant"
