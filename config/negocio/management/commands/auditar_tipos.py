from django.core.management.base import BaseCommand

from negocio.services import auditar_textos


class Command(BaseCommand):
    """Reporte de SOLO LECTURA. No tiene `--apply` y no escribe una sola fila.

    `costo_unitario` es una columna guardada y no se re-costea: Bryan ya
    decidio que las cuentas historicas no se mueven. Este comando existe para
    otra cosa — verificar que un alias resuelve como creias, y ver que textos
    estan cayendo en el tipo equivocado antes de que se acumulen.
    """

    help = ('Muestra a que tipo resuelve hoy cada texto de venta, si fue por '
            'alias o por keyword, y si el costo grabado coincide. SOLO LECTURA.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--solo-divergentes', action='store_true',
            help='Solo los textos cuyo costo grabado no coincide con la regla de hoy.')

    def handle(self, *args, **options):
        filas = auditar_textos()
        solo_div = options['solo_divergentes']
        if solo_div:
            filas = [f for f in filas if not f['coincide']]

        if not filas:
            self.stdout.write(
                'Sin divergencias: todo texto que resuelve tiene grabado el costo '
                'que dicta la regla de hoy.' if solo_div else
                'No hay ventas con textos que resuelvan a un tipo.')
            return

        cab = (f'{"TEXTO":<34} {"PZ":>4} {"PED":>4} {"RESUELVE A":<24} '
               f'{"ORIGEN":<8} {"REGLA":>10} {"GRABADO":>22}  ')
        self.stdout.write(cab)
        self.stdout.write('-' * len(cab))

        for f in filas:
            grabados = ', '.join(f'{c:,.2f}' for c in f['costos_grabados'])
            linea = (f'{f["texto"][:34]:<34} {f["piezas"]:>4} {f["pedidos"]:>4} '
                     f'{f["tipo"].nombre[:24]:<24} {f["origen"]:<8} '
                     f'{f["costo_regla"]:>10,.2f} {grabados:>22}  '
                     f'{"ok" if f["coincide"] else "NO COINCIDE"}')
            self.stdout.write(
                linea if f['coincide'] else self.style.WARNING(linea))

        divergentes = [f for f in filas if not f['coincide']]
        self.stdout.write('')
        self.stdout.write(
            f'{len(filas)} textos revisados, {len(divergentes)} sin coincidir. '
            f'Nada se modifico: este comando no escribe.')
