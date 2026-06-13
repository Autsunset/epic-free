# -*- coding: utf-8 -*-
"""Tests for the disk-retention pruner (no network, uses tmp files)."""

import os
import time

from epic_free.cleanup import prune_old_files


def _make_file(path, age_days: int) -> None:
    path.write_text("x")
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))


def test_prune_removes_files_older_than_retention(tmp_path):
    old = tmp_path / "old.mp4"
    fresh = tmp_path / "fresh.mp4"
    _make_file(old, age_days=40)
    _make_file(fresh, age_days=1)

    removed = prune_old_files(tmp_path, retention_days=30)

    assert removed == 1
    assert not old.exists()
    assert fresh.exists()


def test_prune_recurses_into_subdirs_but_keeps_dirs(tmp_path):
    sub = tmp_path / "purchase_debug"
    sub.mkdir()
    old = sub / "shot.png"
    _make_file(old, age_days=99)

    removed = prune_old_files(tmp_path, retention_days=30)

    assert removed == 1
    assert not old.exists()
    # directory roots are preserved (recorder / hcaptcha-challenger expect them)
    assert sub.exists()


def test_prune_disabled_when_retention_is_zero(tmp_path):
    old = tmp_path / "old.mp4"
    _make_file(old, age_days=999)

    assert prune_old_files(tmp_path, retention_days=0) == 0
    assert old.exists()


def test_prune_missing_directory_is_noop(tmp_path):
    assert prune_old_files(tmp_path / "does-not-exist", retention_days=30) == 0
