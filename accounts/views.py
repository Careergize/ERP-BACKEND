from rest_framework.generics import RetrieveAPIView
from rest_framework.serializers import ModelSerializer, SerializerMethodField
from rest_framework.throttling import AnonRateThrottle
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from .models import User


class UserSerializer(ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'name', 'role', 'department')

    name = SerializerMethodField()
    role = SerializerMethodField()

    def get_role(self, user):
        return user.effective_role

    def get_name(self, user):
        return user.get_full_name() or user.username


class LoginSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        data['user'] = UserSerializer(self.user).data
        return data


class LoginThrottle(AnonRateThrottle):
    scope = 'login'


class LoginView(TokenObtainPairView):
    serializer_class = LoginSerializer
    throttle_classes = [LoginThrottle]


class MeView(RetrieveAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user
