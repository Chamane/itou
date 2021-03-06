from unittest import mock

from django.conf import settings
from django.core import mail
from django.test import RequestFactory, TestCase

from itou.siaes.factories import (
    SiaeFactory,
    SiaeWith2MembershipsFactory,
    SiaeWith4MembershipsFactory,
    SiaeWithMembershipAndJobsFactory,
    SiaeWithMembershipFactory,
)
from itou.siaes.models import Siae
from itou.users.factories import SiaeStaffFactory


class FactoriesTest(TestCase):
    def test_siae_with_membership_factory(self):
        siae = SiaeWithMembershipFactory()
        self.assertEqual(siae.members.count(), 1)
        user = siae.members.get()
        self.assertTrue(siae.has_admin(user))

    def test_siae_with_membership_and_jobs_factory(self):
        siae = SiaeWithMembershipAndJobsFactory(romes=("N1101", "N1105"))
        self.assertEqual(siae.jobs.count(), 4)

    def test_siae_with_2_memberships_factory(self):
        siae = SiaeWith2MembershipsFactory()
        self.assertEqual(siae.members.count(), 2)
        self.assertEqual(siae.active_members.count(), 2)
        self.assertEqual(siae.active_admin_members.count(), 1)
        admin_user = siae.active_admin_members.get()
        self.assertTrue(siae.has_admin(admin_user))
        all_users = list(siae.members.all())
        self.assertEqual(len(all_users), 2)
        all_users.remove(admin_user)
        self.assertEqual(len(all_users), 1)
        regular_user = all_users[0]
        self.assertFalse(siae.has_admin(regular_user))

    def test_siae_with_4_memberships_factory(self):
        siae = SiaeWith4MembershipsFactory()
        self.assertEqual(siae.members.count(), 4)
        self.assertEqual(siae.active_members.count(), 2)
        self.assertEqual(siae.active_admin_members.count(), 1)


class ModelTest(TestCase):
    def test_is_subject_to_eligibility_rules(self):
        siae = SiaeFactory(kind=Siae.KIND_GEIQ)
        self.assertFalse(siae.is_subject_to_eligibility_rules)

        siae = SiaeFactory(kind=Siae.KIND_EI)
        self.assertTrue(siae.is_subject_to_eligibility_rules)

    def test_has_members(self):
        siae1 = SiaeFactory()
        siae2 = SiaeWithMembershipFactory()

        self.assertFalse(siae1.has_members)
        self.assertTrue(siae2.has_members)

    def test_has_member(self):
        siae1 = SiaeWithMembershipFactory()
        siae2 = SiaeWithMembershipFactory()

        user1 = siae1.members.get()
        user2 = siae2.members.get()

        self.assertTrue(siae1.has_member(user1))
        self.assertFalse(siae1.has_member(user2))

        self.assertTrue(siae2.has_member(user2))
        self.assertFalse(siae2.has_member(user1))

    def test_active_members(self):
        siae = SiaeWith2MembershipsFactory(membership2__user__is_active=False)
        self.assertEqual(siae.members.count(), 2)
        self.assertEqual(siae.active_members.count(), 1)

    def test_active_admin_members(self):
        """
        Test that if a user is admin of siae1 and regular user
        of siae2 it does not get considered as admin of siae2.
        """
        siae1 = SiaeWith4MembershipsFactory()
        siae1_admin_user = siae1.active_admin_members.get()
        siae2 = SiaeWith4MembershipsFactory(membership2__user=siae1_admin_user)

        self.assertEqual(siae1.members.count(), 4)
        self.assertEqual(siae1.active_members.count(), 2)
        self.assertEqual(siae1.active_admin_members.count(), 1)

        self.assertEqual(siae2.members.count(), 4)
        self.assertEqual(siae2.active_members.count(), 2)
        self.assertEqual(siae2.active_admin_members.count(), 1)

    def test_has_admin(self):
        siae1 = SiaeWith2MembershipsFactory()
        siae1_admin_user = siae1.active_admin_members.get()
        siae1_regular_user = siae1.active_members.exclude(pk=siae1_admin_user.pk).get()
        siae2 = SiaeWith4MembershipsFactory(membership2__user=siae1_admin_user)

        self.assertTrue(siae1.has_member(siae1_admin_user))
        self.assertTrue(siae1.has_admin(siae1_admin_user))

        self.assertTrue(siae1.has_member(siae1_regular_user))
        self.assertFalse(siae1.has_admin(siae1_regular_user))

        self.assertTrue(siae2.has_member(siae1_admin_user))
        self.assertFalse(siae2.has_admin(siae1_admin_user))

    def test_new_signup_activation_email_to_official_contact(self):

        siae = SiaeWithMembershipFactory()
        token = siae.get_token()
        with mock.patch("itou.utils.tokens.SiaeSignupTokenGenerator.make_token", return_value=token):

            factory = RequestFactory()
            request = factory.get("/")

            message = siae.new_signup_activation_email_to_official_contact(request)
            message.send()

            self.assertEqual(len(mail.outbox), 1)
            email = mail.outbox[0]
            self.assertIn("Un nouvel utilisateur souhaite rejoindre votre structure", email.subject)
            self.assertIn("veuillez ouvrir le lien suivant pour continuer votre inscription", email.body)
            self.assertIn(siae.signup_magic_link, email.body)
            self.assertIn(siae.display_name, email.body)
            self.assertIn(siae.siret, email.body)
            self.assertIn(siae.kind, email.body)
            self.assertIn(siae.auth_email, email.body)
            self.assertNotIn(siae.email, email.body)
            self.assertEqual(email.from_email, settings.DEFAULT_FROM_EMAIL)
            self.assertEqual(len(email.to), 1)
            self.assertEqual(email.to[0], siae.auth_email)

    def test_new_signup_warning_email_to_existing_members(self):
        siae = SiaeWithMembershipFactory()
        user = siae.members.first()

        new_user = SiaeStaffFactory()

        message = siae.new_signup_warning_email_to_existing_members(new_user)
        message.send()

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn("Un nouvel utilisateur vient de rejoindre votre structure", email.subject)
        self.assertIn("Si vous ne connaissez pas cette personne veuillez nous contacter", email.body)
        self.assertIn(new_user.first_name, email.body)
        self.assertIn(new_user.last_name, email.body)
        self.assertIn(new_user.email, email.body)
        self.assertIn(siae.display_name, email.body)
        self.assertIn(siae.siret, email.body)
        self.assertIn(siae.kind, email.body)
        self.assertEqual(email.from_email, settings.DEFAULT_FROM_EMAIL)
        self.assertEqual(len(email.to), 1)
        self.assertEqual(email.to[0], user.email)
