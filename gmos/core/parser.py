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
from typing import List, NamedTuple, Optional


class TokenType:
    KEYWORD = "KW"
    IDENTIFIER = "ID"
    DOT = "DOT"
    ASSIGN = "EQ"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    STRING = "STR"
    COMMENT = "COM"
    NEWLINE = "NL"
    SKIP = "WS"
    OTHER = "OTH"
    NUMBER = "NUM"


class Token(NamedTuple):
    type: str
    value: str
    line: int
    start_byte: int
    end_byte: int


class Lexer:
    """State-machine lexer resilient to string escapes, nodepaths, and line continuations."""

    def tokenize(self, code: str) -> List[Token]:
        tokens: List[Token] = []
        pos = 0
        length = len(code)
        line = 1

        while pos < length:
            c = code[pos]
            start_byte = pos

            if c in " \t":
                while pos < length and code[pos] in " \t":
                    pos += 1
                tokens.append(
                    Token(TokenType.SKIP, code[start_byte:pos], line, start_byte, pos)
                )
                continue

            if c == "\\" and pos + 1 < length and code[pos + 1] == "\n":
                pos += 2
                line += 1
                continue

            if c in "\n\r":
                if c == "\r" and pos + 1 < length and code[pos + 1] == "\n":
                    pos += 2
                else:
                    pos += 1
                tokens.append(Token(TokenType.NEWLINE, "\n", line, start_byte, pos))
                line += 1
                continue

            if c == "#":
                while pos < length and code[pos] not in "\n\r":
                    pos += 1
                tokens.append(
                    Token(
                        TokenType.COMMENT, code[start_byte:pos], line, start_byte, pos
                    )
                )
                continue

            is_string = False
            if c in "\"'":
                is_string = True
            elif c in "^r" and pos + 1 < length and code[pos + 1] in "\"'":
                is_string = True
                pos += 1
                c = code[pos]

            if is_string:
                quote = c
                is_multiline = pos + 2 < length and code[pos : pos + 3] == quote * 3
                if is_multiline:
                    quote = quote * 3
                    pos += 3
                else:
                    pos += 1

                while pos < length:
                    if not is_multiline and code[pos] in "\n\r":
                        break
                    if code[pos] == "\\":
                        pos += 2
                        continue
                    if code[pos : pos + len(quote)] == quote:
                        pos += len(quote)
                        break
                    if code[pos] == "\n":
                        line += 1
                    pos += 1
                tokens.append(
                    Token(TokenType.STRING, code[start_byte:pos], line, start_byte, pos)
                )
                continue

            if c.isalpha() or c == "_" or c.isdigit():
                while pos < length and (code[pos].isalnum() or code[pos] in "_."):
                    pos += 1
                val = code[start_byte:pos]
                t_type = (
                    TokenType.KEYWORD
                    if val
                    in (
                        "var",
                        "const",
                        "func",
                        "pass",
                        "return",
                        "if",
                        "for",
                        "while",
                        "match",
                        "elif",
                        "else",
                    )
                    else TokenType.IDENTIFIER
                )
                tokens.append(Token(t_type, val, line, start_byte, pos))
                continue

            if c == ".":
                t_type = TokenType.DOT
            elif c == "=":
                t_type = TokenType.ASSIGN
            elif c == "(":
                t_type = TokenType.LPAREN
            elif c == ")":
                t_type = TokenType.RPAREN
            else:
                t_type = TokenType.OTHER

            pos += 1
            tokens.append(Token(t_type, c, line, start_byte, pos))

        return tokens


class ASTNode:
    start_byte: int = 0
    end_byte: int = 0

    def children(self) -> List["ASTNode"]:
        return []


class RootNode(ASTNode):
    def __init__(self) -> None:
        self.statements: List["ASTNode"] = []

    def children(self) -> List["ASTNode"]:
        return self.statements


class AssignNode(ASTNode):
    def __init__(self, target: "ASTNode", value: "ASTNode", sb: int, eb: int) -> None:
        self.target, self.value, self.start_byte, self.end_byte = target, value, sb, eb

    def children(self) -> List["ASTNode"]:
        return [self.target, self.value]


class CallNode(ASTNode):
    def __init__(
        self, caller: "ASTNode", args: List["ASTNode"], sb: int, eb: int
    ) -> None:
        self.caller, self.args, self.start_byte, self.end_byte = caller, args, sb, eb

    def children(self) -> List["ASTNode"]:
        return [self.caller] + self.args


class MemberNode(ASTNode):
    def __init__(self, obj: "ASTNode", prop: str, sb: int, eb: int) -> None:
        self.obj, self.prop, self.start_byte, self.end_byte = obj, prop, sb, eb

    def children(self) -> List["ASTNode"]:
        return [self.obj]


class BinaryNode(ASTNode):
    def __init__(
        self, left: "ASTNode", op: str, right: "ASTNode", sb: int, eb: int
    ) -> None:
        self.left, self.op, self.right, self.start_byte, self.end_byte = (
            left,
            op,
            right,
            sb,
            eb,
        )

    def children(self) -> List["ASTNode"]:
        return [self.left, self.right]


class IdentifierNode(ASTNode):
    def __init__(self, name: str, sb: int, eb: int) -> None:
        self.name, self.start_byte, self.end_byte = name, sb, eb


class StringNode(ASTNode):
    def __init__(self, val: str, sb: int, eb: int) -> None:
        self.val, self.start_byte, self.end_byte = val, sb, eb


class GDScriptParser:
    """Pratt-style parser to extract structural semantics, resyncing on unknown statements."""

    def __init__(self, tokens: List[Token]):
        self.tokens = [
            t
            for t in tokens
            if t.type not in (TokenType.SKIP, TokenType.COMMENT, TokenType.NEWLINE)
        ]
        self.pos = 0

    def current(self) -> Optional[Token]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def advance(self) -> None:
        self.pos += 1

    def parse(self) -> ASTNode:
        root = RootNode()
        while self.pos < len(self.tokens):
            start_pos = self.pos
            stmt = self.parse_statement()
            if stmt:
                root.statements.append(stmt)
            if self.pos == start_pos:
                self.advance()
        return root

    def parse_statement(self) -> Optional[ASTNode]:
        tok = self.current()
        if not tok:
            return None

        if tok.type == TokenType.KEYWORD and tok.value in (
            "func",
            "pass",
            "if",
            "for",
            "while",
            "match",
            "elif",
            "else",
            "return",
        ):
            self.advance()
            return None

        if tok.type == TokenType.KEYWORD and tok.value in ("var", "const"):
            self.advance()
            ident = self.current()
            if ident and ident.type == TokenType.IDENTIFIER:
                target = IdentifierNode(ident.value, ident.start_byte, ident.end_byte)
                self.advance()
                curr_assign = self.current()
                if curr_assign and curr_assign.type == TokenType.ASSIGN:
                    self.advance()
                    try:
                        right = self.parse_expr(0)
                        return AssignNode(
                            target,
                            right,
                            tok.start_byte,
                            right.end_byte if right else target.end_byte,
                        )
                    except Exception:
                        pass
                return target

        start_pos = self.pos
        try:
            expr = self.parse_expr(0)
            curr_assign_expr = self.current()
            if curr_assign_expr and curr_assign_expr.type == TokenType.ASSIGN:
                self.advance()
                right = self.parse_expr(0)
                return AssignNode(
                    expr,
                    right,
                    expr.start_byte,
                    right.end_byte if right else expr.end_byte,
                )
            return expr
        except Exception:
            self.pos = start_pos + 1
            return None

    def get_precedence(self, tok: Token) -> int:
        if tok.type == TokenType.DOT:
            return 4
        if tok.type == TokenType.LPAREN:
            return 3
        if tok.type == TokenType.OTHER and tok.value in ("+", "-", "*", "/", "%"):
            return 2
        return 0

    def parse_expr(self, precedence: int) -> ASTNode:
        tok = self.current()
        if not tok:
            raise ValueError("EOF")
        self.advance()
        left: ASTNode
        if tok.type in (TokenType.IDENTIFIER, TokenType.OTHER):
            left = IdentifierNode(tok.value, tok.start_byte, tok.end_byte)
        elif tok.type == TokenType.STRING:
            left = StringNode(tok.value, tok.start_byte, tok.end_byte)
        elif tok.type == TokenType.LPAREN:
            left = self.parse_expr(0)
            curr_rparen = self.current()
            if curr_rparen and curr_rparen.type == TokenType.RPAREN:
                self.advance()
            else:
                raise ValueError("Missing )")
        else:
            raise ValueError("Unexpected token")

        while True:
            op_tok = self.current()
            if not op_tok or op_tok.type == TokenType.NEWLINE:
                break
            prec = self.get_precedence(op_tok)
            if precedence >= prec:
                break

            self.advance()
            if op_tok.type == TokenType.DOT:
                prop = self.current()
                if prop and prop.type == TokenType.IDENTIFIER:
                    self.advance()
                    left = MemberNode(left, prop.value, left.start_byte, prop.end_byte)
                else:
                    raise ValueError("Expected identifier")
            elif op_tok.type == TokenType.LPAREN:
                args: List[ASTNode] = []
                while True:
                    arg_tok = self.current()
                    if not arg_tok or arg_tok.type == TokenType.RPAREN:
                        break
                    try:
                        args.append(self.parse_expr(0))
                    except Exception:
                        self.advance()
                    comma_tok = self.current()
                    if comma_tok and comma_tok.value == ",":
                        self.advance()
                end_byte = left.end_byte
                end_tok = self.current()
                if end_tok and end_tok.type == TokenType.RPAREN:
                    end_byte = end_tok.end_byte
                    self.advance()
                left = CallNode(left, args, left.start_byte, end_byte)
            elif op_tok.type == TokenType.OTHER and op_tok.value in (
                "+",
                "-",
                "*",
                "/",
                "%",
            ):
                right = self.parse_expr(prec)
                left = BinaryNode(
                    left, op_tok.value, right, left.start_byte, right.end_byte
                )
            else:
                break
        return left
