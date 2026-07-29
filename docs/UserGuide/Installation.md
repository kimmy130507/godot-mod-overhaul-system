# Installation & Verification

This guide explains how to download GMOS, verify it, and prepare your system for modding Godot games.

## 1. Download GMOS

GMOS binaries are available on the project’s official GitHub Releases page.
The build pipeline provides OS-specific archives:

* **Windows**: `GMOS-windows-latest.zip`
  * Contains: `GMOS.exe`, `GMOS.exe.sha256`, `GMOS.exe.sig.asc`
* **macOS**: `GMOS-macos-latest.zip`
  * Contains: `GMOS-Installer.dmg` (Disk Image), signature files.
* **Linux**: `GMOS-linux-latest.zip`
  * Contains: `GMOS` (Portable binary), signature files.

Each release includes verification files:
* `*.sha256` — checksum
* `*.sig.asc` — GPG detached signature

## 2. Verify Integrity (SHA256)

### Linux
```sh
sha256sum -c GMOS.sha256
````

### macOS

```sh
shasum -a 256 -c GMOS-Installer.dmg.sha256
```

### Windows (PowerShell)

```powershell
Get-FileHash -Path .\GMOS.exe -Algorithm SHA256
```

Compare the output hash with the contents of the `.sha256` file provided on the release page. If they do not match, **do not run the program**.

## 3. Verify Authenticity (GPG Signature)

### 1. Import the GMOS public key

```sh
gpg --import gmos-pub.asc
```

### 2. Verify the signature

**Windows:**

```sh
gpg --verify GMOS.exe.sig.asc GMOS.exe
```

**macOS:**

```sh
gpg --verify GMOS-Installer.dmg.sig.asc GMOS-Installer.dmg
```

**Linux:**

```sh
gpg --verify GMOS.sig.asc GMOS
```

If verification fails, contact the project maintainers immediately.

## 4. System Requirements

| Component | Requirement | Notes |
| :--- | :--- | :--- |
| **OS** | Windows, Linux, macOS | 64-bit required. |
| **Disk Space** | \~20MB for Tool | + Space for game backups. |
| **GDRE Tools** | **Optional** | Only required if you use the **Developer SDK** to decompile games. |
| **Godot Editor** | **Optional** | Only required if you are a **Mod Author** creating new mods. |
| **Python** | **None** | GMOS is a standalone binary; no system Python is needed. |

## 5. Directory Preparation

GMOS automatically manages the directory structure. When you add a new Game Instance:
1.  It creates a `gmos_data` folder for configuration and cache.
2.  It creates a `mods` folder if one does not exist.
3.  It verifies the presence of `game.exe` or `project.binary`.

```
<GameDir>/
├── game.exe
├── main.pck (optional)
└── mods/           <-- REQUIRED
    ├── ModA/
    └── ModB/
```

**Important:** Mods must live inside `<GameDir>/mods/`. Many mod scripts reference assets using paths like `res://mods/...`; placing them elsewhere will break runtime loading.

## 6. First-Time Launch

1.  Download & verify the binary.
2.  **Windows/Linux:** Place `GMOS.exe` (or `GMOS`) anywhere on your system.
    **macOS:** Open `GMOS-Installer.dmg` and drag `GMOS.app` to your Applications folder.
3.  Launch GMOS.
4.  Use the **Instance Manager** to add your game directory and activate it.

See [First Run Guide](docs/ModAuthorGuide/FirstRun.md) for detailed instructions.

### CLI Mode

You can also run GMOS from the command line:

```sh
./GMOS --help
```