# Generated by Django 3.0.6 on 2020-05-13 15:08

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("users", "0008_auto_20200506_1103")]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="resume_link",
            field=models.URLField(max_length=500, null=True, verbose_name="Lien vers un CV"),
        )
    ]
