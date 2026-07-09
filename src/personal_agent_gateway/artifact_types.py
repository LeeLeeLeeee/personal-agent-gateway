from pathlib import Path

_IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp"}
_VIDEO = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
_AUDIO = {".mp3", ".m4a", ".wav", ".ogg", ".flac"}
_DOCUMENT = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".md", ".csv", ".hwp", ".hwpx", ".html", ".htm",
}

_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".hwp": "application/x-hwp",
    ".hwpx": "application/haansofthwpx",
    ".html": "text/html",
    ".htm": "text/html",
}


def _ext(path: str) -> str:
    return Path(path).suffix.lower()


def is_registrable(path: str) -> bool:
    return _ext(path) in _IMAGE | _VIDEO | _AUDIO | _DOCUMENT


def artifact_type_for(path: str) -> str:
    ext = _ext(path)
    if ext in _IMAGE:
        return "image"
    if ext in _VIDEO:
        return "video"
    if ext in _AUDIO:
        return "audio"
    if ext in _DOCUMENT:
        return "document"
    return "other"


def mime_type_for(path: str) -> str:
    return _MIME.get(_ext(path), "application/octet-stream")
