# GMOS - Godot Mod Overhaul System
# Copyright (C) 2025-2026 Kim
#
# This file is part of GMOS.
#
# GMOS is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# GMOS is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with GMOS.  If not, see <https://www.gnu.org/licenses/>.
"""
Network API Abstraction Layer (DTOs and Interfaces).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional


@dataclass
class ModDTO:
    """Data Transfer Object normalizing mod metadata."""

    id: str
    name: str
    version: str
    author: str
    description: str
    thumbnail_url: str = ""
    download_url: str = ""
    dependencies: List[str] = field(default_factory=lambda: [])
    tags: List[str] = field(default_factory=lambda: [])
    source_provider: str = "Generic"  # e.g., "Nexus"
    downloads: int = 0
    endorsements: int = 0


class RepositoryProvider(ABC):
    """Abstract Base Class for Mod Repository integrations."""

    @abstractmethod
    def get_name(self) -> str:
        """Returns the display name of the provider."""
        pass

    @abstractmethod
    def search(self, query: str) -> List[ModDTO]:
        """Searches the repository for mods matching the query."""
        pass

    @abstractmethod
    def get_metadata(self, mod_id: str) -> Optional[ModDTO]:
        """Retrieves detailed metadata for a specific mod ID."""
        pass

    @abstractmethod
    def get_download_url(
        self, mod_id: str, version: Optional[str] = None
    ) -> Optional[str]:
        """Resolves the actual file download URL."""
        pass

    @abstractmethod
    def resolve_dependencies(self, mod_id: str) -> List[str]:
        """Returns a list of mod_ids that this mod depends on."""
        pass

    @abstractmethod
    def get_rate_limits(self) -> Optional[tuple[int, int, int, int]]:
        """Returns rate limits: (daily_remaining, daily_limit, hourly_remaining, hourly_limit)."""
        pass


# Global API Rate Limit State (Updated by API responses)
_daily_limit = 20000
_daily_remaining = 20000
_hourly_limit = 500
_hourly_remaining = 500


def update_rate_limits(headers: Mapping[str, Any]) -> None:
    """Parses standard Nexus API headers to update global counters."""
    global _daily_remaining, _daily_limit, _hourly_remaining, _hourly_limit
    try:
        if "x-rl-daily-remaining" in headers:
            _daily_remaining = int(headers.get("x-rl-daily-remaining", 0))
            _daily_limit = int(headers.get("x-rl-daily-limit", 0))
        if "x-rl-hourly-remaining" in headers:
            _hourly_remaining = int(headers.get("x-rl-hourly-remaining", 0))
            _hourly_limit = int(headers.get("x-rl-hourly-limit", 0))
    except ValueError:
        pass


def get_rate_limits() -> tuple[int, int, int, int]:
    """Returns (daily_rem, daily_lim, hourly_rem, hourly_lim)."""
    return (_daily_remaining, _daily_limit, _hourly_remaining, _hourly_limit)
