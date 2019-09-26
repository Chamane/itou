# Generated by Django 2.2.4 on 2019-09-26 08:05

from django.db import migrations, models
import itou.utils.tokens


class Migration(migrations.Migration):

    dependencies = [("prescribers", "0002_prescriberorganization_secret_code")]

    operations = [
        migrations.AddField(
            model_name="prescriberorganization",
            name="is_authorized",
            field=models.BooleanField(
                default=False, verbose_name="Habilité par le préfet"
            ),
        ),
        migrations.AlterField(
            model_name="prescriberorganization",
            name="secret_code",
            field=models.CharField(
                default=itou.utils.tokens.generate_random_token,
                help_text="Code permettant à un utilisateur de rejoindre l'organisation.",
                max_length=6,
                unique=True,
                verbose_name="Code secret",
            ),
        ),
    ]