from __future__ import annotations

import os
import subprocess
import sys
import time

# Ensure we can import the local gmos package
sys.path.insert(0, os.getcwd())


def run_zombie_process(lock_path: str, hold_time: float) -> subprocess.Popen[bytes]:
    """
    Simulates the 'Old' instance.
    Acquires lock, holds it for `hold_time` seconds, then exits.
    """
    code = f"""
import sys, time, os
sys.path.insert(0, r"{os.getcwd()}")
from gmos.io.locking import manager

print(f"ZOMBIE: Acquiring lock on {{sys.argv[1]}}")
if manager.acquire(sys.argv[1], graceful_restart=False):
    print("ZOMBIE: Locked. Sleeping...")
    time.sleep({hold_time})
    print("ZOMBIE: Exiting.")
else:
    print("ZOMBIE: Failed to lock!")
    sys.exit(1)
"""
    return subprocess.Popen(
        [sys.executable, "-c", code, lock_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def run_new_process(lock_path: str) -> subprocess.Popen[bytes]:
    """
    Simulates the 'New' instance starting up immediately.
    """
    code = f"""
import sys, time, os
sys.path.insert(0, r"{os.getcwd()}")
from gmos.io.locking import manager

print(f"NEW: Attempting to acquire lock...")
start = time.time()
# This should invoke the Smart Grace Period (wait 0.5s)
if manager.acquire(sys.argv[1], graceful_restart=True):
    elapsed = time.time() - start
    print(f"NEW: Success! Acquired lock after {{elapsed:.2f}}s")
else:
    print("NEW: Failed (Another instance is actually running)")
    sys.exit(1)
"""
    return subprocess.Popen(
        [sys.executable, "-c", code, lock_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_smart_grace_period() -> None:
    lock_file = os.path.abspath("test_grace.lock")
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
        except OSError:
            pass

    print("--- TEST: Simulating Restart Race Condition ---")

    # 1. Start "Zombie" (Old Instance) - closes after 0.2s
    # This simulates the OS taking a moment to release the handle
    zombie = run_zombie_process(lock_file, hold_time=0.3)

    # Give it a tiny moment to actually grab the lock
    time.sleep(0.1)

    # 2. Start "New Instance" immediately
    # Without the fix, this would crash immediately.
    # With the fix, it should wait ~0.5s and succeed.
    new_proc = run_new_process(lock_file)

    stdout, stderr = new_proc.communicate()
    z_out, _ = zombie.communicate()

    # Pylance needs to know these are not None before decoding
    assert z_out is not None
    assert stdout is not None
    assert stderr is not None

    print("\n[Zombie Output]:")
    print(z_out.decode())

    print("[New Instance Output]:")
    output = stdout.decode()
    print(output)

    if new_proc.returncode == 0 and "Success!" in output:
        print(
            "\nPASS: Smart Grace Period worked. New instance waited for zombie to close."
        )
    else:
        print("\nFAIL: New instance crashed or failed to acquire lock.")
        print("Stderr:", stderr.decode())

    # Cleanup
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
        except OSError:
            pass


if __name__ == "__main__":
    test_smart_grace_period()
