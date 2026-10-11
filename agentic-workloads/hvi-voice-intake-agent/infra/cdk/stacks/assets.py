"""No-Docker Lambda asset bundling for the HVI CDK app.

Custom-resource handlers need their ``handler.py`` plus a few vendored
``seed/`` JSON files staged into the Lambda asset. The HARD SAFETY constraint
for this app is that synthesis — including the asset staging that
``Template.from_stack`` triggers — must run **in-process with no Docker and no
network**. CDK only shells out to Docker when a ``BundlingOptions`` is supplied
*and* the local bundler declines; so we supply an :class:`ILocalBundling`
implementation that copies the required files into the asset output directory
and returns ``True``, meaning Docker is never invoked.

The copied set is deterministic (same inputs -> same asset hash), so the
seed-file fingerprint we pass as a custom-resource property is what actually
drives re-seeding on change, not the asset hash.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import jsii
import aws_cdk as cdk
from aws_cdk import aws_lambda as lambda_


@jsii.implements(cdk.ILocalBundling)
class _VendoredLocalBundling:
    """Copies a fixed set of source files into the asset output dir, no Docker."""

    def __init__(self, sources: dict[str, str]) -> None:
        # dest filename (inside the asset) -> absolute source path
        self._sources = sources

    def try_bundle(self, output_dir: str, *, image, **_kwargs) -> bool:  # noqa: ANN001
        for dest, src in self._sources.items():
            target = os.path.join(output_dir, dest)
            if os.path.isdir(src):
                # Vendor a whole package directory (e.g. ``requests``) by copy —
                # a plain filesystem copy, so still no Docker and no network.
                shutil.copytree(src, target, dirs_exist_ok=True)
            else:
                shutil.copy(src, target)
        return True


def vendored_asset_code(
    handler_dir: Path,
    extra_files: dict[str, str] | None = None,
) -> lambda_.Code:
    """Build a Lambda ``Code`` asset from ``handler_dir`` plus ``extra_files``.

    ``handler_dir`` must contain ``handler.py``. ``extra_files`` maps a
    destination name (as the handler will reference it, alongside ``handler.py``)
    to an absolute source path — used to vendor the ``seed/`` JSON a seed
    handler loads at deploy time, a reused helper module (``opensearch_admin.py``),
    or a pure-Python dependency *directory* (e.g. ``requests``). A source that is
    a directory is copied recursively. Bundling is performed locally (no Docker).
    """
    handler_dir = handler_dir.resolve()
    sources: dict[str, str] = {"handler.py": str(handler_dir / "handler.py")}
    for dest, src in (extra_files or {}).items():
        sources[dest] = str(Path(src).resolve())

    return lambda_.Code.from_asset(
        path=str(handler_dir),
        bundling=cdk.BundlingOptions(
            # Required by BundlingOptions, but never used: the local bundler
            # above returns True, so Docker is not invoked.
            image=lambda_.Runtime.PYTHON_3_12.bundling_image,
            local=_VendoredLocalBundling(sources),
            command=["/bin/sh", "-c", "false"],
        ),
    )
