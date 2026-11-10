import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import polaroid_pile as pp  # noqa: E402
from test_polaroid_reel import make_photos  # noqa: E402


def test_pages_split_evenly():
    assert pp.pages_for(0, 9) == []
    assert pp.pages_for(9, 9) == [9]
    assert pp.pages_for(20, 9) == [7, 7, 6]
    assert sum(pp.pages_for(57, 9)) == 57


def test_fit_timing_hits_duration():
    s, n = pp.fit_timing(40, pp.Settings(), 60)
    assert n == 40
    assert pp.total_duration(n, s) == pytest.approx(60, abs=0.01)
    s, n = pp.fit_timing(5, pp.Settings(), 60)    # few photos: piles linger instead
    assert n == 5 and s.interval <= 1.1 + 1e-9
    assert pp.total_duration(n, s) == pytest.approx(60, abs=0.01)


def test_fit_timing_subsamples_when_crowded():
    s, n = pp.fit_timing(500, pp.Settings(), 45)
    assert n < 500
    assert s.interval >= 0.3 - 1e-9
    assert pp.total_duration(n, s) <= 45.01


def test_timeline_matches_total_duration(tmp_path):
    s = pp.Settings()
    photos = [tmp_path / f"{i}.jpg" for i in range(23)]
    tl = pp.build_timeline(photos, {}, s)
    assert tl.end == pytest.approx(pp.total_duration(23, s))
    assert tl.pages == 3
    # cards rest inside their own pile's screen, and the camera pan comes last
    for c in tl.cards:
        top = c.page * s.height
        assert 0 <= c.x <= s.width and top <= c.y <= top + s.height
    assert tl.pans[-1][3] == tl.pages * s.height


def test_view_pans_between_piles():
    s = pp.Settings()
    tl = pp.build_timeline([Path(f"{i}.jpg") for i in range(12)], {}, s)
    t0, t1, a, b = tl.pans[0]
    assert pp.view_y(tl, t0 - 0.01)[0] == a
    assert pp.view_y(tl, t1 + 0.01)[0] == b
    assert a < pp.view_y(tl, (t0 + t1) / 2)[0] < b


def test_captions_file(tmp_path):
    f = tmp_path / "captions.txt"
    f.write_text("# comment\nIMG_1.jpg = Wichita, KS\nimg_2\tCambodia\n\nno separator\n", encoding="utf-8")
    caps = pp.load_captions(f)
    assert pp.caption_for(Path("img_1.JPG"), caps) == "Wichita, KS"
    assert pp.caption_for(Path("IMG_2.jpg"), caps) == "Cambodia"
    assert pp.caption_for(Path("other.jpg"), caps) == ""
    assert pp.load_captions(tmp_path / "missing.txt") == {}


def test_card_slides_in_from_off_screen():
    s = pp.Settings()
    tl = pp.build_timeline([Path("x.jpg")], {}, s)
    c = tl.cards[0]
    sp = pp.Sprite(np.zeros((10, 10, 4), np.uint8), np.zeros((10, 10), np.uint8), 600, 700)
    assert pp.card_pose(c, sp, c.start - 0.1, s) is None
    x, y, *_ = pp.card_pose(c, sp, c.start, s)
    assert not (0 <= x <= s.width and 0 <= y <= s.height)
    assert pp.card_pose(c, sp, c.start + s.slide, s)[:3] == (c.x, c.y, c.rot)


def test_no_sideways_cards():
    """Every card rests upright with only a small tilt (no quarter-turned frames)."""
    tl = pp.build_timeline([Path(f"{i}.jpg") for i in range(200)], {}, pp.Settings())
    assert all(abs(c.rot) <= 11 for c in tl.cards)


def test_output_is_always_mp4():
    assert pp.pr.mp4_path(Path("D:/x/reel.mov")) == Path("D:/x/reel.mp4")
    assert pp.pr.mp4_path(Path("reel.MP4")) == Path("reel.MP4")
    assert pp.pr.mp4_path(Path("Day 10 - 30.10.26 - Julian_pile")).suffix == ".mp4"


def test_bitrate_cap_suits_projection():
    assert pp.pr.peak_kbps(pp.Settings()) == 24000                              # 1080p30
    assert pp.pr.peak_kbps(pp.Settings(width=3840, height=2160)) == 60000       # 4K: capped
    assert pp.pr.peak_kbps(pp.Settings(width=320, height=180)) == 4000          # tiny: floor


@pytest.mark.skipif(not (pp.ASSETS / "polaroid.png").exists(),
                    reason="template2 artwork is not in the public repo (copy it from the Drive folder)")
def test_end_to_end_render(tmp_path):
    photos = make_photos(tmp_path / "in", 4)
    (tmp_path / "in" / "broken.jpg").write_bytes(b"not a jpeg")  # half-copied file is skipped
    s = pp.Settings(width=320, height=180, fps=10, per_page=3, interval=0.3, slide=0.3, linger=0.3,
                    pan=0.3, cam_pan=0.3, cam_hold=0.2, flash=0.3, eject=0.4, develop=0.4,
                    end_hold=0.3, preset="ultrafast")
    out = tmp_path / "out.mp4"
    caps = {"p00.jpg": "Day one"}
    dur = pp.render(photos + [tmp_path / "in" / "broken.jpg"], out, s, pp.ASSETS / "desk.jpg",
                    pp.ASSETS / "end_card.png", caps, quiet=True)
    assert out.exists() and out.stat().st_size > 1000
    assert dur == pytest.approx(pp.total_duration(5, s))
