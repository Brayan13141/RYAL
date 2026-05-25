from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0007_supplierorder_modaverse_code'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='supplierorder',
            name='modaverse_code',
        ),
        migrations.RemoveField(
            model_name='supplierorder',
            name='screenshot',
        ),
        migrations.AddField(
            model_name='supplierorder',
            name='cart_script',
            field=models.TextField(blank=True),
        ),
    ]
