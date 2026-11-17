import base64
import hmac
import json
import random
import secrets
from datetime import datetime, timedelta
from django.utils import timezone
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.pagination import PageNumberPagination
from rest_framework.decorators import action
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from apps.farmer.models import CropSplit
from apps.farmer.models import FarmerCustomer, FarmerVendor, FarmerOutstanding
from apps.farmer.serializers import (
    FarmerCustomerSerializer, FarmerVendorSerializer,
    FarmerOutstandingSerializer, CropSellSerializer
)
class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

from apps.farmer.serializers import (
    FarmerRegistrationSerializer,
    FarmerRegistrationVerifyOTPSerializer,
    FarmerSendOTPSerializer,
    FarmerVerifyOTPSerializer,
)
from apps.superadmin.views import send_otp_sms
from auth.models import Profile
from apps.farmer.models import FarmerStaff 
from contextvars import ContextVar

# Active profile type ('farmer' / 'manager') for the request currently being
# processed, read from the JWT's 'profile_type' claim. It is populated centrally
# by the APIView.initial hook below and consumed by get_farmer_profile(),
# ManagerPermissionsAPIView and AvailableProfilesAPIView so a switched-profile
# token resolves to the correct farmer/manager profile.
ACTIVE_PROFILE_TYPE = ContextVar('ACTIVE_PROFILE_TYPE', default=None)

_original_api_view_initial = APIView.initial


def _initial_populating_active_profile(self, request, *args, **kwargs):
    """Expose the token's profile_type claim for the whole request.

    Accessing ``request.auth`` triggers DRF authentication, so the claim is
    resolved before permission checks and the view body run.
    """
    try:
        token = getattr(request, 'auth', None)
    except Exception:
        token = None
    payload = getattr(token, 'payload', None)
    ACTIVE_PROFILE_TYPE.set(
        payload.get('profile_type') if isinstance(payload, dict) else None
    )
    return _original_api_view_initial(self, request, *args, **kwargs)


if not getattr(APIView.initial, '_agriot_profile_hook', False):
    _initial_populating_active_profile._agriot_profile_hook = True
    APIView.initial = _initial_populating_active_profile

mobile_param = openapi.Parameter('mobile', in_=openapi.IN_FORM, description="10-digit mobile number", type=openapi.TYPE_STRING, required=True)
otp_param = openapi.Parameter('otp', in_=openapi.IN_FORM, description="6-digit OTP received via SMS", type=openapi.TYPE_STRING, required=True)


def _resolve_login_user(mobile):
    """
    Resolve a mobile-app login user.

    Active farmer accounts and active manager-access staff may log in. Regular
    employees and inactive/suspended accounts are rejected.
    Returns (user, error_response_tuple). Exactly one of the two is non-None.
    """
    from apps.farmer.models import FarmerStaff

    def is_inactive(value):
        return str(value or '').strip().lower() in {
            '1', 'inactive', 'suspended', 'suspended locked', 'locked',
        }

    staff = FarmerStaff.objects.filter(mobile_number=mobile).select_related('user', 'user__profile', 'farmer').first()
    if staff is not None:
        if is_inactive(staff.status):
            return None, ({"error": "Your account has been suspended/locked. Access is disabled."},
                          status.HTTP_403_FORBIDDEN)
        if not staff.is_role_manager:
            return None, ({"error": "Only staff with manager access can log in to the mobile app."},
                          status.HTTP_403_FORBIDDEN)
        user = staff.user
        if user is None:
            user = User.objects.filter(username=mobile).first()
            if user is None:
                user = User.objects.create(username=mobile)
                user.set_unusable_password()
                user.save()
            staff.user = user
            staff.save(update_fields=['user'])
        if not hasattr(user, 'profile') or user.profile is None:
            profile, _ = Profile.objects.get_or_create(user=user)
            user.profile = profile
        # Deliberately NOT checking user.is_active / user.profile.status
        # here: the Django User and its farmer Profile are shared with the
        # farmer side of this mobile number. Deactivating the farmer profile
        # must not lock out an active manager (staff) account — the gates for
        # this branch are staff.status + is_role_manager checked above.
        if user.profile.mobile_number != mobile:
            user.profile.mobile_number = mobile
            user.profile.save(update_fields=['mobile_number'])
        return user, None

    user = User.objects.filter(username=mobile).select_related('profile').first()
    if user is None:
        profile = Profile.objects.filter(
            mobile_number=mobile,
            role='farmer',
        ).select_related('user').first()
        if profile is not None:
            user = profile.user

    if user is None or not hasattr(user, 'profile') or user.profile is None \
            or getattr(user.profile, 'role', None) != 'farmer':
        return None, ({"error": "Farmer not found. Please register first."},
                      status.HTTP_401_UNAUTHORIZED)
    if not user.is_active or is_inactive(getattr(user.profile, 'status', '0')):
        return None, ({"error": "Your account has been suspended/locked. Access is disabled."},
                      status.HTTP_403_FORBIDDEN)
    return user, None


def _get_user_staff_assignment(user, manager_only=False):
    staff_assignments = FarmerStaff.objects.filter(user=user)
    if manager_only:
        staff_assignments = staff_assignments.filter(is_role_manager=True)
    return staff_assignments.select_related('farmer').order_by('id').first()


def _manager_for_request(user):
    if ACTIVE_PROFILE_TYPE.get() == 'farmer':
        return None
    return FarmerStaff.objects.filter(
        user=user,
        is_role_manager=True,
        status='0',
    ).select_related('farmer').order_by('id').first()


def get_farmer_profile(user):
    """
    Resolve the effective farmer Profile for the authenticated user.
    Mirrors website helper apps.farmer.views.get_farmer_profile:
    - Staff/Manager (user.staff_assignments) → owning farmer profile
    - Direct farmer → own profile
    When the token carries a 'profile_type' claim (profile switching), the
    active profile type wins: 'farmer' → own profile, 'manager' → staff.farmer.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    profile_type = ACTIVE_PROFILE_TYPE.get()
    if profile_type == 'farmer':
        profile = getattr(user, 'profile', None)
        if profile is not None and getattr(profile, 'role', None) == 'farmer':
            return profile
    elif profile_type == 'manager':
        staff = _get_user_staff_assignment(user, manager_only=True)
        if staff is not None:
            return staff.farmer
    staff = _get_user_staff_assignment(user)
    if staff is not None:
        return staff.farmer
    if hasattr(user, 'profile'):
        return user.profile
    return None


MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB (matches website upload limit)


def _decode_data_uri(value, prefix='upload'):
    """
    Decode a base64 payload into (bytes, filename).
    Accepts 'data:<mime>;base64,<data>' (mobile app) or raw base64.
    Returns None when the value is empty, malformed or larger than 10MB.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        if value.startswith('data:'):
            header, _, payload = value.partition(',')
            media_type = header[len('data:'):].split(';')[0] or 'image/png'
        else:
            payload = value
            media_type = 'image/png'
        raw = base64.b64decode(payload)
        if not raw or len(raw) > MAX_UPLOAD_SIZE:
            return None
        extension = {
            'image/jpeg': 'jpg',
            'image/jpg': 'jpg',
            'image/png': 'png',
            'image/webp': 'webp',
            'application/pdf': 'pdf',
        }.get(media_type.lower(), 'png')
        return raw, f'{prefix}.{extension}'
    except Exception:
        return None

class FarmerSendOTPAPIView(APIView):
    permission_classes = [AllowAny]
    parser_classes = [FormParser, MultiPartParser]

    @swagger_auto_schema(
        operation_summary="Send OTP for Farmer Login",
        operation_description="Generates and sends a 6-digit OTP via SMS to the provided mobile number.",
        manual_parameters=[mobile_param],
        responses={
            200: openapi.Response("OTP sent successfully"),
            400: openapi.Response("Invalid input"),
            500: openapi.Response("Failed to send SMS")
        },
        tags=['Mobile - 01. Authentication']
    )
    def post(self, request):
        serializer = FarmerSendOTPSerializer(data=request.data)
        if serializer.is_valid():
            mobile = serializer.validated_data['mobile']

            # Allow farmers AND staff/managers (dual lookup; mirrors website)
            user, error = _resolve_login_user(mobile)
            if error is not None:
                return Response(error[0], status=error[1])

            # Generate OTP
            otp = str(random.randint(100000, 999999))

            # Send SMS using existing Msg91 implementation
            success, message = send_otp_sms(mobile, otp)
            if not success:
                return Response({"error": f"Failed to send SMS: {message}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # Store OTP in Profile model (DB) instead of cache for reliability
            user.profile.otp = otp
            user.profile.otp_expiry_at = timezone.now() + timedelta(minutes=7)
            user.profile.save(update_fields=['otp', 'otp_expiry_at'])
            print(f"DEBUG: OTP for {mobile} is {otp}")  # For debugging; remove in production

            return Response({"message": "OTP sent successfully"}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class FarmerRegistrationAPIView(APIView):
    permission_classes = [AllowAny]
    parser_classes = [FormParser, MultiPartParser]

    @swagger_auto_schema(
        operation_summary="Register a Farmer",
        operation_description=(
            "Creates or updates an unverified farmer account and sends a registration "
            "OTP. Verify the OTP before selecting a subscription package. Accepts the "
            "full registration form: personal details, farmer category and location "
            "(state / district / taluk / village / pincode / address)."
        ),
        request_body=FarmerRegistrationSerializer,
        responses={
            200: openapi.Response("Registration OTP sent successfully"),
            400: openapi.Response("Invalid registration data"),
        },
        tags=["Mobile - 01. Authentication"],
    )
    def post(self, request):
        serializer = FarmerRegistrationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        from apps.superadmin.models import City, State, Taluk

        data = serializer.validated_data
        mobile = data["mobile"]
        email = data.get("email") or None

        state_obj = State.objects.filter(pk=data["state_id"]).first()
        district_obj = City.objects.filter(pk=data["district_id"]).first()
        taluk_raw = data.get("taluk") or ""
        taluk_name = taluk_raw
        if taluk_raw.isdigit():
            taluk_obj = Taluk.objects.filter(pk=int(taluk_raw)).first()
            if taluk_obj is not None:
                taluk_name = taluk_obj.name

        existing_user = User.objects.filter(username=mobile).select_related(
            "profile"
        ).first()
        if FarmerStaff.objects.filter(mobile_number=mobile).exists() or (
            existing_user is not None
            and (
                not hasattr(existing_user, "profile")
                or existing_user.profile.role != "farmer"
                or existing_user.profile.is_verified
            )
        ):
            return Response(
                {"error": "This mobile number is already registered. Please log in."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing_profile_id = (
            existing_user.profile.id
            if existing_user is not None and hasattr(existing_user, "profile")
            else None
        )
        if email and (
            User.objects.filter(email__iexact=email)
            .exclude(pk=existing_user.pk if existing_user else None)
            .exists()
            or Profile.objects.filter(email__iexact=email)
            .exclude(pk=existing_profile_id)
            .exists()
        ):
            return Response(
                {"error": "This email address is already registered."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                user = existing_user
                if user is None:
                    user = User(
                        username=mobile,
                        first_name=data["name"],
                        email=email or "",
                    )
                    user.set_unusable_password()
                    user.save()
                else:
                    user.first_name = data["name"]
                    user.email = email or ""
                    user.save(update_fields=["first_name", "email"])
                profile = user.profile
                profile.role = "farmer"
                profile.mobile_number = mobile
                profile.email = email
                if "gender" in data:
                    profile.gender = data["gender"] or None
                profile.farmer_type = data.get("farmer_type") or "individual"
                profile.company_name = data.get("company_name") or None
                profile.state = state_obj
                profile.district = district_obj
                profile.taluka = taluk_name or None
                profile.village = data.get("village") or None
                profile.pincode = data.get("pincode") or None
                profile.address = data.get("address") or None
                profile.otp = str(secrets.randbelow(900000) + 100000)
                profile.otp_expiry_at = timezone.now() + timedelta(minutes=7)
                profile.save()
        except IntegrityError:
            return Response(
                {"error": "An account with these details already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        success, message = send_otp_sms(mobile, profile.otp)
        if not success:
            profile.otp = None
            profile.otp_expiry_at = None
            profile.save(update_fields=["otp", "otp_expiry_at"])
            return Response(
                {"error": f"Failed to send SMS: {message}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            {
                "message": "OTP sent successfully.",
                "otp": profile.otp ,
                "mobile": mobile,
            },
            status=status.HTTP_200_OK,
        )


class FarmerRegistrationVerifyOTPAPIView(APIView):
    permission_classes = [AllowAny]
    parser_classes = [FormParser, MultiPartParser]

    @swagger_auto_schema(
        operation_summary="Verify Registration OTP",
        request_body=FarmerRegistrationVerifyOTPSerializer,
        responses={200: openapi.Response("OTP verified")},
        tags=["Mobile - 01. Authentication"],
    )
    def post(self, request):
        serializer = FarmerRegistrationVerifyOTPSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        mobile = serializer.validated_data["mobile"]
        otp = serializer.validated_data["otp"]
        user = User.objects.filter(username=mobile).select_related("profile").first()
        if (
            user is None
            or not hasattr(user, "profile")
            or user.profile.role != "farmer"
        ):
            return Response(
                {"error": "Registration not found. Please register again."},
                status=status.HTTP_404_NOT_FOUND,
            )

        profile = user.profile
        if not profile.otp or not profile.otp_expiry_at:
            return Response(
                {"error": "OTP expired or invalid. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if timezone.now() > profile.otp_expiry_at:
            profile.otp = None
            profile.otp_expiry_at = None
            profile.save(update_fields=["otp", "otp_expiry_at"])
            return Response(
                {"error": "OTP expired. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not hmac.compare_digest(str(profile.otp), otp):
            return Response(
                {"error": "Invalid verification code."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile.otp = None
        profile.otp_expiry_at = None
        profile.is_verified = True
        profile.save(update_fields=["otp", "otp_expiry_at", "is_verified"])

        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(user)
        refresh["farmer_id"] = profile.id
        refresh["profile_type"] = "farmer"
        return Response(
            {
                "token": str(refresh.access_token),
                "message": "Mobile number verified successfully.",
            },
            status=status.HTTP_200_OK,
        )


class FarmerVerifyOTPAPIView(APIView):
    permission_classes = [AllowAny]
    parser_classes = [FormParser, MultiPartParser]

    @swagger_auto_schema(
        operation_summary="Verify OTP for Farmer Login",
        operation_description="Verifies the OTP sent to the mobile number and logs the user in, returning an auth token.",
        manual_parameters=[mobile_param, otp_param],
        responses={
            200: openapi.Response("Login successful", openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'token': openapi.Schema(type=openapi.TYPE_STRING, description="JWT Auth token (decrypt to get farmer_id)"),
                    'message': openapi.Schema(type=openapi.TYPE_STRING, description="Success message")
                }
            )),
            400: openapi.Response("Invalid input or OTP"),
            403: openapi.Response("Access denied")
        },
        tags=['Mobile - 01. Authentication']
    )
    def post(self, request):
        serializer = FarmerVerifyOTPSerializer(data=request.data)
        if serializer.is_valid():
            mobile = serializer.validated_data['mobile']
            otp_entered = serializer.validated_data['otp']

            # Allow farmers AND staff/managers (dual lookup; mirrors website)
            user, error = _resolve_login_user(mobile)
            if error is not None:
                return Response(error[0], status=error[1])

            stored_otp = user.profile.otp
            otp_expiry = user.profile.otp_expiry_at

            if not stored_otp or not otp_expiry:
                return Response({"error": "OTP expired or invalid. Please request again."}, status=status.HTTP_400_BAD_REQUEST)

            if timezone.now() > otp_expiry:
                # Clear expired OTP
                user.profile.otp = None
                user.profile.otp_expiry_at = None
                user.profile.save(update_fields=['otp', 'otp_expiry_at'])
                return Response({"error": "OTP expired. Please request a new one."}, status=status.HTTP_400_BAD_REQUEST)

            if str(otp_entered) == str(stored_otp):
                # Valid OTP - clear it
                user.profile.otp = None
                user.profile.otp_expiry_at = None
                user.profile.save(update_fields=['otp', 'otp_expiry_at'])

                # Generate JWT with custom claim for farmer_id
                from rest_framework_simplejwt.tokens import RefreshToken
                refresh = RefreshToken.for_user(user)
                # For staff, point at owning farmer profile; for farmers, own profile
                staff_assignment = FarmerStaff.objects.filter(
                    user=user, mobile_number=mobile, is_role_manager=True,
                ).select_related('farmer').first()
                if staff_assignment is not None:
                    refresh['farmer_id'] = staff_assignment.farmer_id
                    refresh['profile_type'] = 'manager'
                else:
                    refresh['farmer_id'] = user.profile.id
                    refresh['profile_type'] = 'farmer'

                return Response({
                    "token": str(refresh.access_token),
                    "message": "Login successful"
                }, status=status.HTTP_200_OK)
            else:
                return Response({"error": "Invalid OTP. Please try again."}, status=status.HTTP_400_BAD_REQUEST)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@swagger_auto_schema(
    operation_summary="Get Logged-in Manager Permissions",
    operation_description="Returns is_manager flag, role, and permission list for the authenticated user (staff/manager or farmer).",
    responses={
        200: openapi.Response("Permissions payload", openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'is_manager': openapi.Schema(type=openapi.TYPE_INTEGER, description="1 if manager, else 0"),
                'role': openapi.Schema(type=openapi.TYPE_STRING),
                'permissions': openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Items(type=openapi.TYPE_STRING)),
                'status': openapi.Schema(type=openapi.TYPE_STRING),
            }
        )),
        401: openapi.Response("Unauthenticated"),
    },
    tags=['Mobile - 01. Authentication']
)
class ManagerPermissionsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        profile_type = ACTIVE_PROFILE_TYPE.get()
        staff = (
            _get_user_staff_assignment(user, manager_only=True)
            if profile_type != 'farmer'
            else None
        )
        if staff is not None and profile_type != 'farmer':
            perms = staff.permissions if isinstance(staff.permissions, list) else []
            return Response({
                "is_manager": 1 if staff.is_role_manager else 0,
                "role": staff.role or ("Manager" if staff.is_role_manager else "Employee"),
                "permissions": perms,
                "status": str(staff.status or "0"),
            }, status=status.HTTP_200_OK)
        profile = getattr(user, 'profile', None)
        return Response({
            "is_manager": 0,
            "role": getattr(profile, 'role', 'farmer') or 'farmer',
            "permissions": [],
            "status": str(getattr(profile, 'status', '0') or "0"),
        }, status=status.HTTP_200_OK)

from apps.farmer.serializers import (
    FarmerProfileDetailSerializer,
    ManagerProfileDetailSerializer,
)

class FarmerProfileAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [FormParser, MultiPartParser]

    @swagger_auto_schema(
        operation_summary='Get Farmer Profile Details',
        operation_description='Returns the full profile details of the currently authenticated farmer, including their land and crop counts. Requires JWT Bearer token.',
        responses={
            200: FarmerProfileDetailSerializer(),
            401: openapi.Response('Unauthorized')
        },
        tags=['Mobile - 02. Profile & Subscription']
    )
    def get(self, request):
        if ACTIVE_PROFILE_TYPE.get() == 'manager':
            staff = _get_user_staff_assignment(
                request.user, manager_only=True
            )
            if staff is None or not staff.is_role_manager:
                return Response(
                    {'error': 'Access denied: Manager profile not found.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            return Response(
                ManagerProfileDetailSerializer(staff).data,
                status=status.HTTP_200_OK,
            )
        profile = get_farmer_profile(request.user)
        if profile is None:
            return Response({'error': 'Access denied: Not a farmer profile.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = FarmerProfileDetailSerializer(profile)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        operation_summary='Update Farmer Profile',
        operation_description='Updates profile details like name, email, KYC numbers, location, etc. Accepts multipart/form-data or form data (JSON is not supported).',
        request_body=FarmerProfileDetailSerializer,
        responses={200: FarmerProfileDetailSerializer()},
        tags=['Mobile - 02. Profile & Subscription']
    )
    def put(self, request):
        if ACTIVE_PROFILE_TYPE.get() == 'manager':
            staff = _get_user_staff_assignment(
                request.user, manager_only=True
            )
            if staff is None or not staff.is_role_manager:
                return Response(
                    {'error': 'Access denied: Manager profile not found.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            data = request.data
            for field in ('name', 'mobile_number', 'gender', 'address'):
                if field in data:
                    setattr(staff, field, str(data.get(field) or '').strip())
            if 'date_of_birth' in data:
                value = str(data.get('date_of_birth') or '').strip()
                staff.date_of_birth = (
                    datetime.strptime(value, '%Y-%m-%d').date()
                    if value else None
                )
            staff.save()
            if staff.user is not None and 'email' in data:
                staff.user.email = str(data.get('email') or '').strip()
                staff.user.save(update_fields=['email'])
            return Response(
                ManagerProfileDetailSerializer(staff).data,
                status=status.HTTP_200_OK,
            )
        profile = get_farmer_profile(request.user)
        if profile is None:
            return Response({'error': 'Access denied.'}, status=status.HTTP_403_FORBIDDEN)

        data = request.data
        owner = profile.user

        def text_value(*keys, allow_empty=False):
            """First present, non-empty value among keys (empty allowed only when allow_empty)."""
            for key in keys:
                if key in data:
                    value = data.get(key)
                    if value is None:
                        continue
                    value = str(value).strip()
                    if value or allow_empty:
                        return value
            return None

        # --- Name / email / mobile (mirrors website update_profile) ---
        name = text_value('name', 'first_name', 'full_name', 'farmer_name')
        if name:
            owner.first_name = name
        last_name = text_value('last_name')
        if last_name:
            owner.last_name = last_name

        email = text_value('email')
        if email:
            if Profile.objects.filter(email__iexact=email).exclude(pk=profile.pk).exists():
                return Response({'error': 'This email is already used by another account.'},
                                status=status.HTTP_400_BAD_REQUEST)
            owner.email = email
            profile.email = email

        mobile_number = text_value('mobile_number', 'phone')
        if mobile_number:
            if Profile.objects.filter(mobile_number=mobile_number).exclude(pk=profile.pk).exists():
                return Response({'error': 'This mobile number is already used by another account.'},
                                status=status.HTTP_400_BAD_REQUEST)
            profile.mobile_number = mobile_number

        # --- Simple text fields: present key = update (empty value clears the field) ---
        simple_fields = {
            'farmer_type': ('farmer_type',),
            'company_name': ('company_name',),
            'company_mobile_number': ('company_mobile_number',),
            'msme_number': ('msme_number', 'msme_registration_no'),
            'aadhaar_number': ('aadhaar_number', 'aadhaar_no'),
            'pan_number': ('pan_number', 'pan_card_no'),
            'pmkisan_id': ('pmkisan_id', 'pm_kisan_reg_id'),
            'gender': ('gender',),
            'educational_qualification': ('educational_qualification',),
            'taluka': ('taluka', 'taluk'),
            'village': ('village',),
            'pincode': ('pincode',),
            'address': ('address', 'door_no'),
        }
        for field_name, aliases in simple_fields.items():
            for key in aliases:
                if key in data:
                    value = data.get(key)
                    value = '' if value is None else str(value).strip()
                    if field_name == 'pan_number':
                        value = value.upper()
                    setattr(profile, field_name, value)
                    break

        # Father's / Husband's / Guardian name (website posts guardian_name, mobile posts father_name)
        guardian = text_value('father_name', 'guardian_name')
        if guardian:
            profile.father_name = guardian
            profile.guardian_name = guardian

        # Date of birth: accepts yyyy-MM-dd (mobile/website) and dd/MM/yyyy (mobile picker)
        dob_value = text_value('date_of_birth', 'dob')
        if dob_value:
            for date_format in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
                try:
                    parsed_dob = datetime.strptime(dob_value, date_format).date()
                except ValueError:
                    continue
                profile.date_of_birth = parsed_dob
                profile.dob = parsed_dob
                break

        # State / district: accept id, name or code
        from apps.superadmin.models import State, City
        state_value = text_value('state')
        if state_value:
            state_obj = State.objects.filter(id=int(state_value)).first() if state_value.isdigit() else None
            if state_obj is None:
                state_obj = State.objects.filter(name__iexact=state_value).first()
            if state_obj is not None:
                profile.state = state_obj

        district_value = text_value('district')
        if district_value:
            district_obj = City.objects.filter(id=int(district_value)).first() if district_value.isdigit() else None
            if district_obj is None:
                district_obj = City.objects.filter(name__iexact=district_value).first()
            if district_obj is not None:
                profile.district = district_obj

        # Profile image: uploaded file (website) or base64 data-URI under 'img' (mobile)
        if 'profile_image' in request.FILES:
            image_file = request.FILES['profile_image']
            if image_file.size <= MAX_UPLOAD_SIZE:
                profile.profile_image = image_file
        else:
            decoded = _decode_data_uri(text_value('img'), prefix='profile')
            if decoded:
                profile.profile_image.save(decoded[1], ContentFile(decoded[0]), save=False)

        # MSME certificate: uploaded file or base64 data-URI (mobile)
        if 'msme_certificate' in request.FILES:
            certificate_file = request.FILES['msme_certificate']
            if certificate_file.size <= MAX_UPLOAD_SIZE:
                profile.msme_certificate = certificate_file
        else:
            decoded = _decode_data_uri(text_value('msme_certificate'), prefix='msme_certificate')
            if decoded:
                profile.msme_certificate.save(decoded[1], ContentFile(decoded[0]), save=False)

        owner.save()
        profile.save()

        return Response(FarmerProfileDetailSerializer(profile).data, status=status.HTTP_200_OK)

    @swagger_auto_schema(operation_summary='Partial Update Farmer Profile', tags=['Mobile - 02. Profile & Subscription'])
    def patch(self, request):
        return self.put(request)


class FarmerLogoutAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Logout Farmer',
        operation_description='Logs out the farmer from the mobile app. The mobile developer should clear the JWT token from local storage after calling this API.',
        responses={
            200: openapi.Response('Logged out successfully'),
            401: openapi.Response('Unauthorized')
        },
        tags=['Mobile - 01. Authentication']
    )
    def post(self, request):
        # Since JWT access tokens are stateless, actual logout happens on the client side
        # by deleting the token. This API provides the standard success response.
        return Response({'message': 'Logged out successfully'}, status=status.HTTP_200_OK)


from apps.superadmin.models import Subscription
from apps.farmer.serializers import FarmerSubscriptionSerializer

class FarmerSubscriptionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Farmer Subscription Details',
        operation_description='Returns all details of the active package/subscription for the authenticated farmer.',
        responses={
            200: FarmerSubscriptionSerializer(),
            404: openapi.Response('No active subscription found'),
            401: openapi.Response('Unauthorized')
        },
        tags=['Mobile - 02. Profile & Subscription']
    )
    def get(self, request):
        profile = get_farmer_profile(request.user)
        if profile is None:
            return Response({'error': 'Access denied: Not a farmer profile.'}, status=status.HTTP_403_FORBIDDEN)

        subscription = Subscription.objects.filter(farmer=profile,).order_by('-created_at').first()
        if not subscription:
            return Response({'error': 'No active subscription found for this farmer.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = FarmerSubscriptionSerializer(subscription)
        return Response(serializer.data, status=status.HTTP_200_OK)


class FarmerAvailableAcresAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Available Acres for Active Package',
        operation_description='Returns the active package acre limit and the remaining available acres for the authenticated farmer.',
        responses={
            200: openapi.Response('Available acres returned successfully'),
            404: openapi.Response('No active subscription found'),
            401: openapi.Response('Unauthorized')
        },
        tags=['Mobile - 02. Profile & Subscription']
    )
    def get(self, request):
        profile = get_farmer_profile(request.user)
        if profile is None:
            return Response({'error': 'Access denied: Not a farmer profile.'}, status=status.HTTP_403_FORBIDDEN)

        subscription = Subscription.objects.filter(farmer=profile ).order_by('-created_at').first()
        if not subscription or not subscription.package:
            return Response({'error': 'No active subscription found for this farmer.'}, status=status.HTTP_404_NOT_FOUND)

        from apps.farmer.models import Farm
        import re

        package = subscription.package
        max_acre_value = str(package.max_acre_support or '').strip()
        max_acres = None
        if max_acre_value.lower() != 'unlimited' and max_acre_value:
            match = re.search(r'[\d\.]+', max_acre_value)
            if match:
                max_acres = float(match.group())

        used_acres = 0.0
        farm_count = Farm.objects.filter(farmer=profile).count()
        manager_count = FarmerStaff.objects.filter(
            farmer=profile, is_role_manager=True
        ).count()
        crop_count = 0 
        for farm in Farm.objects.filter(farmer=profile):
            crop_count += CropSplit.objects.filter( farm =farm).count() 
            if not farm.total_area:
                continue
            match = re.search(r'[\d\.]+', str(farm.total_area))
            if match:
                try:
                    used_acres += float(match.group())
                except ValueError:
                    pass

        available = None if max_acres is None else max(0.0, max_acres - used_acres)

        def to_display(value):
            if value is None:
                return 'Unlimited'
            value_str = str(value)
            if value_str.lower() == 'unlimited':
                return 'Unlimited'
            if value_str == '0' or value_str == '0.0' or value_str == '0.00':
                return '0'
            return value_str.rstrip('0').rstrip('.') if '.' in value_str else value_str
        def remaining_limit(value, used_count):
            limit_value = str('' if value is None else value).strip()
            if not limit_value or limit_value.lower() == 'unlimited':
                return 'Unlimited'
            try:
                limit = int(float(limit_value))
            except (ValueError, TypeError):
                return 0
            return max(0, limit - used_count)

     
        return Response({
            'package_id': package.id,
            'package_name': package.name,
            'land_limit': remaining_limit(package.my_land, farm_count),
            'crop_limit': remaining_limit(package.my_crop, crop_count),
            # Package user limits apply to manager-access accounts only.
            'staff_limit': remaining_limit(package.my_users, manager_count),
            'manager_limit': remaining_limit(package.my_users, manager_count),
            'manager_count': manager_count,
            'max_acre_limit': package.max_acre_support or 'Unlimited',
            'max_package_acres': package.max_acre_support or 'Unlimited',
            'available_acres': 'Unlimited' if available is None else to_display(available),
            'balance_acre_limit': 'Unlimited' if available is None else to_display(available),
            'used_acres': to_display(used_acres),
        }, status=status.HTTP_200_OK)


subscription_history_search_param = openapi.Parameter(
    'search', openapi.IN_QUERY,
    description='Search by package name, subscription status or payment status',
    type=openapi.TYPE_STRING,
)


class FarmerSubscriptionHistoryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Farmer Subscription History',
        operation_description='Returns paginated subscription history for the authenticated farmer.',
        manual_parameters=[
            openapi.Parameter('page', openapi.IN_QUERY, description='Page number', type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, description='Items per page (max 100)', type=openapi.TYPE_INTEGER),
            subscription_history_search_param,
        ],
        responses={200: FarmerSubscriptionSerializer(many=True)},
        tags=['Mobile - 02. Profile & Subscription']
    )
    def get(self, request):
        profile = get_farmer_profile(request.user)
        if profile is None:
            return Response({'error': 'Access denied: Not a farmer profile.'}, status=status.HTTP_403_FORBIDDEN)

        subscriptions = Subscription.objects.filter(farmer=profile)
        search = (request.query_params.get('search') or '').strip()
        if search:
            subscriptions = subscriptions.filter(
                Q(package__name__icontains=search)
                | Q(subscription_status__icontains=search)
                | Q(payment_status__icontains=search)
            )
        subscriptions = subscriptions.order_by('-created_at', '-id')
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(subscriptions, request)
        serializer = FarmerSubscriptionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


from apps.superadmin.models import Package
from rest_framework import serializers, generics

class PackageSerializer(serializers.ModelSerializer):
    cycle_quotes = serializers.SerializerMethodField()

    class Meta:
        model = Package
        fields = '__all__'

    def get_cycle_quotes(self, package):
        request = self.context.get('request')
        farmer = get_farmer_profile(request.user) if request else None
        if farmer is None:
            return {}

        from apps.superadmin.subscription_utils import (
            billing_cycle_duration_label,
            billing_cycle_unit_label,
            calculate_plan_switch_quote,
            get_package_cycle_price,
            is_package_cycle_active,
        )

        return {
            cycle: {
                **calculate_plan_switch_quote(farmer, package, target_cycle=cycle),
                'is_cycle_active': is_package_cycle_active(package, cycle),
                'price': get_package_cycle_price(package, cycle),
                'unit_label': billing_cycle_unit_label(cycle),
                'duration_label': billing_cycle_duration_label(cycle),
            }
            for cycle in ('monthly', 'quarterly', 'yearly')
        }

class PackageListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PackageSerializer
    pagination_class = None

    def get_queryset(self):
        return Package.objects.filter(is_active=True).order_by('price', 'id')

    @swagger_auto_schema(operation_summary='Get All Subscription Packages', tags=['Mobile - 02. Profile & Subscription'])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

class FarmerSwitchPlanAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Initialize Plan Upgrade (Razorpay)',
        operation_description='Creates a Razorpay order for upgrading/switching subscription plans.',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['package_id'],
            properties={'package_id': openapi.Schema(type=openapi.TYPE_INTEGER)}
        ),
        responses={200: openapi.Response('Order created')},
        tags=['Mobile - 02. Profile & Subscription']
    )
    def post(self, request):
        from apps.superadmin.subscription_utils import (
            calculate_plan_switch_quote,
            cancel_scheduled_subscription_switch,
            initiate_farmer_subscription_order,
            normalize_billing_cycle,
            schedule_subscription_switch,
        )

        farmer = get_farmer_profile(request.user)
        if farmer is None:
            return Response(
                {'error': 'Farmer profile not found.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        action = str(request.data.get('action') or 'initiate').strip().lower()
        if action == 'cancel_scheduled_switch':
            ok, message = cancel_scheduled_subscription_switch(farmer)
            if not ok:
                return Response({'error': message}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'success': True, 'message': message})

        package_id = request.data.get('package_id')
        package = Package.objects.filter(id=package_id).first()
        if not package:
            return Response({'error': 'Package not found'}, status=status.HTTP_404_NOT_FOUND)

        billing_cycle = normalize_billing_cycle(request.data.get('billing_cycle'))
        if action == 'quote':
            quote = calculate_plan_switch_quote(
                farmer, package, target_cycle=billing_cycle
            )
            return Response({'success': True, 'quote': quote})

        if action == 'schedule_switch':
            ok, message, quote = schedule_subscription_switch(
                farmer, package, target_cycle=billing_cycle
            )
            if not ok:
                return Response(
                    {'error': message, 'quote': quote},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response({
                'success': True,
                'scheduled': True,
                'message': message,
                'quote': quote,
            })

        if action != 'initiate':
            return Response(
                {'error': 'Unsupported subscription action.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        is_renewal = str(request.data.get('is_renewal') or '').lower() in (
            'true',
            '1',
            'yes',
        )
        ok, message, quote, order_payload = initiate_farmer_subscription_order(
            farmer_profile=farmer,
            user=request.user,
            target_package=package,
            target_cycle=billing_cycle,
            is_renewal=is_renewal,
        )
        if not ok or not order_payload:
            return Response(
                {'error': message, 'quote': quote},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({'success': True, **order_payload})

class FarmerVerifyPaymentAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Verify Plan Upgrade Payment',
        operation_description='Verifies the Razorpay payment signature and activates the new subscription package.',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['package_id'],
            properties={
                'razorpay_payment_id': openapi.Schema(type=openapi.TYPE_STRING),
                'razorpay_order_id': openapi.Schema(type=openapi.TYPE_STRING),
                'razorpay_signature': openapi.Schema(type=openapi.TYPE_STRING),
                'package_id': openapi.Schema(type=openapi.TYPE_INTEGER),
            }
        ),
        responses={200: openapi.Response('Payment verified and plan updated')},
        tags=['Mobile - 02. Profile & Subscription']
    )
    def post(self, request):
        from apps.superadmin.subscription_utils import (
            billing_cycle_label,
            complete_farmer_subscription_payment,
            mark_pending_subscription_status,
            normalize_billing_cycle,
        )

        data = request.data
        farmer = get_farmer_profile(request.user)
        if farmer is None:
            return Response(
                {"error": "Farmer profile not found."},
                status=status.HTTP_403_FORBIDDEN,
            )

        order_id = str(data.get('razorpay_order_id') or '').strip()
        status_action = str(
            data.get('payment_status') or data.get('status') or ''
        ).strip().lower()
        if status_action in ('failed', 'cancelled', 'canceled'):
            mark_pending_subscription_status(
                farmer, razorpay_order_id=order_id, new_status=status_action
            )
            return Response({
                'success': True,
                'status': 'cancelled' if status_action == 'canceled' else status_action,
            })

        payment_id = str(data.get('razorpay_payment_id') or '').strip()
        signature = str(data.get('razorpay_signature') or '').strip()
        package_id = data.get('package_id')
        if not order_id or not payment_id or not signature or not package_id:
            return Response(
                {'error': 'Payment details missing.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        package = Package.objects.filter(id=package_id).first()
        if not package:
            return Response(
                {'error': 'Selected plan not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        pending_subscription = Subscription.objects.filter(
            farmer=farmer, razorpay_order_id=order_id
        ).select_related('package').first()
        if (
            pending_subscription is None
            or pending_subscription.package_id != package.id
        ):
            return Response(
                {'error': 'No matching subscription order was found.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        billing_cycle = normalize_billing_cycle(
            data.get('billing_cycle') or pending_subscription.billing_cycle
        )
        is_renewal = str(data.get('is_renewal') or '').lower() in (
            'true',
            '1',
            'yes',
        )
        ok, message, subscription, replay = complete_farmer_subscription_payment(
            farmer_profile=farmer,
            package=package,
            razorpay_order_id=order_id,
            razorpay_payment_id=payment_id,
            razorpay_signature=signature,
            billing_cycle=billing_cycle,
            is_renewal=is_renewal,
            mobile_number=str(data.get('mobile_number') or farmer.mobile_number or ''),
            request=request,
        )
        if not ok or subscription is None:
            return Response(
                {'error': message or 'Payment verification failed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cycle = normalize_billing_cycle(subscription.billing_cycle)
        return Response({
            'success': True,
            'subscription_id': subscription.id,
            'package_id': subscription.package_id,
            'package_name': subscription.package.name if subscription.package else '',
            'billing_cycle': cycle,
            'billing_cycle_label': billing_cycle_label(cycle),
            'start_date': (
                subscription.start_date.isoformat() if subscription.start_date else ''
            ),
            'end_date': (
                subscription.end_date.isoformat() if subscription.end_date else ''
            ),
            'amount_paid': str(subscription.effective_amount_paid or 0),
            'replayed': replay,
        }, status=status.HTTP_200_OK)


from rest_framework import generics, filters
from apps.farmer.models import Farm
from apps.farmer.serializers import FarmSerializer

farm_search_param = openapi.Parameter('search', openapi.IN_QUERY, description='Search farms by name, address, state/district/taluk, crop type or crop-split crop name', type=openapi.TYPE_STRING)
farm_health_param = openapi.Parameter('health', openapi.IN_QUERY, description='Filter by health indicator (e.g., Excellent, Good, Needs Attention)', type=openapi.TYPE_STRING)
farm_region_param = openapi.Parameter('region', openapi.IN_QUERY, description='Filter by state or district name', type=openapi.TYPE_STRING)


class FarmListCreateAPIView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = FarmSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [filters.SearchFilter]
    search_fields = [
        'land_name', 'total_area', 'address',
        'state__name', 'district__name', 'taluk__name', 'crop_type__name',
        'crops__name',
        'splits__crop__name',
    ]

    def get_serializer(self, *args, **kwargs):
        data = kwargs.get('data')
        if data is not None:
            data = data.copy()
            raw_crops = data.get('crops', data.get('crop_ids', []))
            crop_ids = self._normalize_crop_ids(raw_crops)
            if hasattr(data, 'setlist'):
                data.setlist('crops', [str(crop_id) for crop_id in crop_ids])
                if 'crop_ids' in data:
                    data.pop('crop_ids', None)
            else:
                data['crops'] = crop_ids
                data.pop('crop_ids', None)
            kwargs['data'] = data
        return super().get_serializer(*args, **kwargs)

    @staticmethod
    def _normalize_crop_ids(value):
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return []
            try:
                import json
                value = json.loads(value)
            except (TypeError, ValueError):
                value = value.split(',')
        if not isinstance(value, (list, tuple, set)):
            value = [value]
        return [
            int(crop_id) for crop_id in value
            if str(crop_id).strip().isdigit()
        ]

    @swagger_auto_schema(
        operation_summary='List Farmer Farms (Mobile)',
        operation_description='Paginated farms list for the authenticated farmer (page / page_size). Supports server-side search (?search=, matches farm name, address, location and crop names) and filters (?health=, ?region=). Each farm includes crop_count and allocated_acres for allocation bars.',
        tags=['Mobile - 03. Farm & Land Management'],
        manual_parameters=[farm_search_param, farm_health_param, farm_region_param]
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary='Create a new Farm (Mobile)',
        operation_description='Registers a new farm/land under the authenticated farmer\'s profile. Required fields must be provided by the mobile form.',
        tags=['Mobile - 03. Farm & Land Management']
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def get_queryset(self):
        from django.db.models import Prefetch

        manager = _manager_for_request(self.request.user)
        if ACTIVE_PROFILE_TYPE.get() == 'manager' and manager is None:
            return Farm.objects.none()

        qs = Farm.objects.filter(
            farmer=get_farmer_profile(self.request.user),
        ).select_related('state', 'district', 'taluk', 'crop_type')
        if manager:
            qs = qs.filter(splits__supervisors=manager).distinct().prefetch_related(
                'crops',
                Prefetch(
                    'splits',
                    queryset=CropSplit.objects.filter(
                        supervisors=manager,
                    ).select_related('crop'),
                ),
            )
        else:
            qs = qs.prefetch_related('crops', 'splits__crop')
        qs = qs.order_by('-id')

        # Server-side filters (mobile forming list — no client-side filtering).
        health = (self.request.query_params.get('health') or '').strip()
        if health:
            qs = qs.filter(health_indicator__iexact=health)

        region = (self.request.query_params.get('region') or '').strip()
        if region:
            qs = qs.filter(Q(state__name__iexact=region) | Q(district__name__iexact=region))

        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        manager = _manager_for_request(self.request.user)
        if manager is not None:
            context['manager_staff_id'] = manager.id
        return context

    def perform_create(self, serializer):
        # Automatically assign the effective farmer (staff resolve to owning farmer)
        serializer.save(farmer=get_farmer_profile(self.request.user))


class FarmRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = FarmSerializer

    @swagger_auto_schema(
        operation_summary='Retrieve Farm Details (Mobile)',
        operation_description='Gets the full details of a specific farm by its ID. Used for the Farm Detail screen on mobile.',
        tags=['Mobile - 03. Farm & Land Management']
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @swagger_auto_schema(operation_summary='Update a Farm Fully (Mobile)', operation_description='Replaces all farm details. Usually PATCH is preferred over PUT for mobile forms.', tags=['Mobile - 03. Farm & Land Management'])
    def put(self, request, *args, **kwargs):
        return super().put(request, *args, **kwargs)

    @swagger_auto_schema(operation_summary='Update a Farm Partially (Mobile)', operation_description='Updates specific fields of the farm without requiring the full object. Best for quick mobile edits.', tags=['Mobile - 03. Farm & Land Management'])
    def patch(self, request, *args, **kwargs):
        return super().patch(request, *args, **kwargs)

    @swagger_auto_schema(operation_summary='Delete a Farm (Mobile)', operation_description='Removes the farm permanently from the farmer\'s profile.', tags=['Mobile - 03. Farm & Land Management'])
    def delete(self, request, *args, **kwargs):
        farm = self.get_object()
        from apps.farmer.deletion_rules import farm_delete_block_reason

        block_reason = farm_delete_block_reason(farm)
        if block_reason:
            return Response({'error': block_reason}, status=status.HTTP_400_BAD_REQUEST)
        return super().delete(request, *args, **kwargs)

    def get_queryset(self):
        from django.db.models import Prefetch

        manager = _manager_for_request(self.request.user)
        if ACTIVE_PROFILE_TYPE.get() == 'manager' and manager is None:
            return Farm.objects.none()

        qs = Farm.objects.filter(
            farmer=get_farmer_profile(self.request.user),
        ).select_related('state', 'district', 'taluk', 'crop_type')
        if manager:
            return qs.filter(splits__supervisors=manager).distinct().prefetch_related(
                'crops',
                Prefetch(
                    'splits',
                    queryset=CropSplit.objects.filter(
                        supervisors=manager,
                    ).select_related('crop'),
                ),
            )
        return qs.prefetch_related('crops', 'splits__crop')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        manager = _manager_for_request(self.request.user)
        if manager is not None:
            context['manager_staff_id'] = manager.id
        return context

    def get_serializer(self, *args, **kwargs):
        data = kwargs.get('data')
        if data is not None:
            data = data.copy()
            raw_crops = data.get('crops', data.get('crop_ids', []))
            if isinstance(raw_crops, str):
                raw_crops = [
                    item.strip() for item in raw_crops.split(',')
                    if item.strip()
                ]
            if not isinstance(raw_crops, (list, tuple, set)):
                raw_crops = [raw_crops]
            crop_ids = [
                int(crop_id) for crop_id in raw_crops
                if str(crop_id).strip().isdigit()
            ]
            if hasattr(data, 'setlist'):
                data.setlist('crops', [str(crop_id) for crop_id in crop_ids])
                data.pop('crop_ids', None)
            else:
                data['crops'] = crop_ids
                data.pop('crop_ids', None)
            kwargs['data'] = data
        return super().get_serializer(*args, **kwargs)


from apps.farmer.models import CropSplit, FarmActivity, FarmTask
from apps.farmer.serializers import CropSplitSerializer, FarmActivitySerializer, FarmTaskSerializer, CropHarvestSerializer
from rest_framework import viewsets
from django.utils.decorators import method_decorator

farm_id_param = openapi.Parameter('farm_id', openapi.IN_QUERY, description="Filter by Land/Farm ID", type=openapi.TYPE_INTEGER)
stage_param = openapi.Parameter('stage', openapi.IN_QUERY, description="Filter by Crop Stage (e.g., Completed)", type=openapi.TYPE_STRING)
activity_type_param = openapi.Parameter('activity_type', openapi.IN_QUERY, description="Filter by Activity Type (e.g., Resource Consumption)", type=openapi.TYPE_STRING)
category_param = openapi.Parameter('category', openapi.IN_QUERY, description="Filter by Category (e.g., Expense)", type=openapi.TYPE_STRING)
status_param = openapi.Parameter('status', openapi.IN_QUERY, description="Filter by Status (e.g., Pending, In Progress)", type=openapi.TYPE_STRING)
priority_param = openapi.Parameter('priority', openapi.IN_QUERY, description="Filter by Priority (e.g., High Priority)", type=openapi.TYPE_STRING)
worker_id_param = openapi.Parameter('worker_id', openapi.IN_QUERY, description="Filter by Assigned Worker ID", type=openapi.TYPE_INTEGER)
date_from_param = openapi.Parameter('date_from', openapi.IN_QUERY, description="Filter tasks active on/after this date (YYYY-MM-DD)", type=openapi.TYPE_STRING)
date_to_param = openapi.Parameter('date_to', openapi.IN_QUERY, description="Filter tasks active on/before this date (YYYY-MM-DD)", type=openapi.TYPE_STRING)
task_search_param = openapi.Parameter('search', openapi.IN_QUERY, description="Search in task title and description (icontains)", type=openapi.TYPE_STRING)
status_in_param = openapi.Parameter('status_in', openapi.IN_QUERY, description="Comma-separated statuses, e.g. 'Pending,In Progress' (OR match)", type=openapi.TYPE_STRING)
worker_name_param = openapi.Parameter('worker_name', openapi.IN_QUERY, description="Filter by assigned worker name (icontains, single or multi-assigned)", type=openapi.TYPE_STRING)
staff_id_param = openapi.Parameter('staff_id', openapi.IN_QUERY, description="Filter by Staff/Employee ID", type=openapi.TYPE_INTEGER)
category_id_param = openapi.Parameter('category_id', openapi.IN_QUERY, description="Filter by Category ID", type=openapi.TYPE_INTEGER)
is_active_param = openapi.Parameter('is_active', openapi.IN_QUERY, description="Filter active splits (true: excludes Harvested/Sold, false: only Harvested/Sold)", type=openapi.TYPE_BOOLEAN)
order_param = openapi.Parameter('order', openapi.IN_QUERY, description="Sort order: asc or desc (default: desc)", type=openapi.TYPE_STRING)

@method_decorator(name='list', decorator=swagger_auto_schema(tags=['Mobile - 04. Crop Splits & Cultivation'], manual_parameters=[farm_id_param, stage_param, is_active_param]))
@method_decorator(name='create', decorator=swagger_auto_schema(tags=['Mobile - 04. Crop Splits & Cultivation']))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 04. Crop Splits & Cultivation']))
@method_decorator(name='update', decorator=swagger_auto_schema(tags=['Mobile - 04. Crop Splits & Cultivation']))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(tags=['Mobile - 04. Crop Splits & Cultivation']))
@method_decorator(name='destroy', decorator=swagger_auto_schema(tags=['Mobile - 04. Crop Splits & Cultivation']))
class CropSplitViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CropSplitSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        farm_id = self.request.query_params.get('farm_id')
        stage = self.request.query_params.get('stage')
        is_active = self.request.query_params.get('is_active')

        qs = CropSplit.objects.filter(farm__farmer=get_farmer_profile(self.request.user))
        manager = _manager_for_request(self.request.user)
        if ACTIVE_PROFILE_TYPE.get() == 'manager' and manager is None:
            return CropSplit.objects.none()
        if manager is not None:
            qs = qs.filter(supervisors=manager).order_by('-id')
        if farm_id:
            qs = qs.filter(farm_id=farm_id)
        if stage:
            qs = qs.filter(stage__iexact=stage)

        if is_active is not None:
            if is_active.lower() == 'true':
                qs = qs.exclude(stage__iexact='Inactive')
            elif is_active.lower() == 'false':
                qs = qs.filter(stage__iexact='Inactive')

        return qs

    def destroy(self, request, *args, **kwargs):
        split = self.get_object()
        from apps.farmer.deletion_rules import crop_split_delete_block_reason

        block_reason = crop_split_delete_block_reason(split)
        if block_reason:
            return Response({'error': block_reason}, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    def _crop_activity_operator(self, split):
        manager = _get_user_staff_assignment(self.request.user, manager_only=True)
        if manager:
            return manager.name
        return (
            self.request.user.first_name
            or (split.supervisor.name if split.supervisor else '')
            or self.request.user.get_username()
        )

    def _record_crop_activity(
        self,
        split,
        *,
        activity_type='Other Land Care',
        asset_name,
        quantity_metric,
        notes,
        operator_name=None,
    ):
        from apps.farmer.models import FarmActivity

        return FarmActivity.objects.create(
            farm=split.farm,
            activity_type=activity_type,
            asset_name=asset_name,
            quantity_metric=quantity_metric,
            operator_name=operator_name or self._crop_activity_operator(split),
            notes=notes,
        )

    def perform_create(self, serializer):
        # Normalize harvest flushes like the website does:
        # single -> 1 flush, multiple -> 3 flushes when not provided.
        h_type = self.request.data.get('harvest_type') or 'single'
        is_multi = h_type in ['multiple', 'Multiple Harvest']
        tf_raw = self.request.data.get('total_flushes')
        try:
            total_flushes = max(1, min(50, int(tf_raw))) if tf_raw not in [None, ''] else (3 if is_multi else 1)
        except (TypeError, ValueError):
            total_flushes = 3 if is_multi else 1
        supervisor_ids = self._supervisor_ids()
        split = serializer.save(
            harvest_type=h_type,
            total_flushes=total_flushes,
            supervisor=self._primary_supervisor(supervisor_ids),
        )
        self._assign_supervisors(split, supervisor_ids)

        crop_name = split.crop.name if split.crop else 'Crop'
        operator_name = self._crop_activity_operator(split)
        notes_method = (
            f' using {split.cultivation_method} method'
            if split.cultivation_method else ''
        )
        self._record_crop_activity(
            split,
            activity_type='Seeds Sowing',
            asset_name=crop_name,
            quantity_metric=f'{split.acreage} Acres',
            operator_name=operator_name,
            notes=(
                f'Initial crop split creation for {crop_name}{notes_method} '
                f'by {operator_name}. | CropSplit #{split.id}'
            ),
        )

        auto_generate = self.request.data.get('auto_generate_tasks', False)
        if auto_generate and str(auto_generate).lower() in ['true', 'yes', '1']:
            from apps.superadmin.models import CropBlueprint
            from apps.farmer.models import FarmTask
            from datetime import timedelta

            if split.crop and split.start_date:
                blueprint = CropBlueprint.objects.filter(crop_name__iexact=split.crop.name, farmer__isnull=True).first()
                if blueprint:
                    for stage in blueprint.stages.all():
                        task_date = split.start_date + timedelta(days=stage.day_offset)
                        FarmTask.objects.create(
                            farm=split.farm,
                            farmer=split.farm.farmer if split.farm else None,
                            associated_split=split,
                            title=stage.milestone_title,
                            description=f"Activity: {stage.activity_type}",
                            priority=stage.priority_level,
                            status='Pending',
                            assigned_worker=split.supervisor,
                            task_date=task_date,
                            work_timeline=task_date.strftime('%Y-%m-%d')
                        )

    def perform_update(self, serializer):
        instance = serializer.instance
        detail_fields = (
            'crop_id',
            'acreage',
            'cultivation_method',
            'start_date',
            'crop_category',
            'crop_pattern',
            'crop_variety',
            'harvest_type',
            'total_flushes',
            'row_count',
            'plant_count',
            'duration_min',
            'duration_max',
            'expected_yield',
            'expected_yield_unit',
            'seed_details',
            'plant_spacing',
            'crop_notes',
        )
        old_details = tuple(getattr(instance, field) for field in detail_fields)
        old_stage = instance.stage
        old_supervisor_ids = set(instance.supervisor_ids_list)

        supervisor_ids = self._supervisor_ids()
        split = serializer.save(
            supervisor=self._primary_supervisor(supervisor_ids),
        )
        self._assign_supervisors(split, supervisor_ids)

        operator_name = self._crop_activity_operator(split)
        crop_name = split.crop.name if split.crop else 'Crop'
        if old_stage != split.stage:
            self._record_crop_activity(
                split,
                asset_name=f'{crop_name} Phase Update',
                quantity_metric=f'{split.acreage} Ac',
                operator_name=operator_name,
                notes=(
                    f"Crop cultivation stage updated to '{split.stage}' "
                    f'by {operator_name} | CropSplit #{split.id}.'
                ),
            )

        details_changed = old_details != tuple(
            getattr(split, field) for field in detail_fields
        )
        supervisors_were_submitted = (
            'supervisors' in self.request.data or 'supervisor' in self.request.data
        )
        if supervisors_were_submitted and old_supervisor_ids != set(split.supervisor_ids_list):
            details_changed = True
        if details_changed:
            self._record_crop_activity(
                split,
                asset_name=f'{crop_name} Details Updated',
                quantity_metric=f'{split.acreage} Ac',
                operator_name=operator_name,
                notes=(
                    f"Crop segment '{crop_name}' details updated by "
                    f'{operator_name} | CropSplit #{split.id}.'
                ),
            )
    def _supervisor_ids(self):
        raw = self.request.data.get('supervisors')
        if raw is None:
            raw = self.request.data.get('supervisor')
        if raw in (None, ''):
            return []
        if not isinstance(raw, (list, tuple)):
            raw = [raw]
        try:
            ids = list(dict.fromkeys(int(value) for value in raw))
        except (TypeError, ValueError):
            raise ValidationError({'supervisors': 'Supervisor IDs must be integers.'})
        staff_ids = set(
            FarmerStaff.objects.filter(
                id__in=ids,
                farmer=get_farmer_profile(self.request.user),
                is_role_manager=True,
                status='0',
            ).values_list('id', flat=True)
        )
        if len(staff_ids) != len(ids):
            raise ValidationError({
                'supervisors': 'Select only active manager-access staff from this farm account.'
            })
        return ids

    def _primary_supervisor(self, supervisor_ids):
        if not supervisor_ids:
            return None
        return FarmerStaff.objects.get(
            id=supervisor_ids[0],
            farmer=get_farmer_profile(self.request.user),
            is_role_manager=True,
            status='0',
        )

    def _assign_supervisors(self, split, supervisor_ids):
        managers = FarmerStaff.objects.filter(
            id__in=supervisor_ids,
            farmer=get_farmer_profile(self.request.user),
            is_role_manager=True,
            status='0',
        )
        split.supervisors.set(managers)

    @swagger_auto_schema(method='get', tags=['Mobile - 04. Crop Splits & Cultivation'], operation_description="Get complete crop profile data bundle in a single fast call for smooth rendering")
    @action(detail=True, methods=['get'])
    def profile(self, request, pk=None):
        split = self.get_object()
        split_data = CropSplitSerializer(split).data

        from apps.farmer.models import FarmTask, FarmActivity, LedgerTransaction
        from apps.farmer.serializers import FarmTaskSerializer, FarmActivitySerializer, LedgerTransactionSerializer
        from django.db.models import Q

        # 1. Associated tasks
        tasks_qs = FarmTask.objects.filter(associated_split=split).select_related('assigned_worker').order_by('-id')
        task_data = FarmTaskSerializer(tasks_qs, many=True).data
        total_tasks = tasks_qs.count()
        pending_tasks = tasks_qs.filter(status__iexact='Pending').count()
        active_tasks = tasks_qs.filter(status__in=['In Progress', 'Scheduled']).count()
        completed_tasks = tasks_qs.filter(status__iexact='Completed').count()

        # 2. Consumption
        split_id_str = str(split.id)
        activities_qs = FarmActivity.objects.filter(
            farm=split.farm,
            activity_type="Resource Consumption"
        ).filter(
            Q(notes__icontains=f"CropSplit #{split_id_str}") |
            Q(notes__icontains=f"Crop Split ID: {split_id_str}") |
            Q(asset_name__icontains=f"CropSplit #{split_id_str}")
        ).order_by('-id')
        consumption_data = FarmActivitySerializer(activities_qs, many=True).data
        total_consumption = sum(float(a.amount or 0) for a in activities_qs)
        fleet_consumption = sum(
            float(a.amount or 0) for a in activities_qs
            if any(w in ((a.notes or '') + ' ' + (a.asset_name or '') + ' ' + (a.quantity_metric or '')).lower() 
                   for w in ['vehicle', 'fleet', 'tool', 'fuel', 'tractor', 'hour', 'machin', 'equip'])
        )
        warehouse_consumption = max(0.0, total_consumption - fleet_consumption)

        # 3. Expenses
        expenses_qs = LedgerTransaction.objects.filter(
            farm=split.farm
        ).filter(
            Q(tags__icontains=f"CropSplit #{split_id_str}") |
            Q(tags__icontains=f"crop split id: {split_id_str}") |
            Q(description__icontains=f"CropSplit #{split_id_str}")
        ).order_by('-id')
        expenses_data = LedgerTransactionSerializer(expenses_qs, many=True).data
        total_expenses = sum(float(e.amount or 0) for e in expenses_qs)
        seeds_cost = sum(
            float(e.amount or 0) for e in expenses_qs
            if 'seed' in (e.category or '').lower() or 'seed' in (e.description or '').lower()
        )
        fert_cost = sum(
            float(e.amount or 0) for e in expenses_qs
            if any(w in (e.category or '').lower() or w in (e.description or '').lower() for w in ['fertilizer', 'input', 'nutrient'])
        )

        # 4. Farm meta
        farm = split.farm
        farm_data = {
            'id': farm.id,
            'name': farm.land_name,
            'area': float(farm.total_area or 0),
            'latitude': float(farm.latitude) if farm.latitude else None,
            'longitude': float(farm.longitude) if farm.longitude else None,
            'locations': farm.district.name if farm.district else (farm.state.name if farm.state else ''),
            'health': getattr(farm, 'health', 'Good'),
        }

        # 5. Sold splits history
        sold_splits_qs = CropSplit.objects.filter(farm=farm, stage__in=['Sold', 'Harvested']).order_by('-updated_at')[:20]
        sold_history = CropSplitSerializer(sold_splits_qs, many=True).data

        return Response({
            'success': True,
            'crop_split': split_data,
            'farm': farm_data,
            'tasks': {
                'count': total_tasks,
                'total': total_tasks,
                'pending': pending_tasks,
                'active': active_tasks,
                'completed': completed_tasks,
                'results': task_data,
            },
            'consumption': {
                'count': len(consumption_data),
                'total': total_consumption,
                'warehouse': warehouse_consumption,
                'fleet': fleet_consumption,
                'results': consumption_data,
            },
            'expenses': {
                'count': len(expenses_data),
                'total': total_expenses,
                'seeds': seeds_cost,
                'fertilizers': fert_cost,
                'results': expenses_data,
            },
            'sold_history': {
                'count': len(sold_history),
                'results': sold_history,
            }
        })

    @swagger_auto_schema(method='get', tags=['Mobile - 04. Crop Splits & Cultivation'], operation_description="Get crop splits pending harvest (Stage: Yield)", manual_parameters=[farm_id_param])
    @action(detail=False, methods=['get'])
    def pending_harvest(self, request):
        farm_id = self.request.query_params.get('farm_id')
        qs = CropSplit.objects.filter(farm__farmer=get_farmer_profile(self.request.user), stage__iexact='Yield')
        if farm_id:
            qs = qs.filter(farm__id=farm_id)
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)

    @swagger_auto_schema(method='get', tags=['Mobile - 04. Crop Splits & Cultivation'], operation_description="Get crop splits pending sell (Stage: Harvested)", manual_parameters=[farm_id_param])
    @action(detail=False, methods=['get'])
    def pending_sells(self, request):
        farm_id = self.request.query_params.get('farm_id')
        qs = CropSplit.objects.filter(farm__farmer=get_farmer_profile(self.request.user), stage__iexact='Harvested')
        if farm_id:
            qs = qs.filter(farm__id=farm_id)
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)

    @swagger_auto_schema(method='post', tags=['Mobile - 04. Crop Splits & Cultivation'], operation_description="Advance crop stages, start a multiple-harvest flush with action=next_flush, complete a crop with action=complete, or toggle Active/Inactive status.")
    @action(detail=True, methods=['post'])
    def advance(self, request, pk=None):
        import re as _re
        from apps.farmer.models import FarmActivity
        split = self.get_object()
        crop_name = split.crop.name if split.crop else 'Crop'
        toggle_action = request.data.get('action')
        target_stage = request.data.get('stage')
        is_multi_harvest = str(split.harvest_type or '').lower() in {
            'multiple', 'multiple harvest',
        }
        mark_completed = (
            toggle_action == 'complete'
            or str(request.data.get('mark_completed', '')).lower() == 'true'
            or str(target_stage or '').lower() == 'completed'
        )
        if mark_completed:
            notes = str(split.crop_notes or '')
            if '[ALL_FLUSHES_COMPLETED]' not in notes:
                split.crop_notes = f'{notes} [ALL_FLUSHES_COMPLETED]'.strip()
            split.stage = 'Harvested'
            split.save()
            return Response({
                'success': True,
                'stage': 'Harvested',
                'new_stage': 'Harvested',
                'is_completed': True,
                'current_flush': split.current_flush,
                'harvest_type': split.harvest_type,
                'message': 'All harvest flushes have been completed.',
            })

        requested_next_flush = request.data.get('next_flush')
        next_flush_number = None
        if toggle_action == 'next_flush' or requested_next_flush not in (None, ''):
            if not is_multi_harvest:
                return Response(
                    {'success': False, 'error': 'Next flush is only available for multiple-harvest crops.'},
                    status=400,
                )
            try:
                next_flush_number = int(
                    requested_next_flush or (split.current_flush or 1) + 1
                )
            except (TypeError, ValueError):
                return Response(
                    {'success': False, 'error': 'next_flush must be a positive integer.'},
                    status=400,
                )
            if next_flush_number < 1:
                return Response(
                    {'success': False, 'error': 'next_flush must be a positive integer.'},
                    status=400,
                )
            if target_stage and str(target_stage).lower() != 'sown':
                return Response(
                    {'success': False, 'error': 'A new flush must start at the Sown stage.'},
                    status=400,
                )
            target_stage = 'Sown'

        # 1. Active / Inactive toggle (same as web advance_crop_split)
        if toggle_action in ['toggle_status', 'set_active', 'set_inactive'] or target_stage in ['Active', 'Inactive']:
            is_activating = (
                toggle_action == 'set_active'
                or target_stage == 'Active'
                or (toggle_action == 'toggle_status' and split.stage == 'Inactive')
            )
            farmer_prof = get_farmer_profile(request.user)
            if is_activating:
                # Subscription active-crop limit check
                active_sub = farmer_prof.subscriptions.filter(subscription_status='active').order_by('-id').first()
                if active_sub and active_sub.package:
                    max_crops_str = str(active_sub.package.my_crop or '').lower().strip()
                    if max_crops_str and max_crops_str != 'unlimited':
                        match_crop = _re.search(r'\d+', max_crops_str)
                        if match_crop:
                            max_crops = int(match_crop.group())
                            current_active_crops = CropSplit.objects.filter(farm__farmer=farmer_prof).exclude(id=split.id).exclude(stage='Inactive').count()
                            if current_active_crops >= max_crops:
                                return Response({
                                    'success': False,
                                    'limit_reached': True,
                                    'max_crops': max_crops,
                                    'current_active': current_active_crops,
                                    'error': f"You have reached your limit of {max_crops} active crops. Please inactivate another crop to activate this one.",
                                })

                # Restore previous stage if saved, else default 'Sown'
                notes = str(split.crop_notes or '')
                m_prev = _re.search(r'\[PREV_STAGE:([^\]]+)\]', notes)
                if m_prev:
                    restored_stage = m_prev.group(1).strip()
                    split.crop_notes = _re.sub(r'\[PREV_STAGE:[^\]]+\]', '', notes).strip()
                else:
                    restored_stage = 'Sown'
                split.stage = restored_stage
                split.save()

                op_name = split.supervisor.name if split.supervisor else 'Farm Manager'
                FarmActivity.objects.create(
                    farm=split.farm,
                    activity_type='Other Land Care',
                    asset_name=f'{crop_name} Activated',
                    quantity_metric=f'{split.acreage} Ac',
                    operator_name=op_name,
                    notes=f"Crop cultivation reactivated in '{split.stage}' stage | CropSplit #{split.id}.",
                )
                return Response({
                    'success': True,
                    'is_active': True,
                    'stage': split.stage,
                    'new_stage': split.stage,
                    'current_flush': split.current_flush,
                    'harvest_type': split.harvest_type,
                    'message': f"Crop activated in stage '{split.stage}'.",
                })

            # Deactivate -> Inactive
            notes = str(split.crop_notes or '')
            notes = _re.sub(r'\[PREV_STAGE:[^\]]+\]', '', notes).strip()
            if split.stage != 'Inactive':
                notes = f'{notes} [PREV_STAGE:{split.stage}]'.strip()
            split.crop_notes = notes
            split.stage = 'Inactive'
            split.save()

            op_name = split.supervisor.name if split.supervisor else 'Farm Manager'
            FarmActivity.objects.create(
                farm=split.farm,
                activity_type='Other Land Care',
                asset_name=f'{crop_name} Inactivated',
                quantity_metric=f'{split.acreage} Ac',
                operator_name=op_name,
                notes=f'Crop cultivation set to Inactive | CropSplit #{split.id}.',
            )
            return Response({
                'success': True,
                'is_active': False,
                'stage': 'Inactive',
                'new_stage': 'Inactive',
                'current_flush': split.current_flush,
                'harvest_type': split.harvest_type,
                'message': 'Crop marked as Inactive.',
            })

        new_stage = target_stage
        mobile_next_flush = (
            next_flush_number is None
            and is_multi_harvest
            and str(split.stage).lower() in {'harvested', 'sold'}
            and str(target_stage or '').lower() == 'yield'
        )
        if mobile_next_flush:
            existing_flushes = [split.current_flush or 1]
            for batch in split.harvest_batches or []:
                try:
                    existing_flushes.append(int(batch.get('flush_no') or 1))
                except (AttributeError, TypeError, ValueError):
                    continue
            next_flush_number = max(existing_flushes) + 1

        if (
            split.crop_notes
            and '[ALL_FLUSHES_COMPLETED]' in split.crop_notes
            and target_stage
            and str(target_stage).lower() not in {'harvested', 'completed'}
        ):
            return Response({
                'success': False,
                'error': f"All harvest flushes for '{crop_name}' have been completed.",
            }, status=400)

        valid_stages = ['Sown', 'Vegetative', 'Flowering', 'Yield', 'Harvested', 'Sold']
        if new_stage and any(s.lower() == new_stage.lower() for s in valid_stages):
            matched_stage = next(s for s in valid_stages if s.lower() == new_stage.lower())
            if split.stage == matched_stage and next_flush_number is None:
                return Response({
                    'success': True,
                    'stage': split.stage,
                    'is_completed': split.is_completed,
                })

            split.stage = matched_stage
            if next_flush_number is not None:
                split.current_flush = next_flush_number
                split.crop_notes = str(split.crop_notes or '').replace(
                    '[ALL_FLUSHES_COMPLETED]', ''
                ).strip()
            split.save()

            operator_name = self._crop_activity_operator(split)
            h_type = split.harvest_type or 'Single Harvest'
            flush_str = (
                f'Flush #{split.current_flush}'
                if split.harvest_type in ['multiple', 'Multiple Harvest']
                else ''
            )
            self._record_crop_activity(
                split,
                asset_name=f'{crop_name} Phase Update',
                quantity_metric=f'{split.acreage} Ac',
                operator_name=operator_name,
                notes=(
                    f"Crop cultivation stage updated to '{split.stage}' "
                    f'({h_type} {flush_str}) by {operator_name} | CropSplit #{split.id}.'
                ),
            )
            return Response({
                'success': True,
                'stage': split.stage,
                'current_flush': split.current_flush,
                'is_completed': split.is_completed,
            })

        stages = ['Sown', 'Vegetative', 'Flowering', 'Yield']
        if split.stage in stages:
            current_idx = stages.index(split.stage)
            if current_idx < len(stages) - 1:
                split.stage = stages[current_idx + 1]
                split.save()

                operator_name = self._crop_activity_operator(split)
                self._record_crop_activity(
                    split,
                    asset_name=f'{crop_name} Phase Update',
                    quantity_metric=f'{split.acreage} Ac',
                    operator_name=operator_name,
                    notes=(
                        f"Crop cultivation stage updated to '{split.stage}' "
                        f'by {operator_name} | CropSplit #{split.id}.'
                    ),
                )
                return Response({'success': True, 'stage': split.stage})
        return Response({'success': False, 'error': 'Cannot advance further.'}, status=400)

    @swagger_auto_schema(method='post', request_body=CropHarvestSerializer, tags=['Mobile - 04. Crop Splits & Cultivation'], operation_description="Finalize harvest and register crop yield.")
    @action(detail=True, methods=['post'])
    @transaction.atomic
    def finalize_harvest(self, request, pk=None):
        split = self.get_object()
        if split.stage.lower() != 'yield':
            return Response({
                'success': False,
                'error': f'Harvest can only be finalized at Yield stage (current stage: {split.stage}).',
            }, status=400)

        serializer = CropHarvestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        yield_tons = serializer.validated_data['yield_tons']
        price_per_mt = serializer.validated_data.get('price_per_mt')
        total_sale_value = serializer.validated_data.get('total_sale_value')
        unit_str = (
            request.data.get('unit')
            or split.expected_yield_unit
            or 'Ton'
        )
        split.expected_yield_unit = unit_str
        split.stage = 'Harvested'
        split.yield_tons = yield_tons
        if price_per_mt is not None:
            split.price_per_mt = price_per_mt
        if total_sale_value is not None:
            split.total_sale_value = total_sale_value

        parsed_batches = serializer.validated_data.get('batches_json') or []
        if not isinstance(parsed_batches, list) or any(
            not isinstance(batch, dict) for batch in parsed_batches
        ):
            return Response(
                {'batches_json': 'Expected a list of batch objects.'},
                status=400,
            )

        completed_flush = split.current_flush or 1
        if not parsed_batches:
            parsed_batches = [{
                'yield': float(yield_tons),
                'price': float(price_per_mt or 0),
                'saleValue': float(total_sale_value or 0),
                'quality': 'Grade 1',
                'unit': unit_str,
                'status': 'Ready',
                'flush_no': completed_flush,
            }]

        for batch in parsed_batches:
            if not batch.get('flush_no'):
                batch['flush_no'] = completed_flush
            if not batch.get('status'):
                batch['status'] = 'Ready'
        try:
            completed_flush = max(
                int(batch.get('flush_no') or completed_flush)
                for batch in parsed_batches
            )
        except (TypeError, ValueError):
            return Response(
                {'batches_json': 'Each batch flush_no must be a positive integer.'},
                status=400,
            )
        if completed_flush < 1:
            return Response(
                {'batches_json': 'Each batch flush_no must be a positive integer.'},
                status=400,
            )

        from apps.farmer.harvest_batches import normalize_batch_numbers
        all_batches = normalize_batch_numbers(
            list(split.harvest_batches or []) + parsed_batches
        )
        total_cum_yield = sum(float(batch.get('yield') or 0) for batch in all_batches)
        total_cum_value = sum(
            float(batch.get('saleValue') or 0) for batch in all_batches
        )
        if total_cum_yield <= 0:
            return Response(
                {'batches_json': 'Harvest batches must contain a positive yield.'},
                status=400,
            )

        split.harvest_batches = all_batches
        split.current_flush = max(split.current_flush or 1, completed_flush)
        split.yield_tons = total_cum_yield
        split.total_sale_value = total_cum_value
        split.price_per_mt = (
            total_cum_value / total_cum_yield
            if total_cum_yield > 0
            else float(price_per_mt or 0)
        )
        split.save()

        flush_label = (
            f'Flush #{completed_flush}'
            if split.harvest_type in ['multiple', 'Multiple Harvest']
            else 'Harvest'
        )
        crop_title = split.crop.name if split.crop else 'Crop'
        operator_name = self._crop_activity_operator(split)
        self._record_crop_activity(
            split,
            asset_name=f'{crop_title} ({flush_label})',
            quantity_metric=f'{yield_tons} {unit_str}',
            operator_name=operator_name,
            notes=(
                f'{flush_label} finalized with yield of {yield_tons} {unit_str} '
                f'at ₹{price_per_mt}/{unit_str} by {operator_name} | '
                f'CropSplit #{split.id}.'
            ),
        )

        return Response({
            'success': True,
            'harvest_batches': split.harvest_batches,
            'current_flush': split.current_flush,
            'stage': split.stage,
            'yield_tons': str(split.yield_tons),
            'total_sale_value': str(split.total_sale_value),
        })

    @swagger_auto_schema(method='get', tags=['Mobile - 04. Crop Splits & Cultivation'], operation_description="Get sell history for a crop split from LedgerTransactions", manual_parameters=[])
    @action(detail=True, methods=['get'])
    def sell_history(self, request, pk=None):
        split = self.get_object()
        from apps.farmer.models import LedgerTransaction
        tag_pattern = f"CropSplit #{split.id}"
        crop_name = split.crop.name if split.crop else ''
        transactions = LedgerTransaction.objects.filter(
            farmer=get_farmer_profile(request.user),
            category__in=["Sales", "Harvest Trade"],
        ).filter(
            Q(tags__icontains=tag_pattern) |
            Q(description__icontains=crop_name)
        ).order_by('-close_date', '-id')

        results = []
        for t in transactions:
            qty = float(t.quantity) if t.quantity else 0
            amt = float(t.amount) if t.amount else 0
            unit = t.unit or split.expected_yield_unit or 'MT'
            rate = (amt / qty) if qty > 0 and amt > 0 else 0
            results.append({
                'id': t.id,
                'description': t.description,
                'date': t.close_date.strftime('%d %b %Y') if t.close_date else '',
                'status': t.status,
                'quantity': qty,
                'unit': unit,
                'rate': round(rate),
                'amount': amt,
                'payment_method': t.payment_method or 'Cash',
            })

        return Response({'results': results})

    @swagger_auto_schema(
        method='get',
        tags=['Mobile - 04. Crop Splits & Cultivation'],
        operation_description="Get the crop activity and lifecycle timeline for a crop split",
        manual_parameters=[],
    )
    @action(detail=True, methods=['get'])
    def history(self, request, pk=None):
        import re
        from datetime import datetime, time
        from apps.farmer.models import FarmActivity, FarmTask, LedgerTransaction

        split = self.get_object()
        farmer = get_farmer_profile(request.user)
        split_tag = f"CropSplit #{split.id}"
        legacy_split_tag = f"Crop Split ID: {split.id}"
        events = []

        def add_event(event, event_date, priority, sort_date=None):
            sort_date = sort_date or event_date
            event['_sort_key'] = (
                sort_date.timestamp() if sort_date else 0,
                priority,
            )
            events.append(event)

        sales = LedgerTransaction.objects.filter(
            farmer=farmer,
        ).filter(
            Q(tags__icontains=split_tag) |
            Q(tags__icontains=legacy_split_tag)
        ).filter(
            Q(category__in=['Sales', 'Harvest Trade']) |
            Q(status__in=['Received', 'Income', 'Settled', 'Paid', 'Pending'])
        ).exclude(
            category='Harvest Trade',
            description__istartswith='Harvest Trade Settlement',
        ).order_by('-close_date', '-id')

        has_harvest_sale = False
        for sale in sales:
            description = sale.description or ''
            if (
                sale.category == 'Harvest Trade'
                and description.lower().startswith('harvest trade settlement')
            ):
                continue

            amount = float(sale.amount or 0)
            quantity = float(sale.quantity) if sale.quantity else None
            unit = sale.unit or split.expected_yield_unit or 'MT'
            is_harvest = (
                sale.category == 'Harvest Trade'
                or 'harvest trade settlement' in description.lower()
                or (
                    'harvest' in description.lower()
                    and not description.lower().startswith('sale:')
                )
            )
            if quantity is None and is_harvest and split.yield_tons:
                quantity = float(split.yield_tons)
            has_harvest_sale = has_harvest_sale or (
                sale.category == 'Harvest Trade'
                or 'harvest trade settlement' in description.lower()
            )

            paid_match = re.search(
                r'\[Paid:\s*₹?([\d.,]+),\s*Pending:\s*₹?([\d.,]+)\]',
                description,
            )
            if paid_match:
                paid = float(paid_match.group(1).replace(',', ''))
                pending = float(paid_match.group(2).replace(',', ''))
            elif sale.status in ['Received', 'Paid', 'Income', 'Settled']:
                paid, pending = amount, 0
            elif sale.status == 'Pending':
                paid, pending = 0, amount
            else:
                paid, pending = amount, 0

            title = (
                re.sub(r'Harvest Trade Settlement\s*-\s*', '', description, flags=re.I).strip()
                if is_harvest
                else re.sub(r'\[Paid:[^\]]+\]', '', description, flags=re.I).strip()
            )
            sale_date = (
                datetime.combine(sale.close_date, time.min)
                if sale.close_date
                else sale.created_at
            )
            add_event({
                'id': sale.id,
                'type': 'harvest' if is_harvest else 'sale',
                'event_label': 'Harvest Output' if is_harvest else 'Customer Sale',
                'title': title or (split.crop.name if split.crop else 'Crop'),
                'description': description,
                'date': sale_date.isoformat() if sale_date else '',
                'date_label': sale.close_date.strftime('%d %b %Y') if sale.close_date else 'Recently',
                'status': sale.status or '',
                'quantity': quantity,
                'unit': unit,
                'amount': amount,
                'paid': paid,
                'pending': pending,
                'payment_method': sale.payment_method or 'Cash',
                'operator_name': '',
            }, sale_date, 4 if not is_harvest else 3,
                sort_date=sale.created_at or sale_date)

        activities = FarmActivity.objects.filter(farm=split.farm).filter(
            Q(notes__icontains=split_tag) |
            Q(notes__icontains=legacy_split_tag)
        ).order_by('created_at', 'id')
        crop_name = split.crop.name if split.crop else 'Crop'
        has_sowing_event = False
        previous_stage_event = None

        for activity in activities:
            notes = activity.notes or ''
            if (
                has_harvest_sale
                and 'finalized with yield' in notes.lower()
            ):
                continue

            asset = activity.asset_name or ''
            is_stage_change = (
                'Phase Update' in asset
                or 'stage updated to' in notes.lower()
                or 'stage set to' in notes.lower()
            )
            is_sowing = (
                activity.activity_type == 'Seeds Sowing'
                or 'sowing' in notes.lower()
                or 'initial crop' in notes.lower()
            )

            if is_stage_change:
                event_key = (asset, notes, activity.operator_name or '')
                event_time = activity.created_at.timestamp() if activity.created_at else 0
                if (
                    previous_stage_event
                    and previous_stage_event[0] == event_key
                    and event_time - previous_stage_event[1] <= 5
                ):
                    continue
                previous_stage_event = (event_key, event_time)

                stage_match = re.search(
                    r"stage (?:updated to|set to) '([^']+)'",
                    notes,
                    flags=re.I,
                )
                stage_name = stage_match.group(1) if stage_match else 'Phase Update'
                event_type = 'stage'
                title = f"{crop_name} Stage Updated to '{stage_name}'"
            elif is_sowing:
                has_sowing_event = True
                event_type = 'sowing'
                title = 'Initial Sowing'
            else:
                event_type = 'activity'
                title = asset or f'{crop_name} Operation'

            add_event({
                'id': activity.id,
                'type': event_type,
                'event_label': (
                    'Stage Update' if event_type == 'stage'
                    else 'Initial Sowing' if event_type == 'sowing'
                    else activity.activity_type or 'Land Operation'
                ),
                'title': title,
                'description': notes,
                'date': activity.created_at.isoformat() if activity.created_at else '',
                'date_label': activity.created_at.strftime('%Y-%m-%d') if activity.created_at else 'Recently',
                'status': '',
                'quantity': None,
                'unit': '',
                'amount': None,
                'paid': None,
                'pending': None,
                'payment_method': '',
                'operator_name': activity.operator_name or '',
            }, activity.created_at, 2 if event_type in ['stage', 'sowing'] else 1,
                sort_date=activity.created_at)

        tasks = FarmTask.objects.filter(associated_split=split).select_related(
            'assigned_worker', 'task_category'
        ).prefetch_related('assigned_workers').order_by('-created_at', '-id')
        for task in tasks:
            task_date = task.created_at or (
                datetime.combine(task.start_date, time.min) if task.start_date else None
            )
            date_label = (
                task.start_date.strftime('%Y-%m-%d')
                if task.start_date
                else task.created_at.strftime('%Y-%m-%d') if task.created_at else 'Recently'
            )
            description = task.description or (
                f'{task.task_category.name} operation recorded for {crop_name}.'
                if task.task_category else 'Task checklist operation.'
            )
            assigned_workers = list(task.assigned_workers.all())
            if not assigned_workers and task.assigned_worker:
                assigned_workers = [task.assigned_worker]
            add_event({
                'id': task.id,
                'type': 'task',
                'event_label': 'Task',
                'title': task.title,
                'description': description,
                'date': task_date.isoformat() if task_date else '',
                'date_label': date_label,
                'status': task.status or '',
                'quantity': None,
                'unit': '',
                'amount': None,
                'paid': None,
                'pending': None,
                'payment_method': '',
                'operator_name': ', '.join(worker.name for worker in assigned_workers),
            }, task_date, 1, sort_date=task.created_at or task_date)

        if not has_sowing_event and split.start_date:
            sowing_date = datetime.combine(split.start_date, time.min)
            supervisors = split.supervisor_names
            add_event({
                'id': f'split-{split.id}-sowing',
                'type': 'sowing',
                'event_label': 'Initial Sowing',
                'title': 'Initial Sowing',
                'description': (
                    f"Crop split established in Sown stage "
                    f"({split.cultivation_method or 'Standard'} cultivation method)."
                ),
                'date': split.start_date.isoformat(),
                'date_label': split.start_date.strftime('%Y-%m-%d'),
                'status': '',
                'quantity': float(split.acreage) if split.acreage else None,
                'unit': 'Acres',
                'amount': None,
                'paid': None,
                'pending': None,
                'payment_method': '',
                'operator_name': ', '.join(supervisors),
            }, sowing_date, 2, sort_date=split.created_at or sowing_date)

        events.sort(key=lambda event: event['_sort_key'], reverse=True)
        for event in events:
            del event['_sort_key']
        return Response({'results': events})

    @swagger_auto_schema(method='post', tags=['Mobile - 04. Crop Splits & Cultivation'], operation_description="Mark the harvested crop as Sold, create ledger entry and handle outstanding balances")
    @action(detail=True, methods=['post'])
    def sell(self, request, pk=None):
        import datetime as dt
        from decimal import Decimal
        from apps.farmer.models import FarmerCustomer, FarmerOutstanding, LedgerTransaction
        from apps.farmer.harvest_batches import normalize_batch_numbers
        split = self.get_object()
        farmer_prof = get_farmer_profile(request.user)

        yield_tons = float(request.data.get('yield_tons', 0) or 0)
        price_per_mt = float(request.data.get('price_per_mt', 0) or 0)
        total_sale_value = float(request.data.get('total_sale_value', 0) or 0)
        customer_name = request.data.get('customer_name') or 'Direct Customer'
        paid_amount_raw = request.data.get('paid_amount')
        paid_amount = float(paid_amount_raw or 0)
        direct_customer_name = str(
            request.data.get('direct_customer_name') or ''
        ).strip()
        direct_customer_phone = str(
            request.data.get('direct_customer_phone') or ''
        ).strip()
        if customer_name == 'Direct Customer' and direct_customer_name:
            customer_name = direct_customer_name
        batch_index_str = request.data.get('batch_index')
        unit = request.data.get('unit') or split.expected_yield_unit or 'MT'
        quality = request.data.get('quality') or 'Grade 1'
        payment_method = request.data.get('payment_method') or 'Cash'
        payment_status = request.data.get('payment_status') or ('Received' if paid_amount > 0 else 'Pending')

        total_f = total_sale_value if total_sale_value > 0 else (yield_tons * price_per_mt)
        paid_f = (
            total_f
            if paid_amount_raw in (None, '')
            else paid_amount
        )

        crop_name = split.crop.name if split.crop else 'Crop'

        batches = normalize_batch_numbers(split.harvest_batches)

        if batch_index_str is not None and batch_index_str != '':
            if not batches and float(split.yield_tons or 0) > 0:
                available_split_yield = float(split.yield_tons)
                batches = [{
                    'batch_no': 1,
                    'flush_no': split.current_flush or 1,
                    'yield': available_split_yield,
                    'unit': unit,
                    'quality': quality,
                    'price': float(split.price_per_mt or price_per_mt),
                    'saleValue': float(
                        split.total_sale_value
                        or available_split_yield * float(split.price_per_mt or price_per_mt)
                    ),
                    'status': 'Ready',
                }]
            try:
                batch_index = int(batch_index_str)
                batch = batches[batch_index]
                available = float(batch.get('yield') or 0)
            except (ValueError, TypeError, IndexError):
                return Response(
                    {'error': 'This batch is no longer available. Refresh and try again.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if batch_index < 0 or batch.get('status') in ('Sold', 'Loss / Discard'):
                return Response(
                    {'error': 'This batch is no longer available. Refresh and try again.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if yield_tons <= 0 or yield_tons > available:
                return Response(
                    {
                        'error': (
                            'Quantity sold cannot exceed the available batch '
                            f'quantity of {available:g} {unit}.'
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            original_yield = available
            original_value = float(batch.get('saleValue') or 0)
            sale_price = price_per_mt if price_per_mt > 0 else float(batch.get('price') or 0)
            pending_amount = max(0, round(total_f - paid_f, 2))
            sale_date = dt.date.today().strftime('%d %b %Y')
            sale_fields = {
                'sold_to': customer_name,
                'sold_at_price': sale_price,
                'sold_total': total_f,
                'paid': paid_f,
                'pending': pending_amount,
                'payment_method': payment_method,
                'payment_status': payment_status,
                'sold_date': sale_date,
            }

            if yield_tons < original_yield:
                remaining_yield = round(original_yield - yield_tons, 2)
                remaining_batch = dict(batch)
                batch['yield'] = yield_tons
                batch['status'] = 'Sold'
                batch.update(sale_fields)

                base_price = float(batch.get('price') or 0) or sale_price
                remaining_batch.update({
                    'yield': remaining_yield,
                    'unit': batch.get('unit', unit),
                    'price': batch.get('price', sale_price),
                    'priceUnit': batch.get('priceUnit', batch.get('unit', unit)),
                    'saleValue': (
                        round(original_value * remaining_yield / original_yield, 2)
                        if original_value
                        else round(remaining_yield * base_price, 2)
                    ),
                    'quality': batch.get('quality', quality),
                    'status': 'Ready',
                    'flush_no': batch.get('flush_no', 1),
                })
                if original_value:
                    batch['saleValue'] = round(
                        original_value * yield_tons / original_yield,
                        2,
                    )
                for field in sale_fields:
                    remaining_batch.pop(field, None)
                batches.insert(batch_index + 1, remaining_batch)
            else:
                batch['yield'] = original_yield
                batch['status'] = 'Sold'
                batch.update(sale_fields)

            split.harvest_batches = batches

        if batches:
            all_sold = all(b.get('status') == 'Sold' for b in batches)
            split.stage = 'Sold' if all_sold else 'Harvested'
            ready_yield = sum(
                float(batch.get('yield') or 0)
                for batch in batches
                if batch.get('status') not in ('Sold', 'Loss / Discard')
            )
            split.yield_tons = max(
                Decimal('0'),
                Decimal(str(round(ready_yield, 2))),
            )
        else:
            split.stage = 'Sold'
            split.yield_tons = Decimal('0')

        split.save()

        if total_f > 0:
            from datetime import date
            customer_instance = FarmerCustomer.objects.filter(farmer=farmer_prof, name=customer_name).first()
            if customer_instance is None and direct_customer_name:
                customer_instance = FarmerCustomer.objects.create(
                    farmer=farmer_prof,
                    name=customer_name,
                    contact_number=direct_customer_phone or None,
                )
            elif customer_instance is not None and direct_customer_phone:
                customer_instance.contact_number = direct_customer_phone
                customer_instance.save(update_fields=['contact_number'])

            LedgerTransaction.objects.create(
                farmer=farmer_prof,
                farm=split.farm,
                close_date=date.today(),
                description=f"Sale: {crop_name} ({quality}) to {customer_name}",
                payment_method=payment_method,
                category="Sales",
                category_color="#28a745" if payment_status in ['Received', 'Paid', 'Settled'] else "#6c757d",
                tags=f"{crop_name} Sale ({quality}) - CropSplit #{split.id}",
                status=payment_status,
                amount=paid_f if paid_f > 0 else total_f,
                quantity=yield_tons if yield_tons > 0 else None,
                unit=unit
            )

            if total_f > paid_f:
                FarmerOutstanding.objects.create(
                    farmer=farmer_prof,
                    customer=customer_instance,
                    invoice_no=f"Sale: {crop_name} - {customer_name}",
                    due_date=date.today(),
                    amount_total=total_f,
                    amount_paid=paid_f,
                    status='Partial' if paid_f > 0 else 'Unpaid'
                )

        return Response({
            'success': True,
            'sale': {
                'description': f"Sale: {crop_name} ({quality}) to {customer_name}",
                'date_formatted': dt.date.today().strftime('%d %b %Y'),
                'status': payment_status,
                'quantity': yield_tons,
                'unit': unit,
                'amount': total_f,
                'payment_method': payment_method
            },
            'batches': batches,
            'stage': split.stage
        })

@method_decorator(name='list', decorator=swagger_auto_schema(tags=['Mobile - 06. Resource Consumption'], manual_parameters=[farm_id_param, activity_type_param, order_param]))
@method_decorator(name='create', decorator=swagger_auto_schema(tags=['Mobile - 06. Resource Consumption']))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 06. Resource Consumption']))
@method_decorator(name='update', decorator=swagger_auto_schema(tags=['Mobile - 06. Resource Consumption']))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(tags=['Mobile - 06. Resource Consumption']))
@method_decorator(name='destroy', decorator=swagger_auto_schema(tags=['Mobile - 06. Resource Consumption']))
class FarmActivityViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = FarmActivitySerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        farm_id = self.request.query_params.get('farm_id')
        activity_type = self.request.query_params.get('activity_type')
        order = self.request.query_params.get('order', 'desc').lower()
        qs = FarmActivity.objects.filter(farm__farmer=get_farmer_profile(self.request.user))
        if farm_id:
            qs = qs.filter(farm_id=farm_id)
        if activity_type:
            qs = qs.filter(activity_type__iexact=activity_type)

        if order == 'asc':
            qs = qs.order_by('created_at', 'id')
        else:
            qs = qs.order_by('-created_at', '-id')

        return qs

@method_decorator(name='list', decorator=swagger_auto_schema(manual_parameters=[farm_id_param], tags=['Mobile - 06. Resource Consumption']))
@method_decorator(name='create', decorator=swagger_auto_schema(tags=['Mobile - 06. Resource Consumption']))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 06. Resource Consumption']))
@method_decorator(name='update', decorator=swagger_auto_schema(tags=['Mobile - 06. Resource Consumption']))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(tags=['Mobile - 06. Resource Consumption']))
@method_decorator(name='destroy', decorator=swagger_auto_schema(tags=['Mobile - 06. Resource Consumption']))
class ConsumptionViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = FarmActivitySerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        farm_id = self.request.query_params.get('farm_id')
        crop_split_id = self.request.query_params.get('crop_split_id')
        qs = FarmActivity.objects.filter(farm__farmer=get_farmer_profile(self.request.user), activity_type="Resource Consumption")
        if farm_id:
            qs = qs.filter(farm_id=farm_id)
        if crop_split_id:
            from django.db.models import Q
            qs = qs.filter(
                Q(notes__icontains=f"CropSplit #{crop_split_id}") |
                Q(notes__icontains=f"Crop Split ID: {crop_split_id}") |
                Q(asset_name__icontains=f"CropSplit #{crop_split_id}")
            )
        return qs.order_by('-created_at', '-id')

    def perform_create(self, serializer):
        inv_item_id = self.request.data.get('inventory_item')
        crop_split_id = self.request.data.get('crop_split_id')
        if crop_split_id:
            from apps.farmer.models import CropSplit
            split = CropSplit.objects.filter(
                id=crop_split_id,
                farm__farmer=get_farmer_profile(self.request.user),
            ).first()
            if split and split.stage.lower() == 'inactive':
                raise serializers.ValidationError(
                    {'crop_split_id': 'Inactive crops are view-only and cannot receive consumption records.'}
                )
        activity = serializer.save(activity_type="Resource Consumption")

        if inv_item_id:
            try:
                from apps.farmer.models import FarmerInventory
                inv_item = FarmerInventory.objects.filter(id=inv_item_id, farmer=get_farmer_profile(self.request.user)).first()
                if inv_item:
                    qty_val = self._consumption_quantity()
                    if qty_val > 0:
                        inv_item.quantity = max(0, inv_item.quantity - qty_val)
                        if inv_item.quantity <= 0:
                            inv_item.quantity = 0
                            inv_item.status = 'Out of Stock'
                        elif inv_item.quantity <= inv_item.safety_stock:
                            inv_item.status = 'Low Stock'
                        else:
                            inv_item.status = 'In Stock'
                        inv_item.save()
            except Exception:
                pass

        if crop_split_id:
            try:
                from apps.farmer.models import CropSplit
                split = CropSplit.objects.filter(id=crop_split_id, farm=activity.farm).first()
                if split and f"CropSplit #{crop_split_id}" not in (activity.notes or ''):
                    base = (activity.notes or '').strip(' |')
                    suffix = f"CropSplit #{crop_split_id} ({split.crop.name if split.crop else 'Crop'})"
                    activity.notes = f"{base} | {suffix}".strip(' |')
                    activity.save(update_fields=['notes'])
            except Exception:
                pass

    def _consumption_quantity(self):
        """Quantity used for the stock decrement.

        The web form posts a plain ``quantity`` number; mobile posts the whole
        ``quantity_metric`` string (e.g. "5 kg"), so fall back to its number.
        """
        raw = self.request.data.get('quantity')
        if raw in (None, ''):
            raw = self.request.data.get('quantity_metric') or ''
            import re
            match = re.match(r'\s*(-?\d+(?:\.\d+)?)', str(raw))
            raw = match.group(1) if match else 0
        try:
            return float(raw or 0)
        except (TypeError, ValueError):
            return 0.0


@method_decorator(name='list', decorator=swagger_auto_schema(tags=['Mobile - 03. Farm & Land Management'], manual_parameters=[farm_id_param, status_param, status_in_param, priority_param, worker_id_param, worker_name_param, task_search_param, date_from_param, date_to_param]))
@method_decorator(name='create', decorator=swagger_auto_schema(tags=['Mobile - 03. Farm & Land Management']))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 03. Farm & Land Management']))
@method_decorator(name='update', decorator=swagger_auto_schema(tags=['Mobile - 03. Farm & Land Management']))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(tags=['Mobile - 03. Farm & Land Management']))
@method_decorator(name='destroy', decorator=swagger_auto_schema(tags=['Mobile - 03. Farm & Land Management']))
class FarmTaskViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = FarmTaskSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        farm_id = self.request.query_params.get('farm_id')
        crop_split_id = self.request.query_params.get('crop_split_id')
        status = self.request.query_params.get('status')
        status_in = self.request.query_params.get('status_in')
        priority = self.request.query_params.get('priority')
        worker_id = self.request.query_params.get('worker_id')
        worker_name = self.request.query_params.get('worker_name')
        search = self.request.query_params.get('search')
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')

        user_farmer = get_farmer_profile(self.request.user)
        # Base queryset: user's tasks + tasks on farms/splits user has access to
        if crop_split_id:
            # When crop_split_id is specified, include all tasks for that split
            # (user has access to the crop split via farm membership)
            qs = FarmTask.objects.filter(
                Q(farmer=user_farmer) | Q(associated_split_id=crop_split_id, farm_id=farm_id)
            )
        else:
            qs = FarmTask.objects.filter(farmer=user_farmer)
        if farm_id:
            qs = qs.filter(farm_id=farm_id)
        if crop_split_id:
            qs = qs.filter(associated_split_id=crop_split_id)
        if status:
            qs = qs.filter(status__iexact=status)
        if status_in:
            statuses = [s.strip() for s in status_in.split(',') if s.strip()]
            if statuses:
                status_q = Q()
                for s in statuses:
                    status_q |= Q(status__iexact=s)
                qs = qs.filter(status_q)
        if priority:
            qs = qs.filter(priority__iexact=priority)
        if worker_id:
            qs = qs.filter(Q(assigned_worker_id=worker_id) | Q(assigned_workers__id=worker_id)).distinct()
        if worker_name:
            qs = qs.filter(
                Q(assigned_worker__name__icontains=worker_name)
                | Q(assigned_workers__name__icontains=worker_name)
            ).distinct()
        if search:
            qs = qs.filter(
                Q(title__icontains=search) | Q(description__icontains=search)
            )
        if date_from or date_to:
            from django.db.models.functions import Coalesce
            qs = qs.annotate(
                span_start=Coalesce('start_date', 'task_date'),
                span_end=Coalesce('end_date', 'start_date', 'task_date'),
            )
            if date_from:
                qs = qs.filter(span_end__gte=date_from)
            if date_to:
                qs = qs.filter(span_start__lte=date_to)
        return qs.order_by('-task_date', 'start_time', 'id')

    def perform_create(self, serializer):
        crop_split_id = self.request.data.get('crop_split_id')
        if crop_split_id:
            split = CropSplit.objects.filter(
                id=crop_split_id,
                farm__farmer=get_farmer_profile(self.request.user),
            ).first()
            if split and split.stage.lower() == 'inactive':
                raise serializers.ValidationError(
                    {'crop_split_id': 'Inactive crops are view-only and cannot receive tasks.'}
                )
        task = serializer.save(farmer=get_farmer_profile(self.request.user))
        if task.assigned_worker and task.work_timeline in ['Half Day', 'Full Day']:
            from apps.farmer.models import StaffAttendance
            from datetime import date
            task_date = task.task_date if task.task_date else date.today()
            att_record = StaffAttendance.objects.filter(staff=task.assigned_worker, date=task_date).first()
            if not att_record or att_record.status in ['A', '-', '']:
                status_to_mark = 'H' if task.work_timeline == 'Half Day' else 'P'
                StaffAttendance.objects.update_or_create(
                    staff=task.assigned_worker,
                    date=task_date,
                    defaults={'status': status_to_mark}
                )
            elif att_record.status == 'H':
                att_record.status = 'P'
                att_record.save()

    def create(self, request, *args, **kwargs):
        """Create one FarmTask per date in a recurrence schedule."""
        from datetime import date, time, timedelta

        payload = request.data.copy()
        recurrence_type = str(payload.pop('recurrence_type', 'Single Day')).strip()
        selected_dates = payload.pop('selected_dates', [])
        selected_weekdays = payload.pop('selected_weekdays', [])

        if isinstance(selected_dates, str):
            try:
                selected_dates = json.loads(selected_dates)
            except json.JSONDecodeError:
                selected_dates = [value.strip() for value in selected_dates.split(',')]
        if isinstance(selected_weekdays, str):
            try:
                selected_weekdays = json.loads(selected_weekdays)
            except json.JSONDecodeError:
                selected_weekdays = [
                    value.strip() for value in selected_weekdays.split(',')
                ]

        if recurrence_type not in {'Single Day', 'Daily', 'Weekly'}:
            return Response(
                {'detail': 'recurrence_type must be Single Day, Daily, or Weekly.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        def parse_date(value, field_name):
            try:
                return date.fromisoformat(str(value))
            except (TypeError, ValueError):
                raise ValueError(f'{field_name} must use YYYY-MM-DD format.')

        def parse_time(value, field_name):
            try:
                hour, minute = str(value).split(':')[:2]
                return time(int(hour), int(minute))
            except (TypeError, ValueError):
                raise ValueError(f'{field_name} must use HH:MM format.')

        try:
            raw_start_date = payload.get('start_date') or payload.get('task_date')
            start_date = parse_date(raw_start_date, 'start_date')
            end_date = parse_date(
                payload.get('end_date') or raw_start_date,
                'end_date',
            )
            if end_date < start_date:
                raise ValueError('end_date must be greater than or equal to start_date.')

            default_start = parse_time(payload.get('start_time'), 'start_time')
            default_end = parse_time(payload.get('end_time'), 'end_time')
            if default_end <= default_start:
                raise ValueError('end_time must be later than start_time.')

            schedules = []
            if recurrence_type == 'Single Day':
                schedules = [(start_date, default_start, default_end)]
            elif recurrence_type == 'Daily':
                if not isinstance(selected_dates, list) or not selected_dates:
                    raise ValueError('Select at least one date for a daily task.')
                unique_dates = set()
                for raw_date in selected_dates:
                    scheduled_date = parse_date(raw_date, 'selected_dates')
                    if scheduled_date < start_date or scheduled_date > end_date:
                        raise ValueError('Every selected date must be inside the date range.')
                    unique_dates.add(scheduled_date)
                for scheduled_date in sorted(unique_dates):
                    schedules.append((scheduled_date, default_start, default_end))
            else:
                if not isinstance(selected_weekdays, list) or not selected_weekdays:
                    raise ValueError('Select at least one weekday for a weekly task.')
                requested_weekdays = selected_weekdays
                selected_weekdays = set()
                for raw_day in requested_weekdays:
                    weekday = int(raw_day)
                    if weekday < 0 or weekday > 6:
                        raise ValueError('Weekdays must be numbered from 0 (Monday) to 6 (Sunday).')
                    selected_weekdays.add(weekday)
                current = start_date
                while current <= end_date:
                    if current.weekday() in selected_weekdays:
                        schedules.append((current, default_start, default_end))
                    current += timedelta(days=1)

            if not schedules:
                raise ValueError('The recurrence selection did not produce any task dates.')
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        payload['start_date'] = schedules[0][0].isoformat()
        payload['end_date'] = schedules[0][0].isoformat()
        payload['task_date'] = schedules[0][0].isoformat()
        payload['start_time'] = schedules[0][1].strftime('%H:%M')
        payload['end_time'] = schedules[0][2].strftime('%H:%M')
        payload.pop('total_hours', None)

        with transaction.atomic():
            created = []
            for scheduled_date, task_start, task_end in schedules:
                task_payload = payload.copy()
                task_payload.update({
                    'task_date': scheduled_date.isoformat(),
                    'start_date': scheduled_date.isoformat(),
                    'end_date': scheduled_date.isoformat(),
                    'start_time': task_start.strftime('%H:%M'),
                    'end_time': task_end.strftime('%H:%M'),
                    'total_hours': round(
                        (task_end.hour * 60 + task_end.minute -
                         task_start.hour * 60 - task_start.minute) / 60,
                        2,
                    ),
                })
                serializer = self.get_serializer(data=task_payload)
                serializer.is_valid(raise_exception=True)
                task = serializer.save(farmer=get_farmer_profile(request.user))
                assigned_workers = task_payload.get('assigned_workers')
                if assigned_workers:
                    task.assigned_workers.set(assigned_workers)
                if task.assigned_worker and task.work_timeline in ['Half Day', 'Full Day']:
                    from apps.farmer.models import StaffAttendance
                    attendance, _ = StaffAttendance.objects.get_or_create(
                        staff=task.assigned_worker,
                        date=task.task_date,
                        defaults={
                            'status': 'H' if task.work_timeline == 'Half Day' else 'P',
                        },
                    )
                    if attendance.status == 'H' and task.work_timeline == 'Full Day':
                        attendance.status = 'P'
                        attendance.save(update_fields=['status'])
                created.append(task)

        return Response(
            {
                'message': f'{len(created)} task(s) created successfully.',
                'count': len(created),
                'tasks': self.get_serializer(created, many=True).data,
            },
            status=status.HTTP_201_CREATED,
        )

    def _record_crop_task_status_activity(self, task, previous_status):
        split = task.associated_split
        old_status = str(previous_status or '').strip()
        new_status = str(task.status or '').strip()
        if split is None or old_status == new_status:
            return

        manager = _get_user_staff_assignment(
            self.request.user,
            manager_only=True,
        )
        operator_name = (
            manager.name
            if manager
            else self.request.user.first_name
            or self.request.user.get_username()
        )
        FarmActivity.objects.create(
            farm=split.farm,
            activity_type='Other Land Care',
            asset_name='Crop Task Status Updated',
            quantity_metric='',
            operator_name=operator_name,
            notes=(
                f"Task '{task.title}' status changed from '{old_status}' "
                f"to '{new_status}'. | CropSplit #{split.id}."
            ),
        )

    def perform_update(self, serializer):
        previous_status = serializer.instance.status
        with transaction.atomic():
            task = serializer.save()
            self._record_crop_task_status_activity(task, previous_status)
        if task.assigned_worker and task.work_timeline in ['Half Day', 'Full Day']:
            from apps.farmer.models import StaffAttendance
            from datetime import date
            task_date = task.task_date if task.task_date else date.today()
            att_record = StaffAttendance.objects.filter(staff=task.assigned_worker, date=task_date).first()
            if not att_record or att_record.status in ['A', '-', '']:
                status_to_mark = 'H' if task.work_timeline == 'Half Day' else 'P'
                StaffAttendance.objects.update_or_create(
                    staff=task.assigned_worker,
                    date=task_date,
                    defaults={'status': status_to_mark}
                )
            elif att_record.status == 'H':
                att_record.status = 'P'
                att_record.save()

from apps.farmer.models import LedgerTransaction, FarmerInventory
from apps.farmer.serializers import LedgerTransactionSerializer, FarmerInventorySerializer

tag_param = openapi.Parameter('tag', openapi.IN_QUERY, description="Filter by Tag", type=openapi.TYPE_STRING)
type_param = openapi.Parameter('type', openapi.IN_QUERY, description="Filter by type: 'inflow' (Sales/Income) or 'outflow' (Cost/Expense)", type=openapi.TYPE_STRING)
farm_name_param = openapi.Parameter('farm_name', openapi.IN_QUERY, description="Filter by farm land name (icontains)", type=openapi.TYPE_STRING)
date_filter_param = openapi.Parameter('date_filter', openapi.IN_QUERY, description="Preset close_date filter: this_month, last_month, this_year", type=openapi.TYPE_STRING)
tx_search_param = openapi.Parameter('search', openapi.IN_QUERY, description="Search in description, category, tags, farm land name and vendor/buyer name (icontains)", type=openapi.TYPE_STRING)

@method_decorator(name='list', decorator=swagger_auto_schema(manual_parameters=[farm_id_param, farm_name_param, category_param, tag_param, type_param, date_filter_param, tx_search_param], tags=['Mobile - 08. Ledger & Transactions']))
@method_decorator(name='create', decorator=swagger_auto_schema(tags=['Mobile - 08. Ledger & Transactions']))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 08. Ledger & Transactions']))
@method_decorator(name='update', decorator=swagger_auto_schema(tags=['Mobile - 08. Ledger & Transactions']))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(tags=['Mobile - 08. Ledger & Transactions']))
@method_decorator(name='destroy', decorator=swagger_auto_schema(tags=['Mobile - 08. Ledger & Transactions']))
class LedgerTransactionViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = LedgerTransactionSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        from django.db.models import Q
        from datetime import date, timedelta
        category = self.request.query_params.get('category')
        farm_id = self.request.query_params.get('farm_id')
        farm_name = self.request.query_params.get('farm_name')
        crop_split_id = self.request.query_params.get('crop_split_id')
        search = self.request.query_params.get('search')
        tag = self.request.query_params.get('tag')
        transaction_type = self.request.query_params.get('type')
        date_filter = self.request.query_params.get('date_filter')
        view = self.request.query_params.get('view')

        if view == 'sales':
            # Website parity: financials_sales inflow list (views.financials_sales)
            # tags Inflow | amount>0 | status Received, excluding 'Customer Payment',
            # ordered by -close_date, -created_at, -id.
            qs = (
                LedgerTransaction.objects
                .filter(farmer=get_farmer_profile(self.request.user))
                .filter(Q(tags__icontains='Inflow') | Q(amount__gt=0) | Q(status='Received'))
                .exclude(category='Customer Payment')
                .exclude(category__iexact='Harvest Trade')
                .distinct()
                .order_by('-close_date', '-created_at', '-id')
            )
        else:
            qs = LedgerTransaction.objects.filter(farmer=get_farmer_profile(self.request.user)).order_by('-created_at', '-id')

        if transaction_type == 'inflow':
            qs = qs.exclude(status='Cost')
        elif transaction_type == 'outflow':
            qs = qs.filter(status='Cost')

        if category:
            qs = qs.filter(category__iexact=category)
        if farm_id:
            qs = qs.filter(farm_id=farm_id)
        if farm_name:
            qs = qs.filter(farm__land_name__icontains=farm_name)
        if crop_split_id:
            qs = qs.filter(
                Q(tags__icontains=f"CropSplit #{crop_split_id}") |
                Q(tags__icontains=f"crop split id: {crop_split_id}") |
                Q(description__icontains=f"CropSplit #{crop_split_id}")
            )
        if tag:
            qs = qs.filter(tags__icontains=tag)
        if search:
            qs = qs.filter(
                Q(description__icontains=search)
                | Q(category__icontains=search)
                | Q(tags__icontains=search)
                | Q(farm__land_name__icontains=search)
                | Q(vendor__vendor_name__icontains=search)
            )
        if date_filter:
            today = date.today()
            if date_filter == 'this_month':
                start = today.replace(day=1)
                qs = qs.filter(close_date__gte=start)
            elif date_filter == 'last_month':
                first_of_this = today.replace(day=1)
                last_of_prev = first_of_this - timedelta(days=1)
                start = last_of_prev.replace(day=1)
                qs = qs.filter(close_date__gte=start, close_date__lte=last_of_prev)
            elif date_filter == 'this_year':
                qs = qs.filter(close_date__year=today.year)
        return qs

    def perform_create(self, serializer):
        from django.db import transaction
        from apps.farmer.models import FarmerInventory, InventoryStockLog, CropSplit
        import datetime

        profile = get_farmer_profile(self.request.user)
        add_to_inventory = serializer.validated_data.get('add_to_inventory', False)
        crop_split_id = self.request.data.get('crop_split_id')
        quantity = self.request.data.get('quantity', 1)
        unit = str(self.request.data.get('unit') or 'Units').strip() or 'Units'
        description = str(self.request.data.get('description') or '').strip()

        try:
            inventory_quantity = float(quantity or 1)
        except (TypeError, ValueError):
            inventory_quantity = 1
        if inventory_quantity <= 0:
            inventory_quantity = 1

        with transaction.atomic():
            transaction_instance = serializer.save(farmer=profile)
            if not add_to_inventory:
                return

            if not description:
                raise serializers.ValidationError(
                    {'description': 'A description is required when adding to inventory.'}
                )

            farm = transaction_instance.farm
            crop_split = None
            if crop_split_id:
                crop_split = CropSplit.objects.filter(
                    id=crop_split_id,
                    farm__farmer=profile,
                ).first()
                if crop_split is None:
                    raise serializers.ValidationError(
                        {'crop_split_id': 'Selected crop split was not found.'}
                    )
                farm = crop_split.farm

            category = transaction_instance.category
            if category in ['Vendor', 'Rental', 'General', 'Others']:
                category = 'Store Supply'

            existing = FarmerInventory.objects.filter(
                farmer=profile,
                item_name__iexact=description,
            ).first()
            old_quantity = float(existing.quantity or 0) if existing else 0
            new_quantity = int(old_quantity + inventory_quantity)
            unit_price = abs(float(transaction_instance.amount or 0)) / max(
                inventory_quantity,
                1.0,
            )

            if existing:
                existing.quantity = new_quantity
                if existing.unit in ['', 'Units']:
                    existing.unit = unit
                existing.farm = farm or existing.farm
                existing.save(update_fields=['quantity', 'unit', 'farm'])
                inventory_item = existing
            else:
                inventory_item = FarmerInventory.objects.create(
                    farmer=profile,
                    farm=farm,
                    item_name=description,
                    category=category,
                    quantity=new_quantity,
                    unit=unit,
                    unit_price=round(unit_price, 2),
                    total_price=round(abs(float(transaction_instance.amount or 0)), 2),
                    purchase_date=transaction_instance.close_date
                    or datetime.date.today(),
                )

            InventoryStockLog.objects.create(
                inventory=inventory_item,
                farm=farm,
                crop_split=crop_split,
                action='add',
                quantity=inventory_quantity,
                notes=f"Purchased via Expense ({transaction_instance.category})",
                log_date=transaction_instance.close_date or datetime.date.today(),
                qty_before=old_quantity,
                qty_after=new_quantity,
            )

    @swagger_auto_schema(method='get', tags=['Mobile - 09. Sales Management'], operation_description="Get sales summary/stats for dashboard cards")
    @action(detail=False, methods=['get'])
    def summary(self, request):
        from django.db.models import Sum, Count, Q
        from django.db.models.functions import Abs
        from datetime import date

        farmer = get_farmer_profile(request.user)
        base = LedgerTransaction.objects.filter(farmer=farmer)

        # Website parity (views.financials_sales): same inflow/outflow definitions.
        inflow = (
            base.filter(Q(tags__icontains='Inflow') | Q(amount__gt=0) | Q(status='Received'))
            .exclude(category='Customer Payment')
            .distinct()
        )
        outflow = base.filter(
            Q(tags__icontains='Outflow') | Q(amount__lt=0) | Q(status__in=['Paid', 'Cost'])
        ).distinct()

        # KPI cards (website financials_sales.html)
        total_inflow = inflow.aggregate(total=Sum(Abs('amount')))['total'] or 0
        total_outflow = outflow.aggregate(total=Sum(Abs('amount')))['total'] or 0
        net_profit = total_inflow - total_outflow
        margin = (net_profit / total_inflow * 100) if total_inflow > 0 else 0

        # This month is based on close_date on the website, not created_at.
        month_start = date.today().replace(day=1)
        month_qs = inflow.filter(close_date__gte=month_start)
        this_month_inflow = month_qs.aggregate(total=Sum(Abs('amount')))['total'] or 0
        this_month_count = month_qs.count()

        # Outstanding (customer receivables from FarmerOutstanding, web parity:
        # views.outstanding / views.sales_dashboard exclude Vendor: and Paid)
        from apps.farmer.models import FarmerOutstanding
        cust_outs = FarmerOutstanding.objects.filter(
            farmer=farmer
        ).exclude(invoice_no__startswith='Vendor:').exclude(status='Paid')
        outstanding_total = sum(
            max(0.0, float(o.amount_total) - float(o.amount_paid)) for o in cust_outs
        )
        outstanding_count = cust_outs.count()

        # Categories breakdown
        categories = list(
            inflow.values('category')
            .annotate(count=Count('id'), total=Sum(Abs('amount')))
            .order_by('-total')
        )

        # Farms breakdown
        farms = list(
            inflow.values('farm__land_name')
            .annotate(count=Count('id'), total=Sum(Abs('amount')))
            .order_by('-total')[:10]
        )

        return Response({
            'total_inflow': float(total_inflow),
            'inflow_count': inflow.count(),
            'total_outflow': float(total_outflow),
            'outflow_count': outflow.count(),
            'net_profit': float(net_profit),
            'margin': round(float(margin), 1),
            'this_month_inflow': float(this_month_inflow),
            'this_month_count': this_month_count,
            'this_month_inflow_count': this_month_count,
            'outstanding_total': float(outstanding_total),
            'outstanding_count': outstanding_count,
            'categories': categories,
            'farms': farms,
        })

@method_decorator(name='list', decorator=swagger_auto_schema(manual_parameters=[farm_id_param, farm_name_param, category_param, date_filter_param, tx_search_param], tags=['Mobile - 07. Expenses Management']))
@method_decorator(name='create', decorator=swagger_auto_schema(tags=['Mobile - 07. Expenses Management']))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 07. Expenses Management']))
@method_decorator(name='update', decorator=swagger_auto_schema(tags=['Mobile - 07. Expenses Management']))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(tags=['Mobile - 07. Expenses Management']))
@method_decorator(name='destroy', decorator=swagger_auto_schema(tags=['Mobile - 07. Expenses Management']))
class FarmExpenseViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = LedgerTransactionSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        from django.db.models import Q
        from datetime import date, timedelta
        farm_id = self.request.query_params.get('farm_id')
        farm_name = self.request.query_params.get('farm_name')
        category = self.request.query_params.get('category')
        search = self.request.query_params.get('search')
        date_filter = self.request.query_params.get('date_filter')
        crop_split_id = self.request.query_params.get('crop_split_id')
        qs = LedgerTransaction.objects.filter(
            farmer=get_farmer_profile(self.request.user),
            status__in=["Cost", "Pending"],
        ).order_by('-close_date', '-created_at', '-id')
        if farm_id:
            qs = qs.filter(farm_id=farm_id)
        if farm_name:
            qs = qs.filter(farm__land_name__icontains=farm_name)
        if category:
            qs = qs.filter(category__iexact=category)
        if search:
            qs = qs.filter(
                Q(description__icontains=search)
                | Q(category__icontains=search)
                | Q(tags__icontains=search)
                | Q(farm__land_name__icontains=search)
                | Q(vendor__vendor_name__icontains=search)
            )
        if date_filter:
            today = date.today()
            if date_filter == 'this_month':
                qs = qs.filter(close_date__gte=today.replace(day=1))
            elif date_filter == 'last_month':
                first_of_this = today.replace(day=1)
                last_of_prev = first_of_this - timedelta(days=1)
                qs = qs.filter(
                    close_date__gte=last_of_prev.replace(day=1),
                    close_date__lte=last_of_prev,
                )
            elif date_filter == 'this_year':
                qs = qs.filter(close_date__year=today.year)
        if crop_split_id:
            qs = qs.filter(
                Q(tags__icontains=f"CropSplit #{crop_split_id}") |
                Q(tags__icontains=f"crop split id: {crop_split_id}") |
                Q(description__icontains=f"CropSplit #{crop_split_id}")
            )
        return qs

    def perform_create(self, serializer):
        crop_split_id = self.request.data.get('crop_split_id')
        extra_fields = {}

        if crop_split_id:
            from apps.farmer.models import CropSplit
            split = CropSplit.objects.filter(
                id=crop_split_id,
                farm__farmer=get_farmer_profile(self.request.user),
            ).first()
            if split and split.stage.lower() == 'inactive':
                raise serializers.ValidationError(
                    {'crop_split_id': 'Inactive crops are view-only and cannot receive expenses.'}
                )

        tags = serializer.validated_data.get('tags') or "Outflow"
        if crop_split_id:
            tags = f"{tags} | CropSplit #{crop_split_id}"
        extra_fields['tags'] = tags

        balance_amount = self.request.data.get('balance_amount')
        if balance_amount is not None:
            extra_fields['balance_due'] = float(balance_amount)

        transaction = serializer.save(farmer=get_farmer_profile(self.request.user), status="Cost", **extra_fields)

        # ── Add expense item to inventory ───────────────────────────
        add_to_inventory = serializer.validated_data.get('add_to_inventory', False)
        if add_to_inventory:
            from apps.farmer.models import FarmerInventory, InventoryStockLog
            from datetime import date

            description = str(transaction.description or '').strip()
            if description:
                try:
                    inventory_quantity = float(
                        self.request.data.get('quantity', 1) or 1
                    )
                except (TypeError, ValueError):
                    inventory_quantity = 1
                inventory_quantity = max(inventory_quantity, 1)
                unit = str(self.request.data.get('unit') or 'Units').strip() or 'Units'
                farm = transaction.farm
                crop_split = None
                if crop_split_id:
                    from apps.farmer.models import CropSplit
                    crop_split = CropSplit.objects.filter(
                        id=crop_split_id,
                        farm__farmer=get_farmer_profile(self.request.user),
                    ).first()
                    if crop_split:
                        farm = crop_split.farm

                category = transaction.category
                if category in ['Vendor', 'Rental', 'General', 'Others']:
                    category = 'Store Supply'
                inventory_item = FarmerInventory.objects.filter(
                    farmer=get_farmer_profile(self.request.user),
                    item_name__iexact=description,
                ).first()
                old_quantity = (
                    float(inventory_item.quantity or 0)
                    if inventory_item else 0
                )
                new_quantity = int(old_quantity + inventory_quantity)
                unit_price = abs(float(transaction.amount or 0)) / max(
                    inventory_quantity,
                    1,
                )

                if inventory_item:
                    inventory_item.quantity = new_quantity
                    if inventory_item.unit in ['', 'Units']:
                        inventory_item.unit = unit
                    if farm:
                        inventory_item.farm = farm
                    inventory_item.save(update_fields=['quantity', 'unit', 'farm'])
                else:
                    inventory_item = FarmerInventory.objects.create(
                        farmer=get_farmer_profile(self.request.user),
                        farm=farm,
                        item_name=description,
                        category=category,
                        quantity=new_quantity,
                        unit=unit,
                        unit_price=round(unit_price, 2),
                        total_price=round(abs(float(transaction.amount or 0)), 2),
                        purchase_date=transaction.close_date or date.today(),
                    )

                InventoryStockLog.objects.create(
                    inventory=inventory_item,
                    farm=farm,
                    crop_split=crop_split,
                    action='add',
                    quantity=inventory_quantity,
                    notes=f"Purchased via Expense ({transaction.category})",
                    log_date=transaction.close_date or date.today(),
                    qty_before=old_quantity,
                    qty_after=new_quantity,
                )

        # ── Document upload ─────────────────────────────────────────
        document_file = self.request.FILES.get('document_file')
        document_type = self.request.data.get('document_type', 'My Expenses')
        if document_file:
            import os
            from apps.superadmin.models import Document
            ext = os.path.splitext(document_file.name)[1].lower()
            doc = Document.objects.create(
                title=f"Expense Receipt - {transaction.description[:50]}",
                category=document_type,
                description=(
                    f"Uploaded receipt/bill for expense logged on {transaction.close_date}. "
                    f"Description: {transaction.description}"
                ),
                file=document_file,
                file_size_bytes=document_file.size,
                file_type=ext,
                uploaded_by=self.request.user,
                farmer=get_farmer_profile(self.request.user),
            )
            transaction.tags = f"{transaction.tags} | Doc ID: {doc.id}"
            transaction.save(update_fields=['tags'])

        balance_amount_val = float(transaction.balance_due or 0)
        vendor_value = self.request.data.get('vendor') or self.request.data.get('vendor_name_text')

        if vendor_value and balance_amount_val > 0:
            from apps.farmer.models import FarmerOutstanding, FarmerVendor
            if isinstance(vendor_value, int):
                vendor_instance = FarmerVendor.objects.filter(id=vendor_value, farmer=get_farmer_profile(self.request.user)).first()
            else:
                vendor_instance = FarmerVendor.objects.filter(farmer=get_farmer_profile(self.request.user), vendor_name=vendor_value).first()
                if not vendor_instance:
                    vendor_instance = FarmerVendor.objects.create(farmer=get_farmer_profile(self.request.user), vendor_name=vendor_value)

            FarmerOutstanding.objects.create(
                farmer=get_farmer_profile(self.request.user),
                invoice_no=f"Vendor: {vendor_value} - {transaction.description}",
                due_date=transaction.close_date,
                amount_total=balance_amount_val,
                amount_paid=0,
                status='Unpaid'
            )

    @swagger_auto_schema(method='get', tags=['Mobile - 07. Expenses Management'], operation_description="Get expense summary/stats for dashboard cards")
    @action(detail=False, methods=['get'])
    def summary(self, request):
        from django.db.models import Sum, Count, Q
        from datetime import datetime, timedelta

        farmer = get_farmer_profile(request.user)
        qs = LedgerTransaction.objects.filter(farmer=farmer, status__in=["Cost", "Pending"])

        # Total cost
        total_cost = qs.aggregate(total=Sum('amount'))['total'] or 0

        # This month cost
        now = datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        this_month = qs.filter(created_at__gte=month_start)
        this_month_cost = this_month.aggregate(total=Sum('amount'))['total'] or 0
        this_month_count = this_month.count()

        # Outstanding (payables tracked on the expense transactions themselves;
        # LedgerTransaction field is `balance_due` — `balance_amount` is not a model field)
        outstanding = qs.exclude(balance_due=0).exclude(balance_due__isnull=True)
        outstanding_total = outstanding.aggregate(total=Sum('balance_due'))['total'] or 0
        outstanding_count = outstanding.count()

        # Categories breakdown
        categories = list(qs.values('category').annotate(count=Count('id'), total=Sum('amount')).order_by('-total'))

        # Farms breakdown
        farms = list(qs.values('farm__land_name').annotate(count=Count('id'), total=Sum('amount')).order_by('-total')[:10])

        return Response({
            'total_cost': float(total_cost),
            'this_month_cost': float(this_month_cost),
            'this_month_count': this_month_count,
            'outstanding_total': float(outstanding_total),
            'outstanding_count': outstanding_count,
            'categories': categories,
            'farms': farms,
        })

inventory_search_param = openapi.Parameter('search', openapi.IN_QUERY, description="Search by item name or license plate", type=openapi.TYPE_STRING)
inventory_tab_param = openapi.Parameter('tab', openapi.IN_QUERY, description="Split tab: warehouse or machinery", type=openapi.TYPE_STRING)
inventory_category_param = openapi.Parameter('category', openapi.IN_QUERY, description="Filter by exact category name", type=openapi.TYPE_STRING)
inventory_status_param = openapi.Parameter('status', openapi.IN_QUERY, description="Filter by exact status", type=openapi.TYPE_STRING)

@method_decorator(name='list', decorator=swagger_auto_schema(tags=['Mobile - 14. Inventory Management'], manual_parameters=[farm_id_param, inventory_search_param, inventory_tab_param, inventory_category_param, inventory_status_param]))
@method_decorator(name='create', decorator=swagger_auto_schema(tags=['Mobile - 14. Inventory Management']))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 14. Inventory Management']))
@method_decorator(name='update', decorator=swagger_auto_schema(tags=['Mobile - 14. Inventory Management']))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(tags=['Mobile - 14. Inventory Management']))
@method_decorator(name='destroy', decorator=swagger_auto_schema(tags=['Mobile - 14. Inventory Management']))
class FarmerInventoryViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = FarmerInventorySerializer
    pagination_class = StandardResultsSetPagination

    def _machinery_q(self):
        """Same machinery/tools matching used by website inventory view (apps/farmer/views.py inventory())."""
        from apps.superadmin.models import InventoryCategory
        machinery_tool_cats = list(InventoryCategory.objects.filter(
            layout_type__in=['Machinery', 'Tools', 'Machinery & Tools', 'Vehicles', 'Vehicle'],
            is_active=True
        ).values_list('name', flat=True))
        default_machinery_tool_names = [
            'Vehicles & Fleet', 'Tool & Implements', 'Vehicles', 'Tools', 'Machinery',
            'Machinery & Tools', 'Equipment', 'Implement', 'Tractor', 'Fleet', 'Tool', 'Vehicle'
        ]
        all_mach_tool_names = list(set(machinery_tool_cats + default_machinery_tool_names))

        mach_q = Q(category__in=all_mach_tool_names)
        for name in ['machinery', 'vehicle', 'fleet', 'tool', 'equipment', 'implement', 'tractor']:
            mach_q |= Q(category__icontains=name)
        mach_q |= Q(rental_available=True)
        mach_q |= (Q(license_plate__isnull=False) & ~Q(license_plate=''))
        return mach_q

    def get_queryset(self):
        farm_id = self.request.query_params.get('farm_id')
        category = self.request.query_params.get('category')
        status = self.request.query_params.get('status')
        search = self.request.query_params.get('search')
        tab = (self.request.query_params.get('tab') or '').lower()
        qs = FarmerInventory.objects.filter(farmer=get_farmer_profile(self.request.user))
        if farm_id:
            qs = qs.filter(farm_id=farm_id)
        if category:
            qs = qs.filter(category__iexact=category)
        if status:
            qs = qs.filter(status__iexact=status)
        if search:
            qs = qs.filter(
                Q(item_name__icontains=search) | Q(license_plate__icontains=search)
            )
        # Website parity: split into Warehouse vs Machinery / Tools tabs (views.py inventory())
        if tab in ('machinery', 'vehicle', 'vehicles', 'machinery_tools'):
            qs = qs.filter(self._machinery_q())
        elif tab == 'warehouse':
            qs = qs.exclude(self._machinery_q())
        return qs.order_by('-created_at', '-id')

    def create(self, request, *args, **kwargs):
        expense_value = request.data.get('record_as_expense', '')
        vendor_value = request.data.get('vendor_id')
        if isinstance(expense_value, (list, tuple)):
            expense_value = expense_value[0] if expense_value else ''
        if isinstance(vendor_value, (list, tuple)):
            vendor_value = vendor_value[0] if vendor_value else None
        self._record_as_expense = str(expense_value).lower() in {
            'true', '1', 'on'
        }
        self._vendor_id = vendor_value
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED,
            headers=headers,
        )

    def perform_create(self, serializer):
        profile = get_farmer_profile(self.request.user)
        vendor_id = self._vendor_id
        vendor = None
        if vendor_id:
            vendor = FarmerVendor.objects.filter(
                id=vendor_id,
                farmer=profile,
            ).first()
            if vendor is None:
                raise ValidationError({'vendor_id': 'Invalid vendor.'})

        from apps.farmer.models import LedgerTransaction
        from django.utils import timezone
        with transaction.atomic():
            item = serializer.save(farmer=profile, vendor=vendor)
            if not self._record_as_expense:
                return

            try:
                total_cost = float(item.quantity or 0) * float(item.unit_price or 0)
            except (TypeError, ValueError):
                total_cost = 0
            if total_cost <= 0:
                return

            purchase_date = item.purchase_date or timezone.now().date()
            LedgerTransaction.objects.create(
                farmer=profile,
                farm=None,
                vendor=vendor,
                close_date=purchase_date,
                description=(
                    f'Inventory Purchase - {item.item_name} '
                    f'({item.quantity} {item.unit})'
                    f'{f" from {vendor.vendor_name}" if vendor else ""}'
                ),
                payment_method='Cash',
                category=item.category or 'Inventory',
                category_color='#f1732a',
                tags=f'Outflow | Inventory: {item.item_name}',
                status='Cost',
                amount=-abs(total_cost),
                balance_due=0,
                quantity=item.quantity,
                unit=item.unit,
            )

    @swagger_auto_schema(tags=['Mobile - 14. Inventory Management'],
        operation_description="Inventory stats matching website inventory page: warehouse (count, low stock, total value, barn locations) and machinery/tools (fleet count, operational, maintenance, tools)")
    @action(detail=False, methods=['get'])
    def stats(self, request):
        from django.db.models import F, Sum
        profile = get_farmer_profile(request.user)
        inventory_items = FarmerInventory.objects.filter(farmer=profile)
        mach_q = self._machinery_q()

        vehicles = inventory_items.filter(mach_q)
        warehouse = inventory_items.exclude(id__in=vehicles.values_list('id', flat=True))

        low_stock_items = warehouse.filter(quantity__lte=F('safety_stock'), quantity__gt=0).count()
        barn_locations = warehouse.values('location').distinct().count()

        total_val = warehouse.aggregate(total=Sum('total_price'))['total'] or 0
        total_inventory_value = round(float(total_val), 2)

        fleet_q = (Q(category__icontains='vehicle') | Q(category__icontains='fleet') |
                   Q(category__icontains='machinery') | (Q(license_plate__isnull=False) & ~Q(license_plate='')))
        total_vehicles = vehicles.filter(fleet_q).count()
        total_tools = vehicles.exclude(fleet_q).count()
        operational_count = vehicles.filter(status__in=['Operational', 'Available']).count()
        maintenance_count = vehicles.filter(status__in=['Maintenance', 'Needs Repair', 'In Maintenance']).count()

        return Response({
            'warehouse': {
                'count': warehouse.count(),
                'low_stock_items': low_stock_items,
                'barn_locations': barn_locations,
                'total_inventory_value': total_inventory_value,
            },
            'machinery': {
                'count': vehicles.count(),
                'total_vehicles': total_vehicles,
                'total_tools': total_tools,
                'operational_count': operational_count,
                'maintenance_count': maintenance_count,
            },
        })

    @swagger_auto_schema(tags=['Mobile - 14. Inventory Management'],
        operation_description="Toggle rental availability (matches website toggle_inventory_rental)",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['item_id', 'rental_available'],
            properties={
                'item_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                'rental_available': openapi.Schema(type=openapi.TYPE_BOOLEAN),
            }
        ))
    @action(detail=False, methods=['post'])
    def toggle_rental(self, request):
        item_id = request.data.get('item_id') or request.data.get('id')
        if not item_id:
            return Response({'error': 'item_id is required.'}, status=400)
        rental_available = str(request.data.get('rental_available', '')).lower() in ['true', '1', 'on']
        try:
            item = FarmerInventory.objects.get(id=item_id, farmer=get_farmer_profile(request.user))
        except FarmerInventory.DoesNotExist:
            return Response({'error': 'Inventory item not found.'}, status=404)

        item.rental_available = rental_available
        if rental_available and item.status not in ['Available', 'Booked', 'Rented', 'Maintenance']:
            item.status = 'Available'
        elif not rental_available and item.status in ['Available', 'Booked', 'Rented']:
            item.status = 'Operational'
        item.save()
        return Response({
            'success': True,
            'rental_available': item.rental_available,
            'status': item.status,
            'message': f"Rental {'enabled' if rental_available else 'disabled'} successfully",
        })

    @swagger_auto_schema(tags=['Mobile - 14. Inventory Management'],
        operation_description="Update operational status of a machinery/tools item (matches website update_inventory_status)",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['item_id', 'status'],
            properties={
                'item_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                'status': openapi.Schema(type=openapi.TYPE_STRING),
            }
        ))
    @action(detail=False, methods=['post'])
    def update_status(self, request):
        item_id = request.data.get('item_id') or request.data.get('id')
        new_status = request.data.get('status')
        if not item_id or not new_status:
            return Response({'error': 'item_id and status are required.'}, status=400)
        try:
            item = FarmerInventory.objects.get(id=item_id, farmer=get_farmer_profile(request.user))
        except FarmerInventory.DoesNotExist:
            return Response({'error': 'Inventory item not found.'}, status=404)
        item.status = new_status
        item.save()
        return Response({'success': True, 'status': item.status, 'message': f"Status updated to '{new_status}'"})

    @swagger_auto_schema(tags=['Mobile - 14. Inventory Management'],
        operation_description="Record a stock log entry (In/Out) for an inventory item",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['item_id', 'action', 'quantity', 'log_date'],
            properties={
                'item_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                'action': openapi.Schema(type=openapi.TYPE_STRING, description="'add' for In, 'remove' for Out"),
                'quantity': openapi.Schema(type=openapi.TYPE_NUMBER),
                'notes': openapi.Schema(type=openapi.TYPE_STRING),
                'log_date': openapi.Schema(type=openapi.TYPE_STRING, format='date'),
            }
        ))
    @action(detail=False, methods=['post'])
    def stock_log(self, request):
        from apps.farmer.models import InventoryStockLog
        import datetime
        item_id = request.data.get('item_id')
        action = request.data.get('action')
        quantity = request.data.get('quantity', 0)
        notes = request.data.get('notes', '').strip()
        log_date = request.data.get('log_date', '')

        if not item_id or not action:
            return Response({'error': 'item_id and action are required.'}, status=400)

        try:
            qty = float(quantity)
        except (ValueError, TypeError):
            return Response({'error': 'Invalid quantity.'}, status=400)

        try:
            item = FarmerInventory.objects.get(id=item_id, farmer=get_farmer_profile(request.user))
        except FarmerInventory.DoesNotExist:
            return Response({'error': 'Inventory item not found.'}, status=404)

        old_qty = float(item.quantity or 0)
        if action == 'add':
            item.quantity = old_qty + qty
        elif action == 'remove':
            if qty > old_qty:
                return Response({'error': f'Cannot deduct {qty} {item.unit}. Only {old_qty} {item.unit} available.'}, status=400)
            item.quantity = old_qty - qty
        else:
            return Response({'error': "action must be 'add' or 'remove'."}, status=400)

        new_qty = float(item.quantity)
        item.save()

        InventoryStockLog.objects.create(
            inventory=item,
            action=action,
            quantity=qty,
            notes=notes,
            log_date=log_date or datetime.date.today(),
            qty_before=old_qty,
            qty_after=new_qty,
        )
        return Response({'success': True, 'new_quantity': new_qty, 'unit': item.unit})

    @swagger_auto_schema(tags=['Mobile - 14. Inventory Management'],
        operation_description="Get stock log history for an inventory item",
        manual_parameters=[
            openapi.Parameter('item_id', openapi.IN_QUERY, description="Inventory Item ID", type=openapi.TYPE_INTEGER, required=True)
        ])
    @action(detail=False, methods=['get'])
    def stock_logs(self, request):
        from apps.farmer.models import InventoryStockLog
        item_id = request.query_params.get('item_id')
        if not item_id:
            return Response({'error': 'item_id query parameter is required.'}, status=400)
        try:
            item = FarmerInventory.objects.get(id=item_id, farmer=get_farmer_profile(request.user))
        except FarmerInventory.DoesNotExist:
            return Response({'error': 'Inventory item not found.'}, status=404)

        logs = InventoryStockLog.objects.filter(inventory=item).order_by('-log_date', '-created_at')[:50]
        data = [{
            'action': log.action,
            'quantity': log.quantity,
            'unit': item.unit,
            'notes': log.notes or '',
            'log_date': log.log_date.strftime('%d %b %Y') if log.log_date else '',
            'qty_before': log.qty_before,
            'qty_after': log.qty_after,
        } for log in logs]
        return Response({
            'item_name': item.item_name,
            'unit': item.unit,
            'current_qty': float(item.quantity),
            'logs': data,
        })


class QuickAddLocationAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Storage Location',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['name'],
            properties={'name': openapi.Schema(type=openapi.TYPE_STRING)},
        ),
        responses={200: openapi.Response('Success')},
        tags=['Mobile - 14. Inventory Management'],
    )
    def post(self, request):
        from apps.superadmin.models import StorageLocation
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'error': 'Location name is required.'}, status=status.HTTP_400_BAD_REQUEST)
        location, _ = StorageLocation.objects.get_or_create(name=name, defaults={'is_active': True})
        return Response({'success': True, 'id': location.id, 'name': location.name})


class QuickAddInventoryTypeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Inventory Type',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['name'],
            properties={'name': openapi.Schema(type=openapi.TYPE_STRING)},
        ),
        responses={200: openapi.Response('Success')},
        tags=['Mobile - 14. Inventory Management'],
    )
    def post(self, request):
        from apps.superadmin.models import ProductType
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'error': 'Inventory Type name is required.'}, status=status.HTTP_400_BAD_REQUEST)
        inv_type, _ = ProductType.objects.get_or_create(name=name, defaults={'is_active': True})
        return Response({'success': True, 'id': inv_type.id, 'name': inv_type.name})


class QuickAddInventoryCategoryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Inventory Category',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['name'],
            properties={
                'name': openapi.Schema(type=openapi.TYPE_STRING),
                'layout_type': openapi.Schema(type=openapi.TYPE_STRING, description='Warehouse, Machinery, or Tools'),
            },
        ),
        responses={200: openapi.Response('Success')},
        tags=['Mobile - 14. Inventory Management'],
    )
    def post(self, request):
        from apps.superadmin.models import InventoryCategory
        name = request.data.get('name', '').strip()
        layout_type = request.data.get('layout_type', 'Warehouse').strip()
        if not name:
            return Response({'error': 'Category name is required.'}, status=status.HTTP_400_BAD_REQUEST)
        category, _ = InventoryCategory.objects.get_or_create(
            name=name, defaults={'layout_type': layout_type, 'is_active': True}
        )
        return Response({'success': True, 'id': category.id, 'name': category.name, 'layout_type': category.layout_type})


class InventoryDropdownOptionsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Inventory Dropdown Options',
        operation_description='Returns units, inventory types, locations, and categories for the Add/Edit Inventory form.',
        responses={200: openapi.Response('Success')},
        tags=['Mobile - 14. Inventory Management'],
    )
    def get(self, request):
        from apps.superadmin.models import ProductType, StorageLocation, InventoryCategory, ExpenseUnit
        units_qs = FarmerInventory.objects.filter(farmer=get_farmer_profile(request.user)).values_list('unit', flat=True).distinct()
        units = sorted(set(u for u in units_qs if u))
        expense_units = list(ExpenseUnit.objects.filter(is_active=True).values_list('name', flat=True))
        default_units = ['Units', 'Bags', 'Kg', 'Quintal', 'Litres', 'Tonnes', 'Packs']
        all_units = sorted(set(units + expense_units + default_units))

        types = sorted(set(ProductType.objects.filter(is_active=True).values_list('name', flat=True)))
        locations = sorted(set(StorageLocation.objects.filter(is_active=True).values_list('name', flat=True)))
        default_locations = ['Main Warehouse', 'Field Storage', 'Tool Shed', 'Machinery Bay', 'Other']
        all_locations = sorted(set(locations + default_locations))

        categories = list(InventoryCategory.objects.filter(is_active=True).values('id', 'name', 'layout_type').order_by('name'))
        vendors = list(FarmerVendor.objects.filter(
            farmer=get_farmer_profile(request.user)
        ).order_by('vendor_name').values(
            'id', 'vendor_name', 'business_name'
        ))

        return Response({
            'units': all_units,
            'inventory_types': types,
            'locations': all_locations,
            'categories': categories,
            'vendors': vendors,
        })


from apps.farmer.models import FarmerStaff, StaffAttendance, StaffAdvance, StaffPayroll
from apps.farmer.serializers import (
    FarmerStaffSerializer, StaffAttendanceSerializer, StaffAdvanceSerializer, StaffPayrollSerializer
)

role_param = openapi.Parameter('role', openapi.IN_QUERY, description="Filter by Role", type=openapi.TYPE_STRING)
staff_search_param = openapi.Parameter('search', openapi.IN_QUERY, description="Search staff by name or mobile number (icontains)", type=openapi.TYPE_STRING)
staff_status_param = openapi.Parameter('status', openapi.IN_QUERY, description="Filter by staff status code (e.g. 0 = Active, 1 = Inactive)", type=openapi.TYPE_STRING)

@method_decorator(name='list', decorator=swagger_auto_schema(manual_parameters=[role_param, staff_search_param, staff_status_param], tags=['Mobile - 09. Employee Management']))
@method_decorator(name='create', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
@method_decorator(name='update', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
@method_decorator(name='destroy', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
class FarmerStaffViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = FarmerStaffSerializer
    pagination_class = StandardResultsSetPagination
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def get_queryset(self):
        role = self.request.query_params.get('role')
        status_param = self.request.query_params.get('status')
        search = self.request.query_params.get('search')
        qs = FarmerStaff.objects.filter(farmer=get_farmer_profile(self.request.user)).order_by('id')
        if role:
            qs = qs.filter(role__iexact=role)
        if status_param:
            qs = qs.filter(status__iexact=status_param)
        if search:
            from django.db.models import Q
            qs = qs.filter(Q(name__icontains=search) | Q(mobile_number__icontains=search))
        return qs

    def perform_create(self, serializer):
        """
        Mirrors website add_staff_api business logic:
        - Derive role from is_role_manager (Manager / Employee)
        - Auto-create Django User (username = mobile, unusable password) for OTP login
        - Link Profile mobile + role for new users; sync is_active from status
        """
        is_manager = serializer.validated_data.get('is_role_manager', False)
        farmer = get_farmer_profile(self.request.user)
        self._validate_manager_limit(farmer, is_manager)
        role = "Manager" if is_manager else "Employee"
        status_val = str(serializer.validated_data.get('status', '0'))
        mobile = (serializer.validated_data.get('mobile_number') or '').strip()

        user_obj = None
        if mobile:
            user_obj = User.objects.filter(username=mobile).first()
            if not user_obj:
                user_obj = User.objects.create_user(username=mobile)
                user_obj.set_unusable_password()
                user_obj.save()

                if hasattr(user_obj, 'profile'):
                    user_obj.profile.mobile_number = mobile
                    user_obj.profile.role = 'staff'
                    user_obj.profile.save()

            user_obj.is_active = (status_val != "1")
            user_obj.save()

        serializer.save(
            farmer=farmer,
            role=role,
            user=user_obj,
        )

    def perform_update(self, serializer):
        """
        Mirrors website update_staff_api business logic:
        - Derive role from is_role_manager
        - Keep User.username in sync with mobile for OTP login
        - Sync is_active from status; lazily create/link User if missing
        """
        is_manager = serializer.validated_data.get('is_role_manager', False)
        self._validate_manager_limit(
            get_farmer_profile(self.request.user),
            is_manager,
            exclude_id=serializer.instance.id,
        )
        role = "Manager" if is_manager else "Employee"
        status_val = str(serializer.validated_data.get('status', serializer.instance.status))
        mobile = (serializer.validated_data.get('mobile_number') or serializer.instance.mobile_number or '').strip()

        staff = serializer.instance
        user_to_save = None

        if staff.user:
            if mobile and mobile != staff.user.username:
                if not User.objects.filter(username=mobile).exclude(id=staff.user.id).exists():
                    staff.user.username = mobile
            staff.user.is_active = (status_val != "1")
            staff.user.save()
            user_to_save = staff.user
        elif mobile:
            user_obj = User.objects.filter(username=mobile).first()
            if not user_obj:
                user_obj = User.objects.create_user(username=mobile)
                user_obj.set_unusable_password()
                user_obj.save()
            user_obj.is_active = (status_val != "1")
            user_obj.save()
            user_to_save = user_obj

        save_kwargs = {'role': role}
        if user_to_save is not None:
            save_kwargs['user'] = user_to_save
        serializer.save(**save_kwargs)

    def _validate_manager_limit(self, farmer, is_manager, exclude_id=None):
        if not is_manager:
            return

        subscription = Subscription.objects.filter(
            farmer=farmer,
            subscription_status='active',
        ).order_by('-created_at').first()
        package = subscription.package if subscription else None
        raw_limit = str(package.my_users or '').strip() if package else ''
        if not raw_limit or raw_limit.lower() == 'unlimited':
            return

        try:
            manager_limit = int(float(raw_limit))
        except (TypeError, ValueError):
            return

        managers = FarmerStaff.objects.filter(
            farmer=farmer,
            is_role_manager=True,
        )
        if exclude_id is not None:
            managers = managers.exclude(id=exclude_id)
        if managers.count() >= manager_limit:
            raise ValidationError({
                'is_role_manager': (
                    'Manager access limit reached. Upgrade your subscription '
                    'to grant manager access to another user.'
                )
            })

@method_decorator(name='list', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management'], manual_parameters=[staff_id_param]))
@method_decorator(name='create', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
@method_decorator(name='update', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
@method_decorator(name='destroy', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
class StaffAttendanceViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = StaffAttendanceSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        staff_id = self.request.query_params.get('staff_id') or self.request.query_params.get('staff')
        qs = StaffAttendance.objects.filter(staff__farmer=get_farmer_profile(self.request.user))
        if staff_id:
            qs = qs.filter(staff_id=staff_id)
        return qs

    def create(self, request, *args, **kwargs):
        staff_id = request.data.get('staff')
        date = request.data.get('date')
        status = request.data.get('status')
        notes = request.data.get('notes', '')

        if not staff_id or not date or not status:
            return Response({'error': 'staff, date, and status are required fields.'}, status=400)

        from apps.farmer.models import FarmerStaff
        try:
            staff = FarmerStaff.objects.get(id=staff_id, farmer=get_farmer_profile(request.user))
        except FarmerStaff.DoesNotExist:
            return Response({'error': 'Invalid staff ID or permission denied.'}, status=400)

        attendance, created = StaffAttendance.objects.update_or_create(
            staff=staff,
            date=date,
            defaults={'status': status}
        )

        serializer = self.get_serializer(attendance)
        return Response(serializer.data, status=201 if created else 200)

    @swagger_auto_schema(
        method='get',
        operation_summary='Get Monthly Attendance Grid',
        operation_description='Returns a grid of all employees and their daily attendance status (P, A, H, -) for the requested month. Equivalent to the web Labor Force Daily Sign-In table.',
        tags=['Mobile - 09. Employee Management'],
        manual_parameters=[
            openapi.Parameter('month', openapi.IN_QUERY, description="Filter by Month (1-12)", type=openapi.TYPE_INTEGER),
            openapi.Parameter('year', openapi.IN_QUERY, description="Filter by Year (e.g., 2026)", type=openapi.TYPE_INTEGER)
        ]
    )
    @action(detail=False, methods=['get'])
    def monthly_grid(self, request):
        from datetime import date, timedelta
        import calendar
        from apps.farmer.models import FarmerStaff, StaffAttendance

        month_param = request.query_params.get('month')
        year_param = request.query_params.get('year')
        today = date.today()

        if month_param and year_param:
            try:
                target_date = date(int(year_param), int(month_param), 1)
            except ValueError:
                target_date = today.replace(day=1)
        else:
            target_date = today.replace(day=1)

        _, last_day = calendar.monthrange(target_date.year, target_date.month)

        days = []
        for i in range(last_day):
            d = target_date + timedelta(days=i)
            days.append({
                "label": d.strftime("%b %d"),
                "key": d.strftime("%Y-%m-%d"),
                "date_obj": d,
                "is_future": d > today
            })

        staff_members = FarmerStaff.objects.filter(farmer=get_farmer_profile(request.user))
        attend_employees = []

        for staff in staff_members:
            attendances = []
            # Optimization: pre-fetch all attendances for this staff in the month
            monthly_att = StaffAttendance.objects.filter(
                staff=staff,
                date__gte=target_date,
                date__lte=target_date + timedelta(days=last_day-1)
            )
            att_dict = {a.date.strftime("%Y-%m-%d"): a.status for a in monthly_att}

            for d in days:
                status = att_dict.get(d['key'], "-")
                attendances.append({
                    "status": status,
                    "date": d['key'],
                    "is_future": d['is_future']
                })

            attend_employees.append({
                "id": staff.id,
                "name": staff.name,
                "role": staff.role,
                "attendance": attendances
            })

        return Response({
            "month_name": target_date.strftime("%B %Y").upper(),
            "month": target_date.month,
            "year": target_date.year,
            "days": [{"label": d["label"], "key": d["key"], "is_future": d["is_future"]} for d in days],
            "employees": attend_employees
        })

    @swagger_auto_schema(
        method='post',
        operation_summary='Bulk Update Attendance for a Date',
        operation_description='Mark all active staff attendance for a given date (P, A, H, or -)',
        tags=['Mobile - 09. Employee Management'],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['date', 'status'],
            properties={
                'date': openapi.Schema(type=openapi.TYPE_STRING, description='Date YYYY-MM-DD'),
                'status': openapi.Schema(type=openapi.TYPE_STRING, description='P, A, H, or -'),
            },
        ))
    @action(detail=False, methods=['post'])
    def bulk_update(self, request):
        from datetime import date as date_cls
        from apps.farmer.models import FarmerStaff

        date_str = request.data.get('date')
        status_val = request.data.get('status', '-')

        if not date_str:
            return Response({'error': 'date is required'}, status=400)

        try:
            target_date = date_cls.fromisoformat(date_str)
        except ValueError:
            return Response({'error': 'Invalid date format'}, status=400)

        staff_members = FarmerStaff.objects.filter(farmer=get_farmer_profile(request.user), status='0')
        count = 0
        for staff in staff_members:
            if status_val == '-':
                StaffAttendance.objects.filter(staff=staff, date=target_date).delete()
            else:
                StaffAttendance.objects.update_or_create(
                    staff=staff, date=target_date,
                    defaults={'status': status_val}
                )
            count += 1

        return Response({'success': True, 'count': count})

@method_decorator(name='list', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management'], manual_parameters=[staff_id_param]))
@method_decorator(name='create', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
@method_decorator(name='update', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
@method_decorator(name='destroy', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
class StaffAdvanceViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = StaffAdvanceSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        staff_id = self.request.query_params.get('staff_id') or self.request.query_params.get('staff')
        qs = StaffAdvance.objects.filter(staff__farmer=get_farmer_profile(self.request.user)).order_by('-date')
        if staff_id:
            qs = qs.filter(staff_id=staff_id)
        return qs

@method_decorator(name='list', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management'], manual_parameters=[staff_id_param]))
@method_decorator(name='create', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
@method_decorator(name='update', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
@method_decorator(name='destroy', decorator=swagger_auto_schema(tags=['Mobile - 09. Employee Management']))
class StaffPayrollViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = StaffPayrollSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        staff_id = self.request.query_params.get('staff_id') or self.request.query_params.get('staff')
        qs = StaffPayroll.objects.filter(staff__farmer=get_farmer_profile(self.request.user)).order_by('-paid_on')
        if staff_id:
            qs = qs.filter(staff_id=staff_id)
        return qs

    @action(detail=False, methods=['get'])
    def run_sheet(self, request):
        from datetime import date
        from apps.farmer.models import FarmerStaff, StaffAttendance, StaffAdvance

        farmer = get_farmer_profile(request.user)
        staff_members = FarmerStaff.objects.filter(farmer=farmer).order_by('name')
        current_month_str = date.today().strftime("%B %Y").upper()

        sheet = []
        for staff in staff_members:
            is_suspended = (str(staff.status) == "1")
            last_payroll = StaffPayroll.objects.filter(staff=staff).order_by('-paid_on').first()
            curr_month_att = StaffAttendance.objects.filter(staff=staff, is_paid=False)

            present_days = curr_month_att.filter(status='P').count()
            half_days = curr_month_att.filter(status='H').count()
            absent_days_count = curr_month_att.filter(status='A').count()

            total_worked = present_days + (half_days * 0.5)
            absent_days = absent_days_count + (half_days * 0.5)

            gross = float(staff.base_daily_rate) * total_worked

            advances = StaffAdvance.objects.filter(staff=staff, is_paid=False)
            total_advance = sum(a.amount for a in advances)

            net_pay = gross - float(total_advance)

            already_paid = is_suspended or (total_worked == 0 and last_payroll and last_payroll.month == current_month_str)

            sheet.append({
                "id": staff.id,
                "name": staff.name,
                "role": staff.role,
                "status": staff.status,
                "is_suspended": is_suspended,
                "base_rate": float(staff.salary) if staff.salary is not None else float(staff.base_daily_rate),
                "salary_frequency": staff.salary_frequency if staff.salary_frequency else "Daily",
                "present_days": total_worked,
                "absent_days": absent_days,
                "half_days": half_days,
                "advance": float(total_advance),
                "gross": gross,
                "net_pay": net_pay,
                "already_paid": already_paid,
            })

        return Response({
            "month": current_month_str,
            "sheet": sheet,
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'])
    def execute_paycheck(self, request):
        from decimal import Decimal
        from apps.farmer.models import FarmerStaff, StaffAttendance, StaffAdvance

        staff_id = request.data.get('staff_id')
        advance = request.data.get('advance', 0)
        incentive = request.data.get('incentive', 0)
        method = request.data.get('method', 'Bank Transfer Wire')
        gross = request.data.get('gross', 0)
        present = request.data.get('present', 0)
        net_pay = request.data.get('net_pay', 0)

        if not staff_id:
            return Response({'error': 'staff_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            staff = FarmerStaff.objects.get(id=staff_id, farmer=get_farmer_profile(request.user))
        except FarmerStaff.DoesNotExist:
            return Response({'error': 'Invalid staff ID'}, status=status.HTTP_400_BAD_REQUEST)

        from datetime import date
        current_month_str = date.today().strftime("%B %Y").upper()

        payroll = StaffPayroll.objects.create(
            staff=staff,
            month=current_month_str,
            base_rate=staff.base_daily_rate,
            present_days=present,
            absent_days=0,
            advance_deduction=Decimal(str(advance)),
            incentives=Decimal(str(incentive)),
            net_pay=Decimal(str(net_pay)),
            payment_method=method,
        )

        StaffAttendance.objects.filter(staff=staff, is_paid=False).update(is_paid=True)
        StaffAdvance.objects.filter(staff=staff, is_paid=False).update(is_paid=True)

        return Response({'success': True, 'payroll_id': payroll.id}, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'])
    def bulk_execute_paycheck(self, request):
        from decimal import Decimal
        from apps.farmer.models import FarmerStaff, StaffAttendance, StaffAdvance

        items = request.data.get('items', [])
        method = request.data.get('method', 'Bank Transfer Wire')

        if not items:
            return Response({'error': 'No items provided'}, status=status.HTTP_400_BAD_REQUEST)

        from datetime import date
        current_month_str = date.today().strftime("%B %Y").upper()
        created = 0
        failed = []

        for item in items:
            staff_id = item.get('employee_id')
            try:
                staff = FarmerStaff.objects.get(id=staff_id, farmer=request.user.profile)
                payroll = StaffPayroll.objects.create(
                    staff=staff,
                    month=current_month_str,
                    base_rate=staff.base_daily_rate,
                    present_days=item.get('present', 0),
                    absent_days=0,
                    advance_deduction=Decimal(str(item.get('advance', 0))),
                    incentives=Decimal(str(item.get('incentive', 0))),
                    net_pay=Decimal(str(item.get('net_pay', 0))),
                    payment_method=method,
                )
                StaffAttendance.objects.filter(staff=staff, is_paid=False).update(is_paid=True)
                StaffAdvance.objects.filter(staff=staff, is_paid=False).update(is_paid=True)
                created += 1
            except Exception:
                failed.append(item.get('name', str(staff_id)))

        return Response({'success': True, 'created': created, 'failed': failed}, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'])
    def update_employee_rate(self, request):
        from apps.farmer.models import FarmerStaff

        staff_id = request.data.get('staff_id')
        new_rate = request.data.get('rate')
        frequency = request.data.get('frequency')

        if not staff_id or new_rate is None:
            return Response({'error': 'staff_id and rate are required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            staff = FarmerStaff.objects.get(id=staff_id, farmer=request.user.profile)
            rate_val = float(new_rate)
            staff.salary = rate_val
            if frequency:
                staff.salary_frequency = frequency

            freq = staff.salary_frequency or "Daily"
            if freq == "Daily":
                staff.base_daily_rate = rate_val
            elif freq == "Weekly":
                staff.base_daily_rate = round(rate_val / 6.0, 2)
            elif freq == "Monthly":
                staff.base_daily_rate = round(rate_val / 30.0, 2)
            else:
                staff.base_daily_rate = rate_val

            staff.save()
            return Response({'success': True}, status=status.HTTP_200_OK)
        except FarmerStaff.DoesNotExist:
            return Response({'error': 'Invalid staff ID'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

from apps.superadmin.models import CropType, State, City, Taluk, Crop, Designation
from apps.farmer.models import FarmerInventory, FarmerVendor

class InventoryDropdownAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Consumption Utilities (Inventory Dropdown)', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        qs = FarmerInventory.objects.filter(farmer=get_farmer_profile(request.user), quantity__gt=0).order_by('item_name')
        return Response(list(qs.values('id', 'item_name', 'category', 'quantity', 'unit', 'unit_price')), status=status.HTTP_200_OK)

class ExpenseDropdownAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Expense Dropdowns (Types, Units, Vendors)', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import ExpenseType, ExpenseUnit
        vendors = FarmerVendor.objects.filter(farmer=get_farmer_profile(request.user)).order_by('vendor_name')
        expense_types = ExpenseType.objects.filter(is_active=True).order_by('name')
        expense_units = ExpenseUnit.objects.filter(is_active=True).order_by('name')
        vendor_data = []
        for v in vendors:
            vendor_data.append({
                'id': v.id,
                'vendor_name': v.vendor_name,
                'business_name': v.business_name or '',
                'mobile_number': v.mobile_number or '',
                'state': v.state or '',
                'district': v.district or '',
                'pincode': v.pincode or '',
                'created_at': v.created_at.strftime('%Y-%m-%d') if v.created_at else '',
            })
        return Response({
            'vendors': vendor_data,
            'expense_types': list(expense_types.values('id', 'name', 'is_vendor_based')),
            'expense_units': list(expense_units.values('id', 'name')),
        }, status=status.HTTP_200_OK)


class DesignationAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Designations', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        designations = Designation.objects.filter(is_active=True).order_by('name')
        return Response(list(designations.values('id', 'name')), status=status.HTTP_200_OK)


class QuickAddDesignationAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Designation',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'name': openapi.Schema(type=openapi.TYPE_STRING, description='Designation name'),
                'description': openapi.Schema(type=openapi.TYPE_STRING, description='Optional description'),
            },
            required=['name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        name = request.data.get('name', '').strip()
        description = request.data.get('description', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Designation name is required.'}, status=status.HTTP_400_BAD_REQUEST)

        desig = Designation.objects.filter(name__iexact=name).first()
        if not desig:
            desig = Designation.objects.create(name=name, description=description, is_active=True)
        elif not desig.is_active:
            desig.is_active = True
            desig.save()

        return Response({
            'success': True,
            'id': desig.id,
            'name': desig.name,
        }, status=status.HTTP_200_OK)


class ServiceClassListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Location Service Classes', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import LocationCategory
        return Response(list(LocationCategory.objects.filter(is_active=True).order_by('name').values('id', 'name')), status=status.HTTP_200_OK)


class QuickAddServiceClassAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Location Service Class',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={'name': openapi.Schema(type=openapi.TYPE_STRING, description='Service class name')},
            required=['name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import LocationCategory
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Service Class name cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        # Website parity (quick_add_service_class view)
        obj, created = LocationCategory.objects.get_or_create(
            name=name,
            defaults={'description': f'Auto-created service class: {name}', 'is_active': True},
        )
        return Response({'success': True, 'id': obj.id, 'name': obj.name}, status=status.HTTP_200_OK)


class WorkTypeListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Labour Skill / Work Types', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import ManPowerWorkType
        return Response(list(ManPowerWorkType.objects.filter(is_active=True).order_by('name').values('id', 'name')), status=status.HTTP_200_OK)


class QuickAddWorkTypeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Labour Skill / Work Type',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={'name': openapi.Schema(type=openapi.TYPE_STRING, description='Skill name')},
            required=['name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import ManPowerWorkType
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Skill name cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        # Website parity (ajax_quick_add_worker_skill view)
        obj, created = ManPowerWorkType.objects.get_or_create(
            name=name,
            defaults={'description': f'Auto-created skill: {name}', 'is_active': True},
        )
        return Response({'success': True, 'id': obj.id, 'name': obj.name}, status=status.HTTP_200_OK)


class CropTypeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Crop Types', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        return Response(list(CropType.objects.filter(is_active=True).values('id', 'name')), status=status.HTTP_200_OK)


class SoilTypeListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Soil Types', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import SoilType
        return Response(list(SoilType.objects.filter(is_active=True).values('id', 'name')), status=status.HTTP_200_OK)


class IrrigationTypeListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Irrigation Types', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import IrrigationType
        return Response(list(IrrigationType.objects.filter(is_active=True).values('id', 'name')), status=status.HTTP_200_OK)


class WaterSourceTypeListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Water Source Types', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import WaterSourceType
        return Response(list(WaterSourceType.objects.filter(is_active=True).values('id', 'name')), status=status.HTTP_200_OK)


class ElectricityTypeListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Electricity Types', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import ElectricityType
        return Response(list(ElectricityType.objects.filter(is_active=True).values('id', 'name')), status=status.HTTP_200_OK)


class FarmingTypeListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Farming Types', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import FarmingType
        return Response(list(FarmingType.objects.filter(is_active=True).values('id', 'name')), status=status.HTTP_200_OK)


class FencingTypeListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Fencing Types', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import FencingType
        return Response(list(FencingType.objects.filter(is_active=True).values('id', 'name')), status=status.HTTP_200_OK)


class MaintenanceScheduleListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Maintenance Schedules', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import MaintenanceSchedule
        return Response(list(MaintenanceSchedule.objects.filter(is_active=True).values('id', 'name')), status=status.HTTP_200_OK)


class DocumentTypeListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Document Types', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import DocumentCategory
        return Response(list(DocumentCategory.objects.filter(is_active=True).values('id', 'name')), status=status.HTTP_200_OK)


class QuickAddCropAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Crop',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'crop_name': openapi.Schema(type=openapi.TYPE_STRING, description='Crop name'),
            },
            required=['crop_name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import Crop
        name = request.data.get('crop_name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Crop name cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)

        crop_type = CropType.objects.filter(name__iexact=name).first()
        if not crop_type:
            crop_type = CropType.objects.create(name=name, description=f'Auto-created crop type for {name}', is_active=True)

        crop = Crop.objects.filter(name__iexact=name).first()
        if not crop:
            crop = Crop.objects.create(name=name, crop_type=crop_type, description=f'Auto-created crop: {name}', is_active=True)

        return Response({
            'success': True,
            'id': crop.id,
            'crop_type_id': crop_type.id,
            'name': crop.name,
        }, status=status.HTTP_200_OK)


class QuickAddSoilTypeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Soil Type',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={'soil_name': openapi.Schema(type=openapi.TYPE_STRING, description='Soil type name')},
            required=['soil_name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import SoilType
        name = request.data.get('soil_name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Soil type cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        obj, created = SoilType.objects.get_or_create(
            name=name, defaults={'description': f'Auto-created soil type: {name}', 'is_active': True}
        )
        return Response({'success': True, 'id': obj.id, 'name': obj.name}, status=status.HTTP_200_OK)


class QuickAddIrrigationTypeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Irrigation Type',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={'irrigation_name': openapi.Schema(type=openapi.TYPE_STRING, description='Irrigation type name')},
            required=['irrigation_name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import IrrigationType
        name = request.data.get('irrigation_name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Irrigation type cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        obj, created = IrrigationType.objects.get_or_create(
            name=name, defaults={'description': f'Auto-created irrigation type: {name}', 'is_active': True}
        )
        return Response({'success': True, 'id': obj.id, 'name': obj.name}, status=status.HTTP_200_OK)


class QuickAddWaterSourceAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Water Source',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={'name': openapi.Schema(type=openapi.TYPE_STRING, description='Water source name')},
            required=['name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import WaterSourceType
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Water source cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        obj, created = WaterSourceType.objects.get_or_create(
            name=name, defaults={'description': f'Auto-created water source: {name}', 'is_active': True}
        )
        return Response({'success': True, 'id': obj.id, 'name': obj.name}, status=status.HTTP_200_OK)


class QuickAddElectricityTypeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Electricity Type',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={'name': openapi.Schema(type=openapi.TYPE_STRING, description='Electricity type name')},
            required=['name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import ElectricityType
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Electricity type cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        obj, created = ElectricityType.objects.get_or_create(
            name=name, defaults={'description': f'Auto-created electricity type: {name}', 'is_active': True}
        )
        return Response({'success': True, 'id': obj.id, 'name': obj.name}, status=status.HTTP_200_OK)


class QuickAddFarmingTypeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Farming Type',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={'name': openapi.Schema(type=openapi.TYPE_STRING, description='Farming type name')},
            required=['name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import FarmingType
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Farming type cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        obj, created = FarmingType.objects.get_or_create(
            name=name, defaults={'description': f'Auto-created farming type: {name}', 'is_active': True}
        )
        return Response({'success': True, 'id': obj.id, 'name': obj.name}, status=status.HTTP_200_OK)


class QuickAddFencingTypeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Fencing Type',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={'name': openapi.Schema(type=openapi.TYPE_STRING, description='Fencing type name')},
            required=['name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import FencingType
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Fencing type cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        obj, created = FencingType.objects.get_or_create(
            name=name, defaults={'description': f'Auto-created fencing type: {name}', 'is_active': True}
        )
        return Response({'success': True, 'id': obj.id, 'name': obj.name}, status=status.HTTP_200_OK)


class QuickAddMaintenanceScheduleAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Maintenance Schedule',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={'name': openapi.Schema(type=openapi.TYPE_STRING, description='Maintenance schedule name')},
            required=['name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import MaintenanceSchedule
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Maintenance schedule cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        obj, created = MaintenanceSchedule.objects.get_or_create(
            name=name, defaults={'description': f'Auto-created maintenance schedule: {name}', 'is_active': True}
        )
        return Response({'success': True, 'id': obj.id, 'name': obj.name}, status=status.HTTP_200_OK)


class QuickAddDocumentTypeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Document Type',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={'name': openapi.Schema(type=openapi.TYPE_STRING, description='Document type name')},
            required=['name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import DocumentCategory
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Document type cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        obj, created = DocumentCategory.objects.get_or_create(
            name=name, defaults={'document_type': 'My Land', 'description': f'Auto-created document type: {name}', 'is_active': True}
        )
        return Response({'success': True, 'id': obj.id, 'name': obj.name}, status=status.HTTP_200_OK)


class CropCategoryListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Crop Categories', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import CropCategory
        return Response(list(CropCategory.objects.filter(is_active=True).values('id', 'name')), status=status.HTTP_200_OK)


class CropPatternListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Crop Patterns', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import CropPattern
        return Response(list(CropPattern.objects.filter(is_active=True).values('id', 'name')), status=status.HTTP_200_OK)


class CropVarietyListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Crop Varieties', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import CropVariety
        return Response(list(CropVariety.objects.filter(is_active=True).values('id', 'name')), status=status.HTTP_200_OK)


class CropQualityListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Crop Qualities', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import CropQuality
        return Response(list(CropQuality.objects.filter(is_active=True).values('id', 'name')), status=status.HTTP_200_OK)


class YieldUnitListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Yield Units', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import ExpenseUnit
        return Response(list(ExpenseUnit.objects.filter(is_active=True).values('id', 'name')), status=status.HTTP_200_OK)


class QuickAddCropCategoryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Crop Category',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={'name': openapi.Schema(type=openapi.TYPE_STRING, description='Crop category name')},
            required=['name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import CropCategory
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Crop category cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        obj, created = CropCategory.objects.get_or_create(
            name=name, defaults={'description': f'Auto-created crop category: {name}', 'is_active': True}
        )
        return Response({'success': True, 'id': obj.id, 'name': obj.name}, status=status.HTTP_200_OK)


class QuickAddCropPatternAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Crop Pattern',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={'name': openapi.Schema(type=openapi.TYPE_STRING, description='Crop pattern name')},
            required=['name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import CropPattern
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Crop pattern cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        obj, created = CropPattern.objects.get_or_create(
            name=name, defaults={'description': f'Auto-created crop pattern: {name}', 'is_active': True}
        )
        return Response({'success': True, 'id': obj.id, 'name': obj.name}, status=status.HTTP_200_OK)


class QuickAddCropVarietyAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Crop Variety',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={'name': openapi.Schema(type=openapi.TYPE_STRING, description='Crop variety name')},
            required=['name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import CropVariety
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Crop variety cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        obj, created = CropVariety.objects.get_or_create(
            name=name, defaults={'description': f'Auto-created crop variety: {name}', 'is_active': True}
        )
        return Response({'success': True, 'id': obj.id, 'name': obj.name}, status=status.HTTP_200_OK)


class QuickAddCropQualityAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Crop Quality',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={'name': openapi.Schema(type=openapi.TYPE_STRING, description='Crop quality name')},
            required=['name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import CropQuality
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Crop quality cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        obj, created = CropQuality.objects.get_or_create(
            name=name, defaults={'description': f'Auto-created crop quality: {name}', 'is_active': True}
        )
        return Response({'success': True, 'id': obj.id, 'name': obj.name}, status=status.HTTP_200_OK)


class FarmDocumentUploadAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        operation_summary='Upload Farm Document',
        manual_parameters=[
            openapi.Parameter('farm_documents', in_=openapi.IN_FORM, type=openapi.TYPE_FILE, description='Document file', required=True),
        ],
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from django.core.files.storage import default_storage
        from django.conf import settings
        file = request.FILES.get('farm_documents')
        if not file:
            return Response({'success': False, 'error': 'No file provided.'}, status=status.HTTP_400_BAD_REQUEST)
        
        path = default_storage.save(f'farm_documents/{file.name}', file)
        file_url = default_storage.url(path)
        if not file_url.startswith('http'):
            file_url = request.build_absolute_uri(file_url)
        
        return Response({'success': True, 'file_url': file_url}, status=status.HTTP_200_OK)


class CropAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get All Crops',
        manual_parameters=[openapi.Parameter('crop_type_id', in_=openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="Optional Crop Type ID to filter by")],
        responses={200: openapi.Response('Success')},
        tags=['Mobile - 13. System Dropdowns']
    )
    def get(self, request):
        crop_type_id = request.query_params.get('crop_type_id')
        queryset = Crop.objects.filter(is_active=True)
        if crop_type_id:
            queryset = queryset.filter(crop_type_id=crop_type_id)
        return Response(list(queryset.values('id', 'name', 'crop_type_id', 'crop_type__name')), status=status.HTTP_200_OK)

class StateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get States', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        qs = State.objects.filter(country__name__iexact='India').order_by('name')
        if not qs.exists():
            qs = State.objects.all().order_by('name')
        return Response(list(qs.values('id', 'name')), status=status.HTTP_200_OK)

class DistrictAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Districts',
        manual_parameters=[openapi.Parameter('state_id', in_=openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="Optional State ID to filter by")],
        responses={200: openapi.Response('Success')},
        tags=['Mobile - 13. System Dropdowns']
    )
    def get(self, request):
        state_id = request.query_params.get('state_id')
        queryset = City.objects.all()
        if state_id:
            queryset = queryset.filter(state_id=state_id)
        return Response(list(queryset.values('id', 'name', 'state_id')), status=status.HTTP_200_OK)

class TalukAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Taluks',
        manual_parameters=[openapi.Parameter('district_id', in_=openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="Optional District/City ID to filter by")],
        responses={200: openapi.Response('Success')},
        tags=['Mobile - 13. System Dropdowns']
    )
    def get(self, request):
        district_id = request.query_params.get('district_id')
        queryset = Taluk.objects.all()
        if district_id:
            queryset = queryset.filter(city_id=district_id)
        return Response(list(queryset.values('id', 'name', 'city_id')), status=status.HTTP_200_OK)


class PublicStateAPIView(StateAPIView):
    """Anonymous state list for the registration form (no JWT required)."""

    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary='Get States for Registration',
        responses={200: openapi.Response('Success')},
        tags=['Mobile - 01. Authentication'],
    )
    def get(self, request):
        return super().get(request)


class PublicDistrictAPIView(DistrictAPIView):
    """Anonymous district list for the registration form (no JWT required)."""

    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary='Get Districts for Registration',
        manual_parameters=[openapi.Parameter('state_id', in_=openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="State ID to filter by")],
        responses={200: openapi.Response('Success')},
        tags=['Mobile - 01. Authentication'],
    )
    def get(self, request):
        return super().get(request)


class PublicTalukAPIView(TalukAPIView):
    """Anonymous taluk list for the registration form (no JWT required)."""

    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary='Get Taluks for Registration',
        manual_parameters=[openapi.Parameter('district_id', in_=openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="District/City ID to filter by")],
        responses={200: openapi.Response('Success')},
        tags=['Mobile - 01. Authentication'],
    )
    def get(self, request):
        return super().get(request)

from apps.superadmin.models import (
    MarketPriceIndex, DepotTerminal, DepotCropPricing,
    NearbyLocation, LogisticsWorker, RentalEquipment,
    CropBlueprint, BlueprintStage, Guideline, GuidelineCategory,
    DailyPriceHistory
)
from apps.farmer.serializers import (
    MarketPriceIndexSerializer, DepotTerminalSerializer, DepotCropPricingSerializer,
    NearbyLocationSerializer, LogisticsWorkerSerializer, RentalEquipmentSerializer,
    CropBlueprintSerializer, BlueprintStageSerializer, GuidelineSerializer, GuidelineCategorySerializer,
    DailyPriceHistorySerializer
)

@method_decorator(name='list', decorator=swagger_auto_schema(tags=['Mobile - 12. Smart Farming Guidelines']))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 12. Smart Farming Guidelines']))
class GuidelineCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = GuidelineCategorySerializer
    pagination_class = StandardResultsSetPagination
    queryset = GuidelineCategory.objects.filter(is_active=True).order_by('name')

@method_decorator(name='list', decorator=swagger_auto_schema(tags=['Mobile - 12. Smart Farming Guidelines'], manual_parameters=[category_id_param]))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 12. Smart Farming Guidelines']))
class GuidelineViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = GuidelineSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        category_id = self.request.query_params.get('category_id')
        search = self.request.query_params.get('search')
        qs = Guideline.objects.all().order_by('-created_at')
        if category_id:
            qs = qs.filter(farming_section_id=category_id)
        if search:
            from functools import reduce
            from operator import or_
            from django.db.models import Q
            qs = qs.filter(reduce(or_, [
                Q(title__icontains=search),
                Q(search_tags__icontains=search),
                Q(detailed_content__icontains=search)
            ]))
        return qs

@method_decorator(name='list', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub']))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub']))
@method_decorator(name='create', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub']))
@method_decorator(name='update', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub']))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub']))
@method_decorator(name='destroy', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub']))
class CropBlueprintViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CropBlueprintSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        from django.db.models import Q
        return CropBlueprint.objects.filter(
            Q(farmer__isnull=True) | Q(farmer=get_farmer_profile(self.request.user))
        ).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(farmer=get_farmer_profile(self.request.user))

@method_decorator(name='list', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub']))
@method_decorator(name='create', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub']))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub']))
@method_decorator(name='update', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub']))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub']))
@method_decorator(name='destroy', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub']))
class BlueprintStageViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BlueprintStageSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        blueprint_id = self.request.query_params.get('blueprint_id')
        qs = BlueprintStage.objects.filter(
            blueprint__in=CropBlueprint.objects.filter(
                Q(farmer__isnull=True) | Q(farmer=get_farmer_profile(self.request.user))
            )
        )
        if blueprint_id:
            qs = qs.filter(blueprint_id=blueprint_id)
        return qs.order_by('day_offset')

@method_decorator(name='list', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub']))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub']))
class MarketPriceIndexViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = MarketPriceIndexSerializer
    pagination_class = StandardResultsSetPagination
    queryset = MarketPriceIndex.objects.all()

@method_decorator(name='list', decorator=swagger_auto_schema(
    tags=['Mobile - 11. Market Hub'],
    manual_parameters=[
        openapi.Parameter('crop_id', openapi.IN_QUERY, description="Filter by crop index ID", type=openapi.TYPE_INTEGER),
        openapi.Parameter('depot_id', openapi.IN_QUERY, description="Filter by depot terminal ID", type=openapi.TYPE_INTEGER),
        openapi.Parameter('from_date', openapi.IN_QUERY, description="Filter from date (YYYY-MM-DD)", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATE),
        openapi.Parameter('to_date', openapi.IN_QUERY, description="Filter to date (YYYY-MM-DD)", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATE),
    ]
))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub']))
class DailyPriceHistoryViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = DailyPriceHistorySerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = DailyPriceHistory.objects.select_related('depot', 'crop_index').all()
        crop_id = self.request.query_params.get('crop_id')
        depot_id = self.request.query_params.get('depot_id')
        from_date = self.request.query_params.get('from_date')
        to_date = self.request.query_params.get('to_date')

        if crop_id:
            qs = qs.filter(crop_index_id=crop_id)
        if depot_id:
            qs = qs.filter(depot_id=depot_id)
        if from_date:
            qs = qs.filter(date__gte=from_date)
        if to_date:
            qs = qs.filter(date__lte=to_date)

        return qs

@method_decorator(name='list', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub']))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub']))
class DepotTerminalViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = DepotTerminalSerializer
    pagination_class = StandardResultsSetPagination
    queryset = DepotTerminal.objects.all().order_by('-id')

demand_param = openapi.Parameter('demand_level', openapi.IN_QUERY, description="Filter by demand level: High, Medium, Low", type=openapi.TYPE_STRING)
service_class_param = openapi.Parameter('service_class', openapi.IN_QUERY, description="Filter Nearby Locations by service class (e.g., Cold Storage, Soil Lab)", type=openapi.TYPE_STRING)
status_filter_param = openapi.Parameter('status', openapi.IN_QUERY, description="Filter by status (e.g., Available)", type=openapi.TYPE_STRING)
workforce_class_param = openapi.Parameter('workforce_class', openapi.IN_QUERY, description="Filter labour by workforce class (e.g., Harvesting, Transport)", type=openapi.TYPE_STRING)
hardware_class_param = openapi.Parameter('hardware_class', openapi.IN_QUERY, description="Filter equipment by hardware class (e.g., Tractor, Harvester)", type=openapi.TYPE_STRING)
crop_name_param = openapi.Parameter('crop_name', openapi.IN_QUERY, description="Filter depot pricing by crop name (e.g., Wheat, Tomato)", type=openapi.TYPE_STRING)
search_param = openapi.Parameter('search', openapi.IN_QUERY, description="Search by name or keyword", type=openapi.TYPE_STRING)

@method_decorator(name='list', decorator=swagger_auto_schema(
    tags=['Mobile - 11. Market Hub'],
    operation_summary='List Depot Crop Pricing (Market Prices)',
    operation_description='Returns all depot-crop pricing combinations. This is the primary data for the Market Price Index screen showing Base Price, Terminal Rate, Markup Offset, and Sourced Quote per depot terminal.',
    manual_parameters=[demand_param, crop_name_param, search_param]
))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub'], operation_summary='Get single Depot Crop Pricing'))
class DepotCropPricingViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = DepotCropPricingSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = DepotCropPricing.objects.filter(trading_restricted=False).select_related('depot', 'crop_index').order_by('-id')
        demand = self.request.query_params.get('demand_level')
        crop_name = self.request.query_params.get('crop_name')
        search = self.request.query_params.get('search')
        if demand:
            qs = qs.filter(depot__demand_level__iexact=demand)
        if crop_name:
            qs = qs.filter(crop_index__crop_name__icontains=crop_name)
        if search:
            qs = qs.filter(depot__name__icontains=search)
        return qs

@method_decorator(name='list', decorator=swagger_auto_schema(
    tags=['Mobile - 11. Market Hub'],
    operation_summary='List Nearby Agri Locations',
    operation_description='Returns nearby cold storage, soil labs, and agri service locations. Used for the Agri Locations tab on the Market Hub screen.',
    manual_parameters=[service_class_param, search_param]
))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub'], operation_summary='Get single Nearby Location'))
@method_decorator(name='create', decorator=swagger_auto_schema(
    tags=['Mobile - 11. Market Hub'],
    operation_summary='Add Nearby Agri Location',
    operation_description='Create a new agri service location (website parity: Add Locations form on the Market page). Read-only after creation (list/retrieve/create only).',
))
class NearbyLocationViewSet(viewsets.ModelViewSet):
    # Website parity: farmer can add locations, but not edit/delete them.
    http_method_names = ['get', 'post', 'head', 'options']
    permission_classes = [IsAuthenticated]
    serializer_class = NearbyLocationSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = NearbyLocation.objects.all().order_by('-created_at')
        service_class = self.request.query_params.get('service_class')
        search = self.request.query_params.get('search')
        if service_class:
            qs = qs.filter(service_class__iexact=service_class)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(address__icontains=search)
                | Q(service_class__icontains=search)
            )
        return qs

@method_decorator(name='list', decorator=swagger_auto_schema(
    tags=['Mobile - 11. Market Hub'],
    operation_summary='List On-Demand Labour Workers',
    operation_description='Returns available logistics/farm workers. Used for the On Demand Labour tab — filter by workforce_class or status.',
    manual_parameters=[workforce_class_param, status_filter_param, search_param]
))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub'], operation_summary='Get single Logistics Worker'))
@method_decorator(name='create', decorator=swagger_auto_schema(
    tags=['Mobile - 11. Market Hub'],
    operation_summary='Add Labour Worker',
    operation_description='Create a worker (website parity: Add Labour Worker modal on the Market page).',
))
@method_decorator(name='update', decorator=swagger_auto_schema(
    tags=['Mobile - 11. Market Hub'],
    operation_summary='Update Labour Worker',
    operation_description='Full edit of a worker (PUT) — website parity: Edit modal.',
))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(
    tags=['Mobile - 11. Market Hub'],
    operation_summary='Update Labour Worker (partial)',
    operation_description='Partial edit (PATCH) — used for inline status changes (Available/Booked/On Leave) and form edits.',
))
@method_decorator(name='destroy', decorator=swagger_auto_schema(
    tags=['Mobile - 11. Market Hub'],
    operation_summary='Delete Labour Worker',
    operation_description='Remove a worker (website parity: Delete button with confirmation).',
))
class LogisticsWorkerViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = LogisticsWorkerSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = LogisticsWorker.objects.all().order_by('-created_at')
        workforce_class = self.request.query_params.get('workforce_class')
        status = self.request.query_params.get('status')
        search = self.request.query_params.get('search')
        if workforce_class:
            qs = qs.filter(workforce_class__iexact=workforce_class)
        if status:
            qs = qs.filter(status__iexact=status)
        if search:
            from django.db.models import Q
            qs = qs.filter(Q(name__icontains=search) | Q(specialty_skill__icontains=search))
        return qs

@method_decorator(name='list', decorator=swagger_auto_schema(
    tags=['Mobile - 11. Market Hub'],
    operation_summary='List Machinery Rental Equipment',
    operation_description='Returns available machinery and equipment for hire. Used for the Machinery Rentals tab — filter by hardware_class or status.',
    manual_parameters=[hardware_class_param, status_filter_param, search_param]
))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 11. Market Hub'], operation_summary='Get single Rental Equipment'))
class RentalEquipmentViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = RentalEquipmentSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = RentalEquipment.objects.all().order_by('-created_at')
        hardware_class = self.request.query_params.get('hardware_class')
        status = self.request.query_params.get('status')
        search = self.request.query_params.get('search')
        if hardware_class:
            qs = qs.filter(hardware_class__iexact=hardware_class)
        if status:
            qs = qs.filter(status__iexact=status)
        if search:
            from django.db.models import Q
            qs = qs.filter(Q(equipment_name__icontains=search) | Q(owner_name__icontains=search))
        return qs

    def list(self, request):
        """Full machinery rental list — mirrors the web Market Hub
        (platform RentalEquipment fleet + farmers' rental_available inventory).
        Returned unpaginated so the mobile app shows the same list as web."""
        from decimal import Decimal

        from apps.farmer.models import FarmerInventory

        results = []
        for r in self.get_queryset():
            results.append({
                'id': r.id,
                'equipment_name': r.equipment_name,
                'hardware_class': r.hardware_class or '',
                'price_per_day': str(r.price_per_day.normalize()) if r.price_per_day else '',
                'rental_unit': 'Per Day',
                'unit_short': 'day',
                'owner_name': r.owner_name or 'Platform Fleet',
                'contact_phone': r.contact_phone or '',
                'condition_rating': r.condition_rating or 'Good',
                'status': r.status or 'Available',
                'is_own': False,
                'is_farmer': False,
                'created_at': r.created_at,
                'updated_at': r.updated_at,
            })

        profile = get_farmer_profile(request.user)
        farmer_rentals = (
            FarmerInventory.objects.filter(rental_available=True)
            .select_related('farmer', 'farmer__user')
            .order_by('-created_at')
        )
        for fr in farmer_rentals:
            if fr.status in ('Maintenance', 'Needs Repair'):
                condition = 'Needs Repair'
            else:
                condition = 'Good'

            unit_str = fr.rental_unit or 'Per Day'
            unit_lower = unit_str.lower()
            if 'hour' in unit_lower:
                unit_short = 'hr'
            elif 'week' in unit_lower:
                unit_short = 'wk'
            elif 'month' in unit_lower:
                unit_short = 'mo'
            elif 'acre' in unit_lower:
                unit_short = 'acre'
            else:
                unit_short = 'day'

            is_own = bool(profile and fr.farmer_id == profile.id)
            if is_own:
                owner_display = 'You (Own)'
            else:
                owner_display = fr.farmer.company_name or (
                    fr.farmer.user.get_full_name()
                    if fr.farmer and fr.farmer.user
                    else 'Farmer'
                )

            farmer_phone = ''
            if fr.farmer:
                farmer_phone = getattr(fr.farmer, 'mobile_number', '') or (
                    fr.farmer.user.username if fr.farmer.user else ''
                )

            price = fr.rental_price or fr.unit_price or 0
            results.append({
                'id': fr.id,
                'equipment_name': fr.item_name,
                'hardware_class': fr.category or 'Machinery',
                'price_per_day': str(Decimal(price).normalize()),
                'rental_unit': unit_str,
                'unit_short': unit_short,
                'owner_name': owner_display,
                'contact_phone': farmer_phone or '',
                'condition_rating': condition,
                'status': fr.status if fr.status in (
                    'Available', 'Booked', 'Rented', 'Maintenance', 'Operational',
                ) else 'Available',
                'is_own': is_own,
                'is_farmer': True,
                'created_at': fr.created_at,
                'updated_at': fr.created_at,
            })

        return Response({
            'count': len(results),
            'next': None,
            'previous': None,
            'results': results,
        })

 

customer_status_param = openapi.Parameter('status', openapi.IN_QUERY, description="Filter by status (Active / Inactive)", type=openapi.TYPE_STRING)
outstanding_type_param = openapi.Parameter('type', openapi.IN_QUERY, description="Filter outstanding by type: vendor (payables) or customer (receivables)", type=openapi.TYPE_STRING)
outstanding_status_param = openapi.Parameter('status', openapi.IN_QUERY, description="Filter by outstanding status: Unpaid, Partial, Paid, Overdue", type=openapi.TYPE_STRING)

@method_decorator(name='list', decorator=swagger_auto_schema(
    tags=['Mobile - 10. Customers & Vendors'],
    operation_summary='List Customers',
    operation_description='Returns all customers registered under the authenticated farmer.',
    manual_parameters=[customer_status_param, search_param]
))
@method_decorator(name='create', decorator=swagger_auto_schema(tags=['Mobile - 10. Customers & Vendors'], operation_summary='Add a Customer'))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 10. Customers & Vendors'], operation_summary='Get Customer Details'))
@method_decorator(name='update', decorator=swagger_auto_schema(tags=['Mobile - 10. Customers & Vendors'], operation_summary='Update Customer (Full)'))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(tags=['Mobile - 10. Customers & Vendors'], operation_summary='Update Customer (Partial)'))
@method_decorator(name='destroy', decorator=swagger_auto_schema(tags=['Mobile - 10. Customers & Vendors'], operation_summary='Delete Customer'))
class FarmerCustomerViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = FarmerCustomerSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = FarmerCustomer.objects.filter(farmer=get_farmer_profile(self.request.user)).order_by('-created_at')
        status = self.request.query_params.get('status')
        search = self.request.query_params.get('search')
        if status:
            qs = qs.filter(status__iexact=status)
        if search:
            from django.db.models import Q
            qs = qs.filter(Q(name__icontains=search) | Q(company_name__icontains=search))
        return qs

    def perform_create(self, serializer):
        serializer.save(farmer=get_farmer_profile(self.request.user))

    @swagger_auto_schema(method='get', tags=['Mobile - 10. Customers & Vendors'],
        operation_summary='Get Customer Transactions',
        operation_description='Returns all ledger transactions related to a specific customer.')
    @action(detail=True, methods=['get'])
    def transactions(self, request, pk=None):
        customer = self.get_object()
        from apps.farmer.models import LedgerTransaction
        from django.db.models import Q

        txns = LedgerTransaction.objects.filter(
            farmer=get_farmer_profile(request.user),
        ).filter(
            Q(description__icontains=customer.name) |
            Q(tags__icontains=customer.name)
        ).order_by('-close_date', '-id')

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(txns, request)
        if page is not None:
            serializer = LedgerTransactionSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = LedgerTransactionSerializer(txns, many=True)
        return Response(serializer.data)

    @swagger_auto_schema(method='get', tags=['Mobile - 10. Customers & Vendors'],
        operation_summary='Get Customer Outstandings',
        operation_description='Returns all outstanding invoices for a specific customer.')
    @action(detail=True, methods=['get'])
    def outstandings(self, request, pk=None):
        customer = self.get_object()
        from apps.farmer.models import FarmerOutstanding

        outstanding_qs = FarmerOutstanding.objects.filter(
            farmer=get_farmer_profile(request.user),
            customer=customer,
        ).order_by('-due_date')

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(outstanding_qs, request)
        if page is not None:
            serializer = FarmerOutstandingSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = FarmerOutstandingSerializer(outstanding_qs, many=True)
        return Response(serializer.data)

@method_decorator(name='list', decorator=swagger_auto_schema(
    tags=['Mobile - 10. Customers & Vendors'],
    operation_summary='List Vendors',
    operation_description='Returns all vendors registered under the authenticated farmer. Vendors are used for expense tracking and outstanding payables.',
    manual_parameters=[search_param]
))
@method_decorator(name='create', decorator=swagger_auto_schema(tags=['Mobile - 10. Customers & Vendors'], operation_summary='Add a Vendor'))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 10. Customers & Vendors'], operation_summary='Get Vendor Details'))
@method_decorator(name='update', decorator=swagger_auto_schema(tags=['Mobile - 10. Customers & Vendors'], operation_summary='Update Vendor (Full)'))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(tags=['Mobile - 10. Customers & Vendors'], operation_summary='Update Vendor (Partial)'))
@method_decorator(name='destroy', decorator=swagger_auto_schema(tags=['Mobile - 10. Customers & Vendors'], operation_summary='Delete Vendor'))
class FarmerVendorViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = FarmerVendorSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = FarmerVendor.objects.filter(farmer=get_farmer_profile(self.request.user)).order_by('vendor_name')
        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(vendor_name__icontains=search)
        return qs

    def _attach_uploaded_document(self, vendor):
        """Website parity (vendor view): create a Document row from the
        uploaded file and link it to the vendor."""
        doc_file = self.request.FILES.get('document_file')
        if not doc_file:
            return
        import os
        from apps.superadmin.models import Document
        document_type = (self.request.data.get('document_type') or 'Vendor Document').strip()
        name = vendor.vendor_name or 'Vendor'
        ext = os.path.splitext(doc_file.name)[1].lower()
        doc = Document.objects.create(
            title=f"Vendor Document - {name[:50]}",
            category=document_type or 'Vendor Document',
            description=f"Uploaded document ({document_type}) for vendor '{name}'.",
            file=doc_file,
            file_size_bytes=doc_file.size,
            file_type=ext,
            uploaded_by=self.request.user,
            farmer=get_farmer_profile(self.request.user),
        )
        vendor.document = doc
        vendor.save()

    def _sync_vendor_rename(self, vendor, old_name, new_name):
        """Website parity (edit_vendor): keep vendor ID & name consistent
        across connected outstandings and transactions on rename."""
        from apps.farmer.models import FarmerOutstanding, LedgerTransaction
        profile = vendor.farmer
        for o in FarmerOutstanding.objects.filter(farmer=profile):
            invoice_no = o.invoice_no or ''
            if o.vendor_id == vendor.id or (old_name and old_name in invoice_no) or (new_name and new_name in invoice_no):
                o.vendor = vendor
                if old_name and old_name != new_name and old_name in invoice_no:
                    o.invoice_no = invoice_no.replace(old_name, new_name)
                o.save()

        for t in LedgerTransaction.objects.filter(farmer=profile):
            description = t.description or ''
            if t.vendor_id == vendor.id or (old_name and old_name in description) or (new_name and new_name in description):
                t.vendor = vendor
                if old_name and old_name != new_name and old_name in description:
                    t.description = description.replace(old_name, new_name)
                t.save()

    def perform_create(self, serializer):
        vendor = serializer.save(farmer=get_farmer_profile(self.request.user))
        self._attach_uploaded_document(vendor)

    def perform_update(self, serializer):
        old_name = serializer.instance.vendor_name
        vendor = serializer.save()
        self._attach_uploaded_document(vendor)
        if old_name:
            self._sync_vendor_rename(vendor, old_name, vendor.vendor_name)

    @swagger_auto_schema(method='get', tags=['Mobile - 10. Customers & Vendors'],
        operation_summary='Get Vendor Transactions',
        operation_description='Returns all ledger transactions related to a specific vendor.')
    @action(detail=True, methods=['get'])
    def transactions(self, request, pk=None):
        vendor = self.get_object()
        from apps.farmer.models import LedgerTransaction
        from django.db.models import Q

        txns = LedgerTransaction.objects.filter(
            farmer=get_farmer_profile(request.user),
        ).filter(
            Q(vendor=vendor) |
            Q(description__icontains=vendor.vendor_name) |
            Q(tags__icontains=vendor.vendor_name)
        ).order_by('-close_date', '-id')

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(txns, request)
        if page is not None:
            serializer = LedgerTransactionSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = LedgerTransactionSerializer(txns, many=True)
        return Response(serializer.data)

    @swagger_auto_schema(method='get', tags=['Mobile - 10. Customers & Vendors'],
        operation_summary='Get Vendor Outstandings',
        operation_description='Returns all outstanding invoices for a specific vendor.')
    @action(detail=True, methods=['get'])
    def outstandings(self, request, pk=None):
        vendor = self.get_object()
        from apps.farmer.models import FarmerOutstanding

        outstanding_qs = FarmerOutstanding.objects.filter(
            farmer=get_farmer_profile(request.user),
            vendor=vendor,
        ).order_by('-due_date')

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(outstanding_qs, request)
        if page is not None:
            serializer = FarmerOutstandingSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = FarmerOutstandingSerializer(outstanding_qs, many=True)
        return Response(serializer.data)

@method_decorator(name='list', decorator=swagger_auto_schema(
    tags=['Mobile - 10. Customers & Vendors'],
    operation_summary='List Outstanding Invoices (Receivables & Payables)',
    operation_description='Returns all outstanding records. Use type=vendor for payables (what farmer owes vendors) and type=customer for receivables (what customers owe the farmer).',
    manual_parameters=[outstanding_type_param, outstanding_status_param]
))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 10. Customers & Vendors'], operation_summary='Get Outstanding Invoice Detail'))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(tags=['Mobile - 10. Customers & Vendors'], operation_summary='Update Outstanding (Mark as Paid etc.)'))
class FarmerOutstandingViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = FarmerOutstandingSerializer
    pagination_class = StandardResultsSetPagination
    # 'post' is required for the add_invoice and pay @action routes below
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def create(self, request, *args, **kwargs):
        # Block the raw router create route; outstanding invoices are created
        # through add_invoice (or sale creation) so farmer/customer are set server-side.
        return Response(
            {'success': False, 'error': 'Use POST /api/farmer/outstanding/add_invoice/ to create an outstanding invoice.'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def get_queryset(self):
        qs = FarmerOutstanding.objects.filter(farmer=get_farmer_profile(self.request.user)).order_by('due_date')
        otype = self.request.query_params.get('type')
        status = self.request.query_params.get('status')
        if otype == 'vendor':
            qs = qs.filter(invoice_no__startswith='Vendor:')
        elif otype == 'customer':
            qs = qs.exclude(invoice_no__startswith='Vendor:')
        if status:
            qs = qs.filter(status__iexact=status)
        return qs

    @swagger_auto_schema(
        operation_summary='Pay Outstanding (Full or Partial)',
        operation_description='Settles an outstanding invoice. Supports full payment or partial payment with payment method and notes.',
        tags=['Mobile - 10. Customers & Vendors'],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'payment_type': openapi.Schema(type=openapi.TYPE_STRING, enum=['full', 'partial'], default='full'),
                'amount_paid': openapi.Schema(type=openapi.TYPE_NUMBER, description='Partial payment amount (required if payment_type=partial)'),
                'payment_method': openapi.Schema(type=openapi.TYPE_STRING, default='Cash'),
                'notes': openapi.Schema(type=openapi.TYPE_STRING),
            },
        ),
        responses={200: openapi.Response('Payment successful')}
    )
    @action(detail=True, methods=['post'])
    def pay(self, request, pk=None):
        outstanding = self.get_object()
        import datetime, re
        from apps.farmer.models import LedgerTransaction

        payment_type = request.data.get('payment_type', 'full')
        payment_method = request.data.get('payment_method', 'Cash')
        notes = request.data.get('notes', '')
        remaining = float(outstanding.amount_total) - float(outstanding.amount_paid)

        try:
            typed_amount = float(request.data.get('amount_paid', 0))
        except (ValueError, TypeError):
            typed_amount = 0.0

        if payment_type == 'partial' and typed_amount > 0 and typed_amount < remaining:
            pay_amount = typed_amount
        else:
            pay_amount = remaining

        if pay_amount <= 0:
            return Response({'success': False, 'error': 'Invalid payment amount.'}, status=400)

        new_amount_paid = float(outstanding.amount_paid) + pay_amount
        new_balance = max(0.0, float(outstanding.amount_total) - new_amount_paid)
        outstanding.amount_paid = new_amount_paid
        outstanding.status = 'Paid' if new_amount_paid >= float(outstanding.amount_total) else 'Partial'
        outstanding.save()

        # Synchronize linked parent sale transaction
        sale_id_match = re.search(r'Sale #(\d+)', outstanding.invoice_no)
        sale_trans = None
        if sale_id_match:
            sale_id = int(sale_id_match.group(1))
            sale_trans = LedgerTransaction.objects.filter(farmer=get_farmer_profile(request.user), id=sale_id).first()
            if sale_trans:
                st_tags = sale_trans.tags or ''
                if 'Paid:' in st_tags:
                    st_tags = re.sub(r'Paid:\s*₹?[\d\.]+', f'Paid: ₹{new_amount_paid:.2f}', st_tags)
                else:
                    st_tags += f' | Paid: ₹{new_amount_paid:.2f}'
                if 'Balance:' in st_tags:
                    st_tags = re.sub(r'Balance:\s*₹?[\d\.]+', f'Balance: ₹{new_balance:.2f}', st_tags)
                else:
                    st_tags += f' | Balance: ₹{new_balance:.2f}'
                sale_trans.tags = st_tags
                sale_trans.status = 'Received' if new_amount_paid >= float(outstanding.amount_total) else 'Partial'
                sale_trans.save()

        cust_name = outstanding.customer.name if outstanding.customer else "Customer"
        desc = f"Received Payment for Invoice {outstanding.invoice_no} ({cust_name})"
        if notes:
            desc += f" - {notes}"

        linked_tag = f"Inflow | Customer Settlement | Linked Sale #{sale_trans.id}" if sale_trans else "Inflow | Customer Settlement"

        LedgerTransaction.objects.create(
            farmer=get_farmer_profile(request.user),
            close_date=datetime.date.today(),
            description=desc,
            category="Customer Payment",
            payment_method=payment_method,
            tags=linked_tag,
            status="Received",
            amount=pay_amount
        )

        return Response({'success': True, 'message': 'Payment recorded successfully!', 'balance_due': new_balance, 'status': outstanding.status})

    @swagger_auto_schema(
        operation_summary='Add Customer Outstanding Invoice',
        operation_description='Creates a new outstanding invoice for a customer.',
        tags=['Mobile - 10. Customers & Vendors'],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['due_date', 'amount_total'],
            properties={
                'customer_id': openapi.Schema(type=openapi.TYPE_INTEGER, description='Customer ID (optional)'),
                'invoice_no': openapi.Schema(type=openapi.TYPE_STRING, description='Invoice number (auto-generated if blank)'),
                'due_date': openapi.Schema(type=openapi.TYPE_STRING, format='date'),
                'amount_total': openapi.Schema(type=openapi.TYPE_NUMBER),
            },
        ),
    )
    @action(detail=False, methods=['post'])
    def add_invoice(self, request):
        import random
        from apps.farmer.models import FarmerCustomer, FarmerOutstanding

        profile = get_farmer_profile(request.user)
        customer_id = request.data.get('customer_id')
        invoice_no = request.data.get('invoice_no', '')
        due_date = request.data.get('due_date')
        amount_total = request.data.get('amount_total')

        if not due_date or not amount_total:
            return Response({'success': False, 'error': 'Due date and amount are required.'}, status=400)

        customer = FarmerCustomer.objects.filter(id=customer_id, farmer=profile).first() if customer_id else None

        if not invoice_no:
            invoice_no = f"INV-CUST-{customer.id if customer else 'GEN'}-{random.randint(1000, 9999)}"

        outstanding = FarmerOutstanding.objects.create(
            farmer=profile,
            customer=customer,
            invoice_no=invoice_no,
            due_date=due_date,
            amount_total=amount_total,
        )

        return Response({'success': True, 'message': 'Invoice added successfully!', 'id': outstanding.id})

    @swagger_auto_schema(
        operation_summary='Get Outstanding Summary Stats',
        operation_description='Returns summary stats for customer outstanding payments (total pending, total paid, overdue count, settled count).',
        tags=['Mobile - 10. Customers & Vendors'],
    )
    @action(detail=False, methods=['get'])
    def summary(self, request):
        import datetime
        from django.db.models import Sum
        from apps.farmer.models import FarmerOutstanding

        profile = get_farmer_profile(request.user)
        today = datetime.date.today()

        customer_outs = FarmerOutstanding.objects.filter(farmer=profile).exclude(invoice_no__startswith='Vendor:')

        # Auto-mark overdue
        customer_outs.filter(due_date__lt=today, status='Unpaid').update(status='Overdue')
        customer_outs = FarmerOutstanding.objects.filter(farmer=profile).exclude(invoice_no__startswith='Vendor:')

        total_receivables = 0.0
        total_paid = 0.0
        overdue_count = 0
        pending_count = 0
        settled_count = 0

        for inv in customer_outs:
            balance = max(0.0, float(inv.amount_total) - float(inv.amount_paid))
            total_receivables += balance
            total_paid += float(inv.amount_paid)

            if inv.status == 'Overdue':
                overdue_count += 1
                pending_count += 1
            elif inv.status in ['Unpaid', 'Partial']:
                pending_count += 1
            elif inv.status == 'Paid':
                settled_count += 1

        return Response({
            'total_receivables': round(total_receivables, 2),
            'total_paid': round(total_paid, 2),
            'overdue_count': overdue_count,
            'pending_count': pending_count,
            'settled_count': settled_count,
        })

    @swagger_auto_schema(
        operation_summary='Get Vendor Outstanding Summary Stats',
        operation_description='Returns summary stats for vendor outstanding payments (total outstanding, overdue count, active count).',
        tags=['Mobile - 10. Customers & Vendors'],
    )
    @action(detail=False, methods=['get'])
    def vendor_summary(self, request):
        import datetime
        from django.db.models import Sum
        from apps.farmer.models import FarmerOutstanding

        profile = get_farmer_profile(request.user)
        today = datetime.date.today()

        vendor_outs = FarmerOutstanding.objects.filter(farmer=profile, invoice_no__startswith='Vendor:')

        # Auto-mark overdue
        vendor_outs.filter(due_date__lt=today, status='Unpaid').update(status='Overdue')
        vendor_outs = FarmerOutstanding.objects.filter(farmer=profile, invoice_no__startswith='Vendor:')

        total_outstanding = vendor_outs.exclude(status='Paid').aggregate(
            total=Sum('amount_total')
        )['total'] or 0
        total_paid = vendor_outs.aggregate(
            total=Sum('amount_paid')
        )['total'] or 0
        overdue_count = vendor_outs.filter(status='Overdue').count()
        active_count = vendor_outs.exclude(status='Paid').count()

        return Response({
            'total_outstanding': round(float(total_outstanding), 2),
            'total_paid': round(float(total_paid), 2),
            'overdue_count': overdue_count,
            'active_count': active_count,
        })

# ─────────────────────────────────────────────
# Mobile - Farm Full Dashboard
# ─────────────────────────────────────────────
class FarmDashboardAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Full Farm Dashboard (Mobile)',
        operation_description='Returns the complete farm profile data: farm info, all crop splits, activities, tasks, workers, expenses, and budget totals. Mirrors the web Farm Profile modal.',
        tags=['Mobile - 03. Farm & Land Management'],
        responses={
            200: openapi.Response('Farm dashboard data'),
            404: openapi.Response('Farm not found')
        }
    )
    def get(self, request, pk):
        from apps.farmer.models import Farm, FarmerStaff, LedgerTransaction, FarmActivity
        from django.db.models import Sum
        try:
            farm = Farm.objects.get(id=pk, farmer=get_farmer_profile(request.user))
        except Farm.DoesNotExist:
            return Response({'error': 'Farm not found'}, status=404)

        from apps.farmer.serializers import CropSplitSerializer, FarmActivitySerializer, FarmTaskSerializer
        splits = CropSplitSerializer(farm.splits.all().order_by('-id'), many=True).data
        activities = FarmActivitySerializer(farm.activities.all().order_by('-created_at'), many=True).data
        tasks = FarmTaskSerializer(farm.tasks.all().order_by('-created_at'), many=True).data
        workers = list(FarmerStaff.objects.filter(farmer=get_farmer_profile(request.user)).values('id', 'name', 'role'))

        expenses_qs = LedgerTransaction.objects.filter(
            farmer=get_farmer_profile(request.user), status='Cost',
            tags__icontains=f"Farm ID: {farm.id}"
        )
        total_budget = abs(float(expenses_qs.aggregate(Sum('amount'))['amount__sum'] or 0))
        total_consumption = float(
            farm.activities.filter(activity_type="Resource Consumption")
            .aggregate(Sum('amount'))['amount__sum'] or 0
        )

        return Response({
            'farm': {
                'id': farm.id, 'land_name': farm.land_name,
                'total_area': farm.total_area, 'latitude': farm.latitude,
                'longitude': farm.longitude, 'health_indicator': farm.health_indicator,
                'crop_count': farm.splits.exclude(stage__in=['Harvested', 'Sold']).count(),
            },
            'splits': splits,
            'activities': activities,
            'tasks': tasks,
            'workers': workers,
            'total_budget': total_budget,
            'total_consumption_value': total_consumption,
        })

# ─────────────────────────────────────────────
# Mobile - Crop Sell with full ledger/outstanding logic
# ─────────────────────────────────────────────
class CropSellAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Sell a Harvest Batch (Mobile)',
        operation_description=(
            'Records a crop batch sale. Partial batch sales preserve the unsold '
            'quantity as a ready batch. The sale creates a ledger transaction and '
            'an outstanding receivable when payment is incomplete.\n\n'
            'Pass the split `id` in the URL path.'
        ),
        request_body=CropSellSerializer,
        tags=['Mobile - 04. Crop Splits & Cultivation'],
        responses={
            200: openapi.Response('Sell recorded successfully'),
            400: openapi.Response('Validation error'),
            404: openapi.Response('Crop split not found'),
        }
    )
    @transaction.atomic
    def post(self, request, pk=None):
        from decimal import Decimal, InvalidOperation
        from datetime import date as today_date
        import datetime
        import re
        from apps.farmer.harvest_batches import normalize_batch_numbers
        from apps.farmer.models import (
            CropSplit, LedgerTransaction, FarmerCustomer, FarmerOutstanding,
        )
        from apps.farmer.views import log_action

        serializer = CropSellSerializer(data=request.data)
        if not serializer.is_valid():
            err_msg = 'Invalid sale parameters.'
            for field, errors in serializer.errors.items():
                if isinstance(errors, (list, tuple)) and errors:
                    err_msg = str(errors[0])
                    break
                elif isinstance(errors, str):
                    err_msg = errors
                    break
            err_dict = dict(serializer.errors)
            err_dict['success'] = False
            err_dict['error'] = err_msg
            return Response(err_dict, status=400)

        target_pk = pk or request.data.get('split_id') or request.data.get('crop_split_id')
        if not target_pk:
            return Response({'success': False, 'error': 'Crop split ID is required.'}, status=400)

        try:
            split = CropSplit.objects.select_for_update().get(
                id=target_pk,
                farm__farmer=get_farmer_profile(request.user),
            )
        except CropSplit.DoesNotExist:
            return Response({'success': False, 'error': 'Crop split not found.'}, status=404)
        if (split.stage or '').lower() == 'inactive':
            return Response(
                {'success': False, 'error': 'Inactive crops are view-only and cannot be sold.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        yield_val = data['yield_tons']
        price_val = data.get('price_per_mt')
        total_val = data.get('total_sale_value')
        paid_val = data.get('paid_amount')
        batch_index = data.get('batch_index')
        customer_name = (data.get('customer_name') or '').strip() or 'Direct Customer'
        direct_name = (data.get('direct_customer_name') or '').strip()
        direct_phone = (data.get('direct_customer_phone') or '').strip()
        is_direct_customer = customer_name.lower() == 'direct customer'
        if direct_name or direct_phone:
            if not direct_name:
                return Response(
                    {'success': False, 'error': 'Enter the direct customer name.', 'direct_customer_name': 'Enter the direct customer name.'},
                    status=400,
                )
            if not re.fullmatch(r'[0-9]{10,13}', direct_phone):
                return Response(
                    {'success': False, 'error': 'Enter a mobile number containing 10 to 13 digits.', 'direct_customer_phone': 'Enter a mobile number containing 10 to 13 digits.'},
                    status=400,
                )
            customer_name = direct_name
            is_direct_customer = True
        elif is_direct_customer and ('customer_name' in request.data or 'customer' in request.data):
            return Response(
                {'success': False, 'error': 'Enter the direct customer name.', 'direct_customer_name': 'Enter the direct customer name.'},
                status=400,
            )

        raw_batches = list(split.harvest_batches or [])
        if any(not isinstance(batch, dict) for batch in raw_batches):
            return Response(
                {'success': False, 'error': 'Crop harvest batches contain invalid data.'},
                status=400,
            )
        batches = normalize_batch_numbers(raw_batches)
        target_batch = None
        available = Decimal(str(split.yield_tons or 0))

        # Auto-synthesize fallback batch if split has recorded yield but batches array was empty (matches web renderSellBatches)
        if not batches and (split.yield_tons or yield_val):
            f_yield = float(split.yield_tons or yield_val)
            f_price = float(price_val or split.price_per_mt or 0)
            f_val = round(f_yield * f_price, 2)
            u = data.get('unit') or split.expected_yield_unit or 'MT'
            q = data.get('quality') or 'Grade 1'
            batches = [{
                'yield': f_yield,
                'price': f_price,
                'saleValue': f_val,
                'quality': q,
                'unit': u,
                'flush_no': 1,
                'status': 'Ready',
                'batch_no': 1,
            }]
            batch_index = 0
            target_batch = batches[0]
            available = Decimal(str(f_yield))
        elif batches:
            if batch_index is None:
                if len(batches) == 1:
                    batch_index = 0
                else:
                    return Response(
                        {'success': False, 'error': 'Select an available harvest batch to sell.', 'batch_index': 'Select an available harvest batch to sell.'},
                        status=400,
                    )
            if batch_index >= len(batches):
                return Response(
                    {'success': False, 'error': 'Select an available harvest batch to sell.', 'batch_index': 'Select an available harvest batch to sell.'},
                    status=400,
                )
            target_batch = batches[batch_index]
            if target_batch.get('status') in ('Sold', 'Loss / Discard'):
                return Response(
                    {'success': False, 'error': 'This batch is no longer available. Refresh and try again.', 'batch_index': 'This batch is no longer available. Refresh and try again.'},
                    status=400,
                )
            try:
                available = Decimal(str(target_batch.get('yield') or 0))
                if price_val is None:
                    price_val = Decimal(str(target_batch.get('price') or 0))
            except InvalidOperation:
                return Response(
                    {'success': False, 'error': 'The selected batch has an invalid quantity or price.'},
                    status=400,
                )
        elif split.yield_tons:
            available = Decimal(str(split.yield_tons))

        if price_val is None:
            price_val = Decimal(str(split.price_per_mt or 0))
        if price_val <= 0:
            return Response(
                {'success': False, 'error': 'Enter a valid sale price.', 'price_per_mt': 'Enter a valid sale price.'},
                status=400,
            )
        if total_val is None:
            total_val = yield_val * price_val
        if total_val <= 0:
            return Response(
                {'success': False, 'error': 'Enter a valid sale price.', 'total_sale_value': 'Enter a valid sale price.'},
                status=400,
            )
        if paid_val is None:
            paid_val = total_val
        if paid_val < 0 or paid_val > total_val:
            return Response(
                {'success': False, 'error': 'Payment must be between zero and the total sale value.', 'paid_amount': 'Payment must be between zero and the total sale value.'},
                status=400,
            )
        unit_val = (
            data.get('unit')
            or (target_batch or {}).get('unit')
            or split.expected_yield_unit
            or 'MT'
        )
        if yield_val > available:
            return Response(
                {'success': False, 'error': f'Quantity sold cannot exceed the available batch quantity of {available:g} {unit_val}.', 'yield_tons': f'Quantity sold cannot exceed the available batch quantity of {available:g} {unit_val}.'},
                status=400,
            )

        quality_str = (
            data.get('quality')
            or (target_batch or {}).get('quality')
            or 'Grade 1'
        )
        payment_method = data.get('payment_method') or 'Cash'
        pending_val = max(Decimal('0'), total_val - paid_val)
        payment_status = (
            'Partial' if paid_val > 0 else 'Pending'
        ) if pending_val > 0 else 'Received'
        crop_name = split.crop.name if split.crop else 'Crop'

        if target_batch is not None:
            original_yield = float(target_batch.get('yield') or 0)
            original_value = float(target_batch.get('saleValue') or 0)
            if 0 < float(yield_val) < original_yield:
                remaining_batch = dict(target_batch)
                remaining_yield = round(original_yield - float(yield_val), 2)

                target_batch['yield'] = float(yield_val)
                target_batch['status'] = 'Sold'
                target_batch['sold_to'] = customer_name
                if is_direct_customer and direct_phone:
                    target_batch['sold_contact'] = direct_phone
                    target_batch['customer_type'] = 'Direct Customer'
                target_batch['sold_at_price'] = float(price_val)
                target_batch['sold_total'] = float(total_val)
                target_batch['paid'] = float(paid_val)
                target_batch['pending'] = float(pending_val)
                target_batch['payment_method'] = payment_method
                target_batch['payment_status'] = payment_status
                target_batch['sold_date'] = datetime.date.today().strftime('%d %b %Y')

                base_price = float(target_batch.get('price') or price_val)
                remaining_batch.update({
                    'yield': remaining_yield,
                    'unit': target_batch.get('unit') or unit_val,
                    'price': target_batch.get('price') or float(price_val),
                    'priceUnit': (
                        target_batch.get('priceUnit')
                        or target_batch.get('unit')
                        or unit_val
                    ),
                    'saleValue': (
                        round(original_value * remaining_yield / original_yield, 2)
                        if original_value
                        else round(remaining_yield * base_price, 2)
                    ),
                    'quality': target_batch.get('quality') or quality_str,
                    'status': 'Ready',
                    'flush_no': target_batch.get('flush_no', 1),
                })
                if original_value:
                    target_batch['saleValue'] = round(
                        original_value * float(yield_val) / original_yield,
                        2,
                    )
                batches.insert(batch_index + 1, remaining_batch)
            else:
                target_batch['yield'] = original_yield if original_yield > 0 else float(yield_val)
                target_batch['status'] = 'Sold'
                target_batch['sold_to'] = customer_name
                if is_direct_customer and direct_phone:
                    target_batch['sold_contact'] = direct_phone
                    target_batch['customer_type'] = 'Direct Customer'
                target_batch['sold_at_price'] = float(price_val)
                target_batch['sold_total'] = float(total_val)
                target_batch['paid'] = float(paid_val)
                target_batch['pending'] = float(pending_val)
                target_batch['payment_method'] = payment_method
                target_batch['payment_status'] = payment_status
                target_batch['sold_date'] = datetime.date.today().strftime('%d %b %Y')

            split.harvest_batches = batches
            all_sold = all(b.get('status') == 'Sold' for b in batches)
            split.stage = 'Sold' if all_sold else 'Harvested'
            ready_yield = sum(
                float(batch.get('yield') or 0)
                for batch in batches
                if batch.get('status') not in ('Sold', 'Loss / Discard')
            )
            split.yield_tons = max(Decimal('0'), Decimal(str(round(ready_yield, 2))))
        else:
            split.stage = 'Sold'
            split.yield_tons = Decimal('0')

        split.save()

        transaction_record = None
        if total_val > 0:
            customer_query = FarmerCustomer.objects.filter(
                farmer=get_farmer_profile(request.user),
                name=customer_name,
            )
            if is_direct_customer and direct_phone:
                customer_query = customer_query.filter(contact_number=direct_phone)
            customer_instance = customer_query.first()
            if customer_instance is None and customer_name:
                customer_instance = FarmerCustomer.objects.create(
                    farmer=get_farmer_profile(request.user),
                    name=customer_name,
                    contact_number=direct_phone if is_direct_customer else '',
                    status='Active',
                )

            pending_note = (
                f' [Paid: ₹{int(paid_val)}, Pending: ₹{int(pending_val)}]'
                if pending_val > 0
                else ''
            )
            transaction_record = LedgerTransaction.objects.create(
                farmer=get_farmer_profile(request.user),
                farm=split.farm,
                close_date=today_date.today(),
                description=(
                    f"Sale: {crop_name} ({quality_str}) to {customer_name}"
                    f"{pending_note}"
                ),
                payment_method=payment_method,
                category='Sales',
                category_color=(
                    '#28a745' if pending_val <= 0
                    else '#f59e0b' if paid_val > 0
                    else '#dc3545'
                ),
                tags=f'{crop_name} Sale ({quality_str}) - CropSplit #{split.id}',
                status=payment_status,
                amount=total_val,
                quantity=yield_val if yield_val > 0 else None,
                unit=unit_val,
            )
            if pending_val > 0:
                FarmerOutstanding.objects.create(
                    farmer=get_farmer_profile(request.user),
                    customer=customer_instance,
                    invoice_no=f'Sale #{transaction_record.id}: {crop_name} - {customer_name}',
                    due_date=today_date.today(),
                    amount_total=total_val,
                    amount_paid=paid_val,
                    status='Partial' if paid_val > 0 else 'Unpaid',
                )

        log_action(
            request,
            "Crop Segment Sold",
            f"Sold {yield_val} {unit_val} of '{crop_name}' ({quality_str}) to '{customer_name}' (Total: ₹{total_val}).",
            icon_class="ri-money-dollar-circle-line",
            bg_color="success",
        )

        return Response({
            'success': True,
            'message': 'Crop sold successfully.',
            'sale': {
                'id': transaction_record.id if transaction_record else None,
                'description': transaction_record.description if transaction_record else f"Sale: {crop_name} ({quality_str}) to {customer_name}",
                'date_formatted': today_date.today().strftime('%d %b %Y'),
                'created_at_iso': transaction_record.created_at.isoformat() if transaction_record and hasattr(transaction_record, 'created_at') and transaction_record.created_at else datetime.datetime.now().isoformat(),
                'close_date': today_date.today().strftime('%Y-%m-%d'),
                'status': payment_status,
                'tags': transaction_record.tags if transaction_record else '',
                'quantity': float(yield_val),
                'unit': unit_val,
                'amount': float(total_val),
                'paid': float(paid_val),
                'pending': float(pending_val),
                'payment_method': payment_method,
                'customer': customer_name,
            },
            'batches': batches,
            'stage': split.stage,
        })


# ═══════════════════════════════════════════════════════════════
# Mobile - Yield Process Steps
# Complete step-by-step crop lifecycle management for mobile
# ═══════════════════════════════════════════════════════════════

YIELD_STAGES = ['Sown', 'Vegetative', 'Flowering', 'Yield', 'Harvested', 'Sold']

STAGE_DESCRIPTIONS = {
    'Sown':      {'step': 1, 'label': 'Sown',       'icon': 'seed',     'description': 'Seeds have been planted. Monitor soil moisture and temperature.'},
    'Vegetative':{'step': 2, 'label': 'Vegetative',  'icon': 'sprout',   'description': 'Crop is in vegetative growth. Apply fertilizers and manage weeds.'},
    'Flowering': {'step': 3, 'label': 'Flowering',   'icon': 'flower',   'description': 'Crop is flowering. Control pests and manage irrigation carefully.'},
    'Yield':     {'step': 4, 'label': 'Yield Ready', 'icon': 'grain',    'description': 'Crop is ready to harvest. Finalize yield tonnage and pricing.'},
    'Harvested': {'step': 5, 'label': 'Harvested',   'icon': 'harvest',  'description': 'Crop has been harvested. Ready to sell to buyers/mandis.'},
    'Sold':      {'step': 6, 'label': 'Sold',        'icon': 'sold',     'description': 'Crop has been sold. Transaction recorded in ledger.'},
}


class YieldProcessStatusAPIView(APIView):
    """
    Returns the full step-by-step yield journey of a specific crop split.
    Shows current stage, completed steps, pending steps, and next action.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Yield Process Journey for a Crop Split',
        operation_description=(
            'Returns the complete 6-step cultivation journey for a crop split:\n\n'
            '**Step 1 → Sown**: Seeds planted\n'
            '**Step 2 → Vegetative**: Growth phase\n'
            '**Step 3 → Flowering**: Flowering phase\n'
            '**Step 4 → Yield Ready**: Ready to harvest — call `/finalize-harvest/`\n'
            '**Step 5 → Harvested**: Yield recorded — call `/sell/`\n'
            '**Step 6 → Sold**: Transaction complete\n\n'
            'Use `next_action` in the response to know which mobile screen to show next.'
        ),
        tags=['Mobile - 05. Yield Process Journey'],
        responses={
            200: openapi.Response('Yield process journey data'),
            404: openapi.Response('Crop split not found'),
        }
    )
    def get(self, request, pk):
        try:
            split = CropSplit.objects.select_related('crop', 'farm', 'supervisor').get(
                id=pk, farm__farmer=get_farmer_profile(request.user)
            )
        except CropSplit.DoesNotExist:
            return Response({'error': 'Crop split not found.'}, status=404)

        current_stage = split.stage
        current_step = STAGE_DESCRIPTIONS.get(current_stage, {}).get('step', 0)

        steps = []
        for stage, info in STAGE_DESCRIPTIONS.items():
            step_num = info['step']
            steps.append({
                'step': step_num,
                'stage': stage,
                'label': info['label'],
                'icon': info['icon'],
                'description': info['description'],
                'status': 'completed' if step_num < current_step
                          else 'current' if step_num == current_step
                          else 'pending',
            })

        # Determine next action
        next_action = None
        can_advance = False
        if current_stage in ['Sown', 'Vegetative', 'Flowering']:
            next_action = {
                'label': f'Advance to {YIELD_STAGES[YIELD_STAGES.index(current_stage) + 1]}',
                'endpoint': f'POST /api/farmer/crop-splits/{split.id}/advance/',
                'action_type': 'advance',
            }
            can_advance = True
        elif current_stage == 'Yield':
            next_action = {
                'label': 'Finalize Harvest (Enter Yield & Price)',
                'endpoint': f'POST /api/farmer/crop-splits/{split.id}/finalize-harvest/',
                'action_type': 'finalize_harvest',
            }
        elif current_stage == 'Harvested':
            next_action = {
                'label': 'Sell Crop to Buyer',
                'endpoint': f'POST /api/farmer/crop-splits/{split.id}/sell/',
                'action_type': 'sell',
            }
        else:
            next_action = {'label': 'Crop lifecycle complete', 'action_type': 'complete'}

        return Response({
            'split_id': split.id,
            'crop_name': split.crop.name if split.crop else 'Unknown',
            'farm_name': split.farm.land_name,
            'acreage': str(split.acreage),
            'current_stage': current_stage,
            'current_step': current_step,
            'total_steps': 6,
            'progress_percent': round((current_step / 6) * 100),
            'start_date': str(split.start_date),
            'growth_duration_days': split.growth_duration,
            'supervisor': split.supervisor.name if split.supervisor else None,
            'yield_tons': str(split.yield_tons) if split.yield_tons else None,
            'price_per_mt': str(split.price_per_mt) if split.price_per_mt else None,
            'total_sale_value': str(split.total_sale_value) if split.total_sale_value else None,
            'can_advance': can_advance,
            'next_action': next_action,
            'steps': steps,
        })


class YieldStageAdvanceAPIView(APIView):
    """
    Explicitly advance a crop split to the next stage.
    This is an alternate to the ViewSet `advance` action — designed for simpler mobile integration.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Advance Crop to Next Stage (Step-by-Step)',
        operation_description=(
            'Moves the crop split to the next growth stage in sequence:\n\n'
            '`Sown → Vegetative → Flowering → Yield`\n\n'
            'After reaching **Yield**, use `POST /crop-splits/{id}/finalize-harvest/` instead.\n'
            'Returns updated stage info and the next required action.'
        ),
        tags=['Mobile - 05. Yield Process Journey'],
        responses={
            200: openapi.Response('Stage advanced successfully'),
            400: openapi.Response('Cannot advance — already at Yield or beyond'),
            404: openapi.Response('Crop split not found'),
        }
    )
    def post(self, request, pk):
        try:
            split = CropSplit.objects.select_related('crop', 'farm', 'supervisor').get(
                id=pk, farm__farmer=get_farmer_profile(request.user)
            )
        except CropSplit.DoesNotExist:
            return Response({'error': 'Crop split not found.'}, status=404)

        advance_stages = ['Sown', 'Vegetative', 'Flowering', 'Yield']
        if split.stage not in advance_stages:
            return Response({
                'error': f'Cannot advance. Current stage is "{split.stage}". Use finalize-harvest or sell endpoints.',
            }, status=400)

        idx = advance_stages.index(split.stage)
        if idx >= len(advance_stages) - 1:
            return Response({
                'error': 'Crop is already at Yield stage. Use finalize-harvest to record yield.',
                'next_endpoint': f'POST /api/farmer/crop-splits/{split.id}/finalize-harvest/',
            }, status=400)

        old_stage = split.stage
        split.stage = advance_stages[idx + 1]
        split.save()

        # Log activity automatically
        FarmActivity.objects.create(
            farm=split.farm,
            activity_type='Other Land Care',
            asset_name=f"{split.crop.name if split.crop else 'Crop'} Stage Advanced",
            quantity_metric=f"{split.acreage} Ac",
            operator_name=split.supervisor.name if split.supervisor else 'Farm Manager',
            notes=f"Stage advanced from {old_stage} to {split.stage}."
        )

        new_step = STAGE_DESCRIPTIONS.get(split.stage, {})
        return Response({
            'success': True,
            'split_id': split.id,
            'old_stage': old_stage,
            'new_stage': split.stage,
            'step': new_step.get('step'),
            'progress_percent': round((new_step.get('step', 1) / 6) * 100),
            'message': f'✅ Crop advanced to {split.stage} successfully.',
            'next_action': {
                'label': 'Finalize Harvest' if split.stage == 'Yield' else f'Advance to {advance_stages[advance_stages.index(split.stage) + 1]}' if split.stage in advance_stages[:-1] else '',
                'endpoint': f'POST /api/farmer/crop-splits/{split.id}/finalize-harvest/' if split.stage == 'Yield' else f'POST /api/farmer/crop-splits/{split.id}/advance/',
            }
        })


class YieldHarvestFinalizeAPIView(APIView):
    """
    Finalize harvest for a crop split at Yield stage.
    Records the actual yield tonnage and expected sale price.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Step 5 — Finalize Harvest (Enter Yield Tons & Price)',
        operation_description=(
            'Called when crop is at **Yield** stage. Records the actual harvest data:\n\n'
            '- `yield_tons` — Total quantity harvested (in tons)\n'
            '- `price_per_mt` — Expected price per metric ton (optional)\n'
            '- `total_sale_value` — Total expected sale value (optional, auto-calculated if not provided)\n\n'
            'After this, the crop moves to **Harvested** stage and is ready to sell.'
        ),
        request_body=CropHarvestSerializer,
        tags=['Mobile - 05. Yield Process Journey'],
        responses={
            200: openapi.Response('Harvest finalized. Crop is now Harvested.'),
            400: openapi.Response('Validation error or wrong stage'),
            404: openapi.Response('Crop split not found'),
        }
    )
    def post(self, request, pk):
        try:
            split = CropSplit.objects.select_related('crop', 'farm', 'supervisor').get(
                id=pk, farm__farmer=get_farmer_profile(request.user)
            )
        except CropSplit.DoesNotExist:
            return Response({'error': 'Crop split not found.'}, status=404)

        if split.stage != 'Yield':
            return Response({
                'error': f'Crop is at "{split.stage}" stage. Finalize harvest is only available at Yield stage.',
                'current_stage': split.stage,
            }, status=400)

        serializer = CropHarvestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        yield_tons = serializer.validated_data['yield_tons']
        price_per_mt = serializer.validated_data.get('price_per_mt')
        total_sale_value = serializer.validated_data.get('total_sale_value')

        # Auto-calculate total if price given
        if price_per_mt and not total_sale_value:
            total_sale_value = float(yield_tons) * float(price_per_mt)

        split.stage = 'Harvested'
        split.yield_tons = yield_tons
        if price_per_mt:
            split.price_per_mt = price_per_mt
        if total_sale_value:
            split.total_sale_value = total_sale_value
        split.save()

        FarmActivity.objects.create(
            farm=split.farm,
            activity_type='Other Land Care',
            asset_name=f"{split.crop.name if split.crop else 'Crop'} Harvested",
            quantity_metric=f"{yield_tons} Tons",
            operator_name=split.supervisor.name if split.supervisor else 'Farm Manager',
            notes=f"Harvest finalized: {yield_tons} tons at ₹{price_per_mt}/MT." if price_per_mt else f"Harvest finalized: {yield_tons} tons."
        )

        return Response({
            'success': True,
            'split_id': split.id,
            'stage': 'Harvested',
            'step': 5,
            'progress_percent': round((5 / 6) * 100),
            'yield_tons': str(yield_tons),
            'price_per_mt': str(price_per_mt) if price_per_mt else None,
            'total_sale_value': str(total_sale_value) if total_sale_value else None,
            'message': '✅ Harvest finalized. Crop is now ready to sell.',
            'next_action': {
                'label': 'Sell Crop to Buyer',
                'endpoint': f'POST /api/farmer/crop-splits/{split.id}/sell/',
            }
        })


class YieldProcessSummaryAPIView(APIView):
    """
    Farm-level overview of all crops grouped by stage.
    Shows the complete yield pipeline for a farmer at a glance.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Yield Process Summary — All Crops by Stage',
        operation_description=(
            'Returns a complete overview of all crop splits grouped by their current stage.\n\n'
            'Use this as the **home screen** for the Yield Process module in the mobile app.\n\n'
            'Optional: Pass `?farm_id=<id>` to filter by a specific farm.'
        ),
        tags=['Mobile - 05. Yield Process Journey'],
        manual_parameters=[farm_id_param],
        responses={200: openapi.Response('Summary of all crop stages')}
    )
    def get(self, request):
        farm_id = request.query_params.get('farm_id')
        qs = CropSplit.objects.filter(
            farm__farmer=get_farmer_profile(request.user)
        ).select_related('crop', 'farm')

        if farm_id:
            qs = qs.filter(farm_id=farm_id)

        summary = {}
        for stage, info in STAGE_DESCRIPTIONS.items():
            splits_at_stage = qs.filter(stage=stage)
            summary[stage] = {
                'step': info['step'],
                'label': info['label'],
                'count': splits_at_stage.count(),
                'splits': [
                    {
                        'id': s.id,
                        'crop_name': s.crop.name if s.crop else 'Unknown',
                        'farm_name': s.farm.land_name,
                        'acreage': str(s.acreage),
                        'yield_tons': str(s.yield_tons) if s.yield_tons else None,
                        'progress_percent': round((info['step'] / 6) * 100),
                    }
                    for s in splits_at_stage
                ]
            }

        total = qs.count()
        active = qs.exclude(stage__in=['Harvested', 'Sold']).count()
        harvested = qs.filter(stage='Harvested').count()
        sold = qs.filter(stage='Sold').count()

        return Response({
            'total_crop_splits': total,
            'active_crops': active,
            'ready_to_harvest': qs.filter(stage='Yield').count(),
            'ready_to_sell': harvested,
            'sold': sold,
            'pipeline': summary,
        })


# ═══════════════════════════════════════════════════════════════
# Mobile - Legacy Compatibility Endpoints
# These endpoints support the Flutter forming module's
# existing HTTP calls for land/crop/task CRUD operations.
# ═══════════════════════════════════════════════════════════════

class DeactivateLandAPIView(APIView):
    """
    Delete a farm land.
    Called by Flutter via POST /deactivate_my_land/ with body: {id: landId}
    Matches website delete_farm() behavior (hard delete).
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Delete a Farm Land (Mobile)',
        operation_description='Deletes a farm land owned by the authenticated farmer. Matches website delete_farm behavior.',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['id'],
            properties={'id': openapi.Schema(type=openapi.TYPE_INTEGER, description='Farm/Land ID to delete')}
        ),
        responses={200: openapi.Response('Land deleted'), 404: openapi.Response('Land not found')},
        tags=['Mobile - 03. Farm & Land Management']
    )
    def post(self, request):
        land_id = request.data.get('id')
        if not land_id:
            return Response({'error': 'id is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            farm = Farm.objects.get(id=land_id, farmer=get_farmer_profile(request.user))
        except Farm.DoesNotExist:
            return Response({'error': 'Farm not found'}, status=status.HTTP_404_NOT_FOUND)

        from apps.farmer.deletion_rules import farm_delete_block_reason

        block_reason = farm_delete_block_reason(farm)
        if block_reason:
            return Response({'error': block_reason}, status=status.HTTP_400_BAD_REQUEST)

        land_name = farm.land_name
        farm.delete()
        return Response({'success': True, 'message': f"Farm '{land_name}' deleted successfully."}, status=status.HTTP_200_OK)


class DeactivateScheduleAPIView(APIView):
    """
    Soft-delete (deactivate) a farm task.
    Called by Flutter via POST /deactivate-my-schedule/ with body: {id: taskId}
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Deactivate / Delete a Task (Mobile)',
        operation_description='Soft-deletes a farm task owned by the authenticated farmer.',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['id'],
            properties={'id': openapi.Schema(type=openapi.TYPE_INTEGER, description='Task ID to deactivate')}
        ),
        responses={200: openapi.Response('Task deactivated'), 404: openapi.Response('Task not found')},
        tags=['Mobile - 03. Farm & Land Management']
    )
    def post(self, request):
        task_id = request.data.get('id')
        if not task_id:
            return Response({'error': 'id is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            task = FarmTask.objects.get(id=task_id, farmer=get_farmer_profile(request.user))
        except FarmTask.DoesNotExist:
            return Response({'error': 'Task not found'}, status=status.HTTP_404_NOT_FOUND)

        task.is_active = False
        task.save(update_fields=['is_active'])
        return Response({'success': True, 'message': 'Task deactivated successfully.'}, status=status.HTTP_200_OK)


class DeleteCropAPIView(APIView):
    """
    Soft-delete a crop type.
    Called by Flutter via POST /delete_crop/ with body: {id: cropId}
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Delete a Crop Type (Mobile)',
        operation_description='Soft-deletes a crop type from the system dropdown.',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['id'],
            properties={'id': openapi.Schema(type=openapi.TYPE_INTEGER, description='Crop ID to delete')}
        ),
        responses={200: openapi.Response('Crop deleted'), 404: openapi.Response('Crop not found')},
        tags=['Mobile - 13. System Dropdowns']
    )
    def post(self, request):
        crop_id = request.data.get('id')
        if not crop_id:
            return Response({'error': 'id is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            crop = Crop.objects.get(id=crop_id)
        except Crop.DoesNotExist:
            return Response({'error': 'Crop not found'}, status=status.HTTP_404_NOT_FOUND)

        crop.is_active = False
        crop.save(update_fields=['is_active'])
        return Response({'success': True, 'message': 'Crop deactivated successfully.'}, status=status.HTTP_200_OK)


class CropDetailsLandAPIView(APIView):
    """
    Get crop overview for a specific land+crop combination.
    Called by Flutter via POST /crop_details_land/ with body: {land_id, crop_id}
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Crop Overview by Land + Crop (Mobile)',
        operation_description='Returns aggregated crop overview data for a specific farm land and crop combination. Used by the Flutter forming module.',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['land_id', 'crop_id'],
            properties={
                'land_id': openapi.Schema(type=openapi.TYPE_INTEGER, description='Farm/Land ID'),
                'crop_id': openapi.Schema(type=openapi.TYPE_INTEGER, description='Crop ID'),
            }
        ),
        responses={200: openapi.Response('Crop overview data'), 404: openapi.Response('Not found')},
        tags=['Mobile - 04. Crop Splits & Cultivation']
    )
    def post(self, request):
        land_id = request.data.get('land_id')
        crop_id = request.data.get('crop_id')
        if not land_id or not crop_id:
            return Response({'error': 'land_id and crop_id are required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            farm = Farm.objects.get(id=land_id, farmer=get_farmer_profile(request.user))
        except Farm.DoesNotExist:
            return Response({'error': 'Farm not found'}, status=status.HTTP_404_NOT_FOUND)

        splits = CropSplit.objects.filter(farm=farm, crop_id=crop_id).select_related('crop', 'supervisor')
        if not splits.exists():
            return Response({'error': 'No crop splits found for this land and crop'}, status=status.HTTP_404_NOT_FOUND)

        total_acreage = sum(float(s.acreage or 0) for s in splits)
        first_split = splits.first()
        tasks = FarmTask.objects.filter(farm=farm, associated_split__in=splits)

        return Response({
            'land_id': farm.id,
            'land_name': farm.land_name,
            'crop_id': crop_id,
            'crop_name': first_split.crop.name if first_split and first_split.crop else '',
            'total_acreage': str(total_acreage),
            'split_count': splits.count(),
            'splits': CropSplitSerializer(splits, many=True).data,
            'task_count': tasks.count(),
            'pending_tasks': tasks.filter(status='Pending').count(),
            'completed_tasks': tasks.filter(status='Completed').count(),
        }, status=status.HTTP_200_OK)


class CropViewDetailAPIView(APIView):
    """
    Get full crop details for a specific land+crop combination.
    Called by Flutter via GET /crop_view/{landId}/{cropId}/
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Full Crop Details by Land + Crop (Mobile)',
        operation_description='Returns complete crop details including all splits, tasks, activities, and expenses for a specific farm land and crop.',
        responses={200: openapi.Response('Full crop details'), 404: openapi.Response('Not found')},
        tags=['Mobile - 04. Crop Splits & Cultivation']
    )
    def get(self, request, land_id, crop_id):
        try:
            farm = Farm.objects.get(id=land_id, farmer=get_farmer_profile(request.user))
        except Farm.DoesNotExist:
            return Response({'error': 'Farm not found'}, status=status.HTTP_404_NOT_FOUND)

        splits = CropSplit.objects.filter(farm=farm, crop_id=crop_id).select_related('crop', 'supervisor')
        if not splits.exists():
            return Response({'error': 'No crop splits found'}, status=status.HTTP_404_NOT_FOUND)

        first_split = splits.first()
        tasks = FarmTask.objects.filter(farm=farm, associated_split__in=splits).order_by('-created_at')
        activities = FarmActivity.objects.filter(farm=farm).order_by('-created_at')

        return Response({
            'land_id': farm.id,
            'land_name': farm.land_name,
            'total_area': str(farm.total_area),
            'latitude': str(farm.latitude) if farm.latitude else None,
            'longitude': str(farm.longitude) if farm.longitude else None,
            'crop_id': crop_id,
            'crop_name': first_split.crop.name if first_split and first_split.crop else '',
            'splits': CropSplitSerializer(splits, many=True).data,
            'tasks': FarmTaskSerializer(tasks, many=True).data,
            'activities': FarmActivitySerializer(activities, many=True).data,
        }, status=status.HTTP_200_OK)


class EditMyLandAPIView(APIView):
    """
    Update crop split details.
    Called by Flutter via POST /edit_my_land/ with arbitrary map.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Update Crop Split Details (Mobile)',
        operation_description='Updates crop split details. Pass split_id in the body along with fields to update.',
        responses={200: openapi.Response('Updated'), 400: openapi.Response('Validation error'), 404: openapi.Response('Not found')},
        tags=['Mobile - 04. Crop Splits & Cultivation']
    )
    def post(self, request):
        split_id = request.data.get('split_id') or request.data.get('id')
        if not split_id:
            return Response({'error': 'split_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            split = CropSplit.objects.get(id=split_id, farm__farmer=get_farmer_profile(request.user))
        except CropSplit.DoesNotExist:
            return Response({'error': 'Crop split not found'}, status=status.HTTP_404_NOT_FOUND)

        updatable_fields = [
            'cultivation_method', 'stage', 'acreage', 'growth_duration',
            'yield_tons', 'price_per_mt', 'total_sale_value',
            'crop_area', 'crop_latitude', 'crop_longitude',
            'row_count', 'plant_count', 'crop_variety', 'duration_min', 'duration_max',
            'expected_yield', 'expected_yield_unit', 'crop_notes', 'seed_details', 'plant_spacing',
        ]
        updated = []
        for field in updatable_fields:
            if field in request.data:
                setattr(split, field, request.data[field])
                updated.append(field)

        if 'crop' in request.data:
            split.crop_id = request.data['crop']
            updated.append('crop')
        if 'supervisor' in request.data:
            split.supervisor_id = request.data['supervisor']
            updated.append('supervisor')
        if 'sowing_date' in request.data:
            split.start_date = request.data['sowing_date']
            updated.append('start_date')
        if 'harvest_type' in request.data:
            split.harvest_type = request.data['harvest_type']
            updated.append('harvest_type')

        # Total planned flushes / pickings (Multiple Harvest), same
        # defaults as the website (single -> 1, multiple -> 3).
        h_type = split.harvest_type or 'single'
        is_multi = h_type in ['multiple', 'Multiple Harvest']
        if 'total_flushes' in request.data:
            try:
                split.total_flushes = max(1, min(50, int(request.data['total_flushes'])))
            except (TypeError, ValueError):
                split.total_flushes = 3 if is_multi else 1
            updated.append('total_flushes')
        elif 'harvest_type' in request.data:
            split.total_flushes = 3 if is_multi else 1
            updated.append('total_flushes')
        elif split.total_flushes in [None, 0]:
            split.total_flushes = 3 if is_multi else 1
            updated.append('total_flushes')

        if updated:
            split.save(update_fields=updated)

        return Response({'success': True, 'message': 'Crop split updated.', 'updated_fields': updated}, status=status.HTTP_200_OK)


class TaskListForMonthCropAPIView(APIView):
    """
    Get task groups for a specific month, crop and land.
    Called by Flutter via GET /tasks_list/for-month-and-crop/?land_id=&crop_id=&month=&year=
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Task Groups for Month & Crop (Mobile)',
        operation_description='Returns tasks grouped by day for a specific land, crop, month and year. Used for the calendar/task list view.',
        manual_parameters=[
            openapi.Parameter('land_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True),
            openapi.Parameter('crop_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True),
            openapi.Parameter('month', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True),
            openapi.Parameter('year', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True),
        ],
        responses={200: openapi.Response('Task groups')},
        tags=['Mobile - 03. Farm & Land Management']
    )
    def get(self, request):
        land_id = request.query_params.get('land_id')
        crop_id = request.query_params.get('crop_id')
        month = request.query_params.get('month')
        year = request.query_params.get('year')

        if not all([land_id, crop_id, month, year]):
            return Response({'error': 'land_id, crop_id, month, and year are required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            splits = CropSplit.objects.filter(
                farm_id=land_id, farm__farmer=get_farmer_profile(request.user), crop_id=crop_id
            ).values_list('id', flat=True)
        except Exception:
            splits = []

        from datetime import date as date_type
        import calendar as cal

        month_int = int(month)
        year_int = int(year)
        _, last_day = cal.monthrange(year_int, month_int)
        start_date = date_type(year_int, month_int, 1)
        end_date = date_type(year_int, month_int, last_day)

        tasks = FarmTask.objects.filter(
            farmer=get_farmer_profile(request.user),
            farm_id=land_id,
        )
        if splits:
            tasks = tasks.filter(associated_split_id__in=list(splits))

        tasks = tasks.filter(
            task_date__gte=start_date,
            task_date__lte=end_date,
        ).order_by('task_date')

        # Group by date
        groups = {}
        for task in tasks:
            day_key = str(task.task_date) if task.task_date else 'unknown'
            if day_key not in groups:
                groups[day_key] = []
            groups[day_key].append({
                'id': task.id,
                'title': task.title,
                'description': task.description,
                'priority': task.priority,
                'status': task.status,
                'task_date': str(task.task_date) if task.task_date else None,
                'work_timeline': task.work_timeline,
                'assigned_worker_name': task.assigned_worker.name if task.assigned_worker else None,
            })

        result = [{'date': k, 'tasks': v} for k, v in sorted(groups.items())]
        return Response(result, status=status.HTTP_200_OK)


class TasksForMonthCropAPIView(APIView):
    """
    Get flat task list for a specific month, crop and land.
    Called by Flutter via GET /tasks/for-month-and-crop?land_id=&crop_id=&month=&year=
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Tasks for Month & Crop (Flat List)',
        operation_description='Returns a flat list of tasks for a specific land, crop, month and year.',
        manual_parameters=[
            openapi.Parameter('land_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True),
            openapi.Parameter('crop_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True),
            openapi.Parameter('month', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True),
            openapi.Parameter('year', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True),
        ],
        responses={200: openapi.Response('Tasks list')},
        tags=['Mobile - 03. Farm & Land Management']
    )
    def get(self, request):
        land_id = request.query_params.get('land_id')
        crop_id = request.query_params.get('crop_id')
        month = request.query_params.get('month')
        year = request.query_params.get('year')

        if not all([land_id, crop_id, month, year]):
            return Response({'error': 'land_id, crop_id, month, and year are required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            splits = CropSplit.objects.filter(
                farm_id=land_id, farm__farmer=get_farmer_profile(request.user), crop_id=crop_id
            ).values_list('id', flat=True)
        except Exception:
            splits = []

        from datetime import date as date_type
        import calendar as cal

        month_int = int(month)
        year_int = int(year)
        _, last_day = cal.monthrange(year_int, month_int)
        start_date = date_type(year_int, month_int, 1)
        end_date = date_type(year_int, month_int, last_day)

        tasks = FarmTask.objects.filter(
            farmer=get_farmer_profile(request.user),
            farm_id=land_id,
        )
        if splits:
            tasks = tasks.filter(associated_split_id__in=list(splits))

        tasks = tasks.filter(
            task_date__gte=start_date,
            task_date__lte=end_date,
        ).order_by('task_date')

        return Response(FarmTaskSerializer(tasks, many=True).data, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────
# Mobile - Dashboard (split into fast per-section endpoints)
# Each builder returns only the keys it serves; the legacy
# /dashboard/ endpoint composes them for backward compatibility.
# ─────────────────────────────────────────────
def _dashboard_today(request):
    """Dashboard date: client-pinned (attendance rows use device dates) with server fallback."""
    raw = request.query_params.get('date')
    if raw:
        try:
            return datetime.strptime(raw, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            pass
    return datetime.now().date()


def _dashboard_range(request):
    today = _dashboard_today(request)
    start_raw = request.query_params.get('start_date')
    end_raw = request.query_params.get('end_date')
    try:
        start = datetime.strptime(start_raw, '%Y-%m-%d').date() if start_raw else None
        end = datetime.strptime(end_raw, '%Y-%m-%d').date() if end_raw else None
    except (ValueError, TypeError):
        start = end = None
    if not start or not end or start > end:
        start = today - timedelta(days=today.weekday())
        end = today
    return today, start, end


def _dash_farms(user):
    """Farms queryset (already iterated, so queryset cache is warm) + parsed total acres."""
    from apps.farmer.models import Farm

    farms_qs = Farm.objects.filter(farmer=user)
    total_acres = 0.0
    for f in farms_qs:
        if f.total_area:
            try:
                clean_area = ''.join(c for c in str(f.total_area) if c.isdigit() or c == '.')
                if clean_area:
                    total_acres += float(clean_area)
            except (ValueError, TypeError):
                pass
    return farms_qs, total_acres


def _dash_tasks(user, start_date=None, end_date=None):
    """All tasks for the farmer (assigned directly or via their farms)."""
    from apps.farmer.models import FarmTask

    tasks = FarmTask.objects.filter(Q(farmer=user) | Q(farm__farmer=user)).distinct()
    if start_date and end_date:
        tasks = tasks.filter(task_date__gte=start_date, task_date__lte=end_date)
    return tasks


def _dash_staff_today(user, start_date=None, end_date=None):
    """Staff queryset + today's attendance rows."""
    from apps.farmer.models import FarmerStaff, StaffAttendance

    staff_qs = FarmerStaff.objects.filter(farmer=user)
    attendances = StaffAttendance.objects.filter(staff__farmer=user)
    if start_date and end_date:
        attendances = attendances.filter(date__gte=start_date, date__lte=end_date)
    return staff_qs, attendances


def _dash_sales_expenses(user, start_date=None, end_date=None):
    """Inflow/outflow ledger splits using the website dashboard exclusions."""
    from apps.farmer.models import LedgerTransaction
    from django.db.models import Sum
    from django.db.models.functions import Abs

    transactions = LedgerTransaction.objects.filter(farmer=user).exclude(
        category='Harvest Trade',
        description__istartswith='Harvest Trade Settlement',
    )
    if start_date and end_date:
        transactions = transactions.filter(
            close_date__gte=start_date,
            close_date__lte=end_date,
        )
    sales_qs = transactions.filter(
        Q(tags__icontains='Inflow') | Q(amount__gt=0) | Q(status='Received')
    ).exclude(category='Customer Payment').distinct()
    expenses_qs = transactions.filter(
        Q(tags__icontains='Outflow') | Q(amount__lt=0) | Q(status__in=['Paid', 'Cost'])
    ).distinct()
    total_sales = float(sales_qs.aggregate(total=Sum(Abs('amount')))['total'] or 0)
    total_expenses = float(expenses_qs.aggregate(total=Sum(Abs('amount')))['total'] or 0)
    return sales_qs, expenses_qs, total_sales, total_expenses


def _mobile_dashboard_overview(user, today, start_date=None, end_date=None):
    """Hero, KPI cards, farm status, weather, subscription — light aggregates only."""
    from apps.farmer.models import CropSplit, FarmerOutstanding, FarmerInventory
    from apps.superadmin.models import Subscription

    farms_qs, total_acres = _dash_farms(user)
    total_farms = farms_qs.count()

    all_splits = CropSplit.objects.filter(farm__farmer=user)
    active_crops = all_splits.exclude(stage__iexact='Harvested').count()
    harvested_crops = all_splits.filter(stage__iexact='Harvested').count()

    all_tasks = _dash_tasks(user, start_date, end_date)
    total_tasks = all_tasks.count()
    pending_tasks = all_tasks.filter(status='Pending').count()
    completed_tasks = all_tasks.filter(status='Completed').count()

    staff_qs, attendances = _dash_staff_today(user, start_date, end_date)
    total_staff = staff_qs.count()
    present_today = attendances.filter(status='P').count()

    # Hero financials match the website's lifetime totals; timeline filters
    # remain in effect for analytics charts.
    _, _, total_sales, total_expenses = _dash_sales_expenses(user)
    net_profit = total_sales - total_expenses

    outstanding_qs = FarmerOutstanding.objects.filter(farmer=user)
    customer_outs = outstanding_qs.exclude(invoice_no__startswith='Vendor:').exclude(status='Paid')
    receivables = sum(max(0.0, float(o.amount_total) - float(o.amount_paid)) for o in customer_outs)
    vendor_outs = outstanding_qs.filter(invoice_no__startswith='Vendor:').exclude(status='Paid')
    payables = sum(max(0.0, float(o.amount_total) - float(o.amount_paid)) for o in vendor_outs)

    inventory_value = sum(
        float(i.quantity or 0) * float(i.unit_price or 0)
        for i in FarmerInventory.objects.filter(farmer=user)
    )
    business_value = total_sales + inventory_value + receivables

    # ── Farm Health (website: health_indicator based) ──
    healthy_count = farms_qs.filter(health_indicator__in=['Good', 'Healthy', 'Optimal']).count()
    attention_count = farms_qs.filter(health_indicator__in=['Moderate', 'Needs Attention', 'Fair']).count()
    critical_count = farms_qs.filter(health_indicator__in=['Poor', 'Critical']).count()
    if total_farms > 0:
        if healthy_count == 0 and attention_count == 0 and critical_count == 0:
            healthy_count = total_farms
        avg_health = round((healthy_count / total_farms) * 100)
    else:
        avg_health = 75

    # ── Soil Health ──
    ph_values = []
    for f in farms_qs:
        if f.soil_ph:
            try:
                clean_ph = ''.join(c for c in str(f.soil_ph) if c.isdigit() or c == '.')
                if clean_ph:
                    ph_values.append(float(clean_ph))
            except (ValueError, TypeError):
                pass
    avg_soil_ph = round(sum(ph_values) / len(ph_values), 1) if ph_values else 6.5

    crop_health_chips = []
    for farm in farms_qs[:6]:
        health = farm.health_indicator or 'Good'
        color = 'success'
        if health in ['Moderate', 'Needs Attention', 'Fair']:
            color = 'warning'
        elif health in ['Poor', 'Critical']:
            color = 'danger'
        crop_health_chips.append({
            'name': farm.land_name,
            'status': health,
            'color': color,
        })

    # ── Subscription / package acre limit ──
    subscription = Subscription.objects.filter(
        farmer=user, subscription_status='active'
    ).order_by('-id').first()
    active_subscription = subscription
    if not subscription:
        subscription = Subscription.objects.filter(farmer=user).order_by('-id').first()

    package_acre_limit = None
    if active_subscription and active_subscription.package:
        max_acre_value = str(active_subscription.package.max_acre_support or '').strip()
        if max_acre_value and max_acre_value.lower() != 'unlimited':
            clean_limit = ''.join(ch for ch in max_acre_value if ch.isdigit() or ch == '.')
            if clean_limit:
                try:
                    package_acre_limit = float(clean_limit)
                except (ValueError, TypeError):
                    package_acre_limit = None

    from apps.superadmin.subscription_utils import (
        billing_cycle_label,
        get_package_cycle_price,
        normalize_billing_cycle,
    )

    subscription_data = {}
    if subscription:
        days_left = (subscription.end_date - today).days if subscription.end_date else 0
        cycle = normalize_billing_cycle(subscription.billing_cycle)
        sub_price = (
            float(subscription.monthly_amount)
            if subscription.monthly_amount is not None
            else get_package_cycle_price(subscription.package, cycle)
        )
        subscription_data = {
            'plan_name': subscription.package.name if subscription.package else 'No Active Plan',
            'plan_price': sub_price,
            'plan_price_fmt': f'₹{sub_price:,.0f}',
            'renews_on': subscription.end_date.strftime('%b %d, %Y') if subscription.end_date else 'N/A',
            'days_left': max(0, days_left),
            'auto_renew': subscription.auto_renew,
            'billing_cycle': cycle,
            'billing_cycle_label': billing_cycle_label(cycle),
            'has_subscription': active_subscription is not None,
            'subscription_status': subscription.subscription_status,
        }
    else:
        subscription_data = {
            'plan_name': 'No Active Plan',
            'plan_price': 0.0,
            'plan_price_fmt': '₹0',
            'renews_on': 'N/A',
            'days_left': 0,
            'auto_renew': False,
            'billing_cycle': '',
            'billing_cycle_label': 'None',
            'has_subscription': False,
            'subscription_status': 'none',
        }

    # ── Weather (mock data for now - integrate with weather API later) ──
    weather_data = {
        'current': {
            'location': 'Coimbatore, Tamil Nadu',
            'temp': 31,
            'condition': 'Partly Cloudy',
            'feels_like': 34,
            'humidity': 78,
            'wind_speed': 14,
            'rain_chance': 10,
            'uv_index': 5,
            'icon_class': 'ri-cloudy-line',
        },
        'forecast': [
            {'label': 'Mon', 'date': (today + timedelta(days=i)).strftime('%d %b'), 'full_date': (today + timedelta(days=i)).strftime('%Y-%m-%d'), 'condition': 'Partly Cloudy', 'icon_class': 'ri-cloudy-line', 'temp_high': 33, 'temp_low': 24, 'feels_like': 35, 'rain_chance': 10, 'humidity': 75, 'wind_speed': 12, 'uv_index': 5}
            for i in range(7)
        ],
    }

    return {
        'total_business_value': business_value,
        'net_profit': net_profit,
        'inventory_value': inventory_value,
        'receivables': receivables,
        'payables': payables,
        'total_farms': total_farms,
        'total_acres': float(total_acres),
        'used_acres': float(total_acres),
        'package_acre_limit': package_acre_limit,
        'active_crops': active_crops,
        'harvested_crops': harvested_crops,
        'pending_tasks': pending_tasks,
        'completed_tasks': completed_tasks,
        'total_tasks': total_tasks,
        'total_staff': total_staff,
        'present_today': present_today,
        'farm_health_score': avg_health,
        'healthy_farms_count': healthy_count,
        'needs_attention_farms_count': attention_count,
        'critical_farms_count': critical_count,
        'total_sales': total_sales,
        'total_expenses': total_expenses,
        'subscription': subscription_data,
        'weather': weather_data,
        'avg_soil_ph': avg_soil_ph,
        'crop_health_chips': crop_health_chips,
    }


def _mobile_dashboard_analytics(user, today, start_date=None, end_date=None):
    """Sales / expense / task analytics chart data."""
    from django.db.models import Count, Sum
    from django.db.models.functions import Abs

    sales_qs, expenses_qs, total_sales, total_expenses = _dash_sales_expenses(user, start_date, end_date)

    all_tasks = _dash_tasks(user, start_date, end_date)
    total_tasks = all_tasks.count()
    pending_tasks = all_tasks.filter(status='Pending').count()
    completed_tasks = all_tasks.filter(status='Completed').count()
    in_progress_tasks = all_tasks.filter(status='In Progress').count()

    # ── Sales Categories ──
    sales_categories_data = list(sales_qs.values('category').annotate(
        total=Sum(Abs('amount'))
    ).order_by('-total')[:10])
    sales_categories = []
    for cat in sales_categories_data:
        cat_amount = abs(float(cat['total'] or 0))
        sales_categories.append({
            'name': cat['category'] or 'Uncategorized',
            'amount': cat_amount,
            'pct': round((cat_amount / total_sales * 100), 1) if total_sales > 0 else 0,
        })

    # ── Expense Categories ──
    expense_categories_data = list(expenses_qs.values('category').annotate(
        total=Sum(Abs('amount'))
    ).order_by('-total')[:10])
    expense_categories = []
    for cat in expense_categories_data:
        cat_amount = abs(float(cat['total'] or 0))
        expense_categories.append({
            'name': cat['category'] or 'Uncategorized',
            'amount': cat_amount,
            'pct': round((cat_amount / total_expenses * 100), 1) if total_expenses > 0 else 0,
        })

    # ── Task Categories ──
    task_categories_data = list(all_tasks.values('task_category__name').annotate(
        count=Count('id')
    ).order_by('-count')[:10])
    task_categories = []
    for cat in task_categories_data:
        cat_count = cat['count']
        task_categories.append({
            'name': cat['task_category__name'] or 'Uncategorized',
            'count': cat_count,
            'pct': round((cat_count / total_tasks * 100), 1) if total_tasks > 0 else 0,
        })

    # ── Revenue by Crop ──
    revenue_by_crop = list(sales_qs.values('category').annotate(
        total=Sum('amount')
    ).order_by('-total')[:6])
    revenue_by_crop_formatted = [
        {'label': r['category'] or 'Uncategorized', 'amount_fmt': f'₹{abs(float(r["total"])):,.0f}', 'pct': round((abs(float(r["total"])) / total_sales * 100), 1) if total_sales > 0 else 0}
        for r in revenue_by_crop
    ]

    # ── Expense Breakdown ──
    expense_breakdown = list(expenses_qs.values('category').annotate(
        total=Sum('amount')
    ).order_by('-total')[:6])
    expense_breakdown_formatted = [
        {'label': e['category'] or 'Uncategorized', 'amount_fmt': f'₹{abs(float(e["total"])):,.0f}', 'pct': round((abs(float(e["total"])) / total_expenses * 100), 1) if total_expenses > 0 else 0, 'color': '#EF4444'}
        for e in expense_breakdown
    ]

    return {
        'total_sales': total_sales,
        'total_expenses': total_expenses,
        'pending_tasks': pending_tasks,
        'completed_tasks': completed_tasks,
        'total_tasks': total_tasks,
        'in_progress_tasks': in_progress_tasks,
        'sales_categories': sales_categories,
        'expense_categories': expense_categories,
        'task_categories': task_categories,
        'revenue_by_crop': revenue_by_crop_formatted,
        'expense_breakdown': expense_breakdown_formatted,
    }


def _mobile_dashboard_operations(user, today, start_date=None, end_date=None):
    """Inventory & machinery, workforce snapshot, financials/payroll."""
    from apps.farmer.models import FarmerInventory, FarmerOutstanding, StaffPayroll
    from django.db.models import Sum

    staff_qs, attendances = _dash_staff_today(user, start_date, end_date)
    total_staff = staff_qs.count()
    active_staff = staff_qs.filter(status='0').count()
    suspended_staff = staff_qs.exclude(status='0').count()
    present_today = attendances.filter(status='P').count()
    absent_today = attendances.filter(status='A').count()
    leave_today = attendances.filter(status__in=['H', 'L']).count()
    attendance_pct = round((present_today / total_staff * 100), 1) if total_staff > 0 else 0
    leave_pct = round((leave_today / total_staff * 100), 1) if total_staff > 0 else 0
    absent_pct = round((absent_today / total_staff * 100), 1) if total_staff > 0 else 0

    all_tasks = _dash_tasks(user, start_date, end_date)
    in_progress_tasks = all_tasks.filter(status='In Progress').count()

    sales_qs, _, total_sales, total_expenses = _dash_sales_expenses(user, start_date, end_date)

    overdue_invoices = FarmerOutstanding.objects.filter(
        farmer=user, due_date__lt=today
    ).exclude(status='Paid').count()

    # ── Inventory ──
    inventory_qs = FarmerInventory.objects.filter(farmer=user)
    inventory_value = sum(float(i.quantity or 0) * float(i.unit_price or 0) for i in inventory_qs)
    total_stock_value = inventory_value
    low_stock_count = sum(1 for i in inventory_qs if (i.quantity or 0) <= (i.safety_stock or 0))

    # ── Fleet ──
    fleet_qs = inventory_qs.filter(
        Q(category__icontains='Vehicle') | Q(category__icontains='Fleet') |
        Q(category__icontains='Machin') | Q(category__icontains='Equipment') |
        Q(category__icontains='Tractor') | Q(category__icontains='Implement') |
        Q(license_plate__isnull=False)
    )
    fleet_total = fleet_qs.count()
    fleet_operational = fleet_qs.filter(status__in=['In Stock', 'Operational', 'Good']).count()
    fleet_maintenance = max(0, fleet_total - fleet_operational)
    tools_count = inventory_qs.filter(Q(category__icontains='Tool') | Q(category__icontains='Implement')).count()

    # ── Payroll ──
    payroll_qs = StaffPayroll.objects.filter(staff__farmer=user)
    monthly_payroll_total = float(
        payroll_qs.aggregate(s=Sum('net_pay'))['s'] or 0
    )
    if monthly_payroll_total == 0:
        monthly_payroll_total = sum(
            float(s.salary or (float(s.base_daily_rate or 500) * 26)) for s in staff_qs
        ) if staff_qs.exists() else 0.0
    payroll_paid = float(payroll_qs.aggregate(s=Sum('net_pay'))['s'] or 0)
    payroll_pending = max(0.0, monthly_payroll_total - payroll_paid)

    # ── Recent Orders ──
    recent_sales = sales_qs.order_by('-close_date', '-id')[:5]
    recent_orders = []
    for tx in recent_sales:
        amt = float(abs(float(tx.amount or 0)))
        is_ok = str(tx.status or '') in ['Received', 'Finished']
        buyer_name = tx.vendor.business_name if (tx.vendor and tx.vendor.business_name) else (tx.tags or 'Local Wholesale Market')
        recent_orders.append({
            'title': tx.description or f'{tx.category} Sale',
            'buyer': buyer_name,
            'amount_fmt': f'₹{amt:,.0f}',
            'status': 'Delivered' if is_ok else 'Pending',
            'badge_class': 'ok' if is_ok else 'low',
        })

    # ── Inventory Items ──
    inv_items = []
    for item in inventory_qs[:6]:
        qty = item.quantity or 0
        safety = item.safety_stock or 5
        unit_p = float(item.unit_price or 0)
        inv_items.append({
            'name': item.item_name,
            'category': item.category or '',
            'quantity': qty,
            'unit': item.unit or 'Units',
            'total_value': qty * unit_p,
            'is_low': qty <= safety,
            'stock_pct': min(100, max(0, int((qty / max(1, safety * 2)) * 100))) if safety else 100,
        })

    # ── Fleet Items ──
    fl_items = []
    for item in fleet_qs[:4]:
        is_op = str(item.status or '').lower() in ['in stock', 'operational', 'good']
        is_low = (item.quantity or 0) <= (item.safety_stock or 0)
        status_label = 'Operational' if is_op else ('Low Stock' if is_low else 'Maintenance')
        fl_items.append({
            'name': item.item_name,
            'category': item.category or '',
            'plate': item.license_plate or 'N/A plate',
            'location': item.location or 'Main Garage',
            'qty': item.quantity or 1,
            'status_label': status_label,
            'badge_class': 'ok' if is_op else 'low',
            'last_inspected': item.last_service_date.strftime('%b %d, %Y') if item.last_service_date else 'Recently',
        })

    # ── Payroll by Role ──
    payroll_roles = []
    role_groups = {}
    for s in staff_qs:
        r_name = s.role or (s.designation.name if s.designation else 'Farm Workers')
        if r_name not in role_groups:
            role_groups[r_name] = {'count': 0, 'present': 0, 'total_amount': 0.0, 'rates': []}
        role_groups[r_name]['count'] += 1
        rate = float(s.base_daily_rate or 500)
        role_groups[r_name]['rates'].append(rate)
        role_groups[r_name]['total_amount'] += float(s.salary or (rate * 26))
    for r_name, r_data in role_groups.items():
        avg_rate = round(sum(r_data['rates']) / len(r_data['rates'])) if r_data['rates'] else 500
        role_pres = [
            a for a in attendances
            if a.status == 'P'
            and (a.staff.role
                 or (a.staff.designation.name if a.staff.designation else 'Farm Workers')) == r_name
        ]
        pres_count = len(role_pres)
        payroll_roles.append({
            'role_name': r_name,
            'worker_count': r_data['count'],
            'present_count': pres_count,
            'total_fmt': f"₹{r_data['total_amount']:,.0f}",
            'daily_rate': f'₹{avg_rate}/day',
        })

    return {
        'in_progress_tasks': in_progress_tasks,
        'attendance_pct': attendance_pct,
        'total_sales': total_sales,
        'total_expenses': total_expenses,
        'total_stock_value': total_stock_value,
        'low_stock_count': low_stock_count,
        'fleet_operational_count': fleet_operational,
        'fleet_total_count': fleet_total,
        'fleet_maintenance_count': fleet_maintenance,
        'tools_count': tools_count,
        'active_staff': active_staff,
        'suspended_staff': suspended_staff,
        'absent_today': absent_today,
        'overdue_invoices_count': overdue_invoices,
        'monthly_payroll_total': monthly_payroll_total,
        'payroll_paid': payroll_paid,
        'payroll_pending': payroll_pending,
        'leave_today': leave_today,
        'leave_pct': leave_pct,
        'absent_pct': absent_pct,
        'inventory_items': inv_items,
        'fleet_items': fl_items,
        'recent_orders': recent_orders,
        'payroll_by_role': payroll_roles,
    }


def _mobile_dashboard_market(user, today, start_date=None, end_date=None):
    """Nearby mandi prices + crop/market picker lists."""
    from apps.superadmin.models import MarketPriceIndex as SPMarketPriceIndex, DepotTerminal

    mandi_prices = list(
        SPMarketPriceIndex.objects.all()[:10]
        .values('crop_name', 'base_price', 'unit')
    )
    mandi_formatted = [
        {'name': m['crop_name'], 'crop': m['crop_name'] or '', 'amount': float(m['base_price']), 'unit': m['unit'] or '₹/Ton'}
        for m in mandi_prices
    ]

    mandi_crops_list = list(SPMarketPriceIndex.objects.values_list('crop_name', flat=True).distinct().order_by('crop_name'))
    mandi_markets_list = list(DepotTerminal.objects.values_list('name', flat=True).distinct().order_by('name'))

    crop_base_rates = {}
    for mpi in SPMarketPriceIndex.objects.all():
        crop_base_rates[mpi.crop_name] = {'rate': float(mpi.base_price), 'unit': mpi.unit}

    return {
        'mandi_prices': mandi_formatted,
        'mandi_crops_list': mandi_crops_list,
        'mandi_markets_list': mandi_markets_list,
        'crop_base_rates': crop_base_rates,
    }


class MobileDashboardAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Full Mobile Dashboard (legacy, composes all sections)',
        operation_description='Returns aggregated dashboard data matching website dashboard: KPIs, farm stats, task stats, sales/expenses, inventory, workforce, payroll, recent orders, mandi prices, analytics, subscription, weather. Prefer the per-section endpoints (/dashboard/overview/, /dashboard/analytics/, /dashboard/operations/, /dashboard/market/) for faster progressive loading.',
        tags=['Mobile - 00. Dashboard'],
        responses={200: openapi.Response('Dashboard data')}
    )
    def get(self, request):
        user = get_farmer_profile(request.user)
        today, start_date, end_date = _dashboard_range(request)
        data = {}
        for section in (
            _mobile_dashboard_overview,
            _mobile_dashboard_analytics,
            _mobile_dashboard_operations,
            _mobile_dashboard_market,
        ):
            data.update(section(user, today, start_date, end_date))
        return Response(data)


class MobileDashboardOverviewAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Dashboard Overview Section (hero, KPI cards, farm status, weather, subscription)',
        tags=['Mobile - 00. Dashboard'],
        responses={200: openapi.Response('Overview data')}
    )
    def get(self, request):
        today, start_date, end_date = _dashboard_range(request)
        return Response(_mobile_dashboard_overview(
            get_farmer_profile(request.user), today, start_date, end_date,
        ))


class MobileDashboardAnalyticsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Dashboard Analytics Section (sales / expense / task charts)',
        tags=['Mobile - 00. Dashboard'],
        responses={200: openapi.Response('Analytics data')}
    )
    def get(self, request):
        today, start_date, end_date = _dashboard_range(request)
        return Response(_mobile_dashboard_analytics(
            get_farmer_profile(request.user), today, start_date, end_date,
        ))


class MobileDashboardOperationsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Dashboard Operations Section (inventory, workforce, financials)',
        tags=['Mobile - 00. Dashboard'],
        responses={200: openapi.Response('Operations data')}
    )
    def get(self, request):
        today, start_date, end_date = _dashboard_range(request)
        return Response(_mobile_dashboard_operations(
            get_farmer_profile(request.user), today, start_date, end_date,
        ))


class MobileDashboardMarketAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Dashboard Market Section (mandi prices)',
        tags=['Mobile - 00. Dashboard'],
        responses={200: openapi.Response('Market data')}
    )
    def get(self, request):
        today, start_date, end_date = _dashboard_range(request)
        return Response(_mobile_dashboard_market(
            get_farmer_profile(request.user), today, start_date, end_date,
        ))


class HarvestedCropsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Harvested Crops for Sale Items Dropdown', tags=['Mobile - 09. Sales Management'])
    def get(self, request):
        from django.db.models import Q
        from apps.farmer.models import CropSplit

        profile = get_farmer_profile(request.user)
        farm_id = request.query_params.get('farm_id')
        split_id = request.query_params.get('split_id')
        requested_grade = (request.query_params.get('grade') or '').strip()

        if split_id:
            try:
                split = CropSplit.objects.select_related(
                    'crop', 'farm'
                ).get(id=split_id, farm__farmer=profile)
            except (CropSplit.DoesNotExist, ValueError, TypeError):
                return Response(
                    {'error': 'Harvested crop split not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if (split.stage or '').lower() in ['completed', 'inactive']:
                return Response(
                    {'error': 'This crop split is not available for sale.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            row, available, _, _, _ = _harvest_stock_row(split)
            if available <= 0.001:
                return Response(
                    {'error': 'This crop has no available harvest stock.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            grade_data = None
            if requested_grade:
                grade_data = next(
                    (
                        grade for grade in row.get('grade_splits', [])
                        if grade['grade'].strip().casefold()
                        == requested_grade.casefold()
                    ),
                    None,
                )
                if grade_data is None:
                    return Response(
                        {'error': f'{requested_grade} has no available stock for this crop.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            return Response([{
                'split_id': split.id,
                'crop__id': split.crop_id,
                'crop__name': row['crop_name'],
                'farm__id': split.farm_id,
                'farm__land_name': row['farm_name'],
                'available': grade_data['yield'] if grade_data else available,
                'price': grade_data['price'] if grade_data else row['price_per_mt'],
                'grade': grade_data['grade'] if grade_data else None,
                'unit': grade_data['unit'] if grade_data else row['unit'],
            }], status=status.HTTP_200_OK)

        harvest_splits = CropSplit.objects.filter(
            farm__farmer=profile,
            stage__in=['Harvested', 'Yield', 'Partially Sold', 'Harvesting'],
        )
        if farm_id:
            harvest_splits = harvest_splits.filter(farm_id=farm_id)

        extra_splits = CropSplit.objects.filter(
            farm__farmer=profile,
        ).exclude(
            id__in=harvest_splits.values_list('id', flat=True),
        ).filter(
            Q(yield_tons__gt=0) | ~Q(harvest_batches=[]),
        )
        if farm_id:
            extra_splits = extra_splits.filter(farm_id=farm_id)

        splits = list(
            harvest_splits.select_related('crop', 'farm').order_by(
                '-updated_at', '-id'
            )
        ) + list(
            extra_splits.select_related('crop', 'farm').order_by(
                '-updated_at', '-id'
            )
        )

        farmer_crops = []
        seen_keys = set()
        for split in splits:
            crop_name = (
                split.crop.name if split.crop
                else split.crop_category or split.crop_variety or 'Produce'
            )
            farm_name = split.farm.land_name if split.farm else 'General Farm'
            unit = split.expected_yield_unit or 'Tons'
            grades = []

            if split.harvest_batches:
                grade_totals = {}
                for batch in split.harvest_batches:
                    quantity = float(batch.get('yield', 0) or 0)
                    if (
                        batch.get('status') in ['Sold', 'Loss / Discard']
                        or quantity <= 0.0001
                    ):
                        continue
                    grade_name = (batch.get('quality') or 'Grade 1').strip()
                    batch_price = float(batch.get('price', 0) or 0)
                    batch_value = float(
                        batch.get('saleValue') or quantity * batch_price
                    )
                    grade = grade_totals.setdefault(grade_name, {
                        'grade': grade_name,
                        'yield': 0.0,
                        'amount': 0.0,
                        'unit': batch.get('unit') or unit,
                        'price': batch_price,
                    })
                    grade['yield'] += quantity
                    grade['amount'] += batch_value
                    grade['price'] = batch_price
                grades = list(grade_totals.values())
            else:
                available = float(split.yield_tons or 0)
                if available > 0:
                    price = float(split.price_per_mt or 0)
                    grades = [{
                        'grade': '',
                        'yield': available,
                        'price': price,
                        'amount': available * price,
                        'unit': unit,
                    }]

            for grade in grades:
                quantity = float(grade.get('yield') or 0)
                if quantity <= 0:
                    continue
                grade_name = (grade.get('grade') or '').strip()
                key = (crop_name, grade_name, split.farm_id)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                amount = float(
                    grade.get('amount')
                    or (quantity * float(grade.get('price') or 0))
                )
                farmer_crops.append({
                    'crop__id': split.crop_id,
                    'crop__name': crop_name,
                    'farm__id': split.farm_id,
                    'farm__land_name': farm_name,
                    'grade': grade_name or None,
                    'available': round(quantity, 2),
                    'price': round(amount / quantity, 2) if quantity else 0,
                    'unit': grade.get('unit') or unit,
                })

        if requested_grade:
            farmer_crops = [
                crop for crop in farmer_crops
                if (crop['grade'] or '').casefold() == requested_grade.casefold()
            ]

        return Response(farmer_crops, status=status.HTTP_200_OK)


class CustomerTypeListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Active Customer Types', tags=['Mobile - 09. Sales Management'])
    def get(self, request):
        from apps.superadmin.models import CustomerType
        types = CustomerType.objects.filter(is_active=True).order_by('name').values('id', 'name', 'description')
        return Response(list(types), status=status.HTTP_200_OK)

    @swagger_auto_schema(operation_summary='Quick Add Customer Type', tags=['Mobile - 09. Sales Management'],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['name'],
            properties={
                'name': openapi.Schema(type=openapi.TYPE_STRING),
                'description': openapi.Schema(type=openapi.TYPE_STRING),
            },
        ))
    def post(self, request):
        from apps.superadmin.models import CustomerType
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'error': 'Name is required'}, status=status.HTTP_400_BAD_REQUEST)
        ct, created = CustomerType.objects.get_or_create(
            name__iexact=name,
            defaults={'name': name, 'description': request.data.get('description', ''), 'is_active': True},
        )
        return Response({'id': ct.id, 'name': ct.name, 'description': ct.description},
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class SaleCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Create Sale with GST/Discount/Commission/Expenses',
        tags=['Mobile - 09. Sales Management'],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['description', 'close_date'],
            properties={
                'description': openapi.Schema(type=openapi.TYPE_STRING, description='Buyer/Customer name'),
                'farm_id': openapi.Schema(type=openapi.TYPE_INTEGER, description='Farm ID (optional)'),
                'crop_split_id': openapi.Schema(type=openapi.TYPE_INTEGER, description='Specific harvested crop split to sell'),
                'grade': openapi.Schema(type=openapi.TYPE_STRING, description='Harvest quality grade to deduct from the selected crop split'),
                'category': openapi.Schema(type=openapi.TYPE_STRING, description='Item/crop name or __other__'),
                'custom_item_name': openapi.Schema(type=openapi.TYPE_STRING, description='Custom item name when Other selected'),
                'load_weight': openapi.Schema(type=openapi.TYPE_STRING, description='Quantity'),
                'unit': openapi.Schema(type=openapi.TYPE_STRING, description='Unit of measurement'),
                'amount': openapi.Schema(type=openapi.TYPE_NUMBER, description='Subtotal valuation amount'),
                'gst_rate': openapi.Schema(type=openapi.TYPE_STRING, description='GST rate: 0,5,12,18,28 or custom'),
                'custom_gst_rate': openapi.Schema(type=openapi.TYPE_NUMBER, description='Custom GST percentage'),
                'discount': openapi.Schema(type=openapi.TYPE_NUMBER, description='Discount value'),
                'discount_type': openapi.Schema(type=openapi.TYPE_STRING, description='amount or percent'),
                'commission': openapi.Schema(type=openapi.TYPE_NUMBER, description='Commission value'),
                'commission_type': openapi.Schema(type=openapi.TYPE_STRING, description='amount or percent'),
                'payment_method': openapi.Schema(type=openapi.TYPE_STRING, description='Payment method'),
                'close_date': openapi.Schema(type=openapi.TYPE_STRING, description='Deal date YYYY-MM-DD'),
                'paid_amount': openapi.Schema(type=openapi.TYPE_NUMBER, description='Amount paid upfront'),
                'due_date': openapi.Schema(type=openapi.TYPE_STRING, description='Payment due date'),
                'has_sale_expenses': openapi.Schema(type=openapi.TYPE_BOOLEAN),
                'sale_expenses': openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'category': openapi.Schema(type=openapi.TYPE_STRING),
                        'vendor_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'note': openapi.Schema(type=openapi.TYPE_STRING),
                        'amount': openapi.Schema(type=openapi.TYPE_NUMBER),
                    }
                )),
            }
        )
    )
    def post(self, request):
        import datetime
        from decimal import Decimal
        from django.db import transaction
        from django.db.models import Sum
        from apps.farmer.models import CropSplit, FarmerCustomer, LedgerTransaction, Farm, FarmerVendor, FarmerOutstanding

        profile = get_farmer_profile(request.user)

        try:
            buyer_name = (request.data.get('description') or '').strip()
            category_raw = (request.data.get('category') or '').strip()
            custom_item_name = (request.data.get('custom_item_name') or '').strip()

            if (category_raw == '__other__' or category_raw.lower() == 'other') and custom_item_name:
                category = custom_item_name
            elif category_raw and category_raw != '__other__':
                category = category_raw
            else:
                category = 'Harvest Trade'

            load_weight = (request.data.get('load_weight') or '0').strip()
            amount_raw = request.data.get('amount', 0)
            amount = abs(float(amount_raw)) if amount_raw else 0.0

            gst_rate_raw = str(request.data.get('gst_rate', '0'))
            custom_gst_rate_raw = str(request.data.get('custom_gst_rate', '0'))
            discount_raw = str(request.data.get('discount', '0'))
            discount_type = str(request.data.get('discount_type', 'amount'))
            commission_raw = str(request.data.get('commission', '0'))
            commission_type = str(request.data.get('commission_type', 'amount'))

            try:
                discount_val = float(discount_raw) if discount_raw else 0.0
            except (ValueError, TypeError):
                discount_val = 0.0

            try:
                commission_val = float(commission_raw) if commission_raw else 0.0
            except (ValueError, TypeError):
                commission_val = 0.0

            if discount_type == 'percent':
                discount_amount = (amount * discount_val / 100.0)
            else:
                discount_amount = discount_val

            if commission_type == 'percent':
                commission_amount = (amount * commission_val / 100.0)
            else:
                commission_amount = commission_val

            gst_pct = 0.0
            if gst_rate_raw == 'custom':
                try:
                    gst_pct = float(custom_gst_rate_raw) if custom_gst_rate_raw else 0.0
                except (ValueError, TypeError):
                    gst_pct = 0.0
            else:
                try:
                    gst_pct = float(gst_rate_raw) if gst_rate_raw else 0.0
                except (ValueError, TypeError):
                    gst_pct = 0.0

            gst_amount = (amount * gst_pct / 100.0)
            net_amount = amount + gst_amount - discount_amount - commission_amount

            paid_amount_raw = request.data.get('paid_amount')
            try:
                paid_amount = float(paid_amount_raw) if paid_amount_raw is not None and paid_amount_raw != '' else net_amount
            except (ValueError, TypeError):
                paid_amount = net_amount

            paid_amount = min(net_amount, max(0.0, paid_amount))
            balance_amount = max(0.0, net_amount - paid_amount)

            farm_id = request.data.get('farm_id') or None
            payment_method = str(request.data.get('payment_method') or 'Bank Transfer').strip()
            crop_split_id_raw = request.data.get('crop_split_id')
            crop_split_id = None
            if crop_split_id_raw not in (None, ''):
                try:
                    crop_split_id = int(crop_split_id_raw)
                except (TypeError, ValueError):
                    raise ValueError('crop_split_id must be a valid crop split ID.')
            requested_grade = str(request.data.get('grade') or '').strip()

            try:
                close_date = datetime.datetime.strptime(str(request.data.get('close_date', '')), '%Y-%m-%d').date()
            except (ValueError, TypeError):
                close_date = datetime.date.today()

            due_date_raw = request.data.get('due_date')
            try:
                due_date = datetime.datetime.strptime(str(due_date_raw), '%Y-%m-%d').date() if due_date_raw else (close_date + datetime.timedelta(days=15))
            except (ValueError, TypeError):
                due_date = close_date + datetime.timedelta(days=15)

            if balance_amount <= 0.001:
                tx_status = 'Received'
                outstanding_status = 'Paid'
            elif paid_amount > 0:
                tx_status = 'Partial'
                outstanding_status = 'Partial'
            else:
                tx_status = 'Pending'
                outstanding_status = 'Unpaid'

            farm_instance = Farm.objects.filter(farmer=profile, id=farm_id).first() if farm_id else None

            qty = None
            try:
                qty = float(load_weight) if load_weight and float(load_weight) > 0 else None
            except (ValueError, TypeError):
                pass

            unit = str(request.data.get('unit') or 'Tons').strip()

            tags_parts = ['Inflow']
            tags_parts.append(f"Subtotal: ₹{amount:.2f}")
            if gst_pct > 0:
                tags_parts.append(f"GST: {gst_pct}% (₹{gst_amount:.2f})")
            if discount_amount > 0:
                disc_tag = f"Discount: {discount_val}%" if discount_type == 'percent' else f"Discount: ₹{discount_amount:.2f}"
                tags_parts.append(disc_tag)
            if commission_amount > 0:
                comm_tag = f"Commission: {commission_val}%" if commission_type == 'percent' else f"Comm: ₹{commission_amount:.2f}"
                tags_parts.append(comm_tag)
            tags_parts.append(f"Paid: ₹{paid_amount:.2f}")
            tags_parts.append(f"Balance: ₹{balance_amount:.2f}")
            tags_parts.append(f"Due: {due_date.strftime('%Y-%m-%d')}")
            if crop_split_id is not None:
                tags_parts.append(f"CropSplit #{crop_split_id}")
            tags_str = ", ".join(tags_parts)

            with transaction.atomic():
                if crop_split_id is not None:
                    if qty is None or qty <= 0:
                        raise ValueError(
                            'A positive sale quantity is required for harvested stock.'
                        )
                    split = CropSplit.objects.select_for_update().filter(
                        id=crop_split_id,
                        farm__farmer=profile,
                    ).first()
                    if split is None:
                        raise ValueError('Harvested crop split not found.')
                    if (split.stage or '').lower() in ['completed', 'inactive']:
                        raise ValueError('This crop split is not available for sale.')
                    if farm_id and str(split.farm_id) != str(farm_id):
                        raise ValueError(
                            'The selected crop does not belong to the selected farm.'
                        )
                    farm_instance = split.farm

                    _, available_qty, _, _, _ = _harvest_stock_row(split)
                    if qty > available_qty + 0.001:
                        raise ValueError(
                            f'Sale quantity exceeds available stock ({available_qty:g} {unit}).'
                        )

                    sale_quantity = Decimal(str(qty))
                    remaining_quantity = sale_quantity
                    batches = list(split.harvest_batches or [])
                    if requested_grade:
                        if batches:
                            matching_batches = [
                                batch for batch in batches
                                if batch.get('status') not in ['Sold', 'Loss / Discard']
                                and float(batch.get('yield') or 0) > 0
                                and (batch.get('quality') or 'Grade 1').strip().casefold()
                                == requested_grade.casefold()
                            ]
                            grade_available = sum(
                                float(batch.get('yield') or 0)
                                for batch in matching_batches
                            )
                        else:
                            grade_available = (
                                available_qty
                                if requested_grade.casefold() == 'grade 1'
                                else 0
                            )
                        if grade_available <= 0.001:
                            raise ValueError(
                                f'{requested_grade} has no available stock for this crop.'
                            )
                        if qty > grade_available + 0.001:
                            raise ValueError(
                                f'Sale quantity exceeds available {requested_grade} stock '
                                f'({grade_available:g} {unit}).'
                            )
                    if batches:
                        updated_batches = []
                        unit_sale_price = Decimal(str(amount)) / sale_quantity
                        for batch in batches:
                            batch_status = batch.get('status')
                            batch_quantity = Decimal(str(batch.get('yield', 0) or 0))
                            if (
                                remaining_quantity <= 0
                                or batch_status in ['Sold', 'Loss / Discard']
                                or batch_quantity <= 0
                                or (
                                    requested_grade
                                    and (batch.get('quality') or 'Grade 1').strip().casefold()
                                    != requested_grade.casefold()
                                )
                            ):
                                updated_batches.append(batch)
                                continue

                            sold_quantity = min(batch_quantity, remaining_quantity)
                            sold_batch = dict(batch)
                            original_value = float(
                                batch.get('saleValue')
                                or (
                                    float(batch_quantity)
                                    * float(batch.get('price') or 0)
                                )
                            )
                            sold_value = round(
                                original_value
                                * float(sold_quantity)
                                / float(batch_quantity),
                                2,
                            )
                            sold_batch.update({
                                'yield': float(sold_quantity),
                                'status': 'Sold',
                                'sold_to': buyer_name or 'Direct Customer',
                                'sold_at_price': float(unit_sale_price),
                                'sold_total': float(unit_sale_price * sold_quantity),
                                'saleValue': sold_value,
                                'payment_method': payment_method,
                                'payment_status': tx_status,
                                'sold_date': close_date.strftime('%d %b %Y'),
                            })
                            remaining_batch_quantity = batch_quantity - sold_quantity
                            if remaining_batch_quantity > 0:
                                ready_batch = dict(batch)
                                ready_batch['yield'] = float(remaining_batch_quantity)
                                ready_batch['saleValue'] = round(
                                    original_value - sold_value,
                                    2,
                                )
                                updated_batches.extend([sold_batch, ready_batch])
                            else:
                                updated_batches.append(sold_batch)
                            remaining_quantity -= sold_quantity

                        if remaining_quantity > Decimal('0.001'):
                            raise ValueError(
                                'Sale quantity exceeds available harvest batches.'
                            )
                        split.harvest_batches = updated_batches
                        available_after_sale = max(
                            0.0,
                            available_qty - qty,
                        )
                        remaining_value = sum(
                            float(batch.get('saleValue') or (
                                float(batch.get('yield') or 0)
                                * float(batch.get('price') or 0)
                            ))
                            for batch in updated_batches
                            if batch.get('status') not in ['Sold', 'Loss / Discard']
                        )
                        split.yield_tons = Decimal(str(available_after_sale))
                        split.total_sale_value = Decimal(str(round(remaining_value, 2)))
                        if available_after_sale > 0.001:
                            split.price_per_mt = Decimal(
                                str(round(remaining_value / available_after_sale, 2))
                            )
                        split.stage = (
                            'Sold'
                            if available_after_sale <= 0.001
                            else 'Partially Sold'
                        )
                    else:
                        split.yield_tons = max(
                            Decimal('0'),
                            Decimal(str(split.yield_tons or 0)) - sale_quantity,
                        )
                        if split.price_per_mt:
                            split.total_sale_value = (
                                split.yield_tons * split.price_per_mt
                            )
                        split.stage = (
                            'Sold'
                            if split.yield_tons <= Decimal('0.001')
                            else 'Partially Sold'
                        )
                    split.save()

                sale_trans = LedgerTransaction.objects.create(
                    farmer=profile,
                    close_date=close_date,
                    description=buyer_name,
                    payment_method=payment_method,
                    category=category,
                    category_color='#198754',
                    tags=tags_str,
                    status=tx_status,
                    amount=net_amount if net_amount > 0 else amount,
                    balance_due=balance_amount,
                    farm=farm_instance,
                    quantity=qty,
                    unit=unit,
                )

            if crop_split_id is None and qty and qty > 0 and category:
                try:
                    from apps.farmer.views import deduct_crop_inventory
                    deduct_crop_inventory(profile, category, qty, farm_instance)
                except Exception:
                    pass

            if balance_amount > 0:
                customer_inst = FarmerCustomer.objects.filter(farmer=profile, name=buyer_name).first()
                if not customer_inst and ' - ' in buyer_name:
                    # Mobile sends the buyer as "Name - Company" (add_sales_controller._getSelectedBuyerName)
                    name_part, company_part = buyer_name.split(' - ', 1)
                    customer_inst = FarmerCustomer.objects.filter(
                        farmer=profile, name=name_part.strip(), company_name=company_part.strip()
                    ).first() or FarmerCustomer.objects.filter(
                        farmer=profile, name=name_part.strip()
                    ).first()
                FarmerOutstanding.objects.create(
                    farmer=profile,
                    customer=customer_inst,
                    invoice_no=f"Sale #{sale_trans.id}: {buyer_name}",
                    due_date=due_date,
                    amount_total=net_amount,
                    amount_paid=paid_amount,
                    status=outstanding_status,
                )

            has_sale_expenses = request.data.get('has_sale_expenses', False)
            sale_expenses = request.data.get('sale_expenses', [])

            if has_sale_expenses and sale_expenses:
                for exp in sale_expenses:
                    try:
                        exp_amt = float(exp.get('amount', 0))
                    except (ValueError, TypeError):
                        exp_amt = 0.0
                    if exp_amt > 0:
                        ven_id = exp.get('vendor_id')
                        vendor_inst = FarmerVendor.objects.filter(farmer=profile, id=ven_id).first() if ven_id else None
                        exp_cat = exp.get('category', 'Transport & Logistics')
                        exp_note = exp.get('note', '')
                        exp_desc = f"Sale Expense ({buyer_name}) - {exp_note.strip() if exp_note and exp_note.strip() else (exp_cat or 'Transport/Labor')}"
                        LedgerTransaction.objects.create(
                            farmer=profile,
                            close_date=close_date,
                            description=exp_desc,
                            payment_method=payment_method,
                            category=exp_cat or 'Transport & Logistics',
                            category_color='#dc3545',
                            tags=f"Outflow | Sale Expense | Linked Sale #{sale_trans.id}",
                            status='Cost',
                            amount=-abs(exp_amt),
                            farm=farm_instance,
                            vendor=vendor_inst,
                        )

            return Response({
                'success': True,
                'sale_id': sale_trans.id,
                'net_amount': net_amount,
                'balance_amount': balance_amount,
                'paid_amount': paid_amount,
                'gst_amount': gst_amount,
                'discount_amount': discount_amount,
                'commission_amount': commission_amount,
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class TaskCategoryListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Task Categories', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import TaskCategory
        return Response(list(TaskCategory.objects.filter(is_active=True).values('id', 'name', 'is_farm_required', 'is_crop_required')), status=status.HTTP_200_OK)


class TaskPriorityListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Get Task Priorities', responses={200: openapi.Response('Success')}, tags=['Mobile - 13. System Dropdowns'])
    def get(self, request):
        from apps.superadmin.models import TaskPriority
        return Response(list(TaskPriority.objects.filter(is_active=True).values('id', 'name')), status=status.HTTP_200_OK)


class QuickAddTaskCategoryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Task Category',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'name': openapi.Schema(type=openapi.TYPE_STRING, description='Category name'),
                'description': openapi.Schema(type=openapi.TYPE_STRING, description='Description'),
                'is_farm_required': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Farm selection mandatory'),
                'is_crop_required': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Crop split selection mandatory'),
            },
            required=['name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import TaskCategory
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Category name is required.'}, status=status.HTTP_400_BAD_REQUEST)
        description = request.data.get('description', '').strip()
        is_farm_required = request.data.get('is_farm_required', False)
        is_crop_required = request.data.get('is_crop_required', False)
        obj, created = TaskCategory.objects.get_or_create(
            name=name, defaults={
                'description': description,
                'is_farm_required': is_farm_required,
                'is_crop_required': is_crop_required,
            }
        )
        return Response({'success': True, 'task_category_id': obj.id, 'name': obj.name}, status=status.HTTP_200_OK)


class QuickAddTaskPriorityAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Task Priority',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'name': openapi.Schema(type=openapi.TYPE_STRING, description='Priority name'),
                'description': openapi.Schema(type=openapi.TYPE_STRING, description='Description'),
            },
            required=['name'],
        ),
        responses={200: openapi.Response('Success'), 400: openapi.Response('Invalid input')},
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import TaskPriority
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Priority name is required.'}, status=status.HTTP_400_BAD_REQUEST)
        description = request.data.get('description', '').strip()
        obj, created = TaskPriority.objects.get_or_create(
            name=name, defaults={'description': description}
        )
        return Response({'success': True, 'task_priority_id': obj.id, 'name': obj.name}, status=status.HTTP_200_OK)


class QuickAddExpenseTypeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Expense Type',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'name': openapi.Schema(type=openapi.TYPE_STRING, description='Expense type name'),
                'is_vendor_based': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Is vendor based'),
            },
            required=['name'],
        ),
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import ExpenseType
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Expense type name is required.'}, status=status.HTTP_400_BAD_REQUEST)
        is_vendor_based = request.data.get('is_vendor_based', False)
        obj, created = ExpenseType.objects.get_or_create(
            name=name, defaults={'is_vendor_based': is_vendor_based, 'is_active': True}
        )
        return Response({'success': True, 'id': obj.id, 'name': obj.name, 'is_vendor_based': obj.is_vendor_based}, status=status.HTTP_200_OK)


class QuickAddExpenseUnitAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Expense Unit',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'name': openapi.Schema(type=openapi.TYPE_STRING, description='Unit name'),
            },
            required=['name'],
        ),
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        from apps.superadmin.models import ExpenseUnit
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'success': False, 'error': 'Unit name is required.'}, status=status.HTTP_400_BAD_REQUEST)
        slug = name.lower().replace(' ', '_')
        obj, created = ExpenseUnit.objects.get_or_create(
            name=name, defaults={'slug': slug, 'is_active': True}
        )
        return Response({'success': True, 'id': obj.id, 'name': obj.name}, status=status.HTTP_200_OK)


class QuickAddExpenseVendorAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Quick Add Expense Vendor',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'vendor_name': openapi.Schema(type=openapi.TYPE_STRING, description='Vendor name'),
                'business_name': openapi.Schema(type=openapi.TYPE_STRING, description='Business name'),
                'mobile_number': openapi.Schema(type=openapi.TYPE_STRING, description='Mobile number'),
            },
            required=['vendor_name'],
        ),
        tags=['Mobile - 13. System Dropdowns'],
    )
    def post(self, request):
        vendor_name = request.data.get('vendor_name', '').strip()
        if not vendor_name:
            return Response({'success': False, 'error': 'Vendor name is required.'}, status=status.HTTP_400_BAD_REQUEST)
        business_name = request.data.get('business_name', '').strip() or vendor_name
        mobile_number = request.data.get('mobile_number', '').strip()
        vendor, created = FarmerVendor.objects.get_or_create(
            farmer=get_farmer_profile(request.user),
            vendor_name=vendor_name,
            defaults={'business_name': business_name, 'mobile_number': mobile_number}
        )
        return Response({'success': True, 'id': vendor.id, 'vendor_name': vendor.vendor_name}, status=status.HTTP_200_OK)


# ── My Documents (Farmer Document Management) ──
from apps.superadmin.models import Document, DocumentCategory
from apps.farmer.serializers import FarmerDocumentSerializer, DocumentCategorySerializer

@method_decorator(name='list', decorator=swagger_auto_schema(tags=['Mobile - 15. My Documents']))
@method_decorator(name='create', decorator=swagger_auto_schema(tags=['Mobile - 15. My Documents']))
@method_decorator(name='retrieve', decorator=swagger_auto_schema(tags=['Mobile - 15. My Documents']))
@method_decorator(name='update', decorator=swagger_auto_schema(tags=['Mobile - 15. My Documents']))
@method_decorator(name='partial_update', decorator=swagger_auto_schema(tags=['Mobile - 15. My Documents']))
@method_decorator(name='destroy', decorator=swagger_auto_schema(tags=['Mobile - 15. My Documents']))
class FarmerDocumentViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    pagination_class = StandardResultsSetPagination

    def get_serializer_class(self):
        if self.action == 'categories':
            return DocumentCategorySerializer
        return FarmerDocumentSerializer

    def get_queryset(self):
        return Document.objects.filter(
            farmer=get_farmer_profile(self.request.user)
        ).order_by('-created_at')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    def perform_create(self, serializer):
        doc_file = self.request.FILES.get('file')
        file_size = doc_file.size if doc_file else 0
        file_type = ''
        if doc_file:
            import os
            file_type = os.path.splitext(doc_file.name)[1].lower()
        serializer.save(
            uploaded_by=self.request.user,
            farmer=get_farmer_profile(self.request.user),
            file_size_bytes=file_size,
            file_type=file_type,
        )

    def perform_update(self, serializer):
        doc_file = self.request.FILES.get('file')
        if doc_file:
            import os
            file_size = doc_file.size
            file_type = os.path.splitext(doc_file.name)[1].lower()
            serializer.save(file_size_bytes=file_size, file_type=file_type)
        else:
            serializer.save()

    @swagger_auto_schema(
        operation_summary='Get Document Categories (Mobile)',
        operation_description='List all active document categories grouped by type.',
        tags=['Mobile - 15. My Documents'],
    )
    @action(detail=False, methods=['get'])
    def categories(self, request):
        cats = DocumentCategory.objects.filter(is_active=True).order_by('document_type', 'name')
        serializer = DocumentCategorySerializer(cats, many=True)
        return Response(serializer.data)

    @swagger_auto_schema(
        operation_summary='Get Document Summary (Mobile)',
        operation_description='Returns total documents count, total file size, and category breakdown.',
        tags=['Mobile - 15. My Documents'],
    )
    @action(detail=False, methods=['get'])
    def summary(self, request):
        docs = Document.objects.filter(farmer=get_farmer_profile(request.user))
        total_count = docs.count()
        total_size = sum(d.file_size_bytes or 0 for d in docs)
        categories = {}
        for d in docs:
            cat = d.category or 'General'
            if cat not in categories:
                categories[cat] = 0
            categories[cat] += 1
        return Response({
            'success': True,
            'total_documents': total_count,
            'total_size_bytes': total_size,
            'categories': categories,
        })


# ─────────────────────────────────────────────────────────────────────────────
# Mobile - 16. Notifications
# ─────────────────────────────────────────────────────────────────────────────

def _notification_queryset(user):
    """Notifications visible to this user.

    Mirrors config.context_processors.notifications_data: own notifications
    plus role broadcasts (every notification for a superadmin).
    """
    from auth.models import SystemNotification

    profile = getattr(user, 'profile', None)
    role = getattr(profile, 'role', None)

    if role == 'superadmin':
        query = Q()
    else:
        query = Q(user=user)
        if role:
            query |= Q(user__isnull=True, role_target=role)
    return SystemNotification.objects.filter(query).order_by('-created_at', '-id')


def _notification_type(icon_class):
    icon = icon_class or ''
    rules = (
        ('map-pin', 'Farm'),
        ('file', 'Document'),
        ('document', 'Document'),
        ('leaf', 'Crop'),
        ('plant', 'Crop'),
        ('seed', 'Crop'),
        ('wallet', 'Finance'),
        ('money', 'Finance'),
        ('shopping', 'Finance'),
        ('task', 'Task'),
        ('calendar', 'Task'),
        ('user', 'Staff'),
        ('team', 'Staff'),
        ('drop', 'Water'),
        ('storage', 'Inventory'),
        ('box', 'Inventory'),
        ('notification', 'Alert'),
    )
    for needle, label in rules:
        if needle in icon:
            return label
    return 'General'


def _notification_counts(queryset):
    total = queryset.count()
    unread = queryset.filter(is_read=False).count()
    return {
        'total_notifications': total,
        'read_notifications': total - unread,
        'unread_notifications': unread,
    }


notification_search_param = openapi.Parameter('search', openapi.IN_QUERY, description="Search notifications by title, message or link", type=openapi.TYPE_STRING)
notification_status_param = openapi.Parameter('status', openapi.IN_QUERY, description="Filter by read status: read or unread", type=openapi.TYPE_STRING)


class NotificationListView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='List notifications (Mobile)',
        manual_parameters=[notification_search_param, notification_status_param],
        tags=['Mobile - 16. Notifications'],
    )
    def get(self, request):
        base_queryset = _notification_queryset(request.user)
        queryset = base_queryset
        search = (request.query_params.get('search') or '').strip()
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search)
                | Q(message__icontains=search)
                | Q(link__icontains=search)
            )
        read_status = (request.query_params.get('status') or '').strip().lower()
        if read_status == 'unread':
            queryset = queryset.filter(is_read=False)
        elif read_status == 'read':
            queryset = queryset.filter(is_read=True)

        counts = _notification_counts(base_queryset)
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        notifications = [
            {
                'notification_id': item.id,
                'name': item.title,
                'type': _notification_type(item.icon_class),
                'message': item.message,
                'created_at': item.created_at.isoformat(),
                'is_read': item.is_read,
                'link': item.link or '',
            }
            for item in page
        ]
        data = dict(paginator.get_paginated_response(notifications).data)
        data['notifications'] = data.pop('results')
        data.update(counts)
        return Response(data)


class NotificationCountView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Notification counts (Mobile)', tags=['Mobile - 16. Notifications'])
    def get(self, request):
        return Response(_notification_counts(_notification_queryset(request.user)))


class NotificationReadAllView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Mark all notifications as read (Mobile)', tags=['Mobile - 16. Notifications'])
    def post(self, request):
        updated = _notification_queryset(request.user).filter(is_read=False).update(is_read=True)
        return Response({'status': 'success', 'updated': updated, 'unread_notifications': 0})


class NotificationReadView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Mark a notification as read (Mobile)', tags=['Mobile - 16. Notifications'])
    def post(self, request, pk):
        queryset = _notification_queryset(request.user)
        if not queryset.filter(pk=pk).exists():
            return Response({'status': 'error', 'message': 'Notification not found'}, status=status.HTTP_404_NOT_FOUND)
        updated = queryset.filter(pk=pk, is_read=False).update(is_read=True)
        return Response({'status': 'success', 'updated': updated, **_notification_counts(queryset)})


class NotificationClearAllView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary='Clear all notifications (Mobile)', tags=['Mobile - 16. Notifications'])
    def post(self, request):
        deleted, _ = _notification_queryset(request.user).delete()
        return Response({
            'status': 'success',
            'deleted': deleted,
            'total_notifications': 0,
            'read_notifications': 0,
            'unread_notifications': 0,
        })


# ── Mobile Map View ──

def _geo_marks_as_points(geo_marks):
    """Convert stored [[lat, lng], ...] geo marks to [{'lat': x, 'lng': y}, ...]."""
    points = []
    for mark in (geo_marks or []):
        try:
            points.append({'lat': float(mark[0]), 'lng': float(mark[1])})
        except (TypeError, ValueError, IndexError):
            continue
    return points


class LandAndCropDetailsAPIView(APIView):
    """Lands + crops list for the mobile map view dropdowns."""
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Land and Crop Details for Map (Mobile)',
        operation_description='Returns the farmer\'s lands with their crop splits (ids are CropSplit ids) for the map view selectors.',
        responses={200: openapi.Response('Lands with crops')},
        tags=['Mobile - 17. Map View']
    )
    def get(self, request):
        profile = get_farmer_profile(request.user)
        if profile is None:
            return Response({'lands': []}, status=status.HTTP_200_OK)

        farms = Farm.objects.filter(
            farmer=profile,
        ).prefetch_related('splits__crop').order_by('id')

        lands = []
        for farm in farms:
            crops = [
                {
                    'id': split.id,
                    'name': split.crop.name if split.crop else '',
                }
                for split in farm.splits.all()
            ]
            lands.append({
                'id': farm.id,
                'name': farm.land_name,
                'crops': crops,
            })
        return Response({'lands': lands}, status=status.HTTP_200_OK)


class LandGeoMarksAPIView(APIView):
    """Farm boundary + per-crop-split polygons for the mobile map view."""
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Land and Crop Geo Marks (Mobile)',
        operation_description='Returns farm boundary polygons and crop split polygons. Optional land_id query param filters to one farm.',
        manual_parameters=[
            openapi.Parameter('land_id', in_=openapi.IN_QUERY, description='Farm id (optional)', type=openapi.TYPE_INTEGER, required=False),
        ],
        responses={200: openapi.Response('Geo marks list')},
        tags=['Mobile - 17. Map View']
    )
    def get(self, request):
        profile = get_farmer_profile(request.user)
        if profile is None:
            return Response([], status=status.HTTP_200_OK)

        farms = Farm.objects.filter(
            farmer=profile,
        ).select_related('crop_type').prefetch_related('splits__crop__crop_type', 'splits__supervisor').order_by('id')

        land_id = request.query_params.get('land_id')
        if land_id:
            farms = farms.filter(id=land_id)

        data = []
        for farm in farms:
            crops = []
            manager = ''
            for split in farm.splits.all():
                if not manager and split.supervisor:
                    manager = split.supervisor.name
                crop = split.crop
                crop_image = ''
                if crop and crop.image:
                    crop_image = request.build_absolute_uri(crop.image.url)
                crops.append({
                    'crop_id': split.id,
                    'crop_type': crop.crop_type.name if crop and crop.crop_type else '',
                    'crop_name': crop.name if crop else '',
                    'geo_marks': _geo_marks_as_points(split.geo_marks),
                    'crop_image': crop_image,
                })
            data.append({
                'land_id': farm.id,
                'land_name': farm.land_name,
                'manager': manager,
                'solit_type': farm.soil_type or '',
                'farmer_id': farm.farmer_id,
                'geo_marks': _geo_marks_as_points(farm.geo_marks),
                'crops': crops,
            })
        return Response(data, status=status.HTTP_200_OK)


class CropSummaryMapViewAPIView(APIView):
    """Crop summary (details sheet) for a crop split on the mobile map view."""
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Crop Summary for Map (Mobile)',
        operation_description='Returns summary details for a crop split (crop_id is a CropSplit id).',
        responses={200: openapi.Response('Crop summary'), 404: openapi.Response('Not found')},
        tags=['Mobile - 17. Map View']
    )
    def get(self, request, land_id, crop_id):
        profile = get_farmer_profile(request.user)
        split = CropSplit.objects.filter(
            id=crop_id,
            farm_id=land_id,
            farm__farmer=profile,
        ).select_related('crop', 'crop__crop_type', 'farm').first()
        if split is None:
            return Response({'error': 'Crop not found'}, status=status.HTTP_404_NOT_FOUND)

        farm = split.farm
        crop = split.crop

        crop_image = ''
        if crop and crop.image:
            crop_image = request.build_absolute_uri(crop.image.url)

        activity_qs = FarmActivity.objects.filter(
            farm=farm,
        ).filter(
            Q(notes__icontains=f"CropSplit #{split.id}") |
            Q(notes__icontains=f"Crop Split ID: {split.id}") |
            Q(asset_name__icontains=f"CropSplit #{split.id}")
        )
        expense = sum(abs(float(a.amount or 0)) for a in activity_qs)

        status_map = {'Pending': 4, 'In Progress': 3, 'Completed': 2}
        tasks = FarmTask.objects.filter(
            associated_split=split,
        ).select_related('task_category').order_by('-task_date', '-id')

        task_list = []
        for task in tasks:
            task_list.append({
                'id': task.id,
                'crop_type': crop.name if crop else '',
                'crop_image': crop_image,
                'activity_type_name': task.task_category.name if task.task_category else (task.title or ''),
                'description': task.description or '',
                'status_id': status_map.get(task.status, 4),
                'created_at': task.task_date.strftime('%d-%m-%Y') if task.task_date else '',
            })

        from datetime import date as _date
        return Response({
            'id': split.id,
            'crop': crop.name if crop else '',
            'type': crop.crop_type.name if crop and crop.crop_type else '',
            'soil_type': farm.soil_type or '',
            'plantation_date': (split.start_date or _date.today()).isoformat(),
            'harvesting_type': split.harvest_type or 'single',
            'measurement_value': float(split.acreage or 0),
            'measurement_unit': 'Acres',
            'expense': expense,
            'sales': float(split.total_sale_value or 0),
            'task': task_list,
        }, status=status.HTTP_200_OK)


switch_profile_request_body = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    required=['profile_type'],
    properties={
        'profile_type': openapi.Schema(
            type=openapi.TYPE_STRING,
            description="Target profile type: 'farmer' or 'manager'.",
            enum=['farmer', 'manager'],
        ),
    },
)


class AvailableProfilesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Get Available Profiles for Switching",
        operation_description=(
            "Returns the farmer and/or manager profiles linked to the authenticated "
            "user's mobile number, plus the currently active profile type."
        ),
        responses={
            200: openapi.Response("Available profiles"),
            401: openapi.Response("Unauthorized"),
        },
        tags=['Mobile - 01. Authentication'],
    )
    def get(self, request):
        user = request.user
        active = ACTIVE_PROFILE_TYPE.get()
        profile = getattr(user, 'profile', None)

        def is_inactive(value):
            return str(value or '').strip().lower() in {
                '1', 'inactive', 'suspended', 'suspended locked', 'locked',
            }

        # Retrieve all active manager assignments
        staff_assignments = FarmerStaff.objects.filter(
            user=user
        ).exclude(
            status__in=['1', 'inactive', 'suspended', 'suspended locked', 'locked']
        ).select_related('farmer', 'user')

        if active not in ('farmer', 'manager'):
            active = 'manager' if staff_assignments.filter(
                farmer__isnull=False
            ).exists() else 'farmer'

        profiles = []

        # Farmer profile
        if (
            profile is not None
            and getattr(profile, 'role', None) == 'farmer'
            and not is_inactive(getattr(profile, 'status', '0'))
            and user.is_active
        ):
            profiles.append({
                'profile_type': 'farmer',
                'profile_id': profile.id,
                'name': (
                    user.get_full_name()
                    or user.first_name
                    or getattr(profile, 'company_name', None)
                    or str(user.username)
                ),
                'mobile': profile.mobile_number or str(user.username),
                'is_active': active == 'farmer',
                'is_account_active': True,
            })

        # Multiple manager profiles
        for staff in staff_assignments:
            if staff.farmer_id is None:
                continue

            profiles.append({
                'profile_type': 'manager',
                'profile_id': staff.farmer_id,
                'staff_id': staff.id,
                'name': staff.name or (
                    staff.user.first_name if staff.user
                    else str(staff.mobile_number)
                ),
                'role': staff.role or (
                    'Manager' if staff.is_role_manager else 'Staff'
                ),
                'mobile': staff.mobile_number,
                'is_active': active == 'manager',
                'is_account_active': True,
            })

        return Response({
            'success': True,
            'current_profile': active,
            'profiles': profiles,
        }, status=status.HTTP_200_OK)

class SwitchProfileAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [FormParser, JSONParser, MultiPartParser]

    @swagger_auto_schema(
        operation_summary="Switch Active Profile",
        operation_description=(
            "Switches the authenticated session between the farmer profile and the "
            "manager (FarmerStaff) profile linked to the same mobile number. "
            "Returns a fresh JWT access token scoped to the selected profile."
        ),
        request_body=switch_profile_request_body,
        responses={
            200: openapi.Response("Profile switched successfully", openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'success': openapi.Schema(type=openapi.TYPE_BOOLEAN),
                    'message': openapi.Schema(type=openapi.TYPE_STRING),
                    'token': openapi.Schema(type=openapi.TYPE_STRING),
                    'active_profile': openapi.Schema(type=openapi.TYPE_OBJECT),
                }
            )),
            400: openapi.Response("Invalid profile type, unauthorized, or suspended profile"),
            401: openapi.Response("Unauthenticated"),
        },
        tags=['Mobile - 01. Authentication'],
    )
    def post(self, request):
        from rest_framework_simplejwt.tokens import RefreshToken

        user = request.user
        profile_type = (request.data.get('profile_type') or '').strip().lower()
        if profile_type not in ('farmer', 'manager'):
            return Response(
                {'success': False, 'error': "profile_type must be 'farmer' or 'manager'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validation failures use 400 (not 403) so the mobile app retains the
        # current session — HttpService force-logs-out on 401/403 responses.
        if profile_type == 'manager':
            staff = _get_user_staff_assignment(user, manager_only=True)
            if staff is None or not staff.farmer_id:
                return Response(
                    {'success': False, 'error': 'Manager profile is not available for this account.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if str(getattr(staff, 'status', '0')) == "1":
                return Response(
                    {'success': False, 'error': 'Your account has been suspended/locked. Access is disabled.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            farmer_profile = staff.farmer
            if farmer_profile is None or str(getattr(farmer_profile, 'status', '0')) == "1":
                return Response(
                    {'success': False, 'error': 'The farm account linked to this manager is suspended.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            farmer_id = staff.farmer_id
            active_profile = {
                'profile_type': 'manager',
                'profile_id': staff.farmer_id,
                'staff_id': staff.id,
                'name': staff.name,
                'role': staff.role or ('Manager' if staff.is_role_manager else 'Staff'),
            }
        else:
            profile = getattr(user, 'profile', None)
            if profile is None or getattr(profile, 'role', None) != 'farmer':
                return Response(
                    {'success': False, 'error': 'Farmer profile is not available for this account.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if str(getattr(profile, 'status', '0')) == "1":
                return Response(
                    {'success': False, 'error': 'Your account has been suspended/locked. Access is disabled.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            farmer_id = profile.id
            active_profile = {
                'profile_type': 'farmer',
                'profile_id': profile.id,
                'name': (
                    user.get_full_name()
                    or user.first_name
                    or getattr(profile, 'company_name', None)
                    or str(user.username)
                ),
            }

        refresh = RefreshToken.for_user(user)
        refresh['farmer_id'] = farmer_id
        refresh['profile_type'] = profile_type

        return Response({
            'success': True,
            'message': 'Profile switched successfully',
            'token': str(refresh.access_token),
            'active_profile': active_profile,
        }, status=status.HTTP_200_OK)


def _harvest_stock_row(split):
    """
    Build the harvest stock row for a CropSplit.
    Mirrors website inventory() logic in apps/farmer/views.py:7183-7263,
    including the yield_tons auto-sync from harvest batches.
    Returns (row_dict, avail_qty, sold_qty, loss_qty, loss_val).
    """
    from decimal import Decimal

    c_name = split.crop.name if split.crop else (split.crop_category or split.crop_variety or 'Produce')
    c_variety = split.crop_variety or split.crop_pattern or ''
    f_name = split.farm.land_name if split.farm else 'General Farm'
    f_id = split.farm.id if split.farm else None
    p_mt = float(split.price_per_mt or 0)
    unit = split.expected_yield_unit or 'Tons'

    split_losses = list(split.losses.all())
    loss_qty = sum(float(l.loss_quantity) for l in split_losses)
    loss_val = sum(float(l.estimated_loss_value) for l in split_losses)

    batches = split.harvest_batches or []
    grade_totals = {}
    if batches:
        sold_batch_yield = sum(float(b.get('yield', 0)) for b in batches if b.get('status') == 'Sold')
        ready_batches = [
            b for b in batches
            if b.get('status') not in ['Sold', 'Loss / Discard']
            and float(b.get('yield') or 0) > 0.001
        ]
        ready_batch_yield = sum(float(b.get('yield', 0)) for b in ready_batches)
        avail_qty = max(0.0, ready_batch_yield)
        sold_qty = sold_batch_yield
        init_yield = round(sold_qty + avail_qty + loss_qty, 2)

        est_val = 0.0
        for batch in ready_batches:
            quantity = float(batch.get('yield') or 0)
            rate = float(batch.get('price') or p_mt)
            value = float(batch.get('saleValue') or (quantity * rate))
            quality = (batch.get('quality') or 'Grade 1').strip()
            grade = grade_totals.setdefault(
                quality,
                {'yield': 0.0, 'amount': 0.0, 'unit': batch.get('unit') or unit},
            )
            grade['yield'] += quantity
            grade['amount'] += value
            est_val += value

        # Auto-sync split.yield_tons (same as website GET behavior)
        target_yield = Decimal(str(round(avail_qty, 2)))
        if split.yield_tons is None or abs(float(split.yield_tons) - avail_qty) > 0.001:
            split.yield_tons = target_yield
            if split.yield_tons <= Decimal('0.001') and all(b.get('status') == 'Sold' for b in batches):
                split.stage = 'Sold'
            split.save(update_fields=['yield_tons', 'stage'])
    else:
        avail_qty = float(split.yield_tons or 0)
        init_yield = float(split.expected_yield or avail_qty)
        if init_yield < (avail_qty + loss_qty):
            init_yield = avail_qty + loss_qty
        sold_qty = max(0.0, init_yield - avail_qty - loss_qty)
        est_val = avail_qty * p_mt

    available_batches = [
        {
            'batch_index': index,
            'batch_no': batch.get('batch_no') or batch.get('batch_num') or index + 1,
            'flush_no': batch.get('flush_no') or 1,
            'yield': round(float(batch.get('yield') or 0), 2),
            'unit': batch.get('unit') or unit,
            'quality': batch.get('quality') or 'Grade 1',
            'price': float(batch.get('price') or p_mt),
            'sale_value': float(
                batch.get('saleValue')
                or (float(batch.get('yield') or 0) * float(batch.get('price') or p_mt))
            ),
            'status': batch.get('status') or 'Ready',
        }
        for index, batch in enumerate(batches)
        if batch.get('status') not in ('Sold', 'Loss / Discard')
        and float(batch.get('yield') or 0) > 0.001
    ]
    if not batches and avail_qty > 0.001:
        available_batches = [{
            'batch_index': 0,
            'batch_no': 1,
            'flush_no': split.current_flush or 1,
            'yield': round(avail_qty, 2),
            'unit': unit,
            'quality': 'Grade 1',
            'price': p_mt,
            'status': 'Ready',
            'sale_value': round(avail_qty * p_mt, 2),
        }]

    if not batches and avail_qty > 0.001:
        grade_totals['Grade 1'] = {
            'yield': avail_qty,
            'amount': est_val,
            'unit': unit,
        }
    elif avail_qty > 0.001 and est_val:
        p_mt = round(est_val / avail_qty, 2)

    grade_splits = [
        {
            'grade': quality,
            'yield': round(values['yield'], 2),
            'price': round(values['amount'] / values['yield'], 2),
            'amount': round(values['amount'], 2),
            'unit': values['unit'],
        }
        for quality, values in grade_totals.items()
        if values['yield'] > 0.001
    ]

    if avail_qty > 0.001:
        if sold_qty > 0.001 or loss_qty > 0.001:
            stock_status = 'In Stock (Partial)'
            stock_status_badge = 'warning'
        else:
            stock_status = 'In Stock'
            stock_status_badge = 'success'
    else:
        if loss_qty >= init_yield and init_yield > 0:
            stock_status = 'Lost / Waste'
            stock_status_badge = 'danger'
        else:
            stock_status = 'Sold Out'
            stock_status_badge = 'secondary'

    row = {
        'id': split.id,
        'crop_id': split.crop_id,
        'crop_name': c_name,
        'crop_variety': c_variety,
        'farm_name': f_name,
        'farm_id': f_id,
        'initial_yield': round(init_yield, 2),
        'sold_qty': round(sold_qty, 2),
        'loss_qty': round(loss_qty, 2),
        'loss_val': round(loss_val, 2),
        'available_qty': round(avail_qty, 2),
        'unit': unit,
        'price_per_mt': p_mt,
        'est_value': round(est_val, 2),
        'status': stock_status,
        'status_badge': stock_status_badge,
        'batches_count': len(batches),
        'grade_splits': grade_splits,
        'available_batches': available_batches,
        'updated_at': split.updated_at.isoformat() if split.updated_at else None,
    }
    return row, avail_qty, sold_qty, loss_qty, loss_val


class HarvestStockListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Harvest Produce & Stock',
        operation_description=(
            'Harvested crop stock balances (website inventory Harvest Produce & Stock tab parity): '
            'per-split yield, sold, loss/waste, available stock, estimated value and status, '
            'plus overall stats. Supports search, farm/status filters and pagination.'
        ),
        manual_parameters=[
            openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description='Page number (default 1)'),
            openapi.Parameter('page_size', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description='Rows per page (default 20, max 100)'),
            openapi.Parameter('search', openapi.IN_QUERY, type=openapi.TYPE_STRING, description='Match crop name, variety or farm land name'),
            openapi.Parameter('farm_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description='Filter by farm id'),
            openapi.Parameter('status', openapi.IN_QUERY, type=openapi.TYPE_STRING, description='In Stock | In Stock (Partial) | Sold Out | Lost / Waste'),
        ],
        responses={200: 'Paginated harvest stock with stats'},
        tags=['Mobile - 11. Inventory'],
    )
    def get(self, request):
        profile = get_farmer_profile(request.user)
        if profile is None:
            return Response({'error': 'Access denied: Not a farmer profile.'}, status=status.HTTP_403_FORBIDDEN)

        harvest_splits_qs = CropSplit.objects.filter(
            farm__farmer=profile,
            stage__in=['Harvested', 'Sold', 'Partially Sold'],
        ).select_related('farm', 'crop').order_by('-updated_at', '-id')

        extra_splits = CropSplit.objects.filter(
            farm__farmer=profile,
        ).exclude(
            id__in=harvest_splits_qs.values_list('id', flat=True),
        ).filter(
            Q(yield_tons__gt=0) | ~Q(harvest_batches=[]),
        ).select_related('farm', 'crop').order_by('-updated_at', '-id')

        all_raw_splits = list(harvest_splits_qs) + list(extra_splits)

        rows = []
        total_stock = 0.0
        total_val = 0.0
        total_loss = 0.0
        for split in all_raw_splits:
            row, avail_qty, _sold_qty, loss_qty, _loss_val = _harvest_stock_row(split)
            rows.append(row)
            total_stock += avail_qty
            total_val += row['est_value']
            total_loss += loss_qty

        # Filters (client-side over the computed rows; website does the same in JS)
        search = (request.query_params.get('search') or '').strip().lower()
        farm_id = request.query_params.get('farm_id')
        status_filter = (request.query_params.get('status') or '').strip()

        filtered = rows
        if search:
            filtered = [
                r for r in filtered
                if search in r['crop_name'].lower()
                or search in (r['crop_variety'] or '').lower()
                or search in r['farm_name'].lower()
            ]
        if farm_id:
            try:
                farm_id_int = int(farm_id)
                filtered = [r for r in filtered if r['farm_id'] == farm_id_int]
            except (TypeError, ValueError):
                pass
        if status_filter:
            filtered = [r for r in filtered if r['status'] == status_filter]

        # Pagination
        try:
            page = max(1, int(request.query_params.get('page', 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = min(100, max(1, int(request.query_params.get('page_size', 20))))
        except (TypeError, ValueError):
            page_size = 20

        start = (page - 1) * page_size
        end = start + page_size
        page_rows = filtered[start:end]

        from apps.farmer.models import CropHarvestLoss
        loss_incidents = CropHarvestLoss.objects.filter(farmer=profile).count()

        return Response({
            'count': len(filtered),
            'results': page_rows,
            'stats': {
                'total_crops': len(rows),
                'total_stock_tons': round(total_stock, 2),
                'total_value': round(total_val, 2),
                'total_loss_tons': round(total_loss, 2),
                'loss_incidents': loss_incidents,
            },
        }, status=status.HTTP_200_OK)


class RecordCropLossAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [FormParser, JSONParser, MultiPartParser]

    @swagger_auto_schema(
        operation_summary='Record Harvest Crop Loss / Waste',
        operation_description=(
            'Website record_crop_loss parity: deducts loss from the split stock and ready '
            'harvest batches, creates a CropHarvestLoss record and a FarmActivity log.'
        ),
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['split_id', 'loss_quantity'],
            properties={
                'split_id': openapi.Schema(type=openapi.TYPE_INTEGER, description='CropSplit id'),
                'loss_quantity': openapi.Schema(type=openapi.TYPE_NUMBER, description='Loss qty (> 0, <= available stock)'),
                'loss_reason': openapi.Schema(type=openapi.TYPE_STRING, description='Default: Spoilage / Rot'),
                'loss_date': openapi.Schema(type=openapi.TYPE_STRING, description='YYYY-MM-DD, default today'),
                'estimated_loss_value': openapi.Schema(type=openapi.TYPE_NUMBER, description='Defaults to qty * price_per_mt'),
                'notes': openapi.Schema(type=openapi.TYPE_STRING),
            },
        ),
        responses={
            200: openapi.Response('Loss recorded', openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'success': openapi.Schema(type=openapi.TYPE_BOOLEAN),
                    'message': openapi.Schema(type=openapi.TYPE_STRING),
                    'remaining_qty': openapi.Schema(type=openapi.TYPE_NUMBER),
                    'stock_status': openapi.Schema(type=openapi.TYPE_STRING),
                },
            )),
            400: openapi.Response('Validation error'),
        },
        tags=['Mobile - 11. Inventory'],
    )
    def post(self, request):
        from decimal import Decimal
        from django.utils.dateparse import parse_date as django_parse_date
        from apps.farmer.models import CropHarvestLoss, FarmActivity

        profile = get_farmer_profile(request.user)
        if profile is None:
            return Response({'error': 'Access denied: Not a farmer profile.'}, status=status.HTTP_403_FORBIDDEN)

        split_id = request.data.get('split_id')
        try:
            split = CropSplit.objects.select_related('farm', 'crop').get(id=split_id, farm__farmer=profile)
        except (CropSplit.DoesNotExist, TypeError, ValueError):
            return Response({'success': False, 'error': 'Harvest stock record not found.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            loss_qty = Decimal(str(request.data.get('loss_quantity', '0')))
        except Exception:
            loss_qty = Decimal('0')
        if loss_qty <= Decimal('0'):
            return Response({'success': False, 'error': 'Please enter a valid loss quantity greater than zero.'}, status=status.HTTP_400_BAD_REQUEST)

        # Sync stock first (batches-aware availability, same as the list API)
        _harvest_stock_row(split)
        split.refresh_from_db()
        available = split.yield_tons or Decimal('0')
        if loss_qty > available:
            return Response(
                {'success': False, 'error': f'Loss quantity ({loss_qty}) cannot exceed available stock ({available}).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        loss_reason = (request.data.get('loss_reason') or 'Spoilage / Rot').strip()
        notes = (request.data.get('notes') or '').strip()
        loss_date_raw = request.data.get('loss_date')
        loss_date = None
        if loss_date_raw:
            try:
                loss_date = django_parse_date(str(loss_date_raw))
            except (TypeError, ValueError):
                loss_date = None
        if loss_date is None:
            loss_date = timezone.localdate()

        price = split.price_per_mt or Decimal('0')
        est_val_raw = request.data.get('estimated_loss_value')
        try:
            est_val = Decimal(str(est_val_raw)) if est_val_raw not in (None, '') else (loss_qty * price)
        except Exception:
            est_val = loss_qty * price

        # Deduct from split yield
        split.yield_tons = max(Decimal('0'), available - loss_qty)
        if split.yield_tons <= Decimal('0.001'):
            split.yield_tons = Decimal('0')
            split.stage = 'Sold'
        if split.price_per_mt:
            split.total_sale_value = split.yield_tons * split.price_per_mt

        # Deduct from ready batches
        if split.harvest_batches:
            rem_loss = float(loss_qty)
            batches = list(split.harvest_batches)
            for b in batches:
                if b.get('status') != 'Sold' and rem_loss > 0:
                    b_yield = float(b.get('yield', 0))
                    if b_yield <= rem_loss:
                        rem_loss -= b_yield
                        b['yield'] = 0.0
                        b['status'] = 'Loss / Discard'
                    else:
                        b['yield'] = round(b_yield - rem_loss, 2)
                        rem_loss = 0.0
            split.harvest_batches = batches

        split.save()

        CropHarvestLoss.objects.create(
            farmer=profile,
            split=split,
            loss_date=loss_date,
            loss_quantity=loss_qty,
            unit=split.expected_yield_unit or 'Tons',
            loss_reason=loss_reason,
            estimated_loss_value=est_val,
            notes=notes,
        )

        crop_name = split.crop.name if split.crop else (split.crop_category or 'Produce')
        try:
            FarmActivity.objects.create(
                farm=split.farm,
                activity_type='Crop Loss / Wastage',
                asset_name=f'{crop_name} - {loss_qty} {split.expected_yield_unit or "Tons"}',
                quantity_metric=f'{loss_qty} {split.expected_yield_unit or "Tons"}',
                operator_name=request.user.get_full_name() or request.user.first_name or request.user.username,
                notes=f'Reason: {loss_reason}. {notes}'.strip(),
                amount=float(est_val),
            )
        except Exception:
            pass

        row, avail_qty, _sold, _loss, _lval = _harvest_stock_row(split)
        return Response({
            'success': True,
            'message': f'Recorded {loss_qty} {split.expected_yield_unit or "Tons"} loss for {crop_name}.',
            'remaining_qty': avail_qty,
            'stock_status': row['status'],
        }, status=status.HTTP_200_OK)


class HarvestLossHistoryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get Harvest Loss / Waste History',
        operation_description='Website crop_loss_history_api parity. Optional split_id filter.',
        manual_parameters=[
            openapi.Parameter('split_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description='Filter losses for one harvest split'),
        ],
        responses={200: 'List of crop loss records'},
        tags=['Mobile - 11. Inventory'],
    )
    def get(self, request):
        from apps.farmer.models import CropHarvestLoss

        profile = get_farmer_profile(request.user)
        if profile is None:
            return Response({'error': 'Access denied: Not a farmer profile.'}, status=status.HTTP_403_FORBIDDEN)

        qs = CropHarvestLoss.objects.filter(farmer=profile)
        split_id = request.query_params.get('split_id')
        if split_id:
            qs = qs.filter(split_id=split_id)

        losses_data = []
        for l in qs.select_related('split', 'split__farm', 'split__crop').order_by('-loss_date', '-id')[:100]:
            crop_title = l.split.crop.name if l.split.crop else (l.split.crop_category or 'Produce')
            losses_data.append({
                'id': l.id,
                'date': l.loss_date.strftime('%Y-%m-%d') if l.loss_date else None,
                'farm': l.split.farm.land_name if l.split.farm else 'General Farm',
                'crop': crop_title,
                'loss_quantity': float(l.loss_quantity),
                'unit': l.unit or 'Tons',
                'reason': l.loss_reason or 'Other',
                'estimated_value': float(l.estimated_loss_value or 0),
                'notes': l.notes or '',
            })
        return Response({'count': len(losses_data), 'results': losses_data}, status=status.HTTP_200_OK)
