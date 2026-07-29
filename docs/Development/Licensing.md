# GMOS Licensing Guidelines

GMOS is licensed under **GPL-3.0-or-later**.

# 1. User Responsibilities

Anyone modifying GMOS must:
- keep the GPL-3 license
- distribute source alongside binaries
- retain all copyright headers
- retain all SPDX notices

# 2. File Header Rules

### Full Header (source files)
For all primary source files:

```

# GMOS - Godot Mod Overhaul System

# Copyright (C) 2025-2026 Kim

#

# This file is part of GMOS.

#

# GMOS is free software: you can redistribute it and/or modify

# it under the terms of the GNU General Public License as published by

# the Free Software Foundation, either version 3 of the License, or

# (at your option) any later version.

```

### SPDX Header (small/trivial files)

```

# SPDX-License-Identifier: GPL-3.0-or-later

```

# 3. Third-Party Tools

GMOS interacts with:
- GDRE Tools (MIT License)
- Godot Engine (MIT License)

These do **not** conflict with GPL-3.

# 4. Mod License Note

Mods built with GMOS are **not required** to be GPL-3.

The runtime payload (the Sandbox Autoload and the `gmos_override.pck` package) dynamically injected into the game utilizes MIT-compatible GDScript and native engine archives, so modders may use any license for their specific mod files.

# 5. EULA and Liability Disclaimer

GMOS modifies game data and dynamically injects an override package (`gmos_override.pck`) and a Sandbox Autoload into the engine at runtime. These engine-level injections and file modifications may violate a game's End User License Agreement (EULA) or Terms of Service (ToS), potentially triggering anti-cheat mechanisms or account restrictions. GMOS is provided 'AS IS', without warranty of any kind. The authors are not liable for any damages, data loss, or account restrictions. You assume all responsibility for its use.
