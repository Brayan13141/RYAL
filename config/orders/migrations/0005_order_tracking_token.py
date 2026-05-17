import uuid
from django.db import migrations, models


def populate_tracking_tokens(apps, schema_editor):
    Order = apps.get_model('orders', 'Order')
    for order in Order.objects.filter(tracking_token=None):
        order.tracking_token = uuid.uuid4()
        order.save(update_fields=['tracking_token'])


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0004_order_deposit_order_is_paid'),
    ]

    operations = [
        # Step 1: add column nullable (no unique yet) so existing rows get None
        migrations.AddField(
            model_name='order',
            name='tracking_token',
            field=models.UUIDField(null=True, blank=True, db_index=True),
        ),
        # Step 2: fill unique UUIDs for every existing row
        migrations.RunPython(populate_tracking_tokens, migrations.RunPython.noop),
        # Step 3: enforce unique + not null now that all rows have a value
        migrations.AlterField(
            model_name='order',
            name='tracking_token',
            field=models.UUIDField(default=uuid.uuid4, unique=True, db_index=True),
        ),
    ]
