from decimal import Decimal

from django.db import migrations

from orders.payment_utils import plan_backfill_payments


def backfill(apps, schema_editor):
    Order = apps.get_model('orders', 'Order')
    OrderPayment = apps.get_model('orders', 'OrderPayment')
    for order in Order.objects.prefetch_related('items').all():
        total = sum(
            (i.price_snapshot * i.quantity for i in order.items.all()),
            Decimal('0'),
        ) - order.descuento_aplicado
        for p in plan_backfill_payments(total, order.deposit, order.is_paid):
            OrderPayment.objects.create(
                order=order,
                fecha=order.created_at.date(),
                monto=p['monto'],
                metodo_pago='efectivo',
                notas=p['notas'],
            )
        pagado = sum((pp.monto for pp in order.payments.all()), Decimal('0'))
        paid = (total - pagado) <= 0
        if order.is_paid != paid:
            order.is_paid = paid
            order.save(update_fields=['is_paid'])


def reverse(apps, schema_editor):
    OrderPayment = apps.get_model('orders', 'OrderPayment')
    OrderPayment.objects.filter(
        notas__in=['Adelanto migrado', 'Liquidación migrada']
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0011_orderpayment'),
    ]

    operations = [
        migrations.RunPython(backfill, reverse),
    ]
