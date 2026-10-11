#!/usr/bin/env python3
"""Build the existing SMA Lambda's code-only deployment ZIP."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "build" / "sma-code-only.zip"
FILES = {
    ROOT / "lambdas" / "sma" / "handler.py": "handler.py",
    ROOT / "common" / "hvi_state.py": "common/hvi_state.py",
    ROOT / "common" / "hvi_stt.py": "common/hvi_stt.py",
}

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
with ZipFile(OUTPUT, "w", compression=ZIP_DEFLATED) as archive:
    for source, destination in FILES.items():
        if not source.is_file():
            raise FileNotFoundError(source)
        archive.write(source, destination)

print(OUTPUT)
