def validate_exact_edit(current: str, search: str) -> str | None:
    """Return the existing error, or None when one exact replacement is allowed."""
    if search not in current:
        return "search text not found exactly; read the file and copy it byte-for-byte"
    if current.count(search) > 1:
        return "search text is not unique; add surrounding lines"
    return None
