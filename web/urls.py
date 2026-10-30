from django.urls import path

from web import views

app_name = "web"

urlpatterns = [
    path("", views.index, name="index"),
    path("zakaznik/", views.customer, name="customer"),
    path("administrace/", views.shop_admin, name="shop_admin"),
    path("administrace/simulace/", views.simulation, name="simulation"),
    path("administrace/pozadavky/<str:item_id>/", views.decide_approval, name="decide_approval"),
    path("api/chat/start/", views.chat_start, name="chat_start"),
    path("api/chat/<str:conversation_id>/", views.chat_state, name="chat_state"),
    path("api/chat/<str:conversation_id>/message/", views.chat_message, name="chat_message"),
    path("api/chat/<str:conversation_id>/operator/", views.operator_message, name="operator_message"),
    path("api/operator/chat/<str:conversation_id>/", views.operator_chat_state, name="operator_chat_state"),
    path("administrace/chat/<str:conversation_id>/", views.operator_chat, name="operator_chat"),
    path("api/agent/runs/", views.agent_run_start, name="agent_run_start"),
    path("api/agent/runs/<str:run_id>/", views.agent_run_state, name="agent_run_state"),
    path("api/models/", views.models_api, name="models_api"),
    path("api/attention/", views.attention_api, name="attention_api"),
    path("trace/", views.trace_log, name="trace"),
]
