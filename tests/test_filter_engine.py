from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.filter_engine import filter_files  # noqa: E402


class FilterFilesTest(unittest.TestCase):
    def test_or_filter_excludes_records_with_blocked_tags(self) -> None:
        records = [
            {"filename": "work.txt", "tags": ["work"], "file_type": "document"},
            {"filename": "old-work.txt", "tags": ["work", "archive"], "file_type": "document"},
            {"filename": "home.txt", "tags": ["home"], "file_type": "document"},
        ]

        tagged, untagged = filter_files(records, ["work", "home"], "OR", excluded_tags=["archive"])

        self.assertEqual([record["filename"] for record in tagged], ["work.txt", "home.txt"])
        self.assertEqual(untagged, [])

    def test_and_filter_applies_exclusion_after_positive_match(self) -> None:
        records = [
            {"filename": "fresh.txt", "tags": ["work", "today"], "file_type": "document"},
            {"filename": "old.txt", "tags": ["work", "today", "archive"], "file_type": "document"},
            {"filename": "partial.txt", "tags": ["work"], "file_type": "document"},
        ]

        tagged, _untagged = filter_files(records, ["work", "today"], "AND", excluded_tags=["archive"])

        self.assertEqual([record["filename"] for record in tagged], ["fresh.txt"])

    def test_exclusion_without_selected_tags_keeps_untagged_records(self) -> None:
        records = [
            {"filename": "work.txt", "tags": ["work"], "file_type": "document"},
            {"filename": "archive.txt", "tags": ["archive"], "file_type": "document"},
            {"filename": "plain.txt", "tags": [], "file_type": "document"},
        ]

        tagged, untagged = filter_files(records, [], "OR", excluded_tags=["archive"])

        self.assertEqual([record["filename"] for record in tagged], ["work.txt"])
        self.assertEqual([record["filename"] for record in untagged], ["plain.txt"])


if __name__ == "__main__":
    unittest.main()
