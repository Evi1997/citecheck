"""Fixed settings and release targets.

The release targets are copied from PRD.md section 9. They are constants on
purpose: no command-line flag can change them, and a test checks their values.
"""

# Matching settings (TECH_PLAN.md section 4).
MAX_GAP = 3
CLOSEST_K = 3
LONG_CLAIM_CHARS = 500

# Input limits (QA finding F14). Generous for the PRD S5 case (20 claims,
# 10 documents of about 2,000 words) and small enough to fail fast.
MAX_DOCUMENTS = 100              # .txt and .md files in the documents folder
MAX_TOTAL_DOC_BYTES = 10_000_000 # all documents together (each file: 1 MB)
MAX_CLAIMS = 200                 # claims in one answer file

# Closest passages must share at least this many content words with the claim
# (or all of them, for a claim with fewer). Lower-scoring sentences are hidden
# rather than shown as if they were relevant (QA finding F13).
MIN_SHARED_TERMS = 2
# Below this share of the claim's content words, the best sentence is treated
# as "essentially absent" rather than "partial support" (QA finding F12).
MIN_PARTIAL_COVERAGE = 0.5

# (metric key, comparison, target, display name)
RELEASE_TARGETS = (
    # An automatic proxy: correct label and a quote that overlaps the gold quote.
    # It doesn't show the quote justifies the verdict (see PRD.md section 9).
    ("citation_verified_correctness", ">=", 0.90, "Correct label, quote at gold location"),
    # Quote integrity: the quote matches its file exactly. Not proof of support.
    ("quote_verbatim_rate", ">=", 1.00, "Quote integrity (exact text match)"),
    ("false_support_rate", "<=", 0.05, "False-support rate"),
    # Owner decision 2026-10-29 (QA round 1): provisional, symmetrical with
    # false-support. Not independently validated until fresh human-reviewed
    # evaluation data exists.
    ("false_contradicted_rate", "<=", 0.05, "False-contradicted rate (provisional)"),
    ("keyword_trap_supported", "<=", 0, "Keyword traps marked SUPPORTED"),
    ("precision_SUPPORTED", ">=", 0.90, "Precision, SUPPORTED"),
    ("precision_CONTRADICTED", ">=", 0.85, "Precision, CONTRADICTED"),
    ("recall_INSUFFICIENT_EVIDENCE", ">=", 0.85, "Recall, INSUFFICIENT EVIDENCE"),
    ("in_scope_recall_SUPPORTED", ">=", 0.90, "In-scope recall, SUPPORTED"),
    ("in_scope_recall_CONTRADICTED", ">=", 0.90, "In-scope recall, CONTRADICTED"),
    ("hit_at_3", ">=", 0.80, "Closest passages hit@3"),
    # Renamed from "splitting_accuracy": it measures recall (gold claims
    # recovered). Same target. Precision and extra claims are reported beside it.
    ("splitting_recall", ">=", 0.95, "Claim-splitting recall (gold claims recovered)"),
)

# Targets from the previous (LLM-based) draft. They are kept as historical
# benchmarks and reported on every run, but they are not V0.1 release gates.
# See PRD.md section 11.
HISTORICAL_BENCHMARKS = (
    ("recall_SUPPORTED", ">=", 0.60, "Recall, SUPPORTED (all cases)"),
    ("recall_CONTRADICTED", ">=", 0.60, "Recall, CONTRADICTED (all cases)"),
    ("abstention_correctness", ">=", 0.85, "Abstention correctness"),
)

# Gates proposed but not approved. They are computed and shown on every run,
# never as PASS or FAIL. A gate becomes a release check only when the owner
# approves a threshold and it moves into RELEASE_TARGETS. Don't add a
# threshold here. Empty since false_contradicted_rate was approved on
# 2026-10-29. (metric key, display name)
PENDING_GATES: tuple = ()

# PRD S5 benchmark (measured by hand, see README). Previous draft: 300 s.
S5_TIME_LIMIT_SECONDS = 10
S5_MEMORY_LIMIT_MB = 200
S5_HISTORICAL_TIME_LIMIT_SECONDS = 300
