#!/usr/bin/env python3
"""Build the dashboard's boto3 Lambda layer zip for Terraform (data "external" in layer.tf).

Reads {"dir": ..., "version": ..., "resolved_before": ...} as JSON on stdin. When <dir>/boto3-<version>-<date>.zip
is missing it installs boto3 == version with uv (wheels only, nothing from the package runs), with every dependency
(botocore, urllib3, ...) resolved as uploaded before resolved_before, and zips it under python/ with fixed file times:
the same inputs give the same files on any machine. The hash Terraform keys on is of those files (paths and bytes),
not of the zip, whose compressed bytes depend on the zlib build: another checkout or OS on the same state does not
replace the layer. Runs on every plan, so a fresh clone, another machine or a deleted
terraform/build/ rebuilds it, while an unchanged plan only hashes the existing zip (about 15 MB: no cached digest,
so the hash always matches the file Terraform uploads). Prints {"zip": path, "sha256": base64 digest}.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess  # nosec B404 - fixed argv, no shell
import sys
import tempfile
import time
import zipfile
from pathlib import Path

q = json.load(sys.stdin)
version = q["version"]
resolved_before = q["resolved_before"]
if not re.fullmatch(r"\d+\.\d+\.\d+", version):
    sys.exit(f"build_layer: boto3 version must look like 1.43.118, got {version!r}")
if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", resolved_before):
    sys.exit(f"build_layer: resolved_before must be a date such as 2026-11-13, got {resolved_before!r}")
root = Path(q["dir"]).resolve()
out = root / f"boto3-{version}-{resolved_before}.zip"
if not out.exists():
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        target = Path(tmp) / "python"
        try:
            r = subprocess.run(["uv", "pip", "install", "--quiet", "--no-cache", "--python-version", "3.13", "--only-binary", ":all:", "--exclude-newer", f"{resolved_before}T00:00:00Z",  # nosec B603 B607
                                "--target", str(target), f"boto3=={version}"], capture_output=True, text=True, check=False)
        except FileNotFoundError:
            sys.exit("build_layer: uv is not installed; it builds the dashboard's boto3 layer (see README, Prerequisites)")
        if r.returncode != 0:
            sys.exit(f"build_layer: uv pip install boto3=={version} (packages uploaded before {resolved_before}) failed: "
                     f"{r.stderr[-500:]}\nA boto3 release newer than that date needs boto3_layer_resolved_before moved on too.")
        part = Path(tmp) / "layer.zip.part"  # inside this run's temp folder: two plans at once never share it
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            # Left out: bytecode, console scripts (bin/: their #! line holds this run's temp path) and RECORD files
            # (they list those scripts' hashes). Lambda imports none of them, and with them out the zip is the same
            # on every machine.
            keep = (p for p in target.rglob("*") if p.is_file() and "__pycache__" not in p.parts
                    and p.relative_to(target).parts[0] != "bin" and p.name != "RECORD")
            for f in sorted(keep, key=lambda p: p.relative_to(tmp).as_posix()):  # one order on every OS
                info = zipfile.ZipInfo(f.relative_to(tmp).as_posix(), date_time=(2020, 1, 1, 0, 0, 0))  # "/" on Windows too
                info.external_attr = 0o644 << 16
                info.create_system = 3  # Unix on every OS (Windows writes 0)
                z.writestr(info, f.read_bytes(), zipfile.ZIP_DEFLATED)
        part.replace(out)
    shutil.rmtree(root / "python", ignore_errors=True)  # the unzipped folder of the previous helper version
# Digest files an r100 build left go; other zips only when unused for a week, so a second workspace's zip (another
# boto3_version in the same checkout, perhaps behind a saved plan) is never deleted by this one's plan. "Unused" is
# the mtime, which every run touches on the zip it uses; a zip another plan removes meanwhile is skipped.
os.utime(out)
for old in root.glob("boto3-*.zip*"):
    try:
        stale = old.name.endswith(".sha256") or time.time() - old.stat().st_mtime > 7 * 86400
    except FileNotFoundError:
        continue
    if old != out and stale:
        old.unlink(missing_ok=True)
for tmp_dir in root.glob("tmp*"):  # a build interrupted mid-install leaves its temp folder; one a day old is dead
    try:
        if tmp_dir.is_dir() and time.time() - tmp_dir.stat().st_mtime > 86400:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    except FileNotFoundError:
        continue
h = hashlib.sha256()
with zipfile.ZipFile(out) as z:
    for name in sorted(z.namelist()):
        h.update(name.encode() + b"\0" + hashlib.sha256(z.read(name)).digest())
sha = base64.b64encode(h.digest()).decode()
print(json.dumps({"zip": str(Path(q["dir"]) / out.name), "sha256": sha}))  # zip as given: relative stays relative in state
