# CiteCheck

> **Experimental prototype. Not for high-stakes fact-checking.** CiteCheck's accuracy has not been independently evaluated.

CiteCheck checks the claims in an AI-written answer against your own text documents and shows the evidence behind each result. It is offline, deterministic and rule-based: it uses no LLM, no retrieval model and no paid API, and the same input always gives the same output.

Each claim gets one of three labels, with a quote and location you can check yourself:

| Label | Meaning |
|---|---|
| SUPPORTED | A sentence in your documents matches the claim under CiteCheck's rules. |
| CONTRADICTED | A sentence states the opposite, or gives a different number. |
| INSUFFICIENT EVIDENCE | Your documents don't settle the claim under CiteCheck's rules. |

Labels are relative to the documents you provide. They don't show whether a claim is true in the real world.

## Quote integrity is not evidence correctness

Every quote CiteCheck shows is re-read from your file and matches it exactly. This **quote integrity** proves the quote is real and shows where a label came from.

It does not prove that the quote supports or contradicts the claim. A rule can match the right words and still miss the meaning. Measuring whether evidence actually justifies a verdict needs people to judge it against independently labelled examples, and that hasn't been done. Check important claims yourself.

## Try the demo

The `demo/` folder holds a short synthetic answer and a synthetic library guide. With Python 3 installed and no packages needed, run this from the repository folder:

```
python3 -m citecheck check demo/answer.txt demo/docs
```

The demo has six claims. The expected result is 1 SUPPORTED, 2 CONTRADICTED (a different number, and a negation) and 3 INSUFFICIENT EVIDENCE (a number with "up to", a sentence under a dated heading, and a topic the guide doesn't mention). Two lines are skipped: the lead-in line and "Hope this helps!". All 4 quotes shown pass the quote integrity check.

To check your own files:

```
python3 -m citecheck check answer.txt docs/
```

or use the wrapper script: `sh bin/citecheck check answer.txt docs/`.

**Python versions.** CiteCheck has been tested with Python 3.11 in the Docker image below. The code is written for Python 3.9 or newer, but other versions are unverified.

## Run it in Docker

The image runs offline as a non-root user and works with a read-only filesystem. Building it downloads the `python:3.11-slim` base image once. Nothing else is downloaded or installed.

```
docker build -t citecheck:0.1 .
docker run --rm --network none --read-only --tmpfs /tmp:rw,size=16m \
  --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges \
  --memory 256m --cpus 1 --pids-limit 64 citecheck:0.1
```

The default command runs the demo and writes its reports to `/tmp/citecheck-report`, a temporary in-memory folder that disappears when the container stops. No host folder is mounted.

To check your own files, mount a folder read-only and keep the reports in `/tmp`:

```
docker run --rm --network none --read-only --tmpfs /tmp:rw,size=16m \
  --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges \
  -v "$PWD/my-check:/data:ro" citecheck:0.1 \
  python -m citecheck check /data/answer.txt /data/docs --out /tmp/citecheck-report
```

To run the unit tests, build the `test` target:

```
docker build --target test -t citecheck:0.1-test .
docker run --rm --network none --read-only --tmpfs /tmp:rw,size=64m \
  --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges \
  --memory 512m --cpus 1 --pids-limit 64 citecheck:0.1-test
```

The runtime image contains only the application and the demo. The test image adds the tests and the development evaluation data.

**Pinning the base image.** The `python:3.11-slim` tag can change over time. For a reproducible build, pin it by digest:

```
docker image inspect python:3.11-slim --format '{{index .RepoDigests 0}}'
docker build --build-arg PYTHON_IMAGE=python:3.11-slim@sha256:<digest> -t citecheck:0.1 .
```

## How it works

CiteCheck splits the answer into claims and the documents into sentences, then compares every claim with every sentence using three rules:

- **S1, same words in order → SUPPORTED.** A sentence starts with the claim's words, in the same order, and adds nothing except earlier items of a list ("kettles, toasters and coffee machines"). Numbers match, and neither side has a negation or qualifier word ("not", "may", "only", "if", "about", "typically") that the other lacks.
- **C1, negation flip → CONTRADICTED.** The same words, but exactly one side is negated ("covers" vs "does not cover").
- **C2, different number → CONTRADICTED.** The same words, but one number differs ("30 days" vs "14 days").

The rules are deliberately cautious. CiteCheck says INSUFFICIENT EVIDENCE instead of guessing when:
- **Extra words could narrow the sentence:** "*Cancelled* accounts…", "waived for *UK* students", "free *for members*", or a closing date ("had 60 staff *in 2015*").
- **The numbers can't be compared safely:** a limit, range or estimate ("within", "from", "between", "up to", "about"), a sign ("<", ">", "~"), a negation, or a number that labels something ("Section 3", "Version 2"). Numbers are compared as written, so "3.10" is not "3.1".
- **The context could change the meaning:** the heading suggests old, draft or limited content ("Superseded policy", "Exclusions"); the heading names a year the claim doesn't; or the next or previous sentence in the paragraph may narrow it ("However, sale items…").
- **The subject is unclear:** the claim starts with a word such as "It", "They" or "This plan".
- **The documents disagree:** one sentence supports the claim and another contradicts it. Both are shown.

Every claim also shows up to 3 **closest passages**, found by keyword ranking (BM25, a standard search-ranking formula). A passage appears only if it shares at least two of the claim's content words. Similar wording is not proof, so passages never change a label.

Each INSUFFICIENT EVIDENCE result says why. The reasons fall into four groups: the evidence is absent, only part of the claim matches, the context or values are ambiguous, or the file changed while CiteCheck was running.

## Options and limits

| Option | Meaning |
|---|---|
| `--split sentences` | Split the answer into sentences (default). |
| `--split lines` | Treat each non-blank line as one claim. |
| `--out DIR` | Where to save reports (default `./citecheck-report`). It can't be the documents folder. |
| `--format md\|json\|both` | Report formats (default `both`). |
| `--no-color` | Plain text. `NO_COLOR` is also respected. |
| `--quiet` | Print only the summary and file paths. |

- Reports are never overwritten. If a report already exists, the new one gets a timestamp in its name.
- Exit codes: `0` the run finished (any mix of labels), `1` unexpected failure, `2` usage or input error.
- Documents: top-level `.txt` and `.md` files in UTF-8, up to 1 MB each, at most 100 files and 10 MB in total.
- Answer: up to 1 MB and 200 claims.
- Skipped with a warning: hidden files, subfolders, links that point outside the folder or to hidden files, broken links, special files such as pipes, empty files, and earlier CiteCheck reports.

**How answers are split.** Each line is read on its own, and Markdown decoration is removed. Sentences end at `.`, `!`, `?` or `;` before a capital letter, digit or quote mark, but not after common abbreviations or decimals. Questions, lead-in lines ending in ":", headings, short fragments, code, tables and chat phrases ("Hope this helps", "Let me know", "Sources:") are skipped and listed in the report.

## Evaluation

```
python3 -m citecheck eval --subset dev
```

This runs CiteCheck on the bundled development set and prints the metrics, each with the counts behind it (for example `75% (3/4)`).

**These figures are not an accuracy benchmark.** The development set is small, synthetic (it describes a fictional company), drafted by AI and not yet reviewed by a person. The rules were tuned while looking at it. Its results show how well the rules fit the examples they were tuned on, and the output labels them "tuning results only".

A held-out test set exists but is kept private. It was exposed during development, so it can't serve as independent evidence either. Without it, `eval --subset test` refuses to run. The guard also refuses any test set that hasn't been reviewed by a named person and frozen with a recorded fingerprint. An honest accuracy figure needs a new test set that is written independently of the rules and reviewed by people. That doesn't exist yet.

The metrics include per-label precision and recall, the false-support and false-contradicted rates, quote integrity, and closest-passage hit rate. See [eval/GUIDE.md](eval/GUIDE.md) for how examples are labelled and reviewed.

## Tests

```
python3 -m unittest discover -s tests -t . -v
```

The tests need no network and no packages. They use synthetic fixtures with general wording, and each safety rule has a matching case that should still produce a verdict. The private test set is never opened by the tests.

## Known limitations

- **Narrow by design.** Paraphrases, synonyms and opposite words ("rose" vs "fell") aren't recognised, and facts spread over two sentences aren't combined. Expect many INSUFFICIENT EVIDENCE results.
- **Cautious by design.** Harmless details, such as a closing date, a range or a claim starting with "It", also lead to INSUFFICIENT EVIDENCE.
- **Context checks use fixed word lists.** A restriction worded differently, one more than a sentence away, or one implied only by the file name can be missed.
- **Pronouns** are caught only at the start of a claim.
- **Number formats.** "$20" and "20 dollars" don't match. Commas are read as English thousands separators, so comma decimals ("1,5") are never treated as a value conflict.
- **Speed** has not been benchmarked on large document sets.
- **English plain text only.** No PDF or other formats.
- **Terminal display.** Long lines wrap at 80 characters, and control characters from your files are shown as escapes such as `\x1b`. The JSON report keeps the exact text.

## Security and privacy

- No network code, no API keys, no accounts and no paid services. A test checks that no network or process modules are imported.
- Documents are read as text and never executed.
- Control characters from documents, answers and file names are neutralised before they reach the terminal or the Markdown report.
- Reports contain document text and are written only to the `--out` folder.

## How it was built

CiteCheck was built with Ann, an AI-assisted, multi-agent product-development workflow that I run in Claude Code. An orchestrating agent wrote the code and documents. Specialist agents with read-only access (product, design, engineering, QA and security, and portfolio) planned and reviewed the work.

I directed the product decisions and approved each stage and change. The agents didn't run the application, its tests or its Docker builds: I ran every build and test myself, in an isolated container. The QA and security agent's review failed an early version, and several fix rounds followed. Those rounds produced the cautious rules, the guarded test set and the plain evaluation labels described above.

CiteCheck itself uses no AI at runtime.

## Project files

```
citecheck/        application code (Python standard library only)
demo/             synthetic demo answer and document
tests/            unit and integration tests
eval/             synthetic development set, labelling guide and manifest
bin/citecheck     wrapper script
Dockerfile        runtime image (default) and test image (--target test)
```
