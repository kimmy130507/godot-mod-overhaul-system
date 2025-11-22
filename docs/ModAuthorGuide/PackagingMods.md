# Packaging & Shipping Mods

This guide explains how to package and distribute a polished GMOS mod.

---

## 1. Required structure

Your mod folder must contain:

```

MyMod/
    mod.mos
    patches/
    assets/
    README.txt (optional but recommended)

```

---

## 2. Naming Rules

- Folder name must be unique  
- Folder name is used for dependency resolution  
- Use ASCII-safe names to avoid path issues across OSes  

---

## 3. Ship only your changes

Do **not** include:

- vanilla game files  
- unrelated assets  
- temporary Godot import caches (`.import/`)  

---

## 4. Versioning

Use SemVer:

- major: breaking changes  
- minor: new features  
- patch: small fixes  

GMOS displays version and warns on mismatches.

---

## 5. Test Before Shipping

1. In GMOS, click **Simulate & Diff**  
2. Examine the generated unified diff  
3. Confirm patch hunks represent the intended changes  
4. Apply Patch  
5. Launch game  
6. Validate behavior with other known mods installed

---

## 6. Distribution

Recommended packaging:

- `.zip` with top-level mod folder  
- include optional `README.md`  
- include the version number in the filename  

Example:

```

CoolMod-1.0.2.zip

```

When a user extracts this ZIP into `<GameDir>/mods/`, it should produce:

```

<GameDir>/mods/CoolMod/

```

---

## 7. Legal Note

Mods may interact with proprietary game files.  
Respect the game's EULA and ensure your distribution does not include copyrighted assets without permission.

GMOS authors are not responsible for EULA violations.
