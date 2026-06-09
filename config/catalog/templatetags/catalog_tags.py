from django import template
from catalog.models import PendingProduct

register = template.Library()

@register.simple_tag
def pending_products_count():
    return PendingProduct.objects.filter(status='pending').count()
