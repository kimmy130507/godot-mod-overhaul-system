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
Protocol Handler Module
Handles OS integration (Registry) and Inter-Process Communication (IPC).
"""

import os
import socket
import sys
import threading
import time
import winreg
from typing import Callable

from gmos.utils import logger

# Port for local IPC. GMOS listens here for links from secondary instances.
IPC_PORT = 27028
IPC_HOST = "127.0.0.1"


def register_url_handler(protocol: str = "nxm") -> None:
    """Registers GMOS as the handler for the given protocol (e.g. 'nxm')."""
    if sys.platform != "win32":
        logger.warning("Protocol registration only supported on Windows currently.")
        return

    exe_path = sys.executable

    # Handle frozen vs source execution
    if getattr(sys, "frozen", False):
        command = f'"{exe_path}" "%1"'
    else:
        script = os.path.abspath(sys.argv[0])
        command = f'"{exe_path}" "{script}" "%1"'

    key_path = f"Software\\Classes\\{protocol}"

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(
                key, "", 0, winreg.REG_SZ, f"URL:GMOS {protocol} Protocol"
            )
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")

        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, f"{key_path}\\DefaultIcon"
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"{exe_path},0")

        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, f"{key_path}\\shell\\open\\command"
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)

        logger.info(f"Successfully registered handler for {protocol}://")

    except Exception as e:
        logger.error(f"Failed to register protocol {protocol}: {e}")
        raise e


class LinkListener:
    """Listens for links sent by secondary processes via raw Sockets."""

    def __init__(self, callback: Callable[[str], None]):
        self.callback = callback
        self.running = True
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Allow reuse address to prevent "Address already in use" on quick restarts
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.thread.start()

    def _listen_loop(self) -> None:
        bound = False
        for _ in range(10):
            try:
                self.server_socket.bind((IPC_HOST, IPC_PORT))
                self.server_socket.listen(5)
                bound = True
                logger.info(f"IPC: Listening for links on {IPC_HOST}:{IPC_PORT}")
                break
            except OSError:
                time.sleep(0.5)

        if not bound:
            logger.error(
                f"IPC FATAL: Could not bind to {IPC_HOST}:{IPC_PORT} after retries. Link handling will NOT work."
            )
            return

        while self.running:
            try:
                # Accept connection with a timeout to allow checking self.running
                self.server_socket.settimeout(1.0)
                try:
                    client, _ = self.server_socket.accept()
                except socket.timeout:
                    continue

                # Read data
                client.settimeout(2.0)
                data = client.recv(4096)
                if data:
                    msg = data.decode("utf-8").strip()
                    if "://" in msg:
                        logger.info(f"IPC: Received link: {msg}")
                        self.callback(msg)
                        try:
                            client.sendall(b"OK")
                        except Exception as e:
                            logger.error(f"IPC: Failed to send ACK: {e}")
                client.close()
            except OSError:
                # Socket closed (likely shutdown)
                break
            except Exception as e:
                logger.error(f"IPC connection error: {e}")

    def stop(self) -> None:
        self.running = False
        try:
            self.server_socket.close()
        except Exception:
            pass


def send_link_to_existing_instance(link: str) -> bool:
    """Sends the link to the PRIMARY instance and waits for confirmation."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect((IPC_HOST, IPC_PORT))
        sock.sendall(link.encode("utf-8"))
        try:
            response = sock.recv(1024)
            sock.close()
            if response == b"OK":
                return True
            else:
                logger.warning("IPC: Connected but received invalid response.")
                return False
        except socket.timeout:
            logger.warning(
                "IPC: Connected but timed out waiting for ACK (Zombie process?)."
            )
            sock.close()
            return False

    except (ConnectionRefusedError, TimeoutError):
        return False
    except Exception as e:
        logger.error(f"Failed to send link to existing instance: {e}")
        return False
