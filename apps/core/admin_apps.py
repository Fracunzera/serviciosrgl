from django.contrib.admin.apps import AdminConfig


class GuiaAdminConfig(AdminConfig):
    default_site = "apps.core.admin_site.GuiaAdminSite"
