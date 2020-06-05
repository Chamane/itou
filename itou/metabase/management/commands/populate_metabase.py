"""

FIXME

"""
import logging

import psycopg2
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils.translation import gettext, gettext_lazy as _
from tqdm import tqdm

from itou.eligibility.models import EligibilityDiagnosis, SelectedAdministrativeCriteria
from itou.job_applications.models import JobApplication, JobApplicationWorkflow
from itou.prescribers.models import PrescriberOrganization
from itou.siaes.models import Siae
from itou.utils.address.departments import DEPARTMENT_TO_REGION, DEPARTMENTS


# FIXME
ENABLE_WIP_MODE = False
WIP_MODE_ROWS_PER_TABLE = 50

# Bench results for self.populate_diagnostics()
# by batch of 1 => 2m51s
# by batch of 10 => 19s
# by batch of 100 => 5s
# by batch of 1000 => 5s
INSERT_BATCH_SIZE = 100

# Different wording than the original JobApplication.SENDER_KIND_CHOICES
SENDER_KIND_CHOICES = (
    (JobApplication.SENDER_KIND_JOB_SEEKER, _("Candidature autonome")),
    (JobApplication.SENDER_KIND_PRESCRIBER, _("Candidature via prescripteur")),
    (JobApplication.SENDER_KIND_SIAE_STAFF, _("Auto-prescription")),
)


def chunks(l, n):
    """
    Yield successive n-sized chunks from l.
    """
    for i in range(0, len(l), n):
        yield l[i : i + n]


def get_choice(choices, key):
    choices = dict(choices)
    # Gettext fixes `can't adapt type '__proxy__'` error
    # due to laxy_gettext and psycopg2 not going well together.
    # See https://code.djangoproject.com/ticket/13965
    return gettext(choices[key])


def get_siae_first_join_date(siae):
    if siae.siaemembership_set.exists():
        return siae.siaemembership_set.order_by("joined_at").first().joined_at
    return None


def get_job_application_sub_type(ja):
    sub_type = get_choice(choices=SENDER_KIND_CHOICES, key=ja.sender_kind)
    if ja.sender_kind == JobApplication.SENDER_KIND_SIAE_STAFF:
        sub_type += f" {ja.sender_siae.kind}"
    if ja.sender_kind == JobApplication.SENDER_KIND_PRESCRIBER:
        if ja.sender_prescriber_organization:
            sub_type += f" {ja.sender_prescriber_organization.kind}"
        else:
            sub_type += f" sans organisation"
    return sub_type


def get_ja_time_spent_from_new_to_processing(ja):
    # Find the new=>processing transition log.
    logs = ja.logs.filter(transition=JobApplicationWorkflow.TRANSITION_PROCESS)
    if logs.exists():
        assert logs.count() == 1
        new_timestamp = ja.created_at
        processing_timestamp = logs.first().timestamp
        assert processing_timestamp > new_timestamp
        time_spent_from_new_to_processing = processing_timestamp - new_timestamp
        return time_spent_from_new_to_processing
    return None


def get_ja_time_spent_from_new_to_accepted_or_refused(ja):
    # Find the *=>accepted or *=>refused transition log.
    logs = ja.logs.filter(to_state__in=[JobApplicationWorkflow.STATE_ACCEPTED, JobApplicationWorkflow.STATE_REFUSED])
    if logs.exists():
        assert logs.count() == 1
        new_timestamp = ja.created_at
        accepted_or_refused_timestamp = logs.first().timestamp
        assert accepted_or_refused_timestamp > new_timestamp
        time_spent_from_new_to_accepted_or_refused = accepted_or_refused_timestamp - new_timestamp
        return time_spent_from_new_to_accepted_or_refused
    return None


class MetabaseDatabaseCursor:
    def __enter__(self):
        self.conn = psycopg2.connect(
            host=settings.METABASE_HOST,
            port=settings.METABASE_PORT,
            dbname=settings.METABASE_DATABASE,
            user=settings.METABASE_USER,
            password=settings.METABASE_PASSWORD,
        )
        self.cur = self.conn.cursor()
        return self.cur

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.conn.commit()
        self.cur.close()
        self.conn.close()


class Command(BaseCommand):
    """
    FIXME

    FIXME:
        django-admin populate_metabase --verbosity=2
        docker exec -ti itou_django django-admin populate_metabase --verbosity 2
    """

    help = "FIXME."

    def set_logger(self, verbosity):
        """
        Set logger level based on the verbosity option.
        """
        handler = logging.StreamHandler(self.stdout)

        self.logger = logging.getLogger(__name__)
        self.logger.propagate = False
        self.logger.addHandler(handler)

        self.logger.setLevel(logging.INFO)
        if verbosity > 1:
            self.logger.setLevel(logging.DEBUG)

    def log(self, message):
        self.logger.debug(message)

    def cleanup_tables(self, table_name):
        self.cur.execute(f"DROP TABLE IF EXISTS {table_name}_new;")
        self.cur.execute(f"DROP TABLE IF EXISTS {table_name}_old;")
        self.cur.execute(f"DROP TABLE IF EXISTS {table_name}_wip;")
        self.cur.execute(f"DROP TABLE IF EXISTS {table_name}_wip_new;")
        self.cur.execute(f"DROP TABLE IF EXISTS {table_name}_wip_old;")

    def populate_table(self, table_name, table_columns, objects):
        self.cleanup_tables(table_name)

        if ENABLE_WIP_MODE:
            table_name = f"{table_name}_wip"
            objects = objects[:WIP_MODE_ROWS_PER_TABLE]

        self.log(f"Injecting {len(objects)} records into table {table_name}:")

        # Create table.
        statement = ", ".join([f'{c["name"]} {c["type"]}' for c in table_columns])
        self.cur.execute(f"CREATE TABLE {table_name}_new ({statement});")

        # Add comments on table columns.
        for c in table_columns:
            assert set(c.keys()) == set(["name", "type", "comment", "lambda"])
            column_name = c["name"]
            column_comment = c["comment"]
            self.cur.execute(f"comment on column {table_name}_new.{column_name} is '{column_comment}';")

        # Insert rows.
        column_names = [f'{c["name"]}' for c in table_columns]
        statement = ", ".join(column_names)
        insert_query = f"insert into {table_name}_new ({statement}) values %s"
        with tqdm(total=len(objects)) as progress_bar:
            for chunk in chunks(objects, n=INSERT_BATCH_SIZE):
                data = [[c["lambda"](o) for c in table_columns] for o in chunk]
                psycopg2.extras.execute_values(self.cur, insert_query, data, template=None)
                progress_bar.update(len(chunk))

        # Swap new and old table nicely to avoid downtime.
        self.cur.execute(f"ALTER TABLE IF EXISTS {table_name} RENAME TO {table_name}_old;")
        self.cur.execute(f"ALTER TABLE {table_name}_new RENAME TO {table_name};")
        self.cur.execute(f"DROP TABLE IF EXISTS {table_name}_old;")

    def populate_siaes(self):
        table_name = "structures"

        table_columns = [
            {"name": "id", "type": "integer", "comment": "ID de la structure", "lambda": lambda o: o.id},
            {"name": "nom", "type": "varchar", "comment": "Nom de la structure", "lambda": lambda o: o.display_name},
            {
                "name": "type",
                "type": "varchar",
                "comment": "Type de structure (EI, ETTI, ACI, GEIQ etc..)",
                "lambda": lambda o: o.kind,
            },
            {"name": "siret", "type": "varchar", "comment": "SIRET de la structure", "lambda": lambda o: o.siret},
            {
                "name": "source",
                "type": "varchar",
                "comment": "Source des données de la structure",
                "lambda": lambda o: get_choice(choices=Siae.SOURCE_CHOICES, key=o.source),
            },
            {
                "name": "adresse_ligne_1",
                "type": "varchar",
                "comment": "Première ligne adresse",
                "lambda": lambda o: o.address_line_1,
            },
            {
                "name": "adresse_ligne_2",
                "type": "varchar",
                "comment": "Seconde ligne adresse",
                "lambda": lambda o: o.address_line_2,
            },
            {
                "name": "code_postal",
                "type": "varchar",
                "comment": "Code postal de la structure",
                "lambda": lambda o: o.post_code,
            },
            {"name": "ville", "type": "varchar", "comment": "Ville de la structure", "lambda": lambda o: o.city},
            {
                "name": "département",
                "type": "varchar",
                "comment": "Département de la structure",
                "lambda": lambda o: o.department,
            },
            {
                "name": "nom_département",
                "type": "varchar",
                "comment": "Nom complet du département de la structure",
                "lambda": lambda o: DEPARTMENTS[o.department],
            },
            {
                "name": "région",
                "type": "varchar",
                "comment": "Région de la structure",
                "lambda": lambda o: DEPARTMENT_TO_REGION[o.department],
            },
            {
                "name": "date_inscription",
                "type": "date",
                "comment": "Date inscription du premier compte employeur",
                "lambda": get_siae_first_join_date,
            },
            {
                "name": "total_membres",
                "type": "integer",
                "comment": "Nombre de comptes employeur rattachés à la structure",
                "lambda": lambda o: o.members.count(),
            },
            {
                "name": "total_candidatures",
                "type": "integer",
                "comment": "Nombre de candidatures dont la structure est destinataire",
                # FIXME ugly af
                "lambda": lambda o: JobApplication.objects.filter(to_siae_id=o.id).count(),
            },
            {
                "name": "total_embauches",
                "type": "integer",
                "comment": "Nombre de candidatures en état accepté dont la structure est destinataire",
                # FIXME ugly af
                "lambda": lambda o: JobApplication.objects.filter(
                    to_siae_id=o.id, state=JobApplicationWorkflow.STATE_ACCEPTED
                ).count(),
            },
            {
                "name": "total_candidatures_nouvelles",
                "type": "integer",
                "comment": "Nombre de candidatures en état nouveau dont la structure est destinataire",
                # FIXME ugly af
                "lambda": lambda o: JobApplication.objects.filter(
                    to_siae_id=o.id, state=JobApplicationWorkflow.STATE_NEW
                ).count(),
            },
        ]

        # FIXME select_related for better perf
        objects = Siae.objects.all()

        self.populate_table(table_name=table_name, table_columns=table_columns, objects=objects)

    def populate_organisations(self):
        table_name = "organisations"

        table_columns = [
            {"name": "id", "type": "integer", "comment": "ID organisation", "lambda": lambda o: o.id},
            {"name": "nom", "type": "varchar", "comment": "Nom organisation", "lambda": lambda o: o.display_name},
            {
                "name": "type",
                "type": "varchar",
                "comment": "Type organisation (PE, ML...)",
                "lambda": lambda o: o.kind,
            },
            {
                "name": "habilitée",
                "type": "boolean",
                "comment": "Organisation habilitée par le préfet",
                "lambda": lambda o: o.is_authorized,
            },
            # DNRY address vs structure
            # DNRY dpt/region in many places
            {
                "name": "adresse_ligne_1",
                "type": "varchar",
                "comment": "Première ligne adresse",
                "lambda": lambda o: o.address_line_1,
            },
            {
                "name": "adresse_ligne_2",
                "type": "varchar",
                "comment": "Seconde ligne adresse",
                "lambda": lambda o: o.address_line_2,
            },
            {"name": "code_postal", "type": "varchar", "comment": "Code postal", "lambda": lambda o: o.post_code},
            {"name": "ville", "type": "varchar", "comment": "Ville", "lambda": lambda o: o.city},
            {"name": "département", "type": "varchar", "comment": "Département", "lambda": lambda o: o.department},
            # {
            #     "name": "nom_département",
            #     "type": "varchar",
            #     "comment": "Nom complet du département",
            #     "lambda": lambda o: DEPARTMENTS[o.department],
            # },
            # {
            #     "name": "région",
            #     "type": "varchar",
            #     "comment": "Région",
            #     "lambda": lambda o: DEPARTMENT_TO_REGION[o.department],
            # },
            # {
            #     "name": "date_inscription",
            #     "type": "date",
            #     "comment": "Date inscription du premier compte employeur",
            #     "lambda": get_siae_first_join_date,
            # },
            # {
            #     "name": "total_membres",
            #     "type": "integer",
            #     "comment": "Nombre de comptes employeur rattachés à la structure",
            #     "lambda": lambda o: o.members.count(),
            # },
            # {
            #     "name": "total_candidatures",
            #     "type": "integer",
            #     "comment": "Nombre de candidatures dont la structure est destinataire",
            #     # FIXME ugly af
            #     "lambda": lambda o: JobApplication.objects.filter(to_siae_id=o.id).count(),
            # },
            # {
            #     "name": "total_embauches",
            #     "type": "integer",
            #     "comment": "Nombre de candidatures en état accepté dont la structure est destinataire",
            #     # FIXME ugly af
            #     "lambda": lambda o: JobApplication.objects.filter(
            #         to_siae_id=o.id, state=JobApplicationWorkflow.STATE_ACCEPTED
            #     ).count(),
            # },
            # {
            #     "name": "total_candidatures_nouvelles",
            #     "type": "integer",
            #     "comment": "Nombre de candidatures en état nouveau dont la structure est destinataire",
            #     # FIXME ugly af
            #     "lambda": lambda o: JobApplication.objects.filter(
            #         to_siae_id=o.id, state=JobApplicationWorkflow.STATE_NEW
            #     ).count(),
            # },
        ]

        # FIXME select_related for better perf
        objects = PrescriberOrganization.objects.all()

        self.populate_table(table_name=table_name, table_columns=table_columns, objects=objects)

    def populate_job_applications(self):
        table_name = "candidatures"

        table_columns = [
            {"name": "id", "type": "varchar", "comment": "ID de la candidature", "lambda": lambda o: o.id},
            {
                "name": "date_candidature",
                "type": "date",
                "comment": "Date de la candidature",
                "lambda": lambda o: o.created_at,
            },
            {
                "name": "état",
                "type": "varchar",
                "comment": "Etat de la candidature",
                "lambda": lambda o: get_choice(choices=JobApplicationWorkflow.STATE_CHOICES, key=o.state),
            },
            {
                "name": "type",
                "type": "varchar",
                "comment": "Type de la candidature (auto-prescription, candidature autonome, candidature via prescripteur)",
                "lambda": lambda o: get_choice(choices=SENDER_KIND_CHOICES, key=o.sender_kind),
            },
            {
                "name": "sous_type",
                "type": "varchar",
                "comment": "Sous-type de la candidature (auto-prescription par EI, ACI... candidature autonome, candidature via prescripteur PE, ML...)",
                "lambda": get_job_application_sub_type,
            },
            {
                "name": "délai_prise_en_compte",
                "type": "interval",
                "comment": "Temps écoulé rétroactivement de état nouveau à état étude si la candidature est passée par ces états",
                "lambda": get_ja_time_spent_from_new_to_processing,
            },
            {
                "name": "délai_de_réponse",
                "type": "interval",
                "comment": "Temps écoulé rétroactivement de état nouveau à état accepté ou refusé si la candidature est passée par ces états",
                "lambda": get_ja_time_spent_from_new_to_accepted_or_refused,
            },
            {
                "name": "id_candidat",
                "type": "integer",
                "comment": "ID du candidat",
                "lambda": lambda o: o.job_seeker_id,
            },
            {
                "name": "id_structure",
                "type": "integer",
                "comment": "ID de la structure destinaire de la candidature",
                "lambda": lambda o: o.to_siae_id,
            },
            {
                "name": "type_structure",
                "type": "varchar",
                "comment": "Type de la structure destinaire de la candidature",
                "lambda": lambda o: o.to_siae.kind,
            },
            {
                "name": "nom_structure",
                "type": "varchar",
                "comment": "Nom de la structure destinaire de la candidature",
                "lambda": lambda o: o.to_siae.display_name,
            },
            {
                "name": "département_structure",
                "type": "varchar",
                "comment": "Département de la structure destinaire de la candidature",
                "lambda": lambda o: o.to_siae.department,
            },
            {
                "name": "nom_département_structure",
                "type": "varchar",
                "comment": "Nom complet du département de la structure destinaire de la candidature",
                "lambda": lambda o: DEPARTMENTS[o.to_siae.department],
            },
            {
                "name": "région_structure",
                "type": "varchar",
                "comment": "Région de la structure destinaire de la candidature",
                "lambda": lambda o: DEPARTMENT_TO_REGION[o.to_siae.department],
            },
        ]

        # FIXME select_related for better perf
        objects = JobApplication.objects.all()

        self.populate_table(table_name=table_name, table_columns=table_columns, objects=objects)

    def populate_job_seekers(self):
        table_name = "candidats"

        table_columns = [
            {"name": "id", "type": "integer", "comment": "ID du candidat", "lambda": lambda o: o.id},
            {
                "name": "date_de_naissance",
                "type": "date",
                "comment": "Date de naissance du candidat",
                "lambda": lambda o: o.birthdate,
            },
            {
                "name": "date_inscription",
                "type": "date",
                "comment": "Date inscription du candidat",
                "lambda": lambda o: o.date_joined,
            },
            {
                "name": "total_candidatures",
                "type": "integer",
                "comment": "Nombre de candidatures",
                "lambda": lambda o: o.job_applications.count(),
            },
            {
                "name": "total_embauches",
                "type": "integer",
                "comment": "Nombre de candidatures de type accepté",
                "lambda": lambda o: o.job_applications.filter(state=JobApplicationWorkflow.STATE_ACCEPTED).count(),
            },
            {
                "name": "total_diagnostics",
                "type": "integer",
                "comment": "Nombre de diagnostics",
                "lambda": lambda o: o.eligibility_diagnoses.count(),
            },
        ]

        # FIXME select_related for better perf
        objects = get_user_model().objects.filter(is_job_seeker=True).all()

        self.populate_table(table_name=table_name, table_columns=table_columns, objects=objects)

    def populate_diagnostics(self):
        table_name = "diagnostics"

        table_columns = [
            {"name": "id", "type": "integer", "comment": "ID du diagnostic", "lambda": lambda o: o.id},
            {
                "name": "id_candidat",
                "type": "integer",
                "comment": "ID du candidat",
                "lambda": lambda o: o.job_seeker_id,
            },
            {
                "name": "date_diagnostic",
                "type": "date",
                "comment": "Date du diagnostic",
                "lambda": lambda o: o.created_at,
            },
        ]

        # FIXME select_related for better perf
        objects = EligibilityDiagnosis.objects.all()

        self.populate_table(table_name=table_name, table_columns=table_columns, objects=objects)

    def populate_criteria(self):
        table_name = "critères"

        table_columns = [
            {"name": "id", "type": "integer", "comment": "ID du critère administratif", "lambda": lambda o: o.id},
            {
                "name": "id_diagnostic",
                "type": "integer",
                "comment": "ID du diagnostic",
                "lambda": lambda o: o.eligibility_diagnosis_id,
            },
            {
                "name": "id_candidat",
                "type": "integer",
                "comment": "ID du candidat",
                "lambda": lambda o: o.eligibility_diagnosis.job_seeker_id,
            },
            {
                "name": "niveau_critère",
                "type": "integer",
                "comment": "Niveau du critère adminsitratif (1, 2 ou 3)",
                "lambda": lambda o: o.administrative_criteria.level,
            },
            {
                "name": "nom_critère",
                "type": "varchar",
                "comment": "Nom du critère administratif",
                "lambda": lambda o: o.administrative_criteria.name,
            },
        ]

        # FIXME select_related for better perf
        objects = SelectedAdministrativeCriteria.objects.all()

        self.populate_table(table_name=table_name, table_columns=table_columns, objects=objects)

    def populate_metabase(self):
        with MetabaseDatabaseCursor() as cur:
            self.cur = cur
            self.populate_siaes()
            self.populate_organisations()
            self.populate_job_applications()
            self.populate_job_seekers()
            self.populate_diagnostics()
            self.populate_criteria()

    def handle(self, **options):
        self.set_logger(options.get("verbosity"))
        self.populate_metabase()
        self.log("-" * 80)
        self.log("Done.")
