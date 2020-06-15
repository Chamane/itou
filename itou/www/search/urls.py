from django.urls import path

from itou.www.search import views


# https://docs.djangoproject.com/en/dev/topics/http/urls/#url-namespaces-and-included-urlconfs
app_name = "search"

urlpatterns = [
    path("employers", views.search_siaes, name="siaes"),
    path("prescribers", views.search_prescribers, name="prescribers"),
]
