"""The teaching-guide usage video must stay captioned and findable."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TUTORIAL = ROOT / "guide" / "public" / "tutorial"


def test_captioned_tutorial_assets_ship_with_the_guide():
    video = TUTORIAL / "how-to-use.mp4"
    vtt = TUTORIAL / "how-to-use.vtt"
    srt = TUTORIAL / "how-to-use.srt"
    poster = TUTORIAL / "how-to-use-poster.jpg"

    assert video.is_file() and video.stat().st_size > 1_000_000
    assert poster.is_file() and poster.stat().st_size > 10_000
    text = vtt.read_text(encoding="utf-8")
    assert text.startswith("WEBVTT")
    assert "Worth contacting" in text
    assert "Kept" in text
    assert "Not for me" in text
    assert srt.read_text(encoding="utf-8").count("-->") >= 8


def test_guide_embeds_the_tutorial_player():
    source = (ROOT / "guide" / "src" / "components" / "TutorialVideo.tsx").read_text(
        encoding="utf-8"
    )
    assert "tutorial/how-to-use.mp4" in source
    assert 'kind="captions"' in source
    assert "use-video" in (ROOT / "guide" / "src" / "App.tsx").read_text(encoding="utf-8")
