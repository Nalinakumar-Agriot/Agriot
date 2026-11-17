from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.settings import api_settings


class MobileJWTAuthentication(JWTAuthentication):
    def get_raw_token(self, header):
        # Default behavior splits by space and expects ['Bearer', '<token>']
        parts = header.split()

        # If the user only sent the token (1 part), just return it directly!
        if len(parts) == 1:
            return parts[0]

        # Otherwise, fall back to default behavior (e.g. if they sent 'Bearer <token>')
        return super().get_raw_token(header)

    def get_user(self, validated_token):
        """Allow an active manager session even when the shared Django User
        was deactivated along with its farmer profile.

        One mobile number owns both the farmer profile and the manager
        (FarmerStaff) account. Deactivating the farmer side can leave
        ``user.is_active=False``; SimpleJWT would then reject every request.
        A token carrying ``profile_type='manager'`` stays valid as long as
        the staff record itself is an active manager.
        """
        try:
            return super().get_user(validated_token)
        except AuthenticationFailed:
            if not self._is_active_manager_token(validated_token):
                raise
            from django.contrib.auth import get_user_model

            user_id = validated_token.get(api_settings.USER_ID_CLAIM)
            User = get_user_model()
            try:
                return User.objects.get(pk=user_id)
            except User.DoesNotExist:
                raise AuthenticationFailed('User not found', code='user_not_found')

    @staticmethod
    def _is_active_manager_token(validated_token) -> bool:
        if validated_token.get('profile_type') != 'manager':
            return False
        user_id = validated_token.get(api_settings.USER_ID_CLAIM)
        if user_id is None:
            return False
        from apps.farmer.models import FarmerStaff

        staff = FarmerStaff.objects.filter(user_id=user_id).first()
        if staff is None or not staff.is_role_manager:
            return False
        return str(staff.status or '').strip().lower() not in {
            '1', 'inactive', 'suspended', 'suspended locked', 'locked',
        }
