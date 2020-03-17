# Generated by Django 3.0.4 on 2020-03-11 10:55

from django.db import migrations

from itou.prescribers.models import PrescriberOrganization


def move_data_forward(apps, schema_editor):
    """
    Set `kind`.
    """

    for prescriber_org in PrescriberOrganization.objects.filter(is_authorized=True):

        if prescriber_org.code_safir_pole_emploi:
            prescriber_org.kind = PrescriberOrganization.Kind.PE
            prescriber_org.save()
            continue

        name = prescriber_org.name.lower()

        if name.startswith("c.h.r.s"):
            prescriber_org.kind = PrescriberOrganization.Kind.CHRS

        elif name.startswith("cap emploi"):
            prescriber_org.kind = PrescriberOrganization.Kind.CAP_EMPLOI

        elif name.startswith("conseil départemental"):
            prescriber_org.kind = PrescriberOrganization.Kind.DEPT

        elif name.startswith("direction territoriale de la protection judiciaire de la jeunesse"):
            prescriber_org.kind = PrescriberOrganization.Kind.PJJ

        elif name.startswith("mission locale"):
            prescriber_org.kind = PrescriberOrganization.Kind.ML

        elif name.startswith("mission locale") or name.startswith("adefi"):
            prescriber_org.kind = PrescriberOrganization.Kind.ML

        elif name.startswith("plie"):
            prescriber_org.kind = PrescriberOrganization.Kind.PLIE

        elif name.startswith("spip") or name.startswith("service penitentiaire d'insertion et de probation"):
            prescriber_org.kind = PrescriberOrganization.Kind.SPIP

        prescriber_org.save()


class Migration(migrations.Migration):

    dependencies = [("prescribers", "0010_auto_20200311_1404")]

    operations = [migrations.RunPython(move_data_forward, migrations.RunPython.noop)]