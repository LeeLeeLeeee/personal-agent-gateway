from personal_agent_gateway.artifact_types import (
    artifact_type_for,
    is_registrable,
    mime_type_for,
)


def test_is_registrable_accepts_whitelisted_and_rejects_others() -> None:
    assert is_registrable("out/cat.png") is True
    assert is_registrable("clip.MP4") is True
    assert is_registrable("report.hwpx") is True
    assert is_registrable("index.html") is True
    assert is_registrable("script.py") is False
    assert is_registrable("archive.zip") is False
    assert is_registrable("noext") is False


def test_artifact_type_for_maps_by_extension() -> None:
    assert artifact_type_for("a.png") == "image"
    assert artifact_type_for("a.mp4") == "video"
    assert artifact_type_for("a.mp3") == "audio"
    assert artifact_type_for("a.pdf") == "document"
    assert artifact_type_for("a.py") == "other"


def test_mime_type_for_known_and_unknown() -> None:
    assert mime_type_for("a.png") == "image/png"
    assert mime_type_for("a.pdf") == "application/pdf"
    assert mime_type_for("a.html") == "text/html"
    assert mime_type_for("a.py") == "application/octet-stream"
