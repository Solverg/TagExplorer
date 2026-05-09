"""Filtering logic for tagged files."""

from __future__ import annotations

from typing import Any

from core.tags import normalize_tags


def filter_files(
    records: list[dict[str, Any]],
    selected_tags: list[str],
    mode: str,
    selected_types: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Filter records by tags and types, return (tagged_results, untagged_records)."""
    if selected_types:
        records = [record for record in records if record.get("file_type", "other") in selected_types]

    def record_tags(record: dict[str, Any]) -> list[str]:
        return normalize_tags(record.get("tags", []))

    untagged = [record for record in records if not record_tags(record)]

    selected = set(normalize_tags(selected_tags))
    if not selected:
        tagged = [record for record in records if record_tags(record)]
        return tagged, untagged

    normalized_mode = str(mode).upper()

    tagged_candidates = [record for record in records if record_tags(record)]
    if normalized_mode == "AND":
        tagged = [
            record
            for record in tagged_candidates
            if selected.issubset(set(record_tags(record)))
        ]
    else:
        tagged = [
            record
            for record in tagged_candidates
            if selected.intersection(set(record_tags(record)))
        ]

    return tagged, untagged
