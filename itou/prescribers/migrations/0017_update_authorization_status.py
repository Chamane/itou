# Generated by Django 3.0.4 on 2020-05-06 14:05

from django.db import migrations

from itou.prescribers.models import PrescriberOrganization


# Custom migration:
# Update the new `authorization_status` field for prescribers organizations already in the DB
# Based on:
# - previous `is_authorized` field value
# - number of members in this organization (if =0, authorization is considered `NOT_SET`)
# - if authorization is not required => status becomes `NOT_REQUIRED`


def update_authorization_status(apps, schema_editor):
    for org in PrescriberOrganization.objects.filter(is_authorized=False):
        # Orgs that are not PE and with no habilitation
        org.authorization_status = PrescriberOrganization.AuthorizationStatus.NOT_REQUIRED
        org.save()

    for org in PrescriberOrganization.objects.filter(is_authorized=True):
        if org.kind == PrescriberOrganization.Kind.PE:
            # PE always authorized and validated
            org.authorization_status = PrescriberOrganization.AuthorizationStatus.VALIDATED
        elif not org.has_members:
            # Remove authorization status 'VALIDATED' for prescriber organizations without members
            org.is_authorized = False
            org.authorization_status = PrescriberOrganization.AuthorizationStatus.NOT_SET
        else:
            org.authorization_status = PrescriberOrganization.AuthorizationStatus.VALIDATED

        org.save()


class Migration(migrations.Migration):

    dependencies = [("prescribers", "0016_auto_20200513_1955")]

    operations = [migrations.RunPython(update_authorization_status, migrations.RunPython.noop)]
