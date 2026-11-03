from orders.models import SupplierOrderItem


def crear_supplier_item(supplier_order, order_item):
    """Crea el SupplierOrderItem de un renglón: con URL de Modaverse queda 'pending', si no 'no_url'."""
    url = ''
    status = 'pending'
    product = order_item.product
    if product and product.supplier_url and 'modaverse' in product.supplier_url:
        url = product.supplier_url
    else:
        status = 'no_url'
    return SupplierOrderItem.objects.create(
        supplier_order=supplier_order,
        order_item=order_item,
        supplier_url=url,
        variant_target=order_item.variant_snapshot or '',
        status=status,
    )
