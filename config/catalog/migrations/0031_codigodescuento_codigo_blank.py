from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0030_alter_codigodescuento_tipo_articulo'),
    ]

    operations = [
        migrations.AlterField(
            model_name='codigodescuento',
            name='codigo',
            field=models.CharField(blank=True, max_length=50, unique=True),
        ),
    ]
