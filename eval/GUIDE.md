# CiteCheck labelling and review guide

**Status:** every document, case and expected split in this folder is **SYNTHETIC**. It was drafted by AI on 2026-10-23. No label counts as validated until a person has reviewed it.

## 1. What the labels mean

Judge each claim only against the documents listed for the case (`"*"` means all documents). Ignore real-world knowledge.

| Label | Use it when |
|---|---|
| SUPPORTED | The documents entail the whole claim. |
| CONTRADICTED | The documents state something incompatible with the claim. |
| INSUFFICIENT_EVIDENCE | Anything else, including partial support, hypothetical or outdated statements, and documents that disagree. |

A sentence under a heading such as "Superseded policy" describes the past. It doesn't establish what is true now.

## 2. Scope tags

For SUPPORTED and CONTRADICTED cases, set `scope` **before** running CiteCheck on the case:
- `in`: the gold evidence matches one of the V0.1 rules: S1 ordered match, C1 negation flip or C2 value conflict (see the README, "How it works").
- `out`: anything else, such as paraphrase, opposite words, format differences or facts from two documents.

INSUFFICIENT_EVIDENCE cases have `scope: null`.

## 3. Case types

`verbatim`, `near_verbatim`, `paraphrase`, `multi_doc`, `format`, `number_date`, `negation`, `antonym`, `paraphrased_contradiction`, `absent`, `keyword_trap`, `qualifier_trap`, `partial`, `conflict`, `outdated_context`.

This draft differs slightly from the planned mix:
- SUPPORTED has 3 paraphrase cases plus one `number_date` threshold case (`sup-16`), instead of 4 paraphrase cases.
- INSUFFICIENT_EVIDENCE has 6 keyword traps (including the near-miss `cf-02`), 1 conflict and 1 outdated-context case, instead of 5 traps and 3 conflict or near-miss cases.

## 4. How to review a case

1. Read the claim and the documents.
2. Decide the label yourself before reading the drafted one.
3. If you agree, set `"review_status": "reviewed"` and `"reviewer"` to your name or role.
4. If you disagree, set `"review_status": "disputed"`, keep the drafted label, and add an entry to `disagreements` in `MANIFEST.json`:
   ```json
   {"id": "qt-01", "drafted": "INSUFFICIENT_EVIDENCE", "reviewer_label": "SUPPORTED", "reason": "...", "resolution": "pending"}
   ```
5. Record uncertainty in the case's `notes`. Several cases already say "Uncertain label" where the AI draft was unsure.
6. Gold quotes are stored as text only. Each must occur exactly once in its document; the loader computes the offsets.

To check a quote's location by hand:

```
python3 -m citecheck.evaluation.locate eval/docs/d01_warranty.txt "Batteries are covered for 12 months."
```

## 5. Reviewers

- **First reviewer:** the author. Not started.
- **Second reviewer:** optional for this prototype and not planned. Reports must not describe the set as double-labelled.

## 6. Freezing the test subset

The test subset is kept out of the public repository (see section 7). With it in place locally, freeze it only after every test case is reviewed:

```
python3 -m citecheck.evaluation.freeze
```

This writes `FROZEN.json`. The file is never copied into a Docker image. It records the rule-set version, the CiteCheck version, the dataset version and a SHA-256 fingerprint of `data/test.jsonl`, `docs/` and `splitting/`. The command refuses to run while any test case lacks `"review_status": "reviewed"` and a `"reviewer"`, and it never replaces an existing `FROZEN.json`.

`eval --subset test` refuses to run until the data is frozen, every test case is reviewed, and the files still match the fingerprint. After freezing, never change rules or word lists because of test results. Use the dev subset for checking rules. Dev results are tuning results only.

## 7. This set is not independent release evidence

The test subset (`data/test.jsonl`) is **compromised for independent release evaluation**. It is kept unchanged and private, isn't regenerated, and isn't opened by the unit tests. Even after review and freezing, this set can't show that CiteCheck is ready for release:
- Dev and test cases share the same documents.
- The splitting answers serve both subsets, and the splitter was tuned on them.
- The same AI wrote the rules, documents, cases and labels.
- Scope tags were set before rule sets 0.2.0, 0.3.0 and 0.4.0, and have not been redone.
- Earlier in development, a unit test ran the test subset on every test run without showing the results. That test was removed.

Reviewing it is still useful: it checks the labelling guide and the harness. Release evidence needs a new, independently written test set reviewed by a person, with its own documents and splitting files.
