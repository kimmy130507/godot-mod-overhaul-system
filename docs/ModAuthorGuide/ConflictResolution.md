# Conflict Resolution & Debugging

GMOS provides tools to detect, visualize, and resolve conflicts between mods. Understanding these tools helps you create **compatibility patches** or debug why your mod isn't applying correctly.

## 1. How GMOS Detects Conflicts

A conflict occurs when two mods attempt to modify the exact same resource. GMOS categorizes conflicts into two types:

### Soft Conflicts (Overwrites)
* **Scenario**: Mod A replaces `player.gd`. Mod B also replaces `player.gd`.
* **Default Behavior**: **Last Mod Wins**. Mod B (loading later) overwrites Mod A.
* **Resolution**: Usually acceptable. If Mod A is required, Mod B should list it in `[Dependencies]` to ensure B loads *after* A.

### Hard Conflicts (Logic Collisions)
* **Scenario**: Mod A creates a function `get_gold()`. Mod B tries to create the *same* function `get_gold()`.
* **Default Behavior**: The Patcher flags this. Depending on the mode (Headless vs UI), it may halt or skip the second operation to prevent game crashes.

## 2. The Merge Studio (UI)

GMOS now features a dedicated **Merge Studio** for visualizing conflicts and creating unified patches. See [File Diffing & Merge Engine](docs/Internals/FileDiffing.md) for internal engine behavior.

1.  Click the **Merge Studio** button (merge icon) in the dashboard toolbar.
2.  The window displays a tree of all files with detected conflicts.

### Resolving Conflicts
The Merge Studio offers a **Context-Aware Editor**:

* **Conflict Zones**: Conflicting functions or variables are highlighted in **Red/Pink**.
* **Resolution**: Click a red zone to open the **Resolution Modal**.
    * **Candidates**: Choose between "Mod A", "Mod B", or write a "Custom Patch" (Vanilla is shown for comparison but cannot be applied as the winner).
    * **Preview**: See a side-by-side diff of exactly what will change.
* **Status**: Resolved zones turn **Green**.

Once all conflicts are resolved, click **Generate Patch**. GMOS will generate a `GMOS_Unified_Patch` mod containing your decisions and update the load order to prioritize it.

## 3. Headless Resolution (CLI)

For automated builds or servers, you can control conflict strategy using the command line interface (`gmos`). See [CLI Mode](docs/UserGuide/UsingMods.md#cli-mode).

```bash
gmos --game-dir "<path>" --mods-dir "<path>" patch --conflict [STRATEGY]

```

| Strategy | Behavior | Use Case |
| --- | --- | --- |
| `overwrite` | (Default) The last loaded mod wins. | Standard gameplay. |
| `fail` | Abort immediately if any file overlap is detected. | strict CI/CD testing to ensure mod isolation. |

## 4. Creating Compatibility Patches

If your mod conflicts with another popular mod, you don't need to bundle their files. You can create a **Compatibility Patch**.

1. **Dependencies**: Add the target mod to your `mod.mos`:
```ini
[Dependencies]
requires = TheOtherMod

```


2. **Targeting**: Use `[VariablePatch]` or `[FunctionPatch]` to surgically edit *their* script instead of replacing the whole file.
3. **Load Order**: Since you listed the dependency, GMOS guarantees your patch runs *after* the other mod has applied its changes.

### Example: Patching a Patch

If "Mod A" adds a variable `mana`, your mod can patch that *new* variable even though it doesn't exist in Vanilla.

```ini
; Targeting a variable introduced by another mod
[VariablePatch]
res://player.gd::mana = my_patch.gd::mana_tweak ; mode=replace

```