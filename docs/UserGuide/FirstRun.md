# First Run — Configuration

This guide outlines the initial setup process for GMOS using the multi-game Instance Manager.

## 1. First-run Legal Notice

On first run, GMOS will present a short legal notice that informs you:

- GMOS dynamically injects code and override packages (`gmos_override.pck`) into the engine.
- Modifying games and injecting files may violate a game's EULA or terms of service and trigger anti-cheat software.
- You are responsible for compliance with any third-party EULA.
- GMOS authors are not liable for bans, account actions, or damages.

Acknowledge the notice to store your consent in the global registry.

## 2. Adding a Game Instance

GMOS no longer uses a single-game setup wizard. On first launch, you will use the **Instance Manager** to define your game environments. See the [Instances & Profiles Guide](docs/UserGuide/InstancesAndProfiles.md).

1. Click **Add Instance** in the Instance Manager.
2. Browse and select the **Game Directory** containing your game executable.
3. Provide a **Name** for your instance (e.g., "Buckshot Roulette").
4. Select the instance and click **Activate** to load the Dashboard.

## 3. Mods Directory

When an instance is activated, GMOS ensures a `mods/` directory exists inside the Game Directory:

```
\<GameDir\>/mods/

```

All mods for this specific game instance must be installed here.

## 4. Executable Manager (Launch Overrides)

If your game requires specific launch arguments or you need to run an alternate binary (e.g., debug mode, a dedicated server), configure this via the Executable Manager (see [Custom Executables](docs/UserGuide/InstancesAndProfiles.md#3-custom-executables)):

1. Open the **Dropdown Bar** in the dashboard and click on `<Edit...>`.
2. Click **Add** to create a new entry in the Executable Manager and enter your custom arguments or paths.
3. Select your new configuration on the dropdown bar to use it when clicking **Run**.

## 5. Network & API Setup

To use the integrated Mod Browser and Download Manager, you must provide a Nexus Mods API key:

1. Open **Settings** on the GMOS dashboard.
2. Enter your Personal API Key generated from your Nexus Mods account settings.

## 6. Refresh Mods

Click **Refresh Mod List** on the dashboard to populate your load order. GMOS will validate mod manifests (`mod.mos`) and flag dependency or structure errors.

## 7. Apply Patch & Start Game

When satisfied with enabled mods and ordering:

1. Click **Patch**. GMOS resolves load order, enforces file policies, and applies modifications to the game directory safely via the VFS/Symlink cache.
2. Click **Run** to launch the active executable. 

Vanilla backups are retained (`.bak`) for automated rollback on the next run.

