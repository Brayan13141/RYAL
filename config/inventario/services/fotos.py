import io
import uuid

from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

LADO_MAXIMO = 800


def reducir_foto(archivo):
    """Endereza (EXIF), reduce a 800 px de lado mayor y devuelve un JPEG."""
    try:
        img = Image.open(archivo)
        img = ImageOps.exif_transpose(img)
        img = img.convert('RGB')
    except (UnidentifiedImageError, OSError):
        raise ValueError('Sube una foto JPG o PNG')
    img.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=85)
    return ContentFile(buf.getvalue(), name=f'{uuid.uuid4().hex}.jpg')
