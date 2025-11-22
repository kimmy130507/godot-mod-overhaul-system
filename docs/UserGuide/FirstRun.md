# First Run — Configuration

This guide steps through the configuration that GMOS needs on first launch.

---

## 1. Select the Game Executable (Required)

When GMOS launches for the first time, it will prompt you to select the target **Game Executable** (e.g., `game.exe` or `project.godot` for development).

Once selected, the Setup Wizard automatically:
1. Determines the **Game Directory** (the folder containing the executable).
2. Configures GMOS to read/write within this authoritative path.

---

## 2. Mods Directory (Automatic)

GMOS requires a `mods/` folder inside the detected Game Directory:

```

\<GameDir\>/mods/

```

The wizard will automatically create this folder if it does not exist. All mods must be installed here.

---

## 3. Launch Override (Optional)

In rare cases where the game requires specific launch arguments or a different binary than the one selected for patching, you can configure a **Launch Override** in the settings later. For most users, the executable selected in Step 1 is sufficient.

---

## 4. Force PCK Patching (Advanced)

Some games ignore loose files and require PCK-level modifications. Enable **Force PCK Patching** only if:

- The game ignores loose files in practice, and
- You understand PCK rebuilds are experimental and may require GDRE Tools to fully reconstruct assets.

---

## 5. First-run Legal Notice

On first run, GMOS will present a short legal notice that informs you:

- Modifying games may violate a game's EULA or terms of service.
- You are responsible for compliance with any third-party EULA.
- GMOS authors are not liable for bans, account actions, or damages.

Acknowledge the notice to continue. This acknowledgement is stored in your local GMOS config.

---

## 6. Refresh Mods

After configuration, click **Refresh Mods** to populate the Mods list. GMOS validates manifests and reports errors or missing fields.

---

## 7. Apply Patch & Start Game

When satisfied with enabled mods and ordering:

1. Click **Apply Patch** — GMOS will run the deterministic patch pipeline and write files atomically.
2. Click **Start Game** — launches the patched runtime using the executable configured in Step 1.

Your original game files remain untouched (backups stored as `*.bak`).
