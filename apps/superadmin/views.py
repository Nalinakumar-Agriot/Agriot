import json
import os
import re

from django.conf import settings
from django.contrib import messages
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.superadmin.models import (
    City,
    Country,
    Crop,
    CropType,
    DocumentCategory,
    DocumentType,
    ExpenseType,
    ExpenseUnit,
    Designation,
    CustomerType,
    GeneralSetting,
    GuidelineCategory,
    HardwareClass,
    HarvestingFrequency,
    InventoryItem,
    LocationCategory,
    ManPowerWorkType,
    Package,
    ProductType,
    ProductUnit,
    SalesDeduction,
    ScheduleActivityType,
    SoilType,
    IrrigationType,
    WaterSourceType,
    ElectricityType,
    FarmingType,
    FencingType,
    CropCategory,
    CropPattern,
    CropVariety,
    CropQuality,
    MaintenanceSchedule,
    HealthIndicator,
    State,
    Subscription,
    Taluk,
)
from apps.superadmin.master_utils import get_or_reactivate_master


def ajax_get_states(request):
    country_id = request.GET.get('country_id')
    if country_id:
        states = State.objects.filter(country_id=country_id).values('id', 'name').order_by('name')
        return JsonResponse(list(states), safe=False)
    # Return all states if country_id is omitted or empty
    states = State.objects.all().values('id', 'name').order_by('name')
    return JsonResponse(list(states), safe=False)

def ajax_get_cities(request):
    state_id = request.GET.get('state_id')
    if state_id:
        cities = City.objects.filter(state_id=state_id).values('id', 'name').order_by('name')
        return JsonResponse(list(cities), safe=False)
    return JsonResponse([], safe=False)

def ajax_get_taluks(request):
    from apps.superadmin.models import Taluk
    city_id = request.GET.get('city_id') or request.GET.get('district_id')
    if city_id:
        taluks = Taluk.objects.filter(city_id=city_id).values('id', 'name').order_by('name')
        return JsonResponse(list(taluks), safe=False)
    return JsonResponse([], safe=False)

# -------------------------
# MENU LOADER
# -------------------------
def get_menu_data():
    json_path = os.path.join(
        str(settings.BASE_DIR),
        'templates',
        'layout',
        'partials',
        'menu',
        'superadmin',
        'json',
        'vertical_menu.json'
    )

    print(f"[MENU] Loading from: {json_path}")
    print(f"[MENU] File exists: {os.path.exists(json_path)}")

    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# -------------------------
# AUTH PAGES
# -------------------------
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token

from auth.models import Profile
from auth.portals import admin_users, is_admin_user


def get_admin_actor_name(request):
    if not request.user or not request.user.is_authenticated:
        return "System"
    user = request.user
    name = user.first_name or user.email or user.username
    role = user.profile.role if hasattr(user, 'profile') and user.profile.role else "Admin"
    role_label = "Superadmin" if (user.is_superuser or user.email == 'admin@agriot.com') else role.title()
    return f"{name} ({role_label})"


def log_admin_action(request, title, message, icon_class="ri-notification-3-line", bg_color="primary"):
    try:
        from auth.models import SystemNotification
        actor = get_admin_actor_name(request)
        full_msg = f"{message} (Action by: {actor})"

        SystemNotification.objects.create(
            user=request.user,
            role_target='superadmin',
            title=title,
            message=full_msg,
            icon_class=icon_class,
            bg_color=bg_color
        )
    except Exception as e:
        print(f"[LOG ADMIN ACTION ERROR] {e}")


def login_view(request):
    error = None
    if request.method == "POST":
        mobile = (request.POST.get("mobile") or "").strip()
        password = (request.POST.get("password") or "").strip()
        remember_me = request.POST.get("remember_me")

        from apps.superadmin.phone_utils import find_profile_by_phone, find_staff_by_phone
        profile = find_profile_by_phone(mobile)
        staff = find_staff_by_phone(mobile)
        login_user = profile.user if profile else (staff.user if staff else User.objects.filter(username=mobile).first())

        def is_suspended_status(value):
            value = str(value or '').strip().lower()
            return value == '1' or 'suspend' in value or 'lock' in value

        if (staff and is_suspended_status(staff.status)) or \
                (profile and is_suspended_status(profile.status)) or \
                (login_user and not login_user.is_active):
            error = "This account is suspended. Please contact the administrator."
        elif profile:
            user = authenticate(request, username=profile.user.username, password=password)
            if user is not None:
                if not is_admin_user(user):
                    error = "This account does not have administration access."
                elif not user.is_active:
                    error = "This account is suspended. Please contact the administrator."
                else:
                    login(request, user)
                    for key in ('active_role', 'active_farmer_id', 'active_staff_id'):
                        request.session.pop(key, None)
                    if remember_me:
                        request.session.set_expiry(1209600)  # 2 weeks
                    else:
                        request.session.set_expiry(0) # Browser close

                    # Create/Get auth token
                    token, _ = Token.objects.get_or_create(user=user)  # type: ignore

                    response = redirect('superadmin-dashboard')
                    if remember_me:
                        response.set_cookie('saved_mobile', mobile, max_age=1209600)
                        response.set_cookie('saved_password', password, max_age=1209600)
                    else:
                        response.delete_cookie('saved_mobile')
                        response.delete_cookie('saved_password')

                    return response
            else:
                error = "Invalid mobile number or password."
        else:
            # Fallback to direct username authentication (e.g. 'admin')
            user = authenticate(request, username=mobile, password=password)
            if user is not None:
                if not is_admin_user(user):
                    error = "This account does not have administration access."
                elif not user.is_active:
                    error = "Your account has been deactivated/suspended."
                else:
                    login(request, user)
                    for key in ('active_role', 'active_farmer_id', 'active_staff_id'):
                        request.session.pop(key, None)
                    token, _ = Token.objects.get_or_create(user=user)
                    return redirect('superadmin-dashboard')
            else:
                error = "Invalid mobile number or password."

    template_config = getattr(settings, "TEMPLATE_CONFIG", {})
    theme = template_config.get("theme", "light") if isinstance(template_config, dict) else "light"
    return render(request, "superadmin/login.html", {
        "theme": theme,
        "error": error
    })

def logout_view(request):
    logout(request)
    return redirect('home')

def update_my_profile(request):
    if not is_admin_user(request.user):
        return JsonResponse({"error": "Administration access required."}, status=403)
    if request.method == "POST" and request.user.is_authenticated:
        name = request.POST.get("name") or request.POST.get("first_name")
        email = request.POST.get("email")
        favicon = request.FILES.get("favicon") or request.FILES.get("profile_image")

        user = request.user
        if name:
            name_parts = name.strip().split(" ", 1)
            user.first_name = name_parts[0]
            if len(name_parts) > 1:
                user.last_name = name_parts[1]
            else:
                user.last_name = ""
        if email:
            user.email = email
        user.save()

        if favicon:
            if hasattr(user, 'profile'):
                user.profile.profile_image = favicon
                user.profile.save()
            else:
                from auth.models import Profile
                Profile.objects.create(user=user, profile_image=favicon, role='superadmin', email=user.email)

        messages.success(request, "Profile updated successfully!")

    return redirect('superadmin-dashboard')


import random
from datetime import timedelta

import requests
from django.utils import timezone


def send_otp_sms(mobile, otp_value):
    import os
    from apps.superadmin.phone_utils import format_phone_for_sms
    authkey = os.environ.get('MSG91_AUTHKEY', '155099Ajzgw9B8dfq6737236aP1')
    sender_id = os.environ.get('MSG91_SENDER_ID', 'ELYSIU')
    template_id = os.environ.get('MSG91_TEMPLATE_ID', '1007049094377493090')
    default_country_code = os.environ.get('MSG91_COUNTRY_CODE', '91')
    message_template = os.environ.get(
        'MSG91_MESSAGE_TEMPLATE',
        'Dear User, Your One Time Password (OTP) for logging into the Elysium Academy Application is {#var#}'
    )

    formatted_mobile_number = format_phone_for_sms(mobile, default_country_code=default_country_code)
    message_content = message_template.replace("{#var#}", str(otp_value))

    url = f"https://api.msg91.com/api/sendhttp.php?authkey={authkey}&sender={sender_id}&route=default&message={message_content}&mobiles={formatted_mobile_number}&DLT_TE_ID={template_id}"

    try:
        response = requests.get(url)
        if response.status_code == 200:
            response_text = response.text.strip()
            if response_text and len(response_text) > 10:
                return True, response_text
            else:
                return False, f"Failed to send OTP. {response_text}"
        else:
            return False, "Failed to connect to Msg91."
    except Exception as e:
        return False, str(e)


def forgot_password(request):
    error = None
    if request.method == "POST":
        mobile = (request.POST.get("mobile") or "").strip()
        from apps.superadmin.phone_utils import find_profile_by_phone
        profile = find_profile_by_phone(mobile)
        if profile:
            # Generate 6-digit OTP
            otp_value = random.randint(100000, 999999)
            target_mobile = profile.mobile_number or mobile

            success, message = send_otp_sms(target_mobile, otp_value)

            if success:
                # OTP sent successfully
                profile.otp = str(otp_value)
                profile.otp_expiry_at = timezone.now() + timedelta(minutes=2.5)
                profile.save()

                request.session['reset_mobile'] = target_mobile
                return redirect('verify-otp')
            else:
                error = message
        else:
            error = "Mobile number not found."

    return render(request, "superadmin/forgot_password.html", {
        "error": error
    })


def verify_otp(request):
    error = None
    mobile = request.session.get('reset_mobile')
    if not mobile:
        return redirect('forgot-password')

    if request.method == "POST":
        otp_entered = request.POST.get("otp")
        try:
            profile = Profile.objects.get(mobile_number=mobile)
            if profile.otp and profile.otp == otp_entered:
                if profile.otp_expiry_at and profile.otp_expiry_at >= timezone.now():
                    # OTP is correct
                    request.session['otp_verified'] = True
                    return redirect('reset-password')
                else:
                    error = "OTP has expired. Please request a new one."
            else:
                error = "Invalid OTP. Please try again."
        except Profile.DoesNotExist:
            error = "Mobile number not found."

    return render(request, "superadmin/verify_otp.html", {
        "error": error,
    })


def reset_password(request):
    error = None
    mobile = request.session.get('reset_mobile')
    otp_verified = request.session.get('otp_verified')

    if not mobile or not otp_verified:
        return redirect('forgot-password')

    if request.method == "POST":
        new_password = request.POST.get("new_password")
        confirm_password = request.POST.get("confirm_password")

        if new_password != confirm_password:
            error = "Passwords do not match."
        elif len(new_password) < 6:
            error = "Password must be at least 6 characters long."
        else:
            try:
                profile = Profile.objects.get(mobile_number=mobile)
                user = profile.user
                user.set_password(new_password)
                user.save()
                # Clear token and session
                profile.forget_password_token = None
                profile.otp = None
                profile.otp_expiry_at = None
                profile.save()
                del request.session['reset_mobile']
                del request.session['otp_verified']
                return redirect('home')
            except Profile.DoesNotExist:
                error = "User not found."

    return render(request, "superadmin/reset_password.html", {
        "error": error
    })


# -------------------------
# DASHBOARD
# -------------------------
def dashboard(request):
    import json
    import re
    from django.db.models import Sum, Count
    from apps.superadmin.models import State, Subscription, Package
    from auth.models import Profile
    from apps.farmer.models import Farm, FarmerStaff, FarmerCustomer, FarmerVendor, LedgerTransaction

    farmers_qs = Profile.objects.filter(role='farmer').select_related('user', 'state', 'district').order_by('-id')
    total_farmers = farmers_qs.count()
    active_farmers = farmers_qs.filter(status__in=['1', 'Active', 'active']).count() or total_farmers

    farms_qs = Farm.objects.select_related('crop_type', 'farmer', 'farmer__user', 'district')
    total_farms = farms_qs.count()
    total_acreage = 0.0
    crop_counts = {}
    for f in farms_qs:
        if f.total_area:
            try:
                m = re.search(r'[\d\.]+', str(f.total_area))
                if m:
                    acres = float(m.group())
                    total_acreage += acres
                    c_name = f.crop_type.name if f.crop_type else "Mixed Crops"
                    crop_counts[c_name] = crop_counts.get(c_name, 0.0) + acres
            except (ValueError, TypeError):
                pass

    total_subscriptions = Subscription.objects.count()
    active_subscriptions = Subscription.objects.filter(subscription_status__iexact='active').count()
    total_mrr = Subscription.objects.filter(subscription_status__iexact='active').aggregate(Sum('monthly_amount'))['monthly_amount__sum'] or 0
    total_packages = Package.objects.count()

    active_states = State.objects.filter(country__name__iexact='India').count() or State.objects.count()
    active_districts = Profile.objects.filter(role='farmer', district__isnull=False).values('district').distinct().count()

    total_staff = FarmerStaff.objects.count()
    total_customers = FarmerCustomer.objects.count()
    total_vendors = FarmerVendor.objects.count()
    total_transactions = LedgerTransaction.objects.count()
    
    total_outflow = abs(float(LedgerTransaction.objects.filter(amount__lt=0).aggregate(Sum('amount'))['amount__sum'] or 0))
    total_inflow = float(LedgerTransaction.objects.filter(amount__gt=0).aggregate(Sum('amount'))['amount__sum'] or 0)

    recent_farmers = farmers_qs[:6]
    recent_subscriptions = Subscription.objects.select_related('farmer', 'farmer__user', 'package').order_by('-id')[:6]

    try:
        from auth.models import SystemNotification
        recent_notifications = list(SystemNotification.objects.filter(role_target='superadmin').order_by('-created_at')[:5])
    except Exception:
        recent_notifications = []

    # Package distribution for donut chart
    pkg_distribution = list(Subscription.objects.values('package__name').annotate(count=Count('id')).order_by('-count'))
    pkg_labels = [p['package__name'] or 'Standard' for p in pkg_distribution] if pkg_distribution else ['Standard Plan', 'Premium Plan', 'Basic Plan']
    pkg_series = [p['count'] for p in pkg_distribution] if pkg_distribution else [1, 1, 1]

    # Crop Distribution for Radial/Bar chart
    if not crop_counts:
        crop_counts = {'Paddy': 12.5, 'Sugarcane': 8.0, 'Cotton': 6.2, 'Vegetables': 4.5}
    crop_labels = list(crop_counts.keys())[:5]
    crop_series = [round(v, 1) for v in list(crop_counts.values())[:5]]

    # Monthly Trends (12 months)
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    farmer_trend = [max(0, total_farmers - 2), max(0, total_farmers - 1), total_farmers, total_farmers, total_farmers, total_farmers, total_farmers, total_farmers, total_farmers, total_farmers, total_farmers, total_farmers]
    sub_trend = [max(0, total_subscriptions - 1), total_subscriptions, total_subscriptions, total_subscriptions, total_subscriptions, total_subscriptions, total_subscriptions, total_subscriptions, total_subscriptions, total_subscriptions, total_subscriptions, total_subscriptions]

    context = {
        'total_farmers': total_farmers,
        'active_farmers': active_farmers,
        'total_farms': total_farms,
        'total_acreage': round(total_acreage, 2),
        'total_subscriptions': total_subscriptions,
        'active_subscriptions': active_subscriptions,
        'total_mrr': round(float(total_mrr), 2),
        'total_packages': total_packages,
        'active_states': active_states,
        'active_districts': active_districts,
        'total_staff': total_staff,
        'total_customers': total_customers,
        'total_vendors': total_vendors,
        'total_transactions': total_transactions,
        'total_outflow': round(total_outflow, 2),
        'total_inflow': round(total_inflow, 2),
        'recent_farmers': recent_farmers,
        'recent_subscriptions': recent_subscriptions,
        'recent_notifications': recent_notifications,
        'pkg_labels_json': json.dumps(pkg_labels),
        'pkg_series_json': json.dumps(pkg_series),
        'crop_labels_json': json.dumps(crop_labels),
        'crop_series_json': json.dumps(crop_series),
        'months_json': json.dumps(months),
        'farmer_trend_json': json.dumps(farmer_trend),
        'sub_trend_json': json.dumps(sub_trend),
    }
    return render(request, "superadmin/dashboard.html", context)


# -------------------------
# FARMER MANAGEMENT
# -------------------------

def manage_farmer(request):
    import re
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "create":
                full_name = (request.POST.get("full_name") or "").strip()
                phone = (request.POST.get("phone") or "").strip()
                email = (request.POST.get("email") or "").strip()
                state_id = request.POST.get("state")
                district_id = request.POST.get("district")
                taluk_val = request.POST.get("taluk")
                pincode = (request.POST.get("pincode") or "").strip()
                status = request.POST.get("status")

                # Clean and validate phone number (international format: 7 to 15 digits, optional +)
                clean_phone = re.sub(r'[^0-9+]', '', phone)
                if clean_phone.count('+') > 1 or clean_phone.find('+') > 0:
                    clean_phone = '+' + clean_phone.replace('+', '')
                digits_only = re.sub(r'[^0-9]', '', clean_phone)

                if not digits_only or len(digits_only) < 7 or len(digits_only) > 15:
                    messages.error(request, "Please enter a valid phone number (7 to 15 digits).")
                    return redirect('manage_farmer')

                if not full_name or len(full_name) < 2:
                    messages.error(request, "Please enter a valid full name (minimum 2 characters).")
                    return redirect('manage_farmer')

                if email and not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
                    messages.error(request, "Please enter a valid email address.")
                    return redirect('manage_farmer')

                if not re.fullmatch(r'[0-9]{6}', pincode):
                    messages.error(request, "Pincode must be exactly 6 digits.")
                    return redirect('manage_farmer')

                phone = clean_phone

                taluk_name = taluk_val
                if taluk_val and taluk_val.isdigit():
                    from apps.superadmin.models import Taluk
                    t_obj = Taluk.objects.filter(id=taluk_val).first()
                    if t_obj:
                        taluk_name = t_obj.name

                from django.db import transaction

                if email and (User.objects.filter(email__iexact=email).exists() or Profile.objects.filter(email__iexact=email).exists()):
                    messages.error(request, f"A user with email '{email}' already exists.")
                    return redirect('manage_farmer')

                from apps.superadmin.phone_utils import check_mobile_already_exists
                is_phone_taken, phone_err = check_mobile_already_exists(phone)
                if is_phone_taken:
                    messages.error(request, phone_err)
                    return redirect('manage_farmer')

                if User.objects.filter(username=phone).exists():
                    existing_user = User.objects.get(username=phone)
                    if not hasattr(existing_user, 'profile'):
                        with transaction.atomic():
                            profile, _ = Profile.objects.get_or_create(user=existing_user)
                            profile.role = 'farmer'
                            profile.mobile_number = phone
                            if email: profile.email = email
                            if state_id: profile.state_id = state_id
                            if district_id: profile.district_id = district_id
                            if taluk_name: profile.taluka = taluk_name
                            profile.pincode = pincode
                            if status: profile.status = status
                            profile.save()
                            if full_name:
                                existing_user.first_name = full_name
                                existing_user.save()
                        messages.success(request, "Farmer registered successfully!")
                    else:
                        messages.error(request, "A user with this phone number already exists.")
                else:
                    try:
                        with transaction.atomic():
                            user = User.objects.create_user(username=phone, first_name=full_name, email=email or None, password=phone)
                            profile, _ = Profile.objects.get_or_create(user=user)
                            profile.role = 'farmer'
                            profile.mobile_number = phone
                            if email: profile.email = email
                            if state_id: profile.state_id = state_id
                            if district_id: profile.district_id = district_id
                            if taluk_name: profile.taluka = taluk_name
                            profile.pincode = pincode
                            if status: profile.status = status
                            profile.save()
                        try:
                            if profile.email:
                                from apps.superadmin.email_utils import send_welcome_email
                                send_welcome_email(profile, request=request)
                        except Exception as e:
                            print(f"[Email Trigger Error - Welcome]: {e}")
                        messages.success(request, "Farmer registered successfully!")
                    except Exception as e:
                        messages.error(request, f"Failed to register farmer: {str(e)}")

            elif action == "update":
                farmer_id = request.POST.get("farmer_id")
                full_name = (request.POST.get("full_name") or "").strip()
                phone = (request.POST.get("phone") or "").strip()
                email = (request.POST.get("email") or "").strip()
                state_id = request.POST.get("state")
                district_id = request.POST.get("district")
                taluk_val = request.POST.get("taluk")
                pincode = (request.POST.get("pincode") or "").strip()
                status = request.POST.get("status")

                clean_phone = re.sub(r'[^0-9+]', '', phone)
                if clean_phone.count('+') > 1 or clean_phone.find('+') > 0:
                    clean_phone = '+' + clean_phone.replace('+', '')
                digits_only = re.sub(r'[^0-9]', '', clean_phone)

                if not digits_only or len(digits_only) < 7 or len(digits_only) > 15:
                    messages.error(request, "Please enter a valid phone number (7 to 15 digits).")
                    return redirect('manage_farmer')

                if not full_name or len(full_name) < 2:
                    messages.error(request, "Please enter a valid full name.")
                    return redirect('manage_farmer')

                if email and not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
                    messages.error(request, "Please enter a valid email address.")
                    return redirect('manage_farmer')

                if not re.fullmatch(r'[0-9]{6}', pincode):
                    messages.error(request, "Pincode must be exactly 6 digits.")
                    return redirect('manage_farmer')

                phone = clean_phone

                taluk_name = taluk_val
                if taluk_val and taluk_val.isdigit():
                    from apps.superadmin.models import Taluk
                    t_obj = Taluk.objects.filter(id=taluk_val).first()
                    if t_obj:
                        taluk_name = t_obj.name

                try:
                    profile = Profile.objects.get(id=farmer_id, role='farmer')
                    user = profile.user

                    from apps.superadmin.phone_utils import check_mobile_already_exists
                    is_phone_taken, phone_err = check_mobile_already_exists(
                        phone,
                        exclude_user_id=user.id,
                        exclude_profile_id=profile.id
                    )
                    if is_phone_taken:
                        messages.error(request, phone_err)
                        return redirect('manage_farmer')

                    if User.objects.filter(username=phone).exclude(id=user.id).exists():
                        messages.error(request, "Another user with this phone number already exists.")
                        return redirect('manage_farmer')

                    if email and Profile.objects.filter(email__iexact=email).exclude(id=profile.id).exists():
                        messages.error(request, f"Another farmer with email '{email}' already exists.")
                        return redirect('manage_farmer')

                    if full_name: user.first_name = full_name
                    if email:
                        user.email = email
                        profile.email = email
                    if phone: user.username = phone
                    user.save()

                    if phone: profile.mobile_number = phone
                    if state_id: profile.state_id = state_id
                    if district_id: profile.district_id = district_id
                    if taluk_name is not None: profile.taluka = taluk_name
                    if pincode is not None: profile.pincode = pincode
                    if status: profile.status = status
                    profile.save()
                    messages.success(request, "Farmer updated successfully!")
                except Profile.DoesNotExist:
                    messages.error(request, "Farmer not found.")

            elif action == "deactivate":
                farmer_id = request.POST.get("farmer_id")
                try:
                    profile = Profile.objects.get(id=farmer_id, role='farmer')
                    profile.status = 'Inactive'
                    profile.save()
                    messages.success(request, f"{profile.user.first_name} has been deactivated successfully.")
                except Profile.DoesNotExist:
                    messages.error(request, "Farmer not found.")
                return redirect('manage_farmer')

            elif action == "toggle_status":
                farmer_id = request.POST.get("farmer_id")
                new_status = request.POST.get("status")
                try:
                    profile = Profile.objects.get(id=farmer_id, role='farmer')
                    if not new_status:
                        new_status = 'Inactive' if profile.status == 'Active' else 'Active'
                    profile.status = new_status
                    profile.save()
                    from django.http import JsonResponse
                    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('ajax') == '1':
                        return JsonResponse({'status': 'success', 'new_status': profile.status, 'message': f'Status updated to {profile.status}'})
                    messages.success(request, f"Farmer status updated to {profile.status}!")
                except Profile.DoesNotExist:
                    from django.http import JsonResponse
                    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('ajax') == '1':
                        return JsonResponse({'status': 'error', 'message': 'Farmer not found'}, status=404)
                    messages.error(request, "Farmer not found.")
                return redirect('manage_farmer')

            elif action == "delete":
                farmer_id = request.POST.get("farmer_id")
                try:
                    profile = Profile.objects.get(id=farmer_id, role='farmer')
                    user = profile.user
                    from django.db.models.signals import post_delete
                    from apps.farmer.models import notify_on_delete
                    post_delete.disconnect(notify_on_delete)
                    try:
                        if user:
                            user.delete()
                        else:
                            profile.delete()
                        messages.success(request, "Farmer deleted successfully!")
                    finally:
                        post_delete.connect(notify_on_delete)
                except Profile.DoesNotExist:
                    messages.error(request, "Farmer not found.")
                except Exception as e:
                    messages.error(request, f"Error deleting farmer: {str(e)}")

        except Exception as e:
            messages.error(request, f"Operation failed: {str(e)}")

        return redirect('manage_farmer')

    states = State.objects.filter(country__name__iexact='India').order_by('name')
    if not states.exists():
        states = State.objects.all().order_by('name')
    farmers = Profile.objects.filter(role='farmer').select_related('user', 'state', 'district').order_by('-created_at')

    unique_districts = Profile.objects.filter(role='farmer', district__isnull=False).values_list('district__name', flat=True).distinct()

    from django.db.models import Sum

    from apps.farmer.models import CropSplit, Farm, LedgerTransaction
    from apps.superadmin.models import Subscription

    for farmer in farmers:
        # Get total physical acreage from Farm lands owned by this farmer
        farms = Farm.objects.filter(farmer=farmer)
        total_physical_acres = 0.0
        for f in farms:
            if f.total_area:
                try:
                    match = re.search(r'[\d\.]+', str(f.total_area))
                    if match:
                        total_physical_acres += float(match.group())
                except (ValueError, TypeError):
                    pass

        from apps.superadmin.subscription_utils import get_farmer_subscription_details
        sub_details = get_farmer_subscription_details(farmer)
        farmer.sub_details = sub_details

        # Package land limit
        active_sub = sub_details['subscription'] if not sub_details['is_expired'] else None
        max_acre_support = active_sub.package.max_acre_support if active_sub and active_sub.package and active_sub.package.max_acre_support else 0
        if str(max_acre_support).strip().lower() == 'unlimited':
            farmer.package_land_limit = "Unlimited"
            farmer.is_land_unlimited = True
            farmer.land_usage_exceeded = False
        else:
            try:
                max_limit = float(max_acre_support)
            except (ValueError, TypeError):
                max_limit = 0.0
            farmer.package_land_limit = round(max_limit, 2)
            farmer.is_land_unlimited = False
            farmer.land_usage_exceeded = (total_physical_acres > max_limit) if max_limit > 0 else False

        farmer.current_land_usage = round(total_physical_acres, 2)

        if total_physical_acres > 0:
            farmer.total_land_size = f"{round(total_physical_acres, 2)} Acres"
        else:
            farmer.total_land_size = "0 Acres"

        # Get primary crop and total crops
        splits = CropSplit.objects.filter(farm__farmer=farmer)
        crop_names = set(splits.filter(crop__isnull=False).values_list('crop__name', flat=True))
        farm_crop_types = set(farms.filter(crop_type__isnull=False).values_list('crop_type__name', flat=True))
        all_crops = [c for c in (crop_names | farm_crop_types) if c]

        splits_count = splits.count()
        farms_crop_count = farms.filter(crop_type__isnull=False).count()
        total_count = splits_count if splits_count > 0 else (farms_crop_count if farms_crop_count > 0 else len(all_crops))

        farmer.total_crops = total_count
        farmer.crop_list_str = ", ".join(all_crops) if all_crops else "No Crops"

        first_split = splits.select_related('crop').first()
        if first_split and first_split.crop:
            farmer.primary_crop = first_split.crop.name
        else:
            first_farm_with_crop = farms.filter(crop_type__isnull=False).first()
            farmer.primary_crop = first_farm_with_crop.crop_type.name if first_farm_with_crop and first_farm_with_crop.crop_type else "Unknown"

        # Get Earning Amount
        inflow = LedgerTransaction.objects.filter(farmer=farmer, amount__gt=0).aggregate(Sum('amount'))['amount__sum'] or 0
        inflow_str = f"{float(inflow):.2f}"
        parts = inflow_str.split('.')
        int_part = parts[0]
        if len(int_part) > 3:
            farmer.total_earning = ",".join([int_part[:-3][max(i-2, 0):i] for i in range(len(int_part[:-3]), 0, -2)][::-1]) + "," + int_part[-3:] + "." + parts[1]
        else:
            farmer.total_earning = inflow_str

        # Attach taluks for preloading in edit modal
        from apps.superadmin.models import Taluk
        if farmer.district_id:
            farmer.taluks = list(Taluk.objects.filter(city_id=farmer.district_id).order_by('name').values('id', 'name'))
        else:
            farmer.taluks = []

        # Attach subscription details (expiry status, days left, reminder flag)
        from apps.superadmin.subscription_utils import get_farmer_subscription_details
        farmer.sub_details = get_farmer_subscription_details(farmer)

    return render(request, "superadmin/farmers/manage_farmers.html", {
        "states": states,
        "farmers": farmers,
        "unique_districts": unique_districts
    })


from apps.farmer.models import (
    Farm,
    FarmerCustomer,
    FarmerVendor,
    LedgerTransaction,
    StaffAdvance,
    StaffAttendance,
    StaffPayroll,
)


def reports_dashboard(request):
    import re
    from django.db.models import Sum

    farmers_qs = Profile.objects.filter(role='farmer').select_related('user').order_by('user__first_name', 'user__username')
    farmers_count = farmers_qs.count()

    customers_qs = FarmerCustomer.objects.select_related('farmer', 'farmer__user').order_by('-id')
    customers_count = customers_qs.count()
    recent_customers = list(customers_qs[:4])

    farms_qs = Farm.objects.select_related('farmer', 'farmer__user').order_by('-id')
    farms_count = farms_qs.count()
    recent_farms = list(farms_qs[:4])
    total_area = 0.0
    for f in farms_qs:
        if f.total_area:
            try:
                m = re.search(r'[\d\.]+', str(f.total_area))
                if m:
                    total_area += float(m.group())
            except (ValueError, TypeError):
                pass

    expenses_qs = LedgerTransaction.objects.filter(amount__lt=0).select_related('farmer', 'farmer__user', 'farm').order_by('-close_date', '-id')
    expenses_count = expenses_qs.count()
    recent_expenses = list(expenses_qs[:4])
    total_expense_amount = abs(float(expenses_qs.aggregate(Sum('amount'))['amount__sum'] or 0))

    vendors_qs = FarmerVendor.objects.select_related('farmer', 'farmer__user').order_by('-id')
    vendors_count = vendors_qs.count()
    recent_vendors = list(vendors_qs[:4])

    payroll_qs = StaffPayroll.objects.select_related('staff', 'staff__farmer', 'staff__farmer__user').order_by('-id')
    payroll_count = payroll_qs.count()
    recent_payrolls = list(payroll_qs[:4])
    total_salary_amount = float(payroll_qs.aggregate(Sum('net_pay'))['net_pay__sum'] or 0)

    attendance_qs = StaffAttendance.objects.select_related('staff', 'staff__farmer', 'staff__farmer__user').order_by('-date', '-id')
    attendance_count = attendance_qs.count()
    recent_attendances = list(attendance_qs[:4])

    advance_qs = StaffAdvance.objects.select_related('staff', 'staff__farmer', 'staff__farmer__user').order_by('-date', '-id')
    advance_count = advance_qs.count()
    recent_advances = list(advance_qs[:4])
    total_advance_amount = float(advance_qs.aggregate(Sum('amount'))['amount__sum'] or 0)

    context = {
        "farmers": farmers_qs,
        "farmers_count": farmers_count,
        "customers_count": customers_count,
        "farms_count": farms_count,
        "total_area": round(float(total_area), 2),
        "expenses_count": expenses_count,
        "total_expense_amount": round(float(total_expense_amount), 2),
        "vendors_count": vendors_count,
        "payroll_count": payroll_count,
        "total_salary_amount": round(float(total_salary_amount), 2),
        "attendance_count": attendance_count,
        "advance_count": advance_count,
        "total_advance_amount": round(float(total_advance_amount), 2),
        "recent_customers": recent_customers,
        "recent_farms": recent_farms,
        "recent_expenses": recent_expenses,
        "recent_vendors": recent_vendors,
        "recent_payrolls": recent_payrolls,
        "recent_attendances": recent_attendances,
        "recent_advances": recent_advances,
    }
    return render(request, "superadmin/farmers/reports/reports_dashboard.html", context)


def customer_report(request):
    farmers = Profile.objects.filter(role='farmer').select_related('user').order_by('user__first_name', 'user__username')
    records = FarmerCustomer.objects.select_related('farmer', 'farmer__user', 'customer_type').order_by('-id')
    
    total_customers = records.count()
    active_customers = records.filter(status='Active').count()
    inactive_customers = records.filter(status='Inactive').count()
    unique_companies = records.exclude(company_name__isnull=True).exclude(company_name='').values('company_name').distinct().count()

    context = {
        "records": records,
        "farmers": farmers,
        "total_customers": total_customers,
        "active_customers": active_customers,
        "inactive_customers": inactive_customers,
        "unique_companies": unique_companies,
    }
    return render(request, "superadmin/farmers/reports/customer_report.html", context)


def land_report(request):
    import re
    farmers = Profile.objects.filter(role='farmer').select_related('user').order_by('user__first_name', 'user__username')
    records = Farm.objects.select_related('farmer', 'farmer__user', 'crop_type', 'district').order_by('-id')

    total_farms = records.count()
    total_acres = 0.0
    for f in records:
        if f.total_area:
            try:
                m = re.search(r'[\d\.]+', str(f.total_area))
                if m:
                    total_acres += float(m.group())
            except (ValueError, TypeError):
                pass

    drip_farms = records.filter(irrigation_type__icontains='drip').count()
    avg_farm_size = round(total_acres / total_farms, 2) if total_farms > 0 else 0

    context = {
        "records": records,
        "farmers": farmers,
        "total_farms": total_farms,
        "total_acres": round(total_acres, 2),
        "drip_farms": drip_farms,
        "avg_farm_size": avg_farm_size,
    }
    return render(request, "superadmin/farmers/reports/land_report.html", context)


def expense_report(request):
    from django.db.models import Sum
    farmers = Profile.objects.filter(role='farmer').select_related('user').order_by('user__first_name', 'user__username')
    records = LedgerTransaction.objects.filter(amount__lt=0).select_related('farmer', 'farmer__user', 'farm').order_by('-close_date', '-id')

    total_expenses_count = records.count()
    total_expense_val = abs(float(records.aggregate(Sum('amount'))['amount__sum'] or 0))
    avg_expense_val = round(total_expense_val / total_expenses_count, 2) if total_expenses_count > 0 else 0
    categories_count = records.values('category').distinct().count()

    context = {
        "records": records,
        "farmers": farmers,
        "total_expenses_count": total_expenses_count,
        "total_expense_val": round(total_expense_val, 2),
        "avg_expense_val": avg_expense_val,
        "categories_count": categories_count,
    }
    return render(request, "superadmin/farmers/reports/expense_report.html", context)


def vendor_report(request):
    farmers = Profile.objects.filter(role='farmer').select_related('user').order_by('user__first_name', 'user__username')
    records = FarmerVendor.objects.select_related('farmer', 'farmer__user').order_by('-id')

    total_vendors = records.count()
    total_with_gst = records.exclude(gst_number__isnull=True).exclude(gst_number='').count()
    unique_districts = records.exclude(district__isnull=True).exclude(district='').values('district').distinct().count()
    shops_count = records.exclude(shop_name__isnull=True).exclude(shop_name='').count()

    context = {
        "records": records,
        "farmers": farmers,
        "total_vendors": total_vendors,
        "total_with_gst": total_with_gst,
        "unique_districts": unique_districts,
        "shops_count": shops_count,
    }
    return render(request, "superadmin/farmers/reports/vendor_report.html", context)


def salary_report(request):
    from django.db.models import Sum
    farmers = Profile.objects.filter(role='farmer').select_related('user').order_by('user__first_name', 'user__username')
    records = StaffPayroll.objects.select_related('staff', 'staff__farmer', 'staff__farmer__user', 'staff__designation').order_by('-id')

    total_payrolls = records.count()
    total_salary_val = float(records.aggregate(Sum('net_pay'))['net_pay__sum'] or 0)
    avg_salary_val = round(total_salary_val / total_payrolls, 2) if total_payrolls > 0 else 0
    total_staff_paid = records.values('staff').distinct().count()

    context = {
        "records": records,
        "farmers": farmers,
        "total_payrolls": total_payrolls,
        "total_salary_val": round(total_salary_val, 2),
        "avg_salary_val": avg_salary_val,
        "total_staff_paid": total_staff_paid,
    }
    return render(request, "superadmin/farmers/reports/salary_report.html", context)


def attendance_report(request):
    from apps.farmer.models import StaffAttendance, FarmerStaff
    farmers = Profile.objects.filter(role='farmer').select_related('user').order_by('user__first_name', 'user__username')
    staff_list = FarmerStaff.objects.select_related('farmer', 'farmer__user', 'designation').all().order_by('name')
    records = StaffAttendance.objects.all().select_related('staff', 'staff__designation', 'staff__farmer', 'staff__farmer__user').order_by('-date', '-id')

    staff_summary = []
    total_present_count = 0
    total_absent_count = 0
    total_halfday_count = 0
    total_logs_count = records.count()

    for s in staff_list:
        s_records = records.filter(staff=s)
        total_days = s_records.count()
        present = s_records.filter(status='P').count()
        absent = s_records.filter(status='A').count()
        half_day = s_records.filter(status='H').count()

        payable_days = float(present) + (0.5 * float(half_day))
        att_percentage = round((payable_days / total_days * 100), 1) if total_days > 0 else 0

        total_present_count += present
        total_absent_count += absent
        total_halfday_count += half_day

        staff_summary.append({
            'staff': s,
            'total_days': total_days,
            'present': present,
            'absent': absent,
            'half_day': half_day,
            'payable_days': f"{payable_days:g}",
            'attendance_pct': att_percentage
        })

    overall_rate = round(((total_present_count + 0.5 * total_halfday_count) / total_logs_count * 100), 1) if total_logs_count > 0 else 0

    context = {
        "records": records,
        "farmers": farmers,
        "staff_summary": staff_summary,
        "staff_list": staff_list,
        "total_staff": staff_list.count(),
        "total_present": total_present_count,
        "total_absent": total_absent_count,
        "total_halfday": total_halfday_count,
        "overall_rate": overall_rate,
    }
    return render(request, "superadmin/farmers/reports/attendance_report.html", context)


def advance_report(request):
    from django.db.models import Sum
    farmers = Profile.objects.filter(role='farmer').select_related('user').order_by('user__first_name', 'user__username')
    records = StaffAdvance.objects.select_related('staff', 'staff__farmer', 'staff__farmer__user', 'staff__designation').order_by('-date', '-id')

    total_advances = records.count()
    total_advance_val = float(records.aggregate(Sum('amount'))['amount__sum'] or 0)
    avg_advance_val = round(total_advance_val / total_advances, 2) if total_advances > 0 else 0
    staff_adv_count = records.values('staff').distinct().count()

    context = {
        "records": records,
        "farmers": farmers,
        "total_advances": total_advances,
        "total_advance_val": round(total_advance_val, 2),
        "avg_advance_val": avg_advance_val,
        "staff_adv_count": staff_adv_count,
    }
    return render(request, "superadmin/farmers/reports/advance_report.html", context)


# -------------------------
# PACKAGE MANAGEMENT
# -------------------------
def manage_package(request):
    import re
    def clean_limit_str(val):
        if val is None or not str(val).strip():
            return "Unlimited"
        s = str(val).strip()
        if s.lower() == "unlimited":
            return "Unlimited"
        m = re.search(r'[\d\.]+', s)
        return m.group() if m else "Unlimited"

    def clean_int_limit_str(val):
        if val is None or not str(val).strip():
            return "Unlimited"
        s = str(val).strip()
        if s.lower() == "unlimited":
            return "Unlimited"
        m = re.search(r'\d+', s)
        return m.group() if m else "Unlimited"

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create":
            name = request.POST.get("name")
            original_price = request.POST.get("original_price")
            try:
                orig_price_val = float(original_price) if original_price else None
            except (ValueError, TypeError):
                orig_price_val = None

            price = request.POST.get("price") or 0.0
            try:
                base_price = float(price)
            except (ValueError, TypeError):
                base_price = 0.0

            if not orig_price_val or orig_price_val < base_price:
                orig_price_val = base_price

            price_quarterly = round(base_price * 3, 2)
            price_yearly = round(base_price * 12, 2)

            max_acre_support = clean_limit_str(request.POST.get("max_acre_support"))
            description = request.POST.get("description")
            discount = request.POST.get("discount", "").strip()
            show_badge = request.POST.get("show_badge") in ["on", "true", "1", True]
            badge_text = request.POST.get("badge_text", "POPULAR").strip()
            if not badge_text:
                badge_text = "POPULAR"
            my_land = clean_int_limit_str(request.POST.get("my_land"))
            my_crop = clean_int_limit_str(request.POST.get("my_crop"))
            my_users = clean_int_limit_str(request.POST.get("my_users"))
            modules = request.POST.getlist("modules")
            features = request.POST.getlist("featureList[]")

            Package.objects.create(
                name=name,
                original_price=orig_price_val,
                price=base_price,
                price_quarterly=price_quarterly,
                price_yearly=price_yearly,
                max_acre_support=max_acre_support,
                description=description,
                discount=discount,
                show_badge=show_badge,
                badge_text=badge_text,
                my_land=my_land,
                my_crop=my_crop,
                my_users=my_users,
                modules=modules,
                features=features
            )
            messages.success(request, "Package created successfully!")
            return redirect('manage_package')

        elif action == "edit":
            pkg_id = request.POST.get("package_id")
            if pkg_id:
                pkg = Package.objects.get(id=pkg_id)
                pkg.name = request.POST.get("name")
                original_price = request.POST.get("original_price")
                try:
                    pkg.original_price = float(original_price) if original_price else None
                except (ValueError, TypeError):
                    pkg.original_price = None

                pkg.price = request.POST.get("price") or 0.0
                try:
                    base_price = float(pkg.price)
                except (ValueError, TypeError):
                    base_price = 0.0
                pkg.price = base_price
                if not pkg.original_price or float(pkg.original_price) < base_price:
                    pkg.original_price = base_price

                pkg.price_quarterly = round(base_price * 3, 2)
                pkg.price_yearly = round(base_price * 12, 2)
                pkg.max_acre_support = clean_limit_str(request.POST.get("max_acre_support"))
                pkg.description = request.POST.get("description")
                pkg.discount = request.POST.get("discount", "").strip()
                pkg.show_badge = request.POST.get("show_badge") in ["on", "true", "1", True]
                b_text = request.POST.get("badge_text", "").strip()
                pkg.badge_text = b_text if b_text else "POPULAR"
                pkg.my_land = clean_int_limit_str(request.POST.get("my_land"))
                pkg.my_crop = clean_int_limit_str(request.POST.get("my_crop"))
                pkg.my_users = clean_int_limit_str(request.POST.get("my_users"))
                pkg.modules = request.POST.getlist("modules")

                # Check for features list logic depending on which form was submitted
                # The edit modal uses featureMainList[]
                features = request.POST.getlist("featureMainList[]")
                if features:
                    pkg.features = features
                else:
                    pkg.features = [] # if none sent, clear it

                pkg.save()
                messages.success(request, "Package updated successfully!")
            return redirect('manage_package')

        elif action == "delete":
            pkg_id = request.POST.get("package_id")
            if pkg_id:
                Package.objects.filter(id=pkg_id).delete()
                messages.success(request, "Package deleted successfully!")
            return redirect('manage_package')

    packages = Package.objects.all().order_by('-created_at')

    eco_modules = [
        {'icon': 'ri-dashboard-line',          'name': 'Dashboards',              'active': True},
        {'icon': 'ri-plant-line',              'name': 'Farms',                   'active': True},
        {'icon': 'ri-store-2-line',            'name': 'Market & Resources Hub',  'active': True},
        {'icon': 'ri-task-line',               'name': 'Task',                    'active': True},
        {'icon': 'ri-money-dollar-circle-line','name': 'Sales',                   'active': True},
        {'icon': 'ri-archive-line',            'name': 'Inventory',               'active': True},
        {'icon': 'ri-wallet-3-line',           'name': 'Expenses',                'active': True},
        {'icon': 'ri-team-line',               'name': 'Employee',                'active': True},
        {'icon': 'ri-file-chart-line',         'name': 'Reports',                 'active': True},
    ]

    mega_static = [
        'Dashboard', 'Dashboards', 'Farms', 'Market & Resources Hub', 'Task', 'Task Management',
        'Sales', 'Inventory', 'Expenses', 'Employee', 'Reports',
    ]

    from django.db.models import Sum

    from auth.models import Profile

    farmers = Profile.objects.filter(role='farmer').select_related('user')
    # Fetch only the latest subscription per farmer for the main list
    latest_subscriptions = []
    for f in farmers:
        sub = Subscription.objects.filter(farmer=f).order_by('-id').first()
        if sub:
            latest_subscriptions.append(sub)

    # Sort by creation date descending
    latest_subscriptions.sort(key=lambda x: x.created_at, reverse=True)

    # Keep the original queryset for aggregations (revenue, pending, etc.)
    all_subscriptions = Subscription.objects.all()
    subscriptions = latest_subscriptions

    total_revenue = all_subscriptions.filter(payment_status='paid').aggregate(total=Sum('monthly_amount'))['total'] or 0
    total_pending = all_subscriptions.exclude(payment_status='paid').aggregate(total=Sum('monthly_amount'))['total'] or 0
    active_accounts = all_subscriptions.filter(subscription_status='active').count()

    context = {
        'packages': packages,
        'eco_modules': eco_modules,
        'mega_static': mega_static,
        'farmers': farmers,
        'subscriptions': subscriptions,
        'total_revenue': total_revenue,
        'total_pending': total_pending,
        'active_accounts': active_accounts,
    }
    return render(request, 'superadmin/packages/manage_packages.html', context)

def manage_package_revenue(request):
    return render(request, "superadmin/packages/revenue_report.html")

def toggle_package_status(request):
    if request.method == "POST":
        package_id = request.POST.get("package_id")
        cycle = (request.POST.get("cycle") or "monthly").lower().strip()
        is_active = request.POST.get("is_active") in ["true", "1", True]

        pkg = Package.objects.filter(id=package_id).first()
        if not pkg:
            return JsonResponse({"status": "error", "message": "Package not found."}, status=404)

        if cycle == "quarterly":
            pkg.is_active_quarterly = is_active
        elif cycle in ["yearly", "annual"]:
            pkg.is_active_yearly = is_active
        elif cycle == "master":
            pkg.is_active = is_active
        else:
            pkg.is_active_monthly = is_active

        if not pkg.is_active_monthly and not pkg.is_active_quarterly and not pkg.is_active_yearly:
            pkg.is_active = False
        else:
            pkg.is_active = True

        pkg.save()

        cycle_label = "Quarterly" if cycle == "quarterly" else ("Annual" if cycle in ["yearly", "annual"] else "Monthly")
        status_label = "Active" if is_active else "Inactive"

        return JsonResponse({
            "status": "success",
            "message": f"{pkg.name} is now {status_label} for {cycle_label} billing cycle.",
            "is_active_monthly": pkg.is_active_monthly,
            "is_active_quarterly": pkg.is_active_quarterly,
            "is_active_yearly": pkg.is_active_yearly,
            "is_active": pkg.is_active
        })

    return JsonResponse({"status": "error", "message": "Invalid request method."}, status=405)


# -------------------------
# SUBSCRIPTION
# -------------------------
def manage_subscription(request):
    from datetime import timedelta
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            farmer_id = request.POST.get("farmer_id")
            package_id = request.POST.get("package_id")
            auto_renew = request.POST.get("auto_renew") == "on"
            payment_status = request.POST.get("payment_status")
            subscription_status = request.POST.get("subscription_status")
            billing_cycle = request.POST.get("billing_cycle", "monthly").lower()

            if farmer_id and package_id:
                farmer = Profile.objects.get(id=farmer_id)
                package = Package.objects.get(id=package_id)

                def parse_date_flexible(val):
                    if not val:
                        return None
                    val = str(val).strip()
                    from datetime import datetime
                    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%Y/%m/%d'):
                        try:
                            return datetime.strptime(val, fmt).date()
                        except ValueError:
                            pass
                    from django.utils.dateparse import parse_date
                    return parse_date(val)

                req_start = request.POST.get("start_date")
                req_end = request.POST.get("end_date")

                start_date = parse_date_flexible(req_start)
                if not start_date:
                    start_date = timezone.now().date()

                end_date = parse_date_flexible(req_end)
                if not end_date:
                    if billing_cycle == 'yearly':
                        end_date = start_date + timedelta(days=365)
                    elif billing_cycle == 'quarterly':
                        end_date = start_date + timedelta(days=90)
                    else:
                        end_date = start_date + timedelta(days=30)

                monthly_amt = request.POST.get("monthly_amount")
                try:
                    if monthly_amt:
                        monthly_amt = float(monthly_amt)
                    elif billing_cycle == 'yearly' and package.price_yearly:
                        monthly_amt = float(package.price_yearly)
                    elif billing_cycle == 'quarterly' and package.price_quarterly:
                        monthly_amt = float(package.price_quarterly)
                    else:
                        monthly_amt = float(package.price)
                except (ValueError, TypeError):
                    monthly_amt = float(package.price)

                if subscription_status == "active":
                    Subscription.objects.filter(farmer=farmer, subscription_status='active').update(subscription_status='cancelled')

                new_sub = Subscription.objects.create(
                    farmer=farmer,
                    package=package,
                    monthly_amount=monthly_amt,
                    billing_cycle=billing_cycle,
                    auto_renew=auto_renew,
                    payment_status=payment_status,
                    subscription_status=subscription_status,
                    start_date=start_date,
                    end_date=end_date
                )
                try:
                    if farmer.email:
                        from apps.superadmin.email_utils import send_package_purchase_email
                        send_package_purchase_email(new_sub, is_upgrade=False, amount=monthly_amt, request=request)
                except Exception as e:
                    print(f"[Email Trigger Error - Package Purchase]: {e}")
                messages.success(request, "Subscription created successfully!")
            referer = request.META.get('HTTP_REFERER')
            return redirect(referer if referer else 'manage_subscription')

        elif action == "update_auto_renew":
            sub_id = request.POST.get("subscription_id")
            auto_renew = request.POST.get("auto_renew") == "true"
            if sub_id:
                Subscription.objects.filter(id=sub_id).update(auto_renew=auto_renew)
                messages.success(request, "Auto renewal updated!")
            referer = request.META.get('HTTP_REFERER')
            return redirect(referer if referer else 'manage_subscription')

        elif action == "update_payment_status":
            sub_id = request.POST.get("subscription_id")
            payment_status = request.POST.get("payment_status")
            if sub_id and payment_status:
                Subscription.objects.filter(id=sub_id).update(payment_status=payment_status)
                messages.success(request, f"Payment status updated to {payment_status}!")
            referer = request.META.get('HTTP_REFERER')
            return redirect(referer if referer else 'manage_subscription')

        elif action == "delete":
            sub_id = request.POST.get("subscription_id")
            if sub_id:
                Subscription.objects.filter(id=sub_id).delete()
                messages.success(request, "Subscription deleted!")
            referer = request.META.get('HTTP_REFERER')
            return redirect(referer if referer else 'manage_subscription')

    farmers = Profile.objects.filter(role='farmer').select_related('user')
    packages = Package.objects.all()
    subscriptions = Subscription.objects.all().order_by('-created_at')

    total_revenue = subscriptions.filter(payment_status='paid').aggregate(total=Sum('monthly_amount'))['total'] or 0
    total_pending = subscriptions.exclude(payment_status='paid').aggregate(total=Sum('monthly_amount'))['total'] or 0
    active_accounts = subscriptions.filter(subscription_status='active').count()

    return render(request, "superadmin/subscription/manage_subscription.html", {
        "farmers": farmers,
        "packages": packages,
        "subscriptions": subscriptions,
        "total_revenue": total_revenue,
        "total_pending": total_pending,
        "active_accounts": active_accounts,
    })


# -------------------------
# MARKET
# -------------------------
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
def quick_price_update(request):
    """AJAX endpoint to update a single crop price from hub buyer card."""
    import json

    from django.http import JsonResponse

    from apps.superadmin.models import DepotTerminal

    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
        terminal_id = data.get("terminal_id")
        crop_name = data.get("crop_name")
        new_price = float(data.get("new_price", 0))

        terminal = DepotTerminal.objects.get(id=terminal_id)
        crops = list(terminal.available_crops or [])

        updated = False
        for c in crops:
            if isinstance(c, dict) and c.get("name") == crop_name:
                old_price_raw = c.get("price", 0)
                try:
                    old_price = float(old_price_raw)
                except (TypeError, ValueError):
                    old_price = 0
                c["prev_price"] = str(int(old_price)) if old_price == int(old_price) else str(old_price)
                c["price"] = str(int(new_price)) if new_price == int(new_price) else str(new_price)

                # Maintain a rolling price history (max 10 points)
                history = c.get("price_history", [])
                if not history:
                    history = [old_price]
                history.append(new_price)
                if len(history) > 10:
                    history = history[-10:]
                c["price_history"] = history

                updated = True
                break

        if not updated:
            return JsonResponse({"success": False, "error": f"Crop '{crop_name}' not found in terminal."}, status=404)

        terminal.available_crops = crops
        terminal.save(update_fields=["available_crops"])

        # Synchronize with DepotCropPricing and DailyPriceHistory
        try:
            from apps.superadmin.models import MarketPriceIndex, DepotCropPricing, DailyPriceHistory
            from django.utils import timezone
            c_index = MarketPriceIndex.objects.filter(crop_name__iexact=crop_name).first()
            if not c_index:
                c_index = MarketPriceIndex.objects.create(crop_name=crop_name, base_price=new_price, unit="Kg")
            DepotCropPricing.objects.update_or_create(
                depot=terminal,
                crop_index=c_index,
                defaults={'terminal_rate': new_price}
            )
            DailyPriceHistory.objects.update_or_create(
                depot=terminal,
                crop_index=c_index,
                date=timezone.localdate(),
                defaults={'price': new_price}
            )
        except Exception:
            pass

        return JsonResponse({"success": True, "new_price": new_price})
    except Exception as e:
        import traceback
        return JsonResponse({"success": False, "error": str(e), "trace": traceback.format_exc()}, status=400)

@csrf_exempt
def ajax_quick_add_crop(request):
    import json

    from apps.superadmin.models import Crop, CropType
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            crop_name = data.get("crop_name", "").strip()
            crop_type_id = data.get("crop_type_id")

            if not crop_name:
                return JsonResponse({"success": False, "error": "Crop name is required."})
            crop_type = None
            if crop_type_id:
                crop_type = CropType.objects.filter(id=crop_type_id).first()
            if not crop_type:
                crop_type = CropType.objects.first()

            crop, created = get_or_reactivate_master(
                Crop,
                name=crop_name,
                defaults={"crop_type": crop_type, "is_active": True},
            )
            if crop_type and crop.crop_type_id != crop_type.id:
                crop.crop_type = crop_type
                crop.save(update_fields=["crop_type"])
            return JsonResponse({"success": True, "crop": {"id": crop.id, "name": crop.name}})
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})
    return JsonResponse({"success": False, "error": "Invalid request method."})

def manage_market(request):
    import json

    from apps.superadmin.models import DepotTerminal

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create":
            name = request.POST.get("name")
            location = request.POST.get("address")
            distance = request.POST.get("distance")
            district = request.POST.get("district")
            demand_level = request.POST.get("demand")
            contact_person = request.POST.get("manager")
            contact_phone = request.POST.get("phone")

            crop_names = request.POST.getlist("crop_name[]")
            crop_prices = request.POST.getlist("crop_price[]")
            crop_units = request.POST.getlist("crop_unit[]")

            available_crops = []
            for i in range(len(crop_names)):
                c_name = crop_names[i].strip()
                if not c_name:  # skip blank rows
                    continue
                c_price = crop_prices[i].strip() if i < len(crop_prices) else "0"
                if not c_price:
                    c_price = "0"
                c_unit = crop_units[i].strip() if i < len(crop_units) else "Quintal"
                if not c_unit:
                    c_unit = "Quintal"
                available_crops.append({
                    "name": c_name,
                    "price": c_price,
                    "prev_price": c_price,
                    "unit": c_unit
                })

            custom_crops_str = request.POST.get("custom_crops_json", "[]")
            try:
                custom_crops = json.loads(custom_crops_str)
            except:
                custom_crops = []

            terminal = DepotTerminal.objects.create(
                name=name,
                location=location,
                distance_km=distance,
                district=district,
                demand_level=demand_level,
                contact_person=contact_person,
                contact_phone=contact_phone,
                available_crops=available_crops,
                custom_crops=custom_crops
            )
            try:
                from apps.superadmin.models import MarketPriceIndex, DepotCropPricing, DailyPriceHistory
                from django.utils import timezone
                today_d = timezone.localdate()
                for c_item in available_crops:
                    if isinstance(c_item, dict) and c_item.get('name'):
                        c_n = c_item.get('name')
                        c_p = float(c_item.get('price', 0) or 0)
                        if c_p > 0:
                            c_idx = MarketPriceIndex.objects.filter(crop_name__iexact=c_n).first()
                            if not c_idx:
                                c_idx = MarketPriceIndex.objects.create(crop_name=c_n, base_price=c_p, unit=c_item.get('unit', 'Kg'))
                            DepotCropPricing.objects.update_or_create(
                                depot=terminal,
                                crop_index=c_idx,
                                defaults={'terminal_rate': c_p}
                            )
                            DailyPriceHistory.objects.update_or_create(
                                depot=terminal,
                                crop_index=c_idx,
                                date=today_d,
                                defaults={'price': c_p}
                            )
            except Exception:
                pass
            messages.success(request, "Hub Buyer created successfully!")
            return redirect('manage_market')

        elif action == "update":
            terminal_id = request.POST.get("terminal_id")
            if terminal_id:
                terminal = DepotTerminal.objects.get(id=terminal_id)
                terminal.name = request.POST.get("name")
                terminal.location = request.POST.get("address")
                terminal.distance_km = request.POST.get("distance")
                terminal.district = request.POST.get("district")
                terminal.demand_level = request.POST.get("demand")
                terminal.contact_person = request.POST.get("manager")
                terminal.contact_phone = request.POST.get("phone")

                crop_names = request.POST.getlist("crop_name[]")
                crop_prices = request.POST.getlist("crop_price[]")
                crop_units = request.POST.getlist("crop_unit[]")

                existing_crops = {}
                if terminal.available_crops:
                    for c in terminal.available_crops:
                        if isinstance(c, dict):
                            existing_crops[c.get('name')] = c
                        else:
                            existing_crops[c] = {'name': c, 'price': 0, 'prev_price': 0, 'unit': 'Quintal'}

                available_crops = []
                for i in range(len(crop_names)):
                    c_name = crop_names[i].strip()
                    if not c_name:  # skip blank rows
                        continue
                    c_price = crop_prices[i].strip() if i < len(crop_prices) else "0"
                    if not c_price:
                        c_price = "0"
                    c_unit = crop_units[i].strip() if i < len(crop_units) else "Quintal"
                    if not c_unit:
                        c_unit = "Quintal"

                    prev_price = c_price
                    if c_name in existing_crops:
                        old_price = existing_crops[c_name].get('price', c_price)
                        if str(old_price) != str(c_price):
                            prev_price = old_price
                        else:
                            prev_price = existing_crops[c_name].get('prev_price', old_price)
                        # preserve price history

                    hist = []
                    if c_name in existing_crops:
                        hist = existing_crops[c_name].get('price_history', [])

                    available_crops.append({
                        "name": c_name,
                        "price": c_price,
                        "prev_price": prev_price,
                        "unit": c_unit,
                        "price_history": hist
                    })

                terminal.available_crops = available_crops

                custom_crops_str = request.POST.get("custom_crops_json", "[]")
                try:
                    terminal.custom_crops = json.loads(custom_crops_str)
                except:
                    terminal.custom_crops = []

                terminal.save()
                try:
                    from apps.superadmin.models import MarketPriceIndex, DepotCropPricing, DailyPriceHistory
                    from django.utils import timezone
                    today_d = timezone.localdate()
                    for c_item in available_crops:
                        if isinstance(c_item, dict) and c_item.get('name'):
                            c_n = c_item.get('name')
                            c_p = float(c_item.get('price', 0) or 0)
                            if c_p > 0:
                                c_idx = MarketPriceIndex.objects.filter(crop_name__iexact=c_n).first()
                                if not c_idx:
                                    c_idx = MarketPriceIndex.objects.create(crop_name=c_n, base_price=c_p, unit=c_item.get('unit', 'Kg'))
                                DepotCropPricing.objects.update_or_create(
                                    depot=terminal,
                                    crop_index=c_idx,
                                    defaults={'terminal_rate': c_p}
                                )
                                DailyPriceHistory.objects.update_or_create(
                                    depot=terminal,
                                    crop_index=c_idx,
                                    date=today_d,
                                    defaults={'price': c_p}
                                )
                except Exception:
                    pass
                messages.success(request, "Hub Buyer updated successfully!")
            return redirect('manage_market')

        elif action == "delete":
            terminal_id = request.POST.get("terminal_id")
            if terminal_id:
                DepotTerminal.objects.filter(id=terminal_id).delete()
                messages.success(request, "Hub Buyer deleted successfully!")
            return redirect('manage_market')

    terminals = DepotTerminal.objects.all().order_by('-id')
    from apps.superadmin.models import City, Crop, CropType, ExpenseUnit
    all_crops = Crop.objects.filter(is_active=True)
    crop_types = CropType.objects.filter(is_active=True)
    cities = City.objects.filter(state__name__icontains='Tamil Nadu').order_by('name')
    units = ExpenseUnit.objects.filter(is_active=True).order_by('name')

    return render(request, "superadmin/market/manage_market.html", {
        "terminals": terminals,
        "all_crops": all_crops,
        "crop_types": crop_types,
        "cities": cities,
        "units": units
    })


def manage_market_price(request):
    from apps.superadmin.models import DepotCropPricing, DepotTerminal, MarketPriceIndex

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create_market_price":
            crop_name = request.POST.get("crop_name")
            base_price = request.POST.get("base_price") or 0.0
            if crop_name:
                crop, created = MarketPriceIndex.objects.get_or_create(
                    crop_name=crop_name,
                    defaults={
                        'base_price': base_price,
                        'unit': "Ton",
                        'trend_percentage': 0.0,
                        'trend_direction': "up"
                    }
                )

                terminal_name = request.POST.get("terminal_name")
                location = request.POST.get("location")
                distance = request.POST.get("distance") or 0
                demand_level = request.POST.get("demand_level")
                contact_person = request.POST.get("contact_person")
                contact_phone = request.POST.get("contact_phone")
                terminal_rate = request.POST.get("terminal_rate")
                if not terminal_rate:
                    terminal_rate = base_price

                if terminal_name and location:
                    terminal = DepotTerminal.objects.create(
                        name=terminal_name,
                        location=location,
                        distance_km=distance,
                        demand_level=demand_level,
                        contact_person=contact_person,
                        contact_phone=contact_phone,
                        sourcing_active=True
                    )
                    DepotCropPricing.objects.create(
                        depot=terminal,
                        crop_index=crop,
                        terminal_rate=terminal_rate,
                        markup_offset=0
                    )

        elif action == "register_terminal":
            crop_id = request.POST.get("crop_id")
            terminal_name = request.POST.get("terminal_name")
            location = request.POST.get("location")
            distance = request.POST.get("distance") or 0
            demand_level = request.POST.get("demand_level")
            base_price = request.POST.get("base_price") or 0
            contact_person = request.POST.get("contact_person")
            contact_phone = request.POST.get("contact_phone")

            terminal = DepotTerminal.objects.create(
                name=terminal_name,
                location=location,
                distance_km=distance,
                demand_level=demand_level,
                contact_person=contact_person,
                contact_phone=contact_phone,
                sourcing_active=True
            )

            if crop_id:
                try:
                    crop = MarketPriceIndex.objects.get(id=crop_id)
                    DepotCropPricing.objects.create(
                        depot=terminal,
                        crop_index=crop,
                        terminal_rate=base_price,
                        markup_offset=0
                    )
                except MarketPriceIndex.DoesNotExist:
                    pass

        elif action == "update_terminal":
            depot_id = request.POST.get("depot_id")
            crop_id = request.POST.get("crop_id")
            terminal_rate = request.POST.get("terminal_rate") or 0
            markup_offset = request.POST.get("markup_offset") or 0
            demand_level = request.POST.get("demand_level")
            sourcing_active = request.POST.get("sourcing_active") == "on"

            if depot_id:
                try:
                    depot = DepotTerminal.objects.get(id=depot_id)
                    depot.name = request.POST.get("terminal_name") or depot.name
                    depot.location = request.POST.get("location") or depot.location
                    if request.POST.get("distance"):
                        depot.distance_km = request.POST.get("distance")
                    if request.POST.get("contact_person"):
                        depot.contact_person = request.POST.get("contact_person")
                    if request.POST.get("contact_phone"):
                        depot.contact_phone = request.POST.get("contact_phone")
                    depot.demand_level = demand_level
                    depot.sourcing_active = sourcing_active
                    depot.save()

                    if crop_id:
                        crop = MarketPriceIndex.objects.get(id=crop_id)
                        if request.POST.get("crop_name"):
                            crop.crop_name = request.POST.get("crop_name")
                        if request.POST.get("base_price"):
                            crop.base_price = request.POST.get("base_price")
                        crop.save()

                        pricing, created = DepotCropPricing.objects.get_or_create(
                            depot=depot,
                            crop_index=crop,
                            defaults={'terminal_rate': terminal_rate, 'markup_offset': markup_offset}
                        )
                        if not created:
                            pricing.terminal_rate = terminal_rate
                            pricing.markup_offset = markup_offset
                            pricing.save()
                except (DepotTerminal.DoesNotExist, MarketPriceIndex.DoesNotExist):
                    pass

        elif action == "delete_terminal":
            pricing_id = request.POST.get("pricing_id")
            if pricing_id:
                try:
                    pricing = DepotCropPricing.objects.get(id=pricing_id)
                    depot = pricing.depot
                    pricing.delete()
                    depot.delete()
                except DepotCropPricing.DoesNotExist:
                    pass

        elif action == "toggle_trading":
            pricing_id = request.POST.get("pricing_id")
            if pricing_id:
                try:
                    pricing = DepotCropPricing.objects.get(id=pricing_id)
                    pricing.trading_restricted = not pricing.trading_restricted
                    pricing.save()
                except DepotCropPricing.DoesNotExist:
                    pass

        return redirect('manage_market_price')

    crops = MarketPriceIndex.objects.all()
    terminals = DepotTerminal.objects.all()
    pricings = DepotCropPricing.objects.select_related('depot', 'crop_index').all().order_by('-id')

    return render(request, "superadmin/market/market_price.html", {
        "crops": crops,
        "terminals": terminals,
        "pricings": pricings,
    })


from datetime import date, datetime


def get_price_history_ajax(request):
    from apps.superadmin.models import DepotTerminal
    crop_name = request.GET.get('crop_name')
    terminal_name = request.GET.get('terminal_name')
    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')

    terminals = DepotTerminal.objects.all()
    if terminal_name and terminal_name != 'All':
        terminals = terminals.filter(name=terminal_name)

    prices_matrix = []
    for term in terminals:
        crops = term.available_crops if term.available_crops else []
        for c in crops:
            if not crop_name or crop_name == 'All' or str(c.get('name', '')).lower() == str(crop_name).lower():
                ph = c.get('price_history', [])
                if ph:
                    prices_matrix.append(ph)

    avg_prices = []
    if prices_matrix:
        num_days = max(len(h) for h in prices_matrix)
        for i in range(num_days):
            sum_p = 0
            count = 0
            for ph in prices_matrix:
                if i < len(ph):
                    sum_p += float(ph[i])
                    count += 1
            if count > 0:
                avg_prices.append(round(sum_p / count, 2))

    dates = []
    today = date.today()
    if avg_prices:
        for i in range(len(avg_prices)):
            d = today - timedelta(days=(len(avg_prices) - 1 - i))
            dates.append(d.strftime('%d-%b-%Y'))

    if from_date or to_date:
        filtered_dates = []
        filtered_prices = []
        for d_str, p in zip(dates, avg_prices):
            d_obj = datetime.strptime(d_str, '%d-%b-%Y').date()
            if from_date and str(d_obj) < from_date:
                continue
            if to_date and str(d_obj) > to_date:
                continue
            filtered_dates.append(d_str)
            filtered_prices.append(p)
        dates = filtered_dates
        avg_prices = filtered_prices

    return JsonResponse({
        'status': 'success',
        'dates': dates,
        'prices': avg_prices
    })

# -------------------------
# SCHEDULE
# -------------------------

from .models import BlueprintStage, CropBlueprint


def manage_schedule(request):
    from apps.superadmin.models import Guideline, GuidelineCategory, Crop
    from django.urls import reverse

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add_guideline":
            title = request.POST.get("title")
            category_id = request.POST.get("category_id")
            author = request.POST.get("author")
            read_time = request.POST.get("read_time")
            tags = request.POST.get("tags")
            content = request.POST.get("content")

            category = GuidelineCategory.objects.filter(id=category_id).first() if category_id else None
            doc_file = request.FILES.get("document_file")

            Guideline.objects.create(
                title=title,
                farming_section=category,
                author_name=author,
                read_time=read_time,
                search_tags=tags,
                detailed_content=content,
                document_file=doc_file
            )
            messages.success(request, "Guideline created successfully!")
            return redirect(f"{reverse('manage_schedule')}#guidelinesTab")

        elif action == "edit_guideline":
            guideline_id = request.POST.get("guideline_id")
            g = Guideline.objects.filter(id=guideline_id).first()
            if g:
                g.title = request.POST.get("title")
                category_id = request.POST.get("category_id")
                g.farming_section = GuidelineCategory.objects.filter(id=category_id).first() if category_id else None
                g.author_name = request.POST.get("author")
                g.read_time = request.POST.get("read_time")
                g.search_tags = request.POST.get("tags")
                g.detailed_content = request.POST.get("content")
                if "document_file" in request.FILES:
                    g.document_file = request.FILES.get("document_file")
                g.save()
                messages.success(request, "Guideline updated successfully!")
            return redirect(f"{reverse('manage_schedule')}#guidelinesTab")

        elif action == "delete_guideline":
            guideline_id = request.POST.get("guideline_id")
            Guideline.objects.filter(id=guideline_id).delete()
            messages.success(request, "Guideline deleted successfully!")
            return redirect(f"{reverse('manage_schedule')}#guidelinesTab")

    blueprints = CropBlueprint.objects.filter(farmer__isnull=True).order_by('-created_at')

    selected_blueprint_id = request.GET.get('blueprint_id')
    selected_blueprint = None
    stages = []

    if blueprints.exists():
        if selected_blueprint_id and selected_blueprint_id != 'None':
            try:
                selected_blueprint = blueprints.filter(id=selected_blueprint_id).first()
            except ValueError:
                selected_blueprint = None
        if not selected_blueprint:
            selected_blueprint = blueprints.first()

        if selected_blueprint:
            stages = selected_blueprint.stages.all().order_by('day_offset')

    crops = Crop.objects.filter(is_active=True).order_by('name')
    guidelines = Guideline.objects.all().order_by('-created_at')
    categories = GuidelineCategory.objects.filter(is_active=True).order_by('name')

    return render(request, "superadmin/schedule/manage_schedule.html", {
        'blueprints': blueprints,
        'selected_blueprint': selected_blueprint,
        'stages': stages,
        'crops': crops,
        'guidelines': guidelines,
        'categories': categories,
    })

def parse_stages_file(uploaded_file):
    """
    Parses an uploaded Excel (.xlsx, .xls) or CSV (.csv) file
    to extract milestone stages (Milestone Title, Activity Type, Day Offset, Priority Level).
    """
    import re
    stages = []
    filename = uploaded_file.name.lower()

    rows_data = []
    if filename.endswith('.csv'):
        import csv
        import io
        content = uploaded_file.read().decode('utf-8-sig', errors='ignore')
        reader = csv.reader(io.StringIO(content))
        for row in reader:
            if any(cell.strip() for cell in row):
                rows_data.append([str(c).strip() for c in row])
    elif filename.endswith(('.xlsx', '.xls')):
        import openpyxl
        wb = openpyxl.load_workbook(uploaded_file, data_only=True)
        ws = wb.active
        for row in ws.iter_rows(values_only=True):
            if any(row):
                rows_data.append([str(c).strip() if c is not None else '' for c in row])

    if not rows_data:
        return stages

    # Check if first row is header
    first_row = [c.lower().replace('_', '').replace(' ', '').replace('-', '') for c in rows_data[0]]
    has_header = False
    col_map = {'title': -1, 'type': -1, 'offset': -1, 'priority': -1}

    for idx, col_name in enumerate(first_row):
        if any(k in col_name for k in ['milestone', 'title', 'stage', 'task', 'name']):
            if col_map['title'] == -1:
                col_map['title'] = idx
                has_header = True
        elif any(k in col_name for k in ['activity', 'type', 'category', 'action']):
            if col_map['type'] == -1:
                col_map['type'] = idx
                has_header = True
        elif any(k in col_name for k in ['day', 'offset', 'timeline']):
            if col_map['offset'] == -1:
                col_map['offset'] = idx
                has_header = True
        elif any(k in col_name for k in ['priority', 'level', 'importance']):
            if col_map['priority'] == -1:
                col_map['priority'] = idx
                has_header = True

    data_rows = rows_data[1:] if has_header else rows_data

    # If columns weren't matched by header names, fall back to positional columns
    if col_map['title'] == -1:
        col_map['title'] = 0
    if col_map['type'] == -1:
        col_map['type'] = 1 if len(rows_data[0]) > 1 else -1
    if col_map['offset'] == -1:
        col_map['offset'] = 2 if len(rows_data[0]) > 2 else -1
    if col_map['priority'] == -1:
        col_map['priority'] = 3 if len(rows_data[0]) > 3 else -1

    for r in data_rows:
        if not any(r):
            continue
        title = r[col_map['title']] if (0 <= col_map['title'] < len(r)) else ''
        activity = r[col_map['type']] if (0 <= col_map['type'] < len(r)) else 'General'
        offset_raw = r[col_map['offset']] if (0 <= col_map['offset'] < len(r)) else '0'
        priority_raw = r[col_map['priority']] if (0 <= col_map['priority'] < len(r)) else 'Medium'

        if not title:
            continue

        try:
            num_match = re.search(r'\d+', str(offset_raw))
            offset = int(num_match.group(0)) if num_match else 0
        except (ValueError, TypeError):
            offset = 0

        priority = str(priority_raw).strip().capitalize()
        if priority not in ['High', 'Medium', 'Low']:
            if 'hi' in priority.lower():
                priority = 'High'
            elif 'lo' in priority.lower():
                priority = 'Low'
            else:
                priority = 'Medium'

        if not activity:
            activity = 'General'

        stages.append({
            'milestone_title': title,
            'activity_type': activity,
            'day_offset': offset,
            'priority_level': priority
        })

    return stages

def add_crop_blueprint(request):
    if request.method == 'POST':
        name = request.POST.get('crop_name')
        season = request.POST.get('cultivation_season')
        days = request.POST.get('yield_cycle_days') or 90

        blueprint = CropBlueprint.objects.create(
            crop_name=name,
            cultivation_season=season,
            yield_cycle_days=days
        )

        stages_file = request.FILES.get('stages_file')
        stages_created = 0
        if stages_file:
            try:
                stages_data = parse_stages_file(stages_file)
                for s in stages_data:
                    BlueprintStage.objects.create(
                        blueprint=blueprint,
                        milestone_title=s['milestone_title'],
                        activity_type=s['activity_type'],
                        day_offset=s['day_offset'],
                        priority_level=s['priority_level']
                    )
                    stages_created += 1
            except Exception as e:
                messages.warning(request, f'Blueprint created, but error parsing stages file: {str(e)}')

        if stages_created > 0:
            messages.success(request, f'Scheduler "{name}" created successfully with {stages_created} milestone steps!')
        else:
            messages.success(request, f'Scheduler "{name}" created successfully.')

        return redirect(f'/admin/schedule/?blueprint_id={blueprint.id}#scheduleTab')
    return redirect('/admin/schedule/#scheduleTab')

def delete_crop_blueprint(request):
    if request.method == 'POST':
        blueprint_id = request.POST.get('blueprint_id')
        CropBlueprint.objects.filter(id=blueprint_id).delete()
        messages.success(request, 'Scheduler deleted successfully.')
    return redirect('/admin/schedule/#scheduleTab')

def add_blueprint_stage(request):
    if request.method == 'POST':
        blueprint_id = request.POST.get('blueprint_id')
        title = request.POST.get('milestone_title')
        activity = request.POST.get('activity_type')
        offset = request.POST.get('day_offset')
        priority = request.POST.get('priority_level')

        BlueprintStage.objects.create(
            blueprint_id=blueprint_id,
            milestone_title=title,
            activity_type=activity,
            day_offset=offset,
            priority_level=priority
        )
        messages.success(request, 'Step added successfully.')
        return redirect(f'/admin/schedule/?blueprint_id={blueprint_id}#scheduleTab')
    return redirect('/admin/schedule/#scheduleTab')

def edit_blueprint_stage(request):
    if request.method == 'POST':
        stage_id = request.POST.get('stage_id')
        blueprint_id = request.POST.get('blueprint_id')

        stage = BlueprintStage.objects.filter(id=stage_id).first()
        if stage:
            stage.milestone_title = request.POST.get('milestone_title')
            stage.activity_type = request.POST.get('activity_type')
            stage.day_offset = request.POST.get('day_offset')
            stage.priority_level = request.POST.get('priority_level')
            stage.save()
            messages.success(request, 'Step updated successfully.')
        return redirect(f'/admin/schedule/?blueprint_id={blueprint_id}#scheduleTab')
    return redirect('/admin/schedule/#scheduleTab')

def delete_blueprint_stage(request):
    if request.method == 'POST':
        stage_id = request.POST.get('stage_id')
        blueprint_id = request.POST.get('blueprint_id')
        BlueprintStage.objects.filter(id=stage_id).delete()
        messages.success(request, 'Step deleted successfully.')
        return redirect(f'/admin/schedule/?blueprint_id={blueprint_id}#scheduleTab')
    return redirect('/admin/schedule/#scheduleTab')



# -------------------------
# NEARBY LOCATIONS
# -------------------------
def manage_nearby(request):
    from django.contrib import messages
    from django.shortcuts import redirect
    from apps.superadmin.models import NearbyLocation, LocationCategory

    if not LocationCategory.objects.exists():
        default_classes = ["Agri Office", "Fishery Office", "Fertilizer Shop", "Organic Shop", "Soil Testing Lab", "Cold Storage", "Warehouse", "Seed Processing Center"]
        for dc in default_classes:
            LocationCategory.objects.create(name=dc, description=f"Default {dc}", is_active=True)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create":
            NearbyLocation.objects.create(
                name=request.POST.get("name"),
                service_class=request.POST.get("service_class") or request.POST.get("demand"),
                contact_person=request.POST.get("contact_person"),
                designation=request.POST.get("designation"),
                contact_number=request.POST.get("phone"),
                email=request.POST.get("email"),
                website=request.POST.get("website"),
                address=request.POST.get("address"),
                notes=request.POST.get("notes")
            )
            messages.success(request, "Agri Location created successfully!")
            return redirect('manage_nearby')

        elif action == "update":
            loc_id = request.POST.get("id")
            if loc_id:
                try:
                    loc = NearbyLocation.objects.get(id=loc_id)
                    loc.name = request.POST.get("name")
                    loc.service_class = request.POST.get("service_class") or request.POST.get("demand")
                    loc.contact_person = request.POST.get("contact_person")
                    loc.designation = request.POST.get("designation")
                    loc.contact_number = request.POST.get("phone")
                    loc.email = request.POST.get("email")
                    loc.website = request.POST.get("website")
                    loc.address = request.POST.get("address")
                    loc.notes = request.POST.get("notes")
                    loc.save()
                    messages.success(request, "Agri Location updated successfully!")
                except NearbyLocation.DoesNotExist:
                    pass
            return redirect('manage_nearby')

        elif action == "delete":
            loc_id = request.POST.get("id")
            if loc_id:
                NearbyLocation.objects.filter(id=loc_id).delete()
                messages.success(request, "Agri Location deleted successfully!")
            return redirect('manage_nearby')

    locations = NearbyLocation.objects.all().order_by('-created_at')
    service_classes = LocationCategory.objects.filter(is_active=True).order_by('name')

    formatted_locations = []
    for loc in locations:
        formatted_locations.append({
            'id': loc.id,
            'type': loc.service_class,
            'name': loc.name,
            'contact_person': loc.contact_person or '',
            'designation': loc.designation or '',
            'address': loc.address or '',
            'phone': loc.contact_number or '',
            'email': loc.email or '',
            'website': loc.website or '',
            'notes': loc.notes or ''
        })

    return render(request, "superadmin/nearby/manage_nearby.html", {
        "locations": formatted_locations,
        "raw_locations": locations,
        "service_classes": service_classes
    })

# -----------------------------------
# GUIDELINE CATEGORY QUICK ADD & AJAX
# -----------------------------------
def ajax_quick_add_guideline_category(request):
    from django.http import JsonResponse
    from apps.superadmin.models import GuidelineCategory

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            return JsonResponse({"success": False, "error": "Category name cannot be empty."})
        obj, created = get_or_reactivate_master(GuidelineCategory,
            name=name,
            defaults={"description": f"Auto-created category: {name}", "is_active": True}
        )
        if not obj.is_active:
            obj.is_active = True
            obj.save()
        return JsonResponse({"success": True, "id": obj.id, "name": obj.name, "created": created})
    return JsonResponse({"success": False, "error": "Invalid request."})

def ajax_quick_delete_guideline_category(request):
    from django.http import JsonResponse
    from apps.superadmin.models import GuidelineCategory

    if request.method == "POST":
        item_id = request.POST.get("item_id")
        name = request.POST.get("name", "").strip()
        if item_id:
            GuidelineCategory.objects.filter(id=item_id).delete()
            return JsonResponse({"success": True, "id": item_id})
        elif name:
            GuidelineCategory.objects.filter(name__iexact=name).delete()
            return JsonResponse({"success": True, "name": name})
    return JsonResponse({"success": False, "error": "Invalid request."})

def ajax_quick_add_service_class(request):
    from django.http import JsonResponse
    from django.views.decorators.csrf import csrf_exempt
    from apps.superadmin.models import LocationCategory

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            return JsonResponse({"success": False, "error": "Service Class name cannot be empty."})
        obj, created = get_or_reactivate_master(LocationCategory,
            name=name,
            defaults={"description": f"Auto-created service class: {name}", "is_active": True}
        )
        return JsonResponse({"success": True, "id": obj.id, "name": obj.name})
    return JsonResponse({"success": False, "error": "Invalid request."})

def ajax_quick_delete_service_class(request):
    from django.http import JsonResponse
    from apps.superadmin.models import LocationCategory

    if request.method == "POST":
        item_id = request.POST.get("item_id")
        name = request.POST.get("name", "").strip()
        if item_id:
            LocationCategory.objects.filter(id=item_id).delete()
            return JsonResponse({"success": True, "id": item_id})
        elif name:
            LocationCategory.objects.filter(name__iexact=name).delete()
            return JsonResponse({"success": True, "name": name})
    return JsonResponse({"success": False, "error": "Invalid request."})


# -------------------------
# WORKERS QUICK ADD & AJAX
# -------------------------
def ajax_quick_add_designation(request):
    from django.http import JsonResponse
    from apps.superadmin.models import Designation

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            return JsonResponse({"success": False, "error": "Designation name cannot be empty."})
        obj, created = get_or_reactivate_master(Designation,
            name=name,
            defaults={"description": f"Auto-created designation: {name}", "is_active": True}
        )
        return JsonResponse({"success": True, "id": obj.id, "name": obj.name})
    return JsonResponse({"success": False, "error": "Invalid request."})

def ajax_quick_delete_designation(request):
    from django.http import JsonResponse
    from apps.superadmin.models import Designation

    if request.method == "POST":
        item_id = request.POST.get("item_id")
        name = request.POST.get("name", "").strip()
        if item_id:
            Designation.objects.filter(id=item_id).delete()
            return JsonResponse({"success": True, "id": item_id})
        elif name:
            Designation.objects.filter(name__iexact=name).delete()
            return JsonResponse({"success": True, "name": name})
    return JsonResponse({"success": False, "error": "Invalid request."})

def ajax_quick_add_worker_skill(request):
    from django.http import JsonResponse
    from apps.superadmin.models import ManPowerWorkType

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            return JsonResponse({"success": False, "error": "Skill name cannot be empty."})
        obj, created = get_or_reactivate_master(ManPowerWorkType,
            name=name,
            defaults={"description": f"Auto-created skill: {name}", "is_active": True}
        )
        return JsonResponse({"success": True, "id": obj.id, "name": obj.name})
    return JsonResponse({"success": False, "error": "Invalid request."})

def ajax_quick_delete_worker_skill(request):
    from django.http import JsonResponse
    from apps.superadmin.models import ManPowerWorkType

    if request.method == "POST":
        item_id = request.POST.get("item_id")
        name = request.POST.get("name", "").strip()
        if item_id:
            ManPowerWorkType.objects.filter(id=item_id).delete()
            return JsonResponse({"success": True, "id": item_id})
        elif name:
            ManPowerWorkType.objects.filter(name__iexact=name).delete()
            return JsonResponse({"success": True, "name": name})
    return JsonResponse({"success": False, "error": "Invalid request."})

def ajax_quick_add_wage_unit(request):
    from django.http import JsonResponse
    from apps.superadmin.models import ExpenseUnit

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            return JsonResponse({"success": False, "error": "Unit name cannot be empty."})
        obj, created = get_or_reactivate_master(ExpenseUnit,
            name=name,
            defaults={"description": f"Auto-created unit: {name}", "is_active": True}
        )
        return JsonResponse({"success": True, "id": obj.id, "name": obj.name})
    return JsonResponse({"success": False, "error": "Invalid request."})

def ajax_quick_delete_wage_unit(request):
    from django.http import JsonResponse
    from apps.superadmin.models import ExpenseUnit

    if request.method == "POST":
        item_id = request.POST.get("item_id")
        name = request.POST.get("name", "").strip()
        if item_id:
            ExpenseUnit.objects.filter(id=item_id).delete()
            return JsonResponse({"success": True, "id": item_id})
        elif name:
            ExpenseUnit.objects.filter(name__iexact=name).delete()
            return JsonResponse({"success": True, "name": name})
    return JsonResponse({"success": False, "error": "Invalid request."})


# -------------------------
# WORKERS
# -------------------------
def manage_workers_availability(request):
    from apps.superadmin.models import LogisticsWorker, Designation, ManPowerWorkType, ExpenseUnit, State, City

    def resolve_state_name(val):
        if not val:
            return ""
        val_str = str(val).strip()
        if val_str.isdigit():
            st = State.objects.filter(id=int(val_str)).first()
            if st:
                return st.name
        return val_str

    def resolve_city_name(val):
        if not val:
            return ""
        val_str = str(val).strip()
        if val_str.isdigit():
            ct = City.objects.filter(id=int(val_str)).first()
            if ct:
                return ct.name
        return val_str

    if request.method == "POST":
        action = request.POST.get('action')

        if action == 'create_worker':
            name = request.POST.get('name', '').strip()
            designation = request.POST.get('designation', '').strip()
            workforce_class = designation or request.POST.get('workforce_class', '').strip()
            specialty_skill = request.POST.get('specialty_skill', '').strip()
            address = request.POST.get('address', '').strip()
            state = resolve_state_name(request.POST.get('state', ''))
            city = resolve_city_name(request.POST.get('city', ''))
            notes = request.POST.get('notes', '').strip()
            wage_rate = request.POST.get('wage_rate') or 0
            wage_unit = request.POST.get('wage_unit', 'Day').strip()
            contact_phone = request.POST.get('contact_phone', '').strip()
            status = request.POST.get('status', 'Available').strip()

            LogisticsWorker.objects.create(
                name=name,
                designation=designation,
                workforce_class=workforce_class,
                specialty_skill=specialty_skill,
                address=address,
                state=state,
                city=city,
                notes=notes,
                wage_rate=wage_rate,
                wage_unit=wage_unit,
                contact_phone=contact_phone,
                status=status
            )
            messages.success(request, f"Worker {name} successfully registered!")
            return redirect('manage_workers_availability')

        elif action == 'edit_worker':
            worker_id = request.POST.get('worker_id')
            worker = get_object_or_404(LogisticsWorker, id=worker_id)
            worker.name = request.POST.get('name', '').strip()
            worker.designation = request.POST.get('designation', '').strip()
            worker.workforce_class = worker.designation or request.POST.get('workforce_class', '').strip()
            worker.specialty_skill = request.POST.get('specialty_skill', '').strip()
            worker.address = request.POST.get('address', '').strip()
            worker.state = resolve_state_name(request.POST.get('state', ''))
            worker.city = resolve_city_name(request.POST.get('city', ''))
            worker.notes = request.POST.get('notes', '').strip()
            worker.wage_rate = request.POST.get('wage_rate') or 0
            worker.wage_unit = request.POST.get('wage_unit', 'Day').strip()
            worker.contact_phone = request.POST.get('contact_phone', '').strip()
            worker.status = request.POST.get('status', 'Available').strip()
            worker.save()
            messages.success(request, f"Worker {worker.name} successfully updated!")
            return redirect('manage_workers_availability')

        elif action == 'delete_worker':
            worker_id = request.POST.get('worker_id')
            worker = get_object_or_404(LogisticsWorker, id=worker_id)
            name = worker.name
            worker.delete()
            messages.success(request, f"Worker {name} successfully deleted!")
            return redirect('manage_workers_availability')

        elif action == 'update_status':
            worker_id = request.POST.get('worker_id')
            status = request.POST.get('status')
            worker = get_object_or_404(LogisticsWorker, id=worker_id)
            worker.status = status
            worker.save()
            messages.success(request, f"Worker {worker.name} status updated to {status}!")
            return redirect('manage_workers_availability')

    workers = LogisticsWorker.objects.all().order_by('-created_at')

    formatted_workers = []
    for w in workers:
        desig_val = w.designation or w.workforce_class or ''
        formatted_workers.append({
            'id': w.id,
            'name': w.name,
            'designation': desig_val,
            'class': desig_val,
            'skill': w.specialty_skill or '',
            'address': w.address or '',
            'state': w.state or '',
            'city': w.city or '',
            'notes': w.notes or '',
            'wage': w.wage_rate.normalize() if w.wage_rate else '',
            'wage_unit': w.wage_unit or 'Day',
            'phone': w.contact_phone or '',
            'status': w.status or 'Available',
            'wage_val': str(w.wage_rate.normalize()) if w.wage_rate else '',
            'initials': "".join([x[0] for x in w.name.split() if x])[:2].upper()
        })

    designations = Designation.objects.filter(is_active=True).order_by('name')
    work_types = ManPowerWorkType.objects.filter(is_active=True).order_by('name')
    wage_units = ExpenseUnit.objects.filter(is_active=True).order_by('name')

    states = State.objects.filter(country__sort_name='IN').order_by('name')
    if not states.exists():
        states = State.objects.all().order_by('name')

    return render(request, "superadmin/workers/manage_workers.html", {
        "workers": formatted_workers,
        "designations": designations,
        "work_types": work_types,
        "wage_units": wage_units,
        "states": states
    })



# -------------------------
# RENTAL
# -------------------------
def manage_rental_availability(request):
    from apps.superadmin.models import RentalEquipment

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_rental':
            equipment_name = request.POST.get('equipment_name')
            hardware_class = request.POST.get('hardware_class')
            price_per_day = request.POST.get('price_per_day')
            owner_name = request.POST.get('owner_name')
            contact_phone = request.POST.get('contact_phone')
            condition_rating = request.POST.get('condition_rating')
            status = request.POST.get('status')

            RentalEquipment.objects.create(
                equipment_name=equipment_name,
                hardware_class=hardware_class,
                price_per_day=price_per_day if price_per_day else None,
                owner_name=owner_name,
                contact_phone=contact_phone,
                condition_rating=condition_rating,
                status=status
            )
            messages.success(request, f"Rental machinery {equipment_name} successfully registered!")
            return redirect('manage_rental_availability')

        elif action == 'edit_rental':
            rental_id = request.POST.get('rental_id')
            rental = get_object_or_404(RentalEquipment, id=rental_id)
            rental.equipment_name = request.POST.get('equipment_name')
            rental.hardware_class = request.POST.get('hardware_class')
            price_per_day = request.POST.get('price_per_day')
            rental.price_per_day = price_per_day if price_per_day else None
            rental.owner_name = request.POST.get('owner_name')
            rental.contact_phone = request.POST.get('contact_phone')
            rental.condition_rating = request.POST.get('condition_rating')
            rental.status = request.POST.get('status')
            rental.save()
            messages.success(request, f"Rental machinery {rental.equipment_name} successfully updated!")
            return redirect('manage_rental_availability')

        elif action == 'delete_rental':
            rental_id = request.POST.get('rental_id')
            rental = get_object_or_404(RentalEquipment, id=rental_id)
            name = rental.equipment_name
            rental.delete()
            messages.success(request, f"Rental machinery {name} successfully deleted!")
            return redirect('manage_rental_availability')

        elif action == 'update_status':
            rental_id = request.POST.get('rental_id')
            status = request.POST.get('status')
            rental = get_object_or_404(RentalEquipment, id=rental_id)
            rental.status = status
            rental.save()
            messages.success(request, f"Rental machinery {rental.equipment_name} status updated to {status}!")
            return redirect('manage_rental_availability')

    rentals = RentalEquipment.objects.all().order_by('-created_at')

    formatted_rentals = []
    for r in rentals:
        formatted_rentals.append({
            'id': r.id,
            'name': r.equipment_name,
            'class': r.hardware_class,
            'price': r.price_per_day.normalize() if r.price_per_day else '',
            'owner': r.owner_name,
            'phone': r.contact_phone,
            'rating': r.condition_rating,
            'status': r.status,
            'initials': "".join([x[0] for x in r.equipment_name.split() if x])[:2].upper(),
            'is_farmer': False
        })

    from apps.farmer.models import FarmerInventory
    farmer_rentals = FarmerInventory.objects.filter(rental_available=True).order_by('-created_at')

    for fr in farmer_rentals:
        # Determine condition based on status
        condition = "Good Condition"
        if fr.status in ["Maintenance", "Needs Repair"]:
            condition = "Needs Repair"

        formatted_rentals.append({
            'id': fr.id,
            'name': fr.item_name,
            'class': 'Tool' if 'Tool' in fr.category else 'Vehicle',
            'price': fr.rental_price.normalize() if fr.rental_price else (fr.unit_price.normalize() if fr.unit_price else ''),
            'rental_unit': fr.rental_unit or 'Per Day',
            'owner': fr.farmer.user.first_name + " " + fr.farmer.user.last_name if fr.farmer and fr.farmer.user else "Farmer",
            'phone': fr.farmer.mobile_number if fr.farmer and hasattr(fr.farmer, 'mobile_number') else '',
            'rating': condition,
            'status': fr.status if fr.status in ['Operational', 'Maintenance', 'Available', 'Rented', 'Booked'] else 'Available',
            'initials': "".join([x[0] for x in fr.item_name.split() if x])[:2].upper(),
            'is_farmer': True
        })

    hardware_classes = HardwareClass.objects.filter(is_active=True).order_by('name')

    total_assets = len(formatted_rentals)
    available_assets = sum(1 for r in formatted_rentals if r['status'] == 'Available')
    rented_assets = sum(1 for r in formatted_rentals if r['status'] in ['Rented', 'Booked'])
    maintenance_assets = sum(1 for r in formatted_rentals if r['status'] in ['Maintenance', 'Needs Repair'])

    return render(request, "superadmin/rental/manage_rental.html", {
        "rentals": formatted_rentals,
        "hardware_classes": hardware_classes,
        "total_assets": total_assets,
        "available_assets": available_assets,
        "rented_assets": rented_assets,
        "maintenance_assets": maintenance_assets,
    })


# -------------------------
# GUIDELINES
# -------------------------
def manage_guidelines(request):
    from apps.superadmin.models import Guideline, GuidelineCategory
    from django.urls import reverse
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add_guideline":
            title = request.POST.get("title")
            category_id = request.POST.get("category_id")
            author = request.POST.get("author")
            read_time = request.POST.get("read_time")
            tags = request.POST.get("tags")
            content = request.POST.get("content")

            category = GuidelineCategory.objects.filter(id=category_id).first() if category_id else None

            doc_file = request.FILES.get("document_file")

            Guideline.objects.create(
                title=title,
                farming_section=category,
                author_name=author,
                read_time=read_time,
                search_tags=tags,
                detailed_content=content,
                document_file=doc_file
            )
            messages.success(request, "Guideline created successfully!")

        elif action == "edit_guideline":
            guideline_id = request.POST.get("guideline_id")
            g = Guideline.objects.filter(id=guideline_id).first()
            if g:
                g.title = request.POST.get("title")
                category_id = request.POST.get("category_id")
                g.farming_section = GuidelineCategory.objects.filter(id=category_id).first() if category_id else None
                g.author_name = request.POST.get("author")
                g.read_time = request.POST.get("read_time")
                g.search_tags = request.POST.get("tags")
                g.detailed_content = request.POST.get("content")
                if "document_file" in request.FILES:
                    g.document_file = request.FILES.get("document_file")
                g.save()
                messages.success(request, "Guideline updated successfully!")

        elif action == "delete_guideline":
            guideline_id = request.POST.get("guideline_id")
            Guideline.objects.filter(id=guideline_id).delete()
            messages.success(request, "Guideline deleted successfully!")

        return redirect(f"{reverse('manage_schedule')}#guidelinesTab")

    return redirect(f"{reverse('manage_schedule')}#guidelinesTab")


# -------------------------
# USERS
# -------------------------
def manage_users(request):
    if not is_admin_user(request.user):
        return JsonResponse({"error": "Administration access required."}, status=403)
    users = admin_users()
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add":
            name = request.POST.get("name", "").strip()
            if HarvestingFrequency.objects.filter(name__iexact=name).exists():
                messages.error(request, "Harvesting Frequency with this name already exists!")
                return redirect(request.path)
            if InventoryItem.objects.filter(name__iexact=name).exists():
                messages.error(request, "Inventory Item with this name already exists!")
                return redirect(request.path)
            if ProductType.objects.filter(name__iexact=name).exists():
                messages.error(request, "Product Type with this name already exists!")
                return redirect(request.path)
            if DocumentCategory.objects.filter(name__iexact=name).exists():
                messages.error(request, "Document Category with this name already exists!")
                return redirect(request.path)
            if GuidelineCategory.objects.filter(name__iexact=name).exists():
                messages.error(request, "Guideline Category with this name already exists!")
                return redirect(request.path)
            if ManPowerWorkType.objects.filter(name__iexact=name).exists():
                messages.error(request, "Man Power Work Type with this name already exists!")
                return redirect(request.path)
            if LocationCategory.objects.filter(name__iexact=name).exists():
                messages.error(request, "Location Category with this name already exists!")
                return redirect(request.path)
            if ScheduleActivityType.objects.filter(name__iexact=name).exists():
                messages.error(request, "Schedule Activity Type with this name already exists!")
                return redirect(request.path)
            if SalesDeduction.objects.filter(name__iexact=name).exists():
                messages.error(request, "Sales Deduction with this name already exists!")
                return redirect(request.path)
            if Crop.objects.filter(name__iexact=name).exists():
                messages.error(request, "Crop with this name already exists!")
                return redirect(request.path)
            if CropType.objects.filter(name__iexact=name).exists():
                messages.error(request, "Crop Type with this name already exists!")
                return redirect(request.path)
            if SoilType.objects.filter(name__iexact=name).exists():
                messages.error(request, "Soil Type with this name already exists!")
                return redirect(request.path)
            if ProductUnit.objects.filter(name__iexact=name).exists():
                messages.error(request, "Product Unit with this name already exists!")
                return redirect(request.path)
            email = request.POST.get("email", "").strip()
            mobile = request.POST.get("mobile", "").strip()
            password = request.POST.get("password", "").strip()
            role = request.POST.get("role", "").strip()
            if role.lower() in {'farmer', 'staff'}:
                messages.error(request, "Choose an administration role, not a farm role.")
                return redirect('manage_users')
            status = request.POST.get("status")  # '0' for Active, '1' for Suspended

            perm_all = request.POST.get('perm_all') == 'true'
            perms_raw = request.POST.get('permissions', '[]')
            try:
                selected_perms = json.loads(perms_raw) if isinstance(perms_raw, str) else request.POST.getlist('permissions')
            except Exception:
                selected_perms = request.POST.getlist('permissions')
            assigned_permissions = ["ALL"] if perm_all else (selected_perms if isinstance(selected_perms, list) else [])

            if name and email and mobile and password:
                from apps.superadmin.phone_utils import check_mobile_already_exists
                is_mobile_taken, mobile_err = check_mobile_already_exists(mobile)

                if User.objects.filter(email=email).exists():
                    messages.error(request, "A user with this email already exists.")
                elif is_mobile_taken:
                    messages.error(request, mobile_err)
                else:
                    user = User.objects.create_user(username=email, email=email, first_name=name, password=password)
                    user.is_active = (status == "0")
                    user.save()

                    # Profile is created by signal, just update it
                    profile = user.profile
                    profile.role = role if role else 'admin'
                    profile.mobile_number = mobile
                    profile.permissions = assigned_permissions
                    profile.save()
                    log_admin_action(request, "Admin User Created", f"Admin/Staff User '{name}' ({email}) was created.", icon_class="ri-user-add-line", bg_color="success")
                    messages.success(request, "Staff member added successfully!")
            else:
                messages.error(request, "Name, Email, Mobile and Password are required.")

        elif action == "edit":
            user_id = request.POST.get("id")
            user = get_object_or_404(users, id=user_id)

            if user.email == 'admin@agriot.com':
                messages.error(request, "You cannot modify the primary Superadmin account.")
                return redirect("manage_users")

            name = request.POST.get("name", "").strip()
            email = request.POST.get("email", "").strip()
            mobile = request.POST.get("mobile", "").strip()
            password = request.POST.get("password", "").strip()
            role = request.POST.get("role", "").strip()
            if role.lower() in {'farmer', 'staff'}:
                messages.error(request, "Choose an administration role, not a farm role.")
                return redirect('manage_users')
            status = request.POST.get("status")

            perm_all = request.POST.get('perm_all') == 'true'
            perms_raw = request.POST.get('permissions', '[]')
            try:
                selected_perms = json.loads(perms_raw) if isinstance(perms_raw, str) else request.POST.getlist('permissions')
            except Exception:
                selected_perms = request.POST.getlist('permissions')
            assigned_permissions = ["ALL"] if perm_all else (selected_perms if isinstance(selected_perms, list) else [])

            if name and email and mobile:
                from apps.superadmin.phone_utils import check_mobile_already_exists
                is_mobile_taken, mobile_err = check_mobile_already_exists(
                    mobile,
                    exclude_user_id=user_id,
                    exclude_profile_id=getattr(user, 'profile', None).id if hasattr(user, 'profile') else None
                )

                if User.objects.filter(email=email).exclude(id=user_id).exists():
                    messages.error(request, "A user with this email already exists.")
                elif is_mobile_taken:
                    messages.error(request, mobile_err)
                else:
                    user.first_name = name
                    user.email = email
                    user.username = email
                    if password:
                        user.set_password(password)
                    user.is_active = (status == "0")
                    user.save()


                    profile = user.profile
                    profile.role = role if role else 'admin'
                    profile.mobile_number = mobile
                    profile.permissions = assigned_permissions
                    profile.save()
                    log_admin_action(request, "Admin User Updated", f"Admin/Staff User '{name}' ({email}) details/permissions were updated.", icon_class="ri-user-settings-line", bg_color="info")
                    messages.success(request, "Staff member updated successfully!")
            else:
                messages.error(request, "Name, Email, and Mobile cannot be empty.")

        elif action == "delete":
            user_id = request.POST.get("id")
            user = get_object_or_404(users, id=user_id)
            if user.email == 'admin@agriot.com':
                messages.error(request, "You cannot delete the primary Superadmin account.")
            else:
                user_name = user.first_name or user.email
                user.delete()
                log_admin_action(request, "Admin User Deleted", f"Admin/Staff User '{user_name}' was deleted.", icon_class="ri-user-unfollow-line", bg_color="danger")
                messages.success(request, "Staff member deleted successfully!")

        return redirect("manage_users")

    # Only administration accounts belong in this list and its role options.
    staff_users = users.order_by('-date_joined')

    # Get all distinct roles dynamically
    roles = set(users.values_list('profile__role', flat=True))
    roles = [r for r in roles if r]  # filter empty roles

    admin_permissions = [
        "Farmers Management",
        "Packages & Subscriptions",
        "Market Management",
        "Schedule Management",
        "Near By Locations",
        "Workers Availability",
        "Rental Availability",
        "Guidelines Management",
        "Documents",
        "Users Management",
        "Reports",
        "Settings"
    ]

    return render(request, "superadmin/users/manage_users.html", {
        "staff_users": staff_users,
        "roles": roles,
        "admin_permissions": admin_permissions
    })


# -------------------------
# SETTINGS
# -------------------------
def general_settings(request):
    if request.method == "POST":
        from apps.farmer.validation import pincode_error
        error = pincode_error(request)
        if error is not None:
            return error

    setting = GeneralSetting.objects.first()
    if not setting:
        setting = GeneralSetting.objects.create()

    if request.method == "POST":
        if 'logo' in request.FILES:
            setting.logo = request.FILES['logo']
        if 'favicon' in request.FILES:
            setting.favicon = request.FILES['favicon']

        setting.title = request.POST.get('title', setting.title)
        setting.website = request.POST.get('website', setting.website)
        setting.contact_person = request.POST.get('contact_person', setting.contact_person)
        setting.email = request.POST.get('email', setting.email)
        setting.date_format = request.POST.get('date_format', setting.date_format)
        setting.timezone = request.POST.get('timezone', setting.timezone)
        setting.street = request.POST.get('street', setting.street)
        setting.door_no = request.POST.get('door_no', setting.door_no)
        setting.pincode = request.POST.get('pincode', setting.pincode)
        setting.gst_number = request.POST.get('gst_number', setting.gst_number)
        setting.cin = request.POST.get('cin', setting.cin)
        setting.weather_api_key = request.POST.get('weather_api_key', setting.weather_api_key)

        # Razorpay settings if present in request
        if 'razorpay_key_id' in request.POST:
            setting.razorpay_key_id = request.POST.get('razorpay_key_id', '').strip()
        if 'razorpay_key_secret' in request.POST:
            setting.razorpay_key_secret = request.POST.get('razorpay_key_secret', '').strip()
        if 'razorpay_webhook_secret' in request.POST:
            setting.razorpay_webhook_secret = request.POST.get('razorpay_webhook_secret', '').strip()
        if 'has_razorpay_section' in request.POST:
            setting.razorpay_enabled = request.POST.get('razorpay_enabled') in ['on', 'true', '1']
            setting.razorpay_auto_renew = request.POST.get('razorpay_auto_renew') in ['on', 'true', '1']

        country_id = request.POST.get('country')
        if country_id:
            try:
                setting.country = Country.objects.get(id=country_id)
            except Country.DoesNotExist:
                pass

        state_id = request.POST.get('state')
        if state_id:
            try:
                setting.state = State.objects.get(id=state_id)
            except State.DoesNotExist:
                pass

        city_id = request.POST.get('city')
        if city_id:
            try:
                setting.city = City.objects.get(id=city_id)
            except City.DoesNotExist:
                pass
        elif 'city' in request.POST:
            setting.city = None

        taluk_id = request.POST.get('taluk')
        if taluk_id:
            try:
                setting.taluk = Taluk.objects.get(id=taluk_id)
            except Taluk.DoesNotExist:
                pass
        elif 'taluk' in request.POST:
            setting.taluk = None

        setting.save()
        messages.success(request, 'General Settings updated successfully!')
        # Ensure redirect URL name matches urls.py. Let's assume it's general-settings
        return redirect(request.path)

    countries = Country.objects.all().order_by('name')
    states = State.objects.filter(country=setting.country).order_by('name') if setting.country else State.objects.none()
    cities = City.objects.filter(state=setting.state).order_by('name') if setting.state else City.objects.none()
    taluks = Taluk.objects.filter(city=setting.city).order_by('name') if setting.city else Taluk.objects.none()

    return render(request, "superadmin/settings/general_settings.html", {
        "setting": setting,
        "countries": countries,
        "states": states,
        "cities": cities,
        "taluks": taluks
    })


def payment_integrations(request):
    setting = GeneralSetting.objects.first()
    if not setting:
        setting = GeneralSetting.objects.create()

    if request.method == "POST":
        setting.razorpay_key_id = request.POST.get("razorpay_key_id", "").strip()
        setting.razorpay_key_secret = request.POST.get("razorpay_key_secret", "").strip()
        setting.razorpay_webhook_secret = request.POST.get("razorpay_webhook_secret", "").strip()
        setting.razorpay_enabled = request.POST.get("razorpay_enabled") in ["on", "true", "1"]
        setting.razorpay_auto_renew = request.POST.get("razorpay_auto_renew") in ["on", "true", "1"]
        setting.save()
        messages.success(request, "Razorpay payment integration & auto-renewal settings saved successfully!")
        return redirect("payment_integrations")

    webhook_url = request.build_absolute_uri("/payment/razorpay/webhook/")
    return render(request, "superadmin/settings/integrations.html", {
        "setting": setting,
        "webhook_url": webhook_url,
    })


def trigger_auto_renew_check(request):
    from apps.superadmin.razorpay_utils import process_all_auto_renewals
    result = process_all_auto_renewals()
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.GET.get("format") == "json":
        return JsonResponse(result)
    if result.get("success"):
        messages.success(request, result.get("message", "Auto-renewal check completed."))
    else:
        messages.warning(request, result.get("message", "Auto-renewal could not be run."))
    return redirect("payment_integrations")


def superadmin_mail_settings(request):
    from apps.superadmin.models import SMTPSettings
    config = SMTPSettings.objects.filter(is_default=True).first()
    if not config:
        config = SMTPSettings.objects.filter(user__isnull=True).first()
    if not config:
        config = SMTPSettings.objects.create(
            is_default=True,
            email_host="smtp.gmail.com",
            email_port=587,
            email_use_tls=True,
            email_use_ssl=False,
            from_name="AgriOT Platform",
            is_active=True
        )

    if request.method == "POST":
        config.email_host = request.POST.get("email_host", "smtp.gmail.com").strip()
        try:
            config.email_port = int(request.POST.get("email_port", 587))
        except (ValueError, TypeError):
            config.email_port = 587
        config.email_host_user = request.POST.get("email_host_user", "").strip()
        pwd = request.POST.get("email_host_password", "").strip()
        if pwd:
            config.email_host_password = pwd
        security = request.POST.get("security_mode", "tls")
        config.email_use_tls = (security == "tls")
        config.email_use_ssl = (security == "ssl")
        config.from_email = request.POST.get("from_email", "").strip() or config.email_host_user
        config.from_name = request.POST.get("from_name", "AgriOT Platform").strip()
        config.is_active = request.POST.get("is_active") in ["on", "true", "1"]
        config.is_default = True
        config.save()
        messages.success(request, "SuperAdmin SMTP Mail settings saved successfully!")
        return redirect("superadmin_mail_settings")

    return render(request, "superadmin/settings/mail_settings.html", {
        "config": config,
    })


def superadmin_test_mail(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST method required."}, status=405)

    from apps.superadmin.models import SMTPSettings
    from apps.superadmin.email_utils import test_smtp_connection

    config = SMTPSettings.objects.filter(is_default=True).first()
    if not config:
        config = SMTPSettings.objects.filter(user__isnull=True).first()

    if not config or not config.email_host_user or not config.email_host_password:
        return JsonResponse({"success": False, "message": "Please configure and save your SMTP Email & App Password before testing."}, status=400)

    test_recipient = (request.POST.get("test_email") or "").strip()
    if not test_recipient:
        return JsonResponse({"success": False, "message": "Please provide a valid recipient email address."}, status=400)

    success, message = test_smtp_connection(config, test_recipient)
    return JsonResponse({"success": success, "message": message})


def superadmin_preview_email(request, template_type):
    from django.http import HttpResponse
    import datetime
    from django.utils import timezone
    sample_context = {
        'farmer_name': "Ramesh Kumar",
        'mobile_number': "+91 98765 43210",
        'email': "ramesh.farmer@example.com",
        'package_name': "Pro AgriOT Plan",
        'max_acre_support': "50",
        'billing_cycle': "Monthly",
        'amount': "2,499.00",
        'start_date': timezone.now().strftime("%d %b %Y"),
        'end_date': (timezone.now() + datetime.timedelta(days=30)).strftime("%d %b %Y"),
        'login_url': request.build_absolute_uri('/farmer/'),
        'dashboard_url': request.build_absolute_uri('/farmer/dashboard/'),
        'is_upgrade': True,
        'current_year': timezone.now().year,
    }
    if template_type == "welcome":
        return render(request, "emails/welcome_email.html", sample_context)
    elif template_type == "renewal":
        return render(request, "emails/renewal_email.html", sample_context)
    elif template_type == "upgrade":
        return render(request, "emails/package_upgrade_email.html", sample_context)
    else:
        return HttpResponse("Unknown template type", status=404)


def product_unit(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name")
            if ProductUnit.objects.filter(name__iexact=name).exists():
                messages.error(request, "Product Unit with this name already exists!")
                return redirect(request.path)
            slug = request.POST.get("slug")
            description = request.POST.get("description")
            is_active = request.POST.get("is_active") == "on"
            ProductUnit.objects.create(name=name, slug=slug, description=description, is_active=is_active)
            messages.success(request, "Product Unit added successfully!")
        elif action == "edit":
            pu_id = request.POST.get("id")
            name = request.POST.get("name")
            if ProductUnit.objects.filter(name__iexact=name).exclude(id=pu_id).exists():
                messages.error(request, "Product Unit with this name already exists!")
                return redirect(request.path)
            pu = ProductUnit.objects.get(id=pu_id)
            pu.name = name
            pu.slug = request.POST.get("slug")
            pu.description = request.POST.get("description")
            pu.is_active = request.POST.get("is_active") == "on"
            pu.save()
            messages.success(request, "Product Unit updated successfully!")
        elif action == "delete":
            pu_id = request.POST.get("id")
            ProductUnit.objects.filter(id=pu_id).delete()
            messages.success(request, "Product Unit deleted successfully!")

        return redirect("product_unit")

    product_units = ProductUnit.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/product_unit.html", {"product_units": product_units})
def soil_type(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name")
            if SoilType.objects.filter(name__iexact=name).exists():
                messages.error(request, "Soil Type with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description")
            is_active = request.POST.get("is_active") == "on"
            SoilType.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Soil Type added successfully!")
        elif action == "edit":
            st_id = request.POST.get("id")
            name = request.POST.get("name")
            if SoilType.objects.filter(name__iexact=name).exclude(id=st_id).exists():
                messages.error(request, "Soil Type with this name already exists!")
                return redirect(request.path)
            st = SoilType.objects.get(id=st_id)
            st.name = name
            st.description = request.POST.get("description")
            st.is_active = request.POST.get("is_active") == "on"
            st.save()
            messages.success(request, "Soil Type updated successfully!")
        elif action == "delete":
            st_id = request.POST.get("id")
            SoilType.objects.filter(id=st_id).delete()
            messages.success(request, "Soil Type deleted successfully!")

        return redirect("soil_type")

    soil_types = SoilType.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/soil_type.html", {"soil_types": soil_types})
def crop_type(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name")
            if CropType.objects.filter(name__iexact=name).exists():
                messages.error(request, "Crop Type with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description")
            is_active = request.POST.get("is_active") == "on"
            CropType.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Crop Type added successfully!")
        elif action == "edit":
            ct_id = request.POST.get("id")
            name = request.POST.get("name")
            if CropType.objects.filter(name__iexact=name).exclude(id=ct_id).exists():
                messages.error(request, "Crop Type with this name already exists!")
                return redirect(request.path)
            ct = CropType.objects.get(id=ct_id)
            ct.name = name
            ct.description = request.POST.get("description")
            ct.is_active = request.POST.get("is_active") == "on"
            ct.save()
            messages.success(request, "Crop Type updated successfully!")
        elif action == "delete":
            ct_id = request.POST.get("id")
            CropType.objects.filter(id=ct_id).delete()
            messages.success(request, "Crop Type deleted successfully!")

        return redirect("crop_type")

    crop_types = CropType.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/crop_type.html", {"crop_types": crop_types})
def crop_settings(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name")
            if Crop.objects.filter(name__iexact=name).exists():
                messages.error(request, "Crop with this name already exists!")
                return redirect(request.path)
            crop_type_id = request.POST.get("crop_type_id")
            description = request.POST.get("description")
            is_active = request.POST.get("is_active") == "on"
            image = request.FILES.get("image")

            crop_type = CropType.objects.get(id=crop_type_id)
            Crop.objects.create(name=name, crop_type=crop_type, description=description, is_active=is_active, image=image)
            messages.success(request, "Crop added successfully!")

        elif action == "edit":
            crop_id = request.POST.get("id")
            name = request.POST.get("name")
            if Crop.objects.filter(name__iexact=name).exclude(id=crop_id).exists():
                messages.error(request, "Crop with this name already exists!")
                return redirect(request.path)
            crop = Crop.objects.get(id=crop_id)
            crop.name = name

            crop_type_id = request.POST.get("crop_type_id")
            if crop_type_id:
                crop.crop_type = CropType.objects.get(id=crop_type_id)

            crop.description = request.POST.get("description")
            crop.is_active = request.POST.get("is_active") == "on"

            if "image" in request.FILES:
                crop.image = request.FILES.get("image")

            crop.save()
            messages.success(request, "Crop updated successfully!")

        elif action == "delete":
            crop_id = request.POST.get("id")
            Crop.objects.filter(id=crop_id).delete()
            messages.success(request, "Crop deleted successfully!")

        return redirect("crop_settings")

    crops = Crop.objects.all().select_related("crop_type").order_by("-created_at")
    crop_types = CropType.objects.filter(is_active=True).order_by("name")

    return render(request, "superadmin/settings/crop_settings.html", {
        "crops": crops,
        "crop_types": crop_types
    })
def sales_deduction(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name")
            if SalesDeduction.objects.filter(name__iexact=name).exists():
                messages.error(request, "Sales Deduction with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description")
            is_active = request.POST.get("is_active") == "on"
            SalesDeduction.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Sales Deduction added successfully!")
        elif action == "edit":
            sd_id = request.POST.get("id")
            name = request.POST.get("name")
            if SalesDeduction.objects.filter(name__iexact=name).exclude(id=sd_id).exists():
                messages.error(request, "Sales Deduction with this name already exists!")
                return redirect(request.path)
            sd = SalesDeduction.objects.get(id=sd_id)
            sd.name = name
            sd.description = request.POST.get("description")
            sd.is_active = request.POST.get("is_active") == "on"
            sd.save()
            messages.success(request, "Sales Deduction updated successfully!")
        elif action == "delete":
            sd_id = request.POST.get("id")
            SalesDeduction.objects.filter(id=sd_id).delete()
            messages.success(request, "Sales Deduction deleted successfully!")

        return redirect("sales_deduction")

    sales_deductions = SalesDeduction.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/sales_deduction.html", {"sales_deductions": sales_deductions})
def schedule_activity(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name")
            if ScheduleActivityType.objects.filter(name__iexact=name).exists():
                messages.error(request, "Schedule Activity Type with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description")
            is_active = request.POST.get("is_active") == "on"
            ScheduleActivityType.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Schedule Activity Type added successfully!")
        elif action == "edit":
            sa_id = request.POST.get("id")
            name = request.POST.get("name")
            if ScheduleActivityType.objects.filter(name__iexact=name).exclude(id=sa_id).exists():
                messages.error(request, "Schedule Activity Type with this name already exists!")
                return redirect(request.path)
            sa = ScheduleActivityType.objects.get(id=sa_id)
            sa.name = name
            sa.description = request.POST.get("description")
            sa.is_active = request.POST.get("is_active") == "on"
            sa.save()
            messages.success(request, "Schedule Activity Type updated successfully!")
        elif action == "delete":
            sa_id = request.POST.get("id")
            ScheduleActivityType.objects.filter(id=sa_id).delete()
            messages.success(request, "Schedule Activity Type deleted successfully!")

        return redirect("schedule_activity")

    activities = ScheduleActivityType.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/schedule_activity.html", {"activities": activities})
def location_category(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name")
            if LocationCategory.objects.filter(name__iexact=name).exists():
                messages.error(request, "Location Category with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description")
            is_active = request.POST.get("is_active") == "on"
            LocationCategory.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Location Category added successfully!")
        elif action == "edit":
            lc_id = request.POST.get("id")
            name = request.POST.get("name")
            if LocationCategory.objects.filter(name__iexact=name).exclude(id=lc_id).exists():
                messages.error(request, "Location Category with this name already exists!")
                return redirect(request.path)
            lc = LocationCategory.objects.get(id=lc_id)
            lc.name = name
            lc.description = request.POST.get("description")
            lc.is_active = request.POST.get("is_active") == "on"
            lc.save()
            messages.success(request, "Location Category updated successfully!")
        elif action == "delete":
            lc_id = request.POST.get("id")
            LocationCategory.objects.filter(id=lc_id).delete()
            messages.success(request, "Location Category deleted successfully!")

        return redirect("location_category")

    categories = LocationCategory.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/location_category.html", {"categories": categories})
def man_power(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name")
            if ManPowerWorkType.objects.filter(name__iexact=name).exists():
                messages.error(request, "Man Power Work Type with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description")
            is_active = request.POST.get("is_active") == "on"
            ManPowerWorkType.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Man Power Work Type added successfully!")
        elif action == "edit":
            mp_id = request.POST.get("id")
            name = request.POST.get("name")
            if ManPowerWorkType.objects.filter(name__iexact=name).exclude(id=mp_id).exists():
                messages.error(request, "Man Power Work Type with this name already exists!")
                return redirect(request.path)
            mp = ManPowerWorkType.objects.get(id=mp_id)
            mp.name = name
            mp.description = request.POST.get("description")
            mp.is_active = request.POST.get("is_active") == "on"
            mp.save()
            messages.success(request, "Man Power Work Type updated successfully!")
        elif action == "delete":
            mp_id = request.POST.get("id")
            ManPowerWorkType.objects.filter(id=mp_id).delete()
            messages.success(request, "Man Power Work Type deleted successfully!")

        return redirect("man_power")

    work_types = ManPowerWorkType.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/man_power.html", {"work_types": work_types})
def guideline_category(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name")
            if GuidelineCategory.objects.filter(name__iexact=name).exists():
                messages.error(request, "Guideline Category with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description")
            is_active = request.POST.get("is_active") == "on"
            GuidelineCategory.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Guideline Category added successfully!")
        elif action == "edit":
            gc_id = request.POST.get("id")
            name = request.POST.get("name")
            if GuidelineCategory.objects.filter(name__iexact=name).exclude(id=gc_id).exists():
                messages.error(request, "Guideline Category with this name already exists!")
                return redirect(request.path)
            gc = GuidelineCategory.objects.get(id=gc_id)
            gc.name = name
            gc.description = request.POST.get("description")
            gc.is_active = request.POST.get("is_active") == "on"
            gc.save()
            messages.success(request, "Guideline Category updated successfully!")
        elif action == "delete":
            gc_id = request.POST.get("id")
            GuidelineCategory.objects.filter(id=gc_id).delete()
            messages.success(request, "Guideline Category deleted successfully!")

        return redirect("guideline_category")

    categories = GuidelineCategory.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/guideline_category.html", {"categories": categories})
def document_category(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            document_type = request.POST.get("document_type", "").strip()
            name = request.POST.get("name", "").strip()
            if DocumentCategory.objects.filter(name__iexact=name).exists():
                messages.error(request, "Document Category with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description", "").strip()
            is_active = request.POST.get("is_active") == "on"
            if name:  # Prevent saving empty names
                DocumentCategory.objects.create(document_type=document_type, name=name, description=description, is_active=is_active)
                messages.success(request, "Document Category added successfully!")
            else:
                messages.error(request, "Document Category cannot be empty.")
        elif action == "edit":
            dc_id = request.POST.get("id")
            dc = DocumentCategory.objects.get(id=dc_id)
            dc.document_type = request.POST.get("document_type", "").strip()
            name = request.POST.get("name", "").strip()
            if name:
                if DocumentCategory.objects.filter(name__iexact=name).exclude(id=dc_id).exists():
                    messages.error(request, "Document Category with this name already exists!")
                    return redirect(request.path)
                dc.name = name
                dc.description = request.POST.get("description", "").strip()
                dc.is_active = request.POST.get("is_active") == "on"
                dc.save()
                messages.success(request, "Document Category updated successfully!")
            else:
                messages.error(request, "Document Category cannot be empty.")
        elif action == "delete":
            dc_id = request.POST.get("id")
            DocumentCategory.objects.filter(id=dc_id).delete()
            messages.success(request, "Document Category deleted successfully!")

        return redirect("document_category")

    categories = DocumentCategory.objects.all().order_by("-created_at")
    doc_types = DocumentType.objects.filter(is_active=True).order_by("name")
    return render(request, "superadmin/settings/document_category.html", {"categories": categories, "doc_types": doc_types})


def document_type(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name", "").strip()
            if DocumentType.objects.filter(name__iexact=name).exists():
                messages.error(request, "Document Type with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description", "").strip()
            is_active = request.POST.get("is_active") == "on"
            if name:
                DocumentType.objects.create(name=name, description=description, is_active=is_active)
                messages.success(request, "Document Type added successfully!")
            else:
                messages.error(request, "Document Type cannot be empty.")
        elif action == "edit":
            dt_id = request.POST.get("id")
            dt = DocumentType.objects.get(id=dt_id)
            name = request.POST.get("name", "").strip()
            if name:
                if DocumentType.objects.filter(name__iexact=name).exclude(id=dt_id).exists():
                    messages.error(request, "Document Type with this name already exists!")
                    return redirect(request.path)
                dt.name = name
                dt.description = request.POST.get("description", "").strip()
                dt.is_active = request.POST.get("is_active") == "on"
                dt.save()
                messages.success(request, "Document Type updated successfully!")
            else:
                messages.error(request, "Document Type cannot be empty.")
        elif action == "delete":
            dt_id = request.POST.get("id")
            DocumentType.objects.filter(id=dt_id).delete()
            messages.success(request, "Document Type deleted successfully!")

        return redirect("document_type")

    doc_types = DocumentType.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/document_type.html", {"doc_types": doc_types})


def product_type(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name")
            if ProductType.objects.filter(name__iexact=name).exists():
                messages.error(request, "Product Type with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description")
            is_active = request.POST.get("is_active") == "on"
            ProductType.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Product Type added successfully!")
        elif action == "edit":
            pt_id = request.POST.get("id")
            name = request.POST.get("name")
            if ProductType.objects.filter(name__iexact=name).exclude(id=pt_id).exists():
                messages.error(request, "Product Type with this name already exists!")
                return redirect(request.path)
            pt = ProductType.objects.get(id=pt_id)
            pt.name = name
            pt.description = request.POST.get("description")
            pt.is_active = request.POST.get("is_active") == "on"
            pt.save()
            messages.success(request, "Product Type updated successfully!")
        elif action == "delete":
            pt_id = request.POST.get("id")
            ProductType.objects.filter(id=pt_id).delete()
            messages.success(request, "Product Type deleted successfully!")

        return redirect("product_type")

    product_types = ProductType.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/product_type.html", {"product_types": product_types})
def inventory_item(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name")
            if InventoryItem.objects.filter(name__iexact=name).exists():
                messages.error(request, "Inventory Item with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description")
            product_type_id = request.POST.get("product_type_id")
            is_active = request.POST.get("is_active") == "on"

            product_type = None
            if product_type_id:
                product_type = ProductType.objects.filter(id=product_type_id).first()

            InventoryItem.objects.create(
                name=name,
                description=description,
                product_type=product_type,
                is_active=is_active
            )
            messages.success(request, "Inventory Item added successfully!")

        elif action == "edit":
            item_id = request.POST.get("id")
            name = request.POST.get("name")
            if InventoryItem.objects.filter(name__iexact=name).exclude(id=item_id).exists():
                messages.error(request, "Inventory Item with this name already exists!")
                return redirect(request.path)
            item = InventoryItem.objects.get(id=item_id)
            item.name = name
            item.description = request.POST.get("description")
            item.is_active = request.POST.get("is_active") == "on"

            product_type_id = request.POST.get("product_type_id")
            if product_type_id:
                item.product_type = ProductType.objects.filter(id=product_type_id).first()
            else:
                item.product_type = None

            item.save()
            messages.success(request, "Inventory Item updated successfully!")

        elif action == "delete":
            item_id = request.POST.get("id")
            InventoryItem.objects.filter(id=item_id).delete()
            messages.success(request, "Inventory Item deleted successfully!")

        return redirect("inventory_item")

    inventory_items = InventoryItem.objects.all().order_by("-created_at")
    product_types = ProductType.objects.filter(is_active=True).order_by("name")

    return render(request, "superadmin/settings/inventory_item.html", {
        "inventory_items": inventory_items,
        "product_types": product_types
    })
def harvesting_frequency(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name")
            if HarvestingFrequency.objects.filter(name__iexact=name).exists():
                messages.error(request, "Harvesting Frequency with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description")
            is_active = request.POST.get("is_active") == "on"
            HarvestingFrequency.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Harvesting Frequency added successfully!")
        elif action == "edit":
            hf_id = request.POST.get("id")
            name = request.POST.get("name")
            if HarvestingFrequency.objects.filter(name__iexact=name).exclude(id=hf_id).exists():
                messages.error(request, "Harvesting Frequency with this name already exists!")
                return redirect(request.path)
            hf = HarvestingFrequency.objects.get(id=hf_id)
            hf.name = name
            hf.description = request.POST.get("description")
            hf.is_active = request.POST.get("is_active") == "on"
            hf.save()
            messages.success(request, "Harvesting Frequency updated successfully!")
        elif action == "delete":
            hf_id = request.POST.get("id")
            HarvestingFrequency.objects.filter(id=hf_id).delete()
            messages.success(request, "Harvesting Frequency deleted successfully!")

        return redirect("harvesting_frequency")

    harvesting_frequencies = HarvestingFrequency.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/harvesting_frequency.html", {"harvesting_frequencies": harvesting_frequencies})


def ajax_get_taluks(request):
    city_id = request.GET.get('city_id') or request.GET.get('district_id')
    from apps.superadmin.models import Taluk
    if city_id:
        taluks = Taluk.objects.filter(city_id=city_id).values('id', 'name').order_by('name')
        return JsonResponse(list(taluks), safe=False)
    return JsonResponse([], safe=False)


def state_settings(request):
    from apps.superadmin.models import State, Country
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = (request.POST.get("name") or "").strip()
            country_id = request.POST.get("country_id") or request.POST.get("country")
            status = request.POST.get("status", "1")
            if not name:
                messages.error(request, "State name is required!")
                return redirect(request.path)
            if State.objects.filter(name__iexact=name).exists():
                messages.error(request, f"State '{name}' already exists!")
                return redirect(request.path)
            country = Country.objects.filter(id=country_id).first() if country_id else Country.objects.first()
            if not country:
                country = Country.objects.create(name="India", sort_name="IN", phone_code="+91", status="1")
            State.objects.create(name=name, country=country, status=status)
            messages.success(request, f"State '{name}' added successfully!")

        elif action == "edit":
            state_id = request.POST.get("id")
            name = (request.POST.get("name") or "").strip()
            country_id = request.POST.get("country_id") or request.POST.get("country")
            status = request.POST.get("status", "1")
            if not name:
                messages.error(request, "State name cannot be empty!")
                return redirect(request.path)
            if State.objects.filter(name__iexact=name).exclude(id=state_id).exists():
                messages.error(request, f"Another State with name '{name}' already exists!")
                return redirect(request.path)
            st = get_object_or_404(State, id=state_id)
            st.name = name
            if country_id:
                c = Country.objects.filter(id=country_id).first()
                if c:
                    st.country = c
            st.status = status
            st.save()
            messages.success(request, f"State '{name}' updated successfully!")

        elif action == "delete":
            state_id = request.POST.get("id")
            st = get_object_or_404(State, id=state_id)
            state_name = st.name
            st.delete()
            messages.success(request, f"State '{state_name}' deleted successfully!")

        return redirect("state_settings")

    from django.core.paginator import Paginator
    from django.db.models import Count
    query = request.GET.get('q', '').strip()
    states_query = State.objects.select_related('country').order_by('name', 'id')
    if query:
        states_query = states_query.filter(name__icontains=query)
    page_obj = Paginator(states_query, 25).get_page(request.GET.get('page'))
    counts = dict(City.objects.filter(state_id__in=[state.id for state in page_obj]).values_list('state_id').annotate(total=Count('id')))
    for state in page_obj:
        state.cities_count = counts.get(state.id, 0)
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, "superadmin/settings/state.html", {
        "states": page_obj, "page_obj": page_obj, "query": query,
        "page_query": params.urlencode(),
        "countries": Country.objects.all().order_by('name'),
    })


def city_settings(request):
    from apps.superadmin.models import State, City
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = (request.POST.get("name") or "").strip()
            state_id = request.POST.get("state_id") or request.POST.get("state")
            status = request.POST.get("status", "1")
            if not name:
                messages.error(request, "District / City name is required!")
                return redirect(request.path)
            if not state_id:
                messages.error(request, "Please select a State!")
                return redirect(request.path)
            state = get_object_or_404(State, id=state_id)
            if City.objects.filter(name__iexact=name, state=state).exists():
                messages.error(request, f"District / City '{name}' already exists in {state.name}!")
                return redirect(request.path)
            City.objects.create(name=name, state=state, status=status)
            messages.success(request, f"District / City '{name}' added successfully!")

        elif action == "edit":
            city_id = request.POST.get("id")
            name = (request.POST.get("name") or "").strip()
            state_id = request.POST.get("state_id") or request.POST.get("state")
            status = request.POST.get("status", "1")
            if not name:
                messages.error(request, "Name cannot be empty!")
                return redirect(request.path)
            ct = get_object_or_404(City, id=city_id)
            if state_id:
                st = get_object_or_404(State, id=state_id)
                if City.objects.filter(name__iexact=name, state=st).exclude(id=city_id).exists():
                    messages.error(request, f"District / City '{name}' already exists in {st.name}!")
                    return redirect(request.path)
                ct.state = st
            ct.name = name
            ct.status = status
            ct.save()
            messages.success(request, f"District / City '{name}' updated successfully!")

        elif action == "delete":
            city_id = request.POST.get("id")
            ct = get_object_or_404(City, id=city_id)
            city_name = ct.name
            ct.delete()
            messages.success(request, f"District / City '{city_name}' deleted successfully!")

        return redirect("city_settings")

    from django.core.paginator import Paginator
    from django.db.models import Count
    selected_state = request.GET.get('state', '')
    selected_state = selected_state if selected_state.isdigit() else ''
    query = request.GET.get('q', '').strip()
    cities_query = City.objects.select_related('state').order_by('state__name', 'name', 'id')
    if selected_state:
        cities_query = cities_query.filter(state_id=selected_state)
    if query:
        cities_query = cities_query.filter(name__icontains=query)
    page_obj = Paginator(cities_query, 25).get_page(request.GET.get('page'))
    counts = dict(Taluk.objects.filter(city_id__in=[city.id for city in page_obj]).values_list('city_id').annotate(total=Count('id')))
    for city in page_obj:
        city.taluks_count = counts.get(city.id, 0)
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, "superadmin/settings/city.html", {
        "cities": page_obj, "page_obj": page_obj, "query": query,
        "page_query": params.urlencode(), "states": State.objects.all().order_by('name'),
        "selected_state": selected_state,
    })


def taluk_settings(request):
    from apps.superadmin.models import State, City, Taluk
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = (request.POST.get("name") or "").strip()
            city_id = request.POST.get("city_id") or request.POST.get("city") or request.POST.get("district")
            status = request.POST.get("status", "1")
            if not name:
                messages.error(request, "Taluk name is required!")
                return redirect(request.path)
            if not city_id:
                messages.error(request, "Please select a District / City!")
                return redirect(request.path)
            city = get_object_or_404(City, id=city_id)
            if Taluk.objects.filter(name__iexact=name, city=city).exists():
                messages.error(request, f"Taluk '{name}' already exists in {city.name}!")
                return redirect(request.path)
            Taluk.objects.create(name=name, city=city, status=status)
            messages.success(request, f"Taluk '{name}' added successfully!")

        elif action == "edit":
            taluk_id = request.POST.get("id")
            name = (request.POST.get("name") or "").strip()
            city_id = request.POST.get("city_id") or request.POST.get("city") or request.POST.get("district")
            status = request.POST.get("status", "1")
            if not name:
                messages.error(request, "Taluk name cannot be empty!")
                return redirect(request.path)
            tlk = get_object_or_404(Taluk, id=taluk_id)
            if city_id:
                ct = get_object_or_404(City, id=city_id)
                if Taluk.objects.filter(name__iexact=name, city=ct).exclude(id=taluk_id).exists():
                    messages.error(request, f"Taluk '{name}' already exists in {ct.name}!")
                    return redirect(request.path)
                tlk.city = ct
            tlk.name = name
            tlk.status = status
            tlk.save()
            messages.success(request, f"Taluk '{name}' updated successfully!")

        elif action == "delete":
            taluk_id = request.POST.get("id")
            tlk = get_object_or_404(Taluk, id=taluk_id)
            taluk_name = tlk.name
            tlk.delete()
            messages.success(request, f"Taluk '{taluk_name}' deleted successfully!")

        return redirect("taluk_settings")

    from django.core.paginator import Paginator
    selected_state = request.GET.get('state', '')
    selected_state = selected_state if selected_state.isdigit() else ''
    selected_city = request.GET.get('city', '') or request.GET.get('district', '')
    selected_city = selected_city if selected_city.isdigit() else ''
    query = request.GET.get('q', '').strip()
    taluks_query = Taluk.objects.select_related('city', 'city__state')
    if selected_city:
        taluks_query = taluks_query.filter(city_id=selected_city)
    if selected_state:
        taluks_query = taluks_query.filter(city__state_id=selected_state)
    if query:
        taluks_query = taluks_query.filter(name__icontains=query)
    taluks_query = taluks_query.order_by('city__name', 'name', 'id')
    page_obj = Paginator(taluks_query, 25).get_page(request.GET.get('page'))
    states = State.objects.all().order_by('name')
    if selected_state:
        cities = City.objects.filter(state_id=selected_state).order_by('name')
    elif selected_city:
        cities = City.objects.filter(id=selected_city)
    else:
        cities = City.objects.none()
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, "superadmin/settings/taluk.html", {
        "taluks": page_obj, "page_obj": page_obj, "query": query,
        "page_query": params.urlencode(), "states": states, "cities": cities,
        "selected_state": selected_state, "selected_city": selected_city,
    })


# -------------------------
# DOCUMENT MANAGEMENT
# -------------------------
def manage_documents(request):
    from apps.superadmin.models import Document
    from auth.models import Profile

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "upload":
            title = request.POST.get("title", "").strip()
            category = request.POST.get("category", "General").strip()
            description = request.POST.get("description", "").strip()
            farmer_id = request.POST.get("farmer_id")
            doc_file = request.FILES.get("file")

            if not title or not doc_file:
                messages.error(request, "Document Title and File are required.")
                return redirect("superadmin-documents")

            # 10MB file size check (10 * 1024 * 1024 = 10485760 bytes)
            MAX_SIZE = 10 * 1024 * 1024
            if doc_file.size > MAX_SIZE:
                messages.error(request, "File size exceeds the 10MB limit. Please upload a smaller file.")
                return redirect("superadmin-documents")

            ext = os.path.splitext(doc_file.name)[1].lower()

            doc = Document.objects.create(
                title=title,
                category=category,
                description=description,
                file=doc_file,
                file_size_bytes=doc_file.size,
                file_type=ext,
                uploaded_by=request.user,
                farmer_id=farmer_id if farmer_id else None
            )
            log_admin_action(request, "Document Uploaded", f"Document '{doc.title}' was uploaded.", icon_class="ri-file-upload-line", bg_color="success")
            messages.success(request, "Document uploaded successfully!")

        elif action == "edit":
            doc_id = request.POST.get("id")
            try:
                doc = Document.objects.get(id=doc_id)
                doc.title = request.POST.get("title", doc.title).strip()
                doc.category = request.POST.get("category", doc.category).strip()
                doc.description = request.POST.get("description", doc.description).strip()
                farmer_id = request.POST.get("farmer_id")
                doc.farmer_id = farmer_id if farmer_id else None

                # Optional new file upload with 10MB check
                new_file = request.FILES.get("file")
                if new_file:
                    if new_file.size > 10 * 1024 * 1024:
                        messages.error(request, "File size exceeds the 10MB limit. Please upload a smaller file.")
                        return redirect("superadmin-documents")
                    doc.file = new_file
                    doc.file_size_bytes = new_file.size
                    doc.file_type = os.path.splitext(new_file.name)[1].lower()

                doc.save()
                messages.success(request, "Document updated successfully!")
            except Document.DoesNotExist:
                messages.error(request, "Document not found.")

        elif action == "delete":
            doc_id = request.POST.get("id")
            try:
                doc = Document.objects.get(id=doc_id)
                if doc.file and os.path.isfile(doc.file.path):
                    try:
                        os.remove(doc.file.path)
                    except Exception:
                        pass
                doc.delete()
                messages.success(request, "Document deleted successfully!")
            except Document.DoesNotExist:
                messages.error(request, "Document not found.")

        return redirect("superadmin-documents")

    documents = Document.objects.select_related("uploaded_by", "farmer", "farmer__user").order_by("-created_at")
    farmers = Profile.objects.filter(role="farmer").select_related("user").order_by("user__first_name")
    doc_categories = DocumentCategory.objects.filter(is_active=True).order_by("document_type", "name")

    return render(request, "superadmin/documents/manage_documents.html", {
        "documents": documents,
        "farmers": farmers,
        "doc_categories": doc_categories,
    })


def share_document_view(request, token):
    from apps.superadmin.models import Document
    try:
        doc = Document.objects.get(uuid_token=token)
        return render(request, "documents/share_document.html", {"document": doc})
    except Document.DoesNotExist:
        return render(request, "documents/share_document.html", {"error": "Document link is invalid or has been removed."})

def hardware_class_settings(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            name = request.POST.get('name')
            if HardwareClass.objects.filter(name__iexact=name).exists():
                messages.error(request, 'Hardware Class with this name already exists!')
                return redirect(request.path)
            description = request.POST.get('description')
            is_active = request.POST.get('is_active') == 'on'
            HardwareClass.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, 'Hardware Class added successfully!')
        elif action == 'edit':
            hc_id = request.POST.get('id')
            name = request.POST.get('name')
            if HardwareClass.objects.filter(name__iexact=name).exclude(id=hc_id).exists():
                messages.error(request, 'Hardware Class with this name already exists!')
                return redirect(request.path)
            hc = HardwareClass.objects.get(id=hc_id)
            hc.name = name
            hc.description = request.POST.get('description')
            hc.is_active = request.POST.get('is_active') == 'on'
            hc.save()
            messages.success(request, 'Hardware Class updated successfully!')
        elif action == 'delete':
            hc_id = request.POST.get('id')
            HardwareClass.objects.filter(id=hc_id).delete()
            messages.success(request, 'Hardware Class deleted successfully!')
        return redirect('hardware_class_settings')

    classes = HardwareClass.objects.all().order_by('-created_at')
    return render(request, 'superadmin/settings/hardware_class.html', {'classes': classes})



def expense_type_settings(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Expense Type name cannot be empty.')
                return redirect(request.path)
            if ExpenseType.objects.filter(name__iexact=name).exists():
                messages.error(request, 'Expense Type with this name already exists!')
                return redirect(request.path)
            description = request.POST.get('description', '').strip()
            is_vendor_based = request.POST.get('is_vendor_based') == 'on'
            is_active = request.POST.get('is_active') == 'on'
            ExpenseType.objects.create(name=name, description=description, is_vendor_based=is_vendor_based, is_active=is_active)
            messages.success(request, 'Expense Type added successfully!')
        elif action == 'edit':
            et_id = request.POST.get('id')
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Expense Type name cannot be empty.')
                return redirect(request.path)
            if ExpenseType.objects.filter(name__iexact=name).exclude(id=et_id).exists():
                messages.error(request, 'Expense Type with this name already exists!')
                return redirect(request.path)
            et = ExpenseType.objects.get(id=et_id)
            et.name = name
            et.description = request.POST.get('description', '').strip()
            et.is_vendor_based = request.POST.get('is_vendor_based') == 'on'
            et.is_active = request.POST.get('is_active') == 'on'
            et.save()
            messages.success(request, 'Expense Type updated successfully!')
        elif action == 'delete':
            et_id = request.POST.get('id')
            ExpenseType.objects.filter(id=et_id).delete()
            messages.success(request, 'Expense Type deleted successfully!')
        return redirect('expense_type_settings')

    expense_types = ExpenseType.objects.all().order_by('-created_at')
    return render(request, 'superadmin/settings/expense_type.html', {'expense_types': expense_types})


def expense_unit_settings(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Expense Unit name cannot be empty.')
                return redirect(request.path)
            if ExpenseUnit.objects.filter(name__iexact=name).exists():
                messages.error(request, 'Expense Unit with this name already exists!')
                return redirect(request.path)
            slug = request.POST.get('slug', '').strip()
            description = request.POST.get('description', '').strip()
            is_active = request.POST.get('is_active') == 'on'
            ExpenseUnit.objects.create(name=name, slug=slug, description=description, is_active=is_active)
            messages.success(request, 'Expense Unit added successfully!')
        elif action == 'edit':
            eu_id = request.POST.get('id')
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Expense Unit name cannot be empty.')
                return redirect(request.path)
            if ExpenseUnit.objects.filter(name__iexact=name).exclude(id=eu_id).exists():
                messages.error(request, 'Expense Unit with this name already exists!')
                return redirect(request.path)
            eu = ExpenseUnit.objects.get(id=eu_id)
            eu.name = name
            eu.slug = request.POST.get('slug', '').strip()
            eu.description = request.POST.get('description', '').strip()
            eu.is_active = request.POST.get('is_active') == 'on'
            eu.save()
            messages.success(request, 'Expense Unit updated successfully!')
        elif action == 'delete':
            eu_id = request.POST.get('id')
            ExpenseUnit.objects.filter(id=eu_id).delete()
            messages.success(request, 'Expense Unit deleted successfully!')
        return redirect('expense_unit_settings')

    expense_units = ExpenseUnit.objects.all().order_by('-created_at')
    return render(request, 'superadmin/settings/expense_unit.html', {'expense_units': expense_units})


def designation_settings(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Designation name cannot be empty.')
                return redirect(request.path)
            if Designation.objects.filter(name__iexact=name).exists():
                messages.error(request, 'Designation with this name already exists!')
                return redirect(request.path)
            description = request.POST.get('description', '').strip()
            is_active = request.POST.get('is_active') == 'on'
            Designation.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, 'Designation added successfully!')
        elif action == 'edit':
            d_id = request.POST.get('id')
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Designation name cannot be empty.')
                return redirect(request.path)
            if Designation.objects.filter(name__iexact=name).exclude(id=d_id).exists():
                messages.error(request, 'Designation with this name already exists!')
                return redirect(request.path)
            d = Designation.objects.get(id=d_id)
            d.name = name
            d.description = request.POST.get('description', '').strip()
            d.is_active = request.POST.get('is_active') == 'on'
            d.save()
            messages.success(request, 'Designation updated successfully!')
        elif action == 'delete':
            d_id = request.POST.get('id')
            Designation.objects.filter(id=d_id).delete()
            messages.success(request, 'Designation deleted successfully!')
        return redirect('designation_settings')

    designations = Designation.objects.all().order_by('-created_at')
    return render(request, 'superadmin/settings/designation.html', {'designations': designations})


def customer_type_settings(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Customer Type name cannot be empty.')
                return redirect(request.path)
            if CustomerType.objects.filter(name__iexact=name).exists():
                messages.error(request, 'Customer Type with this name already exists!')
                return redirect(request.path)
            description = request.POST.get('description', '').strip()
            is_active = request.POST.get('is_active') == 'on'
            CustomerType.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, 'Customer Type added successfully!')
        elif action == 'edit':
            ct_id = request.POST.get('id')
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Customer Type name cannot be empty.')
                return redirect(request.path)
            if CustomerType.objects.filter(name__iexact=name).exclude(id=ct_id).exists():
                messages.error(request, 'Customer Type with this name already exists!')
                return redirect(request.path)
            ct = CustomerType.objects.get(id=ct_id)
            ct.name = name
            ct.description = request.POST.get('description', '').strip()
            ct.is_active = request.POST.get('is_active') == 'on'
            ct.save()
            messages.success(request, 'Customer Type updated successfully!')
        elif action == 'delete':
            ct_id = request.POST.get('id')
            CustomerType.objects.filter(id=ct_id).delete()
            messages.success(request, 'Customer Type deleted successfully!')
        return redirect('customer_type_settings')

    customer_types = CustomerType.objects.all().order_by('-created_at')
    return render(request, 'superadmin/settings/customer_type.html', {'customer_types': customer_types})


def inventory_category_settings(request):
    from apps.superadmin.models import InventoryCategory
    from django.contrib import messages
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Category name cannot be empty.')
                return redirect(request.path)
            if InventoryCategory.objects.filter(name__iexact=name).exists():
                messages.error(request, 'Category with this name already exists!')
                return redirect(request.path)
            layout_type = request.POST.get('layout_type', 'Warehouse')
            description = request.POST.get('description', '').strip()
            is_active = request.POST.get('is_active') == 'on' or request.POST.get('is_active') == True
            InventoryCategory.objects.create(name=name, layout_type=layout_type, description=description, is_active=is_active)
            messages.success(request, 'Inventory Category added successfully!')
        elif action == 'edit':
            cat_id = request.POST.get('id')
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Category name cannot be empty.')
                return redirect(request.path)
            if InventoryCategory.objects.filter(name__iexact=name).exclude(id=cat_id).exists():
                messages.error(request, 'Category with this name already exists!')
                return redirect(request.path)
            cat = InventoryCategory.objects.get(id=cat_id)
            cat.name = name
            cat.layout_type = request.POST.get('layout_type', 'Warehouse')
            cat.description = request.POST.get('description', '').strip()
            cat.is_active = request.POST.get('is_active') == 'on' or request.POST.get('is_active') == True
            cat.save()
            messages.success(request, 'Inventory Category updated successfully!')
        elif action == 'delete':
            cat_id = request.POST.get('id')
            InventoryCategory.objects.filter(id=cat_id).delete()
            messages.success(request, 'Inventory Category deleted successfully!')
        return redirect('inventory_category_settings')

    categories = InventoryCategory.objects.all().order_by('-created_at')
    return render(request, 'superadmin/settings/inventory_category.html', {'categories': categories})


def storage_location_settings(request):
    from apps.superadmin.models import StorageLocation
    from django.contrib import messages
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Location name cannot be empty.')
                return redirect(request.path)
            if StorageLocation.objects.filter(name__iexact=name).exists():
                messages.error(request, 'Location with this name already exists!')
                return redirect(request.path)
            description = request.POST.get('description', '').strip()
            is_active = request.POST.get('is_active') == 'on' or request.POST.get('is_active') == True
            StorageLocation.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, 'Storage Location added successfully!')
        elif action == 'edit':
            loc_id = request.POST.get('id')
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Location name cannot be empty.')
                return redirect(request.path)
            if StorageLocation.objects.filter(name__iexact=name).exclude(id=loc_id).exists():
                messages.error(request, 'Location with this name already exists!')
                return redirect(request.path)
            loc = StorageLocation.objects.get(id=loc_id)
            loc.name = name
            loc.description = request.POST.get('description', '').strip()
            loc.is_active = request.POST.get('is_active') == 'on' or request.POST.get('is_active') == True
            loc.save()
            messages.success(request, 'Storage Location updated successfully!')
        elif action == 'delete':
            loc_id = request.POST.get('id')
            StorageLocation.objects.filter(id=loc_id).delete()
            messages.success(request, 'Storage Location deleted successfully!')
        return redirect('storage_location_settings')

    locations = StorageLocation.objects.all().order_by('-created_at')
    return render(request, 'superadmin/settings/storage_location.html', {'locations': locations})


def task_category_settings(request):
    from apps.superadmin.models import TaskCategory
    from django.contrib import messages
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Category name cannot be empty.')
                return redirect(request.path)
            if TaskCategory.objects.filter(name__iexact=name).exists():
                messages.error(request, 'Category with this name already exists!')
                return redirect(request.path)
            description = request.POST.get('description', '').strip()
            is_farm_required = request.POST.get('is_farm_required') == 'on'
            is_crop_required = request.POST.get('is_crop_required') == 'on'
            is_active = request.POST.get('is_active') == 'on' or request.POST.get('is_active') == True
            TaskCategory.objects.create(
                name=name,
                description=description,
                is_farm_required=is_farm_required,
                is_crop_required=is_crop_required,
                is_active=is_active
            )
            messages.success(request, 'Task Category added successfully!')
        elif action == 'edit':
            cat_id = request.POST.get('id')
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Category name cannot be empty.')
                return redirect(request.path)
            if TaskCategory.objects.filter(name__iexact=name).exclude(id=cat_id).exists():
                messages.error(request, 'Category with this name already exists!')
                return redirect(request.path)
            cat = TaskCategory.objects.get(id=cat_id)
            cat.name = name
            cat.description = request.POST.get('description', '').strip()
            cat.is_farm_required = request.POST.get('is_farm_required') == 'on'
            cat.is_crop_required = request.POST.get('is_crop_required') == 'on'
            cat.is_active = request.POST.get('is_active') == 'on' or request.POST.get('is_active') == True
            cat.save()
            messages.success(request, 'Task Category updated successfully!')
        elif action == 'delete':
            cat_id = request.POST.get('id')
            TaskCategory.objects.filter(id=cat_id).delete()
            messages.success(request, 'Task Category deleted successfully!')
        return redirect('task_category_settings')

    categories = TaskCategory.objects.all().order_by('-created_at')
    return render(request, 'superadmin/settings/task_category.html', {'categories': categories})


def task_priority_settings(request):
    from apps.superadmin.models import TaskPriority
    from django.contrib import messages
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Priority name cannot be empty.')
                return redirect(request.path)
            if TaskPriority.objects.filter(name__iexact=name).exists():
                messages.error(request, 'Priority with this name already exists!')
                return redirect(request.path)
            description = request.POST.get('description', '').strip()
            is_active = request.POST.get('is_active') == 'on' or request.POST.get('is_active') == True
            TaskPriority.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, 'Task Priority added successfully!')
        elif action == 'edit':
            pri_id = request.POST.get('id')
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Priority name cannot be empty.')
                return redirect(request.path)
            if TaskPriority.objects.filter(name__iexact=name).exclude(id=pri_id).exists():
                messages.error(request, 'Priority with this name already exists!')
                return redirect(request.path)
            pri = TaskPriority.objects.get(id=pri_id)
            pri.name = name
            pri.description = request.POST.get('description', '').strip()
            pri.is_active = request.POST.get('is_active') == 'on' or request.POST.get('is_active') == True
            pri.save()
            messages.success(request, 'Task Priority updated successfully!')
        elif action == 'delete':
            pri_id = request.POST.get('id')
            TaskPriority.objects.filter(id=pri_id).delete()
            messages.success(request, 'Task Priority deleted successfully!')
        return redirect('task_priority_settings')

    priorities = TaskPriority.objects.all().order_by('-created_at')
    return render(request, 'superadmin/settings/task_priority.html', {'priorities': priorities})


def irrigation_type(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Irrigation Type cannot be empty.")
                return redirect(request.path)
            if IrrigationType.objects.filter(name__iexact=name).exists():
                messages.error(request, "Irrigation Type with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description", "").strip()
            is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            IrrigationType.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Irrigation Type added successfully!")
        elif action == "edit":
            item_id = request.POST.get("id")
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Irrigation Type cannot be empty.")
                return redirect(request.path)
            if IrrigationType.objects.filter(name__iexact=name).exclude(id=item_id).exists():
                messages.error(request, "Irrigation Type with this name already exists!")
                return redirect(request.path)
            item = IrrigationType.objects.get(id=item_id)
            item.name = name
            item.description = request.POST.get("description", "").strip()
            item.is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            item.save()
            messages.success(request, "Irrigation Type updated successfully!")
        elif action == "delete":
            item_id = request.POST.get("id")
            IrrigationType.objects.filter(id=item_id).delete()
            messages.success(request, "Irrigation Type deleted successfully!")

        return redirect("irrigation_type")

    items = IrrigationType.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/irrigation_type.html", {"irrigation_types": items})


def water_source_type(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Water Source cannot be empty.")
                return redirect(request.path)
            if WaterSourceType.objects.filter(name__iexact=name).exists():
                messages.error(request, "Water Source with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description", "").strip()
            is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            WaterSourceType.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Water Source added successfully!")
        elif action == "edit":
            item_id = request.POST.get("id")
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Water Source cannot be empty.")
                return redirect(request.path)
            if WaterSourceType.objects.filter(name__iexact=name).exclude(id=item_id).exists():
                messages.error(request, "Water Source with this name already exists!")
                return redirect(request.path)
            item = WaterSourceType.objects.get(id=item_id)
            item.name = name
            item.description = request.POST.get("description", "").strip()
            item.is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            item.save()
            messages.success(request, "Water Source updated successfully!")
        elif action == "delete":
            item_id = request.POST.get("id")
            WaterSourceType.objects.filter(id=item_id).delete()
            messages.success(request, "Water Source deleted successfully!")

        return redirect("water_source_type")

    items = WaterSourceType.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/water_source_type.html", {"water_sources": items})


def electricity_type(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Electricity Type cannot be empty.")
                return redirect(request.path)
            if ElectricityType.objects.filter(name__iexact=name).exists():
                messages.error(request, "Electricity Type with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description", "").strip()
            is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            ElectricityType.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Electricity Type added successfully!")
        elif action == "edit":
            item_id = request.POST.get("id")
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Electricity Type cannot be empty.")
                return redirect(request.path)
            if ElectricityType.objects.filter(name__iexact=name).exclude(id=item_id).exists():
                messages.error(request, "Electricity Type with this name already exists!")
                return redirect(request.path)
            item = ElectricityType.objects.get(id=item_id)
            item.name = name
            item.description = request.POST.get("description", "").strip()
            item.is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            item.save()
            messages.success(request, "Electricity Type updated successfully!")
        elif action == "delete":
            item_id = request.POST.get("id")
            ElectricityType.objects.filter(id=item_id).delete()
            messages.success(request, "Electricity Type deleted successfully!")

        return redirect("electricity_type")

    items = ElectricityType.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/electricity_type.html", {"electricity_types": items})


def farming_type(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Farming Type cannot be empty.")
                return redirect(request.path)
            if FarmingType.objects.filter(name__iexact=name).exists():
                messages.error(request, "Farming Type with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description", "").strip()
            is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            FarmingType.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Farming Type added successfully!")
        elif action == "edit":
            item_id = request.POST.get("id")
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Farming Type cannot be empty.")
                return redirect(request.path)
            if FarmingType.objects.filter(name__iexact=name).exclude(id=item_id).exists():
                messages.error(request, "Farming Type with this name already exists!")
                return redirect(request.path)
            item = FarmingType.objects.get(id=item_id)
            item.name = name
            item.description = request.POST.get("description", "").strip()
            item.is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            item.save()
            messages.success(request, "Farming Type updated successfully!")
        elif action == "delete":
            item_id = request.POST.get("id")
            FarmingType.objects.filter(id=item_id).delete()
            messages.success(request, "Farming Type deleted successfully!")

        return redirect("farming_type")

    items = FarmingType.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/farming_type.html", {"farming_types": items})


def fencing_type(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Fencing Type cannot be empty.")
                return redirect(request.path)
            if FencingType.objects.filter(name__iexact=name).exists():
                messages.error(request, "Fencing Type with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description", "").strip()
            is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            FencingType.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Fencing Type added successfully!")
        elif action == "edit":
            item_id = request.POST.get("id")
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Fencing Type cannot be empty.")
                return redirect(request.path)
            if FencingType.objects.filter(name__iexact=name).exclude(id=item_id).exists():
                messages.error(request, "Fencing Type with this name already exists!")
                return redirect(request.path)
            item = FencingType.objects.get(id=item_id)
            item.name = name
            item.description = request.POST.get("description", "").strip()
            item.is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            item.save()
            messages.success(request, "Fencing Type updated successfully!")
        elif action == "delete":
            item_id = request.POST.get("id")
            FencingType.objects.filter(id=item_id).delete()
            messages.success(request, "Fencing Type deleted successfully!")

        return redirect("fencing_type")

    items = FencingType.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/fencing_type.html", {"fencing_types": items})


def crop_category_settings(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Crop Category cannot be empty.")
                return redirect(request.path)
            if CropCategory.objects.filter(name__iexact=name).exists():
                messages.error(request, "Crop Category with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description", "").strip()
            is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            CropCategory.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Crop Category added successfully!")
        elif action == "edit":
            item_id = request.POST.get("id")
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Crop Category cannot be empty.")
                return redirect(request.path)
            if CropCategory.objects.filter(name__iexact=name).exclude(id=item_id).exists():
                messages.error(request, "Crop Category with this name already exists!")
                return redirect(request.path)
            item = CropCategory.objects.get(id=item_id)
            item.name = name
            item.description = request.POST.get("description", "").strip()
            item.is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            item.save()
            messages.success(request, "Crop Category updated successfully!")
        elif action == "delete":
            item_id = request.POST.get("id")
            CropCategory.objects.filter(id=item_id).delete()
            messages.success(request, "Crop Category deleted successfully!")

        return redirect("crop_category_settings")

    if not CropCategory.objects.exists():
        default_categories = ['Organic', 'Live stock', 'Agri', 'Horti', 'Trees']
        for cat_name in default_categories:
            CropCategory.objects.create(name=cat_name, description=f"Standard {cat_name} category", is_active=True)

    items = CropCategory.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/crop_category.html", {"crop_categories": items})


def crop_pattern_settings(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Crop Pattern cannot be empty.")
                return redirect(request.path)
            if CropPattern.objects.filter(name__iexact=name).exists():
                messages.error(request, "Crop Pattern with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description", "").strip()
            is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            CropPattern.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Crop Pattern added successfully!")
        elif action == "edit":
            item_id = request.POST.get("id")
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Crop Pattern cannot be empty.")
                return redirect(request.path)
            if CropPattern.objects.filter(name__iexact=name).exclude(id=item_id).exists():
                messages.error(request, "Crop Pattern with this name already exists!")
                return redirect(request.path)
            item = CropPattern.objects.get(id=item_id)
            item.name = name
            item.description = request.POST.get("description", "").strip()
            item.is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            item.save()
            messages.success(request, "Crop Pattern updated successfully!")
        elif action == "delete":
            item_id = request.POST.get("id")
            CropPattern.objects.filter(id=item_id).delete()
            messages.success(request, "Crop Pattern deleted successfully!")

        return redirect("crop_pattern_settings")

    if not CropPattern.objects.exists():
        default_patterns = ['Pandhal', 'Green House', 'Open Field']
        for pat_name in default_patterns:
            CropPattern.objects.create(name=pat_name, description=f"Standard {pat_name} pattern", is_active=True)

    items = CropPattern.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/crop_pattern.html", {"crop_patterns": items})


def crop_variety_settings(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Crop Variety cannot be empty.")
                return redirect(request.path)
            if CropVariety.objects.filter(name__iexact=name).exists():
                messages.error(request, "Crop Variety with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description", "").strip()
            is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            CropVariety.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Crop Variety added successfully!")
        elif action == "edit":
            item_id = request.POST.get("id")
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Crop Variety cannot be empty.")
                return redirect(request.path)
            if CropVariety.objects.filter(name__iexact=name).exclude(id=item_id).exists():
                messages.error(request, "Crop Variety with this name already exists!")
                return redirect(request.path)
            item = CropVariety.objects.get(id=item_id)
            item.name = name
            item.description = request.POST.get("description", "").strip()
            item.is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            item.save()
            messages.success(request, "Crop Variety updated successfully!")
        elif action == "delete":
            item_id = request.POST.get("id")
            CropVariety.objects.filter(id=item_id).delete()
            messages.success(request, "Crop Variety deleted successfully!")

        return redirect("crop_variety_settings")

    items = CropVariety.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/crop_variety.html", {"crop_varieties": items})


def crop_quality_settings(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Crop Quality cannot be empty.")
                return redirect(request.path)
            if CropQuality.objects.filter(name__iexact=name).exists():
                messages.error(request, "Crop Quality with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description", "").strip()
            is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            CropQuality.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Crop Quality added successfully!")
        elif action == "edit":
            item_id = request.POST.get("id")
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Crop Quality cannot be empty.")
                return redirect(request.path)
            if CropQuality.objects.filter(name__iexact=name).exclude(id=item_id).exists():
                messages.error(request, "Crop Quality with this name already exists!")
                return redirect(request.path)
            item = CropQuality.objects.get(id=item_id)
            item.name = name
            item.description = request.POST.get("description", "").strip()
            item.is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            item.save()
            messages.success(request, "Crop Quality updated successfully!")
        elif action == "delete":
            item_id = request.POST.get("id")
            CropQuality.objects.filter(id=item_id).delete()
            messages.success(request, "Crop Quality deleted successfully!")

        return redirect("crop_quality_settings")

    items = CropQuality.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/crop_quality.html", {"crop_qualities": items})


def maintenance_schedule_settings(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Maintenance Schedule cannot be empty.")
                return redirect(request.path)
            if MaintenanceSchedule.objects.filter(name__iexact=name).exists():
                messages.error(request, "Maintenance Schedule with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description", "").strip()
            is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            MaintenanceSchedule.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Maintenance Schedule added successfully!")
        elif action == "edit":
            item_id = request.POST.get("id")
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Maintenance Schedule cannot be empty.")
                return redirect(request.path)
            if MaintenanceSchedule.objects.filter(name__iexact=name).exclude(id=item_id).exists():
                messages.error(request, "Maintenance Schedule with this name already exists!")
                return redirect(request.path)
            item = MaintenanceSchedule.objects.get(id=item_id)
            item.name = name
            item.description = request.POST.get("description", "").strip()
            item.is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            item.save()
            messages.success(request, "Maintenance Schedule updated successfully!")
        elif action == "delete":
            item_id = request.POST.get("id")
            MaintenanceSchedule.objects.filter(id=item_id).delete()
            messages.success(request, "Maintenance Schedule deleted successfully!")

        return redirect("maintenance_schedule_settings")

    items = MaintenanceSchedule.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/maintenance_schedule.html", {"maintenance_schedules": items})


def health_indicator(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Health Indicator cannot be empty.")
                return redirect(request.path)
            if HealthIndicator.objects.filter(name__iexact=name).exists():
                messages.error(request, "Health Indicator with this name already exists!")
                return redirect(request.path)
            description = request.POST.get("description", "").strip()
            is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            HealthIndicator.objects.create(name=name, description=description, is_active=is_active)
            messages.success(request, "Health Indicator added successfully!")
        elif action == "edit":
            item_id = request.POST.get("id")
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Health Indicator cannot be empty.")
                return redirect(request.path)
            if HealthIndicator.objects.filter(name__iexact=name).exclude(id=item_id).exists():
                messages.error(request, "Health Indicator with this name already exists!")
                return redirect(request.path)
            item = HealthIndicator.objects.get(id=item_id)
            item.name = name
            item.description = request.POST.get("description", "").strip()
            item.is_active = request.POST.get("is_active") == "on" or request.POST.get("is_active") is True
            item.save()
            messages.success(request, "Health Indicator updated successfully!")
        elif action == "delete":
            item_id = request.POST.get("id")
            HealthIndicator.objects.filter(id=item_id).delete()
            messages.success(request, "Health Indicator deleted successfully!")

        return redirect("health_indicator")

    items = HealthIndicator.objects.all().order_by("-created_at")
    return render(request, "superadmin/settings/health_indicator.html", {"health_indicators": items})

