# GMOS - Godot Mod Overhaul System
# Copyright (C) 2025 Kim
#
# This file is part of GMOS.
#
# GMOS is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# GMOS is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with GMOS.  If not, see <https://www.gnu.org/licenses/>.
"""
Security Module: Static analysis for Godot mods.
Scans GDScript files for potentially dangerous operations.
"""

import os
import re
from typing import Iterator, List, NamedTuple

from gmos.utils import logger


class SecurityRisk(NamedTuple):
    file: str
    line: int
    code: str
    reason: str
    severity: str  # 'HIGH', 'MEDIUM', 'LOW'


# --- Lexer Definitions ---
class TokenType:
    IDENTIFIER = "ID"
    DOT = "DOT"
    LPAREN = "LPAREN"
    STRING = "STR"
    OTHER = "OTH"


class Token(NamedTuple):
    type: str
    value: str
    line: int


class GDScriptLexer:
    """
    A simple tokenizer for GDScript to allow analysis resilient to spacing and formatting.
    """

    # Regex for tokens
    # 1. Strings (simple double/single quote handling)
    # 2. Identifiers (include .new for convenience or separate dots)
    # 3. Operators
    # 4. Comments (ignored)
    # 5. Whitespace (ignored)

    TOKEN_SPEC = [
        ("STRING", r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'"),
        ("COMMENT", r"#.*"),
        ("DOT", r"\."),
        ("LPAREN", r"\("),
        ("RPAREN", r"\)"),
        ("IDENTIFIER", r"[a-zA-Z_][a-zA-Z0-9_]*"),
        ("NEWLINE", r"\n"),
        ("SKIP", r"[ \t]+"),
        ("OTHER", r"."),
    ]

    TOK_REGEX = re.compile("|".join(f"(?P<{pair[0]}>{pair[1]})" for pair in TOKEN_SPEC))

    def tokenize(self, code: str) -> Iterator[Token]:
        line_num = 1
        for mo in self.TOK_REGEX.finditer(code):
            kind = mo.lastgroup
            value = mo.group()

            if kind == "NEWLINE":
                line_num += 1
                continue
            elif kind == "SKIP" or kind == "COMMENT":
                continue
            elif kind == "STRING":
                yield Token(TokenType.STRING, value, line_num)
            elif kind == "IDENTIFIER":
                yield Token(TokenType.IDENTIFIER, value, line_num)
            elif kind == "DOT":
                yield Token(TokenType.DOT, ".", line_num)
            elif kind == "LPAREN":
                yield Token(TokenType.LPAREN, "(", line_num)
            else:
                yield Token(TokenType.OTHER, value, line_num)


class SecurityAnalyzer:
    """
    Analyzes token streams for dangerous sequences.
    """

    # Definitions of dangerous sequences
    # Format: ([List of Token Types/Values], Reason, Severity)
    # To match a specific identifier, we use the string value.

    def scan(self, tokens: List[Token], rel_path: str) -> List[SecurityRisk]:
        risks: List[SecurityRisk] = []
        n = len(tokens)

        for i in range(n):
            t = tokens[i]

            # 1. Detection: OS.execute
            # Sequence: ID(OS) -> DOT -> ID(execute)
            if t.type == TokenType.IDENTIFIER and t.value == "OS":
                if self._match_sequence(tokens, i, ["OS", TokenType.DOT, "execute"]):
                    risks.append(
                        self._create_risk(
                            t,
                            rel_path,
                            "Executes external system commands (RCE)",
                            "HIGH",
                        )
                    )
                elif self._match_sequence(
                    tokens, i, ["OS", TokenType.DOT, "get_environment"]
                ):
                    risks.append(
                        self._create_risk(
                            t, rel_path, "Reads system environment variables", "MEDIUM"
                        )
                    )
                elif self._match_sequence(
                    tokens, i, ["OS", TokenType.DOT, "shell_open"]
                ):
                    risks.append(
                        self._create_risk(
                            t, rel_path, "Opens external links/files", "MEDIUM"
                        )
                    )

            # 2. Detection: File Deletion (Directory.new().remove / DirAccess.remove_absolute)
            if t.type == TokenType.IDENTIFIER:
                if t.value == "DirAccess":
                    if self._match_sequence(
                        tokens, i, ["DirAccess", TokenType.DOT, "remove_absolute"]
                    ):
                        risks.append(
                            self._create_risk(
                                t, rel_path, "Deletes files (Godot 4)", "HIGH"
                            )
                        )
                elif t.value == "Directory":
                    # Directory.new().remove(...)
                    # This is harder because of .new(). We look for 'Directory' and then 'remove' shortly after?
                    # Let's try strict sequence: Directory -> . -> new -> ( -> ) -> . -> remove
                    if self._match_sequence(
                        tokens,
                        i,
                        [
                            "Directory",
                            TokenType.DOT,
                            "new",
                            TokenType.LPAREN,
                            "RPAREN",
                            TokenType.DOT,
                            "remove",
                        ],
                    ):
                        risks.append(
                            self._create_risk(
                                t, rel_path, "Deletes files (Godot 3)", "HIGH"
                            )
                        )

            # 3. Detection: Network
            if t.type == TokenType.IDENTIFIER and t.value in (
                "HTTPClient",
                "HTTPRequest",
            ):
                # Just instantiation is suspicious enough
                if self._match_sequence(tokens, i, [t.value, TokenType.DOT, "new"]):
                    risks.append(
                        self._create_risk(
                            t,
                            rel_path,
                            f"Low-level network access ({t.value})",
                            "MEDIUM",
                        )
                    )

            # 4. Detection: Binary Loading
            # load("...dll") or load('...so')
            if t.type == TokenType.IDENTIFIER and t.value == "load":
                # Check next token is LPAREN, then STRING
                if i + 2 < n:
                    t_next = tokens[i + 1]
                    t_arg = tokens[i + 2]
                    if (
                        t_next.type == TokenType.LPAREN
                        and t_arg.type == TokenType.STRING
                    ):
                        content = t_arg.value.lower()
                        if ".dll" in content or ".so" in content or ".dylib" in content:
                            risks.append(
                                self._create_risk(
                                    t,
                                    rel_path,
                                    "Loads binary extension (DLL/SO)",
                                    "HIGH",
                                )
                            )

        return risks

    def _match_sequence(
        self, tokens: List[Token], start_idx: int, pattern: List[str]
    ) -> bool:
        """
        Checks if the tokens starting at start_idx match the pattern.
        Pattern entries can be:
        - A specific string value (e.g., "OS") matching an IDENTIFIER's value
        - A TokenType (e.g., TokenType.DOT) matching the token's type
        """
        if start_idx + len(pattern) > len(tokens):
            return False

        for offset, criterion in enumerate(pattern):
            t = tokens[start_idx + offset]
            if criterion in (TokenType.DOT, TokenType.LPAREN, TokenType.STRING):
                if t.type != criterion:
                    return False
            else:
                # Assume text match implies Identifier (or strict value match)
                if t.value != criterion:
                    return False
        return True

    def _create_risk(
        self, token: Token, file: str, reason: str, severity: str
    ) -> SecurityRisk:
        return SecurityRisk(
            file=file,
            line=token.line,
            code=f"Token: {token.value} (Context analysis)",
            reason=reason,
            severity=severity,
        )


def scan_file(file_path: str, rel_path: str) -> List[SecurityRisk]:
    """Scans a single file for dangerous patterns."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        lexer = GDScriptLexer()
        tokens = list(lexer.tokenize(content))

        analyzer = SecurityAnalyzer()
        return analyzer.scan(tokens, rel_path)
    except Exception as e:
        logger.debug("Security scan failed for %s: %s", file_path, e)
    return []


def scan_mod(mod_path: str) -> List[SecurityRisk]:
    """
    Recursively scans a mod directory for security risks.
    Returns a list of findings.
    """
    all_risks: List[SecurityRisk] = []
    if not os.path.isdir(mod_path):
        return all_risks

    for root, _, files in os.walk(mod_path):
        for file in files:
            if file.endswith(".gd") or file.endswith(".tscn"):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, mod_path)
                all_risks.extend(scan_file(full_path, rel_path))
            elif (
                file.endswith(".dll") or file.endswith(".so") or file.endswith(".dylib")
            ):
                # Binary files are inherently risky
                all_risks.append(
                    SecurityRisk(
                        file=os.path.relpath(os.path.join(root, file), mod_path),
                        line=0,
                        code="BINARY",
                        reason="Compiled binary code (DLL/SO) cannot be audited.",
                        severity="HIGH",
                    )
                )

    return all_risks
