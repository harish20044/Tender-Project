"""Shared browser-health checks for the portal scrapers.

Both the document downloader and the award scraper drive a long sequence of
page loads against the same portals, and both have to survive the browser
dying underneath them. The test for "has Chrome gone?" lives here so the two
cannot drift apart in what they recognise — a scraper that misses a crash
reports every remaining item as a content failure, which is the most
misleading way for a batch to end.
"""

from __future__ import annotations

# Phrases a dead or unreachable browser produces. An auto-update swapping the
# binary under a live session is the usual cause, and a long CAPTCHA sequence
# is exactly the workload that outlives one.
DEAD_SESSION_MARKERS = (
    "invalid session id",
    "browser has closed the connection",
    "not connected to devtools",
    "chrome not reachable",
    "disconnected",
    "target window already closed",
    "no such window",
)


def session_is_dead(exc: BaseException) -> bool:
    """Whether an exception means the browser itself has gone.

    Only this is worth restarting for. Treating an ordinary timeout as a
    crash throws away a working session and pays the CAPTCHA gate again;
    treating a crash as ordinary turns the rest of the batch into failures.
    """
    if type(exc).__name__ == "InvalidSessionIdException":
        return True
    text = str(exc).lower()
    return any(marker in text for marker in DEAD_SESSION_MARKERS)
