# Sample audio

`sample.wav` is a short English clip used by the quickstart and `common/invoke.py`.

Generate your own test clips on macOS:

```bash
say -o clip.aiff "Your sentence here."
afconvert clip.aiff -d LEI16 -f WAVE clip.wav
```

Whisper accepts wav, mp3, flac, m4a, ogg, and more. For Hindi/Hinglish testing,
record or synthesize a clip and pass `--language hi` (see `common/invoke.py`).
