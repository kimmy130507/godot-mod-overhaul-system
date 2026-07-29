# Patch Types

GMOS supports five core patch types. Each serves a different purpose and has different merging semantics.

# 1. FileReplace

Replaces the target file entirely.

```ini
[FileReplace]
res://scripts/inventory.gd = patches/inventory_rewrite.gd
res://assets/sword.png = patches/weapons/new_sword.png
```

Use this when:

  * rewriting a script entirely,
  * replacing textures or audio.

**Adding New Files:**
To add new assets (e.g., a new sword texture), you do not need a specific "patch" command. Simply include the file in your mod folder and reference it in your scripts using `load("res://mods/MyMod/sword.png")`.

# 2. VariablePatch

Modifies individual variables or constants inside a `.gd` script.

```ini
[VariablePatch]
res://player.gd::speed = patches/stats.gd::speed; mode=replace
res://player.gd::inventory = patches/stats.gd::new_inv; mode=add
res://player.gd::mana = patches/stats.gd::mana; mode=create
```

**Modes:**

| mode      | behavior                        |
| --------- | ------------------------------- |
| `replace` | Overwrite existing variable block. |
| `add`     | Append lines to an existing variable (useful for arrays/dicts). |
| `create`  | Append new variable to the file. |

**Alias:**
The section `[DataAdd]` is an alias for `VariablePatch` with `mode=create`.

# 3. FunctionPatch

Modifies or wraps complete functions at their outer perimeters.
 
GMOS utilizes the `CSTParser` to token-scan scripts to resolve macro boundaries of functions. 
Multiple `prefix_` and `postfix_` updates affecting the same function are compiled sequentially into clean, flat aggregation blocks rather than deep, nested hierarchies. Each injected block is annotated with explicit mod origins to streamline runtime debugging.


```ini
[FunctionPatch]
res://combat.gd::hit = patches/combat_mod.gd::prefix_hit
res://combat.gd::hit = patches/combat_mod.gd::hit
```

GMOS determines behavior by **source function name**:

| Source name prefix | Meaning             | Example               |
| ------------------ | ------------------- | --------------------- |
| `prefix_`          | Run before original | `prefix_take_damage`  |
| `postfix_`         | Run after original  | `postfix_take_damage` |
| None               | Full replace        | `take_damage`         |

**Creating New Functions:**

You can add entirely new functions to a script by setting the mode to `create` in the manifest line metadata.

```ini
res://combat.gd::new_special_move = patches/combat_mod.gd::new_special_move ; mode=create
```

**Renaming in Replace Mode:**
When performing a full replace, you can name the function in your patch file whatever you like (e.g., `my_cool_hook`). GMOS will automatically rewrite the signature to match the target function's name (e.g., `_process`) during the patch process.

# 4. SmartPatch

Injects targeted logic *inside* an existing function or variable block structural boundary without replacing it entirely. This is the most resilient approach for avoiding logic collisions.

* **Anchor Mode**: Tokenizes both the block and your `anchor="..."` code string snippet. Splicing occurs immediately after the matching sequence regardless of surrounding formatting churn.
* **Boundary Mode**: If no anchor is defined, setting `at=start` injects lines immediately below the block header's colon, while `at=end` splices code directly before the final block dedent line.

```ini
[SmartPatch]
; Inject at the start of the function (right after the declaration)
res://player.gd::_ready = patches/hooks.gd::init_hook ; at=start

; Inject at the end of the function (default behavior)
res://player.gd::_ready = patches/hooks.gd::cleanup_hook ; at=end

; Inject at specific token anchor
res://player.gd::_ready = patches/hooks.gd::init_hook ; anchor="super._ready()"

```

**Metadata:**

| field | description |
| --- | --- |
| `anchor` | A snippet of code (string) to search for inside the target block. Code is injected after the line containing the match. |
| `at` | `end` (default) or `start`. Ignored if `anchor` is used. |

**Example:**
If `player.gd` contains:

```gdscript
func take_damage(amount):
    health -= amount
    if health <= 0:
        die()

```

And your manifest uses an anchor:

```ini
res://player.gd::take_damage = my_patch.gd ; anchor="health -= amount"

```

The code from `my_patch.gd` will be inserted immediately after the line `health -= amount`.

- If your manifest uses `at=start`:

```ini 
res://player.gd::take_damage = my_patch.gd ; at=start

```

The code from `my_patch.gd` will be inserted immediately after the line `func take_damage(amount):`.

- If your manifest uses `at=end`:

```ini 
res://player.gd::take_damage = my_patch.gd ; at=end

```

The code from `my_patch.gd` will be inserted at the very bottom of the function (after `die()`).

# 5. BinaryPatch
Applies a binary delta (`bsdiff4`) to non-text assets.

```ini 
[BinaryPatch]
res://art/player.tex = patches/player_skin.bin

```

Use this to apply compact binary diffs for large assets instead of shipping full file replacements. Multiple binary patches on the same file without strict ordering will flag a conflict.

# Patch Priority

GMOS resolves:
1.  dependencies (`[Dependencies]`)
2.  load order (user defined or folder name)
3.  persistent conflict policies

In a conflict, GMOS opens the **Merge Studio** to let you select the winning code or write a custom patch.
