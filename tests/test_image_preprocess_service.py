from io import BytesIO

from PIL import Image, ImageDraw

from api.services.image_preprocess_service import ImagePreprocessService


def transparent_png_bytes():
    image = Image.new("RGBA", (128, 128), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.polygon([(20, 20), (108, 64), (20, 108)], fill=(30, 30, 30, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_prepare_preserves_transparent_preview_alpha(tmp_path):
    service = ImagePreprocessService(target_size=128)

    result = service.prepare(
        transparent_png_bytes(),
        preview_dir=tmp_path,
        preview_stem="transparent",
    )

    preview = Image.open(tmp_path / "transparent_sanitized.png")
    alpha = preview.convert("RGBA").getchannel("A")

    assert result.diagnostics["used_source_alpha"] is True
    assert result.diagnostics["preview_has_alpha"] is True
    assert preview.mode == "RGBA"
    assert alpha.getextrema()[0] == 0
    assert alpha.getextrema()[1] == 255
    assert result.alpha.getextrema()[0] == 0
    assert result.alpha.getextrema()[1] == 255


def test_prepare_original_source_keeps_full_image_framing(tmp_path):
    service = ImagePreprocessService(target_size=128)

    result = service.prepare(
        transparent_png_bytes(),
        input_source="original",
        preview_dir=tmp_path,
        preview_stem="transparent",
    )

    preview = Image.open(tmp_path / "transparent_original.png")
    alpha = preview.convert("RGBA").getchannel("A")

    assert result.diagnostics["input_source"] == "original"
    assert result.diagnostics["used_source_alpha"] is True
    assert result.diagnostics["output_size"] == [128, 128]
    assert preview.mode == "RGBA"
    assert alpha.getextrema()[0] == 0
    assert alpha.getextrema()[1] == 255


def test_prepare_applies_manual_mask_edits(tmp_path):
    service = ImagePreprocessService(target_size=128)

    baseline = service.prepare(
        transparent_png_bytes(),
        input_source="original",
        preview_dir=tmp_path,
        preview_stem="baseline",
    )
    edited = service.prepare(
        transparent_png_bytes(),
        input_source="original",
        mask_edits=[{"mode": "erase", "x": 0.5, "y": 0.5, "radius": 0.2}],
        preview_dir=tmp_path,
        preview_stem="edited",
    )

    assert edited.diagnostics["manual_mask_edits"]["count"] == 1
    assert edited.diagnostics["manual_mask_edits"]["erase"] == 1
    assert edited.diagnostics["mask_area_ratio"] < baseline.diagnostics["mask_area_ratio"]
