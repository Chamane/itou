import logging
import uuid

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import models
from django.shortcuts import reverse
from django.utils import timezone
from django.utils.http import urlsafe_base64_decode
from django.utils.translation import gettext_lazy as _

from itou.utils.emails import get_email_message
from itou.utils.urls import get_absolute_url


logger = logging.getLogger(__name__)


class InvitationManager(models.Manager):
    def get_from_encoded_pk(self, encoded_pk):
        pk = int(urlsafe_base64_decode(encoded_pk))
        return self.get(pk=pk)


class Invitation(models.Model):

    EXPIRATION_DAYS = 14

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(verbose_name=_("E-mail"))
    first_name = models.CharField(verbose_name=_("Prénom"), max_length=255)
    last_name = models.CharField(verbose_name=_("Nom"), max_length=255)
    sent = models.BooleanField(verbose_name=_("Envoyée"), default=False)
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Parrain ou marraine"),
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    accepted = models.BooleanField(verbose_name=_("Acceptée"), default=False)
    accepted_at = models.DateTimeField(verbose_name=_("Date d'acceptation"), blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(verbose_name=_("Date de création"), default=timezone.now, db_index=True)
    sent_at = models.DateTimeField(verbose_name=_("Date d'envoi"), blank=True, null=True, db_index=True)

    objects = InvitationManager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.email}"

    @property
    def acceptance_link(self):
        acceptance_path = reverse("invitations_views:accept", kwargs={"invitation_id": self.id})
        return get_absolute_url(acceptance_path)

    @property
    def expiration_date(self):
        return self.sent_at + relativedelta(days=self.EXPIRATION_DAYS)

    @property
    def has_expired(self):
        return self.expiration_date <= timezone.now()

    @property
    def can_be_accepted(self):
        return not self.accepted and not self.has_expired and self.sent

    def extend_expiration_date(self):
        self.sent_at = timezone.now()
        self.save()

    def accept(self):
        self.accepted = True
        self.accepted_at = timezone.now()
        self.save()
        self.accepted_notif_sender()

    def send(self):
        self.sent = True
        self.sent_at = timezone.now()
        self.send_invitation()
        self.save()

    def accepted_notif_sender(self):
        self.email_accepted_notif_sender.send()

    def send_invitation(self):
        self.email_invitation.send()

    # Emails
    @property
    def email_accepted_notif_sender(self):
        to = [self.sender.email]
        context = {"first_name": self.first_name, "last_name": self.last_name, "email": self.email}
        subject = "invitations_views/email/accepted_notif_sender_subject.txt"
        body = "invitations_views/email/accepted_notif_sender_body.txt"
        return get_email_message(to, context, subject, body)

    @property
    def email_invitation(self):
        to = [self.email]
        context = {
            "sender": self.sender,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "email": self.email,
            "acceptance_link": self.acceptance_link,
            "expiration_date": self.expiration_date,
        }
        subject = "invitations_views/email/invitation_subject.txt"
        body = "invitations_views/email/invitation_body.txt"
        return get_email_message(to, context, subject, body)
