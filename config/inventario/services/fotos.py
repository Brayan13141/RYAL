import io
import struct
import uuid

from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

LADO_MAXIMO = 800
MAX_PIXELES = 60_000_000
ERROR = 'Sube una foto JPG o PNG'


def reducir_foto(archivo):
    """Endereza (EXIF), reduce a 800 px de lado mayor y devuelve un JPEG."""
    try:
        img = Image.open(archivo, formats=['JPEG', 'PNG', 'WEBP'])
        if img.width * img.height > MAX_PIXELES:  # antes de decodificar un solo píxel
            raise ValueError(ERROR)
        img.draft('RGB', (2 * LADO_MAXIMO, 2 * LADO_MAXIMO))  # JPEG: decodifica a escala reducida
        img = ImageOps.exif_transpose(img)
        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
            img = img.convert('RGBA')
            fondo = Image.new('RGB', img.size, (255, 255, 255))
            fondo.paste(img, mask=img.getchannel('A'))
            img = fondo
        else:
            img = img.convert('RGB')
    except (UnidentifiedImageError, OSError, EOFError, struct.error, ValueError,
            Image.DecompressionBombError):
        raise ValueError(ERROR)
    img.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=85)
    return ContentFile(buf.getvalue(), name=f'{uuid.uuid4().hex}.jpg')
