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

from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_WINDOWS_PATH = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")

# The portal draws five or six characters. Four-to-eight accepts a misread
# noise speck counted as a character, while still rejecting empty or garbled
# reads early — a hopeless read is not worth a submit round-trip.
MIN_PLAUSIBLE_LENGTH = 4
MAX_PLAUSIBLE_LENGTH = 8

# Tesseract page segmentation modes to try, in order: 7 treats the image as a
# single text line, 8 as a single word, 13 as a raw line without layout
# analysis. A CAPTCHA is one of the first two; trying all three and keeping
# the first plausible read recovers from the mode guessing wrong.
_PSM_MODES = (7, 8, 13)

# The portal's validation is case-insensitive and its alphabet is
# alphanumeric, so the whitelist and the upper-casing both trade a guess the
# OCR is bad at (case) for one it is good at (shape).
_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

_CONFIGURED = False


def configure_tesseract(cmd: str | None = None) -> str | None:
    """Point pytesseract at a usable tesseract binary, once per process.

    Precedence: an explicit argument, then ``TESSERACT_CMD``, then the Windows
    default location, then whatever is on PATH. Returns the command that will
    be used, so callers can distinguish "found" from "will try PATH".
    """
    global _CONFIGURED

    import pytesseract

    if _CONFIGURED:
        return str(pytesseract.pytesseract.tesseract_cmd)

    candidates = (
        cmd,
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
    """Grayscale, contrast-normalise, binarise and denoise.

    The portal draws dark digits over a pale, speckled background, and also
    re-issues dark-background variants, so the inversion decision is taken
    from the image itself rather than assumed. Tesseract wants crisp black
    text on white; the median filter removes the speckle that survives
    thresholding.
    """
    grey = image.convert("L")
    if ImageStat.Stat(grey).mean[0] < 128:
        grey = ImageOps.invert(grey)
    grey = ImageOps.autocontrast(grey)
    binary = grey.point(lambda value: 0 if value < 160 else 255, "1")
    return binary.filter(ImageFilter.MedianFilter(size=3))


def read_text(image: Image.Image) -> str:
    """OCR one preprocessed image, trying the segmentation modes in turn."""
    import pytesseract

    config = f"--oem 3 -c tessedit_char_whitelist={_WHITELIST}"
    best = ""
    for psm in _PSM_MODES:
        try:
            raw = pytesseract.image_to_string(image, config=f"{config} --psm {psm}")
        except pytesseract.TesseractError:
            logger.warning("captcha_ocr_mode_failed", psm=psm)
            continue
        candidate = normalise(raw)
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
