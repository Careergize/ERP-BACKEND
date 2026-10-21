from django.contrib.auth.password_validation import validate_password
from rest_framework import permissions, serializers, viewsets
from .models import Role, User


class IsSuperuser(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user.is_authenticated and request.user.is_superuser)


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ('id', 'code', 'name', 'is_active')


class ManagedUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, trim_whitespace=False)
    assigned_role = serializers.PrimaryKeyRelatedField(queryset=Role.objects.filter(is_active=True))
    role_name = serializers.CharField(source='assigned_role.name', read_only=True)

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'first_name', 'last_name', 'department', 'assigned_role', 'role_name', 'is_active', 'password')

    def validate(self, attrs):
        password = attrs.get('password')
        if not self.instance and not password:
            raise serializers.ValidationError({'password': 'Enter an initial password.'})
        if password:
            validate_password(password, self.instance or User(username=attrs.get('username', ''), email=attrs.get('email', '')))
        if self.instance == self.context['request'].user and attrs.get('is_active') is False:
            raise serializers.ValidationError({'is_active': 'You cannot deactivate your own signed-in account.'})
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        instance = super().update(instance, validated_data)
        if password:
            instance.set_password(password)
            instance.save(update_fields=['password'])
        return instance


class RoleViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Role.objects.filter(is_active=True)
    serializer_class = RoleSerializer
    permission_classes = [IsSuperuser]


class ManagedUserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.select_related('assigned_role').order_by('username')
    serializer_class = ManagedUserSerializer
    permission_classes = [IsSuperuser]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
