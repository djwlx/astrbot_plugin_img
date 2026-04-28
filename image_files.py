import logging
import math
import uuid
from pathlib import Path
from typing import Optional, Tuple

import aiohttp

TELEGRAM_PHOTO_SAFE_SIDE_SUM = 9900
TELEGRAM_PHOTO_SAFE_RATIO = 19.5

logger = logging.getLogger(__name__)

CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}


def content_type_to_extension(content_type: str) -> str:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return CONTENT_TYPE_EXTENSIONS.get(media_type, ".jpg")


def safe_photo_canvas_size(width: int, height: int) -> Tuple[int, int]:
    target_width = width
    target_height = height

    if target_width > target_height * TELEGRAM_PHOTO_SAFE_RATIO:
        target_height = math.ceil(target_width / TELEGRAM_PHOTO_SAFE_RATIO)
    elif target_height > target_width * TELEGRAM_PHOTO_SAFE_RATIO:
        target_width = math.ceil(target_height / TELEGRAM_PHOTO_SAFE_RATIO)

    side_sum = target_width + target_height
    if side_sum > TELEGRAM_PHOTO_SAFE_SIDE_SUM:
        scale = TELEGRAM_PHOTO_SAFE_SIDE_SUM / side_sum
        target_width = max(1, math.floor(target_width * scale))
        target_height = max(1, math.floor(target_height * scale))

    return target_width, target_height


class ImageFileService:
    def __init__(self, temp_dir: Path):
        self.temp_dir = temp_dir

    async def download_direct_image(self, url: str) -> Optional[str]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        return None

                    content_type = resp.headers.get("Content-Type", "").lower()
                    if not content_type.startswith("image/"):
                        return None

                    ext = content_type_to_extension(content_type)
                    image_path = self.temp_dir / f"{uuid.uuid4()}{ext}"
                    data = await resp.read()
                    with open(image_path, "wb") as f:
                        f.write(data)
                    return str(image_path)
        except Exception as exc:
            logger.error("获取图片失败: %s", exc)
            return None

    def normalize_photo_dimensions(self, image_path: str) -> str:
        if image_path.lower().endswith(".gif"):
            return image_path

        try:
            from PIL import Image as PILImage
            from PIL import ImageOps, UnidentifiedImageError
        except Exception as exc:
            logger.warning("Pillow 未安装，跳过图片尺寸处理: %s", exc)
            return image_path

        try:
            with PILImage.open(image_path) as image:
                if getattr(image, "is_animated", False):
                    return image_path

                image = ImageOps.exif_transpose(image)
                width, height = image.size
                if width <= 0 or height <= 0:
                    return image_path

                target_width, target_height = safe_photo_canvas_size(width, height)
                needs_canvas = (target_width, target_height) != (width, height)
                has_alpha = image.mode in ("RGBA", "LA") or (
                    image.mode == "P" and "transparency" in image.info
                )

                if not needs_canvas:
                    return image_path

                image = image.convert("RGBA" if has_alpha else "RGB")
                canvas_mode = "RGBA" if has_alpha else "RGB"
                background = (255, 255, 255, 0) if has_alpha else (255, 255, 255)
                canvas = PILImage.new(
                    canvas_mode, (target_width, target_height), background
                )

                scale = min(target_width / width, target_height / height, 1)
                resized_width = max(1, math.floor(width * scale))
                resized_height = max(1, math.floor(height * scale))
                if (resized_width, resized_height) != (width, height):
                    image = image.resize(
                        (resized_width, resized_height), PILImage.Resampling.LANCZOS
                    )

                left = (target_width - resized_width) // 2
                top = (target_height - resized_height) // 2
                canvas.paste(image, (left, top), image if has_alpha else None)

                suffix = ".png" if has_alpha else ".jpg"
                normalized_path = self.temp_dir / f"{uuid.uuid4()}_normalized{suffix}"
                if has_alpha:
                    canvas.save(normalized_path, "PNG", optimize=True)
                else:
                    canvas.save(normalized_path, "JPEG", quality=90, optimize=True)
                return str(normalized_path)
        except UnidentifiedImageError as exc:
            logger.warning("图片格式无法识别，跳过尺寸处理: %s", exc)
            return image_path
        except Exception as exc:
            logger.warning("图片尺寸处理失败，使用原图: %s", exc)
            return image_path
