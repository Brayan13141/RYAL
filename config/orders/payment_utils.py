"""Lógica pura (sin ORM) para migrar pedidos legacy (deposit + is_paid) al
historial de pagos. Importable desde la migración de datos y desde los tests."""


def plan_backfill_payments(total, deposit, is_paid):
    """Devuelve los pagos a crear para un pedido legacy.

    Cada elemento es {'monto': Decimal, 'notas': str}.
    - Si hubo adelanto (deposit > 0): un pago 'Adelanto migrado'.
    - Si estaba liquidado y queda saldo: un pago 'Liquidación migrada' por el resto.
    """
    payments = []
    if deposit and deposit > 0:
        payments.append({'monto': deposit, 'notas': 'Adelanto migrado'})
    if is_paid:
        remainder = total - deposit
        if remainder > 0:
            payments.append({'monto': remainder, 'notas': 'Liquidación migrada'})
    return payments
