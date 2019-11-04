from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import ugettext as _
from django.views.decorators.http import require_http_methods

from itou.job_applications.models import JobApplication
from itou.www.apply.forms import AnswerForm, RefusalForm


@login_required
def details_for_siae(
    request, job_application_id, template_name="apply/process_details.html"
):
    """
    Detail of an application for an SIAE with the ability to give an answer.
    """

    queryset = (
        JobApplication.objects.siae_member_required(request.user)
        .select_related(
            "job_seeker",
            "sender",
            "sender_siae",
            "sender_prescriber_organization",
            "to_siae",
        )
        .prefetch_related("jobs")
    )
    job_application = get_object_or_404(queryset, id=job_application_id)

    transition_logs = (
        job_application.logs.select_related("user").all().order_by("timestamp")
    )

    context = {"job_application": job_application, "transition_logs": transition_logs}
    return render(request, template_name, context)


@login_required
@require_http_methods(["POST"])
def process(request, job_application_id):
    """
    Trigger the `process` transition.
    """

    queryset = JobApplication.objects.siae_member_required(request.user)
    job_application = get_object_or_404(queryset, id=job_application_id)

    job_application.process(user=request.user)

    next_url = reverse(
        "apply:details_for_siae", kwargs={"job_application_id": job_application.id}
    )
    return HttpResponseRedirect(next_url)


@login_required
def refuse(request, job_application_id, template_name="apply/process_refuse.html"):
    """
    Trigger the `refuse` transition.
    """

    queryset = JobApplication.objects.siae_member_required(request.user)
    job_application = get_object_or_404(queryset, id=job_application_id)

    form = RefusalForm(data=request.POST or None)

    if request.method == "POST" and form.is_valid():

        job_application.refusal_reason = form.cleaned_data["refusal_reason"]
        job_application.answer = form.cleaned_data["answer"]
        job_application.save()

        job_application.refuse(user=request.user)

        messages.success(request, _("Modification effectuée."))

        next_url = reverse(
            "apply:details_for_siae", kwargs={"job_application_id": job_application.id}
        )
        return HttpResponseRedirect(next_url)

    context = {"job_application": job_application, "form": form}
    return render(request, template_name, context)


@login_required
def postpone(request, job_application_id, template_name="apply/process_postpone.html"):
    """
    Trigger the `postpone` transition.
    """

    queryset = JobApplication.objects.siae_member_required(request.user)
    job_application = get_object_or_404(queryset, id=job_application_id)

    form = AnswerForm(data=request.POST or None)

    if request.method == "POST" and form.is_valid():

        job_application.answer = form.cleaned_data["answer"]
        job_application.save()

        job_application.postpone(user=request.user)

        messages.success(request, _("Modification effectuée."))

        next_url = reverse(
            "apply:details_for_siae", kwargs={"job_application_id": job_application.id}
        )
        return HttpResponseRedirect(next_url)

    context = {"job_application": job_application, "form": form}
    return render(request, template_name, context)


@login_required
def accept(request, job_application_id, template_name="apply/process_accept.html"):
    """
    Trigger the `accept` transition.
    """

    queryset = JobApplication.objects.siae_member_required(request.user)
    job_application = get_object_or_404(queryset, id=job_application_id)

    form = AnswerForm(data=request.POST or None)

    if request.method == "POST" and form.is_valid():

        job_application.answer = form.cleaned_data["answer"]
        job_application.save()

        job_application.accept(user=request.user)

        messages.success(request, _("Modification effectuée."))

        next_url = reverse(
            "apply:details_for_siae", kwargs={"job_application_id": job_application.id}
        )
        return HttpResponseRedirect(next_url)

    context = {"job_application": job_application, "form": form}
    return render(request, template_name, context)