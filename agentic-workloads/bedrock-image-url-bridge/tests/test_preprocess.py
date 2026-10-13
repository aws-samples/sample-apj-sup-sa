"""Unit tests for bridge.preprocess.

No network, no AWS calls. Fixture images are generated in-memory with
Pillow, mirroring bedrock-image-preprocessor's own test fixtures
(tests/helpers.ts: createTestImage / createScreenshotImage) ported to
Python equivalents.
"""
from __future__ import annotations

import io
import math
import unittest

from PIL import Image, ImageDraw

from bridge.preprocess import (
    ImageDimensions,
    calculate_patch_count,
    calculate_scaled_dimensions,
    is_photographic,
    preprocess_images,
    preprocess_patch_mode,
    preprocess_tile_mode,
)


def _make_test_image(width: int, height: int, fmt: str = "jpeg") -> bytes:
    """Port of the reference repo's createTestImage(): a flat mid-gray
    image at the given size/format. For 'jpeg' this exercises the
    format-based is_photographic() branch (True unconditionally); it is
    not meant to look like a real photo."""
    img = Image.new("RGB", (width, height), color=(128, 128, 128))
    buf = io.BytesIO()
    if fmt == "jpeg":
        img.save(buf, format="JPEG", quality=80)
    else:
        img.save(buf, format="PNG")
    return buf.getvalue()


def _make_screenshot_image(width: int, height: int) -> bytes:
    """Port of the reference repo's createScreenshotImage(): a
    low-variance, flat-color PNG with a couple of solid rectangles --
    the kind of image is_photographic() should classify as False."""
    img = Image.new("RGB", (width, height), color=(245, 245, 245))
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 210, 40], fill=(200, 200, 200))
    draw.rectangle([10, 50, width - 10, height - 10], fill=(250, 250, 250), outline=(220, 220, 220))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_noisy_png(width: int, height: int) -> bytes:
    """A PNG (no alpha) with high per-pixel variance -- should be
    classified as photographic (True) by the stdev heuristic even
    though it's not literally a JPEG."""
    import random

    random.seed(42)
    img = Image.new("RGB", (width, height))
    pixels = [
        (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        for _ in range(width * height)
    ]
    img.putdata(pixels)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_rgba_png(width: int, height: int) -> bytes:
    img = Image.new("RGBA", (width, height), color=(10, 20, 30, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class CalculatePatchCountTests(unittest.TestCase):
    def test_calculates_correct_patch_count(self):
        self.assertEqual(calculate_patch_count(512, 512), 256)  # 16 * 16
        self.assertEqual(calculate_patch_count(1024, 1024), 1024)  # 32 * 32
        self.assertEqual(calculate_patch_count(33, 33), 4)  # ceil(33/32)^2 = 2*2

    def test_handles_non_divisible_dimensions(self):
        self.assertEqual(calculate_patch_count(100, 100), 16)  # ceil(100/32)^2 = 4*4


class CalculateScaledDimensionsTests(unittest.TestCase):
    def test_scales_down_to_fit_within_bounds(self):
        result = calculate_scaled_dimensions(ImageDimensions(4000, 3000), 2048, 2048)
        self.assertLessEqual(result.width, 2048)
        self.assertLessEqual(result.height, 2048)

    def test_preserves_aspect_ratio(self):
        result = calculate_scaled_dimensions(ImageDimensions(4000, 2000), 2048, 2048)
        self.assertAlmostEqual(result.width / result.height, 2.0, places=1)

    def test_does_not_upscale(self):
        result = calculate_scaled_dimensions(ImageDimensions(800, 600), 2048, 2048)
        self.assertEqual(result.width, 800)
        self.assertEqual(result.height, 600)


class IsPhotographicTests(unittest.TestCase):
    def test_jpeg_is_always_photographic(self):
        image = _make_test_image(200, 150, "jpeg")
        self.assertTrue(is_photographic(image))

    def test_png_with_alpha_is_not_photographic(self):
        image = _make_rgba_png(200, 150)
        self.assertFalse(is_photographic(image))

    def test_flat_screenshot_png_is_not_photographic(self):
        image = _make_screenshot_image(400, 300)
        self.assertFalse(is_photographic(image))

    def test_high_variance_png_is_photographic(self):
        image = _make_noisy_png(200, 150)
        self.assertTrue(is_photographic(image))


class PreprocessPatchModeTests(unittest.TestCase):
    def test_low_detail_resizes_any_image_to_512x512(self):
        image = _make_test_image(4000, 3000)
        result = preprocess_patch_mode(image, detail="low")
        self.assertEqual(result.metadata.resized_dimensions.width, 512)
        self.assertEqual(result.metadata.resized_dimensions.height, 512)
        self.assertEqual(result.metadata.patch_count, 256)

    def test_low_detail_enlarges_small_images(self):
        # withoutEnlargement=False for 'low' in the TS source -- small
        # images still get upscaled to the fixed 512x512 target.
        image = _make_test_image(100, 80)
        result = preprocess_patch_mode(image, detail="low")
        self.assertEqual(result.metadata.resized_dimensions.width, 512)
        self.assertEqual(result.metadata.resized_dimensions.height, 512)

    def test_low_detail_produces_low_token_count(self):
        image = _make_test_image(4000, 3000)
        result = preprocess_patch_mode(image, detail="low")
        self.assertLess(result.metadata.estimated_tokens, 500)

    def test_high_detail_constrains_to_2048px_max_dimension(self):
        image = _make_test_image(4000, 3000)
        result = preprocess_patch_mode(image, detail="high")
        dims = result.metadata.resized_dimensions
        self.assertLessEqual(max(dims.width, dims.height), 2048)

    def test_high_detail_constrains_to_2500_patches(self):
        image = _make_test_image(4000, 3000)
        result = preprocess_patch_mode(image, detail="high")
        self.assertLessEqual(result.metadata.patch_count, 2500)

    def test_high_detail_keeps_small_images_unchanged(self):
        image = _make_test_image(800, 600)
        result = preprocess_patch_mode(image, detail="high")
        self.assertEqual(result.metadata.resized_dimensions.width, 800)
        self.assertEqual(result.metadata.resized_dimensions.height, 600)

    def test_high_detail_reasonable_token_count(self):
        image = _make_test_image(2000, 1500)
        result = preprocess_patch_mode(image, detail="high")
        self.assertLess(result.metadata.estimated_tokens, 5000)

    def test_original_detail_allows_up_to_6000px(self):
        image = _make_test_image(5000, 4000)
        result = preprocess_patch_mode(image, detail="original")
        dims = result.metadata.resized_dimensions
        self.assertLessEqual(max(dims.width, dims.height), 6000)

    def test_original_detail_constrains_to_10000_patches(self):
        image = _make_test_image(5000, 4000)
        result = preprocess_patch_mode(image, detail="original")
        self.assertLessEqual(result.metadata.patch_count, 10000)

    def test_original_detail_keeps_small_images_unchanged(self):
        image = _make_test_image(800, 600)
        result = preprocess_patch_mode(image, detail="original")
        self.assertEqual(result.metadata.resized_dimensions.width, 800)
        self.assertEqual(result.metadata.resized_dimensions.height, 600)

    def test_outputs_jpeg_for_photographic_images_in_auto_mode(self):
        image = _make_test_image(1000, 800, "jpeg")
        result = preprocess_patch_mode(image, detail="high")
        self.assertEqual(result.mime_type, "image/jpeg")

    def test_outputs_png_for_screenshot_like_images_in_auto_mode(self):
        image = _make_screenshot_image(1024, 768)
        result = preprocess_patch_mode(image, detail="high")
        self.assertEqual(result.mime_type, "image/png")

    def test_respects_forced_output_format(self):
        image = _make_test_image(1000, 800, "jpeg")
        result = preprocess_patch_mode(image, detail="high", output_format="png")
        self.assertEqual(result.mime_type, "image/png")

    def test_returns_complete_metadata(self):
        image = _make_test_image(3000, 2000)
        result = preprocess_patch_mode(image, detail="high")
        self.assertEqual(result.metadata.original_dimensions.width, 3000)
        self.assertEqual(result.metadata.original_dimensions.height, 2000)
        self.assertGreater(result.metadata.resized_dimensions.width, 0)
        self.assertGreater(result.metadata.resized_dimensions.height, 0)
        self.assertGreater(result.metadata.patch_count, 0)
        self.assertGreater(result.metadata.estimated_tokens, 0)

    def test_base64_roundtrips_to_valid_image(self):
        image = _make_test_image(1000, 800)
        result = preprocess_patch_mode(image, detail="high")
        decoded = result.to_bytes()
        with Image.open(io.BytesIO(decoded)) as img:
            img.verify()


class PreprocessTileModeTests(unittest.TestCase):
    def test_constrains_longest_edge_to_1568px(self):
        image = _make_test_image(4000, 3000)
        result = preprocess_tile_mode(image)
        dims = result.metadata.resized_dimensions
        self.assertLessEqual(max(dims.width, dims.height), 1568)

    def test_preserves_aspect_ratio(self):
        image = _make_test_image(4000, 2000)
        result = preprocess_tile_mode(image)
        dims = result.metadata.resized_dimensions
        self.assertAlmostEqual(dims.width / dims.height, 2.0, places=0)

    def test_keeps_small_images_unchanged(self):
        image = _make_test_image(400, 300)
        result = preprocess_tile_mode(image)
        self.assertEqual(result.metadata.resized_dimensions.width, 400)
        self.assertEqual(result.metadata.resized_dimensions.height, 300)

    def test_calculates_tile_count_correctly(self):
        image = _make_test_image(1024, 1024)
        result = preprocess_tile_mode(image)
        dims = result.metadata.resized_dimensions
        expected_tiles = math.ceil(dims.width / 512) * math.ceil(dims.height / 512)
        self.assertEqual(result.metadata.tile_count, expected_tiles)

    def test_calculates_estimated_tokens_using_anthropic_formula(self):
        image = _make_test_image(1000, 750)
        result = preprocess_tile_mode(image)
        dims = result.metadata.resized_dimensions
        expected_tokens = round((dims.width * dims.height) / 750)
        self.assertEqual(result.metadata.estimated_tokens, expected_tokens)

    def test_three_stage_cascade_all_stages_engage(self):
        # Very large, very wide image: 10000x1000 (ratio 10:1).
        # Stage 1 (fit within 2048x2048): scale = 2048/10000 = 0.2048
        #   -> 2048 x 205 (round)
        # Stage 2 (long edge <= 1568): long edge is already 2048 > 1568
        #   -> scale = 1568/2048 = 0.765625 -> 1568 x 157
        # Stage 3 (short edge <= 768): short edge 157 < 768, no-op.
        image = _make_test_image(10000, 1000)
        result = preprocess_tile_mode(image)
        dims = result.metadata.resized_dimensions
        self.assertEqual(dims.width, 1568)
        self.assertLessEqual(dims.height, 768)
        self.assertLessEqual(max(dims.width, dims.height), 1568)

    def test_three_stage_cascade_short_edge_engages(self):
        # A large near-square image should get clipped by stage 1 (2048
        # cap) but since it's square, long/short edges are equal and
        # under 1568/768 after stage 1 scaling down further via short
        # edge is only relevant for non-square; use a moderately wide
        # image where stage 3 actually applies after stage 1+2.
        # 3000x2500: stage1 scale=2048/3000=0.6827 -> 2048x1707
        # stage2: long edge 2048>1568 -> scale=1568/2048=0.765625 -> 1568x1307
        # stage3: short edge 1307 > 768 -> scale=768/1307=0.5877 -> 921x768
        image = _make_test_image(3000, 2500)
        result = preprocess_tile_mode(image)
        dims = result.metadata.resized_dimensions
        self.assertEqual(min(dims.width, dims.height), 768)
        self.assertLessEqual(max(dims.width, dims.height), 1568)

    def test_respects_max_token_budget(self):
        image = _make_test_image(3000, 2000)
        result = preprocess_tile_mode(image, max_token_budget=1000)
        self.assertLessEqual(result.metadata.estimated_tokens, 1000)

    def test_does_not_upscale_when_under_budget(self):
        image = _make_test_image(400, 300)
        result = preprocess_tile_mode(image, max_token_budget=10000)
        self.assertEqual(result.metadata.resized_dimensions.width, 400)
        self.assertEqual(result.metadata.resized_dimensions.height, 300)

    def test_achieves_target_token_count_with_budget(self):
        image = _make_test_image(4000, 3000)
        result = preprocess_tile_mode(image, max_token_budget=2000)
        self.assertLessEqual(result.metadata.estimated_tokens, 2000)


class PreprocessImagesBatchTests(unittest.TestCase):
    def test_processes_multiple_images_in_patch_mode(self):
        images = [_make_test_image(2000, 1500) for _ in range(5)]
        results = preprocess_images(images, mode="patch", detail="high")
        self.assertEqual(len(results), 5)
        for r in results:
            self.assertTrue(r.base64)
            self.assertLessEqual(r.metadata.patch_count, 2500)

    def test_processes_multiple_images_in_tile_mode(self):
        images = [_make_test_image(3000, 2000) for _ in range(5)]
        results = preprocess_images(images, mode="tile")
        self.assertEqual(len(results), 5)
        for r in results:
            dims = r.metadata.resized_dimensions
            self.assertLessEqual(max(dims.width, dims.height), 1568)

    def test_under_budget_returns_unmodified_results(self):
        images = [_make_test_image(200, 150) for _ in range(3)]
        results = preprocess_images(images, mode="tile", max_total_tokens=1_000_000)
        for r in results:
            self.assertEqual(r.metadata.resized_dimensions.width, 200)
            self.assertEqual(r.metadata.resized_dimensions.height, 150)

    def test_over_budget_triggers_redistribution(self):
        images = [_make_test_image(3000, 2000) for _ in range(10)]
        max_total_tokens = 20000
        results = preprocess_images(images, mode="tile", max_total_tokens=max_total_tokens)
        total_tokens = sum(r.metadata.estimated_tokens for r in results)
        self.assertLessEqual(total_tokens, max_total_tokens * 1.1)  # 10% tolerance

    def test_handles_many_images_within_token_budget(self):
        images = [_make_test_image(4000, 3000) for _ in range(15)]
        results = preprocess_images(images, mode="tile", max_total_tokens=30000)
        self.assertEqual(len(results), 15)
        total_tokens = sum(r.metadata.estimated_tokens for r in results)
        self.assertLessEqual(total_tokens, 33000)
        for r in results:
            self.assertLessEqual(r.metadata.estimated_tokens, 3000)

    def test_empty_input_returns_empty_list(self):
        results = preprocess_images([], mode="patch", detail="high")
        self.assertEqual(results, [])

    def test_downgrades_patch_detail_when_budget_is_tight(self):
        images = [_make_test_image(3000, 2000) for _ in range(10)]
        results = preprocess_images(
            images, mode="patch", detail="high", max_total_tokens=5000
        )
        self.assertEqual(len(results), 10)
        for r in results:
            self.assertLess(r.metadata.estimated_tokens, 1000)


if __name__ == "__main__":
    unittest.main()
