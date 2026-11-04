from django import template

register = template.Library()

ICONOS_VIA = {'aereo': 'bi-airplane', 'maritimo': 'bi-water', 'nacional': 'bi-truck'}


@register.filter
def via_icono(via):
    """Clase de Bootstrap Icons de la vía: avión, barco o camión (MODA IA, nacional)."""
    return ICONOS_VIA.get(via, 'bi-box-seam')
