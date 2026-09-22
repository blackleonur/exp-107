"""
EXP-124 -- secure artifact import / verification mechanism.

RESEARCH / PAPER ONLY. This module NEVER trains, retrains, fabricates, or modifies a model. It
only ever copies bytes that already exist on local disk (in a staging directory the user
controls) into `artifact/` (repo root), and ONLY after every one of the 12 required files has
been verified byte-for-byte against the SHA-256 hashes the artifact's own author recorded at
freeze time (`artifact/artifact.json`'s `sha256` field, and -- when supplied -- the full
64-character hashes provided directly by the user, cross-checked for consistency against those
recorded prefixes).

Absolute rule, enforced in code, not just documented: **if even one file is missing or its hash
does not match, NOTHING is imported.** There is no partial-import path. This mirrors the task
instruction verbatim: "If even ONE hash differs: STOP immediately and report it. Do not load
the model."

This module makes no network call, reads no credential, and never logs or prints file content
-- only filenames and hash comparisons. `artifact/*.pkl` stays covered by the repository's
existing `.gitignore` (`*.pkl`); this module never stages a `git add` and never removes that
`.gitignore` entry.

Usage:
    python3 -m intelligence.core.artifact_import /path/to/staging/dir [--dry-run]

or programmatically:
    from intelligence.core.artifact_import import verify_and_import
    report = verify_and_import(Path("/path/to/staging/dir"))
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

ARTIFACT_DIR = Path(__file__).resolve().parents[2] / "artifact"
ARTIFACT_JSON = ARTIFACT_DIR / "artifact.json"

REQUIRED_FILES = tuple(
    f"{kind}_{t}.pkl" for t in (10, 30, 60, 120, 240, 480) for kind in ("booster", "isotonic"))

# The full 64-character SHA-256 hashes as supplied by the user in the EXP-124 "CRITICAL UPDATE"
# session message. Cross-checked (see intelligence/tests/unit/test_artifact_import.py and the
# EXP-124/107_INTEGRATION_REPORT.md entry for this session) against artifact/artifact.json's own
# recorded 16-character prefixes -- all 12 are consistent with what the artifact's own author
# recorded at freeze time. This consistency is NECESSARY but NOT SUFFICIENT proof of
# authenticity: it only shows the claimed hashes agree with data already in this repository, not
# that any actual file matching them has ever been produced. Only a real byte-for-byte hash
# computed on a real, present file (verify_and_import's actual job) can establish that.
EXPECTED_SHA256: dict[str, str] = {
    "booster_10.pkl":   "cce859ba4636286b29c7f777d13377ed34a5a376e98c060c7affef820b9ced50",
    "booster_30.pkl":   "c335c66af7f8dd6d7d1285709ce427ec5d2fd4571801cf400a6c4556ac5bee30",
    "booster_60.pkl":   "f259b6f81320f3589f8c66ed3208b6e2fe7ece80e1c9e776d264d39ae2d2496c",
    "booster_120.pkl":  "0b50d501bcf007be5ef0bfe815f901609952346b114ac2b00106979acc3ddf60",
    "booster_240.pkl":  "b041b1608e9675b69c31d98fc9a67c52be29b7182be1d8f76ae1fa44b867d398",
    "booster_480.pkl":  "12859ba779b36c4c8fc0bf06ea5cf01edb025c0800342cb39463c797c0259a07",
    "isotonic_10.pkl":  "5c179e68e0050363ffcb8c2d3d1549313df4481e63a33f362f2b340d7a9a2bfa",
    "isotonic_30.pkl":  "dbc5597f114b5d62f9468fee3acebc40ae1abd67ad22ee2458967f4a9b973453",
    "isotonic_60.pkl":  "23d8fd6d8402507c5696f216e715a91a0538f73a621ec896f821b9f4c6609f23",
    "isotonic_120.pkl": "59c24f3325c47de0bc7705e5bf23df9abb10983413010fea3aa4e073f1976cbc",
    "isotonic_240.pkl": "23b2e49221a08af4f665e5b732d47d98970220cb821bd81cdf110d735e4d20b4",
    "isotonic_480.pkl": "04231cff06db421293797123d5e5f999924bf73da68e053d9397c862afca5619",
}


@dataclass(frozen=True)
class FileCheck:
    filename: str
    present: bool
    sha256: str | None      # the ACTUAL computed hash of the file found on disk, if present
    expected_sha256: str | None
    matches: bool


@dataclass(frozen=True)
class ImportReport:
    source_dir: str
    checks: list[FileCheck] = field(default_factory=list)
    imported: bool = False       # True only if ALL 12 were present AND all 12 matched AND copy ran
    dry_run: bool = False

    @property
    def all_present(self) -> bool:
        return all(c.present for c in self.checks)

    @property
    def all_match(self) -> bool:
        return all(c.matches for c in self.checks)

    @property
    def n_present(self) -> int:
        return sum(1 for c in self.checks if c.present)

    @property
    def n_matching(self) -> int:
        return sum(1 for c in self.checks if c.matches)

    def summary(self) -> str:
        lines = [f"Artifact import report -- source: {self.source_dir}"]
        for c in self.checks:
            if not c.present:
                lines.append(f"  MISSING          {c.filename}")
            elif c.matches:
                lines.append(f"  OK               {c.filename}  sha256={c.sha256[:16]}...")
            else:
                lines.append(f"  HASH MISMATCH    {c.filename}  "
                             f"got={c.sha256[:16]}...  expected={c.expected_sha256[:16]}...")
        lines.append(f"present: {self.n_present}/{len(REQUIRED_FILES)}   "
                     f"matching: {self.n_matching}/{len(REQUIRED_FILES)}")
        if self.dry_run:
            lines.append("DRY RUN -- nothing was copied regardless of the result above.")
        elif self.imported:
            lines.append(f"IMPORTED -- all {len(REQUIRED_FILES)} files verified and copied to "
                         f"{ARTIFACT_DIR}")
        else:
            lines.append("NOT IMPORTED -- see above. Nothing was copied.")
        return "\n".join(lines)


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_and_import(source_dir: Path, dest_dir: Path = ARTIFACT_DIR,
                      expected: dict[str, str] | None = None,
                      dry_run: bool = False) -> ImportReport:
    """Checks all 12 required files in `source_dir` against `expected` (defaults to
    EXPECTED_SHA256). Copies NOTHING unless every single file is present AND every single hash
    matches -- a partial or mismatched set aborts the whole import, verified files included, so
    a caller never ends up with 11 genuine files and 1 silently-skipped bad one."""
    expected = expected or EXPECTED_SHA256
    source_dir = Path(source_dir)
    checks: list[FileCheck] = []
    for name in REQUIRED_FILES:
        p = source_dir / name
        exp = expected.get(name)
        if not p.is_file():
            checks.append(FileCheck(name, present=False, sha256=None, expected_sha256=exp,
                                    matches=False))
            continue
        actual = _sha256_of(p)
        checks.append(FileCheck(name, present=True, sha256=actual, expected_sha256=exp,
                                matches=(exp is not None and actual == exp)))

    report = ImportReport(source_dir=str(source_dir), checks=checks, imported=False,
                          dry_run=dry_run)
    all_ok = report.all_present and report.all_match
    if not all_ok or dry_run:
        return report

    dest_dir.mkdir(parents=True, exist_ok=True)
    for c in checks:
        src = source_dir / c.filename
        tmp = dest_dir / f".{c.filename}.importing"
        shutil.copy2(src, tmp)
        tmp.replace(dest_dir / c.filename)
    return ImportReport(source_dir=str(source_dir), checks=checks, imported=True,
                        dry_run=False)


def _cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source_dir", type=Path, help="staging directory holding the 12 .pkl files")
    ap.add_argument("--dry-run", action="store_true", help="verify only, never copy")
    args = ap.parse_args()
    report = verify_and_import(args.source_dir, dry_run=args.dry_run)
    print(report.summary())
    return 0 if (report.imported or args.dry_run) else 1


if __name__ == "__main__":
    sys.exit(_cli())
