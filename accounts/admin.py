from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Role


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'is_active')
    filter_horizontal = ('permissions',)
    search_fields = ('name', 'code')

    def get_readonly_fields(self, request, obj=None):
        return ('code',) if obj else ()

@admin.register(User)
class ERPUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (('ERP', {'fields': ('assigned_role', 'department')}),)
    add_fieldsets = UserAdmin.add_fieldsets + (('ERP', {'fields': ('assigned_role', 'department')}),)
    list_display = UserAdmin.list_display + ('assigned_role',)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if 'assigned_role' in form.base_fields:
            form.base_fields['assigned_role'].queryset = Role.objects.filter(is_active=True)
            form.base_fields['assigned_role'].required = True
        return form
