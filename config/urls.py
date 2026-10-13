from django.contrib import admin
from django.urls import include, path
from apps.superadmin.views import login_view as superadmin_login

from django.conf import settings
from django.conf.urls.static import static

from rest_framework import permissions
from drf_yasg.views import get_schema_view
from drf_yasg import openapi

api_info = openapi.Info(
    title="AgriOT API",
    default_version="v1",
    description="API documentation for AgriOT",
    terms_of_service="https://www.google.com/policies/terms/",
    contact=openapi.Contact(email="contact@agriot.local"),
    license=openapi.License(name="BSD License"),
)

schema_view = get_schema_view(
    api_info,
    public=True,
    permission_classes=(permissions.AllowAny,),
)

urlpatterns = [
    path('', superadmin_login, name='home'),  # ✅ root = superadmin login
    path('admin-panel/', include('apps.superadmin.urls')),
    path('farmer/', include('apps.farmer.urls')),
    path('api/farmer/', include('apps.farmer.api_urls')),
    
    # Notifications API
    path('api/notifications/mark-read/', include([
        path('', __import__('auth.views', fromlist=['views']).mark_all_notifications_read, name='mark_notifications_read')
    ])),
    path('api/notifications/clear/', include([
        path('', __import__('auth.views', fromlist=['views']).clear_all_notifications, name='clear_notifications')
    ])),

    # Swagger URLs
    path('swagger<format>/', schema_view.without_ui(cache_timeout=0), name='schema-json'),
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)