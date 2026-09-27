import io
import os

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

MAX_SIDE = 1600
JPEG_QUALITY = 85
WEBP_QUALITY = 85

FORMAT_BY_EXT = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}


def shrink_image(file_obj, name: str, *, max_side: int = MAX_SIDE):
    ext = os.path.splitext(name)[1].lower()
    fmt = FORMAT_BY_EXT.get(ext)
    if fmt is None:
        return None

    try:
        file_obj.seek(0)
        image = Image.open(file_obj)
        image.load()
    except Exception:  # noqa: BLE001
        return None

    image = ImageOps.exif_transpose(image)
    if fmt == "JPEG" and image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side), Image.LANCZOS)

    buffer = io.BytesIO()
    if fmt == "JPEG":
        image.save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    elif fmt == "WEBP":
        image.save(buffer, "WEBP", quality=WEBP_QUALITY, method=4)
    else:
        image.save(buffer, "PNG", optimize=True)

    return ContentFile(buffer.getvalue(), name=os.path.basename(name))


def shrink_uploaded_images(sender, instance, **kwargs):
    from django.db.models import ImageField

    for field in instance._meta.get_fields():
        if not isinstance(field, ImageField):
            continue
        field_file = getattr(instance, field.name, None)
        if not field_file or getattr(field_file, "_committed", True):
            continue
        if getattr(field_file, "_feasto_shrunk", False):
            continue
        shrunk = shrink_image(field_file.file, field_file.name)
        if shrunk is None:
            continue
        field_file.file = shrunk
        field_file._feasto_shrunk = True
