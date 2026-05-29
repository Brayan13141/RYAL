from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0018_alter_product_options_product_display_order'),
    ]

    operations = [
        migrations.CreateModel(
            name='SubcategorySection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('display_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('subcategory', models.ForeignKey(
                    limit_choices_to={'parent__isnull': False},
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='product_sections',
                    to='catalog.category',
                )),
                ('products', models.ManyToManyField(
                    blank=True,
                    related_name='in_subsections',
                    to='catalog.product',
                )),
            ],
            options={
                'verbose_name': 'Sección de subcategoría',
                'verbose_name_plural': 'Secciones de subcategoría',
                'ordering': ['display_order', 'name'],
            },
        ),
    ]
