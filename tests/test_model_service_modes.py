import pytest

from api.services.model_service import JobQueueFull, ModelService


def test_auto_mode_uses_silhouette_for_source_alpha():
    service = ModelService()

    mode = service._effective_generation_mode(
        {"mode": "auto"},
        {
            "used_source_alpha": True,
            "mask_area_ratio": 0.4,
            "foreground_size": [400, 400],
        },
    )

    assert mode == "silhouette"


def test_auto_mode_uses_silhouette_for_tiny_or_slender_masks():
    service = ModelService()

    small = service._effective_generation_mode(
        {"mode": "auto"},
        {
            "used_source_alpha": False,
            "mask_area_ratio": 0.05,
            "foreground_size": [120, 400],
        },
    )
    slender = service._effective_generation_mode(
        {"mode": "auto"},
        {
            "used_source_alpha": False,
            "mask_area_ratio": 0.2,
            "foreground_size": [80, 320],
        },
    )

    assert small == "silhouette"
    assert slender == "silhouette"


def test_auto_mode_keeps_ai_for_large_rounded_masks():
    service = ModelService()

    mode = service._effective_generation_mode(
        {"mode": "auto"},
        {
            "used_source_alpha": False,
            "mask_area_ratio": 0.35,
            "foreground_size": [300, 320],
        },
    )

    assert mode == "ai"


def test_object_type_overrides_auto_mode():
    service = ModelService()

    thin = service._effective_generation_mode(
        {"mode": "auto", "object_type": "thin"},
        {
            "used_source_alpha": False,
            "mask_area_ratio": 0.35,
            "foreground_size": [300, 320],
        },
    )
    rounded = service._effective_generation_mode(
        {"mode": "auto", "object_type": "rounded"},
        {
            "used_source_alpha": True,
            "mask_area_ratio": 0.05,
            "foreground_size": [80, 320],
        },
    )

    assert thin == "silhouette"
    assert rounded == "ai"


def test_submit_uses_bounded_queue_without_starting_generation():
    service = ModelService(worker_count=1, max_queue_size=1, start_workers=False)

    job_id = service.submit(b"image-a", {"preset": "fast"})
    job = service.get_job(job_id)

    assert job.status == "pending"
    assert job.queue_position == 1
    assert job.step == "Queued (1)"

    with pytest.raises(JobQueueFull):
        service.submit(b"image-b", {"preset": "fast"})


def test_submit_reuses_active_duplicate_job():
    service = ModelService(worker_count=1, max_queue_size=1, start_workers=False)

    first = service.submit(b"same-image", {"preset": "balanced"})
    second = service.submit(b"same-image", {"preset": "balanced"})

    assert second == first
    assert len(service._jobs) == 1
    assert service.get_job(first).diagnostics["duplicate_submissions"] == 1


def test_cancel_pending_job_marks_terminal_and_frees_submission_key():
    service = ModelService(worker_count=1, max_queue_size=1, start_workers=False)
    job_id = service.submit(b"image-a", {"preset": "fast"})

    job = service.cancel_job(job_id)

    assert job.status == "cancelled"
    assert job.queue_position is None
    assert job.completed_at is not None
    assert service.submit(b"image-a", {"preset": "fast"}) != job_id
