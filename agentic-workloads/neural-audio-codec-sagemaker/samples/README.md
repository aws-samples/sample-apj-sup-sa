# Audio samples

These clips demonstrate the bitrate–quality trade-off in the Mimi codec. The same
source clip is encoded and reconstructed at 1, 2, 4, and 8 codebooks. A single
codebook produces unintelligible output; speech becomes clear at two to four
codebooks, and the eighth adds refinement with diminishing returns.

| File | Codebooks | Bitrate | Tokens/sec |
|---|---|---|---|
| `mimi_1codebook_0.14kbps.wav` | 1 | 0.14 kbps | 12.5 |
| `mimi_2codebook_0.28kbps.wav` | 2 | 0.28 kbps | 25 |
| `mimi_4codebook_0.55kbps.wav` | 4 | 0.55 kbps | 50 |
| `mimi_8codebook_1.10kbps.wav` | 8 | 1.10 kbps | 100 |

`source_poem.wav` is the input clip the reconstructions are made from.

## Provenance

The source audio was synthesized locally with the macOS `say` text-to-speech tool
from the poem "A Dream Within a Dream" by Edgar Allan Poe (1849), which is in the
public domain. No third-party or personal voice recordings are included.
