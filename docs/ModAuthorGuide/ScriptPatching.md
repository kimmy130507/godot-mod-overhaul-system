# Script Patching & Sandbox System

This section covers how GMOS processes GDScript files:
- parsing
- rewriting
- security enforcement

# 1. Script Sanitization

Before writing patch results to the runtime directory, GMOS automatically scans `.gd` files for specific Remote Code Execution (RCE) vectors.

**Forbidden patterns:**
- `OS.execute(...)`
- `OS.shell_open(...)`
- `ClassDB.instantiate(...)`
- `Engine.get_singleton(...)`
- `call(...)`, `callv(...)`, `call_deferred(...)`
- `load(...)`

These are rewritten using **Token Stream Substitution** to:

```gdscript
GMOS_Sandbox.secure_execute(...)
GMOS_Sandbox.secure_shell_open(...)
GMOS_Sandbox.secure_call(...)
GMOS_Sandbox.secure_load(...)
```

This protects the user from malicious mods attempting to run external commands or open phishing links.

*Note: Static analysis (scanning) checks for other risks like FileAccess, but active rewriting currently focuses on these RCE vectors.*

# 2. Function Wrapping Internals

GMOS uses **CST (Concrete Syntax Tree) Parsing** via `GDScriptLexer` to accurately identify function boundaries, ensuring robustness against erratic whitespace or comments.

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
    #--- START PREFIX PATCH: [MyMod] my_mod.gd::prefix_jump ---
    print("Before jump")
    #--- ORIGINAL FUNCTION BODY ---
    velocity.y = -200
    #--- END PREFIX PATCH ---
```

### Postfix example
Similar to prefix, but the patch code is appended after the original function body.

Original:
```gdscript
func jump():
    velocity.y = -200

```

Postfix patch (`postfix_jump`):

```gdscript
func postfix_jump():
    print("After jump")

```

Resulting patched function:

```gdscript
func jump():
    #--- ORIGINAL FUNCTION BODY ---
    velocity.y = -200
    #--- START POSTFIX PATCH: [MyMod] my_mod.gd::postfix_jump ---
    print("After jump")
    #--- END POSTFIX PATCH ---

```

# 3. Variable Extraction

For variable patches:

```ini
res://player.gd::speed = patches/vars.gd::speed; mode=replace
```

GMOS:

1. Locates the variable block in the source file using Regex.
2. Locates the matching variable declaration in the target file.
3. **Replace**: Swaps the entire block (including multiline definitions).
4. **Add**: Injects the *inner content* of the source block into the target block (useful for adding items to a list/dictionary).
5. **Create**: Appends the entire source block to the end of the target file.

# 4. SmartPatch Injection

For SmartPatches (Anchors):

1. **Tokenization**: The target file is converted into a stream of tokens (Identifiers, Keywords, Operators).
2. **Anchor Search**: GMOS scans the stream for the sequence of tokens defined in your `anchor="..."`.
3. **Injection**: The patch content is inserted relative to that anchor (Append/Prepend/Insert) while maintaining indentation context.

This allows for highly resilient patching that survives changes to surrounding whitespace or unrelated code.

# 5. Limitations

  * **Inline Functions**: Anonymous functions (lambdas) cannot be targeted individually.
  * **Renaming**: You cannot use GMOS to rename an existing function or variable in the game code; you must match the original names.
