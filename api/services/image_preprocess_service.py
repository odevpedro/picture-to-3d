"""
Input image sanitization for image-to-3D generation.

This prepares a stable, centered, square RGB image for TripoSR while also
writing a preview image and diagnostics for debugging generation quality.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance, ImageOps


@dataclass
class PreprocessResult:
    image: Image.Image
    preview_filename: str | None
    diagnostics: dict[str, Any]


class ImagePreprocessService:
    def __init__(
        self,
        *,
        target_size: int = 512,
        foreground_ratio: float = 0.84,
        alpha_threshold: int = 8,
        sharpen_factor: float = 1.04,
        contrast_factor: float = 1.03,
    ):
        self.target_size = target_size
        self.foreground_ratio = foreground_ratio
        self.alpha_threshold = alpha_threshold
        self.sharpen_factor = sharpen_factor
        self.contrast_factor = contrast_factor

    def prepare(
        self,
        image_bytes: bytes,
        *,
        preview_dir: Path | None = None,
        preview_stem: str | None = None,
    ) -> PreprocessResult:
        import rembg

        warnings: list[str] = []
        source = Image.open(io.BytesIO(image_bytes))
        original_size = source.size
        source = ImageOps.exif_transpose(source)
        transposed_size = source.size

        if min(transposed_size) < 256:
            warnings.append("input_resolution_low")

        rgba = source.convert("RGBA")
        removed = rembg.remove(rgba).convert("RGBA")
        alpha = np.asarray(removed.getchannel("A"), dtype=np.uint8)
        raw_mask = alpha > self.alpha_threshold
        clean_mask = self._clean_mask(raw_mask)

        if not clean_mask.any():
            warnings.append("foreground_mask_empty")
            removed = rgba
            alpha = np.full((removed.height, removed.width), 255, dtype=np.uint8)
            clean_mask = np.ones_like(alpha, dtype=bool)

        cleaned_alpha = np.where(clean_mask, alpha, 0).astype(np.uint8)
        removed.putalpha(Image.fromarray(cleaned_alpha).convert("L"))

        bbox = self._mask_bbox(clean_mask)
        if bbox is None:
            bbox = (0, 0, removed.width, removed.height)

        if self._bbox_touches_edges(bbox, removed.size):
            warnings.append("foreground_touches_image_edge")

        crop = removed.crop(bbox)
        sanitized_rgba = self._place_on_square_canvas(crop)
        sanitized_rgb = self._composite_on_white(sanitized_rgba)
        sanitized_rgb = self._enhance(sanitized_rgb)

        preview_filename = None
        if preview_dir and preview_stem:
            preview_dir.mkdir(parents=True, exist_ok=True)
            preview_filename = f"{preview_stem}_sanitized.png"
            sanitized_rgb.save(preview_dir / preview_filename, format="PNG", optimize=True)

        bbox_width = bbox[2] - bbox[0]
        bbox_height = bbox[3] - bbox[1]
        mask_area = int(clean_mask.sum())
        image_area = int(clean_mask.shape[0] * clean_mask.shape[1])
        diagnostics = {
            "original_size": list(original_size),
            "transposed_size": list(transposed_size),
            "foreground_bbox": list(bbox),
            "foreground_size": [bbox_width, bbox_height],
            "mask_area_ratio": round(mask_area / image_area, 4) if image_area else 0,
            "output_size": [sanitized_rgb.width, sanitized_rgb.height],
            "foreground_ratio": self.foreground_ratio,
            "warnings": warnings,
        }

        return PreprocessResult(
            image=sanitized_rgb,
            preview_filename=preview_filename,
            diagnostics=diagnostics,
        )

    def _clean_mask(self, mask: np.ndarray) -> np.ndarray:
        if not mask.any():
            return mask

        try:
            from scipy import ndimage
        except Exception:
            return mask

        labeled, count = ndimage.label(mask)
        if count == 0:
            return mask

        sizes = np.bincount(labeled.ravel())
        sizes[0] = 0
        min_component_area = max(64, int(mask.size * 0.0005))
        keep = np.where(sizes >= min_component_area)[0]
        if keep.size == 0:
            keep = np.array([int(sizes.argmax())])

        cleaned = np.isin(labeled, keep)
        cleaned = ndimage.binary_fill_holes(cleaned)
        cleaned = ndimage.binary_closing(cleaned, structure=np.ones((3, 3), dtype=bool))
        return cleaned.astype(bool)

    def _mask_bbox(self, mask: np.ndarray) -> tuple[int, int, int, int] | None:
        ys, xs = np.where(mask)
        if xs.size == 0 or ys.size == 0:
            return None
        return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)

    def _bbox_touches_edges(
        self,
        bbox: tuple[int, int, int, int],
        image_size: tuple[int, int],
        *,
        margin_px: int = 2,
    ) -> bool:
        width, height = image_size
        left, top, right, bottom = bbox
        return (
            left <= margin_px
            or top <= margin_px
            or right >= width - margin_px
            or bottom >= height - margin_px
        )

    def _place_on_square_canvas(self, image: Image.Image) -> Image.Image:
        target = self.target_size
        max_foreground_side = max(1, int(target * self.foreground_ratio))
        scale = min(max_foreground_side / image.width, max_foreground_side / image.height)
        new_size = (
            max(1, int(round(image.width * scale))),
            max(1, int(round(image.height * scale))),
        )

        resized = image.resize(new_size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (target, target), (255, 255, 255, 0))
        offset = ((target - new_size[0]) // 2, (target - new_size[1]) // 2)
        canvas.alpha_composite(resized, dest=offset)
        return canvas

    def _composite_on_white(self, image: Image.Image) -> Image.Image:
        bg = Image.new("RGB", image.size, (255, 255, 255))
        bg.paste(image.convert("RGBA"), mask=image.getchannel("A"))
        return bg

    def _enhance(self, image: Image.Image) -> Image.Image:
        if self.contrast_factor != 1.0:
            image = ImageEnhance.Contrast(image).enhance(self.contrast_factor)
        if self.sharpen_factor != 1.0:
            image = ImageEnhance.Sharpness(image).enhance(self.sharpen_factor)
        return image


image_preprocess_service = ImagePreprocessService()
