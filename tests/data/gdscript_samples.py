# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
from typing import Dict, List, Tuple

# Define the structure of an element in SAMPLES
# (source_code: str, expected_items: Dict[str, List[str]])
SampleType = Tuple[str, Dict[str, List[str]]]

SAMPLES: List[SampleType] = [
    # simple vars
    ("var x = 1\nvar y = 'a'\n", {"var": ["x", "y"], "func": []}),
    # inline function
    (
        "func foo(): return 1\n\nfunc bar():\n    print('ok')\n",
        {"func": ["foo", "bar"]},
    ),
    # multiple vars with comments and whitespace
    ("# comment\nvar a=2\n    var b =3 # inline\n", {"var": ["a", "b"]}),
    # complex default values and arrays
    ("var items = [1,2,3]\nconst PI = 3.1415\n", {"var": ["items"], "const": ["PI"]}),
    # function with decorators and typed args
    (
        "@rpc\nfunc remote_call(user: String) -> void:\n    # doc\n    pass\n",
        {"func": ["remote_call"]},
    ),
    # nested func-like strings and braces (shouldn't break)
    (
        "func tricky():\n    s = 'func not_a_func()'\n    return s\n",
        {"func": ["tricky"]},
    ),
    # edge: no newline at eof
    ("func end_of_file():\n    pass", {"func": ["end_of_file"]}),
]
