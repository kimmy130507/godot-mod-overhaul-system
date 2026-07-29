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
import http.server
import os
import shutil
import socketserver
import threading
import zipfile
from typing import Any, Optional, cast

import requests

from gmos.core.session import GmosSession
from gmos.net.manifest import LobbyManifest, generate_manifest
from gmos.utils import logger

PORT = 27027


class ModRequestHandler(http.server.SimpleHTTPRequestHandler):
    """
    Custom handler to serve the manifest and zip mods on the fly.
    Security: ONLY serves files from the GMOS mods directory.
    """

    def do_GET(self) -> None:
        if self.path == "/manifest.json":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()

            # Generate fresh manifest
            session = cast(GmosSession, cast(Any, self.server).gmos_session)
            manifest = generate_manifest(session)
            self.wfile.write(manifest.to_json().encode())
            return

        if self.path.startswith("/download/"):
            mod_name = self.path.split("/")[-1]
            session = cast(GmosSession, cast(Any, self.server).gmos_session)

            # Find the mod
            target_mod = next(
                (m for m in session.mods if os.path.basename(m.path) == mod_name), None
            )

            if target_mod and os.path.isdir(target_mod.path):
                # We need to zip it on the fly or send a pre-zipped archive
                # Cache the zip to avoid re-compressing static mods on every request
                archive_path = f"{target_mod.path}.zip"
                should_rebuild = True
                if os.path.exists(archive_path):
                    # Check if folder has been modified since zip was created
                    # (Simple check: compare dir mtime vs zip mtime)
                    dir_mtime = os.path.getmtime(target_mod.path)
                    zip_mtime = os.path.getmtime(archive_path)
                    if zip_mtime >= dir_mtime:
                        should_rebuild = False

                if should_rebuild:
                    # Generate zip (base_name, format, root_dir)
                    # shutil expects base_name without extension
                    base_no_ext = os.path.splitext(archive_path)[0]
                    shutil.make_archive(base_no_ext, "zip", target_mod.path)

                try:
                    self.send_response(200)
                    self.send_header("Content-type", "application/zip")
                    self.send_header(
                        "Content-Length", str(os.path.getsize(archive_path))
                    )
                    self.send_header(
                        "Content-Disposition", f'attachment; filename="{mod_name}.zip"'
                    )
                    self.end_headers()

                    with open(archive_path, "rb") as f:
                        cast(Any, shutil).copyfileobj(f, self.wfile)
                except Exception as e:
                    logger.error(f"P2P Transfer Error: {e}")
                return

        self.send_error(404, "File not found")


class P2PHost:
    def __init__(self, session: GmosSession, port: int = PORT):
        self.session = session
        self.port = port
        self.httpd: Optional[socketserver.TCPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        logger.info(f"Starting P2P Host on port {self.port}...")
        self.httpd = socketserver.TCPServer(("", self.port), ModRequestHandler)
        # Monkey-patch session onto the server instance
        # Type checkers generally don't like this, but we use cast() in the handler to resolve it.
        cast(Any, self.httpd).gmos_session = self.session

        self.thread = threading.Thread(target=self.httpd.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        print(f"HOST: Lobby open at http://localhost:{self.port}/manifest.json")

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


class P2PClient:
    def __init__(self, session: GmosSession):
        self.session = session

    def connect(self, host_ip: str, port: int = PORT) -> None:
        base_url = f"http://{host_ip}:{port}"
        print(f"Connecting to {base_url}...")

        try:
            # Get Manifest
            resp = requests.get(f"{base_url}/manifest.json", timeout=5)
            resp.raise_for_status()
            manifest = LobbyManifest.from_json(resp.text)

            print(
                f"Connected to {manifest.host_name}. Found {len(manifest.mods)} mods."
            )

            self.sync_mods(manifest, base_url)

        except Exception as e:
            logger.error(f"P2P Sync Failed: {e}")

    def sync_mods(self, manifest: LobbyManifest, base_url: str) -> None:
        for mod in self.session.mods:
            mod.is_enabled = False

        for entry in manifest.mods:
            print(f"Syncing {entry.name}...")

            # Check if we have it locally
            local_mod = next(
                (
                    m
                    for m in self.session.mods
                    if os.path.basename(m.path) == entry.mod_id
                ),
                None,
            )

            # TODO: Add hash comparison here for strictness
            if local_mod:
                print("  - Found locally. Enabling.")
                local_mod.is_enabled = True
            else:
                print("  - Missing. Downloading from Host...")
                self._download_from_host(base_url, entry.mod_id)

        # Apply changes
        print("Sync complete. Patching...")
        # Note: In real usage, you'd trigger the CLI patch command or session.apply_changes

    def _download_from_host(self, base_url: str, mod_id: str) -> None:
        url = f"{base_url}/download/{mod_id}"
        dest_zip = os.path.join(self.session.mods_dir, f"{mod_id}.zip")

        with requests.get(url, stream=True, timeout=10) as r:
            r.raise_for_status()
            with open(dest_zip, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

        # Using existing session logic would be best, but simple unzip for now:
        extract_to = os.path.join(self.session.mods_dir, mod_id)
        with zipfile.ZipFile(dest_zip, "r") as zf:
            zf.extractall(extract_to)
        os.remove(dest_zip)

        # Refresh session to pick it up
        for _ in self.session.refresh_mods():
            pass

        new_mod = next(
            (m for m in self.session.mods if os.path.basename(m.path) == mod_id), None
        )
        if new_mod:
            new_mod.is_enabled = True
