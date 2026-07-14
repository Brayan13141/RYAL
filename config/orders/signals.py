from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver


@receiver(user_logged_in)
def load_saved_cart(sender, request, user, **kwargs):
    from .models import SavedCartItem
    from .views import _price_with_volume_tier
    saved = SavedCartItem.objects.filter(user=user).select_related(
        'product__category__parent', 'variant'
    )
    if not saved.exists():
        return

    cart = request.session.get('cart', {})
    for item in saved:
        if item.cart_key in cart:
            continue  # sesión tiene prioridad
        try:
            price = float(
                item.variant.final_price if item.variant else item.product.final_price
            )
            # Reaplicar el descuento por volumen según la cantidad guardada —
            # cart_add/cart_update lo aplican, la restauración también debe.
            price = _price_with_volume_tier(item.product, item.quantity, price)
        except Exception:
            continue
        cart[item.cart_key] = {
            'product_id':   item.product_id,
            'variant_id':   item.variant_id,
            'variant_name': item.variant_name,
            'quantity':     item.quantity,
            'price':        price,
        }

    request.session['cart'] = cart
    request.session.modified = True
