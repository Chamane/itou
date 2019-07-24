# Generated by Django 2.2.3 on 2019-07-24 13:50

import django.contrib.gis.db.models.fields
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import itou.utils.validators


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Siae',
            fields=[
                ('address_line_1', models.CharField(max_length=256, verbose_name='Adresse postale, bôite postale')),
                ('address_line_2', models.CharField(blank=True, max_length=256, verbose_name='Appartement, suite, bloc, bâtiment, etc.')),
                ('zipcode', models.CharField(max_length=10, verbose_name='Code Postal')),
                ('city', models.CharField(max_length=256, verbose_name='Ville')),
                ('coords', django.contrib.gis.db.models.fields.PointField(blank=True, geography=True, null=True, srid=4326)),
                ('siret', models.CharField(max_length=14, primary_key=True, serialize=False, validators=[itou.utils.validators.validate_siret], verbose_name='Siret')),
                ('kind', models.CharField(choices=[('EI', "Entreprises d'insertion"), ('AI', 'Associations intermédiaires'), ('ACI', "Ateliers chantiers d'insertion"), ('ETTI', "Entreprises de travail temporaire d'insertion"), ('GEIQ', "Groupements d'employeurs pour l'insertion et la qualification"), ('RQ', 'Régies de quartier')], default='EI', max_length=4, verbose_name='Type')),
                ('name', models.CharField(max_length=256, verbose_name='Nom')),
                ('activities', models.CharField(max_length=256, verbose_name="Secteur d'activités")),
                ('phone', models.CharField(max_length=14, verbose_name='Téléphone')),
                ('email', models.EmailField(max_length=254, verbose_name='E-mail')),
            ],
            options={
                'verbose_name': "Structure d'insertion par l'activité économique",
                'verbose_name_plural': "Structures d'insertion par l'activité économique",
            },
        ),
        migrations.CreateModel(
            name='SiaeMembership',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('joined_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name="Date d'adhésion")),
                ('is_siae_admin', models.BooleanField(default=False, verbose_name='Administrateur de la SIAE')),
                ('siae', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='siae.Siae')),
            ],
        ),
    ]
