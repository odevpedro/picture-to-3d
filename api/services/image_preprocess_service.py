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
from PIL import Image, ImageDraw, ImageEnhance, ImageOps


@dataclass
class PreprocessResult:
    image: Image.Image
    rgba: Image.Image
    alpha: Image.Image
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
        input_source: str = "sanitized",
        foreground_ratio: float | None = None,
        alpha_threshold: int | None = None,
        mask_bias: int = 0,
        mask_edits: list[dict[str, Any]] | None = None,
        preview_dir: Path | None = None,
        preview_stem: str | None = None,
    ) -> PreprocessResult:
        foreground_ratio = self.foreground_ratio if foreground_ratio is None else foreground_ratio
        alpha_threshold = self.alpha_threshold if alpha_threshold is None else alpha_threshold
        warnings: list[str] = []
        source = Image.open(io.BytesIO(image_bytes))
        original_size = source.size
        source = ImageOps.exif_transpose(source)
        transposed_size = source.size

        if min(transposed_size) < 256:
            warnings.append("input_resolution_low")

        rgba = source.convert("RGBA")
        if input_source == "original":
            return self._prepare_original(
                rgba,
                original_size=original_size,
                transposed_size=transposed_size,
                warnings=warnings,
                alpha_threshold=alpha_threshold,
                mask_edits=mask_edits,
                preview_dir=preview_dir,
                preview_stem=preview_stem,
            )

        source_alpha = np.asarray(rgba.getchannel("A"), dtype=np.uint8)
        used_source_alpha = bool(source_alpha.min() < 255)

        if used_source_alpha:
            removed = rgba
        else:
            import rembg
            removed = rembg.remove(rgba).convert("RGBA")

        alpha = np.asarray(removed.getchannel("A"), dtype=np.uint8)
        raw_mask = alpha > alpha_threshold
        clean_mask = self._clean_mask(raw_mask)
        clean_mask = self._apply_mask_bias(clean_mask, mask_bias)

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
        sanitized_rgba = self._place_on_square_canvas(crop, foreground_ratio=foreground_ratio)
        sanitized_rgba, edit_diagnostics = self._apply_mask_edits(sanitized_rgba, mask_edits)
        sanitized_rgb = self._composite_on_white(sanitized_rgba)
        sanitized_rgb = self._enhance(sanitized_rgb)

        preview_filename = None
        if preview_dir and preview_stem:
            preview_dir.mkdir(parents=True, exist_ok=True)
            preview_filename = f"{preview_stem}_sanitized.png"
            sanitized_rgba.save(preview_dir / preview_filename, format="PNG", optimize=True)

        bbox_width = bbox[2] - bbox[0]
        bbox_height = bbox[3] - bbox[1]
        final_alpha = np.asarray(sanitized_rgba.getchannel("A"), dtype=np.uint8)
        final_mask = final_alpha > alpha_threshold
        mask_area = int(final_mask.sum())
        image_area = int(final_mask.shape[0] * final_mask.shape[1])
        diagnostics = {
            "original_size": list(original_size),
            "transposed_size": list(transposed_size),
            "foreground_bbox": list(bbox),
            "foreground_size": [bbox_width, bbox_height],
            "mask_area_ratio": round(mask_area / image_area, 4) if image_area else 0,
            "output_size": [sanitized_rgb.width, sanitized_rgb.height],
            "foreground_ratio": foreground_ratio,
            "input_source": "sanitized",
            "used_source_alpha": used_source_alpha,
            "alpha_threshold": alpha_threshold,
            "mask_bias": mask_bias,
            "manual_mask_edits": edit_diagnostics,
            "preview_has_alpha": True,
            "warnings": warnings,
        }

        return PreprocessResult(
            image=sanitized_rgb,
            rgba=sanitized_rgba,
            alpha=sanitized_rgba.getchannel("A"),
            preview_filename=preview_filename,
            diagnostics=diagnostics,
        )

    def _prepare_original(
        self,
        rgba: Image.Image,
        *,
        original_size: tuple[int, int],
        transposed_size: tuple[int, int],
        warnings: list[str],
        alpha_threshold: int,
        mask_edits: list[dict[str, Any]] | None,
        preview_dir: Path | None,
        preview_stem: str | None,
    ) -> PreprocessResult:
        source_alpha = np.asarray(rgba.getchannel("A"), dtype=np.uint8)
        used_source_alpha = bool(source_alpha.min() < 255)
        raw_mask = source_alpha > alpha_threshold

        if not raw_mask.any():
            raw_mask = np.ones_like(source_alpha, dtype=bool)
            warnings.append("original_alpha_empty")

        bbox = self._mask_bbox(raw_mask)
        if bbox is None:
            bbox = (0, 0, rgba.width, rgba.height)
        if self._bbox_touches_edges(bbox, rgba.size):
            warnings.append("foreground_touches_image_edge")

        original_rgba = self._fit_full_image_on_square_canvas(rgba)
        original_rgba, edit_diagnostics = self._apply_mask_edits(original_rgba, mask_edits)
        original_rgb = self._enhance(self._composite_on_white(original_rgba))

        preview_filename = None
        if preview_dir and preview_stem:
            preview_dir.mkdir(parents=True, exist_ok=True)
            preview_filename = f"{preview_stem}_original.png"
            original_rgba.save(preview_dir / preview_filename, format="PNG", optimize=True)

        final_alpha = np.asarray(original_rgba.getchannel("A"), dtype=np.uint8)
        final_mask = final_alpha > alpha_threshold
        mask_area = int(final_mask.sum())
        image_area = int(final_mask.shape[0] * final_mask.shape[1])
        bbox_width = bbox[2] - bbox[0]
        bbox_height = bbox[3] - bbox[1]
        diagnostics = {
            "original_size": list(original_size),
            "transposed_size": list(transposed_size),
            "foreground_bbox": list(bbox),
            "foreground_size": [bbox_width, bbox_height],
            "mask_area_ratio": round(mask_area / image_area, 4) if image_area else 0,
            "output_size": [original_rgb.width, original_rgb.height],
            "foreground_ratio": None,
            "input_source": "original",
            "used_source_alpha": used_source_alpha,
            "alpha_threshold": alpha_threshold,
            "mask_bias": 0,
            "manual_mask_edits": edit_diagnostics,
            "preview_has_alpha": True,
            "warnings": warnings,
        }

        return PreprocessResult(
            image=original_rgb,
            rgba=original_rgba,
            alpha=original_rgba.getchannel("A"),
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
        cleaned = self._fill_small_holes(cleaned, ndimage)
        cleaned = ndimage.binary_closing(cleaned, structure=np.ones((3, 3), dtype=bool))
        return cleaned.astype(bool)

    def _apply_mask_bias(self, mask: np.ndarray, mask_bias: int) -> np.ndarray:
        if mask_bias == 0 or not mask.any():
            return mask

        try:
            from scipy import ndimage
        except Exception:
            return mask

        iterations = abs(int(mask_bias))
        structure = np.ones((3, 3), dtype=bool)
        if mask_bias > 0:
            return ndimage.binary_dilation(mask, structure=structure, iterations=iterations)
        biased = ndimage.binary_erosion(mask, structure=structure, iterations=iterations)
        return biased if biased.any() else mask

    def _apply_mask_edits(
        self,
        image: Image.Image,
        mask_edits: list[dict[str, Any]] | None,
    ) -> tuple[Image.Image, dict[str, Any]]:
        edits = [edit for edit in (mask_edits or []) if isinstance(edit, dict)]
        edits = edits[:500]
        diagnostics = {
            "count": 0,
            "erase": 0,
            "restore": 0,
        }
        if not edits:
            return image, diagnostics

        result = image.copy().convert("RGBA")
        base_alpha = result.getchannel("A").copy()
        alpha = base_alpha.copy()
        width, height = result.size

        for edit in edits:
            mode = str(edit.get("mode", "erase")).strip().lower()
            if mode not in {"erase", "restore"}:
                continue
            x = self._clamp_unit(edit.get("x"))
            y = self._clamp_unit(edit.get("y"))
            radius = min(0.3, max(0.002, self._clamp_unit(edit.get("radius"), default=0.04)))
            cx = x * width
            cy = y * height
            r = radius * max(width, height)
            bbox = (cx - r, cy - r, cx + r, cy + r)

            if mode == "erase":
                ImageDraw.Draw(alpha).ellipse(bbox, fill=0)
            else:
                restore_mask = Image.new("L", result.size, 0)
                ImageDraw.Draw(restore_mask).ellipse(bbox, fill=255)
                alpha.paste(base_alpha, mask=restore_mask)

            diagnostics[mode] += 1
            diagnostics["count"] += 1

        result.putalpha(alpha)
        return result, diagnostics

    def _clamp_unit(self, value: Any, *, default: float = 0.5) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return max(0.0, min(1.0, parsed))

    def _fill_small_holes(self, mask: np.ndarray, ndimage) -> np.ndarray:
        holes = ~mask
        labeled, count = ndimage.label(holes)
        if count == 0:
            return mask

        border_labels = set(np.unique(labeled[0, :]))
        border_labels.update(np.unique(labeled[-1, :]))
        border_labels.update(np.unique(labeled[:, 0]))
        border_labels.update(np.unique(labeled[:, -1]))

        sizes = np.bincount(labeled.ravel())
        max_hole_area = max(64, int(mask.size * 0.0025))
        fill_labels = [
            label
            for label in range(1, count + 1)
            if label not in border_labels and sizes[label] <= max_hole_area
        ]
        if not fill_labels:
            return mask
        return mask | np.isin(labeled, fill_labels)

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

    def _place_on_square_canvas(
        self,
        image: Image.Image,
        *,
        foreground_ratio: float,
    ) -> Image.Image:
        target = self.target_size
        max_foreground_side = max(1, int(target * foreground_ratio))
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

    def _fit_full_image_on_square_canvas(self, image: Image.Image) -> Image.Image:
        target = self.target_size
        scale = min(target / image.width, target / image.height)
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
