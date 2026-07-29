# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
# GMOS Security Test Suite
# Covers Security Analysis (Scanning) and AST Sanitization (Rewriting)

from pathlib import Path

from gmos.core import security
from gmos.core.parser import GDScriptParser, Lexer
from gmos.core.security import scan_file, secure_rewrite_script

# --- Security Analysis (Scanner) Tests ---


def test_detect_obfuscated_rce() -> None:
    """Verify that spacing/comments don't fool the tokenizer."""
    # Malicious code with heavy obfuscation
    code = """
    extends Node
    func _ready():
        var x = OS  # Comment
        .   # Another comment
        execute(  'cmd.exe' )
    """

    lexer = Lexer()
    tokens = lexer.tokenize(code)
    parser = GDScriptParser(tokens)
    analyzer = security.SecurityAnalyzer()
    risks = analyzer.scan(parser.parse(), code.encode("utf-8"), "test.gd")

    # Taint Analysis now flags 'var x = OS' (Aliasing) AND 'OS.execute' (RCE).
    # We expect at least 1, but likely 2 risks.
    assert len(risks) >= 1

    # Verify the RCE is definitely caught
    rce_found = any("RCE" in r.reason for r in risks)
    assert rce_found, "RCE risk not found in risks list"


def test_detect_binary_load() -> None:
    code = "load ( 'res://hack.dll' )"
    lexer = Lexer()
    tokens = lexer.tokenize(code)
    parser = GDScriptParser(tokens)
    risks = security.SecurityAnalyzer().scan(
        parser.parse(), code.encode("utf-8"), "test.gd"
    )

    assert len(risks) == 1
    assert "DLL" in risks[0].reason


def test_safe_code_ignored() -> None:
    code = """
    func _ready():
        var OS_emulator = "safe"
        print(OS_emulator)
    """
    lexer = Lexer()
    tokens = lexer.tokenize(code)
    parser = GDScriptParser(tokens)
    risks = security.SecurityAnalyzer().scan(
        parser.parse(), code.encode("utf-8"), "test.gd"
    )

    assert len(risks) == 0


def test_scanner_detection_integration(tmp_path: Path) -> None:
    """
    Verifies that the scanner flags high-severity risks using the file wrapper.
    """
    bad_script = tmp_path / "malware.gd"
    bad_script.write_text(
        'func init():\n    DirAccess.remove_absolute("user://save.dat")',
        encoding="utf-8",
    )

    risks = scan_file(str(bad_script), "malware.gd")

    assert len(risks) > 0
    risk = risks[0]
    assert risk.severity == "HIGH"
    assert "DirAccess" in risk.code
    assert "Deletes files" in risk.reason


# --- AST Sanitization (Rewriter) Tests ---


def test_ast_rewrite_execution() -> None:
    """
    Roadmap Feature: AST Sanitization.
    Ensures that OS.execute is rewritten to the Sandbox, even with weird formatting.
    """
    # 1. Standard Case
    code = 'func _ready():\n    OS.execute("rm", ["-rf", "/"])\n'
    rewritten = secure_rewrite_script(code)
    assert "GMOS_Sandbox.secure_execute" in rewritten
    assert "OS.execute" not in rewritten

    # 2. Obfuscated / Spaced Case (Regex usually fails here)
    obfuscated = 'func hack():\n    OS  .   execute  ( "cmd" )'
    rewritten = secure_rewrite_script(obfuscated)
    # Should preserve the spacing but change the caller
    assert "GMOS_Sandbox  .   secure_execute" in rewritten


def test_ast_rewrite_shell_open() -> None:
    """
    Ensures OS.shell_open (link opening) is sandboxed.
    """
    code = 'OS.shell_open("https://malware.site")'
    rewritten = secure_rewrite_script(code)
    assert "GMOS_Sandbox.secure_shell_open" in rewritten


def test_safe_code_untouched_rewriter() -> None:
    """
    Ensures that innocent code looking like OS calls is NOT modified.
    """
    # A variable named OS_execute should not be touched
    code = "var OS_execute = 10\nprint(OS_execute)"
    rewritten = secure_rewrite_script(code)
    assert code == rewritten


def test_rewrite_load_to_secure_load() -> None:
    """
    RASP: Ensure load() calls are rewritten to GMOS_Sandbox.secure_load().
    """
    # 1. Simple Case
    code = 'var res = load("res://exploit.pck")'
    rewritten = secure_rewrite_script(code)
    assert "GMOS_Sandbox.secure_load" in rewritten
    # Ensure the original 'load(' call is gone.
    # (secure_load contains 'load', so we check specific context or count)
    assert " = load(" not in rewritten

    # 2. Spaced/Obfuscated Case
    code = 'var x = load   (   "res://file.gd" )'
    rewritten = secure_rewrite_script(code)
    assert "GMOS_Sandbox.secure_load" in rewritten
    # Ensure no standalone load
    assert " = load " not in rewritten
