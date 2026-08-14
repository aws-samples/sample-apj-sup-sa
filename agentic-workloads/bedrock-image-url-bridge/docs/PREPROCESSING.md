# Preprocessing: reducing image token cost before it reaches Bedrock

## The problem

Amazon Bedrock does not preprocess images server-side the way OpenAI and
Anthropic's own APIs do. When you send an image through Bedrock's
Converse API or the `bedrock-mantle` endpoint, the raw base64-encoded
bytes go straight to the model -- no resizing, no tiling, no
compression. A raw 4000x3000 photo costs roughly **30,000 tokens**
through Bedrock; the same photo through OpenAI or Anthropic's native
APIs costs roughly **1,600-2,000 tokens**, because those providers
resize/tile it first.

That gap adds up fast in multi-image workflows: 10-15 images per request
is a common target, but at ~30,000 tokens each, 10 images alone consume
300,000 tokens -- more than a 128K context window can hold before you've
sent a single word of prompt text.

`bridge/preprocess.py` closes that gap client-side: shrink the image to
a token budget *before* it's base64-encoded and sent to a model, using
the same math OpenAI and Anthropic use server-side.

## The two token formulas

**OpenAI patch-based** (`preprocess_patch_mode`): images are divided
into 32x32 pixel patches. Patch count is
`ceil(width/32) * ceil(height/32)`; estimated tokens is
`patch_count * 1.62`. Three detail levels control the resize budget:

| Detail | Budget |
|---|---|
| `low` | Fixed 512x512 resize (may enlarge small images) |
| `high` | Max 2,500 patches OR 2048px max dimension |
| `original` | Max 10,000 patches OR 6000px max dimension |

**Anthropic tile-based** (`preprocess_tile_mode`): images are resized
through a three-stage cascade -- fit within 2048x2048, then cap the
long edge at 1568px, then cap the short edge at 768px -- and divided
into 512x512 tiles. Estimated tokens is `(width * height) / 750`. An
optional `max_token_budget` scales the image down further past the
cascade if you need a tighter cap.

Neither formula's constants are guesses -- they're taken directly from
the token math OpenAI and Anthropic document/exhibit for their own
native APIs; the point of this module is matching that behavior
client-side for Bedrock, not inventing a new one.

## Usage

Standalone, on raw image bytes you already have:

```python
from bridge.preprocess import preprocess_patch_mode, preprocess_tile_mode

result = preprocess_tile_mode(image_bytes, max_token_budget=2000)
print(result.metadata.estimated_tokens)   # e.g. 1050
print(result.metadata.resized_dimensions) # e.g. ImageDimensions(width=768, height=1025)
data_uri = result.to_data_uri()           # ready to drop into a request payload
```

Composed with `resolve_image_urls()` via the `preprocess=` hook, so a
fetched `http(s)://` image is shrunk before it's inlined -- the SSRF
guard, size cap, and format verification in `bridge/core.py` still run
exactly as before; `preprocess=` only transforms the bytes in between:

```python
from bridge import resolve_image_urls
from bridge.preprocess import preprocess_tile_mode

payload = resolve_image_urls(
    payload,
    preprocess=lambda raw: preprocess_tile_mode(raw, max_token_budget=2000).to_bytes(),
)
```

Batch, with a shared token budget across many images
(`preprocess_images`) -- processes every image, and if the batch total
exceeds `max_total_tokens`, reprocesses every image at a proportionally
reduced target so the total fits:

```python
from bridge.preprocess import preprocess_images

processed = preprocess_images(image_byte_list, mode="tile", max_total_tokens=30000)
```

See `examples/preprocess_demo.py` for a runnable before/after comparison
-- no AWS credentials required to see the token savings; set
`AWS_PROFILE` to also make a live `bedrock-mantle` call with the
preprocessed image.

## Live-verified

Against a real 4000x5338 photo, region `us-east-1`:

| Mode | Before | After | Reduction |
|---|---|---|---|
| Tile (Anthropic-style) | 28,469 tokens | 1,050 tokens | 96.3% |
| Patch (OpenAI-style, high detail) | 33,818 tokens | 4,040 tokens | 88.1% |

Both preprocessed images were fed through a real `bedrock-mantle` Chat
Completions call and the model correctly described the photo's actual
content in both cases -- confirming the shrunk image is still visually
usable to the model, not just smaller.

## Design note

`bridge/preprocess.py` is bytes-in, bytes-out only -- it does no URL
fetching, S3 access, or network I/O of its own. All of that already
happens safely inside `bridge/core.py`'s SSRF-guarded download path.
Reimplementing fetch logic here would duplicate (and risk bypassing)
those guards; instead, preprocessing composes with the existing pipeline
purely as a bytes transform via the `preprocess=` hook.

## Attribution

The patch/tile resize algorithms in `bridge/preprocess.py` are ported
from **bedrock-image-preprocessor** (MIT License, Copyright (c) 2026
Aditya Shahani). No repository URL is cited because none was
discoverable in that project's `package.json` or README at the time of
this port.
