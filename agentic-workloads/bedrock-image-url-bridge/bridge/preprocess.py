"""Client-side image preprocessing for Bedrock inference requests.

Amazon Bedrock does not preprocess images server-side the way OpenAI and
Anthropic's native APIs do: a raw 4000x3000 photo sent through Bedrock
costs roughly 30,000 tokens, versus ~1,600-2,000 tokens for the same
photo through OpenAI or Anthropic after their built-in resizing/tiling.
This module replicates that missing preprocessing step client-side:
resize an image down to a token budget *before* it is base64-encoded and
sent to a model, using the same patch/tile math those providers use.

Design decision -- this module is bytes-in, bytes-out only. It does not
fetch URLs, read from S3, or perform any network I/O of its own. All of
that already happens in ``bridge.core`` behind an SSRF guard, a size
cap, and redirect re-validation (see ``resolve_image_urls()`` and its
``preprocess=`` hook). Reimplementing fetch logic here would duplicate
those guards and risk a second, unguarded path to fetch arbitrary URLs.
Callers get bytes from ``resolve_image_urls()`` (or anywhere else they
trust) and pass them into the functions below; the functions here never
reach out to the network themselves.

Preprocessing algorithms ported from bedrock-image-preprocessor
(MIT License, Copyright (c) 2026 Aditya Shahani). No repository URL is
cited here because none was discoverable in that project's package.json
or README at the time of this port.
"""
from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageOps, ImageStat

DetailLevel = Literal["low", "high", "original"]
OutputFormat = Literal["jpeg", "png", "auto"]
PreprocessMode = Literal["patch", "tile"]

_PATCH_TOKEN_MULTIPLIER = 1.62
_PATCH_SIZE = 32
_TILE_SIZE = 512
_ANTHROPIC_TOKENS_PER_PIXEL_DIVISOR = 750
_ANTHROPIC_MAX_SQUARE = 2048
_ANTHROPIC_MAX_LONG_EDGE = 1568
_ANTHROPIC_MAX_SHORT_EDGE = 768
_LOW_DETAIL_FIXED_SIZE = 512
_JPEG_QUALITY_DEFAULT = 85

# detail -> (max_patches, max_dimension); 'low' is handled separately as
# a fixed 512x512 resize (there is no patch-budget check for it).
_PATCH_BUDGETS: dict[str, tuple[int, int]] = {
    "high": (2500, 2048),
    "original": (10000, 6000),
}


@dataclass(frozen=True)
class ImageDimensions:
    """Width/height pair in pixels."""

    width: int
    height: int


@dataclass(frozen=True)
class PatchMetadata:
    """Metadata returned alongside a patch-mode (OpenAI-style) result."""

    original_dimensions: ImageDimensions
    resized_dimensions: ImageDimensions
    patch_count: int
    estimated_tokens: int


@dataclass(frozen=True)
class TileMetadata:
    """Metadata returned alongside a tile-mode (Anthropic-style) result."""

    original_dimensions: ImageDimensions
    resized_dimensions: ImageDimensions
    tile_count: int
    estimated_tokens: int


@dataclass(frozen=True)
class ProcessedImage:
    """Result of preprocessing a single image.

    ``base64`` holds the encoded output image bytes; combine with
    ``mime_type`` to build a ``data:`` URI (see ``to_data_uri()``), or
    decode with ``to_bytes()`` to get raw bytes -- e.g. to pass as the
    return value of the ``preprocess=`` callable accepted by
    ``bridge.core.resolve_image_urls()``.
    """

    base64: str
    mime_type: Literal["image/jpeg", "image/png"]
    metadata: PatchMetadata | TileMetadata

    def to_data_uri(self) -> str:
        """Return a ``data:<mime>;base64,<...>`` URI, built the same way
        ``bridge.core._bytes_to_data_uri`` builds one."""
        return f"data:{self.mime_type};base64,{self.base64}"

    def to_bytes(self) -> bytes:
        """Decode ``base64`` back to raw image bytes."""
        return base64.b64decode(self.base64)


def calculate_patch_count(width: int, height: int) -> int:
    """Port of ``calculatePatchCount``: number of 32x32 patches needed to
    cover a ``width`` x ``height`` image, per OpenAI's patch tokenizer."""
    return math.ceil(width / _PATCH_SIZE) * math.ceil(height / _PATCH_SIZE)


def calculate_scaled_dimensions(
    original: ImageDimensions, max_width: int, max_height: int
) -> ImageDimensions:
    """Port of ``calculateScaledDimensions``: scale ``original`` down
    (never up) to fit within ``max_width`` x ``max_height``, preserving
    aspect ratio."""
    scale_x = max_width / original.width
    scale_y = max_height / original.height
    scale = min(scale_x, scale_y, 1)
    return ImageDimensions(
        width=round(original.width * scale), height=round(original.height * scale)
    )


def _calculate_anthropic_tokens(width: int, height: int) -> int:
    """Port of ``calculateAnthropicTokens``: Anthropic's token formula,
    ``(width * height) / 750``."""
    return round((width * height) / _ANTHROPIC_TOKENS_PER_PIXEL_DIVISOR)


def _calculate_tile_count(width: int, height: int) -> int:
    """Port of ``calculateTileCount``: number of 512x512 tiles needed to
    cover a ``width`` x ``height`` image."""
    return math.ceil(width / _TILE_SIZE) * math.ceil(height / _TILE_SIZE)


def is_photographic(image_bytes: bytes) -> bool:
    """Port of ``isPhotographic``: heuristic for whether an image is a
    photo (JPEG-worthy) versus a diagram/screenshot (PNG-worthy).

    - JPEG source -> True (already a photo format).
    - PNG with an alpha channel -> False (screenshots/diagrams commonly
      carry transparency; photos essentially never do).
    - PNG without alpha -> compute the average per-channel standard
      deviation (normalized to 0-1) across channels; True if > 0.15
      (photos have more per-pixel variance than flat-color UI/diagram
      images).
    - WebP -> True.
    - Anything else (e.g. GIF) -> False.

    Port note: the TS original uses sharp's ``.stats()``, which reports
    per-channel mean/stdev directly from libvips. Pillow has no
    identical API; the closest equivalent is ``PIL.ImageStat.Stat``,
    which computes the same per-channel standard deviation from decoded
    pixel data. The formula (``stdev / 255`` averaged across channels,
    threshold 0.15) is preserved exactly; only the statistics backend
    differs, and results should match sharp's within normal floating
    point / resampling differences.
    """
    img = Image.open(io.BytesIO(image_bytes))
    fmt = (img.format or "").upper()

    if fmt == "JPEG":
        return True

    if fmt == "PNG":
        has_alpha = img.mode in ("RGBA", "LA", "PA") or (
            img.mode == "P" and "transparency" in img.info
        )
        if has_alpha:
            return False

        stat_img = img if img.mode in ("RGB", "L") else img.convert("RGB")
        stat = ImageStat.Stat(stat_img)
        avg_stdev_ratio = sum(s / 255 for s in stat.stddev) / len(stat.stddev)
        return avg_stdev_ratio > 0.15

    return fmt == "WEBP"


def _fit_to_patch_budget(
    dimensions: ImageDimensions, max_patches: int, max_dimension: int
) -> ImageDimensions:
    """Port of ``fitToPatchBudget``: scale down to fit both the max
    pixel dimension and the max patch count, in that order, with a 1%
    safety margin per iteration on the patch-count loop (patch counts
    round up via ceil, so a plain sqrt-scale can still overshoot by one
    row/column of patches without the margin)."""
    width, height = dimensions.width, dimensions.height

    max_dim = max(width, height)
    if max_dim > max_dimension:
        scale = max_dimension / max_dim
        width = round(width * scale)
        height = round(height * scale)

    patches = calculate_patch_count(width, height)
    while patches > max_patches:
        scale = math.sqrt(max_patches / patches) * 0.99
        width = round(width * scale)
        height = round(height * scale)
        patches = calculate_patch_count(width, height)

    return ImageDimensions(width=width, height=height)


def _fit_to_anthropic_constraints(dimensions: ImageDimensions) -> ImageDimensions:
    """Port of ``fitToAnthropicConstraints``: three sequential,
    cascading proportional scale-downs -- fit within 2048x2048, then cap
    the long edge at 1568px, then cap the short edge at 768px. Order
    matters: each stage operates on the output of the previous one."""
    width, height = dimensions.width, dimensions.height

    max_dim = max(width, height)
    if max_dim > _ANTHROPIC_MAX_SQUARE:
        scale = _ANTHROPIC_MAX_SQUARE / max_dim
        width = round(width * scale)
        height = round(height * scale)

    long_edge = max(width, height)
    if long_edge > _ANTHROPIC_MAX_LONG_EDGE:
        scale = _ANTHROPIC_MAX_LONG_EDGE / long_edge
        width = round(width * scale)
        height = round(height * scale)

    short_edge = min(width, height)
    if short_edge > _ANTHROPIC_MAX_SHORT_EDGE:
        scale = _ANTHROPIC_MAX_SHORT_EDGE / short_edge
        width = round(width * scale)
        height = round(height * scale)

    return ImageDimensions(width=width, height=height)


def _fit_to_token_budget(dimensions: ImageDimensions, max_tokens: int) -> ImageDimensions:
    """Port of ``fitToTokenBudget``: scale down further (never up) so
    the Anthropic token estimate for ``dimensions`` does not exceed
    ``max_tokens``."""
    width, height = dimensions.width, dimensions.height
    current_tokens = _calculate_anthropic_tokens(width, height)
    if current_tokens <= max_tokens:
        return ImageDimensions(width=width, height=height)

    scale = math.sqrt(max_tokens / current_tokens)
    return ImageDimensions(width=round(width * scale), height=round(height * scale))


def _load_original_dimensions(image_bytes: bytes) -> ImageDimensions:
    with Image.open(io.BytesIO(image_bytes)) as img:
        return ImageDimensions(width=img.width, height=img.height)


def _encode(
    img: Image.Image, *, use_jpeg: bool, jpeg_quality: int
) -> bytes:
    buf = io.BytesIO()
    if use_jpeg:
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=jpeg_quality)
    else:
        img.save(buf, format="PNG", compress_level=6)
    return buf.getvalue()


def _resolve_use_jpeg(image_bytes: bytes, output_format: OutputFormat) -> bool:
    if output_format == "auto":
        return is_photographic(image_bytes)
    return output_format == "jpeg"


def preprocess_patch_mode(
    image_bytes: bytes,
    *,
    detail: DetailLevel,
    output_format: OutputFormat = "auto",
    jpeg_quality: int = _JPEG_QUALITY_DEFAULT,
) -> ProcessedImage:
    """Port of ``preprocessPatchMode``: resize ``image_bytes`` to fit
    OpenAI's patch-based (32x32 patch) token budget for the given
    ``detail`` level, then re-encode as JPEG or PNG.

    Detail levels:
      - ``"low"``: fixed 512x512 resize (cover-fit, may enlarge).
      - ``"high"``: max 2,500 patches OR 2048px max dimension (inside-fit,
        never enlarges).
      - ``"original"``: max 10,000 patches OR 6000px max dimension
        (inside-fit, never enlarges).

    Args:
        image_bytes: Raw, already-fetched/verified image bytes.
        detail: Patch budget to apply -- ``"low"``, ``"high"``, or
            ``"original"``.
        output_format: ``"auto"`` (detect photo vs. diagram via
            ``is_photographic``), ``"jpeg"``, or ``"png"``.
        jpeg_quality: JPEG quality (1-100) used when the output is JPEG.

    Returns:
        A ``ProcessedImage`` with the resized/re-encoded bytes (base64)
        and ``PatchMetadata`` describing the resize and token estimate.
    """
    original_dimensions = _load_original_dimensions(image_bytes)

    if detail == "low":
        target_dimensions = ImageDimensions(
            width=_LOW_DETAIL_FIXED_SIZE, height=_LOW_DETAIL_FIXED_SIZE
        )
    else:
        max_patches, max_dimension = _PATCH_BUDGETS[detail]
        target_dimensions = _fit_to_patch_budget(
            original_dimensions, max_patches, max_dimension
        )

    needs_resize = (
        target_dimensions.width != original_dimensions.width
        or target_dimensions.height != original_dimensions.height
    )

    with Image.open(io.BytesIO(image_bytes)) as opened:
        img = ImageOps.exif_transpose(opened)

    if needs_resize:
        if detail == "low":
            # cover fit + allow enlargement (sharp: fit:'cover',
            # withoutEnlargement:false) -- scale to fully cover the
            # target box, then center-crop to it exactly.
            img = ImageOps.fit(
                img,
                (target_dimensions.width, target_dimensions.height),
                method=Image.LANCZOS,
                centering=(0.5, 0.5),
            )
        else:
            # inside fit + no enlargement (sharp: fit:'inside',
            # withoutEnlargement:true) -- target_dimensions was computed
            # by _fit_to_patch_budget as a uniform, aspect-preserving
            # down-scale of the original, so a direct resize lands
            # exactly inside the budget without distorting aspect ratio.
            img = img.resize(
                (target_dimensions.width, target_dimensions.height), Image.LANCZOS
            )

    use_jpeg = _resolve_use_jpeg(image_bytes, output_format)
    output_bytes = _encode(img, use_jpeg=use_jpeg, jpeg_quality=jpeg_quality)

    with Image.open(io.BytesIO(output_bytes)) as final_img:
        final_dimensions = ImageDimensions(width=final_img.width, height=final_img.height)

    patch_count = calculate_patch_count(final_dimensions.width, final_dimensions.height)

    metadata = PatchMetadata(
        original_dimensions=original_dimensions,
        resized_dimensions=final_dimensions,
        patch_count=patch_count,
        estimated_tokens=round(patch_count * _PATCH_TOKEN_MULTIPLIER),
    )

    return ProcessedImage(
        base64=base64.b64encode(output_bytes).decode("ascii"),
        mime_type="image/jpeg" if use_jpeg else "image/png",
        metadata=metadata,
    )


def preprocess_tile_mode(
    image_bytes: bytes,
    *,
    max_token_budget: int | None = None,
    output_format: OutputFormat = "auto",
    jpeg_quality: int = _JPEG_QUALITY_DEFAULT,
) -> ProcessedImage:
    """Port of ``preprocessTileMode``: resize ``image_bytes`` to fit
    Anthropic's tile-based token budget, then re-encode as JPEG or PNG.

    Applies, in order: fit within 2048x2048, cap the long edge at
    1568px, cap the short edge at 768px; then, if ``max_token_budget``
    is given, scale down further so the Anthropic token estimate
    (``width * height / 750``) does not exceed it. Never enlarges.

    Args:
        image_bytes: Raw, already-fetched/verified image bytes.
        max_token_budget: Optional cap on estimated tokens for this
            image; scales dimensions down further to hit it.
        output_format: ``"auto"`` (detect photo vs. diagram via
            ``is_photographic``), ``"jpeg"``, or ``"png"``.
        jpeg_quality: JPEG quality (1-100) used when the output is JPEG.

    Returns:
        A ``ProcessedImage`` with the resized/re-encoded bytes (base64)
        and ``TileMetadata`` describing the resize and token estimate.
    """
    original_dimensions = _load_original_dimensions(image_bytes)

    target_dimensions = _fit_to_anthropic_constraints(original_dimensions)
    if max_token_budget:
        target_dimensions = _fit_to_token_budget(target_dimensions, max_token_budget)

    needs_resize = (
        target_dimensions.width != original_dimensions.width
        or target_dimensions.height != original_dimensions.height
    )

    with Image.open(io.BytesIO(image_bytes)) as opened:
        img = ImageOps.exif_transpose(opened)

    if needs_resize:
        # inside fit + no enlargement, same reasoning as patch mode's
        # high/original branch: target_dimensions is already a uniform
        # aspect-preserving down-scale, so a direct resize is correct.
        img = img.resize(
            (target_dimensions.width, target_dimensions.height), Image.LANCZOS
        )

    use_jpeg = _resolve_use_jpeg(image_bytes, output_format)
    output_bytes = _encode(img, use_jpeg=use_jpeg, jpeg_quality=jpeg_quality)

    with Image.open(io.BytesIO(output_bytes)) as final_img:
        final_dimensions = ImageDimensions(width=final_img.width, height=final_img.height)

    tile_count = _calculate_tile_count(final_dimensions.width, final_dimensions.height)

    metadata = TileMetadata(
        original_dimensions=original_dimensions,
        resized_dimensions=final_dimensions,
        tile_count=tile_count,
        estimated_tokens=_calculate_anthropic_tokens(
            final_dimensions.width, final_dimensions.height
        ),
    )

    return ProcessedImage(
        base64=base64.b64encode(output_bytes).decode("ascii"),
        mime_type="image/jpeg" if use_jpeg else "image/png",
        metadata=metadata,
    )


def _reduce_patch_detail(current: DetailLevel, factor: float) -> DetailLevel:
    """Port of ``reducePatchDetail``: step the detail level down when a
    batch redistribution factor is tight."""
    if factor < 0.3:
        return "low"
    if current == "original" and factor < 0.7:
        return "high"
    return current


def _redistribute_token_budget(
    images: list[bytes],
    initial_results: list[ProcessedImage],
    max_total_tokens: int,
    *,
    mode: PreprocessMode,
    detail: DetailLevel,
    output_format: OutputFormat,
    jpeg_quality: int,
) -> list[ProcessedImage]:
    """Port of ``redistributeTokenBudget``: if the batch's total
    estimated tokens exceeds ``max_total_tokens``, reprocess every image
    at a scaled-down target proportional to how far over budget the
    batch is."""
    total_tokens = sum(r.metadata.estimated_tokens for r in initial_results)
    if total_tokens <= max_total_tokens:
        return initial_results

    reduction_factor = max_total_tokens / total_tokens

    results: list[ProcessedImage] = []
    for image_bytes, initial in zip(images, initial_results):
        target_tokens = math.floor(initial.metadata.estimated_tokens * reduction_factor)

        if mode == "tile":
            results.append(
                preprocess_tile_mode(
                    image_bytes,
                    max_token_budget=target_tokens,
                    output_format=output_format,
                    jpeg_quality=jpeg_quality,
                )
            )
        else:
            lower_detail = _reduce_patch_detail(detail, reduction_factor)
            results.append(
                preprocess_patch_mode(
                    image_bytes,
                    detail=lower_detail,
                    output_format=output_format,
                    jpeg_quality=jpeg_quality,
                )
            )

    return results


def preprocess_images(
    images: list[bytes],
    *,
    mode: PreprocessMode,
    detail: DetailLevel = "high",
    max_total_tokens: int | None = None,
    output_format: OutputFormat = "auto",
    jpeg_quality: int = _JPEG_QUALITY_DEFAULT,
) -> list[ProcessedImage]:
    """Port of ``preprocessImages`` + ``redistributeTokenBudget``: batch
    process every image, then, if ``max_total_tokens`` is given and the
    batch total exceeds it, reprocess every image at a proportionally
    reduced target so the total fits (approximately) within budget.

    Args:
        images: Raw, already-fetched/verified image bytes, one per
            image.
        mode: ``"patch"`` (OpenAI-style, via ``preprocess_patch_mode``)
            or ``"tile"`` (Anthropic-style, via ``preprocess_tile_mode``).
        detail: Patch detail level used in patch mode (ignored in tile
            mode). Default ``"high"``.
        max_total_tokens: Optional cap on the sum of estimated tokens
            across the whole batch. In tile mode, an even per-image
            share of the budget is also applied on the first pass;
            in patch mode, the first pass ignores the budget and only
            the redistribution pass (if triggered) reduces detail.
        output_format: ``"auto"``, ``"jpeg"``, or ``"png"``, forwarded
            to each image's processing call.
        jpeg_quality: JPEG quality (1-100), forwarded to each image's
            processing call.

    Returns:
        A list of ``ProcessedImage``, one per input image, in order.
        Empty input returns an empty list.
    """
    if not images:
        return []

    if mode == "patch":
        results = [
            preprocess_patch_mode(
                img, detail=detail, output_format=output_format, jpeg_quality=jpeg_quality
            )
            for img in images
        ]
        if max_total_tokens:
            return _redistribute_token_budget(
                images,
                results,
                max_total_tokens,
                mode=mode,
                detail=detail,
                output_format=output_format,
                jpeg_quality=jpeg_quality,
            )
        return results

    # Tile mode: an even per-image token share is applied up front (if a
    # budget was given), then a redistribution pass tightens further if
    # the batch is still over budget after that first pass.
    per_image_budget = math.floor(max_total_tokens / len(images)) if max_total_tokens else None
    results = [
        preprocess_tile_mode(
            img,
            max_token_budget=per_image_budget,
            output_format=output_format,
            jpeg_quality=jpeg_quality,
        )
        for img in images
    ]

    if max_total_tokens:
        total_tokens = sum(r.metadata.estimated_tokens for r in results)
        if total_tokens > max_total_tokens:
            return _redistribute_token_budget(
                images,
                results,
                max_total_tokens,
                mode=mode,
                detail=detail,
                output_format=output_format,
                jpeg_quality=jpeg_quality,
            )

    return results
