import os
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import polaroid_reel as pr  # noqa: E402


def make_photos(folder: Path, n: int) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        size = (300, 400) if i % 2 else (400, 300)
        p = folder / f"p{i:02d}.jpg"
        Image.new("RGB", size, (40 * i % 255, 120, 200)).save(p)
        os.utime(p, (1_700_000_000 + (n - i) * 60,) * 2)  # mtime order is reverse of name order
        paths.append(p)
    return paths


def test_easing_endpoints():
    for f in (pr.ease_out_back, pr.ease_out_cubic, pr.ease_in_cubic, pr.smoothstep):
        assert f(0.0) == pytest.approx(0.0, abs=1e-9)
        assert f(1.0) == pytest.approx(1.0, abs=1e-9)


def test_list_photos_orders_by_time(tmp_path):
    make_photos(tmp_path, 4)
    (tmp_path / "notes.txt").write_text("ignore me")
    names = [p.name for p in pr.list_photos(tmp_path, "time")]
    assert names == ["p03.jpg", "p02.jpg", "p01.jpg", "p00.jpg"]
    assert [p.name for p in pr.list_photos(tmp_path, "name")][0] == "p00.jpg"


def test_schedule_never_double_books_a_slot(tmp_path):
    s = pr.Settings()
    photos = [tmp_path / f"{i}.jpg" for i in range(60)]
    sched = pr.build_schedule(photos, s)
    for a in sched:
        for b in sched:
            if a is not b and a.slot == b.slot and a.start < b.start:
                assert b.start >= a.start + s.life - 1e-9


def test_fit_timing_hits_duration_and_subsamples():
    s, n = pr.fit_timing(30, pr.Settings(), 60)
    assert n == 30
    assert pr.total_duration(n, s) == pytest.approx(60, abs=0.01)
    s, n = pr.fit_timing(500, pr.Settings(), 60)
    assert n < 500
    assert pr.total_duration(n, s) <= 60.01
    assert s.life <= s.interval * (len(pr.SLOTS) - 1) + 1e-6


def test_crop_to_aspect():
    img = np.zeros((300, 400, 3), np.uint8)
    out = pr.crop_to_aspect(img, 4 / 5)
    assert out.shape[1] / out.shape[0] == pytest.approx(0.8, abs=0.01)


def test_card_states_cover_life():
    s = pr.Settings()
    sched = pr.build_schedule([Path("x.jpg")], s)
    sprite = pr.CardSprite(np.zeros((10, 10, 4), np.uint8), np.zeros((10, 10), np.uint8), 400, 500, 300, 400)
    pl = sched[0]
    assert pr.card_state(pl, sprite, pl.start - 0.1, s) is None
    x, y, _, lift = pr.card_state(pl, sprite, pl.start + s.enter + s.hold / 2, s)
    assert abs(x - pl.x) < 30 and abs(y - pl.y) < 30 and lift == 0
    x0, y0, _, _ = pr.card_state(pl, sprite, pl.start, s)
    assert not (0 <= x0 <= s.width and 0 <= y0 <= s.height) or abs(x0 - pl.x) > 200


def test_end_to_end_render(tmp_path):
    photos = make_photos(tmp_path / "in", 3)
    (tmp_path / "in" / "broken.jpg").write_bytes(b"not a jpeg")  # half-copied file is skipped
    bg = tmp_path / "bg.png"
    Image.new("RGB", (320, 180), (20, 60, 100)).save(bg)
    s = pr.Settings(width=320, height=180, fps=10, intro=0.5, outro=0.5, interval=0.5, hold=0.5,
                    enter=0.4, exit=0.4, preset="ultrafast")
    out = tmp_path / "out.mp4"
    dur = pr.render(photos + [tmp_path / "in" / "broken.jpg"], out, bg, s, quiet=True)
    assert out.exists() and out.stat().st_size > 1000
    assert dur == pytest.approx(pr.total_duration(4, s))
