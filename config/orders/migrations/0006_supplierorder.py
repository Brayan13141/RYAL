from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0005_order_tracking_token'),
    ]

    operations = [
        migrations.CreateModel(
            name='SupplierOrder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', 'Pendiente'), ('running', 'En progreso'), ('done', 'Completado'), ('partial', 'Parcial'), ('failed', 'Fallido')], default='pending', max_length=20)),
                ('screenshot', models.ImageField(blank=True, null=True, upload_to='supplier_orders/')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('order', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='supplier_order', to='orders.order')),
            ],
            options={'verbose_name': 'Pedido proveedor'},
        ),
        migrations.CreateModel(
            name='SupplierOrderItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('supplier_url', models.URLField(blank=True, max_length=500)),
                ('variant_target', models.CharField(blank=True, max_length=200)),
                ('status', models.CharField(choices=[('pending', 'Pendiente'), ('added', 'Agregado'), ('variant_not_found', 'Variante no encontrada'), ('no_url', 'Sin URL de proveedor')], default='pending', max_length=30)),
                ('notes', models.TextField(blank=True)),
                ('order_item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='supplier_item', to='orders.orderitem')),
                ('supplier_order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='orders.supplierorder')),
            ],
            options={'verbose_name': 'Ítem de pedido proveedor'},
        ),
    ]
