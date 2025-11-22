# Installation & Verification

This guide explains how to download GMOS, verify it, and prepare your system for modding Godot games.

---

## 1. Download GMOS

GMOS binaries are available on the project’s official GitHub Releases page.
The build pipeline provides **single-file executables** bundled in a ZIP archive:

* **Windows**: `gmos.exe`
* **Linux**: `gmos` (Binary)
* **macOS**: `gmos` (Binary/Bundle)

Each release includes verification files:
* `*.sha256` — checksum
* `*.sig.asc` — GPG detached signature

---

## 2. Verify Integrity (SHA256)

### Linux / macOS
```sh
sha256sum -c dist/gmos.sha256
```

### Windows (PowerShell)

```powershell
Get-FileHash -Path .\gmos.exe -Algorithm SHA256
```

Compare the output hash with the contents of the `.sha256` file provided on the release page. If they do not match, **do not run the program**.

-----

## 3. Verify Authenticity (GPG Signature)

### 1. Import the GMOS public key

```sh
gpg --import gmos-pub.asc
```

### 2. Verify the signature

```sh
gpg --verify gmos.exe.sig.asc gmos.exe
```

If verification fails, contact the project maintainers immediately.

-----

## 4. System Requirements

| Component | Requirement | Notes |
| :--- | :--- | :--- |
| **OS** | Windows, Linux, macOS | 64-bit required. |
| **Disk Space** | \~20MB for Tool | + Space for game backups. |
| **GDRE Tools** | **Optional** | Only required if you use the **Developer SDK** to decompile games. |
| **Godot Editor** | **Optional** | Only required if you are a **Mod Author** creating new mods. |
| **Python** | **None** | GMOS is a standalone binary; no system Python is needed. |

-----

## 5. Directory Preparation

GMOS requires exactly **one** game directory. Inside that directory, GMOS expects (or will create) a `mods` folder:

```
<GameDir>/
├── game.exe
├── main.pck (optional)
└── mods/           <-- REQUIRED
    ├── ModA/
    └── ModB/
```

**Important:** Mods must live inside `<GameDir>/mods/`. Many mod scripts reference assets using paths like `res://mods/...`; placing them elsewhere will break runtime loading.

-----

## 6. First-Time Launch

1.  Download & verify the binary.
2.  Place `gmos.exe` (or `gmos`) anywhere on your system. It does **not** need to be in the game folder.
3.  Launch GMOS.
4.  Follow the **Setup Wizard** to select your game executable.

### CLI Mode

You can also run GMOS from the command line:

```sh
./gmos --help
```
