# SPDX-FileCopyrightText: 2025 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
from gmos.core import security


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

    lexer = security.GDScriptLexer()
    tokens = list(lexer.tokenize(code))
    analyzer = security.SecurityAnalyzer()
    risks = analyzer.scan(tokens, "test.gd")

    assert len(risks) == 1
    assert risks[0].severity == "HIGH"
    assert "RCE" in risks[0].reason


def test_detect_binary_load() -> None:
    code = "load ( 'res://hack.dll' )"
    lexer = security.GDScriptLexer()
    tokens = list(lexer.tokenize(code))
    risks = security.SecurityAnalyzer().scan(tokens, "test.gd")

    assert len(risks) == 1
    assert "DLL" in risks[0].reason


def test_safe_code_ignored() -> None:
    code = """
    func _ready():
        var OS_emulator = "safe"
        print(OS_emulator)
    """
    lexer = security.GDScriptLexer()
    tokens = list(lexer.tokenize(code))
    risks = security.SecurityAnalyzer().scan(tokens, "test.gd")

    assert len(risks) == 0
