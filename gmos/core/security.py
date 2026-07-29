# GMOS - Godot Mod Overhaul System
# Copyright (C) 2025-2026 Kim
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
Security Module: Static analysis and AST-based Sanitization.
Scans GDScript for risks and rewrites dangerous calls to use the Sandbox.
"""

import os
from typing import Iterator, List, NamedTuple, Set, Tuple

from gmos.core.parser import (
    AssignNode,
    ASTNode,
    BinaryNode,
    CallNode,
    GDScriptParser,
    IdentifierNode,
    Lexer,
    MemberNode,
    StringNode,
)
from gmos.utils import logger


class SecurityRisk(NamedTuple):
    file: str
    line: int
    code: str
    reason: str
    severity: str


class SecurityAnalyzer:
    """
    Analyzes CST for dangerous sequences and opaque formats.
    Implements multi-pass Taint Analysis to detect aliased singletons.
    """

    TAINT_SOURCES = {
        "OS",
        "DirAccess",
        "ClassDB",
        "ProjectSettings",
        "Engine",
        "Expression",
        "Directory",
    }

    def scan(
        self, root_node: ASTNode, source_bytes: bytes, rel_path: str
    ) -> List[SecurityRisk]:
        risks: List[SecurityRisk] = []
        tainted_vars: Set[str] = set(self.TAINT_SOURCES)

        def walk(node: ASTNode) -> Iterator[ASTNode]:
            yield node
            for child in node.children():
                yield from walk(child)

        def get_ident(n: ASTNode) -> str:
            if isinstance(n, IdentifierNode):
                return n.name
            if isinstance(n, MemberNode):
                return get_ident(n.obj) + "." + n.prop
            return ""

        changed = True
        while changed:
            changed = False
            for node in walk(root_node):
                if isinstance(node, AssignNode):
                    l_name = get_ident(node.target)
                    r_name = get_ident(node.value)
                    if r_name in tainted_vars and l_name and l_name not in tainted_vars:
                        tainted_vars.add(l_name)
                        changed = True
                        risks.append(
                            self._create_risk(
                                node,
                                rel_path,
                                f"Scope Aliasing: '{l_name}' aliases tainted '{r_name}'",
                                "HIGH",
                                source_bytes,
                            )
                        )
        for node in walk(root_node):
            if isinstance(node, CallNode):
                func_text = get_ident(node.caller)
                is_reflection = func_text in (
                    "call",
                    "callv",
                    "call_deferred",
                    "execute",
                )
                is_engine = func_text == "Engine.get_singleton"

                if is_reflection or is_engine:
                    risks.append(
                        self._create_risk(
                            node,
                            rel_path,
                            f"Dynamic Reflection: {func_text} executed",
                            "CRITICAL",
                            source_bytes,
                        )
                    )
                    for arg in node.args:
                        if isinstance(arg, BinaryNode):
                            risks.append(
                                self._create_risk(
                                    arg,
                                    rel_path,
                                    "Obfuscated String Construction in reflective call",
                                    "HIGH",
                                    source_bytes,
                                )
                            )

                if "." in func_text:
                    caller, method = func_text.rsplit(".", 1)
                    if caller in tainted_vars and method in (
                        "execute",
                        "create_process",
                        "create_instance",
                        "instantiate",
                    ):
                        risks.append(
                            self._create_risk(
                                node,
                                rel_path,
                                f"Executes system command via tainted '{caller}' (RCE)",
                                "HIGH",
                                source_bytes,
                            )
                        )
                    elif method in ("remove_absolute", "remove") and (
                        caller in tainted_vars or caller in ("DirAccess", "Directory")
                    ):
                        risks.append(
                            self._create_risk(
                                node, rel_path, "Deletes files", "HIGH", source_bytes
                            )
                        )
                    elif method == "shell_open":
                        risks.append(
                            self._create_risk(
                                node,
                                rel_path,
                                "Opens external links/files",
                                "MEDIUM",
                                source_bytes,
                            )
                        )
                    elif method == "get_environment":
                        risks.append(
                            self._create_risk(
                                node,
                                rel_path,
                                "Reads system environment variables",
                                "MEDIUM",
                                source_bytes,
                            )
                        )
                    elif caller in ("HTTPClient", "HTTPRequest") and method == "new":
                        risks.append(
                            self._create_risk(
                                node,
                                rel_path,
                                "Opens network connections",
                                "MEDIUM",
                                source_bytes,
                            )
                        )
                elif func_text in ("load", "preload") and node.args:
                    arg = node.args[0]
                    if isinstance(arg, StringNode):
                        if (
                            ".dll" in arg.val.lower()
                            or ".so" in arg.val.lower()
                            or ".dylib" in arg.val.lower()
                        ):
                            risks.append(
                                self._create_risk(
                                    node,
                                    rel_path,
                                    "Loads binary extension (DLL/SO)",
                                    "HIGH",
                                    source_bytes,
                                )
                            )
        return risks

    def _create_risk(
        self, node: ASTNode, file: str, reason: str, severity: str, source_bytes: bytes
    ) -> SecurityRisk:
        return SecurityRisk(
            file=file,
            line=0,
            code=source_bytes[node.start_byte : node.end_byte].decode(
                "utf-8", errors="ignore"
            ),
            reason=reason,
            severity=severity,
        )


def scan_file(file_path: str, rel_path: str) -> List[SecurityRisk]:
    """Scans a single file for dangerous patterns."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        lexer = Lexer()
        tokens = lexer.tokenize(content)
        parser = GDScriptParser(tokens)

        analyzer = SecurityAnalyzer()
        return analyzer.scan(parser.parse(), content.encode("utf-8"), rel_path)
    except Exception as e:
        logger.debug("Security scan failed for %s: %s", file_path, e)
    return []


def scan_mod(mod_path: str) -> List[SecurityRisk]:
    """Recursively scans a mod directory."""
    all_risks: List[SecurityRisk] = []
    if not os.path.isdir(mod_path):
        return all_risks
    OPAQUE_EXTENSIONS = (".gdc", ".gdenc", ".gdextension", ".dll", ".so", ".dylib")
    for root, _, files in os.walk(mod_path):
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, mod_path)
            if file.endswith(OPAQUE_EXTENSIONS):
                all_risks.append(
                    SecurityRisk(
                        file=rel_path,
                        line=0,
                        code="BINARY",
                        reason=f"Opaque format {file} bypasses sanitization.",
                        severity="CRITICAL",
                    )
                )
            elif file.endswith(".gd") or file.endswith(".tscn"):
                all_risks.extend(scan_file(full_path, rel_path))
    return all_risks


def secure_rewrite_script(content: str) -> str:
    """
    Parses the script and rewrites dangerous calls to use the GMOS_Sandbox.
    """
    try:
        lexer = Lexer()
        parser = GDScriptParser(lexer.tokenize(content))
        root = parser.parse()
        mutations: List[Tuple[int, int, bytes]] = []
        source_bytes = content.encode("utf-8")
        tainted_vars: Set[str] = {
            "OS",
            "DirAccess",
            "ClassDB",
            "ProjectSettings",
            "Engine",
        }

        def walk(n: ASTNode) -> Iterator[ASTNode]:
            yield n
            for c in n.children():
                yield from walk(c)

        def get_ident(n: ASTNode) -> str:
            if isinstance(n, IdentifierNode):
                return n.name
            if isinstance(n, MemberNode):
                return get_ident(n.obj) + "." + n.prop
            return ""

        for node in walk(root):
            if isinstance(node, AssignNode):
                r_name = get_ident(node.value)
                l_name = get_ident(node.target)
                if r_name in tainted_vars and l_name:
                    tainted_vars.add(l_name)

            elif isinstance(node, CallNode):
                func_text = get_ident(node.caller)
                if func_text in (
                    "call",
                    "callv",
                    "call_deferred",
                    "execute",
                    "Engine.get_singleton",
                ):
                    mutations.append(
                        (
                            node.caller.start_byte,
                            node.caller.end_byte,
                            b"GMOS_Sandbox.secure_call",
                        )
                    )
                elif func_text in ("load", "preload"):
                    mutations.append(
                        (
                            node.caller.start_byte,
                            node.caller.end_byte,
                            b"GMOS_Sandbox.secure_load",
                        )
                    )
                elif "." in func_text:
                    caller, method = func_text.rsplit(".", 1)
                    if caller in tainted_vars:
                        if isinstance(node.caller, MemberNode):
                            obj_start = node.caller.obj.start_byte
                            obj_end = node.caller.obj.end_byte
                            # The property is guaranteed to be at the exact end byte of the MemberNode
                            prop_start = node.caller.end_byte - len(node.caller.prop)
                            prop_end = node.caller.end_byte
                            if method in (
                                "execute",
                                "create_process",
                                "create_instance",
                                "instantiate",
                            ):
                                mutations.append((obj_start, obj_end, b"GMOS_Sandbox"))
                                mutations.append(
                                    (prop_start, prop_end, b"secure_execute")
                                )
                            elif method == "shell_open":
                                mutations.append((obj_start, obj_end, b"GMOS_Sandbox"))
                                mutations.append(
                                    (prop_start, prop_end, b"secure_shell_open")
                                )
                        elif isinstance(node.caller, IdentifierNode):
                            start = node.caller.start_byte
                            end = node.caller.end_byte
                            if method in (
                                "execute",
                                "create_process",
                                "create_instance",
                                "instantiate",
                            ):
                                mutations.append(
                                    (start, end, b"GMOS_Sandbox.secure_execute")
                                )
                            elif method == "shell_open":
                                mutations.append(
                                    (start, end, b"GMOS_Sandbox.secure_shell_open")
                                )

        mutations.sort(key=lambda x: x[0], reverse=True)
        rewritten = bytearray(source_bytes)
        for start, end, replacement in mutations:
            rewritten[start:end] = replacement

        return rewritten.decode("utf-8")
    except Exception as e:
        logger.debug("secure_rewrite_script failed: %s", e)
        return content
