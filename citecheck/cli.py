"""Command-line interface: citecheck check and citecheck eval."""

import argparse
import difflib
import re
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import __version__
from .report import RunReport, Style, render_terminal, write_reports
from .safe_text import neutralise
from .splitter import split_answer
from .textio import InputLimitError, TextReadError, load_documents, read_text
from .thresholds import LONG_CLAIM_CHARS, MAX_CLAIMS
from .verdict import Corpus, check_claim

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

KNOWN_OPTIONS = (
    "--split", "--out", "--format", "--no-color", "--quiet", "--debug",
    "--dataset", "--subset", "--help", "--version",
)

DESCRIPTION = """\
CiteCheck - check claims against your own documents.

What it does:
  Splits an AI answer into claims and compares each one with the sentences
  in your .txt/.md documents. Each claim gets one label:
    SUPPORTED              a sentence in your documents states the claim
    CONTRADICTED           a sentence states the opposite or a different number
    INSUFFICIENT EVIDENCE  your documents don't settle it under CiteCheck's rules

  CiteCheck uses strict word-matching rules, not AI. It often says
  "insufficient evidence" and shows you the closest passages instead."""

EPILOG = """\
Good to know:
  CiteCheck is an experimental prototype. Don't use it for high-stakes decisions.
  CiteCheck runs on your computer. It sends nothing over the network.
  It checks only the documents you give it. It does not check real-world truth."""


class _Parser(argparse.ArgumentParser):
    """argparse with plain-language errors and "did you mean" suggestions."""

    def error(self, message):
        found = re.search(r"unrecognized arguments: (\S+)", message)
        if found:
            option = found.group(1).split("=")[0]
            if option == "--split":
                hint = ' "--split" belongs to "check". For "eval", use --subset dev or --subset test.'
            else:
                close = [o for o in difflib.get_close_matches(option, KNOWN_OPTIONS, n=3) if o != option]
                hint = f' Did you mean "{close[0]}"?' if close else ""
            self.exit(EXIT_USAGE, f'Unknown option "{option}".{hint}\nRun citecheck --help for all options.\n')
        self.exit(EXIT_USAGE, f"{message}\nRun citecheck --help for all options.\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="citecheck", description=DESCRIPTION, epilog=EPILOG,
                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"CiteCheck {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="{check,eval}")

    check = commands.add_parser(
        "check", help="check an AI answer against a folder of documents",
        description=DESCRIPTION, epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    check.add_argument("answer_file", metavar="answer-file", help="the AI answer, as a UTF-8 text file")
    check.add_argument("docs_folder", metavar="docs-folder", help="folder of .txt or .md documents")
    check.add_argument("--split", choices=("sentences", "lines"), default="sentences",
                       help="sentences (default) or lines (one claim per line)")
    check.add_argument("--out", default="citecheck-report", metavar="DIR",
                       help="where to save reports (default: ./citecheck-report)")
    check.add_argument("--format", choices=("md", "json", "both"), default="both",
                       help="md, json or both (default: both)")
    check.add_argument("--no-color", action="store_true",
                       help="plain text only (also respects the NO_COLOR variable)")
    check.add_argument("--quiet", action="store_true", help="print only the summary and file paths")
    check.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)

    evaluate = commands.add_parser(
        "eval", help="run the labelled evaluation set",
        description="Run CiteCheck on the labelled evaluation set and print the metrics.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    evaluate.add_argument("--dataset", default=None, metavar="PATH",
                          help="evaluation folder (default: the eval/ folder next to the code)")
    evaluate.add_argument("--subset", choices=("dev", "test"), default="dev",
                          help="dev (default; tuning results only) or test (release decisions; "
                               "runs only after every test case is reviewed and the data is frozen)")
    evaluate.add_argument("--out", default="citecheck-report", metavar="DIR",
                          help="where to save eval-results.json (default: ./citecheck-report)")
    evaluate.add_argument("--no-color", action="store_true", help="plain text only")
    evaluate.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)
    return parser


def _err(message: str) -> None:
    # Messages can contain user-supplied paths, so control characters are neutralised.
    sys.stderr.write(neutralise(message.rstrip("\n")) + "\n")


def _same_folder(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except (OSError, RuntimeError):
        return False


def run_check(args) -> int:
    started = time.perf_counter()
    answer = Path(args.answer_file)
    docs_dir = Path(args.docs_folder)

    if not answer.is_file():
        _err(f'Can\'t find the answer file "{answer}". Check the path and try again. '
             "Example: citecheck check answer.txt docs/")
        return EXIT_USAGE
    if docs_dir.is_file():
        _err(f'"{docs_dir}" is a file, not a folder. Give CiteCheck the folder that holds '
             "your .txt documents.")
        return EXIT_USAGE
    if not docs_dir.is_dir():
        _err(f'Can\'t find the folder "{docs_dir}". Check the path and try again.')
        return EXIT_USAGE
    if _same_folder(Path(args.out), docs_dir):
        # Reports quote the documents. Saved next to them, they would be read
        # as documents on the next run.
        _err(f'The report folder "{args.out}" is the documents folder. Choose a different '
             "folder with --out, for example --out citecheck-report.")
        return EXIT_USAGE

    try:
        text, _ = read_text(answer)
    except TextReadError as exc:
        if exc.kind == "too_large":
            _err(f'"{answer.name}" is larger than 1 MB. Check a shorter answer.')
        else:
            _err(f'Couldn\'t read "{answer.name}" as text (unsupported encoding). '
                 "Save it as UTF-8 and try again.")
        return EXIT_USAGE

    split = split_answer(text, args.split)
    if not split.claims:
        _err(f'Nothing to check: "{answer.name}" has no claims CiteCheck can check '
             "(only blank lines, questions or lead-ins). Add some statements and try again.")
        return EXIT_USAGE
    if len(split.claims) > MAX_CLAIMS:
        _err(f'"{answer.name}" has {len(split.claims)} claims. CiteCheck checks up to {MAX_CLAIMS} '
             "at a time. Split the answer into smaller files and try again.")
        return EXIT_USAGE

    try:
        docs, warnings = load_documents(docs_dir)
    except InputLimitError as exc:
        _err(str(exc))
        return EXIT_USAGE
    if not docs:
        _err(f'No documents found in "{docs_dir}". CiteCheck reads .txt and .md files '
             "(it does not look inside subfolders). Add some and try again.")
        return EXIT_USAGE

    corpus = Corpus(docs)
    results = [check_claim(claim, corpus) for claim in split.claims]
    for claim in split.claims:
        if len(claim.text) > LONG_CLAIM_CHARS:
            warnings.append(f"Claim {claim.id} is very long. Strict rules rarely match long claims. "
                            "Splitting it into shorter statements usually gives better results.")

    run = RunReport(
        answer_name=answer.name,
        docs_folder=str(docs_dir),
        doc_count=len(docs),
        split_mode=args.split,
        results=results,
        skipped=split.skipped,
        blank_lines=split.blank_lines,
        warnings=warnings,
        elapsed=time.perf_counter() - started,
    )
    sys.stdout.write(render_terminal(run, Style.detect(sys.stdout, args.no_color), quiet=args.quiet))

    try:
        paths, renamed = write_reports(run, Path(args.out), args.format)
    except OSError:
        _err(f'Can\'t write to "{args.out}". You may not have permission to write there. '
             "Choose another folder with --out.")
        return EXIT_USAGE
    sys.stdout.write("\nReports saved:\n")
    for path in paths:
        sys.stdout.write(f"  {neutralise(str(path))}\n")
    if renamed:
        sys.stdout.write("A report with the same name already existed, so the new files have "
                         "a timestamp in their names.\n")
    sys.stdout.write('Reminder: check important claims yourself using the "Find it" lines '
                     "and the closest passages.\n")
    return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    if args.command is None:
        parser.print_help()
        return EXIT_USAGE
    try:
        if args.command == "check":
            return run_check(args)
        from .evaluation.run import run_eval
        return run_eval(args)
    except MemoryError:
        _err("Not enough memory to finish. Close other apps, or check fewer documents at a time.")
        return EXIT_FAILURE
    except Exception:
        if getattr(args, "debug", False):
            raise
        _err("Something went wrong inside CiteCheck. Run the same command with --debug to see the details.")
        return EXIT_FAILURE
