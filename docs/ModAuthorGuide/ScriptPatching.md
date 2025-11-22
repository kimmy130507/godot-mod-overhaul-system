# Script Patching & Sandbox System

This section covers how GMOS processes GDScript files:
- parsing
- rewriting
- security enforcement

---

# 1. Script Sanitization

Before writing patch results to the runtime directory, GMOS automatically scans `.gd` files for specific Remote Code Execution (RCE) vectors.

**Forbidden patterns:**
- `OS.execute(...)`
- `OS.shell_open(...)`

These are rewritten using Regex substitution to:

```gdscript
GMOS_Sandbox.secure_execute(...)
GMOS_Sandbox.secure_shell_open(...)
```

This protects the user from malicious mods attempting to run external commands or open phishing links.

*Note: Static analysis (scanning) checks for other risks like FileAccess, but active rewriting currently focuses on these RCE vectors.*

-----

# 2. Function Wrapping Internals

GMOS uses **Regex-based block extraction** (not AST parsing) to identify function boundaries.

### Prefix example

Original:

```gdscript
func jump():
    velocity.y = -200
```

Prefix patch (`prefix_jump`):

```gdscript
func prefix_jump():
    print("Before jump")
```

Resulting patched function:

```gdscript
func jump():
    #--- START PREFIX PATCH: my_mod.gd::prefix_jump ---
    print("Before jump")
    #--- ORIGINAL FUNCTION BODY ---
    velocity.y = -200
    #--- END PREFIX PATCH ---
```

### Postfix example

Similar to prefix, but the patch code is appended after the original function body.

-----

# 3. Variable Extraction

For variable patches:

```ini
res://player.gd::speed = patches/vars.gd::speed; mode=replace
```

GMOS:

1.  Locates the variable block in the source file using Regex.
2.  Locates the matching variable declaration in the target file.
3.  **Replace**: Swaps the entire block (including multiline definitions).
4.  **Add**: Injects the *inner content* of the source block into the target block (useful for adding items to a list/dictionary).
5.  **Create**: Appends the entire source block to the end of the target file.

-----

# 4. Limitations

  * **Regex Parsing**: Since GMOS uses regex and indentation counting to find blocks, scripts with highly unconventional formatting might cause parsing errors.
  * **Inline Functions**: Anonymous functions (lambdas) cannot be targeted individually.
  * **Renaming**: You cannot use GMOS to rename an existing function or variable in the game code; you must match the original names.
