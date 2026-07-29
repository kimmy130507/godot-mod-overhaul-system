# Network Stack Internals

The `gmos.net` pillar handles all external communication, including mod repository integration, file downloading, and peer-to-peer syncing.

# 1. Architecture

The network stack is built around an **Abstract Provider** model, allowing GMOS to support multiple backends (Nexus, Thunderstore, GitHub) without changing the UI code.

## 1.1. The Provider Interface
Defined in `gmos.net.api.RepositoryProvider`, every backend must implement:

* `get_name()`: Returns the display name of the provider.
* `search(query)`: Returns a list of `ModDTO`.
* `get_metadata(mod_id)`: Fetches detailed info.
* `get_download_url(mod_id)`: Resolves the actual file URI.
* `resolve_dependencies(mod_id)`: Returns a list of required Mod IDs.
* `get_rate_limits()`: Returns current API rate limits (daily/hourly).

## 1.2. Data Transfer Objects (DTO)
To normalize data from different APIs, we use `ModDTO`. This ensures the UI (`BrowserView`) never interacts with raw JSON from a specific vendor.

# 2. Components

## 2.1. NexusProvider (`providers.py`)
The default implementation for Nexus Mods.
* **Rate Limiting**: Automatically tracks `x-rl-daily-remaining` headers to prevent API bans.
* **Scraping**: Since the Nexus API does not strictly expose dependency trees, this provider falls back to HTML scraping for `resolve_dependencies`.

## 2.2. DownloadManager (`downloader.py`)
Handles binary file transfer.
* **Threading**: Uses a dedicated `ThreadPoolExecutor` (`max_workers=4`) to prevent UI freezes.
* **Session**: Maintains a `requests.Session` for TCP Keep-Alive and connection pooling.
* **Atomicity**: Downloads write to a temporary file (`gmos_dl_xxxx`) and use `shutil.move` only upon successful completion.

## 2.3. P2P Subsystem (`p2p.py`)
Enables LAN multiplayer mod syncing without a central server.
* **Host**: Spins up a transient `http.server` on port `27027`. It generates a `LobbyManifest` containing hashes of all currently enabled mods.
* **Client**: Connects to the host, diffs the manifest against local mods, and downloads missing archives via direct HTTP stream.
* **Security**: The request handler is strictly scoped to the `mods/` directory to prevent directory traversal attacks.

# 3. Protocol Flow (IPC)

GMOS handles `nxm://` links (from the "Mod Manager Download" button) via `gmos.core.protocol`.

1.  **OS Trigger**: Browser launches `gmos.exe "nxm://..."`.
2.  **Lock Check**: The new process checks for the App Lock.
3.  **Handover**:
    * If locked (App running): It connects to `127.0.0.1:27028` (IPC) and sends the link to the primary instance.
    * If unlocked: It starts normally and processes the link.