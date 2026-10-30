from django.urls import include, path

urlpatterns = [
    path("mock/", include("simulator.urls")),
    path("", include("web.urls")),
]
