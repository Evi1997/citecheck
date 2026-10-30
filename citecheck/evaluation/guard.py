"""Release guard for the test subset.

The test subset is for release decisions only. eval --subset test runs only
when both of these hold:
  1. The data was frozen with python3 -m citecheck.evaluation.freeze, and
     FROZEN.json still matches the files on disk.
  2. Every test case has review_status "reviewed" and a named reviewer.
No command-line option turns the guard off. The checks run before any test
case is evaluated, and the freeze check runs before test.jsonl is parsed.
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .. import __version__
from ..cues import RULESET_VERSION
from ..textio import Document
from .dataset import Case, fingerprint, load_cases

FREEZE_FILE = "FROZEN.json"
FREEZE_FORMAT = 1
TEST_SUBSET = "test"


class ReleaseGuardError(Exception):
    """The test subset may not be evaluated. The message says why."""


def is_human_reviewed(case: Case) -> bool:
    reviewer = case.provenance.get("reviewer")
    return (case.provenance.get("review_status") == "reviewed"
            and isinstance(reviewer, str) and bool(reviewer.strip()))


def unreviewed_ids(cases: Sequence[Case]) -> List[str]:
    return [c.id for c in cases if not is_human_reviewed(c)]


def data_digest(eval_dir: Path) -> Tuple[List[str], str]:
    """The per-file hash lines for the test data, and one SHA-256 over all of them."""
    lines = fingerprint(eval_dir, TEST_SUBSET)
    return lines.splitlines(), hashlib.sha256(lines.encode("utf-8")).hexdigest()


def build_record(eval_dir: Path, test_cases: Sequence[Case], manifest: dict, today: str) -> dict:
    files, digest = data_digest(eval_dir)
    return {
        "format": FREEZE_FORMAT,
        "frozen_on": today,
        "subset": TEST_SUBSET,
        "ruleset_version": RULESET_VERSION,
        "citecheck_version": __version__,
        "dataset_version": manifest.get("dataset_version"),
        "test_cases": len(test_cases),
        "reviewed_cases": len(test_cases) - len(unreviewed_ids(test_cases)),
        "fingerprint_sha256": digest,
        "files": files,
    }


def _short_list(ids: List[str], limit: int = 10) -> str:
    shown = ", ".join(ids[:limit])
    return shown + (f" and {len(ids) - limit} more" if len(ids) > limit else "")


def check_test_eval(eval_dir: Path, docs: Dict[str, Document]) -> Tuple[dict, List[Case]]:
    """Return the freeze record and the test cases, or raise ReleaseGuardError."""
    path = eval_dir / FREEZE_FILE
    if not path.is_file():
        raise ReleaseGuardError(
            "The test subset hasn't been frozen, so it can't be evaluated. Review every test "
            "case, then freeze the data with: python3 -m citecheck.evaluation.freeze")
    try:
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        raise ReleaseGuardError(f"{FREEZE_FILE} can't be read, so the test subset can't be "
                                "evaluated.") from None
    if (not isinstance(record, dict) or record.get("format") != FREEZE_FORMAT
            or not isinstance(record.get("fingerprint_sha256"), str)):
        raise ReleaseGuardError(f"{FREEZE_FILE} isn't a valid freeze record, so the test subset "
                                "can't be evaluated.")
    try:
        _, digest = data_digest(eval_dir)
    except OSError:
        raise ReleaseGuardError("The frozen test files can't all be read, so the test subset "
                                "can't be evaluated.") from None
    if digest != record["fingerprint_sha256"]:
        raise ReleaseGuardError(
            "The test data has changed since it was frozen, so it can't be evaluated. "
            "Restore the frozen files, or review the changed cases and freeze again.")

    cases = load_cases(eval_dir, docs, subsets=(TEST_SUBSET,))
    if not cases:
        raise ReleaseGuardError("The test subset has no cases.")
    missing = unreviewed_ids(cases)
    if missing:
        raise ReleaseGuardError(
            f"{len(missing)} of {len(cases)} test cases haven't been reviewed by a named person "
            f"({_short_list(missing)}), so the test subset can't be evaluated. A reviewed case "
            'needs "review_status": "reviewed" and a "reviewer".')
    return record, cases
