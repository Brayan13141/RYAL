import re
import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from negocio.models import Pedido

VIA_CHOICES = [('aereo', 'Aéreo'), ('maritimo', 'Marítimo'), ('nacional', 'Nacional')]
VIAS = dict(VIA_CHOICES)


def fmt_talla(d):
    """Talla MX sin ceros sobrantes ni notación científica: 26.0 -> '26', 27.5 -> '27.5', 30.00 -> '30'."""
    return format(d.normalize(), 'f')


class TallaCalzado(models.Model):
    """Talla de calzado. La llave es la talla MX en cm; el resto de equivalencias es opcional."""
    mx_cm = models.DecimalField(max_digits=4, decimal_places=1, unique=True)
    eu = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    us_hombre = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    us_mujer = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    cn_mm = models.PositiveIntegerField(null=True, blank=True)
    orden = models.PositiveIntegerField(default=0)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ['orden']
        verbose_name = 'talla de calzado'
        verbose_name_plural = 'tallas de calzado'

    def __str__(self):
        return f'{fmt_talla(self.mx_cm)} MX'


class Color(models.Model):
    nombre = models.CharField(max_length=60, unique=True)
    # Sinónimos separados por comas; es lo que reconoce el bot.
    alias = models.CharField(max_length=200, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre']

    @staticmethod
    def normalizar(texto):
        """Minúsculas, sin acentos y con espacios colapsados."""
        sin_acentos = ''.join(
            c for c in unicodedata.normalize('NFKD', texto or '')
            if not unicodedata.combining(c)
        )
        return re.sub(r'\s+', ' ', sin_acentos).strip().lower()

    @property
    def alias_list(self):
        return [a for a in (self.normalizar(x) for x in self.alias.split(',')) if a]

    def save(self, *args, **kwargs):
        self.nombre = self.normalizar(self.nombre)
        self.alias = ', '.join(self.alias_list)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nombre


class TarifaFlete(models.Model):
    """Flete por par según la vía. El lote copia el monto al crearse."""
    via = models.CharField(max_length=10, choices=VIA_CHOICES, unique=True)
    monto_por_par = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        verbose_name = 'tarifa de flete'
        verbose_name_plural = 'tarifas de flete'

    def __str__(self):
        return f'{self.get_via_display()}: {self.monto_por_par}'


class TransicionInvalida(Exception):
    """El cambio de estado de un par no está permitido (o falta nota/motivo/origen)."""


class Lote(models.Model):
    ORIGEN_CHOICES = [('manual', 'Manual'), ('csv', 'CSV'), ('inicial', 'Inicial')]

    referencia = models.CharField(max_length=100)
    origen = models.CharField(max_length=10, choices=ORIGEN_CHOICES)
    via = models.CharField(max_length=10, choices=VIA_CHOICES)
    # Copia de TarifaFlete al crear el lote: cambiar la tarifa no re-costea lotes viejos.
    flete_por_par = models.DecimalField(max_digits=8, decimal_places=2)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+')
    creado_at = models.DateTimeField(auto_now_add=True)
    notas = models.TextField(blank=True)
    cerrado_at = models.DateTimeField(null=True, blank=True)
    # sha256 del token de confirmación (CSV o alta manual): el UNIQUE evita el lote doble.
    token_sha = models.CharField(max_length=64, unique=True, null=True, blank=True)

    class Meta:
        ordering = ['-creado_at']

    def __str__(self):
        return self.referencia


# (de, a) -> orígenes permitidos (None = cualquiera)
TRANSICIONES = {
    ('por_recibir', 'en_stock'): {'origenes': None},  # recibir
    ('por_recibir', 'baja'): {'origenes': None},  # cierre de lote, motivo faltante
    ('baja', 'en_stock'): {'origenes': None},  # "apareció", solo si fue faltante
    ('en_stock', 'vendido'): {'origenes': {'escaner', 'bot'}},
    ('en_stock', 'baja'): {'origenes': None},  # danado / perdido con nota
    ('vendido', 'en_stock'): {'origenes': {'deshacer'}},
    ('en_stock', 'por_recibir'): {'origenes': {'deshacer'}},
}
# Transiciones que solo se aceptan con nota no vacía.
_EXIGEN_NOTA = {('vendido', 'en_stock'), ('en_stock', 'por_recibir')}


class Par(models.Model):
    class Estado(models.TextChoices):
        POR_RECIBIR = 'por_recibir', 'Por recibir'
        EN_STOCK = 'en_stock', 'En stock'
        VENDIDO = 'vendido', 'Vendido'
        BAJA = 'baja', 'Baja'

    MOTIVO_CHOICES = [
        ('faltante', 'Faltante'), ('danado', 'Dañado'), ('perdido', 'Perdido')]

    codigo = models.CharField(max_length=20, unique=True)
    tipo = models.ForeignKey('catalog.TipoArticulo', on_delete=models.PROTECT, related_name='+')
    color = models.ForeignKey(Color, on_delete=models.PROTECT, related_name='+')
    talla = models.ForeignKey(TallaCalzado, on_delete=models.PROTECT, related_name='+')
    lote = models.ForeignKey(Lote, on_delete=models.PROTECT, related_name='pares')
    estado = models.CharField(
        max_length=12, choices=Estado.choices, default=Estado.POR_RECIBIR)
    motivo_baja = models.CharField(max_length=10, choices=MOTIVO_CHOICES, blank=True)
    precio_proveedor = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True)
    flete = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    costo = models.DecimalField(max_digits=8, decimal_places=2)
    recibido_at = models.DateTimeField(null=True, blank=True)
    vendido_at = models.DateTimeField(null=True, blank=True)
    pedido_item = models.ForeignKey(
        'negocio.PedidoItem', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='pares')

    class Meta:
        indexes = [models.Index(fields=['tipo', 'color', 'talla', 'estado'])]

    def __str__(self):
        return self.codigo

    def transicionar(self, a_estado, *, usuario, origen, nota='', motivo='', pedido_item=None):
        """Único camino para cambiar `estado`. Bloquea la fila y valida contra TRANSICIONES."""
        nota = (nota or '').strip()
        with transaction.atomic():
            # Se relee bajo candado: la instancia en memoria puede estar vencida.
            bloqueado = Par.objects.select_for_update().get(pk=self.pk)
            de = bloqueado.estado
            regla = TRANSICIONES.get((de, a_estado))
            if regla is None:
                raise TransicionInvalida(f'{de} -> {a_estado} no está permitido')
            if regla['origenes'] is not None and origen not in regla['origenes']:
                raise TransicionInvalida(
                    f'{de} -> {a_estado} exige origen {sorted(regla["origenes"])}')
            if (de, a_estado) in _EXIGEN_NOTA and not nota:
                raise TransicionInvalida(f'{de} -> {a_estado} exige una nota')
            if a_estado == 'baja':
                if motivo not in dict(self.MOTIVO_CHOICES):
                    raise TransicionInvalida('La baja exige un motivo válido')
                if de == 'por_recibir' and motivo != 'faltante':
                    raise TransicionInvalida(
                        'Un par por recibir solo puede darse de baja como faltante')
                if de == 'en_stock' and (motivo == 'faltante' or not nota):
                    raise TransicionInvalida(
                        'Baja de stock: motivo dañado/perdido y nota obligatoria')
            if de == 'baja' and bloqueado.motivo_baja != 'faltante':
                raise TransicionInvalida('Solo un faltante puede volver a stock')

            ahora = timezone.now()
            if a_estado == 'en_stock' and de in ('por_recibir', 'baja'):
                bloqueado.recibido_at = ahora
            if a_estado == 'por_recibir':
                bloqueado.recibido_at = None
            if a_estado == 'vendido':
                bloqueado.vendido_at = ahora
                bloqueado.pedido_item = pedido_item
            if de == 'vendido':
                bloqueado.vendido_at = None
                bloqueado.pedido_item = None
            bloqueado.motivo_baja = motivo if a_estado == 'baja' else ''
            bloqueado.estado = a_estado
            bloqueado.save()
            movimiento = MovimientoPar.objects.create(
                par=bloqueado, de_estado=de, a_estado=a_estado, usuario=usuario,
                origen=origen, nota=nota)

        # Sincroniza la instancia del llamador con lo que quedó en la BD.
        for campo in ('estado', 'motivo_baja', 'recibido_at', 'vendido_at', 'pedido_item'):
            setattr(self, campo, getattr(bloqueado, campo))
        return movimiento


class MovimientoPar(models.Model):
    ORIGEN_CHOICES = [
        ('escaner', 'Escáner'), ('bot', 'Bot'), ('cierre_lote', 'Cierre de lote'),
        ('deshacer', 'Deshacer'), ('manual', 'Manual'), ('alta', 'Alta')]

    par = models.ForeignKey(Par, on_delete=models.CASCADE, related_name='movimientos')
    de_estado = models.CharField(max_length=12, choices=Par.Estado.choices)
    a_estado = models.CharField(max_length=12, choices=Par.Estado.choices)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+')
    origen = models.CharField(max_length=12, choices=ORIGEN_CHOICES)
    nota = models.TextField(blank=True)
    creado_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['creado_at', 'pk']


class PrecioVenta(models.Model):
    """Precio de venta nombrado ("Normal", "Mayoreo") de un modelo base."""
    tipo = models.ForeignKey(
        'catalog.TipoArticulo', on_delete=models.CASCADE, related_name='precios_venta')
    nombre = models.CharField(max_length=60)
    precio = models.DecimalField(max_digits=8, decimal_places=2)
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['orden', 'pk']

    def clean(self):
        super().clean()
        if self.tipo_id and self.tipo.es_variante:
            raise ValidationError(
                {'tipo': 'El precio de venta va en el modelo base, no en la variante.'})

    def __str__(self):
        return f'{self.nombre}: {self.precio}'


CALIDAD_CHOICES = [('importado', 'Importado'), ('nacional', 'Nacional')]
# Aéreo y marítimo son el mismo producto importado: solo cambia cómo llegó.
CALIDAD_DE_VIA = {'aereo': 'importado', 'maritimo': 'importado', 'nacional': 'nacional'}


class PrecioLista(models.Model):
    """Precio de venta por calidad y tipo de venta, igual para todos los modelos.

    El escáner llena con él cada par al vender; el precio escrito a mano gana.
    """
    calidad = models.CharField(max_length=10, choices=CALIDAD_CHOICES)
    tipo_venta = models.CharField(max_length=10, choices=Pedido.TIPO_VENTA_CHOICES)
    precio = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        ordering = ['calidad', 'tipo_venta']
        constraints = [models.UniqueConstraint(
            fields=['calidad', 'tipo_venta'], name='precio_lista_unico')]
        verbose_name = 'precio de lista'
        verbose_name_plural = 'lista de precios'

    def __str__(self):
        return f'{self.get_calidad_display()} {self.get_tipo_venta_display()}: {self.precio}'


def precio_lista_de(par):
    """{'mayoreo': Decimal, 'menudeo': Decimal} según la calidad del par (lo que haya en la lista)."""
    calidad = CALIDAD_DE_VIA.get(par.tipo.via or par.lote.via)
    if calidad is None:
        return {}
    return {p.tipo_venta: p.precio for p in PrecioLista.objects.filter(calidad=calidad)}


class Conteo(models.Model):
    """Inventario físico: se escanea lo que hay en el estante y se compara con el stock."""
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+')
    creado_at = models.DateTimeField(auto_now_add=True)
    cerrado_at = models.DateTimeField(null=True, blank=True)
    notas = models.TextField(blank=True)

    class Meta:
        ordering = ['-creado_at', '-pk']

    def __str__(self):
        return f'Conteo #{self.pk}'


class LecturaConteo(models.Model):
    """Un código leído en un conteo; `par` es None si el código no existe."""
    conteo = models.ForeignKey(Conteo, on_delete=models.CASCADE, related_name='lecturas')
    par = models.ForeignKey(
        Par, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    codigo_leido = models.CharField(max_length=60)
    creado_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['creado_at', 'pk']
        constraints = [models.UniqueConstraint(
            fields=['conteo', 'codigo_leido'], name='lectura_conteo_unica')]


class LecturaRepetida(models.Model):
    """Un par escaneado para recibir cuando ya estaba en stock. No cambia el estado
    (por eso no es un MovimientoPar): sirve para encontrar cajas leídas dos veces o
    dos cajas con la misma etiqueta."""
    par = models.ForeignKey(Par, on_delete=models.CASCADE, related_name='lecturas_repetidas')
    lote = models.ForeignKey(Lote, on_delete=models.CASCADE, related_name='lecturas_repetidas')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+')
    creado_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['creado_at', 'pk']
