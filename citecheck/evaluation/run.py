"""The citecheck eval command."""

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from .. import __version__
from ..cues import RULESET_VERSION
from ..report import result_to_dict, write_new_file
from ..safe_text import neutralise
from ..splitter import Claim
from ..textio import sort_key
from ..thresholds import CLOSEST_K, MAX_GAP
from ..verdict import LABELS, ClaimResult, Corpus, check_claim
from . import metrics as metric_defs
from .dataset import DatasetError, fingerprint, load_cases, load_docs, load_manifest, load_splitting
from .guard import FREEZE_FILE, ReleaseGuardError, check_test_eval

DEFAULT_EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"
SCHEMA_VERSION = "1.1"

TUNING_NOTE = ("The rules were adjusted after looking at dev results, so these numbers show how "
               "well the rules fit the cases they were tuned on. They are not release evidence.")
INDEPENDENCE_NOTE = ("These results count as release evidence only if the test cases were written "
                     "independently of the rules. The bundled Northwind set was not (README, "
                     '"Limits of the bundled dataset").')
QUOTE_NOTES = (
    "Quote integrity shows only that each quote matches its file exactly. It does not show "
    "that the quote supports or contradicts the claim.",
    '"Correct label, quote at gold location" compares quotes with the gold quotes drafted for '
    "each case. Whether the evidence justifies the verdict needs a person to judge it against "
    "independently labelled cases. That review is not automated.",
)


def peak_memory_mb() -> Optional[float]:
    try:
        import resource
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports kilobytes.
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _with_counts(value: Optional[float], pair: Optional[list]) -> str:
    """A percentage with the counts behind it, for example "75% (3/4)"."""
    return _pct(value) if not pair else f"{_pct(value)} ({pair[0]}/{pair[1]})"


def _frac(m: dict, key: str) -> str:
    return _with_counts(m.get(key), m["fractions"].get(key))


def results_fingerprint(block: dict) -> str:
    data = json.dumps(block, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def run_eval(args) -> int:
    eval_dir = Path(args.dataset) if args.dataset else DEFAULT_EVAL_DIR
    freeze_record = None
    try:
        docs = load_docs(eval_dir)
        if args.subset == "test":
            # The release guard runs before anything else touches the test cases.
            freeze_record, cases = check_test_eval(eval_dir, docs)
        else:
            cases = load_cases(eval_dir, docs, subsets=(args.subset,))
        splitting = load_splitting(eval_dir)
    except ReleaseGuardError as exc:
        sys.stderr.write(neutralise(f"Refused: {exc}\nNo test case was evaluated. See eval/GUIDE.md, section 6.\n"))
        return 2
    except DatasetError as exc:
        sys.stderr.write(f'The evaluation set couldn\'t be read at "{eval_dir}". Check that the files '
                         f"exist and follow the dataset format in the README.\nDetail: {exc}\n")
        return 2
    manifest = load_manifest(eval_dir)
    subset = [c for c in cases if c.subset == args.subset]

    started = time.perf_counter()
    corpora: Dict[tuple, Corpus] = {}
    predictions: Dict[str, ClaimResult] = {}
    for case in subset:
        key = tuple(sorted(case.docs, key=sort_key))
        if key not in corpora:
            corpora[key] = Corpus([docs[name] for name in key])
        predictions[case.id] = check_claim(Claim(1, case.claim, 1), corpora[key])
    m = metric_defs.compute(subset, predictions, splitting)
    elapsed = time.perf_counter() - started

    release = args.subset == "test"
    block = {
        "result_type": "release_evaluation" if release else "tuning_only",
        "dataset": {
            "version": manifest.get("dataset_version"),
            "subset": args.subset,
            "count": len(subset),
            "fingerprint": hashlib.sha256(fingerprint(eval_dir, args.subset).encode("utf-8")).hexdigest(),
            "synthetic": manifest.get("synthetic"),
            "reviewed_cases": sum(1 for c in subset if c.provenance.get("review_status") == "reviewed"),
        },
        "freeze": None if freeze_record is None else {
            "file": FREEZE_FILE,
            "frozen_on": freeze_record.get("frozen_on"),
            "ruleset_version": freeze_record.get("ruleset_version"),
            "fingerprint_sha256": freeze_record.get("fingerprint_sha256"),
        },
        "ruleset_version": RULESET_VERSION,
        "settings": {"max_gap": MAX_GAP, "closest_k": CLOSEST_K},
        "per_case": [
            {
                "id": c.id,
                "gold_label": c.gold_label,
                "scope": c.scope,
                "case_type": c.case_type,
                "review_status": c.provenance.get("review_status"),
                "result": result_to_dict(predictions[c.id], "claim"),
            }
            for c in subset
        ],
        "metrics": m,
        # Dev runs compare against the same targets, but under another key so a
        # dev result can't be mistaken for a release check.
        ("release_checks" if release else "tuning_target_comparison"): metric_defs.checks(m),
        "pending_gates": metric_defs.pending(m),
        "historical_benchmarks": metric_defs.historical(m),
    }
    digest = results_fingerprint(block)
    results = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "citecheck", "version": __version__},
        "run": {"date": time.strftime("%Y-%m-%d")},
        "results_fingerprint": digest,
        **block,
        "performance": {"run_time_seconds": round(elapsed, 3), "peak_memory_mb": peak_memory_mb()},
    }

    sys.stdout.write(neutralise(render_eval(block, m, manifest, digest, elapsed, args.subset)))
    try:
        path, _ = write_new_file(Path(args.out), "eval-results", "json",
                                 json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    except OSError:
        sys.stderr.write(f'Can\'t write to "{args.out}". Choose another folder with --out.\n')
        return 2
    sys.stdout.write(f"\nResults saved: {path}\n")
    return 0


def render_eval(block: dict, m: dict, manifest: dict, digest: str, elapsed: float, subset: str) -> str:
    ds = block["dataset"]
    release = subset == "test"
    out: List[str] = []
    out.append("CiteCheck evaluation")
    out.append(f"Dataset {ds['version']} | subset: {subset} | {ds['count']} cases | rule set {RULESET_VERSION}")
    if release:
        freeze = block["freeze"]
        out.append("RELEASE EVALUATION on the frozen test subset.")
        out.append(f"Frozen on {freeze['frozen_on']} under rule set {freeze['ruleset_version']}. "
                   "Every test case is reviewed by a named person.")
        if freeze["ruleset_version"] != RULESET_VERSION:
            out.append(f"Note: the rules changed after freezing ({freeze['ruleset_version']} -> "
                       f"{RULESET_VERSION}). Report both versions with these results.")
        out.append(INDEPENDENCE_NOTE)
    else:
        out.append("DEVELOPMENT RUN: TUNING RESULTS ONLY.")
        out.append(TUNING_NOTE)
        out.append('The "test" subset is for release decisions. eval --subset test runs only after '
                   "every test case is reviewed and the data is frozen.")

    reviewed = ds["reviewed_cases"]
    if manifest.get("synthetic"):
        out.append(f"Labels: SYNTHETIC, drafted by AI. {reviewed} of {ds['count']} cases reviewed by a person.")
    if reviewed < ds["count"]:
        out.append("These results are not validated until every label has been reviewed by a person.")
    out.append("Numbers in brackets are the counts behind each percentage (correct/total).")
    out.append("")

    out.append("CONFUSION MATRIX (rows = correct label, columns = CiteCheck's label)")
    out.append(f"  {'':<24}{'SUPPORTED':>10}{'CONTRADICTED':>14}{'INSUFFICIENT':>14}")
    names = {"SUPPORTED": "SUPPORTED", "CONTRADICTED": "CONTRADICTED",
             "INSUFFICIENT_EVIDENCE": "INSUFFICIENT EVIDENCE"}
    for gold in LABELS:
        row = m["confusion"][gold]
        out.append(f"  {names[gold]:<24}{row['SUPPORTED']:>10}{row['CONTRADICTED']:>14}"
                   f"{row['INSUFFICIENT_EVIDENCE']:>14}")
    out.append("")

    out.append("PER-LABEL SCORES")
    out.append(f"  {'Label':<24}{'Precision':<15}{'Recall':<15}{'F1':<7}In-scope recall")
    for label in LABELS:
        f1 = m[f"f1_{label}"]
        in_scope_text = _frac(m, f"in_scope_recall_{label}") if label != "INSUFFICIENT_EVIDENCE" else "-"
        out.append(f"  {names[label]:<24}{_frac(m, f'precision_{label}'):<15}{_frac(m, f'recall_{label}'):<15}"
                   f"{('n/a' if f1 is None else f'{f1:.2f}'):<7}{in_scope_text}")
    out.append("")

    out.append("OTHER MEASURES")
    traps = m["fractions"]["keyword_trap_supported"]
    rows = [
        ("Correct label, quote at gold location", _frac(m, "citation_verified_correctness"),
         "(target >= 90%; automatic proxy)"),
        ("Quote integrity (exact text match)", _frac(m, "quote_verbatim_rate"), "(target 100%)"),
        ("False-support rate", _frac(m, "false_support_rate"), "(target <= 5%)"),
        ("False-contradicted rate", _frac(m, "false_contradicted_rate"),
         "(target <= 5%; provisional, not independently validated)"),
        ("Keyword traps marked SUPPORTED", f"{traps[0]} of {traps[1]}", "(target 0)"),
        ("Closest passages hit@3", _frac(m, "hit_at_3"), "(target >= 80%)"),
        ("Claim-splitting recall", _frac(m, "splitting_recall"), "(target >= 95%; expected claims found)"),
        ("Claim-splitting precision", _frac(m, "splitting_precision"),
         f"(reported only; {m['splitting_extra_claims']} claims produced were not expected)"),
        ("Abstention correctness", _frac(m, "abstention_correctness"), "(reported only)"),
        ("Coverage (claims decided)", _frac(m, "coverage"), "(reported only)"),
    ]
    for name, value, note in rows:
        out.append(f"  {name:<38}: {value:<13} {note}")
    if m["splitting_extra_list"]:
        out.append("  Extra claims from the splitter (not in the expected lists):")
        for item in m["splitting_extra_list"]:
            out.append(f"    {item['file']}, line {item['line']}: \"{item['text']}\"")
    for note in QUOTE_NOTES:
        out.append(f"  {note}")
    out.append("")

    if release:
        out.append("RELEASE CHECKS")
        checks = block["release_checks"]
        words = ("PASS", "FAIL")
    else:
        out.append("TARGET COMPARISON (tuning only; these are not release checks)")
        checks = block["tuning_target_comparison"]
        words = ("met", "not met")
    for check in checks:
        actual, pair = check["actual"], check["numerator_denominator"]
        if check["metric"] == "keyword_trap_supported":
            shown = "n/a" if actual is None else f"{actual} of {pair[1]}"
        else:
            shown = _with_counts(actual, pair)
        if actual is None:
            # A zero denominator is never a pass.
            shown += ", no cases to measure"
        word = words[0] if check["pass"] else words[1]
        out.append(f"  {word:<8}{check['name']}: {shown} (target {check['target']})")
    out.append("")

    if block["pending_gates"]:
        out.append("PENDING GATES (proposed; no threshold approved, so never PASS or FAIL)")
        for gate in block["pending_gates"]:
            out.append(f"  {gate['name']}: {_with_counts(gate['actual'], gate['numerator_denominator'])} "
                       f"({gate['status']})")
        out.append("")

    out.append("HISTORICAL BENCHMARKS (previous draft targets, not V0.1 release gates)")
    for check in block["historical_benchmarks"]:
        out.append(f"  {'MET' if check['pass'] else 'NOT MET'}  {check['name']}: "
                   f"{_with_counts(check['actual'], check['numerator_denominator'])} "
                   f"(previous target {check['target']})")
    out.append("")

    out.append("V0.2 INDICATORS (see PRD section 10)")
    out.append(f"  Gold SUPPORTED/CONTRADICTED cases abstained : {_frac(m, 'v02_abstained_share')}  (criterion: >= 40%)")
    out.append(f"  ...of which out of scope                    : {_frac(m, 'v02_abstained_out_of_scope_share')}  (criterion: >= 50%)")
    out.append(f"  hit@3 on those abstained cases              : {_frac(m, 'v02_abstained_hit_at_3')}  (criterion: >= 80%)")
    out.append("  These numbers only report. Criteria 4 to 7 need separate evidence.")
    out.append("")

    out.append("PERFORMANCE")
    peak = peak_memory_mb()
    peak_text = "n/a" if peak is None else f"{peak:.0f} MB"
    out.append(f"  Run time {elapsed:.2f} s  Peak memory {peak_text}")
    out.append("  The S5 speed benchmark is measured separately (see README).")
    out.append("")
    out.append(f"Results fingerprint: {digest[:16]} (identical runs give identical fingerprints)")
    out.append("Small sets give rough numbers. With 4 decided cases, one wrong case moves a "
               "precision by 25 points.")
    return "\n".join(out) + "\n"
