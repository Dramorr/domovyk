"""Фото через Pillow: перевірка формату, поворот за EXIF, зменшення, JPEG без метаданих."""

from __future__ import annotations

import asyncio
import io
import warnings

import imagehash
from PIL import Image, ImageOps

from domovyk.adapters.images.base import ImageRejected, PreparedImage

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_PIXELS = 50_000_000  # захист від «бомб» розпакування


class PillowImageProcessor:
    def __init__(self, *, max_side: int = 1600, min_side: int = 400, quality: int = 82) -> None:
        self._max_side = max_side
        self._min_side = min_side
        self._quality = quality

    async def prepare(self, data: bytes) -> PreparedImage:
        return await asyncio.to_thread(self._prepare, data)

    def _prepare(self, data: bytes) -> PreparedImage:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                source = Image.open(io.BytesIO(data))
                if source.format not in ALLOWED_FORMATS:
                    raise ImageRejected("bad_type")
                if source.width * source.height > MAX_PIXELS:
                    raise ImageRejected("too_big")
                source.load()
        except ImageRejected:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ImageRejected("too_big") from None
        except Exception:
            raise ImageRejected("bad_type") from None

        # Фото з телефона — так, як його бачить людина (поворот за EXIF).
        img: Image.Image = ImageOps.exif_transpose(source)
        if img.mode != "RGB":
            background = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            background.paste(rgba, mask=rgba.getchannel("A"))
            img = background
        if min(img.size) < self._min_side:
            raise ImageRejected("too_small")
        img.thumbnail((self._max_side, self._max_side), Image.Resampling.LANCZOS)

        out = io.BytesIO()
        # Без exif/icc: метадані не переносимо.
        img.save(out, "JPEG", quality=self._quality, optimize=True, progressive=True)
        value = int(str(imagehash.phash(img)), 16)
        signed = value - (1 << 64) if value >= 1 << 63 else value
        return PreparedImage(out.getvalue(), img.width, img.height, signed)
