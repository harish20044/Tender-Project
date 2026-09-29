"""Reading the portal's CAPTCHA images with Tesseract.

The document packs on the CPPP portal sit behind a CAPTCHA form. The images
are machine-generated five-or-six character strings — distorted and noisy,
but drawn from a fixed alphabet — which is the one class of CAPTCHA that
optical character recognition handles reliably. This module is the OCR half:
an image in, the best guess at the text out.

The browser half, which screenshots the image element and types the answer
back, lives in ``documents``. Keeping the two apart means the OCR can be
tuned against saved screenshots with no browser involved.

Tesseract is a separate program, not a package. The Windows build most people
install does not add itself to PATH, so its default location is probed too;
see RUNNING.md.
"""

from __future__ import annotations

import io
import os
import re
import shutil
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_WINDOWS_PATH = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")

# The portal draws five or six characters. Four-to-eight accepts a misread
# noise speck counted as a character, while still rejecting empty or garbled
# reads early — a hopeless read is not worth a submit round-trip.
MIN_PLAUSIBLE_LENGTH = 4
MAX_PLAUSIBLE_LENGTH = 8

# Tesseract page segmentation modes for the whole-image fallback: 7 treats it
# as a single text line, 8 as a single word, 13 as a raw line without layout
# analysis. Only used when segmentation cannot find a plausible glyph count.
_PSM_MODES = (7, 8, 13)

# Each isolated glyph is still read with --psm 8 rather than 10 ("single
# character"), which measured worse on real portal images: 83% of characters
# against 31%, on the six-sample set the thresholds below were tuned on.
_GLYPH_PSM = 8

# The portal draws mixed case — lowercase b, d, x, a and e all appear in real
# samples — so the whitelist has to admit it even though validation is
# case-insensitive and `normalise` upper-cases the answer afterwards.
# Restricting Tesseract to uppercase made it force lowercase glyphs into the
# wrong letter rather than skip them.
_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz"

# Tuned against real portal CAPTCHAs (150x40, coloured glyphs and coloured
# speckle on white). 180 with a greyscale median pass first isolates exactly
# six glyphs on every sample; the 160 the synthetic stand-in wanted merged or
# dropped them.
_BINARY_THRESHOLD = 180
# Tesseract needs far more pixels per glyph than a 150x40 CAPTCHA gives it.
_GLYPH_SCALE = 8
_GLYPH_PAD = 15
# A column gap this wide ends a glyph; anything narrower is one character's
# own internal whitespace.
_GLYPH_GAP = 1
_MIN_GLYPH_WIDTH = 2

_CONFIGURED = False


def configure_tesseract(cmd: str | None = None) -> str | None:
    """Point pytesseract at a usable tesseract binary, once per process.

    Precedence: an explicit argument, then ``TESSERACT_CMD`` — read from
    settings, so a value in ``.env`` counts, not just one exported into the
    process — then the Windows default location, then whatever is on PATH.
    Returns the command that will be used, so callers can distinguish "found"
    from "will try PATH".
    """
    global _CONFIGURED

    import pytesseract

    if _CONFIGURED:
        return str(pytesseract.pytesseract.tesseract_cmd)

    candidates = (
        cmd,
        get_settings().tesseract_cmd or None,
        os.environ.get("TESSERACT_CMD"),
        str(DEFAULT_WINDOWS_PATH) if DEFAULT_WINDOWS_PATH.is_file() else None,
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            pytesseract.pytesseract.tesseract_cmd = candidate
            break

    _CONFIGURED = True
    command = str(pytesseract.pytesseract.tesseract_cmd)
    logger.info("tesseract_configured", cmd=command)
    return command


def tesseract_available() -> bool:
    """Whether OCR can actually run in this process."""
    configured = configure_tesseract()
    if configured != "tesseract" and configured and Path(configured).is_file():
        return True
    return shutil.which("tesseract") is not None


def normalise(text: str) -> str:
    """Keep only the characters the portal draws, upper-cased."""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def is_plausible(text: str) -> bool:
    """Whether a cleaned read looks like a portal CAPTCHA at all."""
    return MIN_PLAUSIBLE_LENGTH <= len(text) <= MAX_PLAUSIBLE_LENGTH and text.isalnum()


def preprocess(image: Image.Image) -> Image.Image:
    """Grayscale, contrast-normalise, denoise and binarise.

    The portal draws coloured characters over a pale background scattered
    with coloured speckle, and also re-issues dark-background variants, so
    the inversion decision is taken from the image itself rather than
    assumed. The median pass runs on greyscale, *before* thresholding, so
    speckle is smoothed away instead of being frozen into black pixels that
    later split a glyph in two or invent one of their own.
    """
    grey = image.convert("L")
    if ImageStat.Stat(grey).mean[0] < 128:
        grey = ImageOps.invert(grey)
    grey = ImageOps.autocontrast(grey)
    grey = grey.filter(ImageFilter.MedianFilter(size=3))
    return grey.point(lambda value: 0 if value < _BINARY_THRESHOLD else 255, "1")


def glyph_spans(binary: Image.Image) -> list[tuple[int, int]]:
    """Column ranges holding one character each.

    The portal spaces its characters out and sits them at varying baselines,
    which is what makes Tesseract drop half of them when it reads the image
    as a line. A vertical projection profile splits them cleanly, because
    the gaps between characters contain no ink at all.
    """
    width, height = binary.size
    # One flat read rather than per-pixel access: the projection touches every
    # pixel, and PixelAccess indexing from Python is markedly slower.
    data = list(binary.getdata())
    inked = [any(data[y * width + x] == 0 for y in range(height)) for x in range(width)]

    spans: list[tuple[int, int]] = []
    start: int | None = None
    gap = 0
    for x, is_inked in enumerate(inked):
        if is_inked:
            if start is None:
                start = x
            gap = 0
        elif start is not None:
            gap += 1
            if gap > _GLYPH_GAP:
                if x - gap - start >= _MIN_GLYPH_WIDTH:
                    spans.append((start, x - gap))
                start, gap = None, 0
    if start is not None and width - start >= _MIN_GLYPH_WIDTH:
        spans.append((start, width))
    return spans


def _glyph_image(binary: Image.Image, span: tuple[int, int]) -> Image.Image:
    """One character, trimmed to its own ink, enlarged and padded.

    Tesseract is trained on print-resolution text; a glyph barely 20 pixels
    tall is far outside that, so it is scaled up and given a white margin to
    sit in rather than being read hard against the crop edge.
    """
    x0, x1 = span
    height = binary.size[1]
    glyph = binary.crop((max(0, x0 - 1), 0, min(binary.size[0], x1 + 1), height))

    glyph_width, glyph_height = glyph.size
    data = list(glyph.getdata())
    rows = [
        y
        for y in range(glyph_height)
        if any(data[y * glyph_width + x] == 0 for x in range(glyph_width))
    ]
    if rows:
        glyph = glyph.crop((0, max(0, rows[0] - 1), glyph_width, min(height, rows[-1] + 2)))

    # Upscaled in greyscale, not bilevel: LANCZOS can only anti-alias the
    # enlarged strokes if it has intermediate values to work with, and those
    # smoothed edges are worth about five percentage points of accuracy.
    glyph = glyph.convert("L").resize(
        (glyph.size[0] * _GLYPH_SCALE, glyph.size[1] * _GLYPH_SCALE),
        Image.Resampling.LANCZOS,
    )
    padded = Image.new("L", (glyph.size[0] + _GLYPH_PAD * 2, glyph.size[1] + _GLYPH_PAD * 2), 255)
    padded.paste(glyph, (_GLYPH_PAD, _GLYPH_PAD))
    return padded


def _ocr(image: Image.Image, psm: int) -> str:
    import pytesseract

    config = f"--oem 3 --psm {psm} -c tessedit_char_whitelist={_WHITELIST}"
    try:
        return str(pytesseract.image_to_string(image, config=config))
    except pytesseract.TesseractError:
        logger.warning("captcha_ocr_mode_failed", psm=psm)
        return ""


def read_glyphs(binary: Image.Image) -> str:
    """Read a preprocessed image one character at a time.

    Reading the image as a whole loses characters — measured at 28% of them
    correct against real portal CAPTCHAs, versus 83% this way — because the
    portal's spacing and baseline jitter defeat Tesseract's line layout
    analysis. Isolating each glyph removes the layout question entirely.
    """
    letters = []
    for span in glyph_spans(binary):
        read = normalise(_ocr(_glyph_image(binary, span), _GLYPH_PSM))
        # One glyph is one character; a multi-character read means Tesseract
        # saw noise alongside it, and the first character is the real one.
        letters.append(read[:1])
    return "".join(letters)


def read_text(image: Image.Image) -> str:
    """OCR one preprocessed image, per glyph, falling back to the whole image.

    The fallback matters when segmentation finds an implausible number of
    glyphs — touching characters, or speckle heavy enough to survive the
    median pass — where reading the image as one line is the better guess.
    """
    segmented = read_glyphs(image)
    if is_plausible(segmented):
        return segmented

    best = segmented
    for psm in _PSM_MODES:
        candidate = normalise(_ocr(image, psm))
        if is_plausible(candidate):
            return candidate
        if len(candidate) > len(best):
            best = candidate
    return best


def solve(image_bytes: bytes, *, debug_dir: Path | str | None = None) -> str:
    """Full pipeline for one CAPTCHA screenshot: bytes in, text out.

    With ``debug_dir`` set, the original and preprocessed images are saved
    beside the answer read from them, which is how the threshold and the
    plausible-length window get tuned against real portal images.
    """
    configure_tesseract()
    original = Image.open(io.BytesIO(image_bytes))
    processed = preprocess(original)

    if debug_dir:
        directory = Path(debug_dir)
        directory.mkdir(parents=True, exist_ok=True)
        original.save(directory / "captcha_original.png")
        processed.save(directory / "captcha_processed.png")

    answer = read_text(processed)
    logger.info("captcha_read", answer=answer, plausible=is_plausible(answer))
    return answer
