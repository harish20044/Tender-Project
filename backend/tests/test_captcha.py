"""Tests for the CAPTCHA reader.

The OCR itself needs the Tesseract program installed, which CI and many
development machines do not have, so that case is skipped when it is absent.
Everything else — the image conditioning, the answer hygiene — is pure
Pillow and tested outright.
"""

import shutil
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.corpus.captcha import (
    DEFAULT_WINDOWS_PATH,
    is_plausible,
    normalise,
    preprocess,
    solve,
)

# A distinctive string: mixed letters and digits, no characters the whitelist
# struggles with, long enough that a partial read cannot pass by accident.
SAMPLE_TEXT = "7X4B2M"


def _tesseract_present() -> bool:
    return bool(shutil.which("tesseract") or DEFAULT_WINDOWS_PATH.is_file())


def test_normalise_keeps_only_the_portal_alphabet() -> None:
    assert normalise("a9 x2-b7!") == "A9X2B7"


def test_plausibility_rejects_the_too_short_and_the_too_long() -> None:
    assert is_plausible("A9X2B7") is True
    assert is_plausible("A9") is False  # under four: a garbled read
    assert is_plausible("A9X2B7C1D4E6") is False  # over eight: noise read as text
    assert is_plausible("A9-2B7") is False  # punctuation is not an answer


def test_preprocess_returns_a_bilevel_image_of_the_same_size() -> None:
    dark = Image.new("RGB", (216, 60), (40, 60, 90))
    light = Image.new("RGB", (216, 60), (235, 235, 235))

    for source in (dark, light):
        processed = preprocess(source)

        assert processed.size == source.size
        assert processed.mode == "1"


def _render(text: str) -> bytes:
    """A clean dark-on-white CAPTCHA stand-in, good enough for a smoke test."""
    image = Image.new("RGB", (216, 60), "white")
    draw = ImageDraw.Draw(image)
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont | None = None
    for candidate in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            font = ImageFont.truetype(candidate, 36)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    draw.text((24, 10), text, fill="black", font=font)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.skipif(not _tesseract_present(), reason="Tesseract is not installed")
def test_solve_reads_a_clean_synthetic_captcha(tmp_path: Path) -> None:
    answer = solve(_render(SAMPLE_TEXT), debug_dir=tmp_path)

    # Exact equality is asserted loosely on purpose: the synthetic image is
    # clean, but the point of the test is the pipeline, not Tesseract's score.
    assert is_plausible(answer)
    assert sum(character in answer for character in SAMPLE_TEXT) >= 4
    assert (tmp_path / "captcha_original.png").is_file()
    assert (tmp_path / "captcha_processed.png").is_file()