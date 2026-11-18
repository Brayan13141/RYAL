from django.db import models


class PagoBase(models.Model):
    """Lo que comparten un abono del negocio (negocio.Pago) y uno de la tienda web
    (orders.OrderPayment). `monto` y `created_at` quedan en cada modelo porque no
    son iguales: distinto número de dígitos y distinta forma de llenar la hora."""

    EFECTIVO = 'efectivo'
    TRANSFERENCIA = 'transferencia'
    TARJETA = 'tarjeta'
    OTRO = 'otro'
    METODO_CHOICES = [
        (EFECTIVO, 'Efectivo'),
        (TRANSFERENCIA, 'Transferencia'),
        (TARJETA, 'Tarjeta'),
        (OTRO, 'Otro'),
    ]

    fecha = models.DateField()
    metodo_pago = models.CharField(max_length=20, choices=METODO_CHOICES, default=EFECTIVO)
    notas = models.CharField(max_length=200, blank=True)

    class Meta:
        abstract = True
