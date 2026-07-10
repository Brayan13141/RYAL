"""Management command: cleanup_media

Limpieza semanal de imágenes en MEDIA_ROOT. Tres pasos:

1. PendingProduct rechazados con cover_image → borra el archivo y limpia el campo.
2. Archivos huérfanos en pending/  (sin PendingProduct.cover_image que los refiera).
3. Archivos huérfanos en products/ (sin ProductImage.image que los refiera).

Guardas de seguridad:
- Solo borra archivos con más de --min-age-days de antigüedad (default 7) para
  no chocar con imports en curso (import_pending_images escribe el archivo
  antes del .save()).
- Si la BD devuelve 0 referencias para un directorio, ese paso se salta por
  completo — nunca se vacía un directorio porque una query falló o vino vacía.
"""
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from catalog.models import PendingProduct, ProductImage


class Command(BaseCommand):
    help = (
        'Borra imágenes de pendientes rechazados y archivos huérfanos en '
        'pending/ y products/. Dry-run por default; usa --apply para ejecutar.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true', default=True,
            help='Muestra el plan sin borrar nada (default).'
        )
        parser.add_argument(
            '--apply', action='store_true', default=False,
            help='Borra de verdad.'
        )
        parser.add_argument(
            '--min-age-days', type=int, default=7,
            help='No tocar archivos modificados hace menos de N días (default 7).'
        )

    def handle(self, *args, **options):
        self.dry_run = not options['apply']
        self.min_age_secs = options['min_age_days'] * 86400
        self.media_root = Path(settings.MEDIA_ROOT)
        freed = 0

        if self.dry_run:
            self.stdout.write(self.style.WARNING('[dry-run] No se borra nada. Usa --apply para ejecutar.'))

        freed += self._clean_rejected_covers()
        freed += self._clean_orphans(
            subdir='pending',
            referenced={
                name for name in PendingProduct.objects
                .exclude(cover_image='')
                .values_list('cover_image', flat=True)
            },
        )
        freed += self._clean_orphans(
            subdir='products',
            referenced={
                name for name in ProductImage.objects
                .exclude(image='')
                .values_list('image', flat=True)
            },
        )

        verbo = 'se liberarían' if self.dry_run else 'liberados'
        self.stdout.write(self.style.SUCCESS(f'Total: {freed / 1024 / 1024:.1f} MB {verbo}.'))

    # ── Paso 1: covers de pendientes rechazados ──────────────────────────────
    def _clean_rejected_covers(self):
        qs = PendingProduct.objects.filter(status='rejected').exclude(cover_image='')
        count = qs.count()
        self.stdout.write(f'\n── Rechazados con cover_image: {count}')
        freed = 0
        for pp in qs.iterator():
            path = self.media_root / pp.cover_image.name
            size = path.stat().st_size if path.exists() else 0
            self.stdout.write(f'  [{pp.pk}] {pp.cover_image.name} ({size / 1024:.0f} KB)')
            if not self.dry_run:
                pp.cover_image.delete(save=False)   # borra archivo del storage
                pp.cover_image = ''
                pp.save(update_fields=['cover_image'])
            freed += size
        return freed

    # ── Pasos 2 y 3: huérfanos por directorio ────────────────────────────────
    def _clean_orphans(self, subdir, referenced):
        base = self.media_root / subdir
        if not base.is_dir():
            self.stdout.write(f'\n── {subdir}/: no existe — saltado')
            return 0
        if not referenced:
            self.stdout.write(self.style.WARNING(
                f'\n── {subdir}/: la BD no devolvió NINGUNA referencia — paso saltado por seguridad'
            ))
            return 0

        # Nombres relativos a MEDIA_ROOT con separador unix (como los guarda Django)
        referenced = {name.replace('\\', '/') for name in referenced}
        now = time.time()
        orphans = []
        for path in base.rglob('*'):
            if not path.is_file():
                continue
            rel = path.relative_to(self.media_root).as_posix()
            if rel in referenced:
                continue
            if now - path.stat().st_mtime < self.min_age_secs:
                continue  # demasiado reciente — posible import en curso
            orphans.append(path)

        freed = sum(p.stat().st_size for p in orphans)
        self.stdout.write(
            f'\n── {subdir}/: {len(orphans)} huérfanos ({freed / 1024 / 1024:.1f} MB) '
            f'de {len(referenced)} referenciados en BD'
        )
        for path in orphans[:10]:
            self.stdout.write(f'  {path.relative_to(self.media_root).as_posix()}')
        if len(orphans) > 10:
            self.stdout.write(f'  … y {len(orphans) - 10} más')

        if not self.dry_run:
            for path in orphans:
                try:
                    path.unlink()
                except OSError as e:
                    self.stderr.write(f'  no se pudo borrar {path}: {e}')
        return freed
