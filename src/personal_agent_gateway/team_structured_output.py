def normalize_json_envelope(content: str) -> str:
    """Return raw JSON text, unwrapping one exact outer `json` fence."""
    stripped = content.strip()
    if not stripped.startswith("```json"):
        return stripped

    opening, newline, remainder = stripped.partition("\n")
    if not newline or opening.rstrip("\r") != "```json":
        return stripped

    body, newline, closing = remainder.rpartition("\n")
    if (
        not newline
        or closing.rstrip("\r") != "```"
        or any(line.strip().startswith("```") for line in body.splitlines())
    ):
        return stripped
    return body.strip()
