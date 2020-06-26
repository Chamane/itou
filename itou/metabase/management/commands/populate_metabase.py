"""

FIXME

"""
import logging
from datetime import date, datetime, timezone
from functools import partial

import psycopg2
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext, gettext_lazy as _
from tqdm import tqdm

from itou.eligibility.models import AdministrativeCriteria, EligibilityDiagnosis
from itou.job_applications.models import JobApplication, JobApplicationWorkflow
from itou.prescribers.models import PrescriberOrganization
from itou.siaes.models import Siae
from itou.utils.address.departments import DEPARTMENT_TO_REGION, DEPARTMENTS


# FIXME
ENABLE_WIP_MODE = True
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

# Different wording than the original EligibilityDiagnosis.AUTHOR_KIND_CHOICES
AUTHOR_KIND_CHOICES = (
    (EligibilityDiagnosis.AUTHOR_KIND_JOB_SEEKER, _("Demandeur d'emploi")),
    (EligibilityDiagnosis.AUTHOR_KIND_PRESCRIBER, _("Prescripteur")),
    (EligibilityDiagnosis.AUTHOR_KIND_SIAE_STAFF, _("Employeur")),
)

# Special fake adhoc organization designed to gather stats
# of all prescriber accounts without organization.
# Otherwise organization stats would miss those accounts
# contribution.
# Of course this organization is *never* actually saved in db.
ORG_OF_PRESCRIBERS_WITHOUT_ORG = PrescriberOrganization(
    id=-1, name="Regroupement des prescripteurs sans organisation", kind="SANS-ORGANISATION", is_authorized=False
)


def anonymize(value, salt):
    """
    Use a salted hash to anonymize sensitive ids,
    mainly job_seeker id and job_application id.
    """
    return salted_hmac(salt, value, secret=settings.SECRET_KEY).hexdigest()


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
    if key in choices:
        return gettext(choices[key])
    return None


def get_siae_first_join_date(siae):
    if siae.siaemembership_set.exists():
        return siae.siaemembership_set.order_by("joined_at").first().joined_at
    return None


def get_org_first_join_date(org):
    if org != ORG_OF_PRESCRIBERS_WITHOUT_ORG and org.prescribermembership_set.exists():
        return org.prescribermembership_set.order_by("joined_at").first().joined_at
    return None


def get_org_members_count(org):
    if org == ORG_OF_PRESCRIBERS_WITHOUT_ORG:
        return get_user_model().objects.filter(is_prescriber=True, prescribermembership=None).count()
    return org.members.count()


def get_org_job_applications_count(org):
    if org == ORG_OF_PRESCRIBERS_WITHOUT_ORG:
        return JobApplication.objects.filter(
            sender_kind=JobApplication.SENDER_KIND_PRESCRIBER, sender_prescriber_organization=None
        ).count()
    return org.jobapplication_set.count()


def get_org_accepted_job_applications_count(org):
    if org == ORG_OF_PRESCRIBERS_WITHOUT_ORG:
        return JobApplication.objects.filter(
            sender_kind=JobApplication.SENDER_KIND_PRESCRIBER,
            sender_prescriber_organization=None,
            state=JobApplicationWorkflow.STATE_ACCEPTED,
        ).count()
    return org.jobapplication_set.filter(state=JobApplicationWorkflow.STATE_ACCEPTED).count()


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


def get_timedelta_since_org_last_job_application(org):
    if org != ORG_OF_PRESCRIBERS_WITHOUT_ORG:
        last_job_application = org.jobapplication_set.order_by("-created_at").first()
        if last_job_application:
            now = datetime.now(timezone.utc)
            return now - last_job_application.created_at
    return None


def get_user_age_in_years(user):
    if user.birthdate:
        return date.today().year - user.birthdate.year
    return None


def get_latest_diagnosis(job_seeker):
    assert job_seeker.is_job_seeker
    return job_seeker.eligibility_diagnoses.order_by("-created_at").first()


def get_latest_diagnosis_creation_date(job_seeker):
    latest_diagnosis = get_latest_diagnosis(job_seeker)
    if latest_diagnosis:
        return latest_diagnosis.created_at
    return None


def get_latest_diagnosis_author_kind(job_seeker):
    latest_diagnosis = get_latest_diagnosis(job_seeker)
    if latest_diagnosis:
        return get_choice(choices=AUTHOR_KIND_CHOICES, key=latest_diagnosis.author_kind)
    return None


def get_latest_diagnosis_author_sub_kind(job_seeker):
    latest_diagnosis = get_latest_diagnosis(job_seeker)
    if latest_diagnosis:
        author_kind = get_choice(choices=AUTHOR_KIND_CHOICES, key=latest_diagnosis.author_kind)
        if latest_diagnosis.author_kind == EligibilityDiagnosis.AUTHOR_KIND_SIAE_STAFF:
            author_sub_kind = latest_diagnosis.author_siae.kind
        elif latest_diagnosis.author_kind == EligibilityDiagnosis.AUTHOR_KIND_PRESCRIBER:
            author_sub_kind = latest_diagnosis.author_prescriber_organization.kind
        else:
            raise ValueError("Unexpected latest_diagnosis.author_kind")
        return f"{author_kind} {author_sub_kind}"
    return None


def get_latest_diagnosis_level1_criteria(job_seeker):
    latest_diagnosis = get_latest_diagnosis(job_seeker)
    if latest_diagnosis:
        return latest_diagnosis.administrative_criteria.level1().count()
    return None


def get_latest_diagnosis_level2_criteria(job_seeker):
    latest_diagnosis = get_latest_diagnosis(job_seeker)
    if latest_diagnosis:
        return latest_diagnosis.administrative_criteria.level2().count()
    return None


def get_latest_diagnosis_criteria(job_seeker, criteria_id):
    latest_diagnosis = get_latest_diagnosis(job_seeker)
    if latest_diagnosis:
        return latest_diagnosis.administrative_criteria.filter(id=criteria_id).exists()
    return None

def convert_boolean_to_int(b):
    # True => 1, False => 0, None => None.
    return None if b is None else int(b)

def compose_two_lambdas(f, g):
    # Compose two lambda methods.
    # https://stackoverflow.com/questions/16739290/composing-functions-in-python
    # I had to use this to solve a cryptic
    # `RecursionError: maximum recursion depth exceeded` error
    # when composing convert_boolean_to_int and c["lambda"].
    return lambda *a, **kw: f(g(*a, **kw))

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


def get_address_columns(name_suffix="", comment_suffix=""):
    return [
        {
            "name": f"adresse_ligne_1{name_suffix}",
            "type": "varchar",
            "comment": f"Première ligne adresse{comment_suffix}",
            "lambda": lambda o: o.address_line_1,
        },
        {
            "name": f"adresse_ligne_2{name_suffix}",
            "type": "varchar",
            "comment": f"Seconde ligne adresse{comment_suffix}",
            "lambda": lambda o: o.address_line_2,
        },
        {
            "name": f"code_postal{name_suffix}",
            "type": "varchar",
            "comment": f"Code postal{comment_suffix}",
            "lambda": lambda o: o.post_code,
        },
        {
            "name": f"ville{name_suffix}",
            "type": "varchar",
            "comment": f"Ville{comment_suffix}",
            "lambda": lambda o: o.city,
        },
    ] + get_department_and_region_columns(name_suffix, comment_suffix)


def get_department_and_region_columns(name_suffix="", comment_suffix="", custom_lambda=lambda o: o):
    return [
        {
            "name": f"département{name_suffix}",
            "type": "varchar",
            "comment": f"Département{comment_suffix}",
            "lambda": lambda o: custom_lambda(o).department,
        },
        {
            "name": f"nom_département{name_suffix}",
            "type": "varchar",
            "comment": f"Nom complet du département{comment_suffix}",
            "lambda": lambda o: DEPARTMENTS.get(custom_lambda(o).department),
        },
        {
            "name": f"région{name_suffix}",
            "type": "varchar",
            "comment": f"Région{comment_suffix}",
            "lambda": lambda o: DEPARTMENT_TO_REGION.get(custom_lambda(o).department),
        },
    ]


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

        # Transform boolean fields into 0/1 integer fields.
        # Metabase cannot sum or average boolean columns ¯\_(ツ)_/¯
        for c in table_columns:
            if c["type"] == "boolean":
                c["type"] = "integer"
                c["lambda"] = compose_two_lambdas(convert_boolean_to_int, c["lambda"])

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
                "name": "description",
                "type": "varchar",
                "comment": "Description de la structure",
                "lambda": lambda o: o.description,
            },
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
        ]
        table_columns += get_address_columns(comment_suffix=" de la structure")
        table_columns += [
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
                "name": "total_auto_prescriptions",
                "type": "integer",
                "comment": "Nombre de candidatures de source employeur dont la structure est destinataire",
                # FIXME ugly af
                "lambda": lambda o: JobApplication.objects.filter(
                    to_siae_id=o.id, sender_kind=JobApplication.SENDER_KIND_SIAE_STAFF
                ).count(),
            },
            {
                "name": "total_candidatures_autonomes",
                "type": "integer",
                "comment": "Nombre de candidatures de source candidat dont la structure est destinataire",
                # FIXME ugly af
                "lambda": lambda o: JobApplication.objects.filter(
                    to_siae_id=o.id, sender_kind=JobApplication.SENDER_KIND_JOB_SEEKER
                ).count(),
            },
            {
                "name": "total_candidatures_via_prescripteur",
                "type": "integer",
                "comment": "Nombre de candidatures de source prescripteur dont la structure est destinataire",
                # FIXME ugly af
                "lambda": lambda o: JobApplication.objects.filter(
                    to_siae_id=o.id, sender_kind=JobApplication.SENDER_KIND_PRESCRIBER
                ).count(),
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
                "name": "total_candidatures_non_traitées",
                "type": "integer",
                "comment": "Nombre de candidatures en état nouveau dont la structure est destinataire",
                # FIXME ugly af
                "lambda": lambda o: JobApplication.objects.filter(
                    to_siae_id=o.id, state=JobApplicationWorkflow.STATE_NEW
                ).count(),
            },
            {
                "name": "total_candidatures_en_étude",
                "type": "integer",
                "comment": "Nombre de candidatures en état étude dont la structure est destinataire",
                # FIXME ugly af
                "lambda": lambda o: JobApplication.objects.filter(
                    to_siae_id=o.id, state=JobApplicationWorkflow.STATE_PROCESSING
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
        ]
        table_columns += get_address_columns(comment_suffix=" de cette organisation")
        table_columns += [
            {
                "name": "date_inscription",
                "type": "date",
                "comment": "Date inscription du premier compte prescripteur",
                "lambda": get_org_first_join_date,
            },
            {
                "name": "total_membres",
                "type": "integer",
                "comment": "Nombre de comptes prescripteurs rattachés à cette organisation",
                "lambda": get_org_members_count,
            },
            {
                "name": "total_candidatures",
                "type": "integer",
                "comment": "Nombre de candidatures émises par cette organisation",
                "lambda": get_org_job_applications_count,
            },
            {
                "name": "total_embauches",
                "type": "integer",
                "comment": "Nombre de candidatures en état accepté émises par cette organisation",
                "lambda": get_org_accepted_job_applications_count,
            },
            {
                "name": "temps_écoulé_depuis_dernière_candidature",
                "type": "interval",
                "comment": "Temps écoulé depuis la dernière création de candidature",
                "lambda": get_timedelta_since_org_last_job_application,
            },
        ]

        # FIXME select_related for better perf
        objects = [ORG_OF_PRESCRIBERS_WITHOUT_ORG] + list(PrescriberOrganization.objects.all())

        self.populate_table(table_name=table_name, table_columns=table_columns, objects=objects)

    def populate_job_applications(self):
        table_name = "candidatures"

        table_columns = [
            {
                "name": "id_anonymisé",
                "type": "varchar",
                "comment": "ID anonymisé de la candidature",
                "lambda": lambda o: anonymize(o.id, salt="job_application.id"),
            },
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
                "comment": (
                    "Type de la candidature (auto-prescription, candidature autonome, candidature via prescripteur)"
                ),
                "lambda": lambda o: get_choice(choices=SENDER_KIND_CHOICES, key=o.sender_kind),
            },
            {
                "name": "sous_type",
                "type": "varchar",
                "comment": (
                    "Sous-type de la candidature (auto-prescription par EI, ACI..."
                    " candidature autonome, candidature via prescripteur PE, ML...)"
                ),
                "lambda": get_job_application_sub_type,
            },
            {
                "name": "délai_prise_en_compte",
                "type": "interval",
                "comment": (
                    "Temps écoulé rétroactivement de état nouveau à état étude"
                    " si la candidature est passée par ces états"
                ),
                "lambda": get_ja_time_spent_from_new_to_processing,
            },
            {
                "name": "délai_de_réponse",
                "type": "interval",
                "comment": (
                    "Temps écoulé rétroactivement de état nouveau à état accepté"
                    " ou refusé si la candidature est passée par ces états"
                ),
                "lambda": get_ja_time_spent_from_new_to_accepted_or_refused,
            },
            {
                "name": "motif_de_refus",
                "type": "varchar",
                "comment": "Motif de refus de la candidature",
                "lambda": lambda o: get_choice(choices=JobApplication.REFUSAL_REASON_CHOICES, key=o.refusal_reason),
            },
            {
                "name": "id_candidat_anonymisé",
                "type": "varchar",
                "comment": "ID anonymisé du candidat",
                "lambda": lambda o: anonymize(o.job_seeker_id, salt="job_seeker.id"),
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
        ] + get_department_and_region_columns(
            name_suffix="_structure",
            comment_suffix=" de la structure destinaire de la candidature",
            custom_lambda=lambda o: o.to_siae,
        )

        # FIXME select_related for better perf
        objects = JobApplication.objects.all()

        self.populate_table(table_name=table_name, table_columns=table_columns, objects=objects)

    def populate_job_seekers(self):
        table_name = "candidats"

        table_columns = [
            {
                "name": "id_anonymisé",
                "type": "varchar",
                "comment": "ID anonymisé du candidat",
                "lambda": lambda o: anonymize(o.id, salt="job_seeker.id"),
            },
            {
                "name": "age",
                "type": "integer",
                "comment": "Age du candidat en années",
                "lambda": get_user_age_in_years,
            },
            {
                "name": "date_inscription",
                "type": "date",
                "comment": "Date inscription du candidat",
                "lambda": lambda o: o.date_joined,
            },
            {
                "name": "pe_connect",
                "type": "boolean",
                "comment": "Le candidat utilise PE Connect",
                "lambda": lambda o: o.is_peamu,
            },
            {
                "name": "dernière_connexion",
                "type": "date",
                "comment": "Date de dernière connexion au service du candidat",
                "lambda": lambda o: o.last_login,
            },
        ]
        table_columns += get_department_and_region_columns(comment_suffix=" du candidat")
        table_columns += [
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
            {
                "name": "date_diagnostic",
                "type": "date",
                "comment": "Date du dernier diagnostic",
                "lambda": get_latest_diagnosis_creation_date,
            },
            {
                "name": "type_auteur_diagnostic",
                "type": "varchar",
                "comment": "Type auteur du dernier diagnostic",
                "lambda": get_latest_diagnosis_author_kind,
            },
            {
                "name": "sous_type_auteur_diagnostic",
                "type": "varchar",
                "comment": "Sous type auteur du dernier diagnostic",
                "lambda": get_latest_diagnosis_author_sub_kind,
            },
            {
                "name": "total_critères_niveau_1",
                "type": "integer",
                "comment": "Total critères de niveau 1 du dernier diagnostic",
                "lambda": get_latest_diagnosis_level1_criteria,
            },
            {
                "name": "total_critères_niveau_2",
                "type": "integer",
                "comment": "Total critères de niveau 2 du dernier diagnostic",
                "lambda": get_latest_diagnosis_level2_criteria,
            },
        ]
        for criteria in AdministrativeCriteria.objects.order_by("id").all():
            column_comment = (
                criteria.name.replace("'", " ")
                .replace("12-24", "12 à 24")
                .replace("+", "plus de ")
                .replace("-", "moins de ")
                .strip()
            )
            # Deduplicate consecutive spaces.
            column_comment = " ".join(column_comment.split())
            column_name = column_comment.replace("(", "").replace(")", "").replace(" ", "_").lower()
            table_columns += [
                {
                    "name": f"critère_n{criteria.level}_{column_name}",
                    "type": "boolean",
                    "comment": f"Critère {column_comment} (niveau {criteria.level})",
                    "lambda": partial(get_latest_diagnosis_criteria, criteria_id=criteria.id),
                }
            ]

        # FIXME select_related for better perf
        objects = (
            get_user_model()
            .objects.filter(is_job_seeker=True)
            .prefetch_related(
                "job_applications", "eligibility_diagnoses", "eligibility_diagnoses__administrative_criteria"
            )
            .all()
        )

        self.populate_table(table_name=table_name, table_columns=table_columns, objects=objects)

    def populate_metabase(self):
        with MetabaseDatabaseCursor() as cur:
            self.cur = cur
            self.populate_job_applications()
            self.populate_job_seekers()
            self.populate_siaes()
            self.populate_organisations()

    def handle(self, **options):
        self.set_logger(options.get("verbosity"))
        self.populate_metabase()
        self.log("-" * 80)
        self.log("Done.")
