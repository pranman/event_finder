from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    path("", views.home, name="home"),
    path("<slug:city_slug>/events/", views.city_list, name="city_list"),
    path("<slug:city_slug>/events/<uuid:public_id>/", views.event_detail, name="event_detail"),
]
