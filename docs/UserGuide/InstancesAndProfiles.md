# Instances & Profiles Guide

GMOS uses a hierarchy to manage your modding setup. This allows you to support multiple games, multiple mod builds per game, and custom launch options.

## 1. Instances (Game Installs)
An **Instance** represents a specific installation of a Godot game. You can switch between instances to manage mods for different games (e.g., *Buckshot Roulette* vs *Dome Keeper*) without closing GMOS.

### Managing Instances
1.  Click the **Instance Manager** button (top-left toolbar).
2.  **Add New**:
    * Click **Add**.
    * **Name**: Give it a friendly name.
    * **Game Directory**: Browse to the folder containing the game executable.
3.  **Deployment Mode**:
    * **Packed Deployment Mode (.pck archive)**: When enabled, modified assets are compiled into a standalone `gmos_override.pck` package mounted at runtime.
    * **Loose Deployment Mode**: When disabled, modified assets are deployed directly to the game directory as loose files using symlinks or hard copies.
4.  **Activate**: Select an instance and click **Activate** to switch GMOS to that game context.

## 2. Profiles (Mod Lists)
A **Profile** is a saved configuration of:
* Which mods are enabled/disabled.
* The load order.
* Specific game settings (optional).

This lets you switch between a "Vanilla+" setup and a "Total Overhaul" setup instantly.

### Creating a Profile
1.  Navigate to the **Profiles** tab in the main dashboard.
2.  Click **Create** and enter a name.
3.  This snapshots your *current* mod list into the new profile.

### Isolation Settings
Profiles can protect your save files and configurations from corruption by keeping them separate.
* Select a profile in the list to see the **Configuration** panel on the right.
* **Isolate Profile Data (Saves & Configs)**: If checked, GMOS will launch the game with the `--user-data-dir` argument, redirecting all user data (saves, caches, and game configs) into a dedicated `<GameDir>/profiles/<ProfileName>/userdata/` folder. This keeps your modded save data completely isolated from your vanilla saves.

### Importing/Exporting
* **Export**: Share your profile with a friend by selecting it and clicking the **Export (Arrow Up)** icon. This creates a `.json` file.
* **Import**: Click the **Import (Arrow Down)** icon to load a profile sent to you.

## 3. Custom Executables

Sometimes you need to launch the game with specific command-line arguments (e.g., logging enabled) or run a different binary (e.g., a dedicated server).

1.  Open the **Dropdown Bar** in the Dashboard and click `<Edit...>`.
2.  **Add**: Create a new entry in the Executable Manager.
    * **Name**: Display name (e.g., "Debug Mode").
    * **Path**: The executable file (usually the game `.exe`).
    * **Arguments**: Flags like `--verbose` or `--rendering-driver opengl3`.
3.  **Set Default**: Choose which executable runs when you click **Play** on the dashboard.


## 4. Multiplayer (P2P Sync)

GMOS includes a Peer-to-Peer sync feature. This allows a host to enforce their exact mod list (Profile) on connecting clients, ensuring compatibility for multiplayer sessions.

### Hosting a Lobby
1.  Ensure your mods are set up exactly how you want them.
2.  Open a terminal in the GMOS directory.
3.  Run the command:
    ```bash
    gmos --game-dir "<path>" --mods-dir "<path>" p2p host
    ```
4.  GMOS will start a local server. Share your **IP Address** with your friends.

### Joining a Lobby
*Warning: This will temporarily disable your current mods to match the host.*

1.  Open a terminal.
2.  Run the command:
    ```bash
    gmos --game-dir "<path>" --mods-dir "<path>" p2p join <HOST_IP_ADDRESS>
    ```
3.  GMOS will connect to the host, download any mods you are missing, and match the host's load order exactly.