from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('farmer', '0063_farmeroutstanding_farm')]

    operations = [
        migrations.AlterField(
            model_name='farmerinventory',
            name='quantity',
            field=models.FloatField(default=1),
        ),
    ]
