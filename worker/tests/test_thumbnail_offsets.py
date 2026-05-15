from __future__ import annotations

import pytest


def test_offsets_for_normal_segment():
    from worker.tasks.thumbnail import calc_thumbnail_offsets

    offs = calc_thumbnail_offsets(start_sec=60.0, end_sec=360.0)
    assert offs == pytest.approx([75.0, 210.0, 345.0], abs=0.5)


def test_offsets_for_short_segment():
    from worker.tasks.thumbnail import calc_thumbnail_offsets

    offs = calc_thumbnail_offsets(start_sec=0.0, end_sec=2.0)
    # 5/50/95% of 2s
    assert offs[0] == pytest.approx(0.1, abs=0.05)
    assert offs[1] == pytest.approx(1.0, abs=0.05)
    assert offs[2] == pytest.approx(1.9, abs=0.05)


def test_offsets_never_negative_or_past_end():
    from worker.tasks.thumbnail import calc_thumbnail_offsets

    offs = calc_thumbnail_offsets(start_sec=0.0, end_sec=10.0)
    for o in offs:
        assert 0.0 <= o <= 10.0
