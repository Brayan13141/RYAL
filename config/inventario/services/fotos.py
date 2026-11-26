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
        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
            img = img.convert('RGBA')
            fondo = Image.new('RGB', img.size, (255, 255, 255))
            fondo.paste(img, mask=img.getchannel('A'))
            img = fondo
        else:
            img = img.convert('RGB')
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError('Sube una foto JPG o PNG')
    img.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=85)
    return ContentFile(buf.getvalue(), name=f'{uuid.uuid4().hex}.jpg')
