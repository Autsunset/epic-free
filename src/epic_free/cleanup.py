# -*- coding: utf-8 -*-
"""Disk-retention helper for the git-ignored ``volumes/`` artifacts.

The browser records a video every run and the claim flow saves debug
screenshots / hCaptcha caches; on an always-on deployment those directories grow
without bound. :func:`prune_old_files` deletes files older than a configurable
number of days and is called once per collection run.
"""

from __future__ import annotations

import time
from contextlib import suppress
from pathlib import Path

from loguru import logger

_SECONDS_PER_DAY = 86_400


def prune_old_files(directory: Path, retention_days: int) -> int:
    """Delete files under ``directory`` whose mtime is older than ``retention_days``.

    Returns the number of files removed. ``retention_days <= 0`` disables pruning
    (keep forever). Directories themselves are left in place (hcaptcha-challenger
    and the recorder expect their roots to exist); only files are removed. Never
    raises — cleanup must not break a collection run.
    """
    if not retention_days or retention_days <= 0:
        return 0

    directory = Path(directory)
    if not directory.exists():
        return 0

    cutoff = time.time() - retention_days * _SECONDS_PER_DAY
    removed = 0
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        with suppress(OSError):
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1

    if removed:
        logger.debug(
            "Pruned {} file(s) older than {}d under {}", removed, retention_days, directory
        )
    return removed
