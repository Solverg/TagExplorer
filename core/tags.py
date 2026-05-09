"""Shared helpers for filename tags."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

TAG_PATTERN = re.compile(r"(?<!\[)\[([^\[\]]+)\](?!\])")


class TagValidationError(ValueError):
    """Raised when tag text cannot be safely represented in a filename."""


def normalize_tag_values(tags: Iterable[object]) -> list[str]:
    """Trim tag values and drop empty tags."""
    normalized: list[str] = []
    for tag in tags:
        text = str(tag).strip()
        if text:
            normalized.append(text)
    return normalized


def normalize_tags(raw_tags: object) -> list[str]:
    """Normalize a stored tag payload into a list of non-empty strings."""
    if isinstance(raw_tags, list):
        return normalize_tag_values(raw_tags)
    return []


def validate_tag_text(tag: str) -> None:
    """Ensure a tag can be safely serialized as [tag] in a filename."""
    if not tag:
        raise TagValidationError("Tag cannot be empty.")
    if "[" in tag or "]" in tag:
        raise TagValidationError("Tags cannot include square brackets.")


def validate_tags(tags: Iterable[str]) -> None:
    for tag in tags:
        validate_tag_text(tag)


def tag_sort_key(tag: str) -> tuple[str, str]:
    return (tag.casefold(), tag)


def sort_tags(tags: Iterable[object]) -> list[str]:
    """Return unique tags in deterministic, case-insensitive order."""
    return sorted(set(normalize_tag_values(tags)), key=tag_sort_key)


def parse_tags(filename: str) -> list[str]:
    """Parse tags from a filename using square-bracket syntax."""
    return normalize_tag_values(TAG_PATTERN.findall(filename))


def build_tagged_filename(path: Path, tags: Iterable[object], tags_at_start: bool) -> str:
    """Build a filename by replacing existing bracket tags with updated tags."""
    normalized_tags = sort_tags(tags)
    validate_tags(normalized_tags)

    stem = TAG_PATTERN.sub("", path.stem).strip() or "untitled"
    tag_text = "".join(f"[{tag}]" for tag in normalized_tags)
    if not tag_text:
        return f"{stem}{path.suffix}"
    if tags_at_start:
        return f"{tag_text} {stem}{path.suffix}"
    return f"{stem} {tag_text}{path.suffix}"
