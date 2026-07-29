# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
from gmos.net.api import get_rate_limits, update_rate_limits


def test_rate_limits() -> None:
    """Verify that Nexus API rate limits are correctly parsed and stored globally."""
    headers = {
        "x-rl-daily-remaining": "15000",
        "x-rl-daily-limit": "20000",
        "x-rl-hourly-remaining": "400",
        "x-rl-hourly-limit": "500",
    }

    update_rate_limits(headers)
    daily_rem, daily_lim, hourly_rem, hourly_lim = get_rate_limits()

    assert daily_rem == 15000
    assert daily_lim == 20000
    assert hourly_rem == 400
    assert hourly_lim == 500

    # Test invalid headers are safely ignored without corrupting state
    update_rate_limits({"x-rl-daily-remaining": "invalid"})
    daily_rem_2, _, _, _ = get_rate_limits()
    assert daily_rem_2 == 15000
