from django.urls import path

from simulator import views

app_name = "simulator"

urlpatterns = [
    path("eshop/orders", views.search_orders, name="search_orders"),
    path("eshop/orders/<str:order_id>", views.get_order, name="get_order"),
    path("payments/<str:order_id>", views.get_payment, name="get_payment"),
    path("wms/fulfillments/<str:order_id>", views.get_fulfillment, name="get_fulfillment"),
    path("integration/sync-logs/<str:order_id>", views.get_sync_logs, name="get_sync_logs"),
    path("integration/retry-sync/<str:order_id>", views.retry_sync, name="retry_sync"),
    path("helpdesk/tickets", views.create_ticket, name="create_ticket"),
]
