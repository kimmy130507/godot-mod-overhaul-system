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
Repository Providers.
Concrete implementations of the RepositoryProvider interface.
"""

import re
from typing import Any, Dict, List, Optional, Union, cast

import requests

from gmos.net.api import ModDTO, RepositoryProvider, get_rate_limits, update_rate_limits
from gmos.utils import logger


class NexusProvider(RepositoryProvider):
    """
    Integration with Nexus Mods API.
    Requires an API key (personal) to search and retrieve file links.
    """

    BASE_URL = "https://api.nexusmods.com/v1"

    def __init__(self, api_key: str, game_domain: str = "godotengine"):
        self.api_key = api_key
        self.game_domain = game_domain
        self.headers = {
            "apikey": self.api_key,
            "Application-Name": "GMOS",
            "User-Agent": "GMOS/2.0.0 (non-commercial)",
        }
        self._cache: Dict[str, ModDTO] = {}
        self._dep_pattern = re.compile(
            r"nexusmods\.com/" + re.escape(self.game_domain) + r"/mods/(\d+)"
        )

    def get_name(self) -> str:
        return f"Nexus Mods ({self.game_domain})"

    def search(
        self, query: str, sort_mode: str = "Name", page: int = 1, limit: int = 50
    ) -> List[ModDTO]:
        """
        Supports Sort Modes: 'Name', 'Downloads', 'Endorsements', 'Date'
        """
        if not self.api_key:
            logger.warning("Nexus API Key not set.")
            return []

        results: List[ModDTO] = []
        endpoint = ""
        params: Dict[str, Any] = {}

        try:
            # Strategy:
            # - Search query present: Use 'search.json' (supports sort).
            # - Browse mode: Use 'latest_updated.json' (reliable pagination).

            search_term = query.strip()

            if not search_term:
                # Browse Mode
                endpoint = (
                    f"{self.BASE_URL}/games/{self.game_domain}/mods/latest_added.json"
                )
                params = {"limit": limit, "page": page}
            else:
                # Search Mode
                endpoint = f"{self.BASE_URL}/games/{self.game_domain}/mods/search.json"

                api_sort = "name"
                if sort_mode == "Downloads":
                    api_sort = "downloads"
                elif sort_mode == "Endorsements/Rating":
                    api_sort = "endorsements"

                params = {
                    "term": search_term,
                    "page": page,
                    "limit": limit,
                    "sort": api_sort,
                }

            resp = requests.get(
                endpoint, headers=self.headers, params=params, timeout=15
            )
            update_rate_limits(resp.headers)
            resp.raise_for_status()

            data = cast(Union[List[Any], Dict[str, Any]], resp.json())

            # Nexus API returns a list directly for 'latest', but a dict with 'results' key for 'search'
            items: List[Dict[str, Any]] = []
            if isinstance(data, list):
                items = cast(List[Dict[str, Any]], data)
            else:
                data_dict = data
                items = cast(List[Dict[str, Any]], data_dict.get("results", []))

            for item in items:
                name = str(item.get("name", ""))
                summary = str(item.get("summary", ""))
                mod_id = str(item.get("mod_id"))
                version = str(item.get("version", "1.0.0"))
                author = str(item.get("author", "Unknown"))
                thumbnail = str(item.get("picture_url", ""))
                downloads = int(item.get("mod_downloads", 0))
                endorsements = int(item.get("endorsement_count", 0))

                # Note: Nexus API structure varies, adapting strictly to ModDTO
                dto = ModDTO(
                    id=mod_id,
                    name=name,
                    version=version,
                    author=author,
                    description=summary,
                    thumbnail_url=thumbnail,
                    # Store ID for secondary resolution
                    download_url=f"nexus://{mod_id}",
                    dependencies=[],
                    source_provider="Nexus",
                    downloads=downloads,
                    endorsements=endorsements,
                )
                self._cache[dto.id] = dto
                results.append(dto)

            # Manual sort fallback
            if search_term and sort_mode == "Downloads":
                results.sort(key=lambda x: x.downloads, reverse=True)
            elif search_term and sort_mode == "Endorsements/Rating":
                results.sort(key=lambda x: x.endorsements, reverse=True)

        except Exception as e:
            logger.error(f"Nexus search failed: {e}")

        return results

    def fetch_mod_details(self, mod_id: str) -> Optional[ModDTO]:
        """Explicitly fetches mod details from API (bypassing search cache)."""
        if not self.api_key:
            return None
        try:
            url = f"{self.BASE_URL}/games/{self.game_domain}/mods/{mod_id}.json"
            resp = requests.get(url, headers=self.headers, timeout=10)
            update_rate_limits(resp.headers)
            if resp.status_code == 200:
                item = resp.json()
                dto = ModDTO(
                    id=str(item.get("mod_id")),
                    name=str(item.get("name", "Unknown")),
                    version=str(item.get("version", "1.0.0")),
                    author=str(item.get("author", "Unknown")),
                    description=str(item.get("summary", "")),
                    thumbnail_url=str(item.get("picture_url", "")),
                    download_url=f"nexus://{mod_id}",
                    dependencies=[],
                    source_provider="Nexus",
                    downloads=int(item.get("mod_downloads", 0)),
                    endorsements=int(item.get("endorsement_count", 0)),
                )
                self._cache[mod_id] = dto
                return dto
        except Exception as e:
            logger.warning(f"Failed to fetch mod details for {mod_id}: {e}")
        return None

    def get_metadata(self, mod_id: str) -> Optional[ModDTO]:
        return self._cache.get(mod_id)

    def resolve_dependencies(self, mod_id: str) -> List[str]:
        """
        Nexus API does not expose dependencies, so we scrape the mod page.
        """
        # URL Format: https://www.nexusmods.com/{game_domain}/mods/{mod_id}
        url = f"https://www.nexusmods.com/{self.game_domain}/mods/{mod_id}"

        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; GMOS/3.0)"}
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                logger.warning(
                    f"Could not fetch Nexus mod page for dependencies: {resp.status_code}"
                )
                return []

            html = resp.text

            found_ids = set(self._dep_pattern.findall(html))

            # Remove self from dependencies
            if mod_id in found_ids:
                found_ids.remove(mod_id)

            return list(found_ids)

        except Exception as e:
            logger.error(f"Nexus dependency scrape failed for {mod_id}: {e}")
            return []

    def get_download_url(
        self, mod_id: str, version: Optional[str] = None
    ) -> Optional[str]:
        """
        Resolves the download URL for the main file.
        Prioritizes files in the 'MAIN' category (category_id=1).
        """
        if not self.api_key:
            return None

        try:
            # List Files
            files_url = (
                f"{self.BASE_URL}/games/{self.game_domain}/mods/{mod_id}/files.json"
            )
            files_resp = requests.get(files_url, headers=self.headers, timeout=15)
            update_rate_limits(files_resp.headers)
            files_resp.raise_for_status()
            files_data = files_resp.json()

            files = cast(List[Dict[str, Any]], files_data.get("files", []))
            if not files:
                return None

            # Selection: Newest file from Category 1 (Main), else newest overall.
            main_files = [f for f in files if f.get("category_id") == 1]

            if main_files:
                # Sort Main files by upload timestamp (descending)
                main_files.sort(
                    key=lambda x: int(x.get("uploaded_timestamp", 0)), reverse=True
                )
                target_file = main_files[0]
            else:
                # Fallback
                files.sort(
                    key=lambda x: int(x.get("uploaded_timestamp", 0)), reverse=True
                )
                target_file = files[0]

            file_id = target_file["file_id"]
            logger.info(
                f"Selected Nexus File ID {file_id} for Mod {mod_id} ({target_file.get('file_name')})"
            )

            # Generate Link
            link_url = f"{self.BASE_URL}/games/{self.game_domain}/mods/{mod_id}/files/{file_id}/download_link.json"
            link_resp = requests.get(link_url, headers=self.headers, timeout=15)
            update_rate_limits(link_resp.headers)
            link_resp.raise_for_status()

            locations = link_resp.json()
            if locations and len(locations) > 0:
                return str(locations[0].get("URI"))

        except Exception as e:
            logger.error(f"Failed to resolve Nexus download link for {mod_id}: {e}")
            return None
        return None

    def get_file_download_url_from_nxm(
        self, mod_id: str, file_id: str, query_params: str
    ) -> Optional[str]:
        """Finds the file corresponding to an NXM link."""
        # API: /games/{game_domain}/mods/{mod_id}/files/{file_id}/download_link.json
        endpoint = f"{self.BASE_URL}/games/{self.game_domain}/mods/{mod_id}/files/{file_id}/download_link.json?{query_params}"

        try:
            resp = requests.get(endpoint, headers=self.headers, timeout=15)
            update_rate_limits(resp.headers)
            resp.raise_for_status()

            data = cast(Union[List[Any], Dict[str, Any]], resp.json())
            # Nexus returns a list of URI locations
            if isinstance(data, list) and len(data) > 0:
                item = cast(Dict[str, Any], data[0])
                return str(item.get("URI"))

        except Exception as e:
            logger.error(f"Failed to resolve NXM file link: {e}")

        return None

    def get_rate_limits(self) -> Optional[tuple[int, int, int, int]]:
        return get_rate_limits()
