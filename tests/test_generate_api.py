from io import BytesIO
from types import SimpleNamespace

import anyio
import pytest
from fastapi import HTTPException
from PIL import Image

from api.routers import generate as generate_router


class FakeModelService:
    device_name = "test-device"

    def __init__(self):
        self.jobs = {}
        self.submitted = []
        self.preview_calls = []
        self.history_calls = []
        self.cleanup_calls = []

    def submit(self, image_bytes, params):
        self.submitted.append((image_bytes, params))
        return "job-123"

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def prepare_preview(self, image_bytes, params):
        self.preview_calls.append((image_bytes, params))
        return {"preview": "preview_sanitized.png", "diagnostics": {"preprocess": {}}}

    def list_history(self, **kwargs):
        self.history_calls.append(kwargs)
        return {"count": 0, "items": []}

    def cleanup_outputs(self, **kwargs):
        self.cleanup_calls.append(kwargs)
        return {"removed_count": 0, "error_count": 0, "dry_run": kwargs["dry_run"]}

    def unload(self):
        pass


class FakeUpload:
    def __init__(self, filename, content, content_type):
        self.filename = filename
        self._content = content
        self.content_type = content_type

    async def read(self):
        return self._content


@pytest.fixture
def fake_model_service(monkeypatch):
    fake = FakeModelService()
    monkeypatch.setattr(generate_router, "model_service", fake)
    return fake


def make_image_bytes(size=(128, 128), image_format="PNG"):
    image = Image.new("RGB", size, (180, 120, 80))
    buffer = BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()


def make_upload(filename, content, content_type):
    return FakeUpload(filename, content, content_type)


def call_generate(upload, **overrides):
    params = {
        "image": upload,
        "preset": "balanced",
        "mode": "auto",
        "input_source": "sanitized",
        "object_type": "auto",
        "foreground_ratio": 0.84,
        "extrude_depth": 0.08,
        "alpha_threshold": 8,
        "mask_bias": 0,
        "mask_edits": "[]",
        "advanced": False,
        "resolution": 256,
        "mc_threshold": 25.0,
    }
    params.update(overrides)

    async def invoke():
        return await generate_router.generate(**params)

    return anyio.run(invoke)


def test_generate_accepts_valid_image_and_resolves_preset(fake_model_service):
    response = call_generate(
        make_upload("input.png", make_image_bytes(), "image/png"),
        preset="fast",
    )

    assert response["job_id"] == "job-123"
    assert len(fake_model_service.submitted) == 1

    _, params = fake_model_service.submitted[0]
    assert params["preset"] == "fast"
    assert params["mode"] == "auto"
    assert params["input_source"] == "sanitized"
    assert params["object_type"] == "auto"
    assert params["resolution"] == 128
    assert params["mc_threshold"] == 28.0
    assert params["input"]["width"] == 128
    assert params["input"]["height"] == 128


def test_generate_clamps_advanced_overrides(fake_model_service):
    response = call_generate(
        make_upload("input.png", make_image_bytes(), "image/png"),
        advanced=True,
        resolution=999,
        mc_threshold=-10,
    )

    assert response["job_id"] == "job-123"
    _, params = fake_model_service.submitted[0]
    assert params["resolution"] == 384
    assert params["mc_threshold"] == 1.0


def test_generate_accepts_silhouette_mode(fake_model_service):
    response = call_generate(
        make_upload("input.png", make_image_bytes(), "image/png"),
        mode="silhouette",
    )

    assert response["job_id"] == "job-123"
    _, params = fake_model_service.submitted[0]
    assert params["mode"] == "silhouette"
    assert params["extrude_depth"] == 0.08


def test_generate_accepts_ai_mode(fake_model_service):
    response = call_generate(
        make_upload("input.png", make_image_bytes(), "image/png"),
        mode="ai",
    )

    assert response["job_id"] == "job-123"
    _, params = fake_model_service.submitted[0]
    assert params["mode"] == "ai"


def test_generate_accepts_original_input_source(fake_model_service):
    response = call_generate(
        make_upload("input.png", make_image_bytes(), "image/png"),
        input_source="original",
    )

    assert response["job_id"] == "job-123"
    _, params = fake_model_service.submitted[0]
    assert params["input_source"] == "original"


def test_generate_accepts_and_clamps_mask_controls(fake_model_service):
    response = call_generate(
        make_upload("input.png", make_image_bytes(), "image/png"),
        object_type="thin",
        foreground_ratio=2.0,
        extrude_depth=9.0,
        alpha_threshold=999,
        mask_bias=-99,
    )

    assert response["job_id"] == "job-123"
    _, params = fake_model_service.submitted[0]
    assert params["object_type"] == "thin"
    assert params["foreground_ratio"] == 0.95
    assert params["extrude_depth"] == 0.30
    assert params["alpha_threshold"] == 254
    assert params["mask_bias"] == -8


def test_generate_accepts_manual_mask_edits(fake_model_service):
    response = call_generate(
        make_upload("input.png", make_image_bytes(), "image/png"),
        mask_edits='[{"mode":"erase","x":1.5,"y":-1,"radius":0.8}]',
    )

    assert response["job_id"] == "job-123"
    _, params = fake_model_service.submitted[0]
    assert params["mask_edits"] == [
        {"mode": "erase", "x": 1.0, "y": 0.0, "radius": 0.3}
    ]


def test_generate_rejects_invalid_manual_mask_edits(fake_model_service):
    with pytest.raises(HTTPException) as exc_info:
        call_generate(
            make_upload("input.png", make_image_bytes(), "image/png"),
            mask_edits="{",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "invalid_mask_edits"


def test_generate_rejects_non_numeric_mask_edit_values(fake_model_service):
    with pytest.raises(HTTPException) as exc_info:
        call_generate(
            make_upload("input.png", make_image_bytes(), "image/png"),
            mask_edits='[{"mode":"erase","x":"bad","y":0.5,"radius":0.1}]',
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "invalid_numeric_value"


def test_generate_rejects_invalid_mode(fake_model_service):
    with pytest.raises(HTTPException) as exc_info:
        call_generate(
            make_upload("input.png", make_image_bytes(), "image/png"),
            mode="cloth",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "invalid_generation_mode"


def test_generate_rejects_invalid_input_source(fake_model_service):
    with pytest.raises(HTTPException) as exc_info:
        call_generate(
            make_upload("input.png", make_image_bytes(), "image/png"),
            input_source="magic",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "invalid_input_source"


def test_generate_rejects_invalid_object_type(fake_model_service):
    with pytest.raises(HTTPException) as exc_info:
        call_generate(
            make_upload("input.png", make_image_bytes(), "image/png"),
            object_type="vehicle",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "invalid_object_type"


def test_generate_rejects_unsupported_content_type(fake_model_service):
    with pytest.raises(HTTPException) as exc_info:
        call_generate(make_upload("input.gif", b"GIF87a", "image/gif"))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "unsupported_image_type"


def test_generate_rejects_invalid_image_payload(fake_model_service):
    with pytest.raises(HTTPException) as exc_info:
        call_generate(make_upload("input.png", b"not an image", "image/png"))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "invalid_image"


def test_generate_rejects_too_small_image(fake_model_service):
    with pytest.raises(HTTPException) as exc_info:
        call_generate(
            make_upload("tiny.png", make_image_bytes(size=(32, 32)), "image/png")
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "image_too_small"


def test_status_returns_job_metadata(fake_model_service):
    fake_model_service.jobs["job-1"] = SimpleNamespace(
        job_id="job-1",
        status="done",
        progress=100,
        step="Done",
        output="model.glb",
        full_output="model.glb",
        preview_output="model_preview.glb",
        preview="job-1_sanitized.png",
        error=None,
        diagnostics={"mesh": {"faces": 10}},
        stage_timings={"export": {"duration_seconds": 0.1}},
        created_at=1.0,
        updated_at=2.0,
        completed_at=2.0,
        settings={"preset": "fast"},
    )

    body = anyio.run(generate_router.status, "job-1")

    assert body["status"] == "done"
    assert body["full_output"] == "model.glb"
    assert body["preview_output"] == "model_preview.glb"
    assert body["preview"] == "job-1_sanitized.png"
    assert body["diagnostics"]["mesh"]["faces"] == 10
    assert body["stage_timings"]["export"]["duration_seconds"] == 0.1
    assert body["created_at"] == 1.0
    assert body["settings"]["preset"] == "fast"


def test_status_returns_404_for_missing_job(fake_model_service):
    with pytest.raises(HTTPException) as exc_info:
        anyio.run(generate_router.status, "missing")

    assert exc_info.value.status_code == 404


@pytest.mark.parametrize(
    ("route", "filename"),
    [
        (generate_router.download, "..evil.glb"),
        (generate_router.download, "model.png"),
        (generate_router.preview, "..evil.png"),
        (generate_router.preview, "model.glb"),
    ],
)
def test_file_routes_reject_invalid_filenames(fake_model_service, route, filename):
    with pytest.raises(HTTPException) as exc_info:
        anyio.run(route, filename)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid filename"


def test_cleanup_endpoint_forwards_options(fake_model_service):
    body = anyio.run(
        generate_router.cleanup_outputs,
        True,
        3,
        12,
    )

    assert body["dry_run"] is True
    assert fake_model_service.cleanup_calls == [
        {"dry_run": True, "max_age_days": 3, "max_files": 12}
    ]


def test_preprocess_endpoint_returns_preview(fake_model_service):
    body = anyio.run(
        generate_router.preprocess,
        make_upload("input.png", make_image_bytes(), "image/png"),
        "original",
        0.99,
        999,
        -99,
        '[{"mode":"restore","x":0.25,"y":0.5,"radius":0.02}]',
    )

    assert body["preview"] == "preview_sanitized.png"
    assert fake_model_service.preview_calls
    _, params = fake_model_service.preview_calls[0]
    assert params["input_source"] == "original"
    assert params["foreground_ratio"] == 0.95
    assert params["alpha_threshold"] == 254
    assert params["mask_bias"] == -8
    assert params["mask_edits"] == [
        {"mode": "restore", "x": 0.25, "y": 0.5, "radius": 0.02}
    ]


def test_history_endpoint_forwards_limit(fake_model_service):
    body = anyio.run(generate_router.history, 7)

    assert body == {"count": 0, "items": []}
    assert fake_model_service.history_calls == [{"limit": 7}]
