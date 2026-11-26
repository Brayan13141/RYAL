import io
import shutil
import tempfile
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.test import override_settings
from PIL import Image

from inventario.models import FotoColor, foto_de
from inventario.services.fotos import reducir_foto
from inventario.tests.test_venta_bot import VentaBotBase


def _imagen(size, fmt='PNG', exif=None):
    buf = io.BytesIO()
    kwargs = {}
    if exif is not None:
        kwargs['exif'] = exif
    Image.new('RGB', size, 'red').save(buf, fmt, **kwargs)
    return io.BytesIO(buf.getvalue())


class ReducirFotoTests(VentaBotBase):
    def test_reducir_foto_limita_a_800_y_es_jpeg(self):
        r = reducir_foto(_imagen((2000, 1000)))
        img = Image.open(io.BytesIO(r.read()))
        self.assertEqual(img.format, 'JPEG')
        self.assertEqual(img.size, (800, 400))
        self.assertTrue(r.name.endswith('.jpg'))

    def test_reducir_foto_endereza_exif(self):
        exif = Image.Exif()
        exif[0x0112] = 6
        r = reducir_foto(_imagen((300, 100), 'JPEG', exif=exif))
        img = Image.open(io.BytesIO(r.read()))
        self.assertEqual(img.size, (100, 300))

    def test_reducir_foto_rechaza_no_imagen(self):
        with self.assertRaises(ValueError) as cm:
            reducir_foto(io.BytesIO(b'hola'))
        self.assertIn('Sube una foto JPG o PNG', str(cm.exception))


    def test_reducir_foto_rechaza_gif_y_bmp(self):
        for fmt in ('GIF', 'BMP'):
            with self.assertRaises(ValueError) as cm:
                reducir_foto(_imagen((20, 20), fmt))
            self.assertIn('Sube una foto JPG o PNG', str(cm.exception))

    def test_reducir_foto_rechaza_mas_de_60_megapixeles_sin_cargar_pixeles(self):
        grande = Image.new('1', (8000, 8000))  # 64 MP, comprimido casi no pesa
        buf = io.BytesIO()
        grande.save(buf, 'PNG')
        buf.seek(0)
        with mock.patch.object(Image.Image, 'load', side_effect=AssertionError('cargó píxeles')):
            with self.assertRaises(ValueError) as cm:
                reducir_foto(buf)
        self.assertIn('Sube una foto JPG o PNG', str(cm.exception))

    def test_reducir_foto_jpeg_grande_usa_draft(self):
        r = reducir_foto(_imagen((3200, 2400), 'JPEG'))
        img = Image.open(io.BytesIO(r.read()))
        self.assertEqual(img.size, (800, 600))

    def test_reducir_foto_imagen_truncada_es_valueerror(self):
        datos = _imagen((400, 400), 'PNG').getvalue()
        with self.assertRaises(ValueError):
            reducir_foto(io.BytesIO(datos[:len(datos) // 2]))


class FotosModeloTests(VentaBotBase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        cm = override_settings(MEDIA_ROOT=tmp)
        cm.enable()
        self.addCleanup(cm.disable)

    def guardar(self, nombre='x.jpg'):
        return ContentFile(reducir_foto(_imagen((50, 50))).read(), name=nombre)

    def test_foto_en_variante_no_valida(self):
        self.aereo.foto.save('v.jpg', self.guardar(), save=False)
        with self.assertRaises(ValidationError) as cm:
            self.aereo.full_clean()
        self.assertIn('foto', cm.exception.message_dict)

    def test_fotocolor_unico_por_tipo_color(self):
        FotoColor.objects.create(tipo=self.base, color=self.negro, imagen=self.guardar())
        with self.assertRaises(IntegrityError), transaction.atomic():
            FotoColor.objects.create(tipo=self.base, color=self.negro, imagen=self.guardar())

    def test_foto_de_prefiere_color_luego_base_luego_none(self):
        self.assertIsNone(foto_de(self.base, self.negro))
        self.base.foto.save('base.jpg', self.guardar('base.jpg'))
        self.assertEqual(foto_de(self.base, self.negro).name, self.base.foto.name)
        fc = FotoColor.objects.create(
            tipo=self.base, color=self.negro, imagen=self.guardar('negro.jpg'))
        self.assertEqual(foto_de(self.base, self.negro).name, fc.imagen.name)
        self.assertEqual(foto_de(self.base, self.marino).name, self.base.foto.name)
        self.assertEqual(foto_de(self.base).name, self.base.foto.name)

    def test_foto_de_ignora_color_desmarcado(self):
        self.base.foto.save('base.jpg', self.guardar('base.jpg'))
        FotoColor.objects.create(tipo=self.base, color=self.negro, imagen=self.guardar('n.jpg'))
        self.base.colores.remove(self.negro)
        self.assertEqual(foto_de(self.base, self.negro).name, self.base.foto.name)

    def test_foto_de_archivo_borrado_es_none(self):
        self.base.foto.save('base.jpg', self.guardar('base.jpg'))
        default_storage.delete(self.base.foto.name)
        self.assertIsNone(foto_de(self.base, self.negro))


class ReducirFotoEndurecidaTests(VentaBotBase):
    def test_bomba_de_descompresion_es_valueerror(self):
        from unittest import mock
        with mock.patch('inventario.services.fotos.Image.open',
                        side_effect=Image.DecompressionBombError('grande')):
            with self.assertRaises(ValueError) as cm:
                reducir_foto(io.BytesIO(b'x'))
        self.assertIn('Sube una foto JPG o PNG', str(cm.exception))

    def test_png_transparente_se_compone_sobre_blanco(self):
        buf = io.BytesIO()
        Image.new('RGBA', (20, 20), (0, 0, 0, 0)).save(buf, 'PNG')
        buf.seek(0)
        img = Image.open(io.BytesIO(reducir_foto(buf).read()))
        self.assertTrue(all(c > 240 for c in img.getpixel((10, 10))))
