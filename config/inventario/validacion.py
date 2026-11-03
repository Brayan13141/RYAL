"""Validaciones compartidas de los puntos de entrada del inventario."""
from decimal import Decimal, InvalidOperation


def cabe_en(campo, valor) -> bool:
    """True si `valor` cabe en el DecimalField `campo` (max_digits/decimal_places del modelo).

    Postgres lanza DataError (500) si un monto excede el campo; esto se revisa antes de escribir.
    El valor se redondea a los decimales del campo, como lo haría la BD.
    """
    try:
        d = Decimal(valor)
        if not d.is_finite():
            return False
        redondeado = d.quantize(Decimal(1).scaleb(-campo.decimal_places))
    except (InvalidOperation, TypeError, ValueError):
        return False
    enteros = campo.max_digits - campo.decimal_places
    return abs(redondeado) < Decimal(10) ** enteros
