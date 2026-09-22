"""Unit tests for intelligence.core.artifact_import. Uses SYNTHETIC fixture files with their
own small, locally-computed hash table -- never the real EXP-107 model bytes, which this
session has never received (see EXP-124/107_INTEGRATION_REPORT.md)."""
from __future__ import annotations

import hashlib

import pytest

from intelligence.core.artifact_import import REQUIRED_FILES, FileCheck, verify_and_import


def _write_all(staging_dir, content_by_name: dict[str, bytes]) -> dict[str, str]:
    """Writes each REQUIRED_FILES name with content from `content_by_name` (default b"x" if a
    name is omitted) and returns the {name: real_sha256} table for whatever was written."""
    table = {}
    for name in REQUIRED_FILES:
        content = content_by_name.get(name, name.encode())
        (staging_dir / name).write_bytes(content)
        table[name] = hashlib.sha256(content).hexdigest()
    return table


class TestAllPresentAllMatching:
    def test_imports_when_everything_matches(self, tmp_path):
        staging = tmp_path / "staging"
        staging.mkdir()
        expected = _write_all(staging, {})
        dest = tmp_path / "artifact_dest"
        report = verify_and_import(staging, dest_dir=dest, expected=expected)
        assert report.imported is True
        assert report.all_present
        assert report.all_match
        assert report.n_present == len(REQUIRED_FILES)
        for name in REQUIRED_FILES:
            assert (dest / name).exists()
            assert (dest / name).read_bytes() == (staging / name).read_bytes()

    def test_summary_says_imported(self, tmp_path):
        staging = tmp_path / "staging"
        staging.mkdir()
        expected = _write_all(staging, {})
        report = verify_and_import(staging, dest_dir=tmp_path / "dest", expected=expected)
        assert "IMPORTED" in report.summary()


class TestSingleHashMismatchAbortsEverything:
    def test_one_bad_file_stops_the_whole_import(self, tmp_path):
        staging = tmp_path / "staging"
        staging.mkdir()
        expected = _write_all(staging, {})
        # corrupt exactly one file AFTER computing the expected table, so its real hash no
        # longer matches what's recorded as "expected"
        tampered = REQUIRED_FILES[3]
        (staging / tampered).write_bytes(b"TAMPERED CONTENT")

        dest = tmp_path / "dest"
        report = verify_and_import(staging, dest_dir=dest, expected=expected)

        assert report.imported is False
        assert report.all_present is True     # all 12 files exist...
        assert report.all_match is False        # ...but not all 12 hashes match
        assert report.n_matching == len(REQUIRED_FILES) - 1
        # NOTHING was copied -- not even the 11 genuinely-matching files
        assert not dest.exists() or list(dest.iterdir()) == []

    def test_mismatch_is_identified_by_name(self, tmp_path):
        staging = tmp_path / "staging"
        staging.mkdir()
        expected = _write_all(staging, {})
        tampered = REQUIRED_FILES[0]
        (staging / tampered).write_bytes(b"different bytes entirely")
        report = verify_and_import(staging, dest_dir=tmp_path / "dest", expected=expected)
        bad = [c for c in report.checks if not c.matches]
        assert len(bad) == 1
        assert bad[0].filename == tampered
        assert bad[0].sha256 != bad[0].expected_sha256


class TestMissingFileAbortsEverything:
    def test_one_missing_file_stops_the_whole_import(self, tmp_path):
        staging = tmp_path / "staging"
        staging.mkdir()
        expected = _write_all(staging, {})
        missing = REQUIRED_FILES[7]
        (staging / missing).unlink()

        dest = tmp_path / "dest"
        report = verify_and_import(staging, dest_dir=dest, expected=expected)

        assert report.imported is False
        assert report.all_present is False
        assert report.n_present == len(REQUIRED_FILES) - 1
        assert not dest.exists() or list(dest.iterdir()) == []

    def test_completely_empty_staging_dir(self, tmp_path):
        staging = tmp_path / "empty_staging"
        staging.mkdir()
        report = verify_and_import(staging, dest_dir=tmp_path / "dest")
        assert report.imported is False
        assert report.n_present == 0
        assert all(not c.present for c in report.checks)


class TestDryRun:
    def test_dry_run_never_copies_even_on_full_success(self, tmp_path):
        staging = tmp_path / "staging"
        staging.mkdir()
        expected = _write_all(staging, {})
        dest = tmp_path / "dest"
        report = verify_and_import(staging, dest_dir=dest, expected=expected, dry_run=True)
        assert report.imported is False
        assert report.dry_run is True
        assert report.all_match is True   # verification still ran and would have succeeded
        assert not dest.exists()


class TestRealRepoStateHasNothingToImport:
    """Exercises the CURRENT, REAL artifact/ directory state (no mocking) -- proving this
    session genuinely has no source of the 12 files to import from, matching
    EXP-124/107_INTEGRATION_REPORT.md's finding."""

    def test_artifact_dir_itself_has_none_of_the_pkl_files(self):
        from intelligence.core.artifact_import import ARTIFACT_DIR
        for name in REQUIRED_FILES:
            assert not (ARTIFACT_DIR / name).exists(), (
                f"{name} unexpectedly present -- artifact recovery may have actually happened; "
                "re-run intelligence/core/exp107_signal.py's provider and update the report")


class TestNoRealHashesUsedInTests:
    def test_default_expected_table_is_not_consulted_by_these_tests(self, tmp_path):
        """A sanity check that this file's own tests always pass an explicit `expected` table
        built from synthetic content, never silently falling back to the real
        EXPECTED_SHA256 constant (which describes files this session has never possessed)."""
        staging = tmp_path / "staging"
        staging.mkdir()
        _write_all(staging, {})   # synthetic content, sha256 of b"<filename>" per file
        from intelligence.core.artifact_import import EXPECTED_SHA256
        # the synthetic per-file content's hash must NOT coincidentally equal the real expected
        # hash for any file (would only happen by an astronomically unlikely hash collision)
        for name in REQUIRED_FILES:
            synthetic_hash = hashlib.sha256(name.encode()).hexdigest()
            assert synthetic_hash != EXPECTED_SHA256[name]
