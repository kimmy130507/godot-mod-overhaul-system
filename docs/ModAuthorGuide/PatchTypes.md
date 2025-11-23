# Patch Types

GMOS supports three core patch types. Each serves a different purpose and has different merging semantics.

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

**Aliases:**
The sections `[DataAdd]` and `[DataPatch]` are aliases for `VariablePatch` with `mode=create`.

# 3. FunctionPatch

Modifies or wraps functions.

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

**⚠️ Critical Warning for Replace Mode:**
When performing a full replace (no prefix), you **MUST NOT** attempt to rename the function. The source function in your patch file must define the function with the **exact same name** as the target function. Renaming functions during a replace operation is unsupported.

# Patch Priority

GMOS resolves:

1.  dependencies (`[Dependencies]`)
2.  load order (user defined or folder name)
3.  persistent conflict policies

In a conflict, GMOS opens the hunk viewer to let you select the winning code.
