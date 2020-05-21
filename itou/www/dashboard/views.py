from allauth.account.views import PasswordChangeView
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from itou.job_applications.models import JobApplicationWorkflow
from itou.prescribers.models import PrescriberOrganization
from itou.siaes.models import Siae
from itou.utils.perms.prescriber import get_current_org_or_404
from itou.utils.perms.siae import get_current_siae_or_404
from itou.utils.urls import get_safe_url
from itou.www.dashboard.forms import EditUserInfoForm


@login_required
def dashboard(request, template_name="dashboard/dashboard.html"):
    job_applications_counter = 0
    prescriber_authorization_status_not_set = None
    prescriber_is_orienter = False

    if request.user.is_siae_staff:
        siae = get_current_siae_or_404(request)
        job_applications_counter = siae.job_applications_received.filter(
            state=JobApplicationWorkflow.STATE_NEW
        ).count()

    # See template for display message while authorized organization is being validated (prescriber path)

    if request.user.is_prescriber_with_org:
        prescriber_organization = get_current_org_or_404(request)
        prescriber_authorization_status_not_set = (
            prescriber_organization.authorization_status == PrescriberOrganization.AuthorizationStatus.NOT_SET
        )
        # This is to hide the "secret code", except for orienter orgs
        prescriber_is_orienter = (
            prescriber_organization.authorization_status == PrescriberOrganization.AuthorizationStatus.NOT_REQUIRED
        )

    context = {
        "job_applications_counter": job_applications_counter,
        "prescriber_authorization_status_not_set": prescriber_authorization_status_not_set,
        "prescriber_is_orienter": prescriber_is_orienter,
    }

    return render(request, template_name, context)


class ItouPasswordChangeView(PasswordChangeView):
    """
    https://github.com/pennersr/django-allauth/issues/468
    """

    success_url = reverse_lazy("dashboard:index")


password_change = login_required(ItouPasswordChangeView.as_view())


@login_required
def edit_user_info(request, template_name="dashboard/edit_user_info.html"):
    """
    Edit a user.
    """

    dashboard_url = reverse_lazy("dashboard:index")
    prev_url = get_safe_url(request, "prev_url", fallback_url=dashboard_url)
    form = EditUserInfoForm(instance=request.user, data=request.POST or None)

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Mise à jour de vos informations effectuée !"))
        success_url = get_safe_url(request, "success_url", fallback_url=dashboard_url)
        return HttpResponseRedirect(success_url)

    context = {"form": form, "prev_url": prev_url}
    return render(request, template_name, context)


@login_required
@require_POST
def switch_siae(request):
    """
    Switch to the dashboard of another SIAE of the same SIREN.
    """

    dashboard_url = reverse_lazy("dashboard:index")

    pk = request.POST["siae_id"]
    queryset = Siae.objects.member_required(request.user)
    siae = get_object_or_404(queryset, pk=pk)
    request.session[settings.ITOU_SESSION_CURRENT_SIAE_KEY] = siae.pk
    messages.success(request, _(f"Vous travaillez sur {siae.display_name}"))
    return HttpResponseRedirect(dashboard_url)
