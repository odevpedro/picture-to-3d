from api.services.model_service import ModelService


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
