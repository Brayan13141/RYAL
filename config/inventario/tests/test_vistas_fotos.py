import io
import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image

from inventario.models import Color, FotoColor
from inventario.tests.test_venta_bot import VentaBotBase


def _png(size=(1600, 1200)):
    buf = io.BytesIO()
    Image.new('RGB', size, 'red').save(buf, 'PNG')
    return SimpleUploadedFile('f.png', buf.getvalue(), content_type='image/png')


class FotosVistaTests(VentaBotBase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        cm = override_settings(MEDIA_ROOT=tmp)
        cm.enable()
        self.addCleanup(cm.disable)
        self.staff = User.objects.create_user('st', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.url = reverse('inventario:catalogos') + '?tab=colores'

    def post(self, **extra):
        data = {'accion': 'fotos_modelo', 'tipo': self.base.pk}
        data.update(extra)
        return self.client.post(self.url, data)

    def test_subir_foto_base_y_color(self):
        r = self.post(foto=_png(), **{f'foto_{self.negro.pk}': _png()})
        self.assertEqual(r.status_code, 302)
        self.assertIn(f'modelo={self.base.pk}', r['Location'])
        self.assertTrue(r['Location'].endswith('#colores-modelo'))
        self.base.refresh_from_db()
        self.assertTrue(self.base.foto)
        self.assertLessEqual(max(Image.open(self.base.foto.path).size), 800)
        self.assertTrue(FotoColor.objects.filter(tipo=self.base, color=self.negro).exists())

    def test_reemplazar_borra_archivo_anterior(self):
        self.post(foto=_png(), **{f'foto_{self.negro.pk}': _png()})
        self.base.refresh_from_db()
        viejo_base = self.base.foto.name
        viejo_color = FotoColor.objects.get().imagen.name
        self.post(foto=_png(), **{f'foto_{self.negro.pk}': _png()})
        self.base.refresh_from_db()
        self.assertNotEqual(self.base.foto.name, viejo_base)
        self.assertFalse(default_storage.exists(viejo_base))
        self.assertFalse(default_storage.exists(viejo_color))
        self.assertEqual(FotoColor.objects.count(), 1)
        self.assertTrue(default_storage.exists(FotoColor.objects.get().imagen.name))

    def test_quitar_foto_color(self):
        self.post(**{f'foto_{self.negro.pk}': _png()})
        nombre = FotoColor.objects.get().imagen.name
        r = self.post(**{f'quitar_{self.negro.pk}': 'on'})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(FotoColor.objects.exists())
        self.assertFalse(default_storage.exists(nombre))

    def test_quitar_foto_base(self):
        self.post(foto=_png())
        self.base.refresh_from_db()
        nombre = self.base.foto.name
        self.post(quitar_foto='on')
        self.base.refresh_from_db()
        self.assertFalse(self.base.foto)
        self.assertFalse(default_storage.exists(nombre))

    def test_archivo_no_imagen_da_error_y_no_guarda(self):
        self.post(**{f'foto_{self.negro.pk}': _png()})
        antes = FotoColor.objects.get().imagen.name
        r = self.post(foto=_png(), **{
            f'foto_{self.negro.pk}': SimpleUploadedFile('x.jpg', b'hola')})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Sube una foto JPG o PNG')
        self.base.refresh_from_db()
        self.assertFalse(self.base.foto)
        self.assertEqual(FotoColor.objects.get().imagen.name, antes)
        self.assertTrue(default_storage.exists(antes))

    def test_solo_colores_marcados_tienen_campo(self):
        otro = Color.objects.create(nombre='rojo')
        r = self.client.get(self.url + f'&modelo={self.base.pk}')
        self.assertContains(r, f'name="foto_{self.negro.pk}"')
        self.assertNotContains(r, f'name="foto_{otro.pk}"')

    def test_form_tiene_enctype_y_sin_foto(self):
        r = self.client.get(self.url + f'&modelo={self.base.pk}')
        self.assertContains(r, 'enctype="multipart/form-data"')
        self.assertContains(r, 'Sin foto')

    def test_miniatura_con_foto(self):
        self.post(**{f'foto_{self.negro.pk}': _png()})
        r = self.client.get(self.url + f'&modelo={self.base.pk}')
        self.assertContains(r, FotoColor.objects.get().imagen.url)

    def test_sin_modelo_no_hay_seccion_de_fotos(self):
        r = self.client.get(self.url)
        self.assertNotContains(r, 'multipart/form-data')

    def test_no_staff_redirige(self):
        self.client.logout()
        self.client.force_login(User.objects.create_user('n', password='x'))
        r = self.post(foto=_png())
        self.assertEqual(r.status_code, 302)
        self.assertIn('login', r['Location'])
