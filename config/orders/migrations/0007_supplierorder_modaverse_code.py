from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0006_supplierorder'),
    ]

    operations = [
        migrations.AddField(
            model_name='supplierorder',
            name='modaverse_code',
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
