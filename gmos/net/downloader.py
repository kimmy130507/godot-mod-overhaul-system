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
Download Manager.
Handles asynchronous file transfers via a dedicated thread pool.
"""

import os
import tempfile
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional, cast

try:
    import requests
except ImportError:
    requests = cast(Any, None)
from gmos.io import replace_with_retries
from gmos.utils import get_logger

logger = get_logger()

# Dedicated executor for Network I/O
_net_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gmos_net")


class DownloadError(Exception):
    pass


class DownloadManager:
    """
    Orchestrates file downloads with progress reporting.
    """

    def __init__(self) -> None:
        if requests is None:
            logger.warning(
                "Network module initialized but 'requests' library is missing."
            )
        else:
            # Initialize Session for TCP Keep-Alive and Connection Pooling
            self.session = requests.Session()
            req_any = cast(Any, requests)
            adapter = req_any.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10)
            sess_any = cast(Any, self.session)
            sess_any.mount("https://", adapter)
            sess_any.mount("http://", adapter)

    def download_file(
        self,
        url: str,
        dest_path: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Future[str]:
        """
        Submits a download task to the network thread pool.

        Args:
            url: Source URL.
            dest_path: Final destination path on disk.
            progress_callback: Function(downloaded_bytes, total_bytes) called periodically.
            cancel_event: Optional event to signal cancellation.

        Returns:
            Future[str]: Resolves to dest_path upon success.
        """
        return _net_executor.submit(
            self._download_worker, url, dest_path, progress_callback, cancel_event
        )

    def _download_worker(
        self,
        url: str,
        dest_path: str,
        progress_callback: Optional[Callable[[int, int], None]],
        cancel_event: Optional[threading.Event],
    ) -> str:
        """Blocking worker function executed in thread."""
        if requests is None:
            raise ImportError("The 'requests' library is required for downloads.")

        temp_name = ""
        try:
            # Create a temp file in the same directory to ensure atomic move works later
            dest_dir = os.path.dirname(os.path.abspath(dest_path))
            os.makedirs(dest_dir, exist_ok=True)

            fd, temp_name = tempfile.mkstemp(dir=dest_dir, prefix="gmos_dl_")
            os.close(fd)

            logger.info("Starting download: %s -> %s", url, dest_path)

            with self.session.get(url, stream=True, timeout=30) as r:
                r.raise_for_status()

                total_size = int(r.headers.get("content-length", 0))
                downloaded = 0

                # Chunk size: 1MB
                chunk_size = 1024 * 1024
                last_update_time = 0.0

                with open(temp_name, "wb", buffering=chunk_size) as f:
                    for chunk in r.iter_content(chunk_size=chunk_size):
                        if cancel_event and cancel_event.is_set():
                            r.close()
                            raise DownloadError("Cancelled by user.")
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            # Throttle updates to ~10Hz
                            now = time.time()
                            if progress_callback and (
                                now - last_update_time > 0.1 or downloaded == total_size
                            ):
                                progress_callback(downloaded, total_size)
                                last_update_time = now

            # Safe atomic move to final destination
            replace_with_retries(temp_name, dest_path)
            logger.info("Download complete: %s", dest_path)
            return dest_path
        except Exception as e:
            logger.error("Download failed for %s: %s", url, e)
            # Cleanup temp file
            if temp_name and os.path.exists(temp_name):
                try:
                    os.remove(temp_name)
                except OSError:
                    pass
            raise DownloadError(f"Download failed: {e}") from e
