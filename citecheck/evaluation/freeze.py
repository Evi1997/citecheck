"""Freeze the test data so later changes are detected.

Run this once, after every test case has been reviewed by a named person:
  python3 -m citecheck.evaluation.freeze [EVAL_DIR]
It writes FROZEN.json with the rule-set version, the CiteCheck version, the
dataset version and a SHA-256 fingerprint of test.jsonl, docs and the
splitting set. It refuses to freeze unreviewed cases, and it refuses to
replace an existing FROZEN.json.
"""

import json
import sys
import time
from pathlib import Path

from ..report import write_new_file
from .dataset import DatasetError, load_cases, load_docs, load_manifest
from .guard import FREEZE_FILE, TEST_SUBSET, build_record, unreviewed_ids
from .run import DEFAULT_EVAL_DIR


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    eval_dir = Path(argv[0]) if argv else DEFAULT_EVAL_DIR
    if (eval_dir / FREEZE_FILE).exists():
        sys.stderr.write(f"{FREEZE_FILE} already exists. Delete it by hand only if you mean to re-freeze.\n")
        return 1
    try:
        # Validates both subsets, including ids that are unique across them.
        cases = load_cases(eval_dir, load_docs(eval_dir))
    except DatasetError as exc:
        sys.stderr.write(f"The dataset is not valid, so it was not frozen: {exc}\n")
        return 2
    test_cases = [c for c in cases if c.subset == TEST_SUBSET]
    if not test_cases:
        sys.stderr.write("The test subset has no cases, so it was not frozen.\n")
        return 2
    missing = unreviewed_ids(test_cases)
    if missing:
        sys.stderr.write(f"{len(missing)} of {len(test_cases)} test cases haven't been reviewed by a "
                         "named person, so the data was not frozen. Review them first (see eval/GUIDE.md).\n")
        return 2

    record = build_record(eval_dir, test_cases, load_manifest(eval_dir), time.strftime("%Y-%m-%d"))
    stem, ext = FREEZE_FILE.rsplit(".", 1)
    path, renamed = write_new_file(eval_dir, stem, ext, json.dumps(record, indent=2) + "\n")
    if renamed:
        sys.stderr.write(f"Unexpected: wrote {path} instead of {FREEZE_FILE}. Delete it and try again.\n")
        return 1
    sys.stdout.write(f"Froze the test data under rule set {record['ruleset_version']}: {path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
