from django.contrib.auth.backends import ModelBackend


class RolePermissionBackend(ModelBackend):
    def get_group_permissions(self, user_obj, obj=None):
        permissions = super().get_group_permissions(user_obj, obj)
        if obj is not None or not user_obj.is_active or user_obj.is_anonymous:
            return permissions
        if user_obj.assigned_role_id and user_obj.assigned_role.is_active:
            role_permissions = user_obj.assigned_role.permissions.values_list('content_type__app_label', 'codename')
            permissions = permissions | {f'{app}.{code}' for app, code in role_permissions}
        return permissions
