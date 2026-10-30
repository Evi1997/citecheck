"""Security boundaries: no network code, no secrets, no execution of input."""

import ast
import contextlib
import io
import socket
from pathlib import Path
from unittest import mock

from citecheck.cli import main
from tests.helpers import APP_DIR, TempDocsTestCase

PACKAGE = APP_DIR / "citecheck"
BANNED_MODULES = {"socket", "urllib", "http", "ssl", "subprocess", "ftplib", "smtplib",
                  "telnetlib", "asyncio", "requests", "pickle", "shelve", "ctypes"}
BANNED_CALLS = {"eval", "exec", "compile", "__import__"}


def python_files():
    return sorted(PACKAGE.rglob("*.py"))


class StaticSecurityTests(TempDocsTestCase):
    def test_no_network_or_process_imports(self):
        for path in python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names = [node.module]
                for name in names:
                    self.assertNotIn(name.split(".")[0], BANNED_MODULES, f"{path.name} imports {name}")

    def test_no_dynamic_code_execution(self):
        for path in python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, BANNED_CALLS, f"{path.name} calls {node.func.id}")

    def test_no_urls_or_key_lookups_in_code(self):
        for path in python_files():
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("http://", source, path.name)
            self.assertNotIn("https://", source, path.name)
            for word in ("API_KEY", "api_key", "SECRET", "PASSWORD", "BEARER"):
                self.assertNotIn(word, source, f"{path.name} mentions {word}")

    def test_only_expected_environment_variables(self):
        allowed = {"NO_COLOR", "TERM"}
        read = set()
        for path in python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                # os.environ.get("X") and os.getenv("X")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    target = ast.dump(node.func)
                    if ("environ" in target and node.func.attr == "get") or node.func.attr == "getenv":
                        read.update(a.value for a in node.args[:1] if isinstance(a, ast.Constant))
                # "X" in os.environ
                if isinstance(node, ast.Compare) and "environ" in ast.dump(node):
                    if isinstance(node.left, ast.Constant):
                        read.add(node.left.value)
                # os.environ["X"]
                if isinstance(node, ast.Subscript) and "environ" in ast.dump(node.value):
                    read.update(c.value for c in ast.walk(node.slice) if isinstance(c, ast.Constant))
        self.assertTrue(read <= allowed, f"unexpected environment variables: {read - allowed}")

    def test_check_runs_with_network_disabled(self):
        self.write_docs({"policy.txt": "Policy\n\nThe refund window is 14 days.\n"})
        answer = self.tmp / "answer.txt"
        answer.write_text("The refund window is 14 days.\n", encoding="utf-8")

        def refuse(*args, **kwargs):
            raise AssertionError("CiteCheck tried to open a network socket")

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(socket, "socket", refuse), mock.patch.object(socket, "create_connection", refuse):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["check", str(answer), str(self.docs_dir), "--out", str(self.tmp / "out")])
        self.assertEqual(code, 0, err.getvalue())

    def test_symlinks_outside_the_folder_are_skipped(self):
        outside = self.tmp / "secret.txt"
        outside.write_text("Outside\n\nThe refund window is 99 days.\n", encoding="utf-8")
        link = self.docs_dir / "link.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported here")
        self.write_docs({"policy.txt": "Policy\n\nThe refund window is 14 days.\n"})
        from citecheck.textio import load_documents
        docs, warnings = load_documents(self.docs_dir)
        self.assertEqual([d.name for d in docs], ["policy.txt"])
        self.assertTrue(any("outside the folder" in w for w in warnings))
