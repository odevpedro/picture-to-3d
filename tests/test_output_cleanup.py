import json
import os
import time

from api.services import model_service as model_module


def write_file(path, content=b"x", *, age_days=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    timestamp = time.time() - age_days * 24 * 60 * 60
    os.utime(path, (timestamp, timestamp))
    return path


def write_metadata(path, preview=None, *, age_days=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"preview": preview}), encoding="utf-8")
    timestamp = time.time() - age_days * 24 * 60 * 60
    os.utime(path, (timestamp, timestamp))
    return path


def service_with_storage(tmp_path, monkeypatch, *, max_age_days=7, max_files=100):
    outputs_dir = tmp_path / "outputs"
    previews_dir = tmp_path / "previews"
    outputs_dir.mkdir()
    previews_dir.mkdir()
    monkeypatch.setattr(model_module, "OUTPUTS_DIR", outputs_dir)
    monkeypatch.setattr(model_module, "PREVIEWS_DIR", previews_dir)

    service = model_module.ModelService()
    service.output_retention_days = max_age_days
    service.max_output_files = max_files
    return service, outputs_dir, previews_dir


def test_cleanup_removes_old_output_metadata_and_preview(tmp_path, monkeypatch):
    service, outputs_dir, previews_dir = service_with_storage(tmp_path, monkeypatch)
    old_glb = write_file(outputs_dir / "old.glb", age_days=10)
    old_json = write_metadata(outputs_dir / "old.json", "old_sanitized.png", age_days=10)
    old_preview = write_file(previews_dir / "old_sanitized.png", age_days=10)
    fresh_glb = write_file(outputs_dir / "fresh.glb", age_days=1)
    fresh_json = write_metadata(
        outputs_dir / "fresh.json",
        "fresh_sanitized.png",
        age_days=1,
    )
    fresh_preview = write_file(previews_dir / "fresh_sanitized.png", age_days=1)

    result = service.cleanup_outputs()

    assert result["removed_count"] == 3
    assert not old_glb.exists()
    assert not old_json.exists()
    assert not old_preview.exists()
    assert fresh_glb.exists()
    assert fresh_json.exists()
    assert fresh_preview.exists()
    assert {entry["filename"] for entry in result["removed"]} == {
        "old.glb",
        "old.json",
        "old_sanitized.png",
    }


def test_cleanup_keeps_only_newest_outputs_when_max_files_exceeded(tmp_path, monkeypatch):
    service, outputs_dir, _ = service_with_storage(
        tmp_path,
        monkeypatch,
        max_age_days=30,
        max_files=2,
    )
    write_file(outputs_dir / "oldest.glb", age_days=3)
    write_file(outputs_dir / "middle.glb", age_days=2)
    write_file(outputs_dir / "newest.glb", age_days=1)

    result = service.cleanup_outputs()

    assert result["removed_count"] == 1
    assert not (outputs_dir / "oldest.glb").exists()
    assert (outputs_dir / "middle.glb").exists()
    assert (outputs_dir / "newest.glb").exists()
    assert result["removed"][0]["reason"] == "count"


def test_cleanup_dry_run_reports_without_deleting(tmp_path, monkeypatch):
    service, outputs_dir, _ = service_with_storage(tmp_path, monkeypatch)
    old_glb = write_file(outputs_dir / "old.glb", age_days=10)

    result = service.cleanup_outputs(dry_run=True)

    assert result["dry_run"] is True
    assert result["removed_count"] == 1
    assert old_glb.exists()
    assert result["freed_bytes"] == 0


def test_cleanup_removes_old_orphan_metadata_and_preview(tmp_path, monkeypatch):
    service, outputs_dir, previews_dir = service_with_storage(tmp_path, monkeypatch)
    orphan_json = write_metadata(outputs_dir / "orphan.json", age_days=10)
    orphan_preview = write_file(previews_dir / "orphan.png", age_days=10)

    result = service.cleanup_outputs()

    assert result["removed_count"] == 2
    assert not orphan_json.exists()
    assert not orphan_preview.exists()


def test_cleanup_removes_associated_preview_output(tmp_path, monkeypatch):
    service, outputs_dir, previews_dir = service_with_storage(tmp_path, monkeypatch)
    full = write_file(outputs_dir / "old.glb", age_days=10)
    preview_output = write_file(outputs_dir / "old_preview.glb", age_days=10)
    metadata = {
        "output": "old.glb",
        "full_output": "old.glb",
        "preview_output": "old_preview.glb",
        "preview": "old_sanitized.png",
    }
    metadata_path = outputs_dir / "old.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    old_preview = write_file(previews_dir / "old_sanitized.png", age_days=10)

    result = service.cleanup_outputs()

    assert result["removed_count"] == 4
    assert not full.exists()
    assert not preview_output.exists()
    assert not metadata_path.exists()
    assert not old_preview.exists()


def test_list_history_reads_metadata_outputs(tmp_path, monkeypatch):
    service, outputs_dir, previews_dir = service_with_storage(tmp_path, monkeypatch)
    write_file(outputs_dir / "job.glb")
    write_file(outputs_dir / "job_preview.glb")
    write_file(previews_dir / "job_sanitized.png")
    metadata = {
        "job_id": "job-1",
        "status": "done",
        "output": "job.glb",
        "full_output": "job.glb",
        "preview_output": "job_preview.glb",
        "preview": "job_sanitized.png",
        "settings": {"mode": "auto"},
        "diagnostics": {"output": {"effective_mode": "silhouette"}},
        "stage_timings": {"export": {"duration_seconds": 0.2}},
        "created_at": 1,
        "updated_at": 2,
        "completed_at": 3,
    }
    write_metadata(outputs_dir / "job.json")
    (outputs_dir / "job.json").write_text(json.dumps(metadata), encoding="utf-8")

    history = service.list_history(limit=5)

    assert history["count"] == 1
    item = history["items"][0]
    assert item["job_id"] == "job-1"
    assert item["full_output"] == "job.glb"
    assert item["preview_output"] == "job_preview.glb"
    assert item["preview"] == "job_sanitized.png"
    assert item["stage_timings"]["export"]["duration_seconds"] == 0.2
