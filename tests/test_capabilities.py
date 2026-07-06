import pytest

from personal_agent_gateway.capabilities import CapabilityRegistry, CapabilityValidationError


def test_registry_lists_core_local_capabilities() -> None:
    registry = CapabilityRegistry.default()

    ids = {capability.id for capability in registry.list()}

    assert "shell.run" in ids
    assert "ffmpeg.inspect" in ids
    assert "ffmpeg.extract-audio" in ids
    assert "ffmpeg.thumbnail" in ids
    assert "capture.screen" in ids


def test_capability_exposes_surface_metadata_for_ui_and_agent() -> None:
    registry = CapabilityRegistry.default()

    capability = registry.get("ffmpeg.extract-audio")

    assert capability.title == "Extract Audio"
    assert capability.description
    assert capability.category == "Media"
    assert capability.risk_level == "medium"
    assert capability.requires_approval is True
    assert capability.output_types == ("audio",)


def test_registry_rejects_unknown_capability() -> None:
    registry = CapabilityRegistry.default()

    with pytest.raises(CapabilityValidationError, match="Unknown capability"):
        registry.get("missing.capability")


def test_ffmpeg_extract_audio_requires_source_file() -> None:
    registry = CapabilityRegistry.default()

    with pytest.raises(CapabilityValidationError, match="source_file"):
        registry.validate_input("ffmpeg.extract-audio", {"format": "m4a"})


def test_required_inputs_reject_blank_strings() -> None:
    registry = CapabilityRegistry.default()

    with pytest.raises(CapabilityValidationError, match="source_file"):
        registry.validate_input("ffmpeg.inspect", {"source_file": "  "})
