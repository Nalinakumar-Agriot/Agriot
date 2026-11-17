# apps/farmer/views.py

import datetime
import os
import random
import re
from datetime import date, timedelta
from decimal import Decimal

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.superadmin.models import BlueprintStage, CropBlueprint, Package, Subscription, HardwareClass
from apps.superadmin.master_utils import get_or_reactivate_master
from auth.models import Profile


from apps.farmer.workspaces import farmer_is_inactive

def get_user_workspaces(user, mobile=None):
    """
    Returns all workspaces available to this user:
    - Personal Farm (if Profile with role='farmer')
    - Managed Farms (if FarmerStaff with is_role_manager=True)
    """
    from auth.portals import is_admin_user
    if not user or not user.is_authenticated or not user.is_active or is_admin_user(user):
        return []
    workspaces = []
    from apps.farmer.models import Farm

    # 1. Personal Farm Workspace (Farmer / Owner)
    has_farmer_role = False
    prof = None
    if hasattr(user, 'profile') and user.profile and user.profile.role == 'farmer' and not farmer_is_inactive(user.profile):
        has_farmer_role = True
        prof = user.profile
    if has_farmer_role and prof:
        farm_name = prof.company_name or (f"{user.first_name}'s Farm" if getattr(user, 'first_name', '') else "My Personal Farm")
        farms_count = Farm.objects.filter(farmer=prof).count()
        workspaces.append({
            'type': 'farmer',
            'target_id': prof.id,
            'farmer_id': prof.id,
            'staff_id': None,
            'title': farm_name,
            'role_label': 'Farm Owner (Farmer)',
            'role_badge': 'Owner',
            'owner_name': user.get_full_name() or user.username,
            'farms_count': farms_count,
            'badge_color': 'success',
            'is_owner': True,
        })

    # 2. Managed Farm Workspaces (Manager Role under Employer)
    from apps.farmer.workspaces import manager_assignments
    staff_qs = manager_assignments(user)

    seen_farmers = set()
    for staff in staff_qs:
        if not staff.farmer or staff.farmer.id in seen_farmers:
            continue
        if has_farmer_role and prof and staff.farmer.id == prof.id:
            continue
        seen_farmers.add(staff.farmer.id)
        employer_name = staff.farmer.company_name or (staff.farmer.user.get_full_name() if staff.farmer.user else "Employer Farm")
        workspaces.append({
            'type': 'manager',
            'target_id': staff.id,
            'staff_id': staff.id,
            'farmer_id': staff.farmer.id,
            'title': employer_name,
            'role_label': f"Manager (at {employer_name})",
            'role_badge': 'Manager',
            'owner_name': employer_name,
            'farms_count': Farm.objects.filter(farmer=staff.farmer).count(),
            'badge_color': 'warning',
            'is_owner': False,
            'permissions': staff.permissions or [],
        })

    return workspaces


def get_farmer_profile(target):
    from apps.farmer.workspaces import resolve_workspace
    return resolve_workspace(target)[0]


def get_active_manager_staff(request):
    from apps.farmer.workspaces import resolve_workspace
    return resolve_workspace(request)[1]


def get_active_manager_staff_id(request):
    staff = get_active_manager_staff(request)
    return staff.id if staff else None


def get_actor_name(request):
    if not request or not hasattr(request, 'user') or not request.user.is_authenticated:
        return "System"
    active_mgr = get_active_manager_staff(request)
    if active_mgr:
        return active_mgr.name
    first = (request.user.first_name or '').strip()
    last = (request.user.last_name or '').strip()
    full = f"{first} {last}".strip()
    if full:
        return full
    if hasattr(request.user, 'profile') and request.user.profile:
        return request.user.profile.mobile_number or request.user.username or "Farmer"
    return request.user.username or "Farmer"



def log_action(request, title, message, icon_class="ri-notification-3-line", bg_color="primary"):
    try:
        from auth.models import SystemNotification
        actor = get_actor_name(request)
        full_msg = f"{message} (Action by: {actor})"

        farmer_prof = get_farmer_profile(request.user)
        target_user = farmer_prof.user if farmer_prof and hasattr(farmer_prof, 'user') else request.user

        SystemNotification.objects.create(
            user=target_user,
            title=title,
            message=full_msg,
            icon_class=icon_class,
            bg_color=bg_color
        )
    except Exception as e:
        print(f"[LOG ACTION ERROR] {e}")


def login_view(request):
    error = request.session.pop("inactive_farmer_message", None)
    if request.method == "POST":
        mobile = request.POST.get('mobile', '').strip()
        clean_phone = re.sub(r'[^0-9+]', '', mobile)
        if clean_phone.count('+') > 1 or clean_phone.find('+') > 0:
            clean_phone = '+' + clean_phone.replace('+', '')
        digits_only = re.sub(r'[^0-9]', '', clean_phone)

        if not digits_only or len(digits_only) < 7 or len(digits_only) > 15:
            error = "Please enter a valid phone number (7 to 15 digits)."
        else:
            from auth.models import Profile
            from apps.farmer.models import FarmerStaff
            from apps.superadmin.phone_utils import find_profile_by_phone, find_staff_by_phone

            phone = clean_phone
            existing_profile = find_profile_by_phone(phone, role='farmer')
            existing_staff = find_staff_by_phone(phone)

            # Restrict: Only Farmers and Managers can log in. Employees do not have login access.
            if existing_staff and not existing_profile:
                is_manager = existing_staff.is_role_manager or (existing_staff.role and str(existing_staff.role).strip().lower() == 'manager')
                if not is_manager:
                    error = "Access denied. Employees do not have login access. Only Managers and Farmers can log in."
                    return render(request, "farmer/login.html", {"error": error})

            if existing_staff and str(existing_staff.status) == "1" and not existing_profile:
                error = "Your account has been suspended/locked. Access is disabled."
                return render(request, "farmer/login.html", {"error": error})

            if existing_profile and farmer_is_inactive(existing_profile) and not get_user_workspaces(existing_profile.user):
                error = "Your farmer account is inactive. Please contact your administrator."
                return render(request, "farmer/login.html", {"error": error})

            is_valid_farmer = (existing_profile and existing_profile.role == 'farmer')
            is_valid_manager = (existing_staff is not None and (existing_staff.is_role_manager or (existing_staff.role and str(existing_staff.role).strip().lower() == 'manager')))

            if not is_valid_farmer and not is_valid_manager:
                if existing_staff:
                    error = "Access denied. Employees do not have login access. Only Managers and Farmers can log in."
                    return render(request, "farmer/login.html", {"error": error})
                else:
                    unreg_digits = digits_only[-10:] if len(digits_only) >= 10 else digits_only
                    error = "This mobile number is not registered yet. Please register as a farmer to get started."
                    return render(request, "farmer/login.html", {
                        "error": error,
                        "not_registered": True,
                        "unregistered_mobile": unreg_digits
                    })

            # Check for cooldown
            last_otp_time = request.session.get('login_otp_time', 0)
            if timezone.now().timestamp() - last_otp_time < 60:
                error = "Please wait at least 60 seconds before requesting a new OTP."
                return render(request, "farmer/login.html", {"error": error})

            # Generate OTP
            otp = str(random.randint(100000, 999999))

            # Send SMS using Msg91 implementation
            target_mobile = (existing_profile.mobile_number if existing_profile else None) or (existing_staff.mobile_number if existing_staff else None) or mobile
            from apps.superadmin.views import send_otp_sms
            success, message = send_otp_sms(target_mobile, otp)
            if not success:
                error = f"Failed to send SMS: {message}"
                return render(request, "farmer/login.html", {"error": error})

            # Store in session
            request.session['login_mobile'] = target_mobile
            request.session['login_display_mobile'] = mobile
            request.session['login_otp'] = otp
            request.session['login_otp_time'] = timezone.now().timestamp()

            return redirect('otp-verify')

    return render(request, "farmer/login.html", {"error": error})

def otp_verify(request):
    mobile = request.session.get('login_mobile')
    display_mobile = request.session.get('login_display_mobile', mobile)
    otp = request.session.get('login_otp')
    # If user is already authenticated but hasn't selected active_role yet (e.g. refreshed account selector)
    if request.user.is_authenticated and not request.session.get('active_role'):
        workspaces = get_user_workspaces(request.user)
        if workspaces and len(workspaces) > 1:
            return render(request, "farmer/otp_verify.html", {
                "show_workspace_modal": True,
                "workspaces": workspaces,
                "user_name": request.user.get_full_name() or request.user.username,
                "mobile": display_mobile or request.user.username
            })
        elif workspaces and len(workspaces) == 1:
            ws = workspaces[0]
            request.session['active_role'] = ws['type']
            request.session['active_farmer_id'] = ws['farmer_id']
            request.session['active_staff_id'] = ws.get('staff_id')
            return redirect('farmer-dashboard')
        else:
            from django.contrib.auth import logout
            logout(request)
            request.session.flush()
            return redirect('farmer-login')

    if not mobile:
        if request.user.is_authenticated:
            from django.contrib.auth import logout
            logout(request)
            request.session.flush()
        return redirect('farmer-login')

    error = None
    if request.method == "POST":
        otp_entered = request.POST.get('otp', '').strip()
        session_otp = request.session.get('login_otp')
        otp_time = request.session.get('login_otp_time')

        if not session_otp or not otp_time:
            error = "Session expired. Please request OTP again."
        elif timezone.now().timestamp() - otp_time > 420: # 7 mins expiry
            error = "OTP expired. Please request again."
        elif otp_entered == session_otp:
            # Valid OTP, find or create user
            from apps.superadmin.phone_utils import find_profile_by_phone, find_staff_by_phone
            from auth.models import Profile
            from apps.farmer.models import FarmerStaff

            # 1. Resolve or create User
            existing_profile = find_profile_by_phone(mobile, role='farmer')
            staff_obj = find_staff_by_phone(mobile)

            user = None
            if existing_profile and existing_profile.user:
                user = existing_profile.user
            elif staff_obj and staff_obj.user:
                user = staff_obj.user
            else:
                user = User.objects.filter(username=mobile).first()
                if not user:
                    user = User.objects.create(username=mobile)
                    user.set_unusable_password()
                    user.save()

            # Ensure staff_obj is linked to user if staff_obj exists
            if staff_obj and not staff_obj.user:
                staff_obj.user = user
                staff_obj.save(update_fields=['user'])

            # Ensure profile has mobile_number and appropriate role
            if hasattr(user, 'profile') and user.profile:
                updated_fields = []
                if not user.profile.mobile_number:
                    user.profile.mobile_number = mobile
                    updated_fields.append('mobile_number')
                if staff_obj and not existing_profile and user.profile.role != 'staff':
                    user.profile.role = 'staff'
                    updated_fields.append('role')
                if updated_fields:
                    user.profile.save(update_fields=updated_fields)

            # 2. Check suspensions
            if existing_profile and farmer_is_inactive(existing_profile) and not get_user_workspaces(existing_profile.user):
                error = "Your farmer account is inactive. Please contact your administrator."
                return render(request, "farmer/otp_verify.html", {"error": error, "mobile": display_mobile})

            if staff_obj and str(staff_obj.status) == "1":
                # Only block if user does not also have a valid active farmer profile
                if not (existing_profile and not farmer_is_inactive(existing_profile)):
                    error = "Your account has been suspended/locked. Access is disabled."
                    return render(request, "farmer/otp_verify.html", {"error": error, "mobile": display_mobile})

            # 3. Detect available workspaces
            workspaces = get_user_workspaces(user, mobile=mobile)

            if not workspaces:
                error = "Unauthorized mobile number. Only registered farmers and managers can login."
                return render(request, "farmer/otp_verify.html", {"error": error, "mobile": display_mobile})

            # Log the user in
            login(request, user)

            # Clear session
            for k in ['login_mobile', 'login_display_mobile', 'login_otp', 'login_otp_time', 'active_role', 'active_farmer_id', 'active_staff_id']:
                if k in request.session:
                    del request.session[k]

            # 4. If single workspace -> auto-select and proceed
            if len(workspaces) == 1:
                ws = workspaces[0]
                request.session['active_role'] = ws['type']
                request.session['active_farmer_id'] = ws['farmer_id']
                request.session['active_staff_id'] = ws.get('staff_id')
                return redirect('farmer-dashboard')

            # 5. Dual-Role (Multiple workspaces) -> render modal popup directly on the OTP page!
            return render(request, "farmer/otp_verify.html", {
                "show_workspace_modal": True,
                "workspaces": workspaces,
                "user_name": user.get_full_name() or user.username,
                "mobile": display_mobile
            })
        else:
            error = "Invalid OTP. Please try again."

    return render(request, "farmer/otp_verify.html", {"error": error, "mobile": display_mobile})


def select_workspace_view(request):
    """
    Renders the Role / Workspace Selection page when a user has multiple workspaces
    (e.g. Owner of personal farm AND Farm Manager for an employer).
    """
    if not request.user.is_authenticated:
        return redirect('farmer-login')

    workspaces = get_user_workspaces(request.user)
    if not workspaces:
        messages.error(request, "No active farm workspaces found for your account.")
        return farmer_logout(request)

    if len(workspaces) == 1 and not request.GET.get('force'):
        ws = workspaces[0]
        request.session['active_role'] = ws['type']
        request.session['active_farmer_id'] = ws['farmer_id']
        request.session['active_staff_id'] = ws.get('staff_id')
        return redirect('farmer-dashboard')

    # Handle selection
    if request.method == "POST":
        selected_type = request.POST.get('workspace_type')
        target_id = request.POST.get('target_id')
        try:
            target_id = int(target_id)
        except (ValueError, TypeError):
            target_id = None

        matched_ws = next((w for w in workspaces if w['type'] == selected_type and w['target_id'] == target_id), None)
        if matched_ws:
            request.session['active_role'] = matched_ws['type']
            request.session['active_farmer_id'] = matched_ws['farmer_id']
            request.session['active_staff_id'] = matched_ws.get('staff_id')
            role_msg = "Personal Farm" if matched_ws['type'] == 'farmer' else f"Manager at {matched_ws['title']}"
            messages.success(request, f"Switched to workspace: {matched_ws['title']} ({role_msg})")
            return redirect('farmer-dashboard')
        else:
            messages.error(request, "Invalid workspace selection.")

    active_role = request.session.get('active_role')
    active_target_id = request.session.get('active_staff_id') if active_role == 'manager' else request.session.get('active_farmer_id')

    for ws in workspaces:
        ws['is_active'] = (ws['type'] == active_role and ws['target_id'] == active_target_id)

    can_register_own_farm = not any(w['type'] == 'farmer' for w in workspaces)

    context = {
        'workspaces': workspaces,
        'user_name': request.user.get_full_name() or request.user.username,
        'can_register_own_farm': can_register_own_farm,
    }
    return render(request, "farmer/auth/select_workspace.html", context)


def switch_workspace_view(request, role, target_id):
    """
    Fast workspace switcher endpoint called from the top navigation dropdown.
    Validates ownership and switches active workspace without requiring re-login.
    """
    if not request.user.is_authenticated:
        return redirect('farmer-login')

    workspaces = get_user_workspaces(request.user)
    try:
        target_id = int(target_id)
    except (ValueError, TypeError):
        target_id = None

    matched_ws = next((w for w in workspaces if w['type'] == role and w['target_id'] == target_id), None)
    if matched_ws:
        request.session['active_role'] = matched_ws['type']
        request.session['active_farmer_id'] = matched_ws['farmer_id']
        request.session['active_staff_id'] = matched_ws.get('staff_id')
        role_label = "Personal Farm" if matched_ws['type'] == 'farmer' else f"Manager at {matched_ws['title']}"
        messages.success(request, f"Active workspace changed to: {matched_ws['title']} ({role_label})")
    else:
        messages.error(request, "Selected workspace is not available on your account.")

    next_url = request.META.get('HTTP_REFERER') or '/farmer/dashboard/'
    if 'select-workspace' in next_url:
        next_url = '/farmer/dashboard/'
    return redirect(next_url)

def resend_login_otp(request):
    mobile = request.session.get('login_mobile')
    display_mobile = request.session.get('login_display_mobile', mobile)
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or (request.content_type and 'application/json' in request.content_type)

    if not mobile:
        if is_ajax:
            return JsonResponse({'status': 'error', 'message': 'Session expired. Please enter your mobile number again.'}, status=400)
        messages.error(request, 'Session expired. Please enter your mobile number again.')
        return redirect('farmer-login')

    # Check cooldown (30 seconds)
    last_otp_time = request.session.get('login_otp_time', 0)
    current_time = timezone.now().timestamp()
    if current_time - last_otp_time < 30:
        remaining = max(1, int(30 - (current_time - last_otp_time)))
        msg = f"Please wait {remaining} seconds before requesting a new OTP."
        if is_ajax:
            return JsonResponse({'status': 'error', 'message': msg}, status=429)
        messages.warning(request, msg)
        return redirect('otp-verify')

    # Generate new 6-digit OTP
    otp = str(random.randint(100000, 999999))

    # Send SMS using Msg91 implementation
    from apps.superadmin.views import send_otp_sms
    success, message = send_otp_sms(mobile, otp)
    if not success:
        msg = f"Failed to send SMS: {message}"
        if is_ajax:
            return JsonResponse({'status': 'error', 'message': msg}, status=500)
        messages.error(request, msg)
        return redirect('otp-verify')

    # Update session with new OTP and timestamp
    request.session['login_otp'] = otp
    request.session['login_otp_time'] = current_time

    if is_ajax:
        return JsonResponse({
            'status': 'success',
            'message': f'New OTP sent successfully to {display_mobile or mobile}.'
        })

    messages.success(request, f'New OTP sent successfully to {display_mobile or mobile}.')
    return redirect('otp-verify')

def farmer_logout(request):
    from django.contrib.auth import logout
    logout(request)
    request.session.flush()
    return redirect('farmer-login')


def get_wmo_weather_info(code):
    mapping = {
        0: ('Clear Sunny Sky', 'ri-sun-line'),
        1: ('Mainly Clear', 'ri-sun-cloudy-line'),
        2: ('Partly Cloudy', 'ri-sun-cloudy-line'),
        3: ('Cloudy & Overcast', 'ri-cloudy-line'),
        45: ('Foggy Atmosphere', 'ri-mist-line'),
        48: ('Depositing Rime Fog', 'ri-mist-line'),
        51: ('Light Drizzle', 'ri-drizzle-line'),
        53: ('Moderate Drizzle', 'ri-drizzle-line'),
        55: ('Dense Drizzle', 'ri-drizzle-line'),
        61: ('Slight Rain Showers', 'ri-rainy-line'),
        63: ('Moderate Rain', 'ri-rainy-line'),
        65: ('Heavy Rainfall', 'ri-rainy-line'),
        80: ('Passing Rain Showers', 'ri-showers-line'),
        81: ('Moderate Rain Showers', 'ri-showers-line'),
        82: ('Violent Rain Showers', 'ri-showers-line'),
        95: ('Thunderstorms Anticipated', 'ri-thunderstorms-line'),
        96: ('Thunderstorms with Hail', 'ri-thunderstorms-line'),
        99: ('Heavy Thunderstorms with Hail', 'ri-thunderstorms-line'),
    }
    return mapping.get(code, ('Partly Cloudy', 'ri-sun-cloudy-line'))


def fetch_live_weather(lat=9.9252, lon=78.1198, loc_name="Madurai, Tamil Nadu", base_date=None):
    import json
    import urllib.request
    from datetime import datetime, date, timedelta

    today = date.today()
    if isinstance(base_date, str):
        try:
            base_date = datetime.strptime(base_date, "%Y-%m-%d").date()
        except Exception:
            base_date = today
    elif not base_date:
        base_date = today

    start_str = base_date.strftime("%Y-%m-%d")
    end_str = (base_date + timedelta(days=6)).strftime("%Y-%m-%d")

    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m"
        f"&daily=weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,uv_index_max"
        f"&hourly=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation_probability,precipitation,wind_speed_10m,uv_index"
        f"&start_date={start_str}&end_date={end_str}&timezone=auto"
    )

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'AgriOT-Weather/1.0'})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        curr_raw = data.get('current', {})
        daily_raw = data.get('daily', {})
        hourly_raw = data.get('hourly', {})

        curr_code = curr_raw.get('weather_code', 2)
        curr_cond, curr_icon = get_wmo_weather_info(curr_code)

        daily_times = daily_raw.get('time', [])
        max_temps = daily_raw.get('temperature_2m_max', [])
        min_temps = daily_raw.get('temperature_2m_min', [])
        app_max = daily_raw.get('apparent_temperature_max', [])
        precip_sums = daily_raw.get('precipitation_sum', [])
        precip_probs = daily_raw.get('precipitation_probability_max', [])
        wind_maxs = daily_raw.get('wind_speed_10m_max', [])
        uv_maxs = daily_raw.get('uv_index_max', [])
        w_codes = daily_raw.get('weather_code', [])

        forecast_days = []
        for i in range(min(7, len(daily_times))):
            d_str = daily_times[i]
            d_obj = datetime.strptime(d_str, "%Y-%m-%d").date()
            label = "Today" if d_obj == today else d_obj.strftime("%a")
            code = w_codes[i] if i < len(w_codes) else 2
            cond, icon = get_wmo_weather_info(code)

            t_high = int(round(max_temps[i])) if i < len(max_temps) and max_temps[i] is not None else 32
            t_low = int(round(min_temps[i])) if i < len(min_temps) and min_temps[i] is not None else 24
            feels = int(round(app_max[i])) if i < len(app_max) and app_max[i] is not None else (t_high + 2)
            rain_pct = int(precip_probs[i]) if i < len(precip_probs) and precip_probs[i] is not None else 20
            rain_mm = round(float(precip_sums[i]), 1) if i < len(precip_sums) and precip_sums[i] is not None else 0.0
            wind_spd = int(round(wind_maxs[i])) if i < len(wind_maxs) and wind_maxs[i] is not None else 12
            uv_val = int(round(uv_maxs[i])) if i < len(uv_maxs) and uv_maxs[i] is not None else 6

            forecast_days.append({
                'label': label,
                'date': d_obj.strftime("%d %b"),
                'full_date': d_str,
                'condition': cond,
                'icon_class': icon,
                'icon': icon,
                'temp_high': t_high,
                'temp_low': t_low,
                'feels_like': feels,
                'rain_chance': rain_pct,
                'rain': rain_pct,
                'rain_mm': rain_mm,
                'humidity': int(curr_raw.get('relative_humidity_2m', 70)) if curr_raw else 70,
                'wind_speed': wind_spd,
                'wind': wind_spd,
                'uv_index': uv_val,
                'uv': uv_val,
            })

        # Process 24-hr hourly data (24 samples from 12:00 AM to 11:00 PM for each day)
        h_temps = hourly_raw.get('temperature_2m', [])
        h_feels = hourly_raw.get('apparent_temperature', [])
        h_hum = hourly_raw.get('relative_humidity_2m', [])
        h_wind = hourly_raw.get('wind_speed_10m', [])
        h_prob = hourly_raw.get('precipitation_probability', [])
        h_precip = hourly_raw.get('precipitation', [])
        h_uv = hourly_raw.get('uv_index', [])

        hourly_labels = [
            '12:00 AM', '01:00 AM', '02:00 AM', '03:00 AM', '04:00 AM', '05:00 AM',
            '06:00 AM', '07:00 AM', '08:00 AM', '09:00 AM', '10:00 AM', '11:00 AM',
            '12:00 PM', '01:00 PM', '02:00 PM', '03:00 PM', '04:00 PM', '05:00 PM',
            '06:00 PM', '07:00 PM', '08:00 PM', '09:00 PM', '10:00 PM', '11:00 PM'
        ]

        def extract_day_hourly(day_idx):
            start_i = day_idx * 24
            end_i = start_i + 24
            s_temps = []
            s_feels = []
            s_hums = []
            s_winds = []
            s_probs = []
            s_precips = []
            s_uvs = []
            for idx in range(start_i, end_i):
                if idx < len(h_temps):
                    s_temps.append(int(round(h_temps[idx])) if h_temps[idx] is not None else 30)
                    s_feels.append(int(round(h_feels[idx])) if h_feels[idx] is not None else 32)
                    s_hums.append(int(round(h_hum[idx])) if h_hum[idx] is not None else 70)
                    s_winds.append(int(round(h_wind[idx])) if h_wind[idx] is not None else 10)
                    s_probs.append(int(round(h_prob[idx])) if h_prob[idx] is not None else 15)
                    s_precips.append(round(float(h_precip[idx]), 1) if h_precip[idx] is not None else 0.0)
                    s_uvs.append(int(round(h_uv[idx])) if h_uv[idx] is not None else 0)
                else:
                    s_temps.append(30)
                    s_feels.append(32)
                    s_hums.append(70)
                    s_winds.append(10)
                    s_probs.append(15)
                    s_precips.append(0.0)
                    s_uvs.append(0)
            return {
                'labels': hourly_labels,
                'temp': s_temps,
                'feels_like': s_feels,
                'humidity': s_hums,
                'wind': s_winds,
                'rain_prob': s_probs,
                'precip_mm': s_precips,
                'uv': s_uvs
            }

        hourly_dataset = extract_day_hourly(0)
        hourly_by_day = [extract_day_hourly(d) for d in range(min(7, len(forecast_days)))]

        if base_date == today and curr_raw:
            curr_temp = int(round(curr_raw.get('temperature_2m', 31)))
            curr_cond_str = curr_cond
            curr_feels = int(round(curr_raw.get('apparent_temperature', 34)))
            curr_hum = int(round(curr_raw.get('relative_humidity_2m', 75)))
            curr_wind = int(round(curr_raw.get('wind_speed_10m', 14)))
            curr_rain = forecast_days[0]['rain_chance'] if forecast_days else 20
            curr_uv = forecast_days[0]['uv_index'] if forecast_days else 6
            curr_icon_cls = curr_icon
        else:
            day0 = forecast_days[0] if forecast_days else {}
            curr_temp = day0.get('temp_high', 32)
            curr_cond_str = day0.get('condition', curr_cond)
            curr_feels = day0.get('feels_like', curr_temp + 2)
            curr_hum = day0.get('humidity', 70)
            curr_wind = day0.get('wind_speed', 12)
            curr_rain = day0.get('rain_chance', 20)
            curr_uv = day0.get('uv_index', 6)
            curr_icon_cls = day0.get('icon_class', curr_icon)

        current_weather = {
            'location': loc_name,
            'temp': curr_temp,
            'condition': curr_cond_str,
            'feels_like': curr_feels,
            'humidity': curr_hum,
            'wind_speed': curr_wind,
            'rain_chance': curr_rain,
            'uv_index': curr_uv,
            'icon_class': curr_icon_cls,
        }

        return {
            'is_live': True,
            'current': current_weather,
            'forecast': forecast_days,
            'hourly': hourly_dataset,
            'hourly_by_day': hourly_by_day
        }

    except Exception:
        # Fallback using base_date or today
        fallback_days = []
        weather_icons = [
            'ri-sun-cloudy-line', 'ri-cloudy-line', 'ri-thunderstorms-line', 
            'ri-rainy-line', 'ri-sun-line', 'ri-sun-cloudy-line', 'ri-cloudy-line'
        ]
        conditions = [
            'Partly Cloudy', 'Cloudy & Overcast', 'Thunderstorms Anticipated',
            'Heavy Rainfall', 'Clear Sunny Sky', 'Scattered Clouds', 'Passing Showers'
        ]
        highs = [33, 31, 29, 28, 32, 33, 30]
        lows = [26, 25, 24, 23, 25, 26, 24]
        feels_likes = [34, 32, 30, 29, 35, 36, 31]
        rain_chances = [40, 55, 72, 80, 20, 35, 50]
        humidities = [78, 82, 88, 92, 65, 70, 75]
        winds = [14, 16, 22, 28, 12, 10, 15]
        uv_indices = [6, 5, 4, 3, 8, 9, 7]

        for i in range(7):
            d_obj = base_date + timedelta(days=i)
            day_label = "Today" if d_obj == today else d_obj.strftime("%a")
            fallback_days.append({
                'label': day_label,
                'date': d_obj.strftime("%d %b"),
                'full_date': d_obj.strftime("%Y-%m-%d"),
                'condition': conditions[i % len(conditions)],
                'icon_class': weather_icons[i % len(weather_icons)],
                'icon': weather_icons[i % len(weather_icons)],
                'temp_high': highs[i % len(highs)],
                'temp_low': lows[i % len(lows)],
                'feels_like': feels_likes[i % len(feels_likes)],
                'rain_chance': rain_chances[i % len(rain_chances)],
                'rain': rain_chances[i % len(rain_chances)],
                'rain_mm': 5,
                'humidity': humidities[i % len(humidities)],
                'wind_speed': winds[i % len(winds)],
                'wind': winds[i % len(winds)],
                'uv_index': uv_indices[i % len(uv_indices)],
                'uv': uv_indices[i % len(uv_indices)],
            })

        return {
            'is_live': False,
            'current': {
                'location': loc_name,
                'temp': 32,
                'condition': 'Partly Cloudy',
                'feels_like': 35,
                'humidity': 75,
                'wind_speed': 14,
                'rain_chance': 35,
                'uv_index': 6,
                'icon_class': 'ri-sun-cloudy-line',
            },
            'forecast': fallback_days,
            'hourly': {
                'labels': [
                    '12:00 AM', '01:00 AM', '02:00 AM', '03:00 AM', '04:00 AM', '05:00 AM',
                    '06:00 AM', '07:00 AM', '08:00 AM', '09:00 AM', '10:00 AM', '11:00 AM',
                    '12:00 PM', '01:00 PM', '02:00 PM', '03:00 PM', '04:00 PM', '05:00 PM',
                    '06:00 PM', '07:00 PM', '08:00 PM', '09:00 PM', '10:00 PM', '11:00 PM'
                ],
                'temp': [24, 23, 23, 24, 25, 27, 29, 31, 32, 33, 33, 32, 31, 30, 29, 28, 27, 26, 26, 25, 25, 24, 24, 24],
                'feels_like': [26, 25, 25, 26, 27, 29, 32, 34, 35, 36, 36, 35, 34, 33, 31, 30, 29, 28, 28, 27, 27, 26, 26, 26],
                'humidity': [90, 92, 92, 90, 85, 78, 72, 65, 62, 60, 62, 65, 70, 75, 78, 82, 85, 86, 88, 90, 90, 91, 91, 91],
                'wind': [6, 6, 5, 5, 7, 9, 12, 14, 16, 17, 16, 15, 14, 12, 11, 10, 9, 8, 8, 7, 7, 6, 6, 6],
                'rain_prob': [5, 5, 5, 5, 10, 15, 20, 25, 30, 35, 30, 25, 20, 15, 15, 10, 10, 5, 5, 5, 5, 5, 5, 5],
                'precip_mm': [0, 0, 0, 0, 0, 0, 0, 0.2, 0.5, 0.8, 0.4, 0.2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                'uv': [0, 0, 0, 0, 0, 1, 3, 5, 7, 9, 8, 6, 4, 2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0]
            },
            'hourly_by_day': []
        }


def get_live_weather_api(request):
    """
    Returns live weather forecast data as JSON for the farmer dashboard.
    Supports query parameters:
      - lat: float (latitude)
      - lon: float (longitude)
      - loc_name: string (location display name)
      - date: YYYY-MM-DD (selected date)
    """
    lat = request.GET.get('lat')
    lon = request.GET.get('lon')
    loc_name = request.GET.get('loc_name', '').strip()
    date_str = request.GET.get('date', '').strip()

    if not lat or not lon:
        profile = get_farmer_profile(request.user) if request.user.is_authenticated else None
        if profile:
            from apps.farmer.models import Farm
            first_farm = Farm.objects.filter(farmer=profile).first()
            if first_farm and first_farm.latitude and first_farm.longitude:
                lat = first_farm.latitude
                lon = first_farm.longitude
                if not loc_name:
                    d_name = first_farm.district.name if first_farm.district else ''
                    s_name = first_farm.state.name if first_farm.state else 'Tamil Nadu'
                    loc_name = f"{d_name}, {s_name}" if d_name else s_name

    try:
        lat = float(lat) if lat else 9.9252
        lon = float(lon) if lon else 78.1198
    except (ValueError, TypeError):
        lat = 9.9252
        lon = 78.1198

    if not loc_name:
        loc_name = "Madurai, Tamil Nadu"

    weather_data = fetch_live_weather(lat=lat, lon=lon, loc_name=loc_name, base_date=date_str)
    return JsonResponse({
        'status': 'success',
        'is_live': weather_data.get('is_live', False),
        'weather': weather_data
    })


def dashboard(request):
    import json
    from datetime import timedelta
    from django.db.models import Sum, Q, Count, F
    from django.db.models.functions import Abs
    from django.utils import timezone
    from apps.farmer.models import (
        Farm, CropSplit, FarmTask, FarmActivity, FarmerStaff, 
        StaffAttendance, StaffPayroll, FarmerInventory, 
        LedgerTransaction, FarmerOutstanding, FarmerCustomer, FarmerVendor
    )
    from apps.superadmin.models import Subscription, MarketPriceIndex, DepotCropPricing, DepotTerminal, Crop

    profile = get_farmer_profile(request.user)
    today_date = timezone.localdate()

    # Format INR helper
    def format_inr(val):
        try:
            val_num = float(val)
            is_neg = val_num < 0
            val_num = abs(val_num)
            s = f"{int(round(val_num))}"
            if len(s) > 3:
                last3 = s[-3:]
                rest = s[:-3]
                groups = []
                while len(rest) > 2:
                    groups.insert(0, rest[-2:])
                    rest = rest[:-2]
                if rest:
                    groups.insert(0, rest)
                formatted = ','.join(groups) + ',' + last3
            else:
                formatted = s
            return f"-₹{formatted}" if is_neg else f"₹{formatted}"
        except Exception:
            return "₹0"

    # ---- Active Manager Check ----
    active_mgr = get_active_manager_staff(request)

    # ---- 1. Farms & Total Acreage ----
    farms = Farm.objects.filter(farmer=profile).select_related('district', 'state') if profile else Farm.objects.none()
    if active_mgr:
        farms = farms.filter(
            Q(created_by_staff=active_mgr) | Q(splits__supervisors=active_mgr) | Q(splits__supervisor=active_mgr)
        ).distinct()
    total_farms = farms.count()
    total_acres = 0.0
    for f in farms:
        if f.total_area:
            try:
                clean_area = ''.join(c for c in str(f.total_area) if c.isdigit() or c == '.')
                if clean_area:
                    total_acres += float(clean_area)
            except (ValueError, TypeError):
                pass
    formatted_acres = int(total_acres) if total_acres.is_integer() else round(total_acres, 1)
    first_farm = farms.first()

    # ---- 2. Crops (Active & Harvested) ----
    crop_splits = CropSplit.objects.filter(farm__farmer=profile).select_related('crop', 'farm') if profile else CropSplit.objects.none()
    if active_mgr:
        crop_splits = crop_splits.filter(
            Q(supervisors=active_mgr) | Q(supervisor=active_mgr) | Q(farm__created_by_staff=active_mgr)
        ).distinct()
    active_crops = crop_splits.exclude(stage__in=['Harvested', 'Sold', 'Inactive']).count()
    harvested_crops = crop_splits.filter(stage__in=['Harvested', 'Sold']).count()

    # ---- 3. Tasks ----
    tasks = FarmTask.objects.filter(Q(farmer=profile) | Q(farm__farmer=profile)).distinct() if profile else FarmTask.objects.none()
    if active_mgr:
        tasks = tasks.filter(
            Q(assigned_workers=active_mgr) | 
            Q(assigned_worker=active_mgr) | 
            Q(associated_split__supervisors=active_mgr) |
            Q(associated_split__supervisor=active_mgr)
        ).distinct()
    total_tasks = tasks.count()
    pending_tasks = tasks.filter(status='Pending').count()
    in_progress_tasks = tasks.filter(status='In Progress').count()
    completed_tasks = tasks.filter(status='Completed').count()

    pending_pct = round((pending_tasks / total_tasks * 100)) if total_tasks > 0 else 0
    in_progress_pct = round((in_progress_tasks / total_tasks * 100)) if total_tasks > 0 else 0
    completed_pct = round((completed_tasks / total_tasks * 100)) if total_tasks > 0 else 0

    # ---- 4. Workforce & Attendance ----
    staff_qs = FarmerStaff.objects.filter(farmer=profile) if profile else FarmerStaff.objects.none()
    total_staff = staff_qs.count()
    active_staff = staff_qs.filter(status='0').count()
    suspended_staff = staff_qs.exclude(status='0').count()

    attendances_today = StaffAttendance.objects.filter(staff__farmer=profile, date=today_date) if profile else StaffAttendance.objects.none()
    if attendances_today.exists():
        present_today = attendances_today.filter(status='P').count()
        absent_today = attendances_today.filter(status='A').count()
        leave_today = attendances_today.filter(status__in=['H', 'L']).count()
    else:
        present_today = active_staff
        absent_today = suspended_staff
        leave_today = 0

    attendance_pct = round((present_today / total_staff * 100)) if total_staff > 0 else 0
    leave_pct = round((leave_today / total_staff * 100)) if total_staff > 0 else 0
    absent_pct = round((absent_today / total_staff * 100)) if total_staff > 0 else 0

    # ---- 5. Financial Overview (Strictly aligned with Ledger and Outstandings) ----
    transactions = LedgerTransaction.objects.filter(farmer=profile).exclude(
        category='Harvest Trade',
        description__istartswith='Harvest Trade Settlement'
    ) if profile else LedgerTransaction.objects.none()

    # Sales (Inflow transactions excluding internal customer duplicate entries)
    inflow = transactions.filter(
        Q(tags__icontains='Inflow') | Q(amount__gt=0) | Q(status='Received')
    ).exclude(
        category='Customer Payment'
    ).distinct()
    total_sales = float(inflow.aggregate(total=Sum(Abs('amount')))['total'] or 0)

    # Expenses (Outflow transactions including labor, seed, vendor & rental costs)
    outflow = transactions.filter(
        Q(tags__icontains='Outflow') | Q(amount__lt=0) | Q(status__in=['Paid', 'Cost'])
    ).distinct()
    total_expenses = float(outflow.aggregate(total=Sum(Abs('amount')))['total'] or 0)

    # Net Profit
    net_profit = total_sales - total_expenses

    # Inventory Value
    inventories = FarmerInventory.objects.filter(farmer=profile) if profile else FarmerInventory.objects.none()
    inventory_value = sum(float(i.quantity or 0) * float(i.unit_price or 0) for i in inventories)

    # Receivables (Customer unpaid/partial invoices)
    outstandings = FarmerOutstanding.objects.filter(farmer=profile) if profile else FarmerOutstanding.objects.none()
    customer_outs = outstandings.exclude(invoice_no__startswith='Vendor:').exclude(status='Paid')
    receivables = sum(max(0.0, float(o.amount_total) - float(o.amount_paid)) for o in customer_outs)

    # Payables (Vendor unpaid/partial liabilities)
    vendor_outs = outstandings.filter(invoice_no__startswith='Vendor:').exclude(status='Paid')
    payables = sum(max(0.0, float(o.amount_total) - float(o.amount_paid)) for o in vendor_outs)

    # Total Business Value = Inventory Value + Total Realized Sales + Pending Receivables
    total_business_value = inventory_value + total_sales + receivables

    # Overdue Invoices
    overdue_invoices_count = outstandings.filter(due_date__lt=today_date).exclude(status='Paid').count()
    all_invoices_count = outstandings.count()
    overdue_percentage = min(100, round((overdue_invoices_count / max(1, all_invoices_count)) * 100)) if all_invoices_count > 0 else 0

    # ---- 6. Subscription ----
    from apps.superadmin.subscription_utils import get_farmer_subscription_details
    sub_details = get_farmer_subscription_details(profile) if profile else None
    sub = sub_details.get('subscription') if sub_details else None

    if sub and not sub_details.get('is_pending'):
        plan_name = sub.package.name if sub.package else "Standard Plan"
        plan_price = float(sub.monthly_amount or (sub.package.price if sub.package else 0))
        renews_on = sub.end_date.strftime("%b %d, %Y") if sub.end_date else "N/A"
        days_left = max(0, (sub.end_date - today_date).days) if sub.end_date else 0
        auto_renew = sub.auto_renew
    else:
        plan_name = "No Active Plan"
        plan_price = 0.0
        renews_on = "N/A"
        days_left = 0
        auto_renew = False

    # ---- 7. Agronomy: Weather Forecast & Farm Health ----
    # Location (City / District based)
    # ---- 7. Agronomy: Weather Forecast & Farm Health ----
    # Location (City / District based)
    loc_name = "Madurai, Tamil Nadu"
    if profile:
        city_val = getattr(profile, 'city', None) or getattr(profile, 'district', None)
        state_val = getattr(profile, 'state', None) or 'Tamil Nadu'
        if city_val:
            loc_name = f"{city_val}, {state_val}"
    if loc_name == "Madurai, Tamil Nadu" and first_farm:
        if first_farm.district:
            loc_name = f"{first_farm.district.name}, {first_farm.state.name if first_farm.state else 'Tamil Nadu'}"
        elif first_farm.state:
            loc_name = f"{first_farm.state.name}, India"

    farm_lat = 9.9252
    farm_lon = 78.1198
    if first_farm and getattr(first_farm, 'latitude', None) and getattr(first_farm, 'longitude', None):
        try:
            farm_lat = float(first_farm.latitude)
            farm_lon = float(first_farm.longitude)
        except (ValueError, TypeError):
            pass

    weather = fetch_live_weather(lat=farm_lat, lon=farm_lon, loc_name=loc_name, base_date=today_date)
    weather_forecast_json = json.dumps(weather.get('forecast', []))
    weather_hourly_json = json.dumps(weather.get('hourly', {}))
    weather_hourly_by_day_json = json.dumps(weather.get('hourly_by_day', []))

    # Farm Health & Soil Indicators
    healthy_farms_count = farms.filter(health_indicator__in=['Good', 'Healthy', 'Optimal']).count()
    needs_attention_farms_count = farms.filter(health_indicator__in=['Moderate', 'Needs Attention', 'Fair']).count()
    critical_farms_count = farms.filter(health_indicator__in=['Poor', 'Critical']).count()

    if total_farms > 0:
        if healthy_farms_count == 0 and needs_attention_farms_count == 0 and critical_farms_count == 0:
            healthy_farms_count = total_farms
        farm_health_score = round((healthy_farms_count / total_farms) * 100)
    else:
        farm_health_score = 0
        healthy_farms_count = 0
        needs_attention_farms_count = 0
        critical_farms_count = 0

    # Soil Metrics & Crop Chips
    ph_values = []
    for f in farms:
        if f.soil_ph:
            try:
                clean_ph = ''.join(c for c in str(f.soil_ph) if c.isdigit() or c == '.')
                if clean_ph:
                    ph_values.append(float(clean_ph))
            except Exception:
                pass
    avg_soil_ph = f"{round(sum(ph_values) / len(ph_values), 1)}" if ph_values else "N/A"

    crop_health_chips = []
    if farms.exists():
        for f in farms[:6]:
            status = f.health_indicator or "Good"
            color = "var(--elite-emerald)" if status in ['Good', 'Healthy', 'Optimal'] else ("var(--elite-amber)" if status in ['Moderate', 'Needs Attention', 'Fair'] else "var(--elite-rose)")
            crop_health_chips.append({
                'name': f.land_name,
                'status': status,
                'color': color,
            })
    else:
        crop_health_chips = []

    # ---- 8. Inventory & Fleet Details ----
    inventory_items = []
    category_icons = {
        'Seed': ('ri-seedling-line', 'var(--elite-emerald)'),
        'Fertilizer': ('ri-flask-line', 'var(--elite-amber)'),
        'Pesticide': ('ri-flask-line', 'var(--elite-rose)'),
        'Fuel': ('ri-gas-station-line', 'var(--elite-sky)'),
        'Irrigation': ('ri-git-merge-line', 'var(--elite-violet)'),
        'Machinery': ('ri-settings-3-line', '#12172b'),
        'Vehicle': ('ri-truck-line', 'var(--elite-indigo)'),
        'Tool': ('ri-tools-line', 'var(--elite-violet)'),
    }

    for item in inventories.order_by('-id')[:6]:
        qty = float(item.quantity or 0)
        safety = float(item.safety_stock or 5)
        unit_p = float(item.unit_price or 0)
        tot_val = qty * unit_p
        is_low = qty <= safety

        # icon detection
        icon_class = 'ri-archive-2-line'
        icon_bg = 'var(--elite-emerald)'
        for cat_key, (ic, bg) in category_icons.items():
            if cat_key.lower() in str(item.category).lower():
                icon_class = ic
                icon_bg = bg
                break

        stock_pct = min(100, int((qty / max(1, safety * 2)) * 100)) if safety > 0 else 100

        inventory_items.append({
            'item_name': item.item_name,
            'category': item.category,
            'quantity': int(qty) if qty.is_integer() else round(qty, 3),
            'unit': item.unit or 'Units',
            'safety_stock': int(safety) if safety.is_integer() else safety,
            'location': item.location or 'Main Warehouse',
            'is_low': is_low,
            'unit_price': unit_p,
            'total_val_fmt': format_inr(tot_val) if unit_p > 0 else None,
            'icon_class': icon_class,
            'icon_bg': icon_bg,
            'stock_pct': stock_pct,
        })

    low_stock_list = [i for i in inventories if float(i.quantity or 0) <= float(i.safety_stock or 0)]
    low_stock_count = len(low_stock_list)
    low_stock_subtitle = f"{low_stock_list[0].item_name} below reorder point" if low_stock_list else "All stocks optimal"

    # Fleet / Machinery
    fleet_qs = inventories.filter(
        Q(category__icontains='Vehicle') | Q(category__icontains='Fleet') | 
        Q(category__icontains='Machin') | Q(category__icontains='Equipment') | 
        Q(category__icontains='Tractor') | Q(category__icontains='Implement') |
        Q(license_plate__isnull=False)
    )
    fleet_total_count = fleet_qs.count()
    fleet_operational_count = fleet_qs.filter(status__in=['In Stock', 'Operational', 'Good']).count()
    fleet_maintenance_count = max(0, fleet_total_count - fleet_operational_count)
    fleet_uptime_pct = round((fleet_operational_count / max(1, fleet_total_count)) * 100) if fleet_total_count > 0 else 0

    fleet_items = []
    for f_item in fleet_qs[:4]:
        is_op = str(f_item.status).lower() in ['in stock', 'operational', 'good']
        is_low = float(f_item.quantity or 0) <= float(f_item.safety_stock or 0)
        
        status_label = "Operational" if is_op else ("Low Stock" if is_low else "Maintenance")
        badge_class = "ok" if is_op else "low"
        icon_bg = "bg-op" if is_op else ("bg-low" if is_low else "bg-maint")

        fleet_items.append({
            'name': f_item.item_name,
            'category': f_item.category,
            'plate': f_item.license_plate or 'N/A plate',
            'location': f_item.location or 'Main Garage',
            'qty': float(f_item.quantity or 1),
            'status_label': status_label,
            'badge_class': badge_class,
            'icon_bg': icon_bg,
            'inspected': f_item.last_service_date.strftime("%b %d, %Y") if f_item.last_service_date else "Recently",
        })

    next_service_item = fleet_qs.filter(next_service_date__isnull=False).order_by('next_service_date').first()
    next_service_name = next_service_item.item_name if next_service_item else (fleet_qs.first().item_name if fleet_qs.exists() else "None pending")

    tools_count = inventories.filter(Q(category__icontains='Tool') | Q(category__icontains='Implement')).count()

    # ---- 9. Labour Payroll Details ----
    monthly_payroll_total = sum(float(s.salary or (s.base_daily_rate or 500) * 26) for s in staff_qs) if staff_qs.exists() else 0.0
    paid_payrolls = StaffPayroll.objects.filter(staff__farmer=profile) if profile else StaffPayroll.objects.none()
    payroll_paid = float(paid_payrolls.aggregate(total=Sum('net_pay'))['total'] or 0) if paid_payrolls.exists() else 0.0
    payroll_pending = max(0.0, monthly_payroll_total - payroll_paid)

    # Payroll by role
    payroll_by_role = []
    if staff_qs.exists():
        role_groups = {}
        for s in staff_qs:
            r_name = s.role or (s.designation.name if s.designation else "Farm Workers")
            if r_name not in role_groups:
                role_groups[r_name] = {'count': 0, 'present': 0, 'total_amount': 0.0, 'rates': []}
            role_groups[r_name]['count'] += 1
            rate = float(s.base_daily_rate or 500)
            role_groups[r_name]['rates'].append(rate)
            role_groups[r_name]['total_amount'] += float(s.salary or (rate * 26))

        for r_name, r_data in role_groups.items():
            avg_rate = round(sum(r_data['rates']) / len(r_data['rates'])) if r_data['rates'] else 500
            # count present
            pres_count = attendances_today.filter(staff__role=r_name, status='P').count() if attendances_today.exists() else r_data['count']
            payroll_by_role.append({
                'role_name': r_name,
                'worker_count': r_data['count'],
                'present_count': pres_count,
                'total_fmt': format_inr(r_data['total_amount']),
                'daily_rate': f"₹{avg_rate}/day",
            })
    else:
        payroll_by_role = []

    # End of month for next payroll run
    import calendar
    last_day = calendar.monthrange(today_date.year, today_date.month)[1]
    next_payroll_date = today_date.replace(day=last_day).strftime("%b %d, %Y")

    # ---- 10. Live Sales & Orders ----
    recent_sales_orders = []
    if inflow.exists():
        for tx in inflow.order_by('-close_date', '-id')[:4]:
            amt = float(abs(tx.amount))
            status_text = "Delivered" if tx.status in ['Received', 'Finished'] else "Pending"
            badge_cls = "ok" if tx.status in ['Received', 'Finished'] else "low"
            buyer_name = tx.vendor.business_name if (tx.vendor and tx.vendor.business_name) else (tx.tags or "Local Wholesale Market")
            recent_sales_orders.append({
                'title': tx.description or f"{tx.category} Sale",
                'buyer': buyer_name,
                'amount_fmt': format_inr(amt),
                'status': status_text,
                'badge_class': badge_cls,
                'icon_bg': 'linear-gradient(135deg,var(--elite-emerald),var(--elite-emerald-dk))' if badge_cls == 'ok' else 'linear-gradient(135deg,var(--elite-amber),var(--elite-amber-dk))'
            })
    else:
        recent_sales_orders = []

    # ---- 11. Market Prices & Mandi Chart Data ----
    market_items = []
    grouped_crops = {}

    # 1. From Depot Terminals available_crops JSON
    terminals = DepotTerminal.objects.all().order_by('-id')
    for term in terminals:
        if isinstance(term.available_crops, list):
            for c in term.available_crops:
                if isinstance(c, dict) and c.get('name'):
                    cname = str(c.get('name', '')).strip()
                    unit = c.get('unit', 'KG')
                    price_val = c.get('price', '0')
                    if cname and price_val:
                        if cname not in grouped_crops:
                            grouped_crops[cname] = []
                        grouped_crops[cname].append({
                            'market_name': term.name,
                            'price': f"₹{price_val}/{unit}"
                        })

    # 2. From DepotCropPricing model
    depot_prices = DepotCropPricing.objects.select_related('depot', 'crop_index').filter(trading_restricted=False)
    for dp in depot_prices:
        cname = dp.crop_index.crop_name
        unit = dp.crop_index.unit or 'MT'
        rate_val = f"₹{int(dp.terminal_rate):,}/{unit}"
        if cname not in grouped_crops:
            grouped_crops[cname] = []
        grouped_crops[cname].append({
            'market_name': dp.depot.name,
            'price': rate_val
        })

    # 3. If farmer has active crops with farm prices
    if profile:
        farmer_splits = CropSplit.objects.filter(farm__farmer=profile).select_related('crop', 'farm')
        for split in farmer_splits:
            if split.crop and split.crop.name:
                cname = split.crop.name
                if cname not in grouped_crops and split.price_per_mt:
                    mkt_name = f"{split.farm.land_name} Gate" if split.farm else "Local Farm Gate"
                    grouped_crops[cname] = [{
                        'market_name': mkt_name,
                        'price': f"₹{float(split.price_per_mt):g}/MT"
                    }]

    for cname, mkts in grouped_crops.items():
        market_items.append({
            'crop_name': cname,
            'markets': mkts
        })

    # Mandi Chart labels and series
    mandi_chart_labels = []
    mandi_chart_prices = []
    for cname, mkts in grouped_crops.items():
        for m in mkts[:2]:
            clean_p = ''.join(ch for ch in m['price'] if ch.isdigit() or ch == '.')
            if clean_p:
                mandi_chart_labels.append(f"{cname} ({m['market_name']})")
                mandi_chart_prices.append(float(clean_p))
    if not mandi_chart_labels:
        mandi_chart_labels = ['Rice (Thanjavur)', 'Rice (Chennai)', 'Sugarcane (Villupuram)', 'Groundnut (Vellore)']
        mandi_chart_prices = [21400, 22150, 3200, 58900]

    # ---- 12. Expenses & Revenue Breakdown ----
    # Expense categories
    exp_colors = {
        'Seeds': 'var(--elite-violet)',
        'Fertilizer': 'var(--elite-amber)',
        'Labour': 'var(--elite-sky)',
        'Fuel': 'var(--elite-rose)',
        'Equipment': 'var(--elite-ink)',
        'Other': '#8790b3',
    }
    expense_breakdown = []
    if outflow.exists() and total_expenses > 0:
        cat_sums = {}
        for o in outflow:
            c = o.category or "Other"
            cat_sums[c] = cat_sums.get(c, 0.0) + float(abs(o.amount))
        for c, amt in sorted(cat_sums.items(), key=lambda x: x[1], reverse=True):
            pct = min(100, round((amt / total_expenses) * 100))
            color = exp_colors.get(c, '#8790b3')
            expense_breakdown.append({
                'label': c,
                'amount_fmt': format_inr(amt),
                'pct': pct,
                'color': color,
            })
    else:
        expense_breakdown = []

    # Revenue by crop
    revenue_by_crop = []
    if crop_splits.filter(total_sale_value__gt=0).exists() and total_sales > 0:
        crop_rev_sums = {}
        for cs in crop_splits.filter(total_sale_value__gt=0):
            c_name = cs.crop.name if cs.crop else "General Crop"
            crop_rev_sums[c_name] = crop_rev_sums.get(c_name, 0.0) + float(cs.total_sale_value or 0)
        for c_name, amt in sorted(crop_rev_sums.items(), key=lambda x: x[1], reverse=True):
            pct = min(100, round((amt / total_sales) * 100))
            revenue_by_crop.append({
                'label': c_name,
                'amount_fmt': format_inr(amt),
                'pct': pct,
            })
    else:
        revenue_by_crop = []

    # ---- 13. Recent Activities ----
    recent_activities = FarmActivity.objects.filter(farm__farmer=profile).select_related('farm').order_by('-created_at')[:5] if profile else FarmActivity.objects.none()

    # ---- 13b. Dashboard Farm & Crop Filter Universe ----
    dashboard_farms = []
    if farms.exists():
        for f in farms:
            dashboard_farms.append({
                'id': str(f.id),
                'name': f.land_name
            })
    else:
        dashboard_farms = []

    farm_crops_map = {'all': []}
    all_crops_set = set()

    if farms.exists():
        for f in farms:
            f_id = str(f.id)
            f_crops = set()
            splits_f = crop_splits.filter(farm=f)
            for cs in splits_f:
                if cs.crop and cs.crop.name:
                    f_crops.add(cs.crop.name)
            if f.crop_type and f.crop_type.name:
                f_crops.add(f.crop_type.name)
            farm_crops_map[f_id] = sorted(list(f_crops))
            all_crops_set.update(f_crops)
        dashboard_crops = sorted(list(all_crops_set)) if all_crops_set else []
        farm_crops_map['all'] = dashboard_crops
    else:
        farm_crops_map = {'all': []}
        dashboard_crops = []

    farm_crops_map_json = json.dumps(farm_crops_map)
    dashboard_farms_json = json.dumps(dashboard_farms)
    dashboard_crops_json = json.dumps(dashboard_crops)

    # ---- 14. Sales Analytics Breakdown Data for Filter & Charts ----
    sales_txs = inflow if profile else LedgerTransaction.objects.none()
    farmer_splits_sales = crop_splits.filter(total_sale_value__gt=0) if profile else CropSplit.objects.none()

    start_of_this_month = today_date.replace(day=1)
    if start_of_this_month.month == 1:
        start_of_last_month = start_of_this_month.replace(year=today_date.year - 1, month=12, day=1)
    else:
        start_of_last_month = start_of_this_month.replace(month=today_date.month - 1, day=1)
    end_of_last_month = start_of_this_month - timedelta(days=1)
    start_of_90_days = today_date - timedelta(days=90)
    if today_date.month >= 4:
        start_of_fin_year = today_date.replace(month=4, day=1)
    else:
        start_of_fin_year = today_date.replace(year=today_date.year - 1, month=4, day=1)

    periods_config = {
        'this_month': {'tx_filter': Q(close_date__gte=start_of_this_month), 'split_filter': Q(created_at__date__gte=start_of_this_month)},
        'last_month': {'tx_filter': Q(close_date__gte=start_of_last_month, close_date__lte=end_of_last_month), 'split_filter': Q(created_at__date__gte=start_of_last_month, created_at__date__lte=end_of_last_month)},
        'last_90_days': {'tx_filter': Q(close_date__gte=start_of_90_days), 'split_filter': Q(created_at__date__gte=start_of_90_days)},
        'this_year': {'tx_filter': Q(close_date__gte=start_of_fin_year), 'split_filter': Q(created_at__date__gte=start_of_fin_year)},
        'all_time': {'tx_filter': Q(), 'split_filter': Q()},
    }

    category_palette = [
        '#0f9d6c', '#4338ca', '#e18b1f', '#0e7fb8', '#d1436b', 
        '#7c3aed', '#059669', '#f59e0b', '#ec4899', '#6366f1'
    ]

    sales_analytics_data = {}
    available_filter_categories = set()

    for p_key, p_cfg in periods_config.items():
        period_txs = sales_txs.filter(p_cfg['tx_filter'])
        period_splits = farmer_splits_sales.filter(p_cfg['split_filter'])
        
        cat_map = {}

        for tx in period_txs:
            c_name = (tx.category or "General Sales").strip()
            amt = float(abs(tx.amount))
            cat_map[c_name] = cat_map.get(c_name, 0.0) + amt
            available_filter_categories.add(c_name)

        for cs in period_splits:
            c_name = (cs.crop.name if cs.crop and cs.crop.name else "Crop Harvest").strip()
            amt = float(cs.total_sale_value or 0)
            cat_map[c_name] = cat_map.get(c_name, 0.0) + amt
            available_filter_categories.add(c_name)

        items_list = []
        c_idx = 0
        sorted_cats = sorted(cat_map.items(), key=lambda x: x[1], reverse=True)
        total_period_sales = sum(amt for _, amt in sorted_cats)

        for c_name, amt in sorted_cats:
            color = category_palette[c_idx % len(category_palette)]
            pct = round((amt / total_period_sales * 100), 1) if total_period_sales > 0 else 0.0
            items_list.append({
                'name': c_name,
                'amount': amt,
                'pct': pct,
                'color': color
            })
            c_idx += 1

        sales_analytics_data[p_key] = {
            'all': items_list,
            'total': total_period_sales
        }

        for c_name, amt in sorted_cats:
            sales_analytics_data[p_key][c_name] = [{
                'name': c_name,
                'amount': amt,
                'pct': 100.0,
                'color': category_palette[0]
            }]

    # Collect individual dated records for Custom Date Range & Dynamic Live Filtering
    raw_sales_records = []
    for tx in sales_txs:
        c_name = (tx.category or "General Sales").strip()
        amt = float(abs(tx.amount))
        d_str = tx.close_date.strftime("%Y-%m-%d") if tx.close_date else today_date.strftime("%Y-%m-%d")
        f_id = str(tx.farm.id) if tx.farm else (str(first_farm.id) if first_farm else "all")
        f_name = tx.farm.land_name if tx.farm else (first_farm.land_name if first_farm else "Main Farm")
        crop_name = c_name if c_name in dashboard_crops else (farm_crops_map.get(f_id, [''])[0] if farm_crops_map.get(f_id) else '')
        raw_sales_records.append({
            'date': d_str,
            'name': c_name,
            'amount': amt,
            'farm_id': f_id,
            'farm_name': f_name,
            'crop_name': crop_name,
        })
    for cs in farmer_splits_sales:
        c_name = (cs.crop.name if cs.crop and cs.crop.name else "Crop Harvest").strip()
        amt = float(cs.total_sale_value or 0)
        d_str = cs.created_at.strftime("%Y-%m-%d") if cs.created_at else today_date.strftime("%Y-%m-%d")
        f_id = str(cs.farm.id) if cs.farm else (str(first_farm.id) if first_farm else "all")
        f_name = cs.farm.land_name if cs.farm else (first_farm.land_name if first_farm else "Main Farm")
        raw_sales_records.append({
            'date': d_str,
            'name': c_name,
            'amount': amt,
            'farm_id': f_id,
            'farm_name': f_name,
            'crop_name': c_name,
        })

    sales_analytics_data['raw_records'] = raw_sales_records
    sales_analytics_json = json.dumps(sales_analytics_data)
    filter_categories_list = sorted(list(available_filter_categories))

    # ---- 15. Expense Analytics Breakdown Data ----
    expense_txs = outflow if profile else LedgerTransaction.objects.none()

    expense_palette = [
        '#d1436b', '#e18b1f', '#4338ca', '#0e7fb8', '#7c3aed', 
        '#0f9d6c', '#f59e0b', '#ec4899', '#6366f1', '#64748b'
    ]

    expense_analytics_data = {}
    available_expense_categories = set()

    for p_key, p_cfg in periods_config.items():
        period_exp = expense_txs.filter(p_cfg['tx_filter'])
        exp_map = {}

        for tx in period_exp:
            c_name = (tx.category or "General Expense").strip()
            amt = float(abs(tx.amount))
            exp_map[c_name] = exp_map.get(c_name, 0.0) + amt
            available_expense_categories.add(c_name)

        items_list = []
        c_idx = 0
        sorted_exp = sorted(exp_map.items(), key=lambda x: x[1], reverse=True)
        total_period_exp = sum(amt for _, amt in sorted_exp)

        for c_name, amt in sorted_exp:
            color = expense_palette[c_idx % len(expense_palette)]
            pct = round((amt / total_period_exp * 100), 1) if total_period_exp > 0 else 0.0
            items_list.append({
                'name': c_name,
                'amount': amt,
                'pct': pct,
                'color': color
            })
            c_idx += 1

        expense_analytics_data[p_key] = {
            'all': items_list,
            'total': total_period_exp
        }

        for c_name, amt in sorted_exp:
            expense_analytics_data[p_key][c_name] = [{
                'name': c_name,
                'amount': amt,
                'pct': 100.0,
                'color': expense_palette[0]
            }]

    raw_expense_records = []
    for tx in expense_txs:
        c_name = (tx.category or "General Expense").strip()
        amt = float(abs(tx.amount))
        d_str = tx.close_date.strftime("%Y-%m-%d") if tx.close_date else today_date.strftime("%Y-%m-%d")
        f_id = str(tx.farm.id) if tx.farm else (str(first_farm.id) if first_farm else "all")
        f_name = tx.farm.land_name if tx.farm else (first_farm.land_name if first_farm else "Main Farm")
        crop_name = farm_crops_map.get(f_id, [''])[0] if farm_crops_map.get(f_id) else ''
        raw_expense_records.append({
            'date': d_str,
            'name': c_name,
            'amount': amt,
            'farm_id': f_id,
            'farm_name': f_name,
            'crop_name': crop_name,
        })

    expense_analytics_data['raw_records'] = raw_expense_records
    expense_analytics_json = json.dumps(expense_analytics_data)
    filter_expense_categories_list = sorted(list(available_expense_categories))

    # ---- 16. Task Analytics Breakdown Data ----
    task_qs = tasks if profile else FarmTask.objects.none()

    task_periods_config = {
        'this_month': Q(task_date__gte=start_of_this_month) | Q(created_at__date__gte=start_of_this_month),
        'last_month': (Q(task_date__gte=start_of_last_month, task_date__lte=end_of_last_month) | Q(created_at__date__gte=start_of_last_month, created_at__date__lte=end_of_last_month)),
        'last_90_days': Q(task_date__gte=start_of_90_days) | Q(created_at__date__gte=start_of_90_days),
        'this_year': Q(task_date__gte=start_of_fin_year) | Q(created_at__date__gte=start_of_fin_year),
        'all_time': Q(),
    }

    task_status_colors = {
        'Pending': '#e18b1f',
        'In Progress': '#4338ca',
        'Completed': '#0f9d6c',
        'Cancelled': '#d1436b',
    }
    task_palette = ['#e18b1f', '#4338ca', '#0f9d6c', '#0e7fb8', '#7c3aed', '#d1436b', '#f59e0b']

    task_analytics_data = {}
    available_task_categories = set()

    for p_key, t_filter in task_periods_config.items():
        period_tasks = task_qs.filter(t_filter)
        status_map = {}
        cat_map = {}

        for t in period_tasks:
            st = t.status or "Pending"
            status_map[st] = status_map.get(st, 0) + 1
            c_name = t.task_category.name if (t.task_category and t.task_category.name) else (t.priority or "General Task")
            cat_map[c_name] = cat_map.get(c_name, 0) + 1
            available_task_categories.add(c_name)

        items_list = []
        total_period_tasks = sum(status_map.values())

        for st_name, count in status_map.items():
            color = task_status_colors.get(st_name, task_palette[len(items_list) % len(task_palette)])
            pct = round((count / total_period_tasks * 100), 1) if total_period_tasks > 0 else 0.0
            items_list.append({
                'name': st_name + ' Tasks',
                'amount': count,
                'pct': pct,
                'color': color
            })

        task_analytics_data[p_key] = {
            'all': items_list,
            'total': total_period_tasks
        }

        for c_name in available_task_categories:
            c_count = cat_map.get(c_name, 0)
            task_analytics_data[p_key][c_name] = [{
                'name': c_name,
                'amount': c_count,
                'pct': 100.0,
                'color': task_palette[0]
            }]

    raw_task_records = []
    for t in task_qs:
        st = t.status or "Pending"
        c_name = t.task_category.name if (t.task_category and t.task_category.name) else (t.priority or "General Task")
        d_str = t.task_date.strftime("%Y-%m-%d") if t.task_date else (t.created_at.strftime("%Y-%m-%d") if t.created_at else today_date.strftime("%Y-%m-%d"))
        f_id = str(t.farm.id) if t.farm else (str(first_farm.id) if first_farm else "all")
        f_name = t.farm.land_name if t.farm else (first_farm.land_name if first_farm else "Main Farm")
        crop_name = t.associated_split.crop.name if (t.associated_split and t.associated_split.crop) else (farm_crops_map.get(f_id, [''])[0] if farm_crops_map.get(f_id) else '')
        raw_task_records.append({
            'date': d_str,
            'name': st + ' Tasks',
            'status': st,
            'category': c_name,
            'amount': 1,
            'farm_id': f_id,
            'farm_name': f_name,
            'crop_name': crop_name,
        })

    task_analytics_data['raw_records'] = raw_task_records
    task_analytics_json = json.dumps(task_analytics_data)
    filter_task_categories_list = sorted(list(available_task_categories))

    # ---- 17. Dynamic Crop Mandi Price Trend Analytics from Database ----
    from apps.superadmin.models import MarketPriceIndex, DepotTerminal, DepotCropPricing, DailyPriceHistory

    db_crops = list(MarketPriceIndex.objects.all().order_by('crop_name'))
    mandi_crops_list = [c.crop_name for c in db_crops]
    if not mandi_crops_list:
        mandi_crops_list = ['Tomato', 'Potato', 'Onion', 'Wheat', 'Paddy & Rice', 'Cotton', 'Sugarcane', 'Banana', 'Chilli', 'Maize', 'Soybean', 'Groundnut']

    db_depots = list(DepotTerminal.objects.filter(sourcing_active=True).order_by('name'))
    if not db_depots:
        db_depots = list(DepotTerminal.objects.all().order_by('name'))
    mandi_markets_list = [d.name for d in db_depots]

    crop_base_rates = {}
    for c in db_crops:
        crop_base_rates[c.crop_name] = {
            'rate': float(c.base_price),
            'unit': f"₹/{c.unit}" if not c.unit.startswith('₹') else c.unit
        }
    crop_base_rates_json = json.dumps(crop_base_rates)

    mandi_analytics_data = {}
    mandi_palette = ['#0e7fb8', '#0f9d6c', '#4338ca', '#e18b1f', '#d1436b', '#7c3aed', '#059669', '#f59e0b', '#ec4899', '#0284c7']

    # Pre-fetch all daily price histories and depot crop pricings from DB
    all_histories = list(DailyPriceHistory.objects.select_related('depot', 'crop_index').all())
    all_pricing = list(DepotCropPricing.objects.select_related('depot', 'crop_index').all())

    pricing_map = {}
    for dp in all_pricing:
        pricing_map[(dp.depot_id, dp.crop_index_id)] = float(dp.terminal_rate)

    # Extract terminal available_crops mapping
    depot_available_crop_map = {}
    for d in db_depots:
        if isinstance(d.available_crops, list):
            for ac in d.available_crops:
                if isinstance(ac, dict) and ac.get('name'):
                    ac_n = str(ac.get('name')).strip().lower()
                    try:
                        ac_p = float(ac.get('price', 0))
                        ac_u = str(ac.get('unit', '')).strip()
                        depot_available_crop_map[(d.id, ac_n)] = (ac_p, ac_u)
                    except (ValueError, TypeError):
                        pass

    mandi_periods_config = {
        'this_month': (start_of_this_month, today_date),
        'last_month': (start_of_last_month, end_of_last_month),
        'last_90_days': (start_of_90_days, today_date),
        'this_year': (start_of_fin_year, today_date),
        'all_time': (date(2020, 1, 1), today_date),
    }

    for p_key, (p_start, p_end) in mandi_periods_config.items():
        period_hist = [h for h in all_histories if p_start <= h.date <= p_end]

        # 1. Overall Depot Rates for this period
        p_items = []
        for idx, d in enumerate(db_depots):
            d_hist = [h for h in period_hist if h.depot_id == d.id]
            if d_hist:
                avg_rate = round(sum(float(h.price) for h in d_hist) / len(d_hist), 2)
                d_unit = '₹/Quintal'
            else:
                d_avail_prices = [float(ac.get('price', 0)) for ac in (d.available_crops or []) if isinstance(ac, dict) and ac.get('price')]
                if d_avail_prices:
                    avg_rate = round(sum(d_avail_prices) / len(d_avail_prices), 1)
                    d_unit = f"₹/{d.available_crops[0].get('unit', 'Kg')}"
                else:
                    d_pricing = [pr for pr in all_pricing if pr.depot_id == d.id]
                    avg_rate = round(sum(float(pr.terminal_rate) for pr in d_pricing) / len(d_pricing), 2) if d_pricing else 2150.0
                    d_unit = '₹/Quintal'

            p_items.append({
                'name': d.name,
                'amount': avg_rate,
                'pct': round(min(100.0, (avg_rate / 3000.0) * 100.0), 1),
                'unit': d_unit,
                'color': mandi_palette[idx % len(mandi_palette)]
            })

        mandi_analytics_data[p_key] = {
            'all': p_items,
            'total': len(p_items)
        }

        # 2. Crop-specific market prices from real database records & available_crops
        for c in db_crops:
            c_name = c.crop_name
            c_unit = f"₹/{c.unit}" if not c.unit.startswith('₹') else c.unit
            c_hist = [h for h in period_hist if h.crop_index_id == c.id]
            c_items = []

            for idx, d in enumerate(db_depots):
                dc_hist = [h for h in c_hist if h.depot_id == d.id]
                t_crop_info = depot_available_crop_map.get((d.id, c_name.lower()))

                if t_crop_info and t_crop_info[0] > 0:
                    rate = t_crop_info[0]
                    item_unit = f"₹/{t_crop_info[1]}" if t_crop_info[1] and not t_crop_info[1].startswith('₹') else (f"₹/{t_crop_info[1]}" if t_crop_info[1] else c_unit)
                elif dc_hist:
                    rate = round(sum(float(h.price) for h in dc_hist) / len(dc_hist), 1 if 'Kg' in c_unit else 0)
                    item_unit = c_unit
                else:
                    rate = pricing_map.get((d.id, c.id), float(c.base_price))
                    rate = round(rate, 1 if 'Kg' in c_unit else 0)
                    item_unit = c_unit

                base_ref = float(c.base_price) if c.base_price else (rate if rate > 0 else 100.0)
                pct = round(min(100.0, (rate / max(1.0, max(base_ref, rate) * 1.2)) * 100.0), 1)

                c_items.append({
                    'name': d.name,
                    'amount': rate,
                    'pct': pct,
                    'unit': item_unit,
                    'color': mandi_palette[idx % len(mandi_palette)]
                })

            mandi_analytics_data[p_key][c_name] = c_items

    # 3. Raw records for custom date range & farm/crop queries
    raw_records = []
    recorded_keys = set()
    for h in all_histories:
        c_u = f"₹/{h.crop_index.unit}" if not h.crop_index.unit.startswith('₹') else h.crop_index.unit
        c_name = h.crop_index.crop_name
        matched_fid = 'all'
        for fid, fcrops in farm_crops_map.items():
            if any(c.lower() in c_name.lower() or c_name.lower() in c.lower() for c in fcrops):
                matched_fid = fid
                break
        raw_records.append({
            'date': h.date.strftime('%Y-%m-%d'),
            'crop': c_name,
            'name': h.depot.name,
            'amount': float(h.price),
            'unit': c_u,
            'farm_id': matched_fid
        })
        recorded_keys.add((h.depot_id, h.crop_index.crop_name.lower(), h.date.strftime('%Y-%m-%d')))

    today_str = today_date.strftime('%Y-%m-%d')
    for d in db_depots:
        if isinstance(d.available_crops, list):
            for ac in d.available_crops:
                if isinstance(ac, dict) and ac.get('name'):
                    ac_n = str(ac.get('name')).strip()
                    try:
                        ac_p = float(ac.get('price', 0))
                    except (ValueError, TypeError):
                        ac_p = 0
                    ac_u = ac.get('unit', 'Kg')
                    c_u = f"₹/{ac_u}" if not str(ac_u).startswith('₹') else str(ac_u)
                    if ac_p > 0 and (d.id, ac_n.lower(), today_str) not in recorded_keys:
                        matched_fid = 'all'
                        for fid, fcrops in farm_crops_map.items():
                            if any(c.lower() in ac_n.lower() or ac_n.lower() in c.lower() for c in fcrops):
                                matched_fid = fid
                                break
                        raw_records.append({
                            'date': today_str,
                            'crop': ac_n,
                            'name': d.name,
                            'amount': ac_p,
                            'unit': c_u,
                            'farm_id': matched_fid
                        })

    mandi_analytics_data['raw_records'] = raw_records
    mandi_analytics_json = json.dumps(mandi_analytics_data)

    context = {
        'farm': first_farm,
        'dashboard_farms': dashboard_farms,
        'dashboard_crops': dashboard_crops,
        'farm_crops_map_json': farm_crops_map_json,
        'dashboard_farms_json': dashboard_farms_json,
        'dashboard_crops_json': dashboard_crops_json,
        'total_farms': total_farms,
        'total_acres': formatted_acres,
        'active_crops': active_crops,
        'harvested_crops': harvested_crops,
        'total_tasks': total_tasks,
        'pending_tasks': pending_tasks,
        'in_progress_tasks': in_progress_tasks,
        'completed_tasks': completed_tasks,
        'pending_pct': pending_pct,
        'in_progress_pct': in_progress_pct,
        'completed_pct': completed_pct,
        'total_staff': total_staff,
        'active_staff': active_staff,
        'suspended_staff': suspended_staff,
        'present_today': present_today,
        'absent_today': absent_today,
        'leave_today': leave_today,
        'attendance_pct': attendance_pct,
        'leave_pct': leave_pct,
        'absent_pct': absent_pct,
        'total_business_value': total_business_value,
        'total_business_value_fmt': format_inr(total_business_value),
        'total_sales': total_sales,
        'total_sales_fmt': format_inr(total_sales),
        'total_expenses': total_expenses,
        'total_expenses_fmt': format_inr(total_expenses),
        'net_profit': net_profit,
        'net_profit_fmt': format_inr(net_profit),
        'inventory_value': inventory_value,
        'inventory_value_fmt': format_inr(inventory_value),
        'receivables': receivables,
        'receivables_fmt': format_inr(receivables),
        'payables': payables,
        'payables_fmt': format_inr(payables),
        'overdue_invoices_count': overdue_invoices_count,
        'overdue_percentage': overdue_percentage,
        'plan_name': plan_name,
        'plan_price': plan_price,
        'plan_price_fmt': format_inr(plan_price),
        'renews_on': renews_on,
        'days_left': days_left,
        'auto_renew': auto_renew,
        'weather': weather,
        'weather_forecast_json': weather_forecast_json,
        'weather_hourly_json': weather_hourly_json,
        'weather_hourly_by_day_json': weather_hourly_by_day_json,
        'weather_lat': farm_lat,
        'weather_lon': farm_lon,
        'farm_health_score': farm_health_score,
        'healthy_farms_count': healthy_farms_count,
        'needs_attention_farms_count': needs_attention_farms_count,
        'critical_farms_count': critical_farms_count,
        'avg_soil_ph': avg_soil_ph,
        'crop_health_chips': crop_health_chips,
        'inventory_items': inventory_items,
        'total_inventory_items': inventories.count(),
        'low_stock_count': low_stock_count,
        'low_stock_subtitle': low_stock_subtitle,
        'fleet_items': fleet_items,
        'fleet_total_count': fleet_total_count,
        'fleet_operational_count': fleet_operational_count,
        'fleet_maintenance_count': fleet_maintenance_count,
        'fleet_uptime_pct': fleet_uptime_pct,
        'next_service_name': next_service_name,
        'tools_count': tools_count,
        'monthly_payroll_total_fmt': format_inr(monthly_payroll_total),
        'payroll_paid_fmt': format_inr(payroll_paid),
        'payroll_pending_fmt': format_inr(payroll_pending),
        'payroll_by_role': payroll_by_role,
        'next_payroll_date': next_payroll_date,
        'recent_sales_orders': recent_sales_orders,
        'market_items': market_items,
        'mandi_chart_labels': json.dumps(mandi_chart_labels),
        'mandi_chart_prices': json.dumps(mandi_chart_prices),
        'expense_breakdown': expense_breakdown,
        'revenue_by_crop': revenue_by_crop,
        'recent_activities': recent_activities,
        'sales_analytics_json': sales_analytics_json,
        'filter_categories_list': filter_categories_list,
        'expense_analytics_json': expense_analytics_json,
        'filter_expense_categories_list': filter_expense_categories_list,
        'task_analytics_json': task_analytics_json,
        'filter_task_categories_list': filter_task_categories_list,
        'mandi_crops_list': mandi_crops_list,
        'mandi_markets_list': mandi_markets_list,
        'crop_base_rates_json': crop_base_rates_json,
        'mandi_analytics_json': mandi_analytics_json,
    }
    return render(request, "farmer/dashboard.html", context)

def parse_farm_advance_fields(request, existing_farm=None):
    from django.core.files.storage import default_storage
    from django.core.files.base import ContentFile

    notes = request.POST.get("notes", "").strip()
    farming_type = request.POST.get("farming_type", "").strip() or request.POST.get("farming_type_custom", "").strip()
    organic_registration_number = request.POST.get("organic_registration_number", "").strip()
    fencing_type = request.POST.get("fencing_type", "").strip() or request.POST.get("fencing_type_custom", "").strip()
    fencing_maintenance_schedule = request.POST.get("fencing_maintenance_schedule", "").strip() or request.POST.get("fencing_maintenance_custom", "").strip()
    fencing_details = request.POST.get("fencing_details", "").strip()
    land_allocation_description = request.POST.get("land_allocation_description", "").strip()

    # Blueprint file
    blueprint_file = request.FILES.get("land_allocation_blueprint")
    blueprint_val = existing_farm.land_allocation_blueprint if existing_farm else None
    if blueprint_file:
        path = default_storage.save(f"farm_blueprints/{blueprint_file.name}", ContentFile(blueprint_file.read()))
        blueprint_val = path

    # 1. Water sources
    water_types = request.POST.getlist("water_source[]") or request.POST.getlist("water_source")
    water_customs = request.POST.getlist("water_source_custom[]") or request.POST.getlist("water_source_custom")
    motor_details = request.POST.getlist("motor_details[]") or request.POST.getlist("motor_details")
    water_details = request.POST.getlist("water_source_details[]") or request.POST.getlist("water_source_details")

    water_sources = []
    max_w = max(len(water_types), len(water_customs), len(motor_details), len(water_details)) if any([water_types, water_customs, motor_details, water_details]) else 0
    for i in range(max_w):
        w_type = water_types[i] if i < len(water_types) else ""
        w_custom = water_customs[i] if i < len(water_customs) else ""
        m_detail = motor_details[i] if i < len(motor_details) else ""
        w_detail = water_details[i] if i < len(water_details) else ""
        final_type = w_custom if (w_type == "Other" or not w_type) and w_custom else w_type
        if final_type or m_detail or w_detail:
            water_sources.append({
                "type": final_type,
                "motor": m_detail,
                "details": w_detail
            })

    # 2. Electricity details
    elec_types = request.POST.getlist("electricity_type[]") or request.POST.getlist("electricity_type")
    elec_customs = request.POST.getlist("electricity_type_custom[]") or request.POST.getlist("electricity_type_custom")
    elec_customers = request.POST.getlist("electricity_customer_number[]") or request.POST.getlist("electricity_customer_number")
    elec_payments = request.POST.getlist("electricity_payment_details[]") or request.POST.getlist("electricity_payment_details")
    elec_details = request.POST.getlist("electricity_details[]") or request.POST.getlist("electricity_details")

    electricity_details = []
    max_e = max(len(elec_types), len(elec_customs), len(elec_customers), len(elec_payments), len(elec_details)) if any([elec_types, elec_customs, elec_customers, elec_payments, elec_details]) else 0
    for i in range(max_e):
        e_type = elec_types[i] if i < len(elec_types) else ""
        e_custom = elec_customs[i] if i < len(elec_customs) else ""
        e_cust_no = elec_customers[i] if i < len(elec_customers) else ""
        e_pay = elec_payments[i] if i < len(elec_payments) else ""
        e_det = elec_details[i] if i < len(elec_details) else ""
        final_type = e_custom if (e_type == "Other" or not e_type) and e_custom else e_type
        if final_type or e_cust_no or e_pay or e_det:
            electricity_details.append({
                "type": final_type,
                "customer_number": e_cust_no,
                "payment_details": e_pay,
                "details": e_det
            })

    # 3. Documents
    doc_types = request.POST.getlist("document_type[]") or request.POST.getlist("document_type")
    doc_names = request.POST.getlist("document_name[]") or request.POST.getlist("document_name")
    doc_files = request.FILES.getlist("farm_documents[]") or request.FILES.getlist("farm_documents")
    existing_urls = request.POST.getlist("existing_document_url[]") or request.POST.getlist("existing_document_url")
    has_files = request.POST.getlist("document_has_file[]") or request.POST.getlist("document_has_file")

    farm_documents = []
    file_ptr = 0
    max_d = max(len(doc_types), len(doc_names), len(existing_urls)) if any([doc_types, doc_names, existing_urls]) else 0
    for i in range(max_d):
        d_type = doc_types[i] if i < len(doc_types) else ""
        d_name = doc_names[i] if i < len(doc_names) else ""
        exist_url = existing_urls[i] if i < len(existing_urls) else ""
        has_new_file = (has_files[i] == "1") if i < len(has_files) else False

        file_url = exist_url
        if has_new_file and file_ptr < len(doc_files):
            d_file = doc_files[file_ptr]
            file_ptr += 1
            path = default_storage.save(f"farm_documents/{d_file.name}", ContentFile(d_file.read()))
            file_url = default_storage.url(path)
        elif not has_files and file_ptr < len(doc_files) and not exist_url:
            d_file = doc_files[file_ptr]
            file_ptr += 1
            path = default_storage.save(f"farm_documents/{d_file.name}", ContentFile(d_file.read()))
            file_url = default_storage.url(path)

        if d_type or d_name or file_url:
            farm_documents.append({
                "type": d_type,
                "name": d_name,
                "file_url": file_url
            })

    return {
        "notes": notes,
        "farming_type": farming_type,
        "organic_registration_number": organic_registration_number,
        "fencing_type": fencing_type,
        "fencing_maintenance_schedule": fencing_maintenance_schedule,
        "fencing_details": fencing_details,
        "land_allocation_blueprint": blueprint_val,
        "land_allocation_description": land_allocation_description,
        "water_sources": water_sources,
        "electricity_details": electricity_details,
        "farm_documents": farm_documents
    }

def manage_farm(request):
    if request.method == "POST":
        from apps.farmer.validation import pincode_error
        error = pincode_error(request)
        if error is not None:
            return error

    from django.contrib import messages
    from django.shortcuts import redirect

    from apps.farmer.models import Farm, CropSplit
    from apps.superadmin.models import CropType, State

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        land_name = request.POST.get("land_name")
        total_area = request.POST.get("total_area")
        raw_crop_ids = request.POST.getlist("crop_type") + request.POST.getlist("crops") + request.POST.getlist("crop")
        crop_ids = [str(c).strip() for c in raw_crop_ids if c and str(c).strip()]
        crop_name = request.POST.get("crop_name", "").strip()

        from apps.superadmin.models import Crop, CropType
        crop_objs = []
        if crop_ids:
            # Preserve selected order where possible
            crops_dict = {str(c.id): c for c in Crop.objects.filter(id__in=crop_ids)}
            for cid in crop_ids:
                if cid in crops_dict and crops_dict[cid] not in crop_objs:
                    crop_objs.append(crops_dict[cid])
        elif crop_name:
            crop_obj = Crop.objects.filter(name__iexact=crop_name).first()
            if not crop_obj:
                crop_type_obj = CropType.objects.filter(name__iexact=crop_name).first()
                if not crop_type_obj:
                    crop_type_obj = CropType.objects.create(name=crop_name, description=f"Auto-created crop type for {crop_name}", is_active=True)
                crop_obj = Crop.objects.create(name=crop_name, crop_type=crop_type_obj, description=f"Auto-created crop: {crop_name}", is_active=True)
            crop_objs = [crop_obj]

        crop_obj = crop_objs[0] if crop_objs else None
        crop_type_obj = crop_obj.crop_type if crop_obj and crop_obj.crop_type else None

        latitude = request.POST.get("latitude")
        longitude = request.POST.get("longitude")
        state_id = request.POST.get("state")
        district_id = request.POST.get("district")
        taluk_id = request.POST.get("taluk")
        street = request.POST.get("street")
        door = request.POST.get("door")
        pincode = request.POST.get("pincode")
        address = request.POST.get("address")
        soil_type = request.POST.get("soil_type")
        soil_ph = request.POST.get("soil_ph")
        irrigation_type = request.POST.get("irrigation_type")
        health_indicator = request.POST.get("health_indicator")

        adv_data = parse_farm_advance_fields(request)

        # --- Acreage Validation Logic ---
        try:
            new_area = float(total_area) if total_area else 0.0
        except ValueError:
            new_area = 0.0

        import re

        active_sub = farmer_prof.subscriptions.filter(subscription_status='active').order_by('-id').first() if farmer_prof else None
        if not active_sub or not active_sub.package:
            messages.error(request, "No active subscription found. Please subscribe to a package first.")
            return redirect('farmer-farms')

        # Check Land count limit
        max_lands_str = str(active_sub.package.my_land).lower() if active_sub.package.my_land else "unlimited"
        if max_lands_str != "unlimited":
            match_land = re.search(r'[\d]+', max_lands_str)
            if match_land:
                max_lands = int(match_land.group())
                current_farm_count = Farm.objects.filter(farmer=farmer_prof).count()
                if current_farm_count >= max_lands:
                    messages.error(request, f"Cannot add farm. Your package allows a maximum of {max_lands} lands.")
                    return redirect('farmer-farms')

        # Check Acre limit
        max_acres_str = str(active_sub.package.max_acre_support).lower() if active_sub.package.max_acre_support else "unlimited"
        if max_acres_str != "unlimited":
            match_acre = re.search(r'[\d\.]+', max_acres_str)
            if match_acre:
                max_acres = float(match_acre.group())
                current_farms = Farm.objects.filter(farmer=farmer_prof)
                total_current_area = 0.0
                for farm in current_farms:
                    farm_area_match = re.search(r'[\d\.]+', str(farm.total_area))
                    if farm_area_match:
                        total_current_area += float(farm_area_match.group())

                if total_current_area + new_area > max_acres:
                    messages.error(request, f"Cannot allocate {new_area} acres. Your package allows a maximum of {max_acres} acres (Current allocated: {total_current_area}). Please upgrade your package.")
                    return redirect('farmer-farms')
        # --------------------------------
        # Save new Farm Land
        active_mgr = get_active_manager_staff(request)
        farm = Farm.objects.create(
            farmer=farmer_prof,
            created_by_staff=active_mgr,
            land_name=land_name,
            total_area=new_area,
            crop=crop_obj,
            crop_type=crop_type_obj,
            latitude=latitude,
            longitude=longitude,
            state_id=state_id if state_id else None,
            district_id=district_id if district_id else None,
            taluk_id=taluk_id if taluk_id else None,
            street=street,
            door=door,
            pincode=pincode,
            address=address,
            soil_type=soil_type,
            soil_ph=soil_ph,
            irrigation_type=irrigation_type,
            health_indicator=health_indicator if health_indicator else "Good",
            notes=adv_data["notes"],
            farming_type=adv_data["farming_type"],
            organic_registration_number=adv_data["organic_registration_number"],
            fencing_type=adv_data["fencing_type"],
            fencing_maintenance_schedule=adv_data["fencing_maintenance_schedule"],
            fencing_details=adv_data["fencing_details"],
            land_allocation_blueprint=adv_data["land_allocation_blueprint"],
            land_allocation_description=adv_data["land_allocation_description"],
            water_sources=adv_data["water_sources"],
            electricity_details=adv_data["electricity_details"],
            farm_documents=adv_data["farm_documents"]
        )
        if crop_objs:
            farm.crops.set(crop_objs)

        # Log Notification for Farmer
        from auth.models import SystemNotification
        SystemNotification.objects.create(
            user=request.user,
            title="New Farm Added",
            message=f"Farm '{land_name}' ({new_area} acres) was successfully registered.",
            icon_class="ri-map-pin-line",
            bg_color="success"
        )

        # Log Notification for Superadmin
        SystemNotification.objects.create(
            role_target="superadmin",
            title="New Farm Registered",
            message=f"Farmer {request.user.username} registered a new farm: '{land_name}'.",
            icon_class="ri-map-pin-add-line",
            bg_color="info"
        )

        messages.success(request, "Farm Land Added Successfully!")
        return redirect('farmer-farms')

    states = State.objects.filter(country__name__iexact='India').order_by('name')
    if not states.exists():
        states = State.objects.all().order_by('name')
    crop_types = CropType.objects.filter(is_active=True).order_by('name')

    from apps.superadmin.models import (
        Crop, SoilType, IrrigationType, WaterSourceType, 
        ElectricityType, FarmingType, FencingType, CropCategory, CropPattern, CropVariety, MaintenanceSchedule
    )
    crops = Crop.objects.filter(is_active=True).order_by('name')
    soil_types = SoilType.objects.filter(is_active=True).order_by('name')
    irrigation_types = IrrigationType.objects.filter(is_active=True).order_by('name')
    water_source_types = WaterSourceType.objects.filter(is_active=True).order_by('name')
    electricity_types = ElectricityType.objects.filter(is_active=True).order_by('name')
    farming_types = FarmingType.objects.filter(is_active=True).order_by('name')
    fencing_types = FencingType.objects.filter(is_active=True).order_by('name')
    crop_categories = CropCategory.objects.filter(is_active=True).order_by('name')
    crop_patterns = CropPattern.objects.filter(is_active=True).order_by('name')
    crop_varieties = CropVariety.objects.filter(is_active=True).order_by('name')
    maintenance_schedules = MaintenanceSchedule.objects.filter(is_active=True).order_by('name')

    from django.db.models import Count, Q, Sum
    active_mgr = get_active_manager_staff(request)

    if active_mgr:
        mgr_farm_filter = Q(created_by_staff=active_mgr) | Q(splits__supervisors=active_mgr) | Q(splits__supervisor=active_mgr)
        farms_qs = Farm.objects.filter(farmer=farmer_prof).filter(mgr_farm_filter).annotate(
            split_count=Count('splits', filter=(~Q(splits__stage__in=['Inactive', 'Sold']) & (Q(splits__supervisors=active_mgr) | Q(splits__supervisor=active_mgr) | Q(created_by_staff=active_mgr))), distinct=True),
            allocated_area=Sum('splits__acreage', filter=(~Q(splits__stage__in=['Harvested', 'Sold', 'Inactive']) & (Q(splits__supervisors=active_mgr) | Q(splits__supervisor=active_mgr) | Q(created_by_staff=active_mgr))))
        ).distinct().order_by('-created_at')
    else:
        farms_qs = Farm.objects.filter(farmer=farmer_prof).annotate(
            split_count=Count('splits', filter=~Q(splits__stage__in=['Inactive', 'Sold']), distinct=True),
            allocated_area=Sum('splits__acreage', filter=~Q(splits__stage__in=['Harvested', 'Sold', 'Inactive']))
        ).order_by('-created_at')

    farms = []
    for f in farms_qs:
        alloc = float(f.allocated_area or 0)
        tot = float(f.total_area)
        f.unallocated_area = max(0.0, tot - alloc)
        f.alloc_val = alloc
        if active_mgr:
            f.visible_splits = f.splits.filter(
                Q(supervisors=active_mgr) | Q(supervisor=active_mgr) | Q(farm__created_by_staff=active_mgr)
            ).distinct().order_by('-id')
        else:
            f.visible_splits = f.splits.all().order_by('-id')
        farms.append(f)

    # Extract unique regions and statuses from farmer's registered farms for filters
    farm_regions = []
    seen_regions = set()
    farm_statuses = []
    seen_statuses = set()
    for f in farms:
        loc_name = f.district.name if f.district else (f.state.name if f.state else None)
        if loc_name and loc_name.strip() and loc_name.strip().lower() not in seen_regions:
            seen_regions.add(loc_name.strip().lower())
            farm_regions.append(loc_name.strip())

        st_val = (f.health_indicator or '').strip()
        if st_val and st_val.lower() not in seen_statuses:
            seen_statuses.add(st_val.lower())
            farm_statuses.append(st_val)
    farm_regions.sort()
    farm_statuses.sort()

    from apps.farmer.models import FarmerStaff
    from django.db.models import Q
    staffs = FarmerStaff.objects.filter(farmer=farmer_prof).order_by('name')
    managers = FarmerStaff.objects.filter(farmer=farmer_prof).filter(Q(is_role_manager=True) | Q(role__iexact='Manager')).order_by('name')

    import re
    active_sub = farmer_prof.subscriptions.filter(subscription_status='active').order_by('-id').first() if farmer_prof else None
    max_package_acres = "0"
    available_acres = "0"
    max_lands = "0"
    if active_sub and active_sub.package:
        max_package_acres = str(active_sub.package.max_acre_support).lower() if active_sub.package.max_acre_support else "unlimited"
        max_lands = str(active_sub.package.my_land).lower() if active_sub.package.my_land else "unlimited"

        if max_package_acres != "unlimited":
            match_acre = re.search(r'[\d\.]+', max_package_acres)
            if match_acre:
                max_package_acres_val = float(match_acre.group())
                max_package_acres = f"{max_package_acres_val:g}"

                total_current_area = 0.0
                for f in Farm.objects.filter(farmer=farmer_prof):
                    farm_area_match = re.search(r'[\d\.]+', str(f.total_area))
                    if farm_area_match:
                        total_current_area += float(farm_area_match.group())

                available = max_package_acres_val - total_current_area
                available_acres = f"{available:g}" if available > 0 else "0"
        else:
            available_acres = "unlimited"
            max_package_acres = "unlimited"

        if max_lands != "unlimited":
            match_land = re.search(r'[\d]+', max_lands)
            if match_land:
                max_lands = match_land.group()
    else:
        max_package_acres = "0"
        available_acres = "0"
        max_lands = "0"

    max_crops = "unlimited"
    current_active_crops_qs = CropSplit.objects.filter(farm__farmer=farmer_prof).exclude(stage='Inactive')
    if active_mgr:
        current_active_crops_qs = current_active_crops_qs.filter(
            Q(supervisors=active_mgr) | Q(supervisor=active_mgr) | Q(farm__created_by_staff=active_mgr)
        ).distinct()
    current_active_crops = current_active_crops_qs.count() if farmer_prof else 0
    crop_limit_reached = False
    if active_sub and active_sub.package:
        max_crops_str = str(active_sub.package.my_crop or '').lower().strip()
        if max_crops_str and max_crops_str != "unlimited":
            m_crop = re.search(r'\d+', max_crops_str)
            if m_crop:
                max_crops_val = int(m_crop.group())
                max_crops = str(max_crops_val)
                crop_limit_reached = (current_active_crops >= max_crops_val)

    from apps.farmer.models import FarmerCustomer, FarmerInventory, FarmerVendor
    from apps.superadmin.models import ExpenseType, ExpenseUnit, InventoryCategory
    expense_types = ExpenseType.objects.filter(is_active=True).order_by('name')
    expense_units = ExpenseUnit.objects.filter(is_active=True).order_by('name')
    inventories = FarmerInventory.objects.filter(farmer=farmer_prof, quantity__gt=0).order_by('item_name') if farmer_prof else []
    from apps.farmer.inventory_consumption import inventory_layout, is_fuel
    category_layouts = {
        category.name.strip().lower(): category.layout_type
        for category in InventoryCategory.objects.filter(is_active=True)
    }
    warehouse_inventories = []
    machinery_inventories = []
    tool_inventories = []
    for item in inventories:
        layout = inventory_layout(item, category_layouts)
        if layout == 'machinery':
            # A booked, maintenance, or otherwise unavailable vehicle must
            # never be selectable for a new consumption entry.
            if (item.status or '').strip().lower() == 'available':
                machinery_inventories.append(item)
        elif layout == 'tools':
            if (item.status or '').strip().lower() == 'available':
                tool_inventories.append(item)
        else:
            warehouse_inventories.append(item)
    fuel_inventories = [item for item in inventories if is_fuel(item) and (item.unit or '').strip().lower() in ('l', 'ltr', 'ltrs', 'litre', 'litres', 'liter', 'liters')]
    vendors = FarmerVendor.objects.filter(farmer=farmer_prof).order_by('vendor_name') if farmer_prof else []
    customers = FarmerCustomer.objects.filter(farmer=farmer_prof).order_by('name') if farmer_prof else []
    from apps.superadmin.models import TaskCategory, TaskPriority
    if not TaskCategory.objects.exists():
        TaskCategory.objects.create(name="Harvesting", description="Crop harvest tasks")
        TaskCategory.objects.create(name="Sowing", description="Seed sowing tasks")
        TaskCategory.objects.create(name="Irrigation", description="Irrigation and watering")
        TaskCategory.objects.create(name="Fertilizing", description="Fertilizer applications")
        TaskCategory.objects.create(name="Pest Control", description="Pesticide spraying and weed control")
        TaskCategory.objects.create(name="Pruning", description="Pruning and crop care")
        TaskCategory.objects.create(name="Land Preparation", description="Ploughing and soil prep")
        TaskCategory.objects.create(name="General Chores", description="General field tasks")

    if not TaskPriority.objects.exists():
        TaskPriority.objects.create(name="High Priority", description="Critical, needs immediate attention")
        TaskPriority.objects.create(name="Medium Priority", description="Important, schedule when possible")
        TaskPriority.objects.create(name="Low Priority", description="Low priority, standard chores")
    task_categories = TaskCategory.objects.filter(is_active=True).order_by('name')
    task_priorities = TaskPriority.objects.filter(is_active=True).order_by('id')

    from apps.superadmin.models import CropQuality, DocumentCategory, HealthIndicator
    if not CropQuality.objects.exists():
        CropQuality.objects.create(name="Grade 1", description="Top export grade quality")
        CropQuality.objects.create(name="Grade 2", description="Standard market quality")
        CropQuality.objects.create(name="Grade 3", description="Processing grade quality")
        CropQuality.objects.create(name="Grade 4", description="Feed or biomass grade")
    crop_qualities = CropQuality.objects.filter(is_active=True).order_by('id')

    if not HealthIndicator.objects.exists():
        HealthIndicator.objects.create(name="Excellent", description="Optimal soil and farm standing")
        HealthIndicator.objects.create(name="Good", description="Normal healthy standing")
        HealthIndicator.objects.create(name="Needs Attention", description="Requires inspection or treatment")
    health_indicators = HealthIndicator.objects.filter(is_active=True).order_by('id')

    default_doc_types = ["Patta", "Chitta", "FMB", "Sale Deed", "Lease Agreement", "Adangal", "Soil Test Report", "Water Test Report", "Organic Certificate", "Electricity Bill", "Land Survey Map", "Other"]
    for dt in default_doc_types:
        DocumentCategory.objects.get_or_create(name=dt, defaults={'document_type': 'My Land', 'description': f"Default document type: {dt}", 'is_active': True})
    document_types = DocumentCategory.objects.filter(is_active=True).order_by('id')

    # 4 Top Metric Cards (Land, Crop, Yield, Sales)
    total_area_used = 0.0
    total_area_allocated = 0.0
    for f in farms:
        try:
            total_area_used += float(f.total_area)
            total_area_allocated += float(f.allocated_area or 0)
        except (ValueError, TypeError):
            pass
    total_area_unallocated = max(0.0, total_area_used - total_area_allocated)

    available_crop_slots = 0
    if max_crops != "unlimited":
        try:
            available_crop_slots = max(0, int(max_crops) - current_active_crops)
        except ValueError:
            available_crop_slots = 0
    else:
        available_crop_slots = "unlimited"

    from apps.farmer.models import CropSplit, LedgerTransaction
    all_splits = CropSplit.objects.filter(farm__farmer=farmer_prof)
    if active_mgr:
        all_splits = all_splits.filter(
            Q(supervisors=active_mgr) | Q(supervisor=active_mgr) | Q(farm__created_by_staff=active_mgr)
        ).distinct()
    total_yield_tons = 0.0
    yield_unit_display = "Tons"
    for sp in all_splits:
        y_val = float(sp.yield_tons or sp.expected_yield or 0)
        total_yield_tons += y_val
        if sp.expected_yield_unit:
            yield_unit_display = sp.expected_yield_unit

    sales_qs = LedgerTransaction.objects.filter(
        farmer=farmer_prof,
        category__in=['Sales', 'Harvest Trade']
    ).exclude(
        category='Harvest Trade',
        description__istartswith='Harvest Trade Settlement'
    )
    total_sales_amount = float(sales_qs.aggregate(Sum('amount'))['amount__sum'] or 0.0)
    total_sales_count = sales_qs.count()

    data = {
        "yield_tons": total_yield_tons,
        "price_per_mt": 950,
        "sale_value": total_sales_amount,
        "settlement": f"₹{total_sales_amount:,.0f}",
        "total_area_used": total_area_used,
        "total_area_allocated": total_area_allocated,
        "total_area_unallocated": total_area_unallocated,
        "available_crop_slots": available_crop_slots,
        "total_yield_tons": total_yield_tons,
        "yield_unit_display": yield_unit_display,
        "total_sales_amount": total_sales_amount,
        "total_sales_count": total_sales_count,
        "states": states,
        "crop_types": crop_types,
        "crops": crops,
        "soil_types": soil_types,
        "irrigation_types": irrigation_types,
        "water_source_types": water_source_types,
        "electricity_types": electricity_types,
        "farming_types": farming_types,
        "fencing_types": fencing_types,
        "crop_categories": crop_categories,
        "crop_patterns": crop_patterns,
        "crop_varieties": crop_varieties,
        "crop_qualities": crop_qualities,
        "maintenance_schedules": maintenance_schedules,
        "document_types": document_types,
        "health_indicators": health_indicators,
        "farm_regions": farm_regions,
        "farm_statuses": farm_statuses,
        "farms": farms,
        "staffs": staffs,
        "managers": managers,
        "task_categories": task_categories,
        "task_priorities": task_priorities,
        "max_package_acres": max_package_acres,
        "available_acres": available_acres,
        "max_lands": max_lands,
        "max_crops": max_crops,
        "current_active_crops": current_active_crops,
        "crop_limit_reached": crop_limit_reached,
        "inventories": inventories,
        "warehouse_inventories": warehouse_inventories,
        "machinery_inventories": machinery_inventories,
        "tool_inventories": tool_inventories,
        "fuel_inventories": fuel_inventories,
        "vendors": vendors,
        "customers": customers,
        "expense_types": expense_types,
        "expense_units": expense_units,
        "is_acting_as_manager": bool(active_mgr),
        "active_manager": active_mgr,
    }
    return render(request, "farmer/farms/manage_farm.html", data)

def delete_farm(request):
    import re
    from django.contrib import messages
    from django.shortcuts import redirect
    from django.http import JsonResponse
    from apps.farmer.models import Farm, CropSplit, FarmTask, InventoryStockLog, FarmActivity, LedgerTransaction
    from django.db.models import Q

    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('is_ajax') == '1'

    if get_active_manager_staff(request):
        if is_ajax:
            return JsonResponse({'success': False, 'error': 'Managers do not have permission to delete farm land.'})
        messages.error(request, "Managers do not have permission to delete farm land.")
        return redirect('farmer-farms')

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        farm_id = request.POST.get('farm_id')
        action = request.POST.get('action')

        if farm_id and farmer_prof:
            try:
                farm = Farm.objects.get(id=farm_id, farmer=farmer_prof)
                land_name = farm.land_name

                # 1. Action: Mark as Inactive
                if action == 'set_inactive':
                    farm.health_indicator = 'Inactive'
                    notes = str(farm.notes or '')
                    if '[INACTIVE]' not in notes:
                        notes = f"{notes} [INACTIVE]".strip()
                    farm.notes = notes
                    farm.save()

                    # Inactivate any active crops on this farm as well
                    for split in farm.splits.exclude(stage__in=['Inactive', 'Harvested', 'Sold']):
                        split_notes = str(split.crop_notes or '')
                        split_notes = re.sub(r'\[PREV_STAGE:[^\]]+\]', '', split_notes).strip()
                        split_notes = f"{split_notes} [PREV_STAGE:{split.stage}]".strip()
                        split.crop_notes = split_notes
                        split.stage = 'Inactive'
                        split.save()

                    active_mgr = get_active_manager_staff(request)
                    op_name = active_mgr.name if active_mgr else (request.user.first_name or get_actor_name(request))
                    FarmActivity.objects.create(
                        farm=farm,
                        activity_type="Other Land Care",
                        asset_name=f"Farm '{land_name}' Inactivated",
                        quantity_metric=f"{farm.total_area} Ac",
                        operator_name=op_name,
                        notes=f"Farm land marked Inactive by {op_name}."
                    )
                    log_action(request, "Farm Inactivated", f"Farm '{land_name}' marked as Inactive.", icon_class="ri-pause-circle-line", bg_color="warning")
                    if is_ajax:
                        return JsonResponse({
                            'success': True,
                            'inactivated': True,
                            'message': f"Farm '{land_name}' has been marked as Inactive."
                        })
                    messages.success(request, f"Farm '{land_name}' has been marked as Inactive.")
                    return redirect('farmer-farms')

                # 2. Check for child module dependencies before allowing hard delete
                # A) Crop splits
                crop_count = farm.splits.count()

                # B) Tasks
                task_count = FarmTask.objects.filter(farm=farm).count()

                # C) Resource Consumptions
                stock_logs_count = InventoryStockLog.objects.filter(farm=farm).count()
                consumption_acts_count = FarmActivity.objects.filter(
                    farm=farm,
                    activity_type="Resource Consumption"
                ).count()
                consumption_count = stock_logs_count + consumption_acts_count

                # D) Expenses / Ledger Transactions
                farm_tag = f"Farm ID: {farm.id}"
                expense_count = LedgerTransaction.objects.filter(farmer=farmer_prof).filter(
                    Q(farm=farm) | Q(tags__icontains=farm_tag) | Q(description__icontains=farm_tag)
                ).count()

                dependencies = []
                if crop_count > 0:
                    dependencies.append(f"{crop_count} Crop{'s' if crop_count > 1 else ''}")
                if task_count > 0:
                    dependencies.append(f"{task_count} Task{'s' if task_count > 1 else ''}")
                if consumption_count > 0:
                    dependencies.append(f"{consumption_count} Consumption log{'s' if consumption_count > 1 else ''}")
                if expense_count > 0:
                    dependencies.append(f"{expense_count} Expense transaction{'s' if expense_count > 1 else ''}")

                # If ANY module record has been created, BLOCK hard deletion
                if dependencies:
                    dep_str = ", ".join(dependencies)
                    err_msg = f"Cannot delete '{land_name}' because it has linked records ({dep_str}). You can mark it as Inactive instead."
                    if is_ajax:
                        return JsonResponse({
                            'success': False,
                            'has_dependencies': True,
                            'dependencies': dependencies,
                            'farm_id': farm.id,
                            'land_name': land_name,
                            'error': err_msg
                        })
                    messages.warning(request, err_msg)
                    return redirect('farmer-farms')

                # 3. Clean up setup logs and delete farm if NO dependencies exist
                FarmActivity.objects.filter(farm=farm).delete()
                farm.delete()
                log_action(request, "Farm Deleted", f"Farm '{land_name}' was deleted.", icon_class="ri-delete-bin-line", bg_color="danger")
                if is_ajax:
                    return JsonResponse({'success': True, 'message': f"Farm '{land_name}' deleted successfully!"})
                messages.success(request, "Farm deleted successfully!")
            except Farm.DoesNotExist:
                if is_ajax:
                    return JsonResponse({'success': False, 'error': "Farm not found or you don't have permission."})
                messages.error(request, "Farm not found or you don't have permission.")
            except Exception as e:
                if is_ajax:
                    return JsonResponse({'success': False, 'error': str(e)})
                messages.error(request, f"Error deleting farm: {str(e)}")
    return redirect('farmer-farms')

def update_farm(request):
    if request.method == "POST":
        from apps.farmer.validation import pincode_error
        error = pincode_error(request)
        if error is not None:
            return error

    from django.contrib import messages
    from django.shortcuts import redirect
    from apps.farmer.models import Farm

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        farm_id = request.POST.get('farm_id')
        if farm_id and farmer_prof:
            try:
                farm = Farm.objects.get(id=farm_id, farmer=farmer_prof)

                # --- Acreage Validation Logic for Update ---
                import re
                total_area_str = request.POST.get("total_area", "")
                new_area_match = re.search(r'[\d\.]+', str(total_area_str))
                new_area = float(new_area_match.group()) if new_area_match else 0.0

                old_area_match = re.search(r'[\d\.]+', str(farm.total_area))
                old_area = float(old_area_match.group()) if old_area_match else 0.0

                active_sub = farmer_prof.subscriptions.filter(subscription_status='active').order_by('-id').first()
                if active_sub and active_sub.package:
                    max_acres_str = str(active_sub.package.max_acre_support).lower() if active_sub.package.max_acre_support else "unlimited"
                    if max_acres_str != "unlimited":
                        match_acre = re.search(r'[\d\.]+', max_acres_str)
                        if match_acre:
                            max_acres = float(match_acre.group())
                            other_farms = Farm.objects.filter(farmer=farmer_prof).exclude(id=farm.id)
                            other_farms_area = 0.0
                            for f_obj in other_farms:
                                farm_area_match = re.search(r'[\d\.]+', str(f_obj.total_area))
                                if farm_area_match:
                                    other_farms_area += float(farm_area_match.group())

                            # Check quota only if user is expanding the farm area
                            if new_area > old_area and (other_farms_area + new_area) > max_acres:
                                available_acres = max(0.0, max_acres - other_farms_area)
                                messages.error(request, f"Cannot update to {new_area} acres. Your package allows a maximum of {max_acres} acres (Available: {available_acres:.1f} acres).")
                                return redirect('farmer-farms')
                # --------------------------------

                farm.land_name = request.POST.get("land_name")
                farm.total_area = total_area_str
                raw_crop_ids = request.POST.getlist("crop_type") + request.POST.getlist("crops") + request.POST.getlist("crop")
                crop_ids = [str(c).strip() for c in raw_crop_ids if c and str(c).strip()]
                crop_name = request.POST.get("crop_name", "").strip()

                from apps.superadmin.models import Crop, CropType
                crop_objs = []
                if crop_ids:
                    crops_dict = {str(c.id): c for c in Crop.objects.filter(id__in=crop_ids)}
                    for cid in crop_ids:
                        if cid in crops_dict and crops_dict[cid] not in crop_objs:
                            crop_objs.append(crops_dict[cid])
                elif crop_name:
                    crop_obj = Crop.objects.filter(name__iexact=crop_name).first()
                    if not crop_obj:
                        crop_type_obj = CropType.objects.filter(name__iexact=crop_name).first()
                        if not crop_type_obj:
                            crop_type_obj = CropType.objects.create(name=crop_name, description=f"Auto-created crop type for {crop_name}", is_active=True)
                        crop_obj = Crop.objects.create(name=crop_name, crop_type=crop_type_obj, description=f"Auto-created crop: {crop_name}", is_active=True)
                    crop_objs = [crop_obj]

                primary_crop = crop_objs[0] if crop_objs else None
                primary_crop_type = primary_crop.crop_type if primary_crop and primary_crop.crop_type else None

                farm.crop = primary_crop
                farm.crop_type = primary_crop_type
                farm.crops.set(crop_objs)
                farm.latitude = request.POST.get("latitude")
                farm.longitude = request.POST.get("longitude")
                state_id = request.POST.get("state")
                farm.state_id = state_id if state_id else None
                district_id = request.POST.get("district")
                farm.district_id = district_id if district_id else None
                taluk_id = request.POST.get("taluk")
                farm.taluk_id = taluk_id if taluk_id else None
                farm.street = request.POST.get("street")
                farm.door = request.POST.get("door")
                farm.pincode = request.POST.get("pincode")
                farm.address = request.POST.get("address")
                farm.soil_type = request.POST.get("soil_type")
                farm.soil_ph = request.POST.get("soil_ph")
                farm.irrigation_type = request.POST.get("irrigation_type")
                health_indicator = request.POST.get("health_indicator")
                farm.health_indicator = health_indicator if health_indicator else "Good"

                adv_data = parse_farm_advance_fields(request, existing_farm=farm)
                farm.notes = adv_data["notes"]
                farm.farming_type = adv_data["farming_type"]
                farm.organic_registration_number = adv_data["organic_registration_number"]
                farm.fencing_type = adv_data["fencing_type"]
                farm.fencing_maintenance_schedule = adv_data["fencing_maintenance_schedule"]
                farm.fencing_details = adv_data["fencing_details"]
                if adv_data["land_allocation_blueprint"]:
                    farm.land_allocation_blueprint = adv_data["land_allocation_blueprint"]
                farm.land_allocation_description = adv_data["land_allocation_description"]
                farm.water_sources = adv_data["water_sources"]
                farm.electricity_details = adv_data["electricity_details"]
                farm.farm_documents = adv_data["farm_documents"]

                farm.save()

                log_action(request, "Farm Updated", f"Farm '{farm.land_name}' details were updated.", icon_class="ri-pencil-line", bg_color="info")
                messages.success(request, "Farm updated successfully!")
            except Farm.DoesNotExist:
                messages.error(request, "Farm not found or you don't have permission.")
    return redirect('farmer-farms')

def farm_profile_api(request, farm_id):
    from django.http import JsonResponse
    from django.db.models import Q, Sum
    from apps.farmer.models import Farm

    farmer_prof = get_farmer_profile(request)
    active_mgr = get_active_manager_staff(request)

    try:
        if active_mgr:
            mgr_farm_filter = Q(created_by_staff=active_mgr) | Q(splits__supervisors=active_mgr) | Q(splits__supervisor=active_mgr)
            farm = Farm.objects.filter(id=farm_id, farmer=farmer_prof).filter(mgr_farm_filter).distinct().first()
            if not farm:
                return JsonResponse({'success': False, 'error': 'Farm not found or access denied.'}, status=404)
            splits_qs = farm.splits.filter(
                Q(supervisors=active_mgr) | Q(supervisor=active_mgr) | Q(farm__created_by_staff=active_mgr)
            ).distinct()
        else:
            farm = Farm.objects.get(id=farm_id, farmer=farmer_prof)
            splits_qs = farm.splits.all()

        splits_models = {cs.id: cs for cs in splits_qs.prefetch_related('supervisors', 'supervisor')}
        splits = list(splits_qs.order_by('-id').values(
            'id', 'crop_id', 'crop__name', 'crop_category', 'crop_pattern', 'crop_variety',
            'harvest_type', 'total_flushes', 'current_flush', 'acreage', 'crop_latitude', 'crop_longitude', 'row_count', 'plant_count',
            'growth_duration', 'duration_min', 'duration_max', 'start_date', 'stage',
            'supervisor_id', 'supervisor__name', 'cultivation_method',
            'expected_yield', 'expected_yield_unit', 'crop_notes', 'harvest_batches', 'seed_details', 'plant_spacing',
            'yield_tons', 'price_per_mt', 'total_sale_value', 'has_advanced_details'
        ))
        from apps.farmer.models import CropSplit
        from apps.superadmin.models import DepotCropPricing, DepotTerminal, MarketPriceIndex

        for split in splits:
            cs_obj = splits_models.get(split['id'])
            if cs_obj:
                sup_list = list(cs_obj.supervisors.all())
                if not sup_list and cs_obj.supervisor:
                    sup_list = [cs_obj.supervisor]
                split['supervisor_ids'] = [s.id for s in sup_list]
                split['supervisor_names'] = [s.name for s in sup_list]
                if sup_list:
                    split['supervisor__name'] = ', '.join([s.name for s in sup_list])
            else:
                split['supervisor_ids'] = [split['supervisor_id']] if split.get('supervisor_id') else []
                split['supervisor_names'] = [split['supervisor__name']] if split.get('supervisor__name') else []

            split['start_date_formatted'] = split['start_date'].strftime('%Y-%m-%d') if split['start_date'] else ''
            if split['yield_tons'] is not None:
                y_val = float(split['yield_tons'])
                split['yield_tons'] = int(y_val) if y_val.is_integer() else y_val
            if split['expected_yield'] is not None:
                ey_val = float(split['expected_yield'])
                split['expected_yield'] = int(ey_val) if ey_val.is_integer() else ey_val
            split['is_completed'] = bool(split.get('crop_notes') and '[ALL_FLUSHES_COMPLETED]' in split['crop_notes'])
            if split['price_per_mt'] is not None:
                p_val = float(split['price_per_mt'])
                split['price_per_mt'] = int(p_val) if p_val.is_integer() else p_val

            c_name = split.get('crop__name')
            market_price = 0.0
            market_price_unit = ''
            if c_name:
                clean_name = c_name.split('(')[0].strip()

                farmer_city = farmer_prof.district.name.lower() if farmer_prof and farmer_prof.district else None
                if farmer_city:
                    # 1. Local DepotCropPricing
                    local_dcp = DepotCropPricing.objects.filter(
                        crop_index__crop_name__icontains=clean_name, 
                        terminal_rate__gt=0
                    ).filter(
                        Q(depot__district__icontains=farmer_city) | Q(depot__location__icontains=farmer_city)
                    ).first()
                    
                    if local_dcp and local_dcp.terminal_rate and float(local_dcp.terminal_rate) > 0:
                        market_price = float(local_dcp.terminal_rate)
                        if local_dcp.crop_index and local_dcp.crop_index.unit:
                            market_price_unit = local_dcp.crop_index.unit
                    
                    if market_price == 0.0:
                        # 2. Local DepotTerminal available_crops
                        local_terminals = DepotTerminal.objects.filter(
                            Q(district__icontains=farmer_city) | Q(location__icontains=farmer_city)
                        )
                        for terminal in local_terminals:
                            if isinstance(terminal.available_crops, list):
                                for crop_item in terminal.available_crops:
                                    if isinstance(crop_item, dict):
                                        item_name = crop_item.get('name', '')
                                        try:
                                            price_val = float(crop_item.get('price', 0))
                                        except (ValueError, TypeError):
                                            price_val = 0.0
                                        if clean_name.lower() in item_name.lower() and price_val > 0:
                                            market_price = price_val
                                            market_price_unit = crop_item.get('unit', '')
                                            break
                                    if market_price > 0:
                                        break
                                
                if market_price == 0.0:
                    # 3. Fallback to global MarketPriceIndex only
                    mpi = MarketPriceIndex.objects.filter(Q(crop_name__iexact=c_name) | Q(crop_name__iexact=clean_name)).first()
                    if not mpi:
                        mpi = MarketPriceIndex.objects.filter(crop_name__icontains=clean_name).first()

                    if mpi and mpi.base_price and float(mpi.base_price) > 0:
                        market_price = float(mpi.base_price)
                        market_price_unit = mpi.unit or ''

            split['market_price'] = int(market_price) if market_price.is_integer() else market_price
            split['market_price_unit'] = market_price_unit

            # Clean crop_notes if it was legacy JSON
            if split.get('crop_notes') and str(split['crop_notes']).strip().startswith('['):
                split['crop_notes'] = ''

            # Parse harvest_batches
            h_batches = split.get('harvest_batches')
            if isinstance(h_batches, str):
                import json
                try:
                    split['harvest_batches'] = json.loads(h_batches)
                except Exception:
                    split['harvest_batches'] = []
            elif isinstance(h_batches, list):
                split['harvest_batches'] = h_batches
            else:
                split['harvest_batches'] = []
            from apps.farmer.harvest_batches import normalize_batch_numbers
            split['harvest_batches'] = normalize_batch_numbers(split['harvest_batches'])
        activities = list(farm.activities.all().order_by('-created_at', '-id').values(
            'id', 'activity_type', 'asset_name', 'quantity_metric',
            'operator_name', 'notes', 'amount', 'created_at'
        ))

        # format dates and amounts
        for a in activities:
            a['created_at_formatted'] = a['created_at'].strftime('%Y-%m-%d')
            a['created_at_iso'] = a['created_at'].isoformat() if a.get('created_at') else ''
            a['amount'] = float(a['amount']) if a['amount'] else 0.0

        from django.db.models import Sum as _Sum
        total_consumption_value = farm.activities.filter(
            activity_type="Resource Consumption"
        ).aggregate(_Sum('amount'))['amount__sum'] or 0
        total_consumption_value = float(total_consumption_value)

        tasks = []
        for t in farm.tasks.all().order_by('-created_at', '-id'):
            worker_list = [{'id': w.id, 'name': w.name} for w in t.assigned_workers.all()]
            if not worker_list and t.assigned_worker:
                worker_list = [{'id': t.assigned_worker.id, 'name': t.assigned_worker.name}]
            tasks.append({
                'id': t.id,
                'title': t.title,
                'description': t.description or '',
                'priority': t.priority or (t.priority_level.name if t.priority_level else 'Medium Priority'),
                'priority_level_id': t.priority_level.id if t.priority_level else '',
                'task_category_id': t.task_category.id if t.task_category else '',
                'task_category_name': t.task_category.name if t.task_category else '',
                'work_timeline': t.work_timeline or 'Full Day',
                'associated_split_id': t.associated_split.id if t.associated_split else None,
                'crop_name': t.associated_split.crop.name if t.associated_split and t.associated_split.crop else '',
                'assigned_worker__name': ', '.join([w['name'] for w in worker_list]) if worker_list else '',
                'assigned_workers': worker_list,
                'status': t.status or 'Pending',
                'start_date': t.start_date.strftime('%Y-%m-%d') if t.start_date else (t.task_date.strftime('%Y-%m-%d') if t.task_date else ''),
                'end_date': t.end_date.strftime('%Y-%m-%d') if t.end_date else '',
                'start_time': t.start_time.strftime('%H:%M') if t.start_time else '',
                'end_time': t.end_time.strftime('%H:%M') if t.end_time else '',
                'start_time_formatted': t.start_time.strftime('%I:%M %p') if t.start_time else '',
                'end_time_formatted': t.end_time.strftime('%I:%M %p') if t.end_time else '',
                'total_hours': float(t.total_hours) if t.total_hours is not None else '',
                'actual_hours': float(t.actual_hours) if t.actual_hours is not None else '',
                'created_at_formatted': t.created_at.strftime('%Y-%m-%d') if t.created_at else '',
                'created_at_iso': t.created_at.isoformat() if t.created_at else '',
                'farm_id': t.farm.id if t.farm else farm.id,
                'farm_name': t.farm.land_name if t.farm else farm.land_name,
            })

        from apps.farmer.models import FarmerStaff, LedgerTransaction
        workers = list(FarmerStaff.objects.filter(farmer=farmer_prof).values('id', 'name', 'role'))
        managers = list(FarmerStaff.objects.filter(farmer=farmer_prof).filter(Q(is_role_manager=True) | Q(role__iexact='Manager')).values('id', 'name', 'role'))

        try:
            from apps.farmer.recurring_utils import process_recurring_expenses
            process_recurring_expenses(farmer_prof, request=request)
        except Exception:
            pass

        # Fetch expenses specific to this farm
        expenses_qs = LedgerTransaction.objects.filter(
            farmer=farmer_prof
        ).filter(
            Q(farm=farm) | Q(tags__icontains=f"Farm ID: {farm.id}")
        ).filter(
            Q(status__in=['Cost', 'Paid']) | Q(amount__lt=0)
        )

        total_budget = sum(abs(float(exp.amount)) for exp in expenses_qs)

        seeds_cost = sum(
            abs(float(exp.amount)) for exp in expenses_qs
            if exp.category and ('seed' in exp.category.lower() or 'seed' in exp.description.lower())
        )

        fertilizers_cost = sum(
            abs(float(exp.amount)) for exp in expenses_qs
            if exp.category and ('fertilizer' in exp.category.lower() or 'fertilizer' in exp.description.lower() or 'input' in exp.category.lower() or 'nutrient' in exp.category.lower())
        )

        expenses = list(expenses_qs.order_by('-close_date', '-id').values(
            'id', 'description', 'category', 'amount', 'balance_due', 'close_date', 'tags', 'status', 'payment_method', 'created_at'
        ))
        for exp in expenses:
            exp['date_formatted'] = exp['close_date'].strftime('%Y-%m-%d') if exp['close_date'] else ''
            exp['created_at_iso'] = exp['created_at'].isoformat() if exp.get('created_at') else ''
            actual = abs(float(exp['amount'] or 0))
            bal = float(exp.get('balance_due') or 0)
            paid = max(0.0, actual - bal)
            exp['amount'] = actual
            exp['actual_price'] = actual
            exp['balance_due'] = bal
            exp['paid_amount'] = paid

        # Fetch sales for this farm
        sales_qs = LedgerTransaction.objects.filter(
            farmer=farmer_prof
        ).exclude(
            category='Harvest Trade',
            description__istartswith='Harvest Trade Settlement'
        ).filter(
            Q(farm=farm) | Q(tags__icontains=f"Farm ID: {farm.id}") | Q(tags__icontains="CropSplit #")
        ).filter(
            Q(category__in=['Sales', 'Harvest Trade']) | Q(status__in=['Received', 'Income', 'Settled', 'Paid', 'Pending'])
        ).order_by('-close_date', '-id')

        sales = list(sales_qs.values(
            'id', 'description', 'category', 'amount', 'quantity', 'unit', 'close_date', 'status', 'tags', 'payment_method', 'created_at'
        ))
        import re
        for s in sales:
            s['date_formatted'] = s['close_date'].strftime('%d %b %Y') if s['close_date'] else ''
            s['created_at_iso'] = s['created_at'].isoformat() if s.get('created_at') else ''
            s['amount'] = float(s['amount'])
            s['quantity'] = float(s['quantity']) if s['quantity'] else None

            # Parse paid and pending amounts from description or status
            desc = s.get('description') or ''
            m = re.search(r'\[Paid:\s*₹?([\d.,]+),\s*Pending:\s*₹?([\d.,]+)\]', desc)
            if m:
                try:
                    s['paid'] = float(m.group(1).replace(',', ''))
                    s['pending'] = float(m.group(2).replace(',', ''))
                except (ValueError, TypeError):
                    s['paid'] = s['amount']
                    s['pending'] = 0.0
            elif s.get('status') in ['Received', 'Paid', 'Income', 'Settled']:
                s['paid'] = s['amount']
                s['pending'] = 0.0
            elif s.get('status') == 'Pending':
                s['paid'] = 0.0
                s['pending'] = s['amount']
            else:
                s['paid'] = s['amount']
                s['pending'] = 0.0

            # Extract customer name if 'to <Customer>'
            cust_m = re.search(r'to\s+([^\[\(]+)', desc)
            if cust_m:
                s['customer'] = cust_m.group(1).strip()
            else:
                s['customer'] = ''

        active_sub = farmer_prof.subscriptions.filter(subscription_status='active').order_by('-id').first() if farmer_prof else None
        max_crops = "unlimited"
        current_active_crops_qs = CropSplit.objects.filter(farm__farmer=farmer_prof).exclude(stage='Inactive')
        if active_mgr:
            current_active_crops_qs = current_active_crops_qs.filter(
                Q(supervisors=active_mgr) | Q(supervisor=active_mgr) | Q(farm__created_by_staff=active_mgr)
            ).distinct()
        current_active_crops = current_active_crops_qs.count() if farmer_prof else 0
        crop_limit_reached = False
        if active_sub and active_sub.package:
            max_crops_str = str(active_sub.package.my_crop or '').lower().strip()
            if max_crops_str and max_crops_str != "unlimited":
                m_crop = re.search(r'\d+', max_crops_str)
                if m_crop:
                    max_crops_val = int(m_crop.group())
                    max_crops = str(max_crops_val)
                    crop_limit_reached = (current_active_crops >= max_crops_val)

        return JsonResponse({
            'success': True,
            'is_acting_as_manager': bool(active_mgr),
            'farm': {
                'id': farm.id,
                'name': farm.land_name,
                'area': farm.total_area,
                'lat': farm.latitude,
                'long': farm.longitude,
                'health': farm.health_indicator,
            },
            'splits': splits,
            'activities': activities,
            'tasks': tasks,
            'workers': workers,
            'managers': managers,
            'expenses': expenses,
            'sales': sales,
            'total_consumption_value': total_consumption_value,
            'total_budget': total_budget,
            'seeds_cost': seeds_cost,
            'fertilizers_cost': fertilizers_cost,
            'max_crops': max_crops,
            'current_active_crops': current_active_crops,
            'crop_limit_reached': crop_limit_reached,
        })
    except Farm.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Farm not found'}, status=404)


def get_manager_crops_api(request):
    """
    Returns list of managers and all farms & crop splits for assignment configuration.
    """
    farmer_prof = get_farmer_profile(request.user)
    if not farmer_prof:
        return JsonResponse({'success': False, 'error': 'Farmer profile not found.'}, status=403)

    from apps.farmer.models import Farm, CropSplit, FarmerStaff
    from django.db.models import Q

    managers_qs = FarmerStaff.objects.filter(farmer=farmer_prof).filter(
        Q(is_role_manager=True) | Q(role__iexact='Manager')
    ).order_by('name')

    manager_id = request.GET.get('manager_id')
    selected_mgr = None
    if manager_id:
        try:
            selected_mgr = managers_qs.filter(id=int(manager_id)).first()
        except (ValueError, TypeError):
            selected_mgr = None
    elif managers_qs.exists():
        selected_mgr = managers_qs.first()

    managers_data = []
    for m in managers_qs:
        assigned_crops_cnt = CropSplit.objects.filter(farm__farmer=farmer_prof).filter(
            Q(supervisors=m) | Q(supervisor=m)
        ).distinct().count()
        assigned_farms_cnt = Farm.objects.filter(farmer=farmer_prof).filter(
            Q(created_by_staff=m) | Q(splits__supervisors=m) | Q(splits__supervisor=m)
        ).distinct().count()
        managers_data.append({
            'id': m.id,
            'name': m.name,
            'mobile': m.mobile_number or '',
            'role': m.role or 'Manager',
            'assigned_crops_count': assigned_crops_cnt,
            'assigned_farms_count': assigned_farms_cnt,
            'is_selected': bool(selected_mgr and selected_mgr.id == m.id)
        })

    farms_qs = Farm.objects.filter(farmer=farmer_prof).prefetch_related('splits__crop', 'splits__supervisor', 'splits__supervisors').order_by('land_name')
    farms_data = []
    total_assigned_to_selected = 0
    total_crops_overall = 0

    for f in farms_qs:
        f_splits = f.splits.all().order_by('-id')
        crops_list = []
        farm_has_assigned = False

        for sp in f_splits:
            total_crops_overall += 1
            sup_objs = list(sp.supervisors.all())
            if not sup_objs and sp.supervisor:
                sup_objs = [sp.supervisor]
            sup_ids = [s.id for s in sup_objs]
            sup_names = [s.name for s in sup_objs]

            is_assigned = (selected_mgr is not None and selected_mgr.id in sup_ids)
            if is_assigned:
                total_assigned_to_selected += 1
                farm_has_assigned = True

            other_supervisors = [s.name for s in sup_objs if not selected_mgr or s.id != selected_mgr.id]

            crops_list.append({
                'id': sp.id,
                'crop_name': sp.crop.name if sp.crop else 'Crop Field',
                'crop_variety': sp.crop_variety or '',
                'acreage': float(sp.acreage or 0),
                'stage': sp.stage or 'Sown',
                'supervisor_id': sp.supervisor_id,
                'supervisor_name': ', '.join(sup_names) if sup_names else 'Unassigned',
                'supervisors': [{'id': s.id, 'name': s.name} for s in sup_objs],
                'is_assigned': is_assigned,
                'is_assigned_to_other': len(other_supervisors) > 0,
                'other_supervisors': other_supervisors
            })

        farms_data.append({
            'id': f.id,
            'land_name': f.land_name,
            'total_area': float(f.total_area or 0),
            'crops_count': len(crops_list),
            'has_assigned_crops': farm_has_assigned,
            'crops': crops_list
        })

    return JsonResponse({
        'success': True,
        'managers': managers_data,
        'selected_manager': {
            'id': selected_mgr.id,
            'name': selected_mgr.name,
            'mobile': selected_mgr.mobile_number or '',
        } if selected_mgr else None,
        'farms': farms_data,
        'total_crops': total_crops_overall,
        'total_assigned': total_assigned_to_selected
    })


@csrf_exempt
def save_manager_crops_api(request):
    """
    Saves multiple crop assignments for a specific manager without overwriting other managers.
    Request body (JSON or POST):
    - manager_id: int
    - crop_split_ids: list of int
    """
    if request.method != "POST":
        return JsonResponse({'success': False, 'error': 'POST method required.'}, status=405)

    farmer_prof = get_farmer_profile(request.user)
    if not farmer_prof:
        return JsonResponse({'success': False, 'error': 'Farmer profile not found.'}, status=403)

    import json
    from apps.farmer.models import Farm, CropSplit, FarmerStaff
    from django.db.models import Q

    try:
        if request.content_type and 'application/json' in request.content_type:
            payload = json.loads(request.body.decode('utf-8'))
        else:
            payload = request.POST

        manager_id = payload.get('manager_id')
        if not manager_id:
            return JsonResponse({'success': False, 'error': 'Manager selection is required.'}, status=400)

        manager = FarmerStaff.objects.filter(id=manager_id, farmer=farmer_prof).filter(
            Q(is_role_manager=True) | Q(role__iexact='Manager')
        ).first()

        if not manager:
            return JsonResponse({'success': False, 'error': 'Selected manager not found or not authorized.'}, status=404)

        raw_split_ids = payload.get('crop_split_ids')
        if isinstance(raw_split_ids, str):
            try:
                raw_split_ids = json.loads(raw_split_ids)
            except Exception:
                raw_split_ids = [s.strip() for s in raw_split_ids.split(',') if s.strip()]

        if raw_split_ids is None:
            raw_split_ids = []

        valid_split_ids = []
        for sid in raw_split_ids:
            try:
                valid_split_ids.append(int(sid))
            except (ValueError, TypeError):
                pass

        # Only IDs belonging to this farmer may be persisted or counted.
        crops_to_add = list(CropSplit.objects.filter(
            farm__farmer=farmer_prof,
            id__in=valid_split_ids
        ).distinct())
        valid_split_ids = [cs.id for cs in crops_to_add]

        with transaction.atomic():
            # 1. Unassign crops previously assigned to this manager that are no
            # longer checked. Keep any other manager assignments intact.
            crops_to_remove = CropSplit.objects.filter(
                farm__farmer=farmer_prof
            ).filter(
                Q(supervisors=manager) | Q(supervisor=manager)
            ).exclude(id__in=valid_split_ids).distinct()

            for cs in crops_to_remove:
                cs.supervisors.remove(manager)
                if cs.supervisor_id == manager.id:
                    cs.supervisor = cs.supervisors.first()
                    cs.save(update_fields=['supervisor'])

            # 2. Assign every currently checked crop to this manager.
            for cs in crops_to_add:
                cs.supervisors.add(manager)
                if not cs.supervisor_id:
                    cs.supervisor = manager
                    cs.save(update_fields=['supervisor'])

        assigned_count = len(crops_to_add)

        # 3. Get distinct lands where this manager now has crops
        assigned_lands_count = Farm.objects.filter(
            farmer=farmer_prof
        ).filter(
            Q(created_by_staff=manager) | Q(splits__supervisors=manager) | Q(splits__supervisor=manager)
        ).distinct().count()

        log_action(
            request,
            title="Manager Crop Assignments Updated",
            message=f"Assigned {assigned_count} crop(s) across {assigned_lands_count} land(s) to Manager {manager.name}.",
            icon_class="ri-user-shared-line",
            bg_color="success"
        )

        return JsonResponse({
            'success': True,
            'message': f'Updated assignments for {manager.name}: {assigned_count} crop(s) assigned across {assigned_lands_count} land(s).',
            'assigned_count': assigned_count,
            'assigned_lands_count': assigned_lands_count
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def parse_crop_split_fields(request):
    data = {}
    data['crop_category'] = request.POST.get('crop_category') or None
    data['crop_pattern'] = request.POST.get('crop_pattern') or None
    data['crop_variety'] = request.POST.get('crop_variety') or None
    data['harvest_type'] = request.POST.get('harvest_type') or 'single'
    total_flushes_str = request.POST.get('total_flushes')
    if data['harvest_type'] in ['multiple', 'Multiple Harvest']:
        if total_flushes_str:
            try:
                data['total_flushes'] = max(2, int(total_flushes_str))
            except ValueError:
                data['total_flushes'] = None
        else:
            data['total_flushes'] = None
    else:
        data['total_flushes'] = 1
    
    sowing_date_str = request.POST.get('sowing_date')
    if sowing_date_str:
        from datetime import datetime
        try:
            data['start_date'] = datetime.strptime(sowing_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
            
    lat_str = request.POST.get('crop_latitude')
    if lat_str:
        try: data['crop_latitude'] = float(lat_str)
        except ValueError: pass
        
    long_str = request.POST.get('crop_longitude')
    if long_str:
        try: data['crop_longitude'] = float(long_str)
        except ValueError: pass
        
    row_count_str = request.POST.get('row_count')
    if row_count_str:
        try: data['row_count'] = int(row_count_str)
        except ValueError: pass
        
    plant_count_str = request.POST.get('plant_count')
    if plant_count_str:
        try: data['plant_count'] = int(plant_count_str)
        except ValueError: pass
        
    dur_min_str = request.POST.get('duration_min')
    if dur_min_str:
        try: data['duration_min'] = int(dur_min_str)
        except ValueError: pass
        
    dur_max_str = request.POST.get('duration_max')
    if dur_max_str:
        try: data['duration_max'] = int(dur_max_str)
        except ValueError: pass
        
    yield_str = request.POST.get('expected_yield')
    if yield_str:
        try: data['expected_yield'] = float(yield_str)
        except ValueError: pass
        
    data['expected_yield_unit'] = request.POST.get('expected_yield_unit') or 'Ton'
    data['crop_notes'] = request.POST.get('crop_notes') or None
    data['seed_details'] = request.POST.get('seed_details') or None
    data['plant_spacing'] = request.POST.get('plant_spacing') or None
    data['cultivation_method'] = (request.POST.get('cultivation_method') or '').strip() or None
    
    return data

def add_crop_split(request):
    from django.contrib import messages
    from django.http import JsonResponse
    from django.shortcuts import redirect
    from apps.farmer.models import CropSplit, Farm
    from apps.superadmin.models import Crop, CropType

    farmer_prof = get_farmer_profile(request.user)
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('is_ajax') == 'true'

    if request.method == "POST":
        farm_id = request.POST.get('farm_id')
        if farm_id and farmer_prof:
            try:
                farm = Farm.objects.get(id=farm_id, farmer=farmer_prof)
                crop_id = request.POST.get('crop_type') or request.POST.get('crop_id')
                custom_crop_name = (request.POST.get('crop_name') or '').strip()
                if not crop_id and custom_crop_name:
                    default_ct = CropType.objects.first()
                    crop_obj, _ = Crop.objects.get_or_create(
                        name=custom_crop_name,
                        defaults={'crop_type': default_ct, 'is_active': True}
                    )
                    crop_id = crop_obj.id

                if not crop_id:
                    err_msg = "Please select or enter a valid crop."
                    if is_ajax: return JsonResponse({'success': False, 'error': err_msg})
                    messages.error(request, err_msg)
                    return redirect('farmer-farms')

                acreage = request.POST.get('acreage') or request.POST.get('crop_area') or '1'
                duration = request.POST.get('growth_duration') or request.POST.get('duration_min') or 90
                raw_supervisors = request.POST.getlist('supervisors') or request.POST.getlist('supervisor')
                supervisor_ids = [int(s) for s in raw_supervisors if str(s).isdigit() and int(s) > 0]
                active_mgr = get_active_manager_staff(request)
                if active_mgr and active_mgr.id not in supervisor_ids:
                    supervisor_ids.append(active_mgr.id)
                primary_supervisor_id = supervisor_ids[0] if supervisor_ids else None

                # Check Crop count limit
                import re
                from django.db.models import Sum

                active_sub = farmer_prof.subscriptions.filter(subscription_status='active').order_by('-id').first()
                if not active_sub or not active_sub.package:
                    err_msg = "No active subscription found. Please subscribe to a package first."
                    if is_ajax: return JsonResponse({'success': False, 'error': err_msg})
                    messages.error(request, err_msg)
                    return redirect('farmer-farms')

                max_crops_str = str(active_sub.package.my_crop).lower() if active_sub.package.my_crop else "unlimited"
                if max_crops_str != "unlimited":
                    match_crop = re.search(r'[\d]+', max_crops_str)
                    if match_crop:
                        max_crops = int(match_crop.group())
                        current_crop_count = CropSplit.objects.filter(farm__farmer=farmer_prof).exclude(stage='Inactive').count()
                        if current_crop_count >= max_crops:
                            err_msg = f"Cannot add crop segment. Your package allows a maximum of {max_crops} active crops. Please inactivate another crop first."
                            if is_ajax: return JsonResponse({'success': False, 'error': err_msg})
                            messages.error(request, err_msg)
                            return redirect('farmer-farms')

                # Check Farm allocated area limit
                try:
                    split_acreage = float(acreage)
                except ValueError:
                    split_acreage = 0.0

                allocated = CropSplit.objects.filter(farm=farm).exclude(stage__in=['Harvested', 'Sold', 'Inactive']).aggregate(Sum('acreage'))['acreage__sum'] or 0.0
                unallocated = float(farm.total_area) - float(allocated)

                if split_acreage > (unallocated + 0.001):
                    err_msg = f"Acreage exceeds available land on this farm. Unallocated: {max(0.0, unallocated):.2f} Acres"
                    if is_ajax: return JsonResponse({'success': False, 'error': err_msg})
                    messages.error(request, err_msg)
                    return redirect('farmer-farms')

                adv_fields = parse_crop_split_fields(request)
                has_advanced = request.POST.get('has_advanced_details') in ['yes', 'on', 'true', True, '1', 1]
                method = (request.POST.get('cultivation_method') or adv_fields.get('cultivation_method') or '').strip()

                crop_split = CropSplit.objects.create(
                    farm=farm,
                    crop_id=crop_id,
                    crop_category=adv_fields.get('crop_category'),
                    crop_pattern=adv_fields.get('crop_pattern'),
                    crop_variety=adv_fields.get('crop_variety') if has_advanced else None,
                    harvest_type=adv_fields.get('harvest_type'),
                    total_flushes=adv_fields.get('total_flushes', 1),
                    acreage=split_acreage,
                    has_advanced_details=has_advanced,
                    crop_latitude=adv_fields.get('crop_latitude') if has_advanced else None,
                    crop_longitude=adv_fields.get('crop_longitude') if has_advanced else None,
                    row_count=adv_fields.get('row_count') if has_advanced else None,
                    plant_count=adv_fields.get('plant_count') if has_advanced else None,
                    growth_duration=int(duration) if str(duration).isdigit() else (adv_fields.get('duration_min') or 90),
                    duration_min=adv_fields.get('duration_min') if has_advanced else None,
                    duration_max=adv_fields.get('duration_max') if has_advanced else None,
                    cultivation_method=method if has_advanced else None,
                    supervisor_id=primary_supervisor_id,
                    start_date=adv_fields.get('start_date'),
                    expected_yield=adv_fields.get('expected_yield') if has_advanced else None,
                    expected_yield_unit=adv_fields.get('expected_yield_unit') if has_advanced else 'Ton',
                    crop_notes=adv_fields.get('crop_notes') if has_advanced else None,
                    seed_details=adv_fields.get('seed_details') if has_advanced else None,
                    plant_spacing=adv_fields.get('plant_spacing') if has_advanced else None,
                )

                if supervisor_ids:
                    crop_split.supervisors.set(supervisor_ids)

                active_mgr = get_active_manager_staff(request)
                op_name = active_mgr.name if active_mgr else (request.user.first_name or (crop_split.supervisor.name if crop_split.supervisor else get_actor_name(request)))

                from apps.farmer.models import FarmActivity
                notes_method = f" using {method} method" if method else ""
                FarmActivity.objects.create(
                    farm=farm,
                    activity_type='Seeds Sowing',
                    asset_name=crop_split.crop.name if crop_split.crop else 'Crop',
                    operator_name=op_name,
                    quantity_metric=f"{split_acreage} Acres",
                    notes=f"Initial crop split creation for {crop_split.crop.name if crop_split.crop else 'Crop'}{notes_method} by {op_name}. | CropSplit #{crop_split.id}"
                )

                # Check for auto task generation
                auto_generate = request.POST.get('auto_generate_tasks') in ['yes', 'on', 'true', True]
                if auto_generate and crop_split.crop:
                    from datetime import timedelta, date
                    from apps.farmer.models import FarmTask
                    from apps.superadmin.models import CropBlueprint

                    # We get the blueprint by the exact crop name
                    blueprint = CropBlueprint.objects.filter(crop_name__iexact=crop_split.crop.name, farmer__isnull=True).first()
                    if blueprint:
                        base_date = crop_split.start_date or date.today()
                        for stage in blueprint.stages.all():
                            task_date = base_date + timedelta(days=stage.day_offset)
                            FarmTask.objects.create(
                                farm=farm,
                                farmer=farmer_prof or (farm.farmer if farm else None),
                                associated_split=crop_split,
                                title=stage.milestone_title,
                                description=f"Activity: {stage.activity_type}",
                                priority=stage.priority_level,
                                status='Pending',
                                assigned_worker=crop_split.supervisor,
                                work_timeline=task_date.strftime('%Y-%m-%d')
                            )

                crop_name_str = crop_split.crop.name if crop_split.crop else 'Crop'
                log_action(request, "Crop Split Created", f"Successfully allocated {split_acreage} acres to '{crop_name_str}' in '{farm.land_name}'.", icon_class="ri-plant-line", bg_color="success")
                if is_ajax:
                    return JsonResponse({'success': True, 'message': 'Crop split created successfully!', 'split_id': crop_split.id})
                messages.success(request, "Crop split created successfully!")
            except Exception as e:
                if is_ajax: return JsonResponse({'success': False, 'error': str(e)})
                messages.error(request, f"Error creating crop split: {e!s}")
    return redirect('farmer-farms')

def edit_crop_split(request):
    from django.http import JsonResponse
    from django.contrib import messages
    from django.shortcuts import redirect
    from apps.farmer.models import CropSplit, Farm
    from apps.superadmin.models import Crop, CropType

    farmer_prof = get_farmer_profile(request.user)
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('is_ajax') == 'true'

    if request.method == "POST":
        split_id = request.POST.get('split_id')
        if split_id and farmer_prof:
            try:
                split = CropSplit.objects.get(id=split_id, farm__farmer=farmer_prof)
                farm = split.farm

                crop_id = request.POST.get('crop_type') or request.POST.get('crop_id')
                custom_crop_name = (request.POST.get('crop_name') or '').strip()
                if not crop_id and custom_crop_name:
                    default_ct = CropType.objects.first()
                    crop_obj, _ = Crop.objects.get_or_create(
                        name=custom_crop_name,
                        defaults={'crop_type': default_ct, 'is_active': True}
                    )
                    crop_id = crop_obj.id

                acreage_str = request.POST.get('acreage') or request.POST.get('crop_area')
                duration_str = request.POST.get('growth_duration') or request.POST.get('duration_min')
                method = request.POST.get('cultivation_method')
                supervisor_id = request.POST.get('supervisor')
                stage = request.POST.get('stage')

                if acreage_str:
                    try:
                        new_acreage = float(acreage_str)
                    except ValueError:
                        new_acreage = float(split.acreage)

                    from django.db.models import Sum
                    other_splits_area = CropSplit.objects.filter(farm=farm).exclude(id=split.id).exclude(stage__in=['Harvested', 'Sold', 'Inactive']).aggregate(Sum('acreage'))['acreage__sum'] or 0.0
                    available = float(farm.total_area) - float(other_splits_area)
                    if new_acreage > (available + 0.001):
                        err_msg = f"Acreage exceeds available land on this farm. Available: {max(0.0, available):.2f} Acres"
                        if is_ajax:
                            return JsonResponse({'success': False, 'error': err_msg})
                        messages.error(request, err_msg)
                        return redirect('farmer-farms')
                    split.acreage = new_acreage

                if crop_id:
                    split.crop_id = crop_id
                if duration_str:
                    try:
                        split.growth_duration = int(duration_str)
                    except ValueError:
                        pass
                if method:
                    split.cultivation_method = method
                raw_supervisors = request.POST.getlist('supervisors') or request.POST.getlist('supervisor')
                if raw_supervisors:
                    supervisor_ids = [int(s) for s in raw_supervisors if str(s).isdigit() and int(s) > 0]
                    split.supervisors.set(supervisor_ids)
                    split.supervisor_id = supervisor_ids[0] if supervisor_ids else None
                elif supervisor_id is not None:
                    split.supervisor_id = supervisor_id if (str(supervisor_id).isdigit() and int(supervisor_id) > 0) else None
                    if split.supervisor_id:
                        split.supervisors.set([split.supervisor_id])
                    else:
                        split.supervisors.clear()
                if stage and stage in ['Sown', 'Vegetative', 'Flowering', 'Yield', 'Harvested', 'Sold', 'Inactive']:
                    split.stage = stage

                adv_fields = parse_crop_split_fields(request)
                has_advanced = request.POST.get('has_advanced_details') in ['yes', 'on', 'true', True, '1', 1]
                split.has_advanced_details = has_advanced

                if 'crop_category' in request.POST:
                    split.crop_category = adv_fields.get('crop_category')
                if 'crop_pattern' in request.POST:
                    split.crop_pattern = adv_fields.get('crop_pattern')
                if 'harvest_type' in request.POST:
                    split.harvest_type = adv_fields.get('harvest_type')
                if 'total_flushes' in request.POST or adv_fields.get('total_flushes'):
                    split.total_flushes = adv_fields.get('total_flushes', split.total_flushes or 1)
                if adv_fields.get('start_date') is not None:
                    split.start_date = adv_fields.get('start_date')

                if has_advanced:
                    if 'crop_variety' in request.POST:
                        split.crop_variety = adv_fields.get('crop_variety')
                    if 'cultivation_method' in request.POST:
                        split.cultivation_method = (request.POST.get('cultivation_method') or '').strip() or None
                    if 'crop_latitude' in request.POST:
                        split.crop_latitude = adv_fields.get('crop_latitude')
                    if 'crop_longitude' in request.POST:
                        split.crop_longitude = adv_fields.get('crop_longitude')
                    if 'row_count' in request.POST:
                        split.row_count = adv_fields.get('row_count')
                    if 'plant_count' in request.POST:
                        split.plant_count = adv_fields.get('plant_count')
                    if 'duration_min' in request.POST:
                        split.duration_min = adv_fields.get('duration_min')
                    if 'duration_max' in request.POST:
                        split.duration_max = adv_fields.get('duration_max')
                    if 'expected_yield' in request.POST:
                        split.expected_yield = adv_fields.get('expected_yield')
                    if 'expected_yield_unit' in request.POST:
                        split.expected_yield_unit = adv_fields.get('expected_yield_unit')
                    if 'crop_notes' in request.POST:
                        split.crop_notes = adv_fields.get('crop_notes')
                    if 'seed_details' in request.POST:
                        split.seed_details = adv_fields.get('seed_details')
                    if 'plant_spacing' in request.POST:
                        split.plant_spacing = adv_fields.get('plant_spacing')
                else:
                    split.crop_latitude = None
                    split.crop_longitude = None
                    split.row_count = None
                    split.plant_count = None
                    split.duration_min = None
                    split.duration_max = None
                    split.crop_notes = None
                    split.seed_details = None
                    split.plant_spacing = None
                    split.cultivation_method = None
                    split.expected_yield = None

                split.save()
                crop_name_str = split.crop.name if split.crop else 'Crop'
                active_mgr = get_active_manager_staff(request)
                op_name = active_mgr.name if active_mgr else (request.user.first_name or (split.supervisor.name if split.supervisor else get_actor_name(request)))

                from apps.farmer.models import FarmActivity
                FarmActivity.objects.create(
                    farm=farm,
                    activity_type="Other Land Care",
                    asset_name=f"{crop_name_str} Details Updated",
                    operator_name=op_name,
                    quantity_metric=f"{split.acreage} Ac",
                    notes=f"Crop segment '{crop_name_str}' details updated by {op_name} | CropSplit #{split.id}."
                )

                log_action(request, "Crop Segment Updated", f"Crop segment '{crop_name_str}' in '{farm.land_name}' was updated by {op_name}.", icon_class="ri-edit-line", bg_color="info")
                if is_ajax:
                    return JsonResponse({'success': True, 'message': 'Crop segment updated successfully!'})
                messages.success(request, "Crop segment updated successfully!")
            except Exception as e:
                if is_ajax:
                    return JsonResponse({'success': False, 'error': str(e)})
                messages.error(request, f"Error updating crop: {e}")
    if is_ajax:
        return JsonResponse({'success': False, 'error': 'Invalid request'})
    return redirect('farmer-farms')

@transaction.atomic
def advance_crop_split(request):
    from django.http import JsonResponse
    from apps.farmer.models import CropSplit

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        split_id = request.POST.get('split_id')
        target_stage = request.POST.get('stage')
        next_flush = request.POST.get('next_flush')
        action = request.POST.get('action')
        mark_completed = request.POST.get('mark_completed') == 'true' or action == 'complete'
        reset_batches = request.POST.get('reset_batches') == 'true'

        if split_id and farmer_prof:
            try:
                split = CropSplit.objects.select_for_update().get(id=split_id, farm__farmer=farmer_prof)
                crop_name = split.crop.name if split.crop else 'Crop'

                if mark_completed:
                    split.stage = 'Harvested'
                    current_notes = str(split.crop_notes or '')
                    if '[ALL_FLUSHES_COMPLETED]' not in current_notes:
                        split.crop_notes = (current_notes + ' [ALL_FLUSHES_COMPLETED]').strip()
                    split.save()

                    log_action(request, "Crop Cycle Finalized", f"Crop '{crop_name}' all harvest flushes completed in '{split.farm.land_name}'.", icon_class="ri-check-double-line", bg_color="success")
                    return JsonResponse({
                        'success': True,
                        'stage': 'Harvested',
                        'new_stage': 'Harvested',
                        'is_completed': True,
                        'current_flush': split.current_flush,
                        'harvest_type': split.harvest_type
                    })

                # 1. Handle Active / Inactive switch toggle specifically
                if action in ['toggle_status', 'set_active', 'set_inactive'] or target_stage in ['Active', 'Inactive']:
                    is_activating = (action == 'set_active') or (target_stage == 'Active') or (action == 'toggle_status' and split.stage == 'Inactive')
                    if is_activating:
                        # Check subscription limit for active crops
                        active_sub = farmer_prof.subscriptions.filter(subscription_status='active').order_by('-id').first()
                        if active_sub and active_sub.package:
                            max_crops_str = str(active_sub.package.my_crop or '').lower().strip()
                            if max_crops_str and max_crops_str != "unlimited":
                                match_crop = re.search(r'\d+', max_crops_str)
                                if match_crop:
                                    max_crops = int(match_crop.group())
                                    current_active_crops = CropSplit.objects.filter(farm__farmer=farmer_prof).exclude(id=split.id).exclude(stage='Inactive').count()
                                    if current_active_crops >= max_crops:
                                        return JsonResponse({
                                            'success': False,
                                            'limit_reached': True,
                                            'max_crops': max_crops,
                                            'current_active': current_active_crops,
                                            'error': f"You have reached your limit of {max_crops} active crops. Please inactivate another crop to activate this one."
                                        })

                        # Restore previous stage if saved, else target_stage or default 'Sown'
                        notes = str(split.crop_notes or '')
                        m_prev = re.search(r'\[PREV_STAGE:([^\]]+)\]', notes)
                        if m_prev:
                            restored_stage = m_prev.group(1).strip()
                            split.crop_notes = re.sub(r'\[PREV_STAGE:[^\]]+\]', '', notes).strip()
                        elif target_stage and target_stage in ['Sown', 'Vegetative', 'Flowering', 'Yield', 'Harvested']:
                            restored_stage = target_stage
                        else:
                            restored_stage = 'Sown'

                        split.stage = restored_stage
                        split.save()

                        from apps.farmer.models import FarmActivity
                        active_mgr = get_active_manager_staff(request)
                        op_name = active_mgr.name if active_mgr else (request.user.first_name or (split.supervisor.name if split.supervisor else get_actor_name(request)))
                        FarmActivity.objects.create(
                            farm=split.farm,
                            activity_type="Other Land Care",
                            asset_name=f"{crop_name} Activated",
                            quantity_metric=f"{split.acreage} Ac",
                            operator_name=op_name,
                            notes=f"Crop cultivation reactivated in '{split.stage}' stage by {op_name} | CropSplit #{split.id}."
                        )
                        log_action(request, "Crop Activated", f"Crop '{crop_name}' status set to Active ({split.stage}) in '{split.farm.land_name}'.", icon_class="ri-check-circle-line", bg_color="success")
                        return JsonResponse({
                            'success': True,
                            'is_active': True,
                            'stage': split.stage,
                            'new_stage': split.stage,
                            'current_flush': split.current_flush,
                            'harvest_type': split.harvest_type,
                            'message': f"Crop activated in stage '{split.stage}'."
                        })
                    else:
                        # Deactivate crop -> Inactive
                        notes = str(split.crop_notes or '')
                        notes = re.sub(r'\[PREV_STAGE:[^\]]+\]', '', notes).strip()
                        if split.stage != 'Inactive':
                            notes = f"{notes} [PREV_STAGE:{split.stage}]".strip()
                        split.crop_notes = notes
                        split.stage = 'Inactive'
                        split.save()

                        from apps.farmer.models import FarmActivity
                        active_mgr = get_active_manager_staff(request)
                        op_name = active_mgr.name if active_mgr else (request.user.first_name or (split.supervisor.name if split.supervisor else get_actor_name(request)))
                        FarmActivity.objects.create(
                            farm=split.farm,
                            activity_type="Other Land Care",
                            asset_name=f"{crop_name} Inactivated",
                            quantity_metric=f"{split.acreage} Ac",
                            operator_name=op_name,
                            notes=f"Crop cultivation set to Inactive by {op_name} | CropSplit #{split.id}."
                        )
                        log_action(request, "Crop Inactivated", f"Crop '{crop_name}' status set to Inactive in '{split.farm.land_name}'.", icon_class="ri-pause-circle-line", bg_color="secondary")
                        return JsonResponse({
                            'success': True,
                            'is_active': False,
                            'stage': 'Inactive',
                            'new_stage': 'Inactive',
                            'current_flush': split.current_flush,
                            'harvest_type': split.harvest_type,
                            'message': "Crop marked as Inactive."
                        })

                valid_stages = ['Sown', 'Vegetative', 'Flowering', 'Yield', 'Harvested', 'Sold', 'Inactive']

                if target_stage and target_stage in valid_stages:
                    if target_stage == split.stage and not next_flush and not reset_batches:
                        return JsonResponse({
                            'success': True, 'stage': split.stage, 'new_stage': split.stage,
                            'current_flush': split.current_flush, 'harvest_type': split.harvest_type,
                            'is_completed': bool(split.crop_notes and '[ALL_FLUSHES_COMPLETED]' in split.crop_notes),
                        })
                    # Prevent Single Harvest crops from re-entering Yield or earlier stages once Harvested/Sold
                    is_multi = split.harvest_type in ['multiple', 'Multiple Harvest']
                    if not is_multi and split.stage in ['Harvested', 'Sold'] and target_stage != split.stage:
                        return JsonResponse({
                            'success': False,
                            'error': f"'{crop_name}' is a Single Harvest crop and has already been harvested. It cannot re-enter '{target_stage}'."
                        })

                    # Prevent Multiple Harvest crops from jumping stages once Harvested without next flush
                    if is_multi and split.stage in ['Harvested', 'Sold'] and target_stage != split.stage and not next_flush:
                        return JsonResponse({
                            'success': False,
                            'error': f"'{crop_name}' Flush #{split.current_flush} has already been harvested. Please use Next Flush Options to initiate the next cycle."
                        })

                    if split.crop_notes and '[ALL_FLUSHES_COMPLETED]' in split.crop_notes and target_stage != 'Harvested':
                        return JsonResponse({
                            'success': False,
                            'error': f"All harvest flushes for '{crop_name}' have been completed. Cultivation is finished."
                        })

                    # If currently Inactive and transitioning to an active stage, check crop limit
                    if split.stage == 'Inactive' and target_stage != 'Inactive':
                        active_sub = farmer_prof.subscriptions.filter(subscription_status='active').order_by('-id').first()
                        if active_sub and active_sub.package:
                            max_crops_str = str(active_sub.package.my_crop or '').lower().strip()
                            if max_crops_str and max_crops_str != "unlimited":
                                match_crop = re.search(r'\d+', max_crops_str)
                                if match_crop:
                                    max_crops = int(match_crop.group())
                                    current_active_crops = CropSplit.objects.filter(farm__farmer=farmer_prof).exclude(id=split.id).exclude(stage='Inactive').count()
                                    if current_active_crops >= max_crops:
                                        return JsonResponse({
                                            'success': False,
                                            'limit_reached': True,
                                            'max_crops': max_crops,
                                            'current_active': current_active_crops,
                                            'error': f"You have reached your limit of {max_crops} active crops. Please inactivate another crop to activate this one."
                                        })

                    if next_flush:
                        try:
                            split.current_flush = int(next_flush)
                        except (ValueError, TypeError):
                            split.current_flush = (split.current_flush or 1) + 1
                    elif target_stage == 'Sown' and split.stage == 'Harvested' and split.harvest_type in ['multiple', 'Multiple Harvest']:
                        split.current_flush = (split.current_flush or 1) + 1

                    split.stage = target_stage
                    if split.crop_notes and '[ALL_FLUSHES_COMPLETED]' in split.crop_notes:
                        split.crop_notes = split.crop_notes.replace('[ALL_FLUSHES_COMPLETED]', '').strip()
                    split.save()

                    # Log activity
                    from apps.farmer.models import FarmActivity
                    active_mgr = get_active_manager_staff(request)
                    op_name = active_mgr.name if active_mgr else (request.user.first_name or (split.supervisor.name if split.supervisor else get_actor_name(request)))
                    h_type = split.harvest_type or 'Single Harvest'
                    flush_str = f"Flush #{split.current_flush}" if (split.harvest_type in ['multiple', 'Multiple Harvest']) else ""
                    FarmActivity.objects.create(
                        farm=split.farm,
                        activity_type="Other Land Care",
                        asset_name=f"{crop_name} Phase Update",
                        quantity_metric=f"{split.acreage} Ac",
                        operator_name=op_name,
                        notes=f"Crop cultivation stage updated to '{split.stage}' ({h_type} {flush_str}) by {op_name} | CropSplit #{split.id}."
                    )

                    log_action(request, "Crop Stage Updated", f"Crop '{crop_name}' stage set to '{split.stage}' ({flush_str}) in '{split.farm.land_name}'.", icon_class="ri-refresh-line", bg_color="info")
                    return JsonResponse({
                        'success': True,
                        'stage': split.stage,
                        'new_stage': split.stage,
                        'is_completed': False,
                        'current_flush': split.current_flush,
                        'harvest_type': split.harvest_type
                    })

                stages = ['Sown', 'Vegetative', 'Flowering', 'Yield']
                if split.stage in stages:
                    current_idx = stages.index(split.stage)
                    if current_idx < len(stages) - 1:
                        split.stage = stages[current_idx + 1]
                        split.save()

                        from apps.farmer.models import FarmActivity
                        active_mgr = get_active_manager_staff(request)
                        op_name = active_mgr.name if active_mgr else (request.user.first_name or (split.supervisor.name if split.supervisor else get_actor_name(request)))
                        FarmActivity.objects.create(
                            farm=split.farm,
                            activity_type="Other Land Care",
                            asset_name=f"{split.crop.name if split.crop else 'Crop'} Phase Update",
                            quantity_metric=f"{split.acreage} Ac",
                            operator_name=op_name,
                            notes=f"Successfully advanced crop cultivation stage to '{split.stage}' by {op_name} | CropSplit #{split.id}."
                        )

                        log_action(request, "Crop Stage Advanced", f"Crop '{split.crop.name}' stage advanced to '{split.stage}' in '{split.farm.land_name}'.", icon_class="ri-refresh-line", bg_color="info")
                        return JsonResponse({'success': True, 'stage': split.stage, 'new_stage': split.stage, 'harvest_type': split.harvest_type})
                return JsonResponse({'success': False, 'error': 'Cannot advance further.'})
            except CropSplit.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Split not found.'})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def finalize_harvest_api(request):
    from django.http import JsonResponse
    from apps.farmer.models import CropSplit
    import json

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        split_id = request.POST.get('split_id')
        yield_tons = request.POST.get('yield_tons')
        price_per_mt = request.POST.get('price_per_mt')
        total_sale_value = request.POST.get('total_sale_value')
        harvest_mode = request.POST.get('harvest_mode', 'complete')  # 'next_flush' or 'complete'

        if split_id and farmer_prof:
            try:
                split = CropSplit.objects.get(id=split_id, farm__farmer=farmer_prof)
                curr_flush = split.current_flush or 1
                completed_flush = curr_flush
                split.stage = 'Harvested'

                if yield_tons: split.yield_tons = yield_tons
                if price_per_mt: split.price_per_mt = price_per_mt
                if total_sale_value: split.total_sale_value = total_sale_value

                batches_json = request.POST.get('batches_json')
                parsed_batches = []
                if batches_json:
                    try:
                        parsed_batches = json.loads(batches_json)
                    except Exception as e:
                        print("Batch parse error:", e)
                        parsed_batches = []

                if not isinstance(parsed_batches, list):
                    parsed_batches = []

                unit_str = request.POST.get('unit') or split.expected_yield_unit or 'Ton'
                if unit_str:
                    split.expected_yield_unit = unit_str
                if not parsed_batches and yield_tons and float(yield_tons) > 0:
                    parsed_batches = [{
                        "yield": float(yield_tons),
                        "price": float(price_per_mt) if price_per_mt else 0.0,
                        "saleValue": float(total_sale_value) if total_sale_value else 0.0,
                        "quality": "Grade 1",
                        "unit": unit_str,
                        "status": "Ready",
                        "flush_no": completed_flush
                    }]

                if parsed_batches:
                    existing_batches = list(split.harvest_batches or [])
                    
                    for b in parsed_batches:
                        if 'flush_no' not in b or not b['flush_no']:
                            b['flush_no'] = completed_flush
                        if 'status' not in b:
                            b['status'] = 'Ready'
                    
                    from apps.farmer.harvest_batches import normalize_batch_numbers
                    all_batches = normalize_batch_numbers(normalize_batch_numbers(existing_batches) + parsed_batches)
                    split.harvest_batches = all_batches

                    # Cumulative yield & sale value totals across all batches
                    total_cum_yield = sum([float(b.get('yield', 0)) for b in all_batches])
                    total_cum_val = sum([float(b.get('saleValue', 0)) for b in all_batches])
                    avg_p = (total_cum_val / total_cum_yield) if total_cum_yield > 0 else (float(price_per_mt) if price_per_mt else 0.0)

                    split.yield_tons = total_cum_yield
                    split.total_sale_value = total_cum_val
                    split.price_per_mt = avg_p

                split.save()

                from apps.farmer.models import FarmActivity, LedgerTransaction
                import datetime

                unit_str = request.POST.get('unit') or split.expected_yield_unit or 'MT'
                active_mgr = get_active_manager_staff(request)
                op_name = active_mgr.name if active_mgr else (request.user.first_name or (split.supervisor.name if split.supervisor else get_actor_name(request)))
                crop_title = split.crop.name if split.crop else 'Crop'
                flush_label = f"Flush #{completed_flush}" if (split.harvest_type in ['multiple', 'Multiple Harvest']) else "Harvest"
                FarmActivity.objects.create(
                    farm=split.farm,
                    activity_type="Other Land Care",
                    asset_name=f"{crop_title} ({flush_label})",
                    quantity_metric=f"{yield_tons} {unit_str}",
                    operator_name=op_name,
                    notes=f"{flush_label} finalized with yield of {yield_tons} {unit_str} at ₹{price_per_mt}/{unit_str} by {op_name} | CropSplit #{split.id}."
                )

                action_title = f"{flush_label} Finalized"
                log_action(request, action_title, f"Finalized {flush_label.lower()} for '{crop_title}' ({yield_tons} {unit_str}) in '{split.farm.land_name}'.", icon_class="ri-shopping-bag-line", bg_color="success")
                return JsonResponse({
                    'success': True,
                    'harvest_mode': harvest_mode,
                    'completed_flush': completed_flush,
                    'current_flush': split.current_flush,
                    'stage': split.stage,
                    'harvest_batches': split.harvest_batches,
                    'yield_tons': float(split.yield_tons) if split.yield_tons else 0.0,
                    'total_sale_value': float(split.total_sale_value) if split.total_sale_value else 0.0
                })
            except CropSplit.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Split not found.'})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
@transaction.atomic
def sell_crop_split(request):
    from django.http import JsonResponse
    from apps.farmer.models import CropSplit
    from decimal import Decimal
    import json
    import datetime

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        try:
            split_id = request.POST.get('split_id')
            if not split_id or not farmer_prof:
                return JsonResponse({'success': False, 'error': 'Parameters missing.'})

            split = CropSplit.objects.select_for_update().get(id=split_id, farm__farmer=farmer_prof)

            yield_val_str = request.POST.get('yield')
            price_val_str = request.POST.get('price')
            total_val_str = request.POST.get('total')
            customer_name = request.POST.get('customer') or 'Direct Customer'
            paid_val = request.POST.get('paid')
            payment_method = request.POST.get('payment_method') or 'Cash'
            payment_status = request.POST.get('payment_status') or ('Received' if paid_val and float(paid_val) > 0 else 'Pending')
            unit_val = request.POST.get('unit') or split.expected_yield_unit or 'MT'
            quality_str = request.POST.get('quality') or 'Grade 1'
            crop_name = split.crop.name if split.crop else 'Crop'
            batch_index_str = request.POST.get('batch_index')

            yield_val = float(yield_val_str) if yield_val_str else 0
            price_val = float(price_val_str) if price_val_str else 0
            total_f = float(total_val_str) if total_val_str else (yield_val * price_val)
            paid_f = float(paid_val) if paid_val is not None and paid_val != '' else total_f
            pending_f = max(0.0, round(total_f - paid_f, 2))
            if pending_f > 0:
                payment_status = 'Partial' if paid_f > 0 else 'Pending'
            else:
                payment_status = 'Received'

            import math
            if not all(math.isfinite(v) for v in (yield_val, price_val, total_f, paid_f)) or yield_val <= 0 or price_val <= 0 or total_f <= 0 or paid_f < 0 or paid_f > total_f:
                return JsonResponse({'success': False, 'error': 'Enter valid quantity, price and payment amounts.'})

            direct_name = (request.POST.get('direct_customer_name') or '').strip()
            direct_phone = (request.POST.get('direct_customer_phone') or '').strip()
            is_direct = customer_name == 'Direct Customer'
            if is_direct:
                if not direct_name:
                    return JsonResponse({'success': False, 'error': 'Enter the direct customer name.'})
                if not re.fullmatch(r'[0-9]{10,13}', direct_phone):
                    return JsonResponse({'success': False, 'error': 'Enter a mobile number containing 10 to 13 digits.'})
                customer_name = direct_name

            from apps.farmer.harvest_batches import normalize_batch_numbers
            batches = normalize_batch_numbers(split.harvest_batches)
            if batches:
                try:
                    requested_index = int(batch_index_str)
                    target = batches[requested_index]
                    if requested_index < 0 or target.get('status') in ('Sold', 'Loss / Discard'):
                        raise ValueError
                    available = float(target.get('yield', 0))
                except (ValueError, TypeError, IndexError):
                    return JsonResponse({'success': False, 'error': 'This batch is no longer available. Refresh and try again.'})
            else:
                available = float(split.yield_tons or 0)
            if yield_val > available:
                return JsonResponse({'success': False, 'error': f'Quantity sold cannot exceed the available batch quantity of {available:g} {unit_val}.'})

            # Update specific batch if batch_index provided
            if batch_index_str is not None and batch_index_str != '':
                try:
                    b_idx = int(batch_index_str)
                    if 0 <= b_idx < len(batches):
                        target_batch = batches[b_idx]
                        orig_yield = float(target_batch.get('yield', 0))

                        # If user sold less than the full batch quantity, split out the remaining quantity
                        if 0 < yield_val < orig_yield:
                            remaining_yield = round(orig_yield - yield_val, 2)
                            remaining_batch = dict(target_batch)
                            original_value = float(target_batch.get('saleValue') or 0)
                            
                            # Mark the sold batch with the sold quantity
                            target_batch['yield'] = yield_val
                            target_batch['status'] = 'Sold'
                            target_batch['sold_to'] = customer_name
                            if is_direct:
                                target_batch['sold_contact'] = direct_phone
                                target_batch['customer_type'] = 'Direct Customer'
                            target_batch['sold_at_price'] = price_val if price_val else target_batch.get('price', 0)
                            target_batch['sold_total'] = total_f
                            target_batch['paid'] = paid_f
                            target_batch['pending'] = pending_f
                            target_batch['payment_method'] = payment_method
                            target_batch['payment_status'] = payment_status
                            target_batch['sold_date'] = datetime.date.today().strftime('%d %b %Y')

                            base_price = float(target_batch.get('price', 0)) or price_val
                            remaining_batch.update({
                                'yield': remaining_yield,
                                'unit': target_batch.get('unit', unit_val),
                                'price': target_batch.get('price', price_val),
                                'priceUnit': target_batch.get('priceUnit', target_batch.get('unit', unit_val)),
                                'saleValue': round(original_value * remaining_yield / orig_yield, 2) if original_value else round(remaining_yield * base_price, 2),
                                'quality': target_batch.get('quality', quality_str),
                                'status': 'Ready',
                                'flush_no': target_batch.get('flush_no', 1)
                            })
                            if original_value:
                                target_batch['saleValue'] = round(original_value * yield_val / orig_yield, 2)
                            # Insert the remaining batch right after the sold batch
                            batches.insert(b_idx + 1, remaining_batch)
                        else:
                            # Full batch sold
                            target_batch['yield'] = orig_yield if orig_yield > 0 else yield_val
                            target_batch['status'] = 'Sold'
                            target_batch['sold_to'] = customer_name
                            if is_direct:
                                target_batch['sold_contact'] = direct_phone
                                target_batch['customer_type'] = 'Direct Customer'
                            target_batch['sold_at_price'] = price_val if price_val else target_batch.get('price', 0)
                            target_batch['sold_total'] = total_f
                            target_batch['paid'] = paid_f
                            target_batch['pending'] = pending_f
                            target_batch['payment_method'] = payment_method
                            target_batch['payment_status'] = payment_status
                            target_batch['sold_date'] = datetime.date.today().strftime('%d %b %Y')

                        split.harvest_batches = batches
                except (ValueError, IndexError):
                    raise ValueError('Unable to update this batch. Refresh and try again.')

            if batches:
                all_sold = all(b.get('status') == 'Sold' for b in batches)
                split.stage = 'Sold' if all_sold else 'Harvested'
                ready_yield = sum(float(b.get('yield', 0)) for b in batches if b.get('status') not in ['Sold', 'Loss / Discard'])
                split.yield_tons = max(Decimal('0'), Decimal(str(round(ready_yield, 2))))
            else:
                split.stage = 'Sold'
                split.yield_tons = Decimal('0')

            split.save()

            tx = None
            if total_f > 0:
                from datetime import date
                from apps.farmer.models import FarmerCustomer, FarmerOutstanding, LedgerTransaction

                customer_query = FarmerCustomer.objects.filter(farmer=farmer_prof, name=customer_name)
                if is_direct:
                    customer_query = customer_query.filter(contact_number=direct_phone)
                customer_instance = customer_query.first()
                if not customer_instance and customer_name:
                    customer_instance = FarmerCustomer.objects.create(
                        farmer=farmer_prof,
                        name=customer_name,
                        contact_number=direct_phone if is_direct else '',
                        status='Active'
                    )

                pending_note = f" [Paid: ₹{int(paid_f)}, Pending: ₹{int(pending_f)}]" if pending_f > 0 else ""

                tx = LedgerTransaction.objects.create(
                    farmer=farmer_prof,
                    farm=split.farm,
                    close_date=date.today(),
                    description=f"Sale: {crop_name} ({quality_str}) to {customer_name}{pending_note}",
                    payment_method=payment_method,
                    category="Sales",
                    category_color="#28a745" if pending_f <= 0 else ("#f59e0b" if paid_f > 0 else "#dc3545"),
                    tags=f"{crop_name} Sale ({quality_str}) - CropSplit #{split.id}",
                    status=payment_status,
                    amount=total_f,
                    quantity=yield_val if yield_val > 0 else None,
                    unit=unit_val
                )

                # Add to Receivables (Outstanding) if they haven't paid the full amount
                if pending_f > 0:
                    FarmerOutstanding.objects.create(
                        farmer=farmer_prof,
                        customer=customer_instance,
                        invoice_no=f"Sale #{tx.id}: {crop_name} - {customer_name}",
                        due_date=date.today(),
                        amount_total=total_f,
                        amount_paid=paid_f,
                        status='Partial' if paid_f > 0 else 'Unpaid'
                    )

            log_action(request, "Crop Segment Sold", f"Sold {yield_val} {unit_val} of '{crop_name}' ({quality_str}) to '{customer_name}' (Total: ₹{total_f}).", icon_class="ri-money-dollar-circle-line", bg_color="success")
            return JsonResponse({
                'success': True,
                'sale': {
                    'id': tx.id if tx else None,
                    'description': tx.description if tx else f"Sale: {crop_name} ({quality_str}) to {customer_name}",
                    'date_formatted': datetime.date.today().strftime('%d %b %Y'),
                    'created_at_iso': tx.created_at.isoformat() if tx and hasattr(tx, 'created_at') and tx.created_at else datetime.datetime.now().isoformat(),
                    'close_date': datetime.date.today().strftime('%Y-%m-%d'),
                    'status': payment_status,
                    'tags': tx.tags if tx else '',
                    'quantity': yield_val,
                    'unit': unit_val,
                    'amount': total_f,
                    'paid': paid_f,
                    'pending': pending_f,
                    'payment_method': payment_method,
                    'customer': customer_name
                },
                'batches': batches,
                'stage': split.stage
            })
        except Exception as e:
            transaction.set_rollback(True)
            import traceback
            traceback.print_exc()
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def delete_crop_split(request):
    import re
    from django.http import JsonResponse
    from apps.farmer.models import CropSplit, FarmActivity, LedgerTransaction, FarmTask, InventoryStockLog
    from django.db.models import Q

    if get_active_manager_staff(request):
        return JsonResponse({'success': False, 'error': 'Managers do not have permission to modify crops.'})

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        split_id = request.POST.get('split_id')
        action = request.POST.get('action')

        if split_id and farmer_prof:
            try:
                split = CropSplit.objects.get(id=split_id, farm__farmer=farmer_prof)
                crop_name = split.crop.name if split.crop else 'Crop'
                farm_name = split.farm.land_name
                split_tag = f"CropSplit #{split.id}"

                # 1. Direct Inactivation Request
                if action == 'set_inactive':
                    notes = str(split.crop_notes or '')
                    notes = re.sub(r'\[PREV_STAGE:[^\]]+\]', '', notes).strip()
                    if split.stage != 'Inactive':
                        notes = f"{notes} [PREV_STAGE:{split.stage}]".strip()
                    split.crop_notes = notes
                    split.stage = 'Inactive'
                    split.save()

                    active_mgr = get_active_manager_staff(request)
                    op_name = active_mgr.name if active_mgr else (request.user.first_name or (split.supervisor.name if split.supervisor else get_actor_name(request)))
                    FarmActivity.objects.create(
                        farm=split.farm,
                        activity_type="Other Land Care",
                        asset_name=f"{crop_name} Inactivated",
                        quantity_metric=f"{split.acreage} Ac",
                        operator_name=op_name,
                        notes=f"Crop cultivation marked Inactive by {op_name} | {split_tag}."
                    )
                    log_action(request, "Crop Inactivated", f"Crop '{crop_name}' in '{farm_name}' marked as Inactive.", icon_class="ri-pause-circle-line", bg_color="warning")
                    return JsonResponse({
                        'success': True,
                        'inactivated': True,
                        'message': f"Crop '{crop_name}' has been marked as Inactive."
                    })

                # 2. Check for child module dependencies before allowing hard delete
                # A) Tasks
                task_qs = FarmTask.objects.filter(associated_split=split)
                task_count = task_qs.count()

                # B) Resource Consumptions
                stock_logs_count = InventoryStockLog.objects.filter(crop_split=split).count()
                consumption_acts_count = FarmActivity.objects.filter(
                    farm=split.farm,
                    activity_type="Resource Consumption"
                ).filter(
                    Q(notes__icontains=split_tag) | Q(asset_name__icontains=split_tag)
                ).count()
                consumption_count = stock_logs_count + consumption_acts_count

                # C) Expenses / Ledger Transactions
                task_ids = list(task_qs.values_list('id', flat=True))
                expense_q = Q(tags__icontains=split_tag) | Q(description__icontains=split_tag)
                for tid in task_ids:
                    expense_q |= Q(tags__icontains=f"Task ID: {tid}")
                expense_count = LedgerTransaction.objects.filter(farmer=farmer_prof).filter(expense_q).count()

                dependencies = []
                if task_count > 0:
                    dependencies.append(f"{task_count} Task{'s' if task_count > 1 else ''}")
                if consumption_count > 0:
                    dependencies.append(f"{consumption_count} Consumption log{'s' if consumption_count > 1 else ''}")
                if expense_count > 0:
                    dependencies.append(f"{expense_count} Expense transaction{'s' if expense_count > 1 else ''}")

                # If ANY module record has been created, BLOCK hard deletion
                if dependencies:
                    dep_str = ", ".join(dependencies)
                    return JsonResponse({
                        'success': False,
                        'has_dependencies': True,
                        'dependencies': dependencies,
                        'split_id': split.id,
                        'crop_name': crop_name,
                        'error': f"Cannot delete '{crop_name}' because it has linked records ({dep_str}). You can mark it as Inactive instead."
                    })

                # 3. Clean up basic initial setup logs only if no active child dependencies exist
                FarmActivity.objects.filter(farm=split.farm, notes__icontains=split_tag).delete()

                # Delete the crop split segment
                split.delete()

                log_action(request, "Crop Segment Removed", f"Crop split '{crop_name}' was removed from '{farm_name}'.", icon_class="ri-delete-bin-line", bg_color="warning")
                return JsonResponse({'success': True})
            except CropSplit.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Split not found.'})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def sync_task_labor_expense(task):
    from apps.farmer.models import LedgerTransaction
    from datetime import date
    
    if task.status != 'Completed':
        LedgerTransaction.objects.filter(farmer=task.farmer, tags=f"Task ID: {task.id}").delete()
        return
        
    hours = task.actual_hours if task.actual_hours is not None else task.total_hours
    workers = task.assigned_workers.all()
    
    if not workers or not hours or hours <= 0:
        LedgerTransaction.objects.filter(farmer=task.farmer, tags=f"Task ID: {task.id}").delete()
        return

    total_cost = 0.0
    for worker in workers:
        if getattr(worker, 'salary_frequency', '') == 'Hourly' and worker.salary:
            rate = float(worker.salary)
        elif worker.base_daily_rate:
            rate = float(worker.base_daily_rate) / 8.0
        else:
            rate = 500.0 / 8.0
        
        total_cost += float(hours) * rate

    if total_cost <= 0:
        LedgerTransaction.objects.filter(farmer=task.farmer, tags=f"Task ID: {task.id}").delete()
        return

    target_farm = task.farm
    if not target_farm and task.associated_split:
        target_farm = task.associated_split.farm

    tx = LedgerTransaction.objects.filter(farmer=task.farmer, tags=f"Task ID: {task.id}").first()
    
    close_date = task.start_date if task.start_date else task.task_date
    if not close_date:
        close_date = date.today()
        
    description = f"Labor Expense: {task.title}"
    amount = -total_cost
    
    if tx:
        tx.farm = target_farm
        tx.close_date = close_date
        tx.description = description
        tx.amount = amount
        tx.save()
    else:
        LedgerTransaction.objects.create(
            farmer=task.farmer,
            farm=target_farm,
            close_date=close_date,
            description=description,
            category="Labor Cost",
            category_color="#e57373",
            status="Cost",
            amount=amount,
            tags=f"Task ID: {task.id}"
        )

def delete_task(request):
    from django.http import JsonResponse
    from apps.farmer.models import FarmTask, LedgerTransaction

    if get_active_manager_staff(request):
        return JsonResponse({"success": False, "error": "Managers do not have permission to delete tasks."})

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        task_id = request.POST.get('task_id')
        if task_id and farmer_prof:
            try:
                from django.db.models import Q
                task = FarmTask.objects.filter(
                    Q(farmer=farmer_prof) | Q(farm__farmer=farmer_prof) | Q(associated_split__farm__farmer=farmer_prof),
                    id=task_id
                ).first()
                if not task:
                    return JsonResponse({"success": False, "error": "Task not found."})
                task_title = task.title
                # Delete any associated labor transaction
                LedgerTransaction.objects.filter(farmer=farmer_prof, tags=f"Task ID: {task.id}").delete()
                task.delete()

                log_action(request, "Task Deleted", f"Task '{task_title}' was removed.", icon_class="ri-delete-bin-line", bg_color="danger")
                return JsonResponse({"success": True})
            except Exception as e:
                return JsonResponse({"success": False, "error": str(e)})
    return JsonResponse({"success": False, "error": "Invalid request method."})

@csrf_exempt
@transaction.atomic
def add_farm_activity(request):
    from django.http import JsonResponse
    from apps.farmer.models import Farm, FarmActivity, CropSplit, LedgerTransaction, FarmerInventory
    import datetime

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        farm_id = request.POST.get('farm_id')
        if farm_id and farmer_prof:
            try:
                farm = Farm.objects.get(id=farm_id, farmer=farmer_prof)
                act_type = request.POST.get('activity_type') or 'Resource Consumption'
                asset_name = request.POST.get('asset_name') or 'Inventory Resource'
                op_name = request.POST.get('operator_name')
                split_id = request.POST.get('crop_split_id') or request.POST.get('split_id')
                amount_str = request.POST.get('amount')
                amount = float(amount_str) if amount_str else 0.0
                qty_metric = request.POST.get('quantity_metric') or ''
                notes = request.POST.get('notes', '')
                inv_item_id = request.POST.get('inventory_item') or request.POST.get('inventory_id')

                crop_split = None
                crop_name = ""
                if split_id:
                    crop_split = CropSplit.objects.filter(id=split_id, farm=farm).first()
                    if crop_split:
                        is_crop_final = (
                            crop_split.is_completed or 
                            (crop_split.crop_notes and '[ALL_FLUSHES_COMPLETED]' in crop_split.crop_notes) or 
                            crop_split.stage in ('Sold', 'Harvested')
                        )
                        if is_crop_final:
                            c_name = crop_split.crop.name if crop_split.crop else "Crop"
                            return JsonResponse({'success': False, 'error': f"Cannot log consumption. Crop '{c_name}' is already finalized/harvested."})
                        if crop_split.crop:
                            crop_name = crop_split.crop.name
                        if f"CropSplit #{split_id}" not in notes:
                            notes = (notes + f" | CropSplit #{split_id} ({crop_name or 'Crop'})").strip(' |')
                if not crop_name and farm.crop:
                    crop_name = farm.crop.name

                from apps.farmer.inventory_consumption import consume_stock, is_equipment
                resource_type = request.POST.get('resource_type', '')
                vehicle_id = request.POST.get('vehicle_id')
                is_vehicle = resource_type in ('Vehicle', 'Vehicle & Fleet', 'Vehicle & Fuel')
                vehicle = None
                if is_vehicle:
                    vehicle = FarmerInventory.objects.filter(id=vehicle_id, farmer=farmer_prof).first() if vehicle_id else None
                    if not vehicle or not is_equipment(vehicle):
                        return JsonResponse({'success': False, 'error': 'Select a valid vehicle or machine.'})
                    if (vehicle.status or '').strip().lower() != 'available':
                        return JsonResponse({'success': False, 'error': f"{vehicle.item_name} is not available for use right now."})
                    if not inv_item_id:
                        return JsonResponse({'success': False, 'error': 'Select a fuel inventory item.'})
                if inv_item_id:
                    try:
                        item = consume_stock(farmer=farmer_prof, item_id=inv_item_id,
                            quantity=request.POST.get('quantity'), farm=farm, crop_split=crop_split,
                            fuel_only=is_vehicle, vehicle=vehicle)
                    except ValueError as exc:
                        return JsonResponse({'success': False, 'error': str(exc)})
                    notes = (notes + f" | Inv Ref: inv-{item.id}").strip(' |')
                    if vehicle:
                        notes += f" | Vehicle: {vehicle.item_name} | Fuel: {item.item_name}"

                if not op_name or not str(op_name).strip():
                    op_name = get_actor_name(request)

                activity = FarmActivity.objects.create(
                    farm=farm,
                    activity_type=act_type,
                    asset_name=asset_name,
                    quantity_metric=qty_metric,
                    operator_name=op_name,
                    notes=notes,
                    amount=amount
                )

                # Do NOT create expense for inventory items because inventory purchase was already recorded as expense
                if amount > 0 and not inv_item_id:
                    tag_str = f"Farm ID: {farm.id}"
                    if split_id:
                        tag_str += f" · CropSplit #{split_id}"
                    LedgerTransaction.objects.create(
                        farmer=farmer_prof,
                        farm=farm,
                        close_date=datetime.date.today(),
                        description=f"Resource Consumption: {asset_name} ({qty_metric})",
                        category="Resource Consumption",
                        category_color="#f59e0b",
                        status="Cost",
                        amount=-amount,
                        tags=tag_str
                    )

                log_action(request, "Farm Activity Logged", f"Activity '{act_type}' ({asset_name}) logged for farm '{farm.land_name}'.", icon_class="ri-history-line", bg_color="info")
                return JsonResponse({'success': True, 'activity_id': activity.id})
            except Farm.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Farm not found.'})
            except Exception as e:
                transaction.set_rollback(True)
                import traceback
                traceback.print_exc()
                return JsonResponse({'success': False, 'error': f"Failed to record consumption: {str(e)}"})
    return JsonResponse({'success': False, 'error': 'Invalid request method or unauthenticated.'})

@csrf_exempt
def delete_farm_activity(request):
    from django.http import JsonResponse
    from apps.farmer.models import FarmActivity

    if get_active_manager_staff(request):
        return JsonResponse({'success': False, 'error': 'Managers do not have permission to delete activity records.'})

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        activity_id = request.POST.get('activity_id')
        if activity_id and farmer_prof:
            try:
                activity = FarmActivity.objects.get(id=activity_id, farm__farmer=farmer_prof)
                act_name = activity.asset_name
                activity.delete()
                log_action(request, "Activity Log Deleted", f"Activity log '{act_name}' was deleted.", icon_class="ri-delete-bin-line", bg_color="danger")
                return JsonResponse({'success': True})
            except FarmActivity.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Activity not found.'})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})

@csrf_exempt
def add_farm_task(request):
    from django.http import JsonResponse
    from apps.farmer.models import CropSplit, Farm, FarmerStaff, FarmTask
    from apps.superadmin.models import TaskCategory, TaskPriority

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        farm_id = request.POST.get('farm_id')
        title = request.POST.get('title')
        description = request.POST.get('description')
        priority_id = request.POST.get('priority')
        task_category_id = request.POST.get('task_category')
        work_timeline = request.POST.get('work_timeline') or "Full Day"
        split_id = request.POST.get('split_id')
        worker_id = request.POST.get('worker_id')
        
        start_date = request.POST.get('start_date') or None
        end_date = request.POST.get('end_date') or None
        start_time = request.POST.get('start_time') or None
        end_time = request.POST.get('end_time') or None
        
        total_hours = request.POST.get('total_hours')
        actual_hours = request.POST.get('actual_hours')
        try:
            total_hours = float(total_hours) if total_hours else None
        except (ValueError, TypeError):
            total_hours = None
        try:
            actual_hours = float(actual_hours) if actual_hours else None
        except (ValueError, TypeError):
            actual_hours = None

        split = None
        if split_id:
            if str(split_id).isdigit():
                split = CropSplit.objects.filter(id=int(split_id)).first()
            if split:
                is_crop_final = (
                    split.is_completed or 
                    (split.crop_notes and '[ALL_FLUSHES_COMPLETED]' in split.crop_notes) or 
                    split.stage in ('Sold', 'Harvested')
                )
                if is_crop_final:
                    crop_name = split.crop.name if split.crop else "Crop"
                    return JsonResponse({'success': False, 'error': f"Cannot schedule task. Crop '{crop_name}' is already finalized/harvested."})

        farm = None
        if farm_id:
            if str(farm_id).isdigit():
                farm = Farm.objects.filter(id=int(farm_id)).first()
            elif farmer_prof:
                farm = Farm.objects.filter(land_name__iexact=str(farm_id).strip(), farmer=farmer_prof).first()
            else:
                farm = Farm.objects.filter(land_name__iexact=str(farm_id).strip()).first()

        # Fallback: Link farm from split if farm was not directly specified
        if not farm and split and split.farm:
            farm = split.farm

        # Fallback: Link farmer_prof from farm or split if not resolved from request.user
        if not farmer_prof:
            if farm and farm.farmer:
                farmer_prof = farm.farmer
            elif split and split.farm and split.farm.farmer:
                farmer_prof = split.farm.farmer

        if farmer_prof:
            try:
                from datetime import date
                task_date = start_date if start_date else date.today()

                # Fetch category and priority safely
                category = None
                if task_category_id:
                    if str(task_category_id).isdigit():
                        category = TaskCategory.objects.filter(id=int(task_category_id)).first()
                    else:
                        category = TaskCategory.objects.filter(name__iexact=str(task_category_id).strip()).first()

                priority_lvl = None
                if priority_id:
                    if str(priority_id).isdigit():
                        priority_lvl = TaskPriority.objects.filter(id=int(priority_id)).first()
                    else:
                        priority_lvl = TaskPriority.objects.filter(name__iexact=str(priority_id).strip()).first()

                priority_name = priority_lvl.name if priority_lvl else (str(priority_id).strip() if priority_id else "Medium Priority")

                # Process multiple workers
                raw_worker_ids = request.POST.getlist('worker_id') or request.POST.getlist('worker_id[]')
                if not raw_worker_ids and request.POST.get('worker_id'):
                    raw_worker_ids = [request.POST.get('worker_id')]

                worker_ids = []
                for wid in raw_worker_ids:
                    if isinstance(wid, str) and ',' in wid:
                        worker_ids.extend([x.strip() for x in wid.split(',') if x.strip()])
                    elif wid:
                        worker_ids.append(wid)

                valid_worker_ids = [int(w) for w in worker_ids if str(w).isdigit()]
                first_worker = None
                if valid_worker_ids:
                    first_worker = FarmerStaff.objects.filter(id=valid_worker_ids[0], farmer=farmer_prof).first()

                schedule_type = request.POST.get('schedule_type', 'single')
                recurring_dates_csv = request.POST.get('recurring_dates_csv', '').strip()
                recurring_dates = [x.strip() for x in recurring_dates_csv.split(',') if x.strip()] if recurring_dates_csv else []

                workers_to_assign = FarmerStaff.objects.filter(id__in=valid_worker_ids, farmer=farmer_prof) if valid_worker_ids else None

                if schedule_type == 'recurring' and recurring_dates:
                    daily_hours = None
                    if start_time and end_time:
                        try:
                            from datetime import datetime
                            t1 = datetime.strptime(str(start_time).strip()[:5], "%H:%M")
                            t2 = datetime.strptime(str(end_time).strip()[:5], "%H:%M")
                            diff = (t2 - t1).total_seconds() / 3600.0
                            if diff < 0:
                                diff += 24.0
                            daily_hours = round(diff, 2)
                        except Exception:
                            daily_hours = total_hours
                    else:
                        daily_hours = total_hours

                    created_tasks = []
                    for r_date in sorted(list(set(recurring_dates))):
                        t_item = FarmTask.objects.create(
                            farm=farm,
                            farmer=farmer_prof,
                            title=title,
                            description=description,
                            priority_level=priority_lvl,
                            task_category=category,
                            priority=priority_name,
                            work_timeline=work_timeline,
                            associated_split=split,
                            assigned_worker=first_worker,
                            task_date=r_date,
                            start_date=r_date,
                            end_date=r_date,
                            start_time=start_time,
                            end_time=end_time,
                            total_hours=daily_hours,
                            actual_hours=actual_hours
                        )
                        if workers_to_assign:
                            t_item.assigned_workers.set(workers_to_assign)
                        sync_task_labor_expense(t_item)
                        created_tasks.append(t_item)

                    farm_name = farm.land_name if farm else "General"
                    log_action(request, "Task Created", f"{len(created_tasks)} recurring tasks '{title}' scheduled under '{farm_name}'.", icon_class="ri-repeat-2-line", bg_color="success")
                    return JsonResponse({
                        'success': True,
                        'task_id': created_tasks[0].id if created_tasks else None,
                        'count': len(created_tasks),
                        'message': f"{len(created_tasks)} recurring tasks scheduled successfully across selected dates!"
                    })

                task = FarmTask.objects.create(
                    farm=farm,
                    farmer=farmer_prof,
                    title=title,
                    description=description,
                    priority_level=priority_lvl,
                    task_category=category,
                    priority=priority_name,
                    work_timeline=work_timeline,
                    associated_split=split,
                    assigned_worker=first_worker,
                    task_date=task_date,
                    start_date=start_date,
                    end_date=end_date,
                    start_time=start_time,
                    end_time=end_time,
                    total_hours=total_hours,
                    actual_hours=actual_hours
                )
                
                if workers_to_assign:
                    task.assigned_workers.set(workers_to_assign)

                # Sync labor cost in LedgerTransaction
                sync_task_labor_expense(task)

                farm_name = farm.land_name if farm else "General"
                log_action(request, "Task Created", f"Task '{title}' created under '{farm_name}'.", icon_class="ri-task-line", bg_color="success")
                return JsonResponse({'success': True, 'task_id': task.id, 'message': 'Task created successfully!'})
            except Exception as e:
                return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})

@csrf_exempt
def edit_farm_task(request):
    from django.http import JsonResponse
    from apps.farmer.models import CropSplit, FarmerStaff, FarmTask, Farm
    from apps.superadmin.models import TaskCategory, TaskPriority

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        task_id = request.POST.get('task_id')
        farm_id = request.POST.get('farm_id')
        title = request.POST.get('title')
        description = request.POST.get('description')
        priority_id = request.POST.get('priority')
        task_category_id = request.POST.get('task_category')
        work_timeline = request.POST.get('work_timeline') or "Full Day"
        split_id = request.POST.get('split_id')
        worker_id = request.POST.get('worker_id')
        status = request.POST.get('status')
        
        start_date = request.POST.get('start_date') or None
        end_date = request.POST.get('end_date') or None
        start_time = request.POST.get('start_time') or None
        end_time = request.POST.get('end_time') or None
        
        total_hours = request.POST.get('total_hours')
        actual_hours = request.POST.get('actual_hours')
        try:
            total_hours = float(total_hours) if total_hours else None
        except (ValueError, TypeError):
            total_hours = None
        try:
            actual_hours = float(actual_hours) if actual_hours else None
        except (ValueError, TypeError):
            actual_hours = None

        if task_id and farmer_prof:
            try:
                from datetime import date
                from django.db.models import Q
                task = FarmTask.objects.filter(
                    Q(farmer=farmer_prof) | Q(farm__farmer=farmer_prof) | Q(associated_split__farm__farmer=farmer_prof),
                    id=task_id
                ).first()
                if not task:
                    return JsonResponse({'success': False, 'error': 'Task not found.'})
                if task.status == 'Completed':
                    return JsonResponse({'success': False, 'error': 'Completed tasks cannot be edited.'})
                if not task.farmer and farmer_prof:
                    task.farmer = farmer_prof
                task.title = title
                task.description = description
                
                # Fetch new farm
                farm = None
                if farm_id:
                    if str(farm_id).isdigit():
                        farm = Farm.objects.filter(id=int(farm_id), farmer=farmer_prof).first()
                    else:
                        farm = Farm.objects.filter(land_name__iexact=str(farm_id).strip(), farmer=farmer_prof).first()
                task.farm = farm
                
                # Fetch new category and priority
                category = None
                if task_category_id:
                    if str(task_category_id).isdigit():
                        category = TaskCategory.objects.filter(id=int(task_category_id)).first()
                    else:
                        category = TaskCategory.objects.filter(name__iexact=str(task_category_id).strip()).first()

                priority_lvl = None
                if priority_id:
                    if str(priority_id).isdigit():
                        priority_lvl = TaskPriority.objects.filter(id=int(priority_id)).first()
                    else:
                        priority_lvl = TaskPriority.objects.filter(name__iexact=str(priority_id).strip()).first()
                
                task.task_category = category
                task.priority_level = priority_lvl
                if priority_lvl:
                    task.priority = priority_lvl.name
                elif priority_id:
                    task.priority = str(priority_id).strip()

                task.work_timeline = work_timeline
                split = None
                if split_id and str(split_id).isdigit():
                    split = CropSplit.objects.filter(id=int(split_id)).first()
                task.associated_split = split
                if status:
                    task.status = status
                
                task.start_date = start_date
                task.end_date = end_date
                task.start_time = start_time
                task.end_time = end_time
                task.total_hours = total_hours
                task.actual_hours = actual_hours

                if start_date:
                    task.task_date = start_date

                # Process multiple workers
                raw_worker_ids = request.POST.getlist('worker_id') or request.POST.getlist('worker_id[]')
                if not raw_worker_ids and request.POST.get('worker_id'):
                    raw_worker_ids = [request.POST.get('worker_id')]

                worker_ids = []
                for wid in raw_worker_ids:
                    if isinstance(wid, str) and ',' in wid:
                        worker_ids.extend([x.strip() for x in wid.split(',') if x.strip()])
                    elif wid:
                        worker_ids.append(wid)

                valid_worker_ids = [int(w) for w in worker_ids if str(w).isdigit()]
                first_worker = None
                if valid_worker_ids:
                    first_worker = FarmerStaff.objects.filter(id=valid_worker_ids[0], farmer=farmer_prof).first()

                task.assigned_worker = first_worker
                task.save()
                
                if valid_worker_ids:
                    workers_to_assign = FarmerStaff.objects.filter(id__in=valid_worker_ids, farmer=farmer_prof)
                    task.assigned_workers.set(workers_to_assign)
                else:
                    task.assigned_workers.clear()

                # Sync labor cost in LedgerTransaction
                try:
                    sync_task_labor_expense(task)
                except Exception:
                    pass

                log_action(request, "Task Updated", f"Task '{task.title}' was updated.", icon_class="ri-edit-line", bg_color="info")
                return JsonResponse({'success': True, 'message': 'Task updated successfully!'})
            except FarmTask.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Task not found.'})
            except Exception as e:
                return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})

@csrf_exempt
def update_farm_task_status(request):
    from django.http import JsonResponse
    from apps.farmer.models import FarmTask

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        task_id = request.POST.get('task_id')
        status = request.POST.get('status')
        if task_id and farmer_prof:
            try:
                from django.db.models import Q
                task = FarmTask.objects.filter(
                    Q(farmer=farmer_prof) | Q(farm__farmer=farmer_prof) | Q(associated_split__farm__farmer=farmer_prof),
                    id=task_id
                ).first()
                if not task:
                    return JsonResponse({'success': False, 'error': 'Task not found.'})
                if task.status == 'Completed':
                    return JsonResponse({'success': False, 'error': 'Completed tasks cannot be modified.'})
                if not task.farmer and farmer_prof:
                    task.farmer = farmer_prof
                task.status = status
                task.save()
                
                # Sync labor cost in LedgerTransaction
                sync_task_labor_expense(task)
                
                log_action(request, "Task Status Updated", f"Task '{task.title}' status changed to '{status}'.", icon_class="ri-checkbox-circle-line", bg_color="info")
                return JsonResponse({'success': True})
            except FarmTask.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Task not found.'})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})

def farmer_profile(request):
    import re
    from apps.farmer.models import Farm, FarmerStaff
    from apps.superadmin.models import Package, Subscription, Document, State, City, Taluk

    active_role = request.session.get('active_role', 'farmer')
    is_manager_mode = (active_role == 'manager')

    staff_obj = None
    employer_profile = None

    if is_manager_mode:
        staff_obj = get_active_manager_staff(request)

        if staff_obj:
            employer_profile = staff_obj.farmer
            profile = employer_profile
        else:
            profile = get_farmer_profile(request.user)
    else:
        profile = get_farmer_profile(request.user)
    
    # Get all active/past subscriptions from backend DB
    from apps.superadmin.subscription_utils import (
        billing_cycle_duration_label,
        billing_cycle_label,
        billing_cycle_unit_label,
        calculate_plan_switch_quote,
        get_active_paid_subscription,
        get_farmer_subscription_details,
        get_package_cycle_price,
        has_farmer_used_free_trial,
        is_package_cycle_active,
        is_payment_failed,
        is_payment_pending,
        is_payment_settled,
        normalize_billing_cycle,
    )

    today = timezone.now().date()
    sub_qs = (
        Subscription.objects.filter(farmer=profile)
        .select_related('package', 'scheduled_package')
        .order_by('-created_at', '-id')
        if profile
        else Subscription.objects.none()
    )
    subscriptions = list(sub_qs)
    for s in subscriptions:
        c = normalize_billing_cycle(s.billing_cycle)
        s.norm_cycle = c
        s.cycle_label = billing_cycle_label(c)
        s.duration_label = billing_cycle_duration_label(c)
        s.is_settled = is_payment_settled(s.payment_status)
        s.is_pending = is_payment_pending(s.payment_status)
        s.is_failed = is_payment_failed(s.payment_status)
        s.display_amount = s.amount_paid if s.amount_paid is not None else s.monthly_amount

    sub_details = get_farmer_subscription_details(profile)
    active_subscription = get_active_paid_subscription(profile, today=today)
    is_subscription_active = bool(active_subscription and active_subscription.package)
    latest_pending_sub = next((s for s in subscriptions if s.subscription_status == 'pending' and s.is_pending), None)

    if active_subscription:
        active_cycle = normalize_billing_cycle(active_subscription.billing_cycle)
        active_cycle_label = billing_cycle_label(active_cycle)
        active_cycle_unit = billing_cycle_unit_label(active_cycle)
        current_plan_price = float(
            active_subscription.monthly_amount
            if active_subscription.monthly_amount is not None
            else get_package_cycle_price(active_subscription.package, active_cycle)
        )
    else:
        active_cycle = sub_details.get('billing_cycle') or 'monthly'
        active_cycle_label = sub_details.get('billing_cycle_label') or 'Monthly'
        active_cycle_unit = billing_cycle_unit_label(active_cycle)
        current_plan_price = 0.0

    farmer_used_free_trial = has_farmer_used_free_trial(profile)

    # Get all packages and precompute backend quotes across Monthly, Quarterly, and Annual cycles
    packages = Package.objects.all().order_by('price')
    packages_list = list(packages)
    package_quotes = {}
    for pkg_item in packages_list:
        pkg_price = float(pkg_item.price or 0.0)
        is_trial_pkg = bool(pkg_price == 0.0 or 'trial' in (pkg_item.name or '').lower())
        pkg_item.is_trial = is_trial_pkg
        pkg_item.is_trial_claimed = bool(is_trial_pkg and farmer_used_free_trial)
        pkg_item.price_quarterly_effective = get_package_cycle_price(pkg_item, 'quarterly')
        pkg_item.price_yearly_effective = get_package_cycle_price(pkg_item, 'yearly')

        pkg_quotes_for_cycles = {}
        for cyc in ('monthly', 'quarterly', 'yearly'):
            q = calculate_plan_switch_quote(profile, pkg_item, target_cycle=cyc, is_renewal=False)
            pkg_quotes_for_cycles[cyc] = {
                **q,
                'unit_label': billing_cycle_unit_label(cyc),
                'duration_label': billing_cycle_duration_label(cyc),
                'is_cycle_active': is_package_cycle_active(pkg_item, cyc),
                'price': get_package_cycle_price(pkg_item, cyc),
            }
        package_quotes[str(pkg_item.id)] = pkg_quotes_for_cycles

        # Populate server-rendered flags for the default active cycle view
        default_q = pkg_quotes_for_cycles.get(active_cycle, pkg_quotes_for_cycles['monthly'])
        pkg_item.is_current = bool(default_q['is_current'])
        pkg_item.is_upgrade = bool(default_q['is_upgrade'])
        pkg_item.is_downgrade = bool(default_q['is_downgrade'])
        pkg_item.can_switch = bool(default_q['can_switch'])
        pkg_item.active_cycle_price = default_q['price']

    # Get all uploaded documents
    documents = Document.objects.filter(farmer=profile).order_by('-created_at', '-id') if profile else Document.objects.none()

    # States, Districts and Taluks for location selects
    states = State.objects.all().order_by('name')
    districts = City.objects.filter(state=profile.state).order_by('name') if profile and profile.state else (City.objects.filter(state=states.first()).order_by('name') if states.exists() else City.objects.none())
    talukas = Taluk.objects.filter(city=profile.district).order_by('name') if profile and profile.district else (Taluk.objects.filter(city=districts.first()).order_by('name') if districts.exists() else Taluk.objects.none())

    # Calculate live farm & crop data
    farms = Farm.objects.filter(farmer=profile) if profile else Farm.objects.none()
    total_acreage = 0.0
    for f in farms:
        match = re.search(r'[\d\.]+', str(f.total_area))
        if match:
            total_acreage += float(match.group())

    first_farm = farms.first()
    # Collect all crops configured across farmer lands/farms
    farmer_crops = []
    for f in farms.prefetch_related('crops', 'splits'):
        for c in f.crops.all():
            if c.name and c.name not in farmer_crops:
                farmer_crops.append(c.name)
        if f.crop and f.crop.name and f.crop.name not in farmer_crops:
            farmer_crops.append(f.crop.name)
        if f.crop_type and f.crop_type.name and f.crop_type.name not in farmer_crops:
            farmer_crops.append(f.crop_type.name)
        for s in f.splits.all():
            if s.crop and s.crop.name and s.crop.name not in farmer_crops:
                farmer_crops.append(s.crop.name)
            elif s.crop_variety and s.crop_variety not in farmer_crops:
                farmer_crops.append(s.crop_variety)

    main_crop = ", ".join(farmer_crops) if farmer_crops else "-"
    health_index = first_farm.health_indicator if first_farm and hasattr(first_farm, 'health_indicator') and first_farm.health_indicator else '92'

    # Operational limits & usage metrics for subscription progress card (Package user limit applies to Managers)
    from apps.farmer.models import FarmerStaff
    manager_count = FarmerStaff.objects.filter(farmer=profile, is_role_manager=True).count() if profile else 0
    staff_count = manager_count
    farms_count = farms.count()
    try:
        from apps.farmer.models import FarmCrop
        crop_count = FarmCrop.objects.filter(farm__farmer=profile).count() if profile else 0
    except Exception:
        crop_count = farms_count

    pkg = active_subscription.package if (active_subscription and active_subscription.package and active_subscription.subscription_status == 'active') else None
    has_active_plan = bool(pkg and is_payment_settled(active_subscription.payment_status))

    if pkg and has_active_plan:
        if str(pkg.my_users or '').strip().lower() == 'unlimited':
            max_users = "Unlimited"
            users_pct = 100
        else:
            m_users = re.search(r'\d+', str(pkg.my_users or ''))
            max_users = int(m_users.group()) if m_users else 10
            users_pct = max(0, min(100, int(((max_users - staff_count) / max_users) * 100))) if max_users > 0 else 100

        m_acres = re.search(r'[\d\.]+', str(pkg.max_acre_support or ''))
        if str(pkg.max_acre_support or '').strip().lower() == 'unlimited':
            max_acres_display = "Unlimited"
            acres_pct = 100
        elif m_acres:
            max_acres_val = float(m_acres.group())
            max_acres_display = f"{max_acres_val:g}"
            rem_acres = max(0.0, max_acres_val - total_acreage)
            acres_pct = max(0, min(100, int((rem_acres / max_acres_val) * 100))) if max_acres_val > 0 else 0
        else:
            max_acres_display = "Unlimited"
            acres_pct = 100

        if str(pkg.my_land or '').strip().lower() == 'unlimited':
            max_farms = "Unlimited"
            farms_pct = 100
        else:
            m_land = re.search(r'\d+', str(pkg.my_land or ''))
            max_farms = int(m_land.group()) if m_land else 5
            farms_pct = max(0, min(100, int(((max_farms - farms_count) / max_farms) * 100))) if max_farms > 0 else 100

        if str(pkg.my_crop or '').strip().lower() == 'unlimited':
            max_crops = "Unlimited"
            crops_pct = 100
        else:
            m_crop = re.search(r'\d+', str(pkg.my_crop or ''))
            max_crops = int(m_crop.group()) if m_crop else 15
            crops_pct = max(0, min(100, int(((max_crops - crop_count) / max_crops) * 100))) if max_crops > 0 else 100

        plan_limits = {
            'has_active_plan': True,
            'package_name': pkg.name,
            'users': {'used': staff_count, 'max': max_users, 'pct': users_pct},
            'acres': {'used': f"{total_acreage:g}", 'max': max_acres_display, 'pct': acres_pct},
            'farms': {'used': farms_count, 'max': max_farms, 'pct': farms_pct},
            'crops': {'used': crop_count, 'max': max_crops, 'pct': crops_pct},
        }
    else:
        first_pkg = packages.first() if packages.exists() else None
        preview_name = first_pkg.name if first_pkg else 'Eco-Starter Package'
        m_users = re.search(r'\d+', str(first_pkg.my_users if first_pkg else ''))
        max_users = int(m_users.group()) if m_users else 5

        m_acres = re.search(r'[\d\.]+', str(first_pkg.max_acre_support if first_pkg else ''))
        max_acres_display = first_pkg.max_acre_support if first_pkg else "15"

        m_land = re.search(r'\d+', str(first_pkg.my_land if first_pkg else ''))
        max_farms = int(m_land.group()) if m_land else 3

        m_crop = re.search(r'\d+', str(first_pkg.my_crop if first_pkg else ''))
        max_crops = int(m_crop.group()) if m_crop else 10

        users_pct = 0
        acres_pct = 0
        farms_pct = 0
        crops_pct = 0

        plan_limits = {
            'has_active_plan': False,
            'package_name': preview_name,
            'users': {'used': staff_count, 'max': max_users, 'pct': users_pct},
            'acres': {'used': f"{total_acreage:g}", 'max': max_acres_display, 'pct': acres_pct},
            'farms': {'used': farms_count, 'max': max_farms, 'pct': farms_pct},
            'crops': {'used': crop_count, 'max': max_crops, 'pct': crops_pct},
        }

    import json
    active_sub_quote = calculate_plan_switch_quote(profile, None, target_cycle=active_cycle)

    from apps.superadmin.models import SMTPSettings
    farmer_smtp = SMTPSettings.objects.filter(user=request.user).first()

    context = {
        'profile': profile,
        'employer_profile': employer_profile,
        'is_manager_mode': is_manager_mode,
        'staff_obj': staff_obj,
        'active_subscription': active_subscription,
        'latest_pending_sub': latest_pending_sub,
        'active_cycle': active_cycle,
        'active_cycle_label': active_cycle_label,
        'active_cycle_unit': active_cycle_unit,
        'active_sub_quote': active_sub_quote,
        'active_sub_quote_json': json.dumps(active_sub_quote),
        'package_quotes_json': json.dumps(package_quotes),
        'subscriptions': subscriptions,
        'packages': packages_list,
        'is_subscription_active': is_subscription_active,
        'current_plan_price': current_plan_price,
        'farmer_used_free_trial': farmer_used_free_trial,
        'documents': documents,
        'states': states,
        'districts': districts,
        'talukas': talukas,
        'total_acreage': f"{total_acreage:g}" if total_acreage > 0 else "0",
        'staff_count': staff_count,
        'farms_count': farms_count,
        'crop_count': crop_count,
        'main_crop': main_crop,
        'health_index': health_index,
        'plan_limits': plan_limits,
        'farmer_smtp': farmer_smtp,
    }
    return render(request, "farmer/farmer_profile.html", context)


def farmer_mail_settings(request):
    if not request.user.is_authenticated:
        return redirect('farmer-login')

    from apps.superadmin.models import SMTPSettings
    config = SMTPSettings.objects.filter(user=request.user).first()
    if not config:
        config = SMTPSettings.objects.create(
            user=request.user,
            is_default=False,
            email_host="smtp.gmail.com",
            email_port=587,
            email_use_tls=True,
            email_use_ssl=False,
            from_name=request.user.get_full_name() or request.user.username or "My Farm",
            is_active=False
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
        config.from_name = request.POST.get("from_name", "").strip() or (request.user.get_full_name() or "My Farm")
        config.is_active = request.POST.get("is_active") in ["on", "true", "1"]
        config.save()

        is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or request.POST.get("is_ajax") == "1"
        if is_ajax:
            return JsonResponse({"status": "success", "message": "Custom SMTP mail settings saved successfully!"})

        messages.success(request, "Custom SMTP mail settings saved successfully!")
        return redirect("/farmer/profile_view/?tab=mail_settings#mail_settings")

    return redirect("/farmer/profile_view/?tab=mail_settings#mail_settings")


def farmer_test_mail(request):
    if not request.user.is_authenticated:
        return JsonResponse({"success": False, "message": "Authentication required."}, status=401)

    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST method required."}, status=405)

    from apps.superadmin.models import SMTPSettings
    from apps.superadmin.email_utils import test_smtp_connection

    config = SMTPSettings.objects.filter(user=request.user).first()
    if not config or not config.email_host_user or not config.email_host_password:
        return JsonResponse({"success": False, "message": "Please enter and save your Email & App Password before testing."}, status=400)

    test_recipient = (request.POST.get("test_email") or "").strip() or (request.user.email if request.user.email else "")
    if not test_recipient:
        return JsonResponse({"success": False, "message": "Please specify a test recipient email."}, status=400)

    success, message = test_smtp_connection(config, test_recipient)
    return JsonResponse({"success": success, "message": message})

def warehouse_stock(request):
    return render(request, "farmer/consumption/warehouse_stock.html")
def vehicle(request):
    return render(request, "farmer/consumption/vehicle.html")
@transaction.atomic
def register(request):
    import datetime
    from apps.farmer.models import Farm, FarmActivity, FarmerInventory, LedgerTransaction
    profile = get_farmer_profile(request.user)

    if request.method == "POST":
        resource_type = request.POST.get('resource_type', 'General Utility')
        inventory_id = request.POST.get('inventory_item')
        custom_name = request.POST.get('custom_resource_name', '').strip()
        quantity_str = request.POST.get('quantity', '0')
        unit_price_str = request.POST.get('unit_price', '0')
        unit_metric = request.POST.get('unit_metric', 'Units')
        farm_id = request.POST.get('farm_id')

        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('is_ajax') == '1'

        try:
            quantity = float(quantity_str) if quantity_str else 0.0
            unit_price = float(unit_price_str) if unit_price_str else 0.0
            total_amount = round(quantity * unit_price, 2)

            farm = Farm.objects.filter(id=farm_id, farmer=profile).first() if farm_id else None

            if farm and (inventory_id or custom_name) and quantity > 0:
                asset_name = custom_name if custom_name else "Utility Resource"
                notes_info = f"Resource Meter: {resource_type}"

                if inventory_id and resource_type == 'Warehouse':
                    from apps.farmer.inventory_consumption import consume_stock
                    try:
                        item = consume_stock(farmer=profile, item_id=inventory_id, quantity=quantity, farm=farm)
                    except ValueError as exc:
                        if is_ajax:
                            return JsonResponse({'status': 'error', 'message': str(exc)}, status=400)
                        messages.error(request, str(exc))
                        return redirect(request.POST.get('next_url', 'farmer-farms'))
                    asset_name = item.item_name
                    notes_info = f"Resource Meter: {resource_type} | Inv Ref: inv-{item.id}"

                # Log Farm Activity
                FarmActivity.objects.create(
                    farm=farm,
                    activity_type="Resource Consumption",
                    asset_name=asset_name,
                    quantity_metric=f"{quantity} {unit_metric}",
                    operator_name=get_actor_name(request),
                    notes=notes_info,
                    amount=total_amount
                )

                # Log Farm Expense in Ledger if cost > 0 and NOT an inventory item
                if total_amount > 0 and not inventory_id:
                    cat_map = {'Seeds': 'Seeds', 'Fertilizers & Chemicals': 'Fertilizer', 'Vehicle & Fuel': 'Fuel'}
                    chosen_cat = cat_map.get(resource_type, 'General')
                    LedgerTransaction.objects.create(
                        farmer=profile,
                        farm=farm,
                        close_date=datetime.date.today(),
                        description=f"Resource Consumption: {asset_name} ({quantity} {unit_metric})",
                        payment_method="Farm Meter Outflow",
                        category=chosen_cat,
                        category_color="#0d6efd",
                        tags=f"Farm ID: {farm.id}",
                        status="Cost",
                        amount=-total_amount
                    )

                log_action(request, "Resource Consumption Logged", f"Logged {quantity} {unit_metric} of '{asset_name}' for farm '{farm.land_name}' (₹{total_amount}).", icon_class="ri-speed-up-line", bg_color="info")

                if is_ajax:
                    return JsonResponse({
                        'status': 'success',
                        'message': f"Logged {quantity} {unit_metric} of '{asset_name}' for farm '{farm.land_name}' (₹{total_amount}).",
                        'farm_id': farm.id
                    })
                messages.success(request, f"Logged {quantity} {unit_metric} of '{asset_name}' for farm '{farm.land_name}' (₹{total_amount}).")

        except Exception as e:
            transaction.set_rollback(True)
            print(f"[CONSUMPTION LOG ERROR] {e}")
            if is_ajax:
                return JsonResponse({'status': 'error', 'message': f"Error logging consumption: {str(e)}"}, status=500)
            messages.error(request, f"Error logging consumption: {str(e)}")

        next_url = request.POST.get('next_url', 'farmer-farms')
        return redirect(next_url)

    inventories = FarmerInventory.objects.filter(farmer=profile, quantity__gt=0).order_by('item_name')
    farms = Farm.objects.filter(farmer=profile).order_by('land_name')
    activities = FarmActivity.objects.filter(farm__farmer=profile, activity_type="Resource Consumption").order_by('-created_at')

    return render(request, "farmer/consumption/register.html", {
        "inventories": inventories,
        "farms": farms,
        "activities": activities
    })

def outstanding(request):
    import datetime

    from django.db.models import Sum

    from apps.farmer.models import FarmerOutstanding
    profile = get_farmer_profile(request.user)
    outstandings = FarmerOutstanding.objects.filter(farmer=profile).order_by('-created_at', '-id')
    today = datetime.date.today()

    # Auto-mark overdue records
    outstandings.filter(due_date__lt=today, status='Unpaid').update(status='Overdue')

    # Re-fetch after update
    outstandings = FarmerOutstanding.objects.filter(farmer=profile).order_by('-created_at', '-id')

    vendor_outstandings = outstandings.filter(invoice_no__startswith='Vendor:').exclude(status='Paid')
    customer_outstandings = outstandings.exclude(invoice_no__startswith='Vendor:').exclude(status='Paid')

    # Calculate for vendor (Liabilities)
    total_outstanding = vendor_outstandings.aggregate(Sum('amount_total'))['amount_total__sum'] or 0
    overdue_count = vendor_outstandings.filter(status='Overdue').count()
    vendor_count = vendor_outstandings.count()

    # Calculate for customer (Receivables)
    total_receivables = 0
    for inv in customer_outstandings:
        inv.balance_due = float(inv.amount_total) - float(inv.amount_paid)
        total_receivables += inv.balance_due

    from apps.farmer.models import FarmerVendor, LedgerTransaction
    vendors = FarmerVendor.objects.filter(farmer=profile).order_by('vendor_name')

    from django.db.models import Q
    outflow = LedgerTransaction.objects.filter(farmer=profile).filter(
        Q(status__in=['Paid', 'Cost'], tags='Outflow', amount__lt=0, _connector=Q.OR)
    ).order_by('-close_date', '-id')

    context = {
        'outstandings': outstandings,
        'vendor_outstandings': vendor_outstandings,
        'customer_outstandings': customer_outstandings,
        'total_outstanding': total_outstanding,
        'total_receivables': total_receivables,
        'vendor_count': vendor_count,
        'today': today,
        'overdue_count': overdue_count,
        'vendors': vendors,
        'outflow': outflow,
    }
    return render(request, "farmer/outstanding.html", context)

def financials_sales(request):
    import datetime
    from django.db.models import Sum, Q
    from django.db.models.functions import Abs

    from apps.farmer.models import CropSplit, FarmerCustomer, LedgerTransaction, Farm, FarmerVendor, FarmerOutstanding
    profile = get_farmer_profile(request.user)
    farms = Farm.objects.filter(farmer=profile).order_by('land_name')
    transactions = LedgerTransaction.objects.filter(farmer=profile).exclude(
        category='Harvest Trade',
        description__istartswith='Harvest Trade Settlement'
    ).order_by('-close_date', '-created_at', '-id')

    inflow = transactions.filter(
        Q(tags__icontains='Inflow') | Q(amount__gt=0) | Q(status='Received')
    ).exclude(
        category='Customer Payment'
    ).distinct()

    outflow = transactions.filter(Q(tags__icontains='Outflow') | Q(amount__lt=0) | Q(status__in=['Paid', 'Cost'])).distinct()

    total_inflow = inflow.aggregate(total=Sum(Abs('amount')))['total'] or 0
    total_outflow = outflow.aggregate(total=Sum(Abs('amount')))['total'] or 0
    net_profit = total_inflow - total_outflow
    margin = (net_profit / total_inflow * 100) if total_inflow > 0 else 0

    today = datetime.date.today()
    this_month_start = today.replace(day=1)
    this_month_inflow = inflow.filter(close_date__gte=this_month_start).aggregate(total=Sum(Abs('amount')))['total'] or 0
    this_month_inflow_count = inflow.filter(close_date__gte=this_month_start).count()

    # Get farmer's crops from their harvested farm splits and calculate available tons and latest price
    crops_distinct = CropSplit.objects.filter(farm__farmer=profile, stage='Harvested').select_related('crop').values('crop__id', 'crop__name').distinct()
    farmer_crops = []
    for c in crops_distinct:
        splits_qs = CropSplit.objects.filter(farm__farmer=profile, crop__id=c['crop__id'], stage='Harvested')
        available = splits_qs.aggregate(Sum('yield_tons'))['yield_tons__sum'] or 0
        latest_split = splits_qs.order_by('-id').first()
        price = latest_split.price_per_mt if (latest_split and latest_split.price_per_mt) else 0

        farmer_crops.append({
            'crop__id': c['crop__id'],
            'crop__name': c['crop__name'],
            'available': available,
            'price': float(price)
        })

    # Get farmer's customers for sales entries
    customers = FarmerCustomer.objects.filter(farmer=profile).order_by('name')
    vendors = FarmerVendor.objects.filter(farmer=profile).order_by('vendor_name')

    is_direct_farmer = (request.session.get('active_role') == 'farmer') or (not request.session.get('active_role') and get_farmer_profile(request) is not None and get_active_manager_staff(request) is None)

    import json
    import re
    for t in inflow:
        linked_exp = transactions.filter(tags__icontains=f"Linked Sale #{t.id}")
        t.linked_expenses_list = list(linked_exp.values('category', 'description', 'amount', 'vendor__vendor_name'))
        t.total_sale_expenses = sum(abs(float(e['amount'])) for e in t.linked_expenses_list)
        t.realized_inflow = float(t.amount) - t.total_sale_expenses
        t.linked_expenses_json = json.dumps([{
            'category': e['category'],
            'description': e['description'],
            'amount': abs(float(e['amount'])),
            'vendor_name': e['vendor__vendor_name'] or 'Direct'
        } for e in t.linked_expenses_list])

        # Check if there is an active outstanding record for this sale
        out_rec = FarmerOutstanding.objects.filter(
            farmer=profile
        ).filter(
            Q(invoice_no__startswith=f"Sale #{t.id}:") | Q(invoice_no__icontains=f"Sale #{t.id}")
        ).first()

        # If not found directly, check description for [Paid: ..., Pending: ...]
        desc_str = t.description or ''
        p_match = re.search(r'\[Paid:\s*₹?([\d\.,]+),\s*Pending:\s*₹?([\d\.,]+)\]', desc_str)

        if not out_rec and p_match:
            p_val = float(p_match.group(1).replace(',', ''))
            b_val = float(p_match.group(2).replace(',', ''))
            # Try to find unlinked outstanding record with matching amounts
            out_rec = FarmerOutstanding.objects.filter(
                farmer=profile,
                amount_total=t.amount,
                amount_paid=p_val
            ).first()
            if out_rec and f"Sale #{t.id}" not in out_rec.invoice_no:
                out_rec.invoice_no = f"Sale #{t.id}: " + out_rec.invoice_no
                out_rec.save()

        if out_rec:
            t.outstanding_id = out_rec.id
            t.paid_amount = float(out_rec.amount_paid)
            t.balance_amount = max(0.0, float(out_rec.amount_total) - float(out_rec.amount_paid))
            if t.balance_amount <= 0.001:
                t.status = 'Received'
            else:
                t.status = 'Partial' if t.paid_amount > 0 else 'Pending'
        elif p_match:
            t.outstanding_id = None
            t.paid_amount = float(p_match.group(1).replace(',', ''))
            t.balance_amount = float(p_match.group(2).replace(',', ''))
            if t.balance_amount <= 0.001:
                t.status = 'Received'
            else:
                t.status = 'Partial' if t.paid_amount > 0 else 'Pending'
        else:
            t.outstanding_id = None
            t.paid_amount = float(t.amount)
            t.balance_amount = 0.0
            if t.tags:
                paid_match = re.search(r'Paid:\s*₹?([\d\.]+)', t.tags)
                bal_match = re.search(r'Balance:\s*₹?([\d\.]+)', t.tags)
                if paid_match:
                    t.paid_amount = float(paid_match.group(1))
                if bal_match:
                    t.balance_amount = float(bal_match.group(1))
            if t.status in ['Pending', 'Unpaid'] and t.balance_amount <= 0.001:
                t.balance_amount = float(t.amount)
                t.paid_amount = 0.0

        # Extract customer name and produce details cleanly from description
        # e.g. "Sale: Carrot (Grade 1) to Direct Customer [Paid: ₹180, Pending: ₹20]"
        c_name_match = re.search(r'\bto\s+([^\[\]]+?)(?:\s*\[|$)', desc_str)
        if c_name_match:
            t.customer_name = c_name_match.group(1).strip()
        else:
            t.customer_name = 'Direct Customer' if 'Direct Customer' in desc_str else 'Customer'

        crop_match = re.search(r'Sale:\s*(.*?)\s+\bto\b\s+', desc_str)
        if crop_match:
            t.crop_name_display = crop_match.group(1).strip()
        elif t.category and t.category != 'Sales':
            t.crop_name_display = t.category
        else:
            t.crop_name_display = desc_str.split('[')[0].strip()

        # Format INR currency with Indian commas
        def fmt_inr_curr(v):
            try:
                v_num = float(v or 0)
                int_part = int(abs(v_num))
                dec_part = f"{abs(v_num):.2f}".split('.')[1]
                s = str(int_part)
                if len(s) > 3:
                    last3 = s[-3:]
                    rest = s[:-3]
                    groups = []
                    while len(rest) > 2:
                        groups.insert(0, rest[-2:])
                        rest = rest[:-2]
                    if rest:
                        groups.insert(0, rest)
                    formatted = ','.join(groups) + ',' + last3
                else:
                    formatted = s
                return f"{formatted}.{dec_part}"
            except Exception:
                return f"{v:.2f}"

        t.amount_inr = fmt_inr_curr(t.amount)
        t.paid_inr = fmt_inr_curr(t.paid_amount)
        t.balance_inr = fmt_inr_curr(t.balance_amount)

    context = {
        'transactions': transactions,
        'inflow': inflow,
        'outflow': outflow,
        'total_inflow': total_inflow,
        'total_outflow': total_outflow,
        'net_profit': net_profit,
        'margin': margin,
        'this_month_inflow': this_month_inflow,
        'this_month_inflow_count': this_month_inflow_count,
        'farmer_crops': farmer_crops,
        'customers': customers,
        'vendors': vendors,
        'farms': farms,
        'is_direct_farmer': is_direct_farmer,
        'today_str': datetime.date.today().strftime('%Y-%m-%d'),
    }
    return render(request, "farmer/financials_sales.html", context)

def deduct_crop_inventory(profile, crop_name, quantity_tons, farm_instance=None, buyer_name=None):
    """
    Deducts sold quantity in tons from the farmer's harvested CropSplit inventory.
    Supports grade-wise deductions when crop_name contains a grade (e.g. "Tomato (Grade 1)" or "Tomato - Grade 1").
    """
    try:
        from decimal import Decimal
        import datetime
        from django.db.models import Q
        from apps.farmer.models import CropSplit
        from apps.farmer.harvest_batches import normalize_batch_numbers
        
        if not quantity_tons or float(quantity_tons) <= 0:
            return
            
        remaining = Decimal(str(quantity_tons))
        crop_str = (crop_name or '').strip()

        # Parse crop_name and target grade
        clean_name = crop_str
        target_grade = None

        if ' (' in crop_str:
            parts = crop_str.split(' (', 1)
            clean_name = parts[0].strip()
            g_part = parts[1].rstrip(')').strip()
            if g_part.lower() not in ['all grades', 'all', 'produce', 'blended']:
                target_grade = g_part
        elif ' - Grade' in crop_str or ' - grade' in crop_str.lower():
            parts = crop_str.split(' - ', 1)
            clean_name = parts[0].strip()
            target_grade = parts[1].strip()
        
        splits_query = CropSplit.objects.filter(
            farm__farmer=profile
        ).exclude(stage__in=['Completed', 'Inactive'])

        if clean_name:
            splits_query = splits_query.filter(
                Q(crop__name__iexact=clean_name) | Q(crop__name__icontains=clean_name) |
                Q(crop_category__icontains=clean_name) | Q(crop_variety__icontains=clean_name)
            )
        if farm_instance:
            farm_splits = splits_query.filter(farm=farm_instance).order_by('-id')
            if farm_splits.exists():
                splits_query = farm_splits
            else:
                splits_query = splits_query.order_by('-id')
        else:
            splits_query = splits_query.order_by('-id')

        all_splits = list(splits_query)
        # Prioritize splits with available stock
        all_splits.sort(key=lambda s: 0 if ((s.yield_tons and s.yield_tons > 0) or any(b.get('status') not in ['Sold', 'Loss / Discard'] for b in (s.harvest_batches or []))) else 1)

        for s in all_splits:
            if remaining <= Decimal('0'):
                break

            batches = list(s.harvest_batches or [])
            if batches:
                def batch_is_ready(b):
                    return b.get('status') not in ['Sold', 'Loss / Discard'] and float(b.get('yield', 0)) > 0.0001

                matching_batches = []
                if target_grade:
                    matching_batches = [b for b in batches if batch_is_ready(b) and (b.get('quality') or '').strip().lower() == target_grade.lower()]

                if not matching_batches:
                    matching_batches = [b for b in batches if batch_is_ready(b)]

                for b in matching_batches:
                    if remaining <= Decimal('0'):
                        break
                    b_yield = float(b.get('yield', 0))
                    if b_yield <= 0.0001:
                        continue

                    rem_float = float(remaining)
                    if rem_float >= b_yield - 0.0001:
                        # Full batch sold
                        b['status'] = 'Sold'
                        if buyer_name:
                            b['sold_to'] = buyer_name
                        b['sold_date'] = datetime.date.today().strftime('%d %b %Y')
                        remaining -= Decimal(str(round(b_yield, 2)))
                    else:
                        # Partial batch sold: split the batch
                        sold_qty = round(rem_float, 2)
                        rem_qty = round(b_yield - sold_qty, 2)
                        orig_val = float(b.get('saleValue') or 0)
                        unit_p = float(b.get('price') or 0)

                        b['yield'] = sold_qty
                        b['status'] = 'Sold'
                        if buyer_name:
                            b['sold_to'] = buyer_name
                        b['sold_date'] = datetime.date.today().strftime('%d %b %Y')
                        if orig_val > 0:
                            b['saleValue'] = round(orig_val * sold_qty / b_yield, 2)
                        else:
                            b['saleValue'] = round(sold_qty * unit_p, 2)

                        # Remaining ready portion
                        rem_b = dict(b)
                        rem_b['yield'] = rem_qty
                        rem_b['status'] = 'Ready'
                        rem_b.pop('sold_to', None)
                        rem_b.pop('sold_date', None)
                        if orig_val > 0:
                            rem_b['saleValue'] = round(orig_val * rem_qty / b_yield, 2)
                        else:
                            rem_b['saleValue'] = round(rem_qty * unit_p, 2)

                        b_idx = batches.index(b)
                        batches.insert(b_idx + 1, rem_b)
                        remaining = Decimal('0')
                        break

                s.harvest_batches = normalize_batch_numbers(batches)
                ready_list = [b for b in s.harvest_batches if b.get('status') not in ['Sold', 'Loss / Discard']]
                rem_y = sum(float(b.get('yield', 0)) for b in ready_list)
                rem_v = sum(float(b.get('saleValue') or (float(b.get('yield', 0)) * float(b.get('price', 0)))) for b in ready_list)
                s.yield_tons = Decimal(str(round(rem_y, 2)))
                s.total_sale_value = Decimal(str(round(rem_v, 2)))
                if rem_y > 0:
                    s.price_per_mt = Decimal(str(round(rem_v / rem_y, 2)))
                if rem_y <= 0.001 and all(b.get('status') == 'Sold' for b in s.harvest_batches):
                    s.stage = 'Sold'
                else:
                    s.stage = 'Harvested'
                s.save()
            else:
                available = s.yield_tons or Decimal('0')
                if available <= remaining:
                    remaining -= available
                    s.yield_tons = Decimal('0')
                    s.stage = 'Sold'
                    if s.price_per_mt:
                        s.total_sale_value = Decimal('0')
                    s.save()
                else:
                    s.yield_tons = available - remaining
                    if s.price_per_mt:
                        s.total_sale_value = s.yield_tons * s.price_per_mt
                    s.save()
                    remaining = Decimal('0')
    except Exception as e:
        import logging
        logging.error(f"Error deducting crop inventory: {e}")


def restore_crop_inventory(profile, crop_name, quantity_tons, farm_instance=None):
    """
    Restores sold quantity in tons back to the farmer's harvested CropSplit inventory.
    Supports grade-wise restoration when crop_name contains a grade (e.g. "Tomato (Grade 1)").
    """
    try:
        from decimal import Decimal
        from django.db.models import Q
        from apps.farmer.models import CropSplit
        from apps.farmer.harvest_batches import normalize_batch_numbers
        
        if not quantity_tons or float(quantity_tons) <= 0:
            return
            
        remaining = Decimal(str(quantity_tons))
        crop_str = (crop_name or '').strip()

        clean_name = crop_str
        target_grade = None

        if ' (' in crop_str:
            parts = crop_str.split(' (', 1)
            clean_name = parts[0].strip()
            g_part = parts[1].rstrip(')').strip()
            if g_part.lower() not in ['all grades', 'all', 'produce', 'blended']:
                target_grade = g_part
        elif ' - Grade' in crop_str or ' - grade' in crop_str.lower():
            parts = crop_str.split(' - ', 1)
            clean_name = parts[0].strip()
            target_grade = parts[1].strip()

        splits_query = CropSplit.objects.filter(farm__farmer=profile)
        if clean_name:
            splits_query = splits_query.filter(
                Q(crop__name__iexact=clean_name) | Q(crop__name__icontains=clean_name) |
                Q(crop_category__icontains=clean_name) | Q(crop_variety__icontains=clean_name)
            )
        if farm_instance:
            farm_splits = splits_query.filter(farm=farm_instance).order_by('-id')
            if farm_splits.exists():
                splits_query = farm_splits
            else:
                splits_query = splits_query.order_by('-id')
        else:
            splits_query = splits_query.order_by('-id')

        all_splits = list(splits_query)
        for s in all_splits:
            if remaining <= Decimal('0'):
                break

            batches = list(s.harvest_batches or [])
            if batches:
                sold_batches = [b for b in batches if b.get('status') == 'Sold']
                matching_sold = []
                if target_grade:
                    matching_sold = [b for b in sold_batches if (b.get('quality') or '').strip().lower() == target_grade.lower()]

                if not matching_sold:
                    matching_sold = sold_batches

                # Process sold batches in reverse order (LIFO)
                for b in reversed(matching_sold):
                    if remaining <= Decimal('0'):
                        break
                    sold_y = float(b.get('yield', 0))
                    if sold_y <= 0:
                        continue

                    rem_float = float(remaining)
                    if rem_float >= sold_y - 0.0001:
                        b['status'] = 'Ready'
                        b.pop('sold_to', None)
                        b.pop('sold_date', None)
                        remaining -= Decimal(str(round(sold_y, 2)))
                    else:
                        restored_qty = round(rem_float, 2)
                        rem_sold_qty = round(sold_y - restored_qty, 2)
                        orig_val = float(b.get('saleValue') or 0)
                        unit_p = float(b.get('price') or 0)

                        b['yield'] = rem_sold_qty
                        if orig_val > 0:
                            b['saleValue'] = round(orig_val * rem_sold_qty / sold_y, 2)
                        else:
                            b['saleValue'] = round(rem_sold_qty * unit_p, 2)

                        ready_b = dict(b)
                        ready_b['yield'] = restored_qty
                        ready_b['status'] = 'Ready'
                        ready_b.pop('sold_to', None)
                        ready_b.pop('sold_date', None)
                        if orig_val > 0:
                            ready_b['saleValue'] = round(orig_val * restored_qty / sold_y, 2)
                        else:
                            ready_b['saleValue'] = round(restored_qty * unit_p, 2)

                        b_idx = batches.index(b)
                        batches.insert(b_idx + 1, ready_b)
                        remaining = Decimal('0')
                        break

                s.harvest_batches = normalize_batch_numbers(batches)
                ready_list = [b for b in s.harvest_batches if b.get('status') not in ['Sold', 'Loss / Discard']]
                rem_y = sum(float(b.get('yield', 0)) for b in ready_list)
                rem_v = sum(float(b.get('saleValue') or (float(b.get('yield', 0)) * float(b.get('price', 0)))) for b in ready_list)
                s.yield_tons = Decimal(str(round(rem_y, 2)))
                s.total_sale_value = Decimal(str(round(rem_v, 2)))
                if rem_y > 0:
                    s.price_per_mt = Decimal(str(round(rem_v / rem_y, 2)))
                s.stage = 'Harvested'
                s.save()
            else:
                s.stage = 'Harvested'
                s.yield_tons = (s.yield_tons or Decimal('0')) + remaining
                if s.price_per_mt:
                    s.total_sale_value = s.yield_tons * s.price_per_mt
                s.save()
                remaining = Decimal('0')
    except Exception as e:
        import logging
        logging.error(f"Error restoring crop inventory: {e}")


def add_sale_page(request):
    import datetime
    from django.shortcuts import render, redirect, get_object_or_404
    from django.contrib import messages
    from django.db.models import Sum, Avg, Q
    from apps.farmer.models import CropSplit, FarmerCustomer, LedgerTransaction, Farm, FarmerVendor, FarmerOutstanding
    from apps.superadmin.models import CustomerType, ExpenseType

    profile = get_farmer_profile(request.user)

    if request.method == "POST":
        try:
            buyer_name = (request.POST.get('description') or '').strip()
            category_raw = (request.POST.get('category') or '').strip()
            custom_item_name = (request.POST.get('custom_item_name') or '').strip()
            if (category_raw == '__other__' or category_raw.lower() == 'other') and custom_item_name:
                category = custom_item_name
            elif category_raw and category_raw != '__other__':
                category = category_raw
            else:
                category = 'Harvest Trade'
            load_weight = (request.POST.get('load_weight') or request.POST.get('tags') or '0').strip()
            amount_raw = request.POST.get('amount', 0)
            amount = abs(float(amount_raw)) if amount_raw else 0.0

            gst_rate_raw = request.POST.get('gst_rate', '0')
            custom_gst_rate_raw = request.POST.get('custom_gst_rate', '0')
            discount_raw = request.POST.get('discount', '0')
            discount_type = request.POST.get('discount_type', 'amount')
            commission_raw = request.POST.get('commission', '0')
            commission_type = request.POST.get('commission_type', 'amount')

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

            # Process Paid Amount, Balance, and Due Date
            paid_amount_raw = request.POST.get('paid_amount')
            try:
                paid_amount = float(paid_amount_raw) if paid_amount_raw is not None and paid_amount_raw != '' else net_amount
            except (ValueError, TypeError):
                paid_amount = net_amount

            paid_amount = min(net_amount, max(0.0, paid_amount))
            balance_amount = max(0.0, net_amount - paid_amount)

            farm_id = request.POST.get('farm_id') or None
            payment_method = request.POST.get('payment_method') or 'Bank Transfer'
            
            try:
                close_date = datetime.datetime.strptime(request.POST.get('close_date', ''), '%Y-%m-%d').date()
            except (ValueError, TypeError):
                close_date = datetime.date.today()

            due_date_raw = request.POST.get('due_date')
            try:
                due_date = datetime.datetime.strptime(due_date_raw, '%Y-%m-%d').date() if due_date_raw else (close_date + datetime.timedelta(days=15))
            except (ValueError, TypeError):
                due_date = close_date + datetime.timedelta(days=15)

            if balance_amount <= 0.001:
                status = 'Received'
                outstanding_status = 'Paid'
            elif paid_amount > 0:
                status = 'Partial'
                outstanding_status = 'Partial'
            else:
                status = 'Pending'
                outstanding_status = 'Unpaid'

            farm_instance = Farm.objects.filter(farmer=profile, id=farm_id).first() if farm_id else None

            qty = None
            try:
                qty = float(load_weight) if load_weight else None
            except (ValueError, TypeError):
                pass

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
            unit = (request.POST.get('unit') or 'Tons').strip()
            tags_str = ", ".join(tags_parts)

            sale_trans = LedgerTransaction.objects.create(
                farmer=profile,
                close_date=close_date,
                description=buyer_name,
                payment_method=payment_method,
                category=category,
                category_color='#198754',
                tags=tags_str,
                status=status,
                amount=net_amount if net_amount > 0 else amount,
                farm=farm_instance,
                quantity=qty,
                unit=unit
            )

            # Deduct sold crop inventory from CropSplit
            if qty and qty > 0 and category:
                deduct_crop_inventory(profile, category, qty, farm_instance, buyer_name=buyer_name)

            # Create Customer Outstanding record if balance > 0
            if balance_amount > 0:
                customer_inst = FarmerCustomer.objects.filter(farmer=profile, name=buyer_name).first()
                FarmerOutstanding.objects.create(
                    farmer=profile,
                    customer=customer_inst,
                    invoice_no=f"Sale #{sale_trans.id}: {buyer_name}",
                    due_date=due_date,
                    amount_total=net_amount,
                    amount_paid=paid_amount,
                    status=outstanding_status
                )

            # Handle dynamic Sale Expenses if toggle was enabled
            has_sale_expenses = request.POST.get('has_sale_expenses') == '1'
            exp_categories = request.POST.getlist('sale_expense_category[]')
            exp_amounts = request.POST.getlist('sale_expense_amount[]')
            exp_vendors = request.POST.getlist('sale_expense_vendor[]')
            exp_notes = request.POST.getlist('sale_expense_note[]')

            if has_sale_expenses and exp_categories:
                for cat, amt_str, ven_id, note in zip(exp_categories, exp_amounts, exp_vendors, exp_notes):
                    try:
                        exp_amt = float(amt_str) if amt_str else 0.0
                    except (ValueError, TypeError):
                        exp_amt = 0.0
                    if exp_amt > 0:
                        vendor_inst = FarmerVendor.objects.filter(farmer=profile, id=ven_id).first() if ven_id else None
                        exp_desc = f"Sale Expense ({buyer_name}) - {note.strip() if note and note.strip() else (cat or 'Transport/Labor')}"
                        LedgerTransaction.objects.create(
                            farmer=profile,
                            close_date=close_date,
                            description=exp_desc,
                            payment_method=payment_method,
                            category=cat or 'Transport & Logistics',
                            category_color='#dc3545',
                            tags=f"Outflow | Sale Expense | Linked Sale #{sale_trans.id}",
                            status='Cost',
                            amount=-abs(exp_amt),
                            farm=farm_instance,
                            vendor=vendor_inst,
                        )

            messages.success(request, 'Sale recorded successfully!')
            return redirect('farmer-financials')
        except Exception as e:
            messages.error(request, f'Error creating sale: {e}')

    farmer_crops = get_farmer_sale_crops(profile)

    customer_types = CustomerType.objects.filter(is_active=True).order_by('name')
    customers = FarmerCustomer.objects.filter(farmer=profile).select_related('customer_type').order_by('name')
    farms = Farm.objects.filter(farmer=profile).order_by('land_name')
    vendors = FarmerVendor.objects.filter(farmer=profile).order_by('vendor_name')
    expense_types = ExpenseType.objects.filter(is_active=True).order_by('name')

    pre_farm_id = request.GET.get('farm_id') or ''
    pre_crop = request.GET.get('crop') or ''
    pre_grade = request.GET.get('grade') or ''

    # Mark pre-selection
    for crop in farmer_crops:
        if pre_crop and pre_grade and pre_crop.lower() == crop['base_crop_name'].lower() and pre_grade.lower() == crop['grade'].lower():
            crop['is_selected'] = True
            break
        elif pre_crop and not pre_grade and (pre_crop.lower() == crop['crop__name'].lower() or pre_crop.lower() == crop['base_crop_name'].lower()):
            crop['is_selected'] = True
            break

    context = {
        'farmer_crops': farmer_crops,
        'customers': customers,
        'customer_types': customer_types,
        'farms': farms,
        'vendors': vendors,
        'expense_types': expense_types,
        'is_edit': False,
        'pre_farm_id': pre_farm_id,
        'pre_crop': pre_crop,
        'pre_grade': pre_grade,
        'today': datetime.date.today().strftime('%Y-%m-%d'),
    }
    return render(request, "farmer/add_sale.html", context)


def get_farmer_sale_crops(profile, is_edit=False, current_category=None, current_qty=0):
    from decimal import Decimal
    from django.db.models import Q
    from apps.farmer.models import CropSplit

    harvest_splits_qs = CropSplit.objects.filter(
        farm__farmer=profile,
        stage__in=['Harvested', 'Yield', 'Partially Sold', 'Harvesting']
    ).select_related('crop', 'farm').order_by('-updated_at', '-id')

    extra_splits = CropSplit.objects.filter(
        farm__farmer=profile
    ).exclude(
        id__in=harvest_splits_qs.values_list('id', flat=True)
    ).filter(
        Q(yield_tons__gt=0) | ~Q(harvest_batches=[])
    ).select_related('crop', 'farm').order_by('-updated_at', '-id')

    all_harvest_splits = list(harvest_splits_qs) + list(extra_splits)

    farmer_crops = []
    seen_keys = set()

    for s in all_harvest_splits:
        c_name = s.crop.name if s.crop else (s.crop_category or s.crop_variety or 'Produce')
        c_id = s.crop.id if s.crop else None
        f_id = s.farm.id if s.farm else ''
        f_name = s.farm.land_name if s.farm else 'General Farm'
        unit_str = s.expected_yield_unit or 'Tons'
        batches = s.harvest_batches or []

        if batches:
            ready_batches = [b for b in batches if b.get('status') not in ['Sold', 'Loss / Discard'] and float(b.get('yield', 0)) > 0.0001]
            if ready_batches:
                grade_map = {}
                for b in ready_batches:
                    q = (b.get('quality') or 'Grade 1').strip()
                    y = float(b.get('yield', 0))
                    p = float(b.get('price', 0))
                    v = float(b.get('saleValue') or (y * p))
                    u = b.get('unit') or unit_str
                    if q not in grade_map:
                        grade_map[q] = {'grade': q, 'yield': 0.0, 'amount': 0.0, 'unit': u, 'price': p}
                    grade_map[q]['yield'] += y
                    grade_map[q]['amount'] += v
                    grade_map[q]['price'] = p

                for q, gdata in grade_map.items():
                    key = (c_name, q, f_id)
                    if key not in seen_keys and gdata['yield'] > 0:
                        seen_keys.add(key)
                        avg_p = round(gdata['amount'] / gdata['yield'], 2) if gdata['yield'] > 0 else gdata['price']
                        farmer_crops.append({
                            'crop__id': c_id,
                            'base_crop_name': c_name,
                            'crop__name': f"{c_name} ({q})",
                            'display_name': f"{c_name} - {q}",
                            'grade': q,
                            'farm__id': f_id,
                            'farm__land_name': f_name,
                            'available': round(gdata['yield'], 2),
                            'price': avg_p,
                            'unit': gdata['unit'],
                            'is_selected': False,
                        })


        else:
            avail = float(s.yield_tons or 0)
            if avail > 0:
                key = (c_name, '', f_id)
                if key not in seen_keys:
                    seen_keys.add(key)
                    farmer_crops.append({
                        'crop__id': c_id,
                        'base_crop_name': c_name,
                        'crop__name': c_name,
                        'display_name': c_name,
                        'grade': '',
                        'farm__id': f_id,
                        'farm__land_name': f_name,
                        'available': round(avail, 2),
                        'price': float(s.price_per_mt or 0),
                        'unit': unit_str,
                        'is_selected': False,
                    })

    if is_edit and current_qty > 0 and current_category:
        for crop in farmer_crops:
            if crop['crop__name'] == current_category:
                crop['available'] = round(crop['available'] + current_qty, 2)
            elif current_category.split(' (')[0].strip() == crop['base_crop_name']:
                crop['available'] = round(crop['available'] + current_qty, 2)

    return farmer_crops

def edit_sale_page(request, sale_id):
    import datetime
    import re
    import json
    from django.shortcuts import render, redirect, get_object_or_404
    from django.contrib import messages
    from django.db.models import Sum, Avg, Q
    from apps.farmer.models import CropSplit, FarmerCustomer, LedgerTransaction, Farm, FarmerVendor, FarmerOutstanding
    from apps.superadmin.models import CustomerType, ExpenseType

    profile = get_farmer_profile(request.user)
    sale = get_object_or_404(LedgerTransaction, id=sale_id, farmer=profile)
    old_qty = float(sale.quantity or 0)
    old_category = sale.category
    old_farm = sale.farm

    if request.method == "POST":
        try:
            buyer_name = (request.POST.get('description') or '').strip()
            category_raw = (request.POST.get('category') or '').strip()
            custom_item_name = (request.POST.get('custom_item_name') or '').strip()
            if (category_raw == '__other__' or category_raw.lower() == 'other') and custom_item_name:
                category = custom_item_name
            elif category_raw and category_raw != '__other__':
                category = category_raw
            else:
                category = 'Harvest Trade'
            load_weight = (request.POST.get('load_weight') or request.POST.get('tags') or '0').strip()
            amount_raw = request.POST.get('amount', 0)
            amount = abs(float(amount_raw)) if amount_raw else 0.0

            gst_rate_raw = request.POST.get('gst_rate', '0')
            custom_gst_rate_raw = request.POST.get('custom_gst_rate', '0')
            discount_raw = request.POST.get('discount', '0')
            discount_type = request.POST.get('discount_type', 'amount')
            commission_raw = request.POST.get('commission', '0')
            commission_type = request.POST.get('commission_type', 'amount')

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

            # Process Paid Amount, Balance, and Due Date
            paid_amount_raw = request.POST.get('paid_amount')
            try:
                paid_amount = float(paid_amount_raw) if paid_amount_raw is not None and paid_amount_raw != '' else net_amount
            except (ValueError, TypeError):
                paid_amount = net_amount

            paid_amount = min(net_amount, max(0.0, paid_amount))
            balance_amount = max(0.0, net_amount - paid_amount)

            farm_id = request.POST.get('farm_id') or None
            payment_method = request.POST.get('payment_method') or 'Bank Transfer'
            
            try:
                close_date = datetime.datetime.strptime(request.POST.get('close_date', ''), '%Y-%m-%d').date()
            except (ValueError, TypeError):
                close_date = datetime.date.today()

            due_date_raw = request.POST.get('due_date')
            try:
                due_date = datetime.datetime.strptime(due_date_raw, '%Y-%m-%d').date() if due_date_raw else (close_date + datetime.timedelta(days=15))
            except (ValueError, TypeError):
                due_date = close_date + datetime.timedelta(days=15)

            if balance_amount <= 0.001:
                status = 'Received'
                outstanding_status = 'Paid'
            elif paid_amount > 0:
                status = 'Partial'
                outstanding_status = 'Partial'
            else:
                status = 'Pending'
                outstanding_status = 'Unpaid'

            farm_instance = Farm.objects.filter(farmer=profile, id=farm_id).first() if farm_id else None

            qty = None
            try:
                qty = float(load_weight) if load_weight else None
            except (ValueError, TypeError):
                pass

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
            unit = (request.POST.get('unit') or 'Tons').strip()
            tags_str = ", ".join(tags_parts)
            sale.description = buyer_name
            sale.category = category
            sale.amount = net_amount if net_amount > 0 else amount
            sale.farm = farm_instance
            sale.status = status
            sale.payment_method = payment_method
            sale.close_date = close_date
            sale.quantity = qty
            sale.unit = unit
            sale.tags = tags_str
            sale.save()

            # Rebalance CropSplit inventory
            if old_qty > 0 and old_category:
                restore_crop_inventory(profile, old_category, old_qty, old_farm)
            if qty and qty > 0 and category:
                deduct_crop_inventory(profile, category, qty, farm_instance, buyer_name=buyer_name)

            # Update or Create FarmerOutstanding record
            existing_out = FarmerOutstanding.objects.filter(
                farmer=profile
            ).filter(
                Q(invoice_no__startswith=f"Sale #{sale.id}:") | Q(invoice_no__icontains=f"Sale #{sale.id}")
            ).first()

            customer_inst = FarmerCustomer.objects.filter(farmer=profile, name=buyer_name).first()

            if existing_out:
                existing_out.customer = customer_inst
                existing_out.invoice_no = f"Sale #{sale.id}: {buyer_name}"
                existing_out.amount_total = net_amount
                existing_out.amount_paid = paid_amount
                existing_out.due_date = due_date
                existing_out.status = outstanding_status
                existing_out.save()
            elif balance_amount > 0:
                FarmerOutstanding.objects.create(
                    farmer=profile,
                    customer=customer_inst,
                    invoice_no=f"Sale #{sale.id}: {buyer_name}",
                    due_date=due_date,
                    amount_total=net_amount,
                    amount_paid=paid_amount,
                    status=outstanding_status
                )

            # Handle linked sale expenses
            LedgerTransaction.objects.filter(farmer=profile, tags__icontains=f"Linked Sale #{sale.id}").delete()
            has_sale_expenses = request.POST.get('has_sale_expenses') == '1'
            exp_categories = request.POST.getlist('sale_expense_category[]')
            exp_amounts = request.POST.getlist('sale_expense_amount[]')
            exp_vendors = request.POST.getlist('sale_expense_vendor[]')
            exp_notes = request.POST.getlist('sale_expense_note[]')

            if has_sale_expenses and exp_categories:
                for cat, amt_str, ven_id, note in zip(exp_categories, exp_amounts, exp_vendors, exp_notes):
                    try:
                        exp_amt = float(amt_str) if amt_str else 0.0
                    except (ValueError, TypeError):
                        exp_amt = 0.0
                    if exp_amt > 0:
                        vendor_inst = FarmerVendor.objects.filter(farmer=profile, id=ven_id).first() if ven_id else None
                        exp_desc = f"Sale Expense ({buyer_name}) - {note.strip() if note and note.strip() else (cat or 'Transport/Labor')}"
                        LedgerTransaction.objects.create(
                            farmer=profile,
                            close_date=close_date,
                            description=exp_desc,
                            payment_method=payment_method,
                            category=cat or 'Transport & Logistics',
                            category_color='#dc3545',
                            tags=f"Outflow | Sale Expense | Linked Sale #{sale.id}",
                            status='Cost',
                            amount=-abs(exp_amt),
                            farm=farm_instance,
                            vendor=vendor_inst,
                        )

            messages.success(request, 'Sale updated successfully!')
            return redirect('farmer-financials')
        except Exception as e:
            messages.error(request, f'Error updating sale: {e}')

    farmer_crops = get_farmer_sale_crops(profile, is_edit=True, current_category=sale.category, current_qty=old_qty)
    sale_category = (sale.category or '').strip()
    sale_base_category = sale_category.split(' (')[0].strip()
    matching_crop = next((
        crop for crop in farmer_crops
        if sale_category == crop['crop__name']
        or sale_base_category == crop['base_crop_name']
    ), None)
    is_other_category = bool(sale_category and matching_crop is None)

    for crop in farmer_crops:
        if crop is matching_crop:
            crop['is_selected'] = True
            break

    customer_types = CustomerType.objects.filter(is_active=True).order_by('name')
    customers = FarmerCustomer.objects.filter(farmer=profile).select_related('customer_type').order_by('name')
    farms = Farm.objects.filter(farmer=profile).order_by('land_name')
    vendors = FarmerVendor.objects.filter(farmer=profile).order_by('vendor_name')
    expense_types = ExpenseType.objects.filter(is_active=True).order_by('name')
    linked_expenses = LedgerTransaction.objects.filter(
        farmer=profile,
        tags__icontains=f"Linked Sale #{sale.id}"
    ).order_by('id')

    # Parse saved GST, Discount, Commission, and Subtotal from tags
    tags = sale.tags or ''
    gst_rate = '0'
    custom_gst_rate = ''
    discount = ''
    discount_type = 'amount'
    commission = ''
    commission_type = 'amount'
    subtotal = float(sale.amount)

    subtotal_match = re.search(r'Subtotal:\s*₹?([\d\.]+)', tags)
    if subtotal_match:
        subtotal = float(subtotal_match.group(1))

    gst_match = re.search(r'GST:?\s*([\d\.]+)%', tags)
    if gst_match:
        g_val = gst_match.group(1)
        if g_val in ['0', '5', '12', '18', '28']:
            gst_rate = g_val
        else:
            gst_rate = 'custom'
            custom_gst_rate = g_val

    disc_pct_match = re.search(r'Discount:\s*([\d\.]+)%', tags)
    if disc_pct_match:
        discount = disc_pct_match.group(1)
        discount_type = 'percent'
    else:
        disc_amt_match = re.search(r'Discount:\s*₹?([\d\.]+)', tags)
        if disc_amt_match:
            discount = disc_amt_match.group(1)
            discount_type = 'amount'

    comm_pct_match = re.search(r'Comm(?:ission)?:\s*([\d\.]+)%', tags)
    if comm_pct_match:
        commission = comm_pct_match.group(1)
        commission_type = 'percent'
    else:
        comm_amt_match = re.search(r'Comm(?:ission)?:\s*₹?([\d\.]+)', tags)
        if comm_amt_match:
            commission = comm_amt_match.group(1)
            commission_type = 'amount'

    if not subtotal_match:
        try:
            g_pct = float(custom_gst_rate if gst_rate == 'custom' else gst_rate)
        except (ValueError, TypeError):
            g_pct = 0.0
        d_amt = float(discount) if discount and discount_type == 'amount' else 0.0
        c_amt = float(commission) if commission and commission_type == 'amount' else 0.0
        denom = 1 + (g_pct / 100.0)
        if discount_type == 'percent' and discount:
            denom -= float(discount) / 100.0
        if commission_type == 'percent' and commission:
            denom -= float(commission) / 100.0
        if denom > 0:
            subtotal = (float(sale.amount) + d_amt + c_amt) / denom

    # Parse paid, balance, and due date
    existing_out = FarmerOutstanding.objects.filter(
        farmer=profile
    ).filter(
        Q(invoice_no__startswith=f"Sale #{sale.id}:") | Q(invoice_no__icontains=f"Sale #{sale.id}")
    ).first()

    saved_paid_amount = ''
    saved_balance_amount = ''
    saved_due_date = ''

    if existing_out:
        saved_paid_amount = f"{existing_out.amount_paid:.2f}"
        saved_balance_amount = f"{(existing_out.amount_total - existing_out.amount_paid):.2f}"
        saved_due_date = existing_out.due_date.strftime('%Y-%m-%d') if existing_out.due_date else ''
    else:
        paid_match = re.search(r'Paid:\s*₹?([\d\.]+)', tags)
        bal_match = re.search(r'Balance:\s*₹?([\d\.]+)', tags)
        due_match = re.search(r'Due:\s*([\d-]+)', tags)

        if paid_match:
            saved_paid_amount = paid_match.group(1)
        elif sale.status == 'Received':
            saved_paid_amount = f"{sale.amount:.2f}"
        
        if bal_match:
            saved_balance_amount = bal_match.group(1)
        elif sale.status != 'Received':
            saved_balance_amount = f"{sale.amount:.2f}"
        else:
            saved_balance_amount = "0.00"

        if due_match:
            saved_due_date = due_match.group(1)
        else:
            saved_due_date = (sale.close_date + datetime.timedelta(days=15)).strftime('%Y-%m-%d')

    linked_expenses_list = []
    for exp in linked_expenses:
        linked_expenses_list.append({
            'category': exp.category or 'Transport & Logistics',
            'vendor_id': str(exp.vendor_id) if exp.vendor_id else '',
            'note': exp.description.replace(f"Sale Expense ({sale.description}) - ", "").replace("Sale Expense - ", ""),
            'amount': abs(float(exp.amount))
        })
    linked_expenses_json = json.dumps(linked_expenses_list)

    context = {
        'sale': sale,
        'subtotal_valuation': f"{subtotal:.2f}",
        'saved_gst_rate': gst_rate,
        'saved_custom_gst_rate': custom_gst_rate,
        'saved_discount': discount,
        'saved_discount_type': discount_type,
        'saved_commission': commission,
        'saved_commission_type': commission_type,
        'saved_paid_amount': saved_paid_amount,
        'saved_balance_amount': saved_balance_amount,
        'saved_due_date': saved_due_date,
        'farmer_crops': farmer_crops,
        'customers': customers,
        'customer_types': customer_types,
        'farms': farms,
        'vendors': vendors,
        'expense_types': expense_types,
        'linked_expenses': linked_expenses,
        'linked_expenses_json': linked_expenses_json,
        'is_edit': True,
        'is_other_category': is_other_category,
        'today': datetime.date.today().strftime('%Y-%m-%d'),
    }
    return render(request, "farmer/add_sale.html", context)

def expenses_view(request):
    from django.db.models import Sum, Q, Count
    from django.shortcuts import render
    from apps.farmer.models import Farm, FarmerVendor, LedgerTransaction, FarmerOutstanding
    from apps.superadmin.models import ExpenseType, ExpenseUnit, DocumentType
    import datetime

    profile = get_farmer_profile(request.user)
    try:
        from apps.farmer.recurring_utils import process_recurring_expenses
        process_recurring_expenses(profile, request=request)
    except Exception:
        pass

    farms   = Farm.objects.filter(farmer=profile).order_by('land_name')
    vendors = FarmerVendor.objects.filter(farmer=profile).order_by('vendor_name')

    expense_types      = ExpenseType.objects.filter(is_active=True).order_by('name')
    expense_units      = ExpenseUnit.objects.filter(is_active=True).order_by('name')
    document_types = DocumentType.objects.filter(is_active=True).order_by('name')

    expenses_qs = LedgerTransaction.objects.filter(
        farmer=profile
    ).filter(
        Q(status__in=['Cost', 'Paid']) | Q(amount__lt=0)
    ).order_by('-close_date', '-id')

    total_expenses  = sum(abs(float(e.amount)) for e in expenses_qs)
    total_count     = expenses_qs.count()

    # This-month total
    today = datetime.date.today()
    this_month_qs = expenses_qs.filter(close_date__year=today.year, close_date__month=today.month)
    this_month_total = sum(abs(float(e.amount)) for e in this_month_qs)
    this_month_count = this_month_qs.count()

    # Vendor-linked expenses
    vendor_expense_qs = expenses_qs.filter(vendor__isnull=False)
    vendor_expense_total = sum(abs(float(e.amount)) for e in vendor_expense_qs)
    total_vendors   = vendors.count()

    # Outstanding payables (Vendor / Expense unpaid bills)
    outstanding_qs = FarmerOutstanding.objects.filter(
        farmer=profile
    ).filter(
        Q(vendor__isnull=False) | Q(invoice_no__startswith='Vendor:') | Q(invoice_no__startswith='Expense:')
    ).exclude(status='Paid')
    outstanding_total = sum(float(o.amount_total) - float(o.amount_paid) for o in outstanding_qs)
    unpaid_count = outstanding_qs.count()

    seeds_cost = sum(
        abs(float(e.amount)) for e in expenses_qs
        if e.category and ('seed' in e.category.lower() or 'seed' in e.description.lower())
    )
    fertilizers_cost = sum(
        abs(float(e.amount)) for e in expenses_qs
        if e.category and ('fertilizer' in e.category.lower() or 'input' in e.category.lower() or 'nutrient' in e.category.lower())
    )
    general_cost = total_expenses - seeds_cost - fertilizers_cost

    from apps.farmer.models import FarmerInventory
    from apps.superadmin.models import InventoryItem
    farmer_inv_items = FarmerInventory.objects.filter(farmer=profile).order_by('item_name')
    inventory_items = []
    seen = set()
    for fi in farmer_inv_items:
        if fi.item_name and fi.item_name.strip() and fi.item_name.strip().lower() not in seen:
            seen.add(fi.item_name.strip().lower())
            inventory_items.append({
                'name': fi.item_name.strip(),
                'unit': fi.unit or '',
                'category': fi.category or '',
                'quantity': fi.quantity or 0,
                'is_existing': True
            })

    master_items = InventoryItem.objects.filter(is_active=True).order_by('name')
    for mi in master_items:
        if mi.name and mi.name.strip() and mi.name.strip().lower() not in seen:
            seen.add(mi.name.strip().lower())
            inventory_items.append({
                'name': mi.name.strip(),
                'unit': '',
                'category': getattr(mi.product_type, 'name', '') if hasattr(mi, 'product_type') and mi.product_type else '',
                'quantity': 0,
                'is_existing': False
            })

    context = {
        'expenses':            expenses_qs,
        'total_expenses':      total_expenses,
        'total_count':         total_count,
        'this_month_total':    this_month_total,
        'this_month_count':    this_month_count,
        'vendor_expense_total': vendor_expense_total,
        'total_vendors':       total_vendors,
        'outstanding_total':   outstanding_total,
        'unpaid_count':        unpaid_count,
        'seeds_cost':          seeds_cost,
        'fertilizers_cost':    fertilizers_cost,
        'general_cost':        general_cost,
        'farms':               farms,
        'vendors':             vendors,
        'expense_types':       expense_types,
        'expense_units':       expense_units,
        'document_types':      document_types,
        'inventory_items':     inventory_items,
        'is_direct_farmer':    (request.session.get('active_role') == 'farmer') or (not request.session.get('active_role') and get_farmer_profile(request) is not None and get_active_manager_staff(request) is None),
    }
    return render(request, "farmer/expenses.html", context)


def quick_add_customer_api(request):
    from django.http import JsonResponse
    from apps.farmer.models import FarmerCustomer
    from apps.superadmin.models import CustomerType
    if request.method == 'POST':
        profile = get_farmer_profile(request.user)
        name = request.POST.get('name', '').strip()
        company_name = request.POST.get('company_name', '').strip()
        contact_number = request.POST.get('contact_number', '').strip() or request.POST.get('phone', '').strip()
        customer_type_id = request.POST.get('customer_type_id') or None
        
        if not name:
            return JsonResponse({'success': False, 'error': 'Customer name is required'})
        if contact_number and not re.fullmatch(r'[0-9]{10,13}', contact_number):
            return JsonResponse({'success': False, 'error': 'Mobile number must contain 10 to 13 digits.'})
            
        ct_obj = CustomerType.objects.filter(id=customer_type_id).first() if customer_type_id else None
        
        cust, created = FarmerCustomer.objects.get_or_create(
            farmer=profile,
            name=name,
            defaults={
                'company_name': company_name,
                'contact_number': contact_number,
                'customer_type': ct_obj,
                'status': 'Active'
            }
        )
        return JsonResponse({
            'success': True,
            'id': cust.id,
            'name': cust.name,
            'contact_number': cust.contact_number or contact_number or '',
            'company_name': cust.company_name or '',
            'customer_type_id': cust.customer_type_id or ''
        })
    return JsonResponse({'success': False, 'error': 'Invalid request'})


def quick_add_customer_type(request):
    from django.http import JsonResponse
    from apps.superadmin.models import CustomerType
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Customer type name is required'})
        
        ct, created = get_or_reactivate_master(CustomerType,
            name=name,
            defaults={'description': description, 'is_active': True}
        )
        return JsonResponse({
            'success': True,
            'id': ct.id,
            'name': ct.name
        })
    return JsonResponse({'success': False, 'error': 'Invalid request'})


def quick_add_expense_type(request):
    from django.http import JsonResponse
    from apps.superadmin.models import ExpenseType
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        is_vendor = request.POST.get('is_vendor_based', 'false') == 'true'
        if not name:
            return JsonResponse({'success': False, 'error': 'Expense type name is required'})
        
        et, created = get_or_reactivate_master(ExpenseType,
            name=name,
            defaults={'is_vendor_based': is_vendor, 'is_active': True}
        )
        return JsonResponse({
            'success': True,
            'id': et.id,
            'name': et.name,
            'is_vendor_based': et.is_vendor_based
        })
    return JsonResponse({'success': False, 'error': 'Invalid request'})


def quick_add_vendor(request):
    from django.http import JsonResponse
    from apps.farmer.models import FarmerVendor
    if request.method == 'POST':
        profile = get_farmer_profile(request.user)
        vendor_name = request.POST.get('vendor_name', '').strip()
        business_name = request.POST.get('business_name', '').strip()
        mobile_number = request.POST.get('mobile_number', '').strip()

        if not vendor_name:
            return JsonResponse({'success': False, 'error': 'Vendor name is required'})

        vendor, created = FarmerVendor.objects.get_or_create(
            farmer=profile,
            vendor_name=vendor_name,
            defaults={
                'business_name': business_name or vendor_name,
                'mobile_number': mobile_number
            }
        )
        return JsonResponse({
            'success': True,
            'id': vendor.id,
            'vendor_name': vendor.vendor_name,
            'business_name': vendor.business_name
        })
    return JsonResponse({'success': False, 'error': 'Invalid request'})


def quick_add_expense_unit(request):
    from django.http import JsonResponse
    from apps.superadmin.models import ExpenseUnit
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        slug = request.POST.get('slug', '').strip() or name.lower().replace(' ', '_')
        if not name:
            return JsonResponse({'success': False, 'error': 'Unit name is required'})
        
        unit, created = get_or_reactivate_master(ExpenseUnit,
            name=name,
            defaults={'slug': slug, 'is_active': True}
        )
        if not created and request.POST.get('slug'):
            unit.slug = request.POST.get('slug').strip()
            unit.save(update_fields=['slug'])
        return JsonResponse({
            'success': True,
            'id': unit.id,
            'name': unit.name,
            'slug': unit.slug
        })
    return JsonResponse({'success': False, 'error': 'Invalid request'})


def quick_add_task_category(request):
    from django.http import JsonResponse
    from apps.superadmin.models import TaskCategory
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        is_farm_required = request.POST.get('is_farm_required') == 'on'
        is_crop_required = request.POST.get('is_crop_required') == 'on'
        if not name:
            return JsonResponse({'success': False, 'error': 'Category name is required'})
        
        category, created = get_or_reactivate_master(TaskCategory,
            name=name,
            defaults={
                'description': description,
                'is_farm_required': is_farm_required,
                'is_crop_required': is_crop_required
            }
        )
        return JsonResponse({
            'success': True,
            'id': category.id,
            'name': category.name,
            'is_farm_required': category.is_farm_required,
            'is_crop_required': category.is_crop_required
        })
    return JsonResponse({'success': False, 'error': 'Invalid request'})

def quick_add_task_priority(request):
    from django.http import JsonResponse
    from apps.superadmin.models import TaskPriority
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Priority name is required'})
            
        priority, created = get_or_reactivate_master(TaskPriority,
            name=name,
            defaults={'description': description}
        )
        return JsonResponse({
            'success': True,
            'id': priority.id,
            'name': priority.name
        })
    return JsonResponse({'success': False, 'error': 'Invalid request'})


def quick_add_inventory_category(request):
    from django.http import JsonResponse
    from apps.superadmin.models import InventoryCategory
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        layout_type = request.POST.get('layout_type', 'Warehouse').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Category name is required'})
        
        category, created = get_or_reactivate_master(InventoryCategory,
            name=name,
            defaults={'layout_type': layout_type, 'is_active': True}
        )
        return JsonResponse({
            'success': True,
            'id': category.id,
            'name': category.name,
            'layout_type': category.layout_type
        })
    return JsonResponse({'success': False, 'error': 'Invalid request'})


def quick_add_storage_location(request):
    from django.http import JsonResponse
    from apps.superadmin.models import StorageLocation
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Location name is required'})
        
        location, created = get_or_reactivate_master(StorageLocation,
            name=name,
            defaults={'is_active': True}
        )
        return JsonResponse({
            'success': True,
            'id': location.id,
            'name': location.name
        })
    return JsonResponse({'success': False, 'error': 'Invalid request'})


def quick_add_inventory_type(request):
    from django.http import JsonResponse
    from apps.superadmin.models import ProductType
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Inventory Type name is required'})
        
        inv_type, created = get_or_reactivate_master(ProductType,
            name=name,
            defaults={'is_active': True}
        )
        return JsonResponse({
            'success': True,
            'id': inv_type.id,
            'name': inv_type.name
        })
    return JsonResponse({'success': False, 'error': 'Invalid request'})




def customer(request):
    from apps.farmer.models import FarmerCustomer, LedgerTransaction
    from apps.superadmin.models import City, State, DocumentType
    profile = get_farmer_profile(request.user)
    customers = list(FarmerCustomer.objects.filter(farmer=profile).select_related('document').order_by('-created_at'))
    states = State.objects.filter(country__name__iexact='India').order_by('name')
    if not states.exists():
        states = State.objects.all().order_by('name')

    document_types = DocumentType.objects.filter(is_active=True).order_by('name')

    state_dict = {str(s.id): s.name for s in states}
    city_dict = {str(c.id): c.name for c in City.objects.all()}
    from apps.superadmin.models import Taluk

    for c in customers:
        # Load districts & taluks for edit modal
        if c.state and str(c.state).isdigit():
            c.state_id = str(c.state)
            c.district_id = str(c.district) if c.district else ''
            c.districts = list(City.objects.filter(state_id=c.state).order_by('name'))
            c.taluks = list(Taluk.objects.filter(city_id=c.district_id).order_by('name')) if (c.district_id and c.district_id.isdigit()) else []
        elif c.address and '|' in c.address:
            parts = c.address.split('|')
            if len(parts) >= 3:
                c.state_id = parts[0]
                c.district_id = parts[1]
                c.pincode = c.pincode or parts[2]
                if c.state_id and c.state_id.isdigit():
                    c.districts = list(City.objects.filter(state_id=c.state_id).order_by('name'))
                    c.taluks = list(Taluk.objects.filter(city_id=c.district_id).order_by('name')) if (c.district_id and c.district_id.isdigit()) else []
                else:
                    c.districts = []
                    c.taluks = []
            else:
                c.districts = []
                c.taluks = []
        else:
            c.districts = []
            c.taluks = []

        # Formatted address
        addr_parts = []
        if c.address and '|' not in c.address:
            addr_parts.append(c.address.strip())
        if hasattr(c, 'taluk') and c.taluk:
            addr_parts.append(str(c.taluk).strip())
        if c.district:
            d_name = city_dict.get(str(c.district), c.district)
            if d_name: addr_parts.append(str(d_name).strip())
        if c.state:
            s_name = state_dict.get(str(c.state), c.state)
            if s_name: addr_parts.append(str(s_name).strip())
        if c.pincode:
            addr_parts.append(str(c.pincode).strip())

        if addr_parts:
            c.formatted_address = ", ".join(addr_parts)
        elif c.address and '|' in c.address:
            parts = c.address.split('|')
            s_name = state_dict.get(parts[0], parts[0]) if len(parts) > 0 and parts[0] else ''
            d_name = city_dict.get(parts[1], parts[1]) if len(parts) > 1 and parts[1] else ''
            pin = parts[2] if len(parts) > 2 and parts[2] else ''
            formatted = []
            if d_name: formatted.append(d_name)
            if s_name: formatted.append(s_name)
            if pin: formatted.append(pin)
            c.formatted_address = ", ".join(formatted) if formatted else '—'
        else:
            c.formatted_address = c.address or '—'

        import re
        c.transactions = list(LedgerTransaction.objects.filter(farmer=profile, description__icontains=c.name).order_by('-close_date', '-id'))

        from django.db.models import Q
        from apps.farmer.models import FarmerOutstanding

        outstanding_invoices = FarmerOutstanding.objects.filter(farmer=profile).filter(
            Q(customer=c) | Q(invoice_no__icontains=c.name)
        ).exclude(status='Paid')

        for t in c.transactions:
            # Check for linked outstanding
            out_rec = outstanding_invoices.filter(
                Q(invoice_no__startswith=f"Sale #{t.id}:") | Q(invoice_no__icontains=f"Sale #{t.id}")
            ).first()
            desc_m = re.search(r'\[Paid:\s*₹?([\d\.,]+),\s*Pending:\s*₹?([\d\.,]+)\]', t.description or '')

            if out_rec:
                t.outstanding_id = out_rec.id
                t.paid_amount = float(out_rec.amount_paid)
                t.balance_amount = max(0.0, float(out_rec.amount_total) - float(out_rec.amount_paid))
            elif desc_m:
                t.outstanding_id = None
                t.paid_amount = float(desc_m.group(1).replace(',', ''))
                t.balance_amount = float(desc_m.group(2).replace(',', ''))
            elif t.status == 'Received' or t.status == 'Paid':
                t.outstanding_id = None
                t.paid_amount = float(t.amount)
                t.balance_amount = 0.0
            else:
                t.outstanding_id = None
                t.paid_amount = 0.0
                t.balance_amount = float(t.amount)

        # Calculate pending receivables
        pending_due = sum(float(inv.amount_total) - float(inv.amount_paid) for inv in outstanding_invoices)

        if not outstanding_invoices.exists():
            pending_due += sum(
                float(getattr(t, 'balance_amount', t.amount)) for t in c.transactions
                if t.amount > 0 and (t.status in ['Pending', 'In Transit', 'Unpaid', 'Partial'] or getattr(t, 'balance_amount', 0) > 0)
            )

        c.receivables = round(pending_due, 2)
        c.payables = 0
        c.outstanding = c.receivables

    context = {'customers': customers, 'states': states, 'document_types': document_types}
    return render(request, "farmer/customer.html", context)

def get_districts(request):
    from django.http import JsonResponse
    from apps.superadmin.models import City
    state_id = request.GET.get('state_id')
    districts = list(City.objects.filter(state_id=state_id).order_by('name').values('id', 'name')) if state_id else []
    return JsonResponse({'districts': districts})

def get_taluks(request):
    from django.http import JsonResponse
    from apps.superadmin.models import Taluk
    district_id = request.GET.get('district_id') or request.GET.get('city_id')
    taluks = list(Taluk.objects.filter(city_id=district_id).order_by('name').values('id', 'name')) if district_id else []
    return JsonResponse({'taluks': taluks})

@csrf_exempt
def quick_add_crop(request):
    from django.http import JsonResponse
    from apps.superadmin.models import Crop, CropType
    if request.method == "POST":
        name = request.POST.get('crop_name', '').strip()
        crop_type_id = request.POST.get('crop_type_id', '').strip()
        crop_type_name = request.POST.get('crop_type_name', '').strip()

        if not name:
            return JsonResponse({'success': False, 'error': 'Crop name cannot be empty.'})

        crop_type = None
        if crop_type_id:
            crop_type = CropType.objects.filter(id=crop_type_id).first()

        if not crop_type and crop_type_name:
            crop_type = CropType.objects.filter(name__iexact=crop_type_name).first()
            if not crop_type:
                crop_type = CropType.objects.create(name=crop_type_name, description=f"Crop Type: {crop_type_name}", is_active=True)
            elif not crop_type.is_active:
                crop_type.is_active = True
                crop_type.save(update_fields=['is_active'])

        if not crop_type:
            crop_type = CropType.objects.filter(name__iexact=name).first()
        if not crop_type:
            crop_type = CropType.objects.first()
        if not crop_type:
            crop_type = CropType.objects.create(name=name, description=f"Auto-created crop type for {name}", is_active=True)

        crop = Crop.objects.filter(name__iexact=name).first()
        if not crop:
            crop = Crop.objects.create(name=name, crop_type=crop_type, description=f"Crop: {name}", is_active=True)
        else:
            update_fields = []
            if not crop.is_active:
                crop.is_active = True
                update_fields.append('is_active')
            if crop_type and crop.crop_type != crop_type:
                crop.crop_type = crop_type
                update_fields.append('crop_type')
            if update_fields:
                crop.save(update_fields=update_fields)

        return JsonResponse({
            'success': True,
            'id': crop.id,
            'crop_type_id': crop.crop_type_id or crop_type.id,
            'crop_type_name': crop.crop_type.name if crop.crop_type else crop_type.name,
            'name': crop.name
        })
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def quick_add_crop_type(request):
    from django.http import JsonResponse
    from apps.superadmin.models import CropType
    if request.method == "POST":
        name = request.POST.get('name', '').strip() or request.POST.get('crop_type_name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Crop type name cannot be empty.'})

        crop_type = CropType.objects.filter(name__iexact=name).first()
        created = False
        if not crop_type:
            crop_type = CropType.objects.create(name=name, description=f"Crop Type: {name}", is_active=True)
            created = True
        elif not crop_type.is_active:
            crop_type.is_active = True
            crop_type.save(update_fields=['is_active'])

        return JsonResponse({
            'success': True,
            'id': crop_type.id,
            'name': crop_type.name,
            'created': created
        })
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def quick_add_soil_type(request):
    from django.http import JsonResponse
    from apps.superadmin.models import SoilType
    if request.method == "POST":
        name = request.POST.get('soil_name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Soil type cannot be empty.'})
        
        soil, created = get_or_reactivate_master(SoilType,
            name=name,
            defaults={'description': f"Auto-created soil type: {name}", 'is_active': True}
        )
        return JsonResponse({
            'success': True,
            'id': soil.id,
            'name': soil.name
        })
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def quick_add_irrigation_type(request):
    from django.http import JsonResponse
    from apps.superadmin.models import IrrigationType
    if request.method == "POST":
        name = request.POST.get('irrigation_name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Irrigation type cannot be empty.'})
        
        irrigation, created = get_or_reactivate_master(IrrigationType,
            name=name,
            defaults={'description': f"Auto-created irrigation type: {name}", 'is_active': True}
        )
        return JsonResponse({
            'success': True,
            'id': irrigation.id,
            'name': irrigation.name
        })
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def quick_add_water_source(request):
    from django.http import JsonResponse
    from apps.superadmin.models import WaterSourceType
    if request.method == "POST":
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Water source cannot be empty.'})
        
        obj, created = get_or_reactivate_master(WaterSourceType,
            name=name,
            defaults={'description': f"Auto-created water source: {name}", 'is_active': True}
        )
        return JsonResponse({'success': True, 'id': obj.id, 'name': obj.name})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def quick_add_electricity_type(request):
    from django.http import JsonResponse
    from apps.superadmin.models import ElectricityType
    if request.method == "POST":
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Electricity type cannot be empty.'})
        
        obj, created = get_or_reactivate_master(ElectricityType,
            name=name,
            defaults={'description': f"Auto-created electricity type: {name}", 'is_active': True}
        )
        return JsonResponse({'success': True, 'id': obj.id, 'name': obj.name})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def quick_add_farming_type(request):
    from django.http import JsonResponse
    from apps.superadmin.models import FarmingType
    if request.method == "POST":
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Farming type cannot be empty.'})
        
        obj, created = get_or_reactivate_master(FarmingType,
            name=name,
            defaults={'description': f"Auto-created farming type: {name}", 'is_active': True}
        )
        return JsonResponse({'success': True, 'id': obj.id, 'name': obj.name})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def quick_add_fencing_type(request):
    from django.http import JsonResponse
    from apps.superadmin.models import FencingType
    if request.method == "POST":
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Fencing type cannot be empty.'})
        
        obj, created = get_or_reactivate_master(FencingType,
            name=name,
            defaults={'description': f"Auto-created fencing type: {name}", 'is_active': True}
        )
        return JsonResponse({'success': True, 'id': obj.id, 'name': obj.name})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def quick_add_crop_category(request):
    from django.http import JsonResponse
    from apps.superadmin.models import CropCategory
    if request.method == "POST":
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Crop category cannot be empty.'})
        
        obj, created = get_or_reactivate_master(CropCategory,
            name=name,
            defaults={'description': f"Auto-created crop category: {name}", 'is_active': True}
        )
        return JsonResponse({'success': True, 'id': obj.id, 'name': obj.name})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def quick_add_crop_pattern(request):
    from django.http import JsonResponse
    from apps.superadmin.models import CropPattern
    if request.method == "POST":
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Crop pattern cannot be empty.'})
        
        obj, created = get_or_reactivate_master(CropPattern,
            name=name,
            defaults={'description': f"Auto-created crop pattern: {name}", 'is_active': True}
        )
        return JsonResponse({'success': True, 'id': obj.id, 'name': obj.name})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def quick_add_crop_variety(request):
    from django.http import JsonResponse
    from apps.superadmin.models import CropVariety
    if request.method == "POST":
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Crop variety cannot be empty.'})
        
        obj, created = get_or_reactivate_master(CropVariety,
            name=name,
            defaults={'description': f"Auto-created crop variety: {name}", 'is_active': True}
        )
        return JsonResponse({'success': True, 'id': obj.id, 'name': obj.name})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def quick_add_crop_quality(request):
    from django.http import JsonResponse
    from apps.superadmin.models import CropQuality
    if request.method == "POST":
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Quality name cannot be empty.'})
        
        obj, created = get_or_reactivate_master(CropQuality,
            name=name,
            defaults={'description': f"Auto-created crop quality: {name}", 'is_active': True}
        )
        return JsonResponse({'success': True, 'id': obj.id, 'name': obj.name})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def quick_add_maintenance_schedule(request):
    from django.http import JsonResponse
    from apps.superadmin.models import MaintenanceSchedule
    if request.method == "POST":
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Maintenance schedule cannot be empty.'})
        
        obj, created = get_or_reactivate_master(MaintenanceSchedule,
            name=name,
            defaults={'description': f"Auto-created maintenance schedule: {name}", 'is_active': True}
        )
        return JsonResponse({'success': True, 'id': obj.id, 'name': obj.name})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def quick_add_document_type(request):
    from django.http import JsonResponse
    from apps.superadmin.models import DocumentCategory
    if request.method == "POST":
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Document type cannot be empty.'})
        
        obj, created = get_or_reactivate_master(DocumentCategory,
            name=name,
            defaults={'document_type': 'My Land', 'description': f"Auto-created document type: {name}", 'is_active': True}
        )
        return JsonResponse({'success': True, 'id': obj.id, 'name': obj.name})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def quick_add_health_indicator(request):
    from django.http import JsonResponse
    from apps.superadmin.models import HealthIndicator
    if request.method == "POST":
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Health indicator cannot be empty.'})
        
        obj, created = get_or_reactivate_master(HealthIndicator,
            name=name,
            defaults={'description': f"Auto-created health indicator: {name}", 'is_active': True}
        )
        return JsonResponse({'success': True, 'id': obj.id, 'name': obj.name})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

MASTER_TYPE_MODEL_MAP = {
    'crop': ('apps.superadmin.models', 'Crop', 'name'),
    'soil_type': ('apps.superadmin.models', 'SoilType', 'name'),
    'irrigation_type': ('apps.superadmin.models', 'IrrigationType', 'name'),
    'water_source': ('apps.superadmin.models', 'WaterSourceType', 'name'),
    'electricity_type': ('apps.superadmin.models', 'ElectricityType', 'name'),
    'farming_type': ('apps.superadmin.models', 'FarmingType', 'name'),
    'fencing_type': ('apps.superadmin.models', 'FencingType', 'name'),
    'crop_category': ('apps.superadmin.models', 'CropCategory', 'name'),
    'crop_pattern': ('apps.superadmin.models', 'CropPattern', 'name'),
    'crop_variety': ('apps.superadmin.models', 'CropVariety', 'name'),
    'crop_quality': ('apps.superadmin.models', 'CropQuality', 'name'),
    'maintenance_schedule': ('apps.superadmin.models', 'MaintenanceSchedule', 'name'),
    'document_type': ('apps.superadmin.models', 'DocumentCategory', 'name'),
    'health_indicator': ('apps.superadmin.models', 'HealthIndicator', 'name'),
    'customer_type': ('apps.superadmin.models', 'CustomerType', 'name'),
    'expense_type': ('apps.superadmin.models', 'ExpenseType', 'name'),
    'unit': ('apps.superadmin.models', 'ExpenseUnit', 'name'),
    'task_category': ('apps.superadmin.models', 'TaskCategory', 'name'),
    'task_priority': ('apps.superadmin.models', 'TaskPriority', 'name'),
    'inventory_category': ('apps.superadmin.models', 'InventoryCategory', 'name'),
    'storage_location': ('apps.superadmin.models', 'StorageLocation', 'name'),
    'inventory_type': ('apps.superadmin.models', 'ProductType', 'name'),
    'designation': ('apps.superadmin.models', 'Designation', 'name'),
    'vendor': ('apps.farmer.models', 'FarmerVendor', 'vendor_name'),
    'customer': ('apps.farmer.models', 'FarmerCustomer', 'name'),
}

@csrf_exempt
def quick_list_master_items(request):
    import importlib
    from django.http import JsonResponse
    
    item_type = request.GET.get('item_type') or request.POST.get('item_type')
    if not item_type or item_type not in MASTER_TYPE_MODEL_MAP:
        return JsonResponse({'success': False, 'error': 'Invalid item type'})
    
    mod_name, model_name, name_field = MASTER_TYPE_MODEL_MAP[item_type]
    mod = importlib.import_module(mod_name)
    model = getattr(mod, model_name)
    
    qs = model.objects.all()
    if item_type in ['vendor', 'customer']:
        profile = get_farmer_profile(request.user)
        if profile:
            qs = qs.filter(farmer=profile)
            
    items = []
    for obj in qs.order_by('id'):
        label = getattr(obj, name_field, None)
        if not label:
            label = str(obj)
        item_entry = {
            'id': obj.id,
            'name': label
        }
        if item_type == 'crop' and getattr(obj, 'crop_type', None):
            item_entry['subtitle'] = f"Type: {obj.crop_type.name}"
            item_entry['crop_type'] = obj.crop_type.name
            item_entry['crop_type_id'] = obj.crop_type.id
        elif item_type == 'inventory_category' and getattr(obj, 'layout_type', None):
            item_entry['subtitle'] = f"Layout: {obj.layout_type}"
            item_entry['layout_type'] = obj.layout_type
        elif item_type == 'customer':
            extra = []
            if getattr(obj, 'company_name', None):
                extra.append(obj.company_name)
            if getattr(obj, 'contact_number', None):
                extra.append(obj.contact_number)
            if extra:
                item_entry['subtitle'] = " • ".join(extra)
        items.append(item_entry)
    return JsonResponse({'success': True, 'item_type': item_type, 'items': items})

@csrf_exempt
def quick_delete_master_item(request):
    import importlib
    from django.http import JsonResponse
    
    if get_active_manager_staff(request):
        return JsonResponse({'success': False, 'error': 'Managers do not have permission to delete master options.'})

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST method required'})
        
    item_type = request.POST.get('item_type')
    item_id = request.POST.get('item_id')
    
    if not item_type or not item_id or item_type not in MASTER_TYPE_MODEL_MAP:
        return JsonResponse({'success': False, 'error': 'Invalid parameters'})
        
    mod_name, model_name, name_field = MASTER_TYPE_MODEL_MAP[item_type]
    mod = importlib.import_module(mod_name)
    model = getattr(mod, model_name)
    
    try:
        obj = model.objects.filter(id=item_id).first()
        if not obj:
            return JsonResponse({'success': False, 'error': 'Item not found'})
            
        if item_type in ['vendor', 'customer']:
            profile = get_farmer_profile(request.user)
            if profile and hasattr(obj, 'farmer') and obj.farmer != profile:
                return JsonResponse({'success': False, 'error': 'Unauthorized'})
                
        name_val = getattr(obj, name_field, str(obj))
        obj.delete()
        return JsonResponse({'success': True, 'id': item_id, 'name': name_val, 'item_type': item_type})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

def upcoming_payments(request):
    import datetime
    from django.db.models import Q
    from apps.farmer.models import FarmerCustomer, FarmerOutstanding, LedgerTransaction
    from apps.superadmin.models import State

    profile = get_farmer_profile(request.user)
    today = datetime.date.today()

    # Get customer outstandings (excluding Vendor:)
    customer_outs = FarmerOutstanding.objects.filter(farmer=profile).exclude(invoice_no__startswith='Vendor:').order_by('-created_at', '-id')

    # Auto-mark overdue
    customer_outs.filter(due_date__lt=today, status='Unpaid').update(status='Overdue')
    customer_outs = FarmerOutstanding.objects.filter(farmer=profile).exclude(invoice_no__startswith='Vendor:').order_by('-created_at', '-id')

    outstandings_list = []
    total_receivables = 0.0
    total_paid = 0.0
    overdue_count = 0
    pending_count = 0
    settled_count = 0

    for inv in customer_outs:
        balance = float(inv.amount_total) - float(inv.amount_paid)
        if balance < 0:
            balance = 0.0

        total_receivables += balance
        total_paid += float(inv.amount_paid)

        if inv.status == 'Overdue':
            overdue_count += 1
            pending_count += 1
        elif inv.status in ['Unpaid', 'Partial']:
            pending_count += 1
        elif inv.status == 'Paid':
            settled_count += 1

        cust_name = inv.customer.name if inv.customer else (inv.invoice_no.split(':')[1] if ':' in inv.invoice_no else 'Customer')
        cust_phone = inv.customer.contact_number if (inv.customer and inv.customer.contact_number) else 'N/A'

        outstandings_list.append({
            'id': inv.id,
            'customer_id': inv.customer.id if inv.customer else None,
            'customer_name': cust_name,
            'company_name': inv.customer.company_name if (inv.customer and inv.customer.company_name) else '',
            'contact_number': cust_phone,
            'invoice_no': inv.invoice_no,
            'due_date': inv.due_date,
            'amount_total': float(inv.amount_total),
            'amount_paid': float(inv.amount_paid),
            'balance_due': balance,
            'status': inv.status,
            'is_overdue': inv.due_date < today and inv.status != 'Paid'
        })

    customers = FarmerCustomer.objects.filter(farmer=profile).order_by('name')
    states = State.objects.filter(country__name__iexact='India').order_by('name')
    if not states.exists():
        states = State.objects.all().order_by('name')

    context = {
        'outstandings': outstandings_list,
        'total_receivables': round(total_receivables, 2),
        'total_paid': round(total_paid, 2),
        'overdue_count': overdue_count,
        'pending_count': pending_count,
        'settled_count': settled_count,
        'customers': customers,
        'states': states,
        'today': today
    }
    return render(request, "farmer/upcoming_payments.html", context)

@csrf_exempt
def pay_customer_outstanding_api(request):
    import datetime
    import re
    from django.http import JsonResponse
    from django.db.models import Q
    from apps.farmer.models import FarmerOutstanding, LedgerTransaction, CropSplit, FarmerCustomer

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        try:
            o_id = request.POST.get('outstanding_id')
            t_id = request.POST.get('transaction_id')
            payment_type = request.POST.get('payment_type', 'full')
            pay_amount_str = request.POST.get('amount_paid', '0')
            payment_method = request.POST.get('payment_method', 'Cash')
            notes = request.POST.get('notes', '')

            outstanding = None
            if o_id and str(o_id).isdigit():
                outstanding = FarmerOutstanding.objects.filter(id=int(o_id), farmer=farmer_prof).first()

            sale_trans = None
            if t_id and str(t_id).isdigit():
                sale_trans = LedgerTransaction.objects.filter(id=int(t_id), farmer=farmer_prof).first()

            if not outstanding and sale_trans:
                outstanding = FarmerOutstanding.objects.filter(
                    farmer=farmer_prof
                ).filter(
                    Q(invoice_no__startswith=f"Sale #{sale_trans.id}:") | Q(invoice_no__icontains=f"Sale #{sale_trans.id}")
                ).first()

            if not outstanding and sale_trans:
                # Try finding unlinked outstanding with matching amount
                desc_m = re.search(r'\[Paid:\s*₹?([\d\.,]+),\s*Pending:\s*₹?([\d\.,]+)\]', sale_trans.description or '')
                if desc_m:
                    p_val = float(desc_m.group(1).replace(',', ''))
                    outstanding = FarmerOutstanding.objects.filter(
                        farmer=farmer_prof,
                        amount_total=sale_trans.amount,
                        amount_paid=p_val
                    ).first()
                    if outstanding:
                        outstanding.invoice_no = f"Sale #{sale_trans.id}: " + outstanding.invoice_no
                        outstanding.save()

            # Determine remaining balance
            if outstanding:
                remaining = float(outstanding.amount_total) - float(outstanding.amount_paid)
            elif sale_trans:
                desc_m = re.search(r'\[Paid:\s*₹?([\d\.,]+),\s*Pending:\s*₹?([\d\.,]+)\]', sale_trans.description or '')
                if desc_m:
                    paid_already = float(desc_m.group(1).replace(',', ''))
                    remaining = float(desc_m.group(2).replace(',', ''))
                else:
                    paid_already = 0.0
                    remaining = float(sale_trans.amount)
            else:
                return JsonResponse({'success': False, 'error': 'Sale record not found.'})

            try:
                typed_amount = float(pay_amount_str)
            except (ValueError, TypeError):
                typed_amount = 0.0

            if payment_type == 'partial' and typed_amount > 0 and typed_amount < remaining:
                pay_amount = typed_amount
            else:
                pay_amount = remaining if (payment_type == 'full' or typed_amount >= remaining) else typed_amount

            if pay_amount <= 0:
                return JsonResponse({'success': False, 'error': 'Invalid payment amount.'})

            # If outstanding doesn't exist yet, create it
            if not outstanding and sale_trans:
                cust_match = re.search(r'to\s+([^\[\]]+?)(?:\s*\[|$)', sale_trans.description or '')
                c_name = cust_match.group(1).strip() if cust_match else 'Customer'
                cust_inst = FarmerCustomer.objects.filter(farmer=farmer_prof, name=c_name).first()
                if not cust_inst:
                    cust_inst = FarmerCustomer.objects.create(farmer=farmer_prof, name=c_name, status='Active')
                outstanding = FarmerOutstanding.objects.create(
                    farmer=farmer_prof,
                    customer=cust_inst,
                    invoice_no=f"Sale #{sale_trans.id}: {c_name}",
                    due_date=datetime.date.today(),
                    amount_total=sale_trans.amount,
                    amount_paid=paid_already,
                    status='Partial'
                )

            new_amount_paid = float(outstanding.amount_paid) + pay_amount
            outstanding.amount_paid = new_amount_paid
            total_amt = float(outstanding.amount_total)
            new_balance = max(0.0, total_amt - new_amount_paid)

            if new_balance <= 0.01:
                outstanding.status = 'Paid'
            else:
                outstanding.status = 'Partial'

            outstanding.save()

            # Synchronize linked parent sale transaction
            if not sale_trans:
                sale_id_match = re.search(r'Sale #(\d+)', outstanding.invoice_no)
                if sale_id_match:
                    sale_trans = LedgerTransaction.objects.filter(farmer=farmer_prof, id=int(sale_id_match.group(1))).first()

            if sale_trans:
                st_desc = sale_trans.description or ''
                if new_balance <= 0.01:
                    sale_trans.status = 'Received'
                    if '[Paid:' in st_desc:
                        st_desc = re.sub(r'\s*\[Paid:.*?\]', '', st_desc).strip()
                else:
                    sale_trans.status = 'Partial'
                    if '[Paid:' in st_desc:
                        st_desc = re.sub(r'\[Paid:.*?\]', f'[Paid: ₹{int(new_amount_paid)}, Pending: ₹{int(new_balance)}]', st_desc)
                    else:
                        st_desc += f' [Paid: ₹{int(new_amount_paid)}, Pending: ₹{int(new_balance)}]'

                sale_trans.description = st_desc

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
                sale_trans.save()

                # Sync CropSplit batch if linked
                split_match = re.search(r'CropSplit #(\d+)', sale_trans.tags or '')
                if split_match:
                    try:
                        split_obj = CropSplit.objects.filter(id=int(split_match.group(1)), farm__farmer=farmer_prof).first()
                        if split_obj and split_obj.harvest_batches:
                            batches = split_obj.harvest_batches
                            for b in batches:
                                if b.get('status') == 'Sold' and (b.get('pending', 0) > 0 or b.get('payment_status') == 'Partial'):
                                    b_pending = float(b.get('pending', 0))
                                    b_paid = float(b.get('paid', 0))
                                    deduct = min(b_pending, pay_amount)
                                    b['paid'] = b_paid + deduct
                                    b['pending'] = max(0, b_pending - deduct)
                                    if b['pending'] <= 0.01:
                                        b['payment_status'] = 'Received'
                                    split_obj.harvest_batches = batches
                                    split_obj.save()
                                    break
                    except Exception:
                        pass

            cust_name = outstanding.customer.name if outstanding.customer else "Customer"
            desc = f"Received Payment for Invoice {outstanding.invoice_no} ({cust_name})"
            if notes:
                desc += f" - {notes}"

            linked_tag = f"Inflow | Customer Settlement | Linked Sale #{sale_trans.id}" if sale_trans else "Inflow | Customer Settlement"

            LedgerTransaction.objects.create(
                farmer=farmer_prof,
                close_date=datetime.date.today(),
                description=desc,
                category="Customer Payment",
                payment_method=payment_method,
                tags=linked_tag,
                status="Received",
                amount=pay_amount
            )

            try:
                from django.contrib import messages
                messages.success(request, f'Payment of ₹{pay_amount:.2f} logged successfully!')
            except Exception:
                pass
            log_action(request, "Customer Payment Received", f"Received ₹{pay_amount:.2f} for invoice '{outstanding.invoice_no}' ({cust_name}).", icon_class="ri-checkbox-circle-line", bg_color="success")
            return JsonResponse({
                'success': True,
                'message': f'Payment of ₹{pay_amount:.2f} collected successfully!',
                'new_balance': new_balance,
                'new_paid': new_amount_paid,
                'status': outstanding.status
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})

@csrf_exempt
def add_customer_outstanding(request):
    import random
    from django.http import JsonResponse
    from apps.farmer.models import FarmerCustomer, FarmerOutstanding

    if request.method == "POST":
        try:
            profile = get_farmer_profile(request.user)
            customer_id = request.POST.get('customer_id')
            invoice_no = request.POST.get('invoice_no')
            due_date = request.POST.get('due_date')
            amount_total = request.POST.get('amount_total')

            customer = FarmerCustomer.objects.filter(id=customer_id, farmer=profile).first() if customer_id else None

            if not invoice_no:
                invoice_no = f"INV-CUST-{customer.id if customer else 'GEN'}-{random.randint(1000, 9999)}"

            FarmerOutstanding.objects.create(
                farmer=profile,
                customer=customer,
                invoice_no=invoice_no,
                due_date=due_date,
                amount_total=amount_total,
                amount_paid=0,
                status='Unpaid'
            )
            from django.contrib import messages
            messages.success(request, 'Customer outstanding invoice recorded!')
            log_action(request, "Customer Receivable Recorded", f"Recorded invoice '{invoice_no}' (₹{amount_total}) for customer '{customer.name if customer else 'General'}'.", icon_class="ri-file-list-3-line", bg_color="info")
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})

@csrf_exempt
def add_transaction(request):
    import datetime
    from django.http import JsonResponse
    from apps.farmer.models import LedgerTransaction

    if request.method != "POST":
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    try:
        profile = get_farmer_profile(request.user)

        # ── Core fields ─────────────────────────────────────────────
        description  = (request.POST.get('description') or '').strip()
        category     = (request.POST.get('category') or 'General').strip()
        payment_method = (request.POST.get('payment_method') or '').strip()
        category_color = request.POST.get('category_color', '#6c757d')
        status_input   = request.POST.get('status', '')
        tags_input     = request.POST.get('tags', '')
        transaction_type = request.POST.get('transaction_type', '')

        try:
            close_date = datetime.datetime.strptime(
                request.POST.get('close_date', ''), '%Y-%m-%d'
            ).date()
        except (ValueError, TypeError):
            close_date = datetime.date.today()

        try:
            amount = float(request.POST.get('amount', 0))
        except (ValueError, TypeError):
            amount = 0.0

        # ── Direction (inflow vs outflow) ───────────────────────────
        is_outflow = (
            transaction_type.lower() == 'outflow'
            or status_input in ['Cost', 'Paid']
            or tags_input == 'Outflow'
            or amount < 0
        )
        if is_outflow:
            tags   = tags_input or 'Outflow'
            status = status_input or 'Cost'
            amount = -abs(amount)
        else:
            tags   = tags_input or 'Inflow'
            status = status_input or 'Received'
            amount = abs(amount)

        # ── Farm ────────────────────────────────────────────────────
        farm_id = request.POST.get('farm_id')
        farm_instance = None
        if farm_id:
            from apps.farmer.models import Farm
            farm_instance = Farm.objects.filter(farmer=profile, id=farm_id).first()
            tags = f"{tags} | Farm ID: {farm_id}"

        # ── Crop split tag ──────────────────────────────────────────
        crop_split_id = request.POST.get('crop_split_id')
        crop_split_instance = None
        if crop_split_id:
            from apps.farmer.models import CropSplit
            crop_split_instance = CropSplit.objects.filter(id=crop_split_id, farm__farmer=profile).first()
            if not crop_split_instance:
                return JsonResponse({'success': False, 'error': 'Crop not found.'})
            if is_outflow and not request.POST.get('transaction_id') and (
                crop_split_instance.stage in ('Harvested', 'Sold') or crop_split_instance.is_completed
            ):
                return JsonResponse({'success': False, 'error': 'New expenses cannot be added while this crop is harvested or completed.'})
            tags = f"{tags} | Crop Split ID: {crop_split_id} | CropSplit #{crop_split_id}"

        # ── Vendor ──────────────────────────────────────────────────
        vendor_name = (request.POST.get('vendor') or '').strip()
        vendor_instance = None
        if vendor_name:
            from apps.farmer.models import FarmerVendor
            vendor_instance = FarmerVendor.objects.filter(
                farmer=profile, vendor_name=vendor_name
            ).first()

        # ── Outstanding balance ─────────────────────────────────────
        try:
            balance_amount = float(request.POST.get('balance_amount', 0) or 0)
        except (ValueError, TypeError):
            balance_amount = 0.0

        # ── Quantity / Unit ─────────────────────────────────────────
        qty_raw = request.POST.get('quantity')
        try:
            qty = float(qty_raw) if qty_raw else None
        except (ValueError, TypeError):
            qty = None
        unit = (request.POST.get('unit') or '').strip() or None

        # ── Recurring settings ──────────────────────────────────────
        is_recurring   = request.POST.get('repeat_cycle') not in (None, '', 'false')
        repeat_cycle   = (request.POST.get('repeat_cycle') or '').strip() or None
        reminder_type  = (request.POST.get('reminder_type') or '').strip() or None
        reminder_custom_date = None
        if request.POST.get('reminder_custom_date'):
            try:
                reminder_custom_date = datetime.datetime.strptime(
                    request.POST.get('reminder_custom_date'), '%Y-%m-%d'
                ).date()
            except (ValueError, TypeError):
                reminder_custom_date = None

        # ── Document upload ─────────────────────────────────────────
        document_file = request.FILES.get('document_file')
        document_type = request.POST.get('document_type', 'My Expenses')
        if document_file:
            from auth.helpers import validate_uploaded_file
            valid_doc, doc_err = validate_uploaded_file(document_file)
            if not valid_doc:
                return JsonResponse({'success': False, 'error': doc_err})

            import os
            from apps.superadmin.models import Document
            ext = os.path.splitext(document_file.name)[1].lower()
            doc = Document.objects.create(
                title=f"Expense Receipt - {description[:50]}",
                category=document_type,
                description=(
                    f"Uploaded receipt/bill for expense logged on {close_date}. "
                    f"Description: {description}"
                ),
                file=document_file,
                file_size_bytes=document_file.size,
                file_type=ext,
                uploaded_by=request.user,
                farmer=profile
            )
            tags = f"{tags} | Doc ID: {doc.id}"

        # ── Create or Update transaction ────────────────────────────
        transaction_id = request.POST.get('transaction_id')
        if transaction_id:
            transaction = LedgerTransaction.objects.filter(id=transaction_id, farmer=profile).first()
            if not transaction:
                return JsonResponse({'success': False, 'error': 'Transaction not found.'})
            transaction.farm = farm_instance
            transaction.vendor = vendor_instance
            transaction.close_date = close_date
            transaction.description = description
            transaction.payment_method = payment_method
            transaction.category = category
            transaction.category_color = category_color
            transaction.tags = tags
            transaction.status = status
            transaction.amount = amount
            transaction.balance_due = balance_amount
            transaction.quantity = qty
            transaction.unit = unit
            transaction.is_recurring = is_recurring
            transaction.repeat_cycle = repeat_cycle
            transaction.reminder_type = reminder_type
            transaction.reminder_custom_date = reminder_custom_date
            if is_recurring and not transaction.next_due_date:
                from apps.farmer.recurring_utils import calculate_next_recurring_date
                transaction.next_due_date = calculate_next_recurring_date(close_date, repeat_cycle)
            transaction.save()
        else:
            next_date = None
            if is_recurring:
                from apps.farmer.recurring_utils import calculate_next_recurring_date
                next_date = calculate_next_recurring_date(close_date, repeat_cycle)

            transaction = LedgerTransaction.objects.create(
                farmer=profile,
                farm=farm_instance,
                vendor=vendor_instance,
                close_date=close_date,
                description=description,
                payment_method=payment_method,
                category=category,
                category_color=category_color,
                tags=tags,
                status=status,
                amount=amount,
                balance_due=balance_amount,
                quantity=qty,
                unit=unit,
                is_recurring=is_recurring,
                repeat_cycle=repeat_cycle,
                reminder_type=reminder_type,
                reminder_custom_date=reminder_custom_date,
                next_due_date=next_date,
            )

        # ── Send Recurring Email Confirmation ───────────────────────
        if is_recurring:
            try:
                from apps.superadmin.email_utils import send_recurring_expense_email
                send_recurring_expense_email(transaction, request=request)
            except Exception as e:
                pass

        # ── Create outstanding if balance > 0 ───────────────────────
        if is_outflow and balance_amount > 0:
            from apps.farmer.models import FarmerOutstanding
            invoice_ref = (
                f"Vendor: {vendor_instance.vendor_name if vendor_instance else vendor_name} - {description}"
                if vendor_name else f"Expense: {description}"
            )
            FarmerOutstanding.objects.create(
                farmer=profile,
                farm=farm_instance,
                vendor=vendor_instance,
                invoice_no=invoice_ref,
                due_date=close_date,
                amount_total=balance_amount,
                amount_paid=0,
                status='Unpaid'
            )

        # ── Customer outstanding (inflow with Pending status) ───────
        elif not is_outflow and status in ['Pending', 'In Transit']:
            from apps.farmer.models import FarmerCustomer, FarmerOutstanding
            customer_instance = FarmerCustomer.objects.filter(
                farmer=profile, name=description
            ).first()
            FarmerOutstanding.objects.create(
                farmer=profile,
                farm=farm_instance,
                customer=customer_instance,
                invoice_no=f"Customer: {description} - {category} ({tags})",
                due_date=close_date,
                amount_total=abs(amount),
                amount_paid=0,
                status='Unpaid'
            )

        # ── Add to inventory ─────────────────────────────────────────
        if request.POST.get('add_to_inventory') in ['true', 'on', '1', True] and description:
            from apps.farmer.models import FarmerInventory, InventoryStockLog
            import datetime
            inv_qty = 1
            try:
                inv_qty = float(request.POST.get('quantity', 1) or 1)
            except (ValueError, TypeError):
                inv_qty = 1
            inv_unit = unit or 'Units'
            inv_category = category if category not in ['Vendor', 'Rental', 'General', 'Others'] else 'Store Supply'
            
            existing_inv = FarmerInventory.objects.filter(farmer=profile, item_name__iexact=description).first()
            if existing_inv:
                old_qty = float(existing_inv.quantity or 0)
                new_qty = int(old_qty + inv_qty)
                existing_inv.quantity = new_qty
                if inv_unit and (not existing_inv.unit or existing_inv.unit == 'Units'):
                    existing_inv.unit = inv_unit
                existing_inv.save()
                
                # Log stock addition
                InventoryStockLog.objects.create(
                    inventory=existing_inv,
                    farm=farm_instance,
                    crop_split=crop_split_instance,
                    action='add',
                    quantity=inv_qty,
                    notes=f"Purchased via Expense ({category})",
                    log_date=close_date or datetime.date.today(),
                    qty_before=old_qty,
                    qty_after=new_qty,
                )
            else:
                new_inv = FarmerInventory.objects.create(
                    farmer=profile,
                    farm=farm_instance,
                    item_name=description,
                    category=inv_category,
                    quantity=inv_qty,
                    unit=inv_unit,
                    unit_price=round(abs(amount) / (float(inv_qty) if float(inv_qty) > 0 else 1.0), 2),
                )
                InventoryStockLog.objects.create(
                    inventory=new_inv,
                    farm=farm_instance,
                    crop_split=crop_split_instance,
                    action='add',
                    quantity=inv_qty,
                    notes=f"Purchased via Expense ({category})",
                    log_date=close_date or datetime.date.today(),
                    qty_before=0,
                    qty_after=inv_qty,
                )

        # ── Inflow crop yield deduction ─────────────────────────────
        if not is_outflow:
            try:
                import re
                from decimal import Decimal
                load_val = request.POST.get('load_weight') or tags_input or ''
                match = re.search(r"[-+]?\d*\.?\d+", str(load_val))
                tons_sold = float(match.group()) if match else 0.0
                if tons_sold > 0:
                    from functools import reduce
                    import operator
                    from django.db.models import Q
                    from apps.farmer.models import CropSplit
                    clean_cat = category.split(' (')[0].strip()
                    splits = CropSplit.objects.filter(
                        farm__farmer=profile, stage='Harvested'
                    ).filter(
                        reduce(operator.or_, [
                            Q(crop__name__iexact=category),
                            Q(crop__name__icontains=clean_cat),
                        ])
                    ).order_by('id')
                    if not splits.exists():
                        splits = CropSplit.objects.filter(farm__farmer=profile, stage='Harvested').order_by('id')
                    remaining = tons_sold
                    for s in splits:
                        if remaining <= 0:
                            break
                        available = float(s.yield_tons or 0)
                        if available <= remaining:
                            remaining -= available
                            s.yield_tons = Decimal('0')
                            s.stage = 'Sold'
                            s.save()
                        else:
                            s.yield_tons = Decimal(str(round(available - remaining, 2)))
                            s.save()
                            remaining = 0
            except Exception as e:
                import logging
                logging.error(f"Error syncing crop yield during sale: {e}")

        from django.contrib import messages
        messages.success(request, 'Transaction logged successfully!')
        log_action(
            request,
            "Expense Logged",
            f"Expense '{description}' (₹{abs(amount)}) recorded under {category}.",
            icon_class="ri-money-dollar-circle-line",
            bg_color="success"
        )
        return JsonResponse({
            'success': True,
            'id': transaction.id,
            'balance_due': float(transaction.balance_due),
        })

    except Exception as e:
        import traceback, logging
        logging.error(f"add_transaction error: {traceback.format_exc()}")
        return JsonResponse({'success': False, 'error': str(e)})

@csrf_exempt
def delete_transaction(request):
    from django.http import JsonResponse
    from apps.farmer.models import LedgerTransaction

    if request.method != "POST":
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    # Only direct farmer can delete transactions (not manager/staff)
    active_role = request.session.get('active_role')
    is_acting_as_staff = (active_role == 'manager') or bool(get_active_manager_staff(request))
    if is_acting_as_staff:
        return JsonResponse({'success': False, 'error': 'Permission denied. Only the primary Farmer account can delete expense records.'})

    farmer_prof = get_farmer_profile(request.user)
    try:
        t_id = request.POST.get('transaction_id')
        item = LedgerTransaction.objects.filter(id=t_id, farmer=farmer_prof).first()
        if not item:
            return JsonResponse({'success': False, 'error': 'Expense record not found.'})

        desc = item.description

        from apps.farmer.sales import delete_ledger_transaction
        delete_ledger_transaction(item)
        log_action(request, "Expense Deleted", f"Expense '{desc}' (#{t_id}) was deleted.", icon_class="ri-delete-bin-line", bg_color="danger")
        return JsonResponse({'success': True, 'message': 'Expense deleted successfully.'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@csrf_exempt
def add_customer(request):
    if request.method == "POST":
        from apps.farmer.validation import pincode_error
        error = pincode_error(request)
        if error is not None:
            return error

    from django.http import JsonResponse
    from apps.farmer.models import FarmerCustomer

    if request.method == "POST":
        try:
            profile = get_farmer_profile(request.user)
            action = request.POST.get('action')
            name = (request.POST.get('name') or '').strip()
            company_name = (request.POST.get('company_name') or '').strip()
            contact_number = (request.POST.get('contact_number') or '').strip()
            email = (request.POST.get('email') or '').strip()
            state = (request.POST.get('state') or '').strip()
            district = (request.POST.get('district') or '').strip()
            taluk = (request.POST.get('taluk') or '').strip()
            pincode = (request.POST.get('pincode') or '').strip()
            address = (request.POST.get('address') or '').strip()
            gst_number = (request.POST.get('gst_number') or '').strip()

            # Handle Document Upload
            document_file = request.FILES.get('document_file')
            document_type = request.POST.get('document_type', 'Customer Document')
            doc = None
            if document_file:
                import os
                from apps.superadmin.models import Document
                ext = os.path.splitext(document_file.name)[1].lower()
                doc = Document.objects.create(
                    title=f"Customer Document - {name[:50]}",
                    category=document_type or "Customer Document",
                    description=f"Uploaded document ({document_type}) for customer '{name}'.",
                    file=document_file,
                    file_size_bytes=document_file.size,
                    file_type=ext,
                    uploaded_by=request.user,
                    farmer=profile
                )

            if action == 'update':
                c_id = request.POST.get('customer_id')
                cust = FarmerCustomer.objects.get(id=c_id, farmer=profile)
                cust.name = name
                cust.company_name = company_name
                cust.contact_number = contact_number
                cust.email = email
                cust.state = state
                cust.district = district
                cust.taluk = taluk
                cust.pincode = pincode
                cust.address = address
                cust.gst_number = gst_number
                cust.document_type = document_type
                if doc:
                    cust.document = doc
                cust.save()
                from django.contrib import messages
                messages.success(request, 'Customer updated successfully!')
                log_action(request, "Customer Details Updated", f"Customer '{name}' details updated.", icon_class="ri-user-settings-line", bg_color="info")
            else:
                FarmerCustomer.objects.create(
                    farmer=profile,
                    name=name,
                    company_name=company_name,
                    contact_number=contact_number,
                    email=email,
                    state=state,
                    district=district,
                    taluk=taluk,
                    pincode=pincode,
                    address=address,
                    gst_number=gst_number,
                    document_type=document_type,
                    document=doc
                )
                from django.contrib import messages
                messages.success(request, 'Customer added successfully!')
                log_action(request, "New Customer Registered", f"Customer '{name}' registered.", icon_class="ri-user-add-line", bg_color="success")
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})

@csrf_exempt
def update_transaction_status(request):
    from django.http import JsonResponse
    from apps.farmer.models import LedgerTransaction

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        try:
            t_id = request.POST.get('transaction_id')
            new_status = request.POST.get('status')
            t = LedgerTransaction.objects.get(id=t_id, farmer=farmer_prof)
            t.status = new_status
            t.save()
            log_action(request, "Transaction Status Updated", f"Transaction #{t.id} status updated to '{new_status}'.", icon_class="ri-edit-line", bg_color="info")
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})

@csrf_exempt
def delete_customer(request):
    from django.http import JsonResponse
    from apps.farmer.models import FarmerCustomer

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        try:
            c_id = request.POST.get('customer_id')
            cust = FarmerCustomer.objects.filter(id=c_id, farmer=farmer_prof).first()
            c_name = cust.name if cust else "Customer"
            if cust: cust.delete()
            from django.contrib import messages
            messages.success(request, 'Customer removed successfully!')
            log_action(request, "Customer Deleted", f"Customer '{c_name}' removed.", icon_class="ri-user-unfollow-line", bg_color="danger")
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})

@csrf_exempt
def add_outstanding(request):
    from django.http import JsonResponse
    from apps.farmer.models import FarmerOutstanding

    if request.method == "POST":
        try:
            profile = get_farmer_profile(request.user)
            invoice_no = request.POST.get('invoice_no')
            due_date = request.POST.get('due_date')
            amount_total = request.POST.get('amount_total')
            status = request.POST.get('status', 'Unpaid')

            FarmerOutstanding.objects.create(
                farmer=profile,
                invoice_no=invoice_no,
                due_date=due_date,
                amount_total=amount_total,
                status=status
            )
            from django.contrib import messages
            messages.success(request, 'Outstanding liability recorded!')
            log_action(request, "Outstanding Liability Added", f"Liability Invoice '{invoice_no}' (₹{amount_total}) recorded.", icon_class="ri-bill-line", bg_color="warning")
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})

@csrf_exempt
def delete_outstanding(request):
    from django.http import JsonResponse
    from apps.farmer.models import FarmerOutstanding

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        try:
            o_id = request.POST.get('outstanding_id')
            FarmerOutstanding.objects.filter(id=o_id, farmer=farmer_prof).delete()
            from django.contrib import messages
            messages.success(request, 'Liability cleared successfully!')
            log_action(request, "Outstanding Record Deleted", f"Liability record #{o_id} deleted.", icon_class="ri-delete-bin-line", bg_color="danger")
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})

@csrf_exempt
def pay_outstanding(request):
    from django.http import JsonResponse
    from apps.farmer.models import FarmerOutstanding, LedgerTransaction, Farm

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        try:
            o_id = request.POST.get('outstanding_id')
            outstanding = FarmerOutstanding.objects.get(id=o_id, farmer=farmer_prof)

            # Determine target farm, category, and vendor
            target_farm = getattr(outstanding, 'farm', None)
            target_vendor = getattr(outstanding, 'vendor', None)
            orig_txn = None

            # Look up matching original transaction to inherit farm/category and reconcile balance
            clean_invoice = outstanding.invoice_no or ''
            item_desc = clean_invoice
            if ' - ' in clean_invoice:
                item_desc = clean_invoice.split(' - ', 1)[1].strip()
            elif ':' in clean_invoice:
                item_desc = clean_invoice.split(':', 1)[1].strip()

            matching_txns = LedgerTransaction.objects.filter(farmer=farmer_prof)
            if target_vendor:
                matching_txns = matching_txns.filter(vendor=target_vendor)
            if item_desc:
                orig_txn = matching_txns.filter(description__icontains=item_desc).exclude(category='Liability Payment').order_by('-id').first()
            if not orig_txn and target_vendor:
                orig_txn = matching_txns.exclude(category='Liability Payment').order_by('-id').first()

            if not target_farm and orig_txn and orig_txn.farm:
                target_farm = orig_txn.farm
            if not target_vendor and orig_txn and orig_txn.vendor:
                target_vendor = orig_txn.vendor

            target_category = orig_txn.category if (orig_txn and orig_txn.category) else "Liability Payment"
            target_color = orig_txn.category_color if (orig_txn and orig_txn.category_color) else "#6c757d"

            # Log the cash payment transaction before clearing liability
            payment_amount = outstanding.amount_total - outstanding.amount_paid
            if payment_amount > 0:
                import datetime

                desc = f"Settled Liability: {outstanding.invoice_no}"

                LedgerTransaction.objects.create(
                    farmer=farmer_prof,
                    farm=target_farm,
                    vendor=target_vendor,
                    close_date=datetime.date.today(),
                    description=desc,
                    category=target_category,
                    category_color=target_color,
                    tags="Outflow" if 'Vendor:' in outstanding.invoice_no else "Inflow",
                    status="Cost" if 'Vendor:' in outstanding.invoice_no else "Income",
                    amount=-payment_amount if 'Vendor:' in outstanding.invoice_no else payment_amount
                )

            outstanding.amount_paid = outstanding.amount_total
            outstanding.status = 'Paid'
            if target_farm and not outstanding.farm:
                outstanding.farm = target_farm
            outstanding.save()
            from django.contrib import messages
            messages.success(request, 'Payment successful! Liability cleared.')
            log_action(request, "Liability Settled", f"Outstanding invoice '{outstanding.invoice_no}' settled in full.", icon_class="ri-checkbox-circle-line", bg_color="success")
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})

def vendor(request):
    if request.method == "POST" and request.POST.get("action") in ("add_vendor", "edit_vendor"):
        from apps.farmer.validation import pincode_error
        error = pincode_error(request)
        if error is not None:
            return error

    from apps.farmer.models import FarmerVendor, FarmerOutstanding, LedgerTransaction
    from apps.superadmin.models import DocumentType
    profile = get_farmer_profile(request.user)

    if request.method == "POST":
        action = request.POST.get('action')
        from django.contrib import messages
        from django.shortcuts import redirect
        state_name = ""
        district_name = ""
        taluk_name = ""
        if action in ("add_vendor", "edit_vendor"):
            state_val = request.POST.get('state')
            district_val = request.POST.get('district')
            taluk_val = request.POST.get('taluk')

            from apps.superadmin.models import City, State, Taluk
            if state_val and state_val.isdigit():
                state_obj = State.objects.filter(id=state_val).first()
                state_name = state_obj.name if state_obj else state_val
            else:
                state_name = state_val

            if district_val and district_val.isdigit():
                city_obj = City.objects.filter(id=district_val).first()
                district_name = city_obj.name if city_obj else district_val
            else:
                district_name = district_val

            if taluk_val and taluk_val.isdigit():
                taluk_obj = Taluk.objects.filter(id=taluk_val).first()
                taluk_name = taluk_obj.name if taluk_obj else taluk_val
            else:
                taluk_name = taluk_val

            # Handle Document Upload
            document_file = request.FILES.get('document_file')
            document_type = request.POST.get('document_type', 'Vendor Document')
            doc = None
            if document_file:
                import os
                from apps.superadmin.models import Document
                ext = os.path.splitext(document_file.name)[1].lower()
                vendor_name_val = request.POST.get('vendor_name', 'Vendor')
                doc = Document.objects.create(
                    title=f"Vendor Document - {vendor_name_val[:50]}",
                    category=document_type or "Vendor Document",
                    description=f"Uploaded document ({document_type}) for vendor '{vendor_name_val}'.",
                    file=document_file,
                    file_size_bytes=document_file.size,
                    file_type=ext,
                    uploaded_by=request.user,
                    farmer=profile
                )

            if action == "add_vendor":
                FarmerVendor.objects.create(
                    farmer=profile,
                    vendor_name=request.POST.get('vendor_name'),
                    business_name=request.POST.get('business_name'),
                    shop_name=request.POST.get('shop_name'),
                    mobile_number=request.POST.get('mobile_number'),
                    state=state_name,
                    district=district_name,
                    taluk=taluk_name,
                    pincode=request.POST.get('pincode'),
                    address=request.POST.get('address'),
                    gst_number=request.POST.get('gst_number'),
                    document=doc
                )
                messages.success(request, 'Vendor added successfully!')
                return redirect('farmer-vendor')
            elif action == "edit_vendor":
                vendor_id = request.POST.get('vendor_id')
                try:
                    v = FarmerVendor.objects.get(id=vendor_id, farmer=profile)
                    old_name = v.vendor_name
                    new_name = request.POST.get('vendor_name')
                    v.vendor_name = new_name
                    v.business_name = request.POST.get('business_name')
                    v.shop_name = request.POST.get('shop_name')
                    v.mobile_number = request.POST.get('mobile_number')
                    v.state = state_name
                    v.district = district_name
                    v.taluk = taluk_name
                    v.pincode = request.POST.get('pincode')
                    v.address = request.POST.get('address')
                    v.gst_number = request.POST.get('gst_number')
                    if doc:
                        v.document = doc
                    v.save()

                    # Maintain ID & name consistency across all connected transactions and outstandings
                    if old_name:
                        for o in FarmerOutstanding.objects.filter(farmer=profile):
                            if o.vendor_id == v.id or (old_name and old_name in o.invoice_no) or (new_name and new_name in o.invoice_no):
                                o.vendor = v
                                if old_name and old_name != new_name and old_name in o.invoice_no:
                                    o.invoice_no = o.invoice_no.replace(old_name, new_name)
                                o.save()

                        for t in LedgerTransaction.objects.filter(farmer=profile):
                            if t.vendor_id == v.id or (old_name and old_name in t.description) or (new_name and new_name in t.description):
                                t.vendor = v
                                if old_name and old_name != new_name and old_name in t.description:
                                    t.description = t.description.replace(old_name, new_name)
                                t.save()

                    messages.success(request, 'Vendor updated successfully!')
                except FarmerVendor.DoesNotExist:
                    messages.error(request, 'Vendor not found.')
                return redirect('farmer-vendor')
        elif action == "delete_vendor":
            vendor_id = request.POST.get('vendor_id')
            FarmerVendor.objects.filter(id=vendor_id, farmer=profile).delete()
            messages.success(request, 'Vendor deleted successfully!')
            return redirect('farmer-vendor')

    vendors = FarmerVendor.objects.filter(farmer=profile).select_related('document').order_by('-created_at')

    # Format vendor address & auto-link unlinked Outstandings & Transactions
    from apps.superadmin.models import City, State, Taluk
    for v in vendors:
        addr_parts = []
        if v.address:
            addr_parts.append(v.address.strip())
        if hasattr(v, 'taluk') and v.taluk:
            addr_parts.append(v.taluk.strip())
        if v.district:
            addr_parts.append(v.district.strip())
        if v.state:
            addr_parts.append(v.state.strip())
        if v.pincode:
            addr_parts.append(v.pincode.strip())
        v.formatted_address = ", ".join(addr_parts) if addr_parts else "—"

        # Prepopulate districts & taluks for edit modal
        v.districts = []
        v.taluks = []
        v.state_id = ""
        v.district_id = ""
        if v.state:
            st = State.objects.filter(name__iexact=v.state).first()
            if not st and v.state.isdigit():
                st = State.objects.filter(id=v.state).first()
            if st:
                v.state_id = str(st.id)
                v.districts = list(City.objects.filter(state=st).order_by('name').values('id', 'name'))

        if v.district:
            dt = None
            if v.districts and st:
                dt = City.objects.filter(name__iexact=v.district, state=st).first()
            if not dt:
                dt = City.objects.filter(name__iexact=v.district).first()
            if not dt and v.district.isdigit():
                dt = City.objects.filter(id=v.district).first()
            if dt:
                v.district_id = str(dt.id)
                v.taluks = list(Taluk.objects.filter(city=dt).order_by('name').values('id', 'name'))

        # Match invoice_no containing vendor name or part of name
        parts = [p for p in v.vendor_name.split() if len(p) > 2]
        from django.db.models import Q
        q_outs = Q(invoice_no__icontains=v.vendor_name)
        q_txs = Q(description__icontains=v.vendor_name)
        for p in parts:
            q_outs.add(Q(invoice_no__icontains=p), Q.OR)
            q_txs.add(Q(description__icontains=p), Q.OR)

        FarmerOutstanding.objects.filter(farmer=profile, vendor__isnull=True).filter(q_outs).update(vendor=v)
        LedgerTransaction.objects.filter(farmer=profile, vendor__isnull=True).filter(q_txs).update(vendor=v)

    states = State.objects.filter(country__name__iexact='India').order_by('name')
    if not states.exists():
        states = State.objects.all().order_by('name')

    document_types = DocumentType.objects.filter(is_active=True).order_by('name')

    return render(request, "farmer/vendor.html", {
        "vendors": vendors,
        "states": states,
        "document_types": document_types,
    })

def order(request):
    return render(request, "farmer/order.html")
def inventory(request):
    from apps.farmer.models import Farm, FarmerInventory, FarmerVendor
    from apps.superadmin.models import ExpenseUnit, InventoryCategory, StorageLocation, ProductType
    from django.db.models import F, ExpressionWrapper, DecimalField, Q, Sum
    import json

    profile = get_farmer_profile(request.user)
    inventory_items = FarmerInventory.objects.filter(farmer=profile).order_by('-created_at')
    farms = Farm.objects.filter(farmer=profile).order_by('land_name')
    vendors = FarmerVendor.objects.filter(farmer=profile).order_by('vendor_name')

    # Get categories list from database settings
    if not InventoryCategory.objects.exists():
        InventoryCategory.objects.create(name="Warehouse Stock", layout_type="Warehouse", is_active=True)
        InventoryCategory.objects.create(name="Machinery", layout_type="Machinery", is_active=True)
        InventoryCategory.objects.create(name="Tools", layout_type="Tools", is_active=True)

    db_categories = list(InventoryCategory.objects.filter(is_active=True).order_by('id'))

    dropdown_categories = []
    category_layouts = {}

    for cat in db_categories:
        dropdown_categories.append({
            'name': cat.name,
            'layout_type': cat.layout_type
        })
        category_layouts[cat.name] = cat.layout_type
        if cat.name == 'Warehouse Stock':
            category_layouts['Warehouse'] = cat.layout_type

    category_layouts_json = json.dumps(category_layouts)

    # Determine which categories belong to Machinery / Tools layout
    machinery_tool_cats = list(InventoryCategory.objects.filter(
        layout_type__in=['Machinery', 'Tools', 'Machinery & Tools', 'Vehicles', 'Vehicle'],
        is_active=True
    ).values_list('name', flat=True))

    default_machinery_tool_names = [
        'Vehicles & Fleet', 'Tool & Implements', 'Vehicles', 'Tools', 'Machinery', 
        'Machinery & Tools', 'Equipment', 'Implement', 'Tractor', 'Fleet', 'Tool', 'Vehicle'
    ]
    all_mach_tool_names = list(set(machinery_tool_cats + default_machinery_tool_names))

    # Match vehicles by category (exact or icontains) or rental_available or having license plate
    mach_q = Q(category__in=all_mach_tool_names)
    for name in ['machinery', 'vehicle', 'fleet', 'tool', 'equipment', 'implement', 'tractor']:
        mach_q |= Q(category__icontains=name)
    mach_q |= Q(rental_available=True)
    mach_q |= (Q(license_plate__isnull=False) & ~Q(license_plate=''))

    vehicles = inventory_items.filter(mach_q)
    warehouse = inventory_items.exclude(id__in=vehicles.values_list('id', flat=True))

    low_stock_items = warehouse.filter(quantity__lte=F('safety_stock'), quantity__gt=0).count()
    depleted_items = warehouse.filter(quantity=0).count()
    barn_locations = warehouse.values('location').distinct().count()

    total_val = warehouse.aggregate(total=Sum('total_price'))['total'] or 0
    total_inventory_value = round(float(total_val), 2)

    total_vehicles = vehicles.filter(
        Q(category__icontains='vehicle') | Q(category__icontains='fleet') | Q(category__icontains='machinery') | (Q(license_plate__isnull=False) & ~Q(license_plate=''))
    ).count()
    total_tools = vehicles.exclude(
        Q(category__icontains='vehicle') | Q(category__icontains='fleet') | Q(category__icontains='machinery') | (Q(license_plate__isnull=False) & ~Q(license_plate=''))
    ).count()

    operational_count = vehicles.filter(status__in=['Operational', 'Available']).count()
    maintenance_count = vehicles.filter(status__in=['Maintenance', 'Needs Repair', 'In Maintenance']).count()

    units = ExpenseUnit.objects.filter(is_active=True).order_by('name')
    
    # Seed default locations if none exist
    if not StorageLocation.objects.exists():
        StorageLocation.objects.create(name="Main Warehouse", is_active=True)
        StorageLocation.objects.create(name="Fleet Garage A", is_active=True)
        StorageLocation.objects.create(name="Tool Shed 1", is_active=True)

    locations = list(StorageLocation.objects.filter(is_active=True).order_by('id').values_list('name', flat=True))

    # Seed default product types if none exist
    if not ProductType.objects.exists():
        ProductType.objects.create(name="Seeds", is_active=True)
        ProductType.objects.create(name="Fertilizer", is_active=True)
        ProductType.objects.create(name="Pesticide", is_active=True)
        ProductType.objects.create(name="Fuel", is_active=True)
        ProductType.objects.create(name="Other", is_active=True)

    inventory_types = list(ProductType.objects.filter(is_active=True).order_by('id'))

    # Farms for harvest filter dropdown
    farms = Farm.objects.filter(farmer=profile).order_by('land_name')

    # Harvest Stock tracking
    from apps.farmer.models import CropSplit, CropHarvestLoss
    harvest_splits_qs = CropSplit.objects.filter(
        farm__farmer=profile,
        stage__in=['Harvested', 'Sold', 'Partially Sold', 'Yield', 'Harvesting']
    ).select_related('farm', 'crop').order_by('-updated_at', '-id')

    extra_splits = CropSplit.objects.filter(
        farm__farmer=profile
    ).exclude(
        id__in=harvest_splits_qs.values_list('id', flat=True)
    ).filter(
        Q(yield_tons__gt=0) | ~Q(harvest_batches=[])
    ).select_related('farm', 'crop').order_by('-updated_at', '-id')

    all_raw_splits = list(harvest_splits_qs) + list(extra_splits)

    harvest_stocks = []
    total_harvest_stock_tons = 0.0
    total_harvest_val = 0.0
    total_harvest_loss_tons = 0.0
    total_harvest_loss_val = 0.0

    for s in all_raw_splits:
        c_name = s.crop.name if s.crop else (s.crop_category or s.crop_variety or 'Produce')
        c_variety = s.crop_variety or s.crop_pattern or ''
        f_name = s.farm.land_name if s.farm else 'General Farm'
        f_id = s.farm.id if s.farm else ''
        avail_qty = float(s.yield_tons or 0)
        p_mt = float(s.price_per_mt or 0)
        unit = s.expected_yield_unit or 'Tons'

        # Losses for this split
        split_losses = list(s.losses.all())
        loss_qty = sum(float(l.loss_quantity) for l in split_losses)
        loss_val = sum(float(l.estimated_loss_value) for l in split_losses)

        # Harvest batches initial yield & sold tracking
        batches = s.harvest_batches or []
        from decimal import Decimal

        grade_splits = []
        if batches:
            sold_batch_yield = sum(float(b.get('yield', 0)) for b in batches if b.get('status') == 'Sold')
            ready_batches = [b for b in batches if b.get('status') not in ['Sold', 'Loss / Discard'] and float(b.get('yield', 0)) > 0.0001]
            ready_batch_yield = sum(float(b.get('yield', 0)) for b in ready_batches)

            # Ready batches are already adjusted for losses during record_crop_loss.
            # Thus, ready_batch_yield represents the true remaining available stock.
            avail_qty = max(0.0, ready_batch_yield)
            sold_qty = sold_batch_yield
            # Initial harvest yield equals total produce ever generated: sold + available in stock + loss/waste
            init_yield = round(sold_qty + avail_qty + loss_qty, 2)

            # Auto-sync split.yield_tons in database
            target_yield = Decimal(str(round(avail_qty, 2)))
            if s.yield_tons is None or abs(float(s.yield_tons) - avail_qty) > 0.001:
                s.yield_tons = target_yield
                if s.yield_tons <= Decimal('0.001') and all(b.get('status') == 'Sold' for b in batches):
                    s.stage = 'Sold'
                s.save(update_fields=['yield_tons', 'stage'])

            # Exact valuation from ready batches (prevents rounding loss, e.g. 55*17.27=949.85 vs 950.00)
            est_val = sum(float(b.get('saleValue') or (float(b.get('yield', 0)) * float(b.get('price', 0)))) for b in ready_batches)
            if avail_qty > 0:
                p_mt = round(est_val / avail_qty, 2)
            else:
                p_mt = float(s.price_per_mt or 0)

            # Grade-wise summary
            grade_map = {}
            for b in ready_batches:
                q = (b.get('quality') or 'Grade 1').strip()
                y = float(b.get('yield', 0))
                p = float(b.get('price', 0))
                v = float(b.get('saleValue') or (y * p))
                u = b.get('unit') or unit
                if q not in grade_map:
                    grade_map[q] = {'grade': q, 'yield': 0.0, 'amount': 0.0, 'unit': u, 'price': p}
                grade_map[q]['yield'] += y
                grade_map[q]['amount'] += v
                grade_map[q]['price'] = p

            for q, gdata in grade_map.items():
                if gdata['yield'] > 0:
                    avg_rate = round(gdata['amount'] / gdata['yield'], 2) if gdata['yield'] > 0 else gdata['price']
                    grade_splits.append({
                        'grade': q,
                        'yield': round(gdata['yield'], 2),
                        'price': avg_rate,
                        'amount': round(gdata['amount'], 2),
                        'unit': gdata['unit']
                    })
        else:
            avail_qty = float(s.yield_tons if s.yield_tons is not None else (s.expected_yield or 0))
            init_yield = float(s.expected_yield or avail_qty)
            if init_yield < (avail_qty + loss_qty):
                init_yield = avail_qty + loss_qty
            sold_qty = max(0.0, init_yield - avail_qty - loss_qty)
            est_val = avail_qty * p_mt

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

        harvest_stocks.append({
            'split': s,
            'id': s.id,
            'crop_name': c_name,
            'crop_variety': c_variety,
            'farm_name': f_name,
            'farm_id': f_id,
            'initial_yield': init_yield,
            'sold_qty': sold_qty,
            'loss_qty': loss_qty,
            'loss_val': loss_val,
            'available_qty': avail_qty,
            'unit': unit,
            'price_per_mt': p_mt,
            'est_value': est_val,
            'grade_splits': grade_splits,
            'status': stock_status,
            'status_badge': stock_status_badge,
            'losses': split_losses,
            'batches_count': len(batches),
            'updated_at': s.updated_at,
        })

        total_harvest_stock_tons += avail_qty
        total_harvest_val += est_val
        total_harvest_loss_tons += loss_qty
        total_harvest_loss_val += loss_val

    all_crop_losses = CropHarvestLoss.objects.filter(farmer=profile).select_related('split', 'split__farm', 'split__crop').order_by('-loss_date', '-id')

    context = {
        'vehicles': vehicles,
        'warehouse': warehouse,
        'total_vehicles': total_vehicles,
        'total_tools': total_tools,
        'low_stock_items': low_stock_items,
        'depleted_items': depleted_items,
        'barn_locations': barn_locations,
        'operational_count': operational_count,
        'maintenance_count': maintenance_count,
        'units': units,
        'locations': locations,
        'inventory_types': inventory_types,
        'has_fuel_type': any(t.name.strip().lower() == 'fuel' for t in inventory_types),
        'dropdown_categories': dropdown_categories,
        'category_layouts_json': category_layouts_json,
        'all_inventory_items': inventory_items.order_by('item_name'),
        'total_inventory_value': total_inventory_value,
        'vendors': vendors,
        'farms': farms,
        'harvest_stocks': harvest_stocks,
        'total_harvest_stock_tons': round(total_harvest_stock_tons, 2),
        'total_harvest_val': round(total_harvest_val, 2),
        'total_harvest_loss_tons': round(total_harvest_loss_tons, 2),
        'total_harvest_loss_val': round(total_harvest_loss_val, 2),
        'all_crop_losses': all_crop_losses,
    }
    return render(request, "farmer/inventory.html", context)

def record_crop_loss(request):
    from apps.farmer.models import CropSplit, CropHarvestLoss, FarmActivity
    from django.contrib import messages
    from django.shortcuts import redirect
    from decimal import Decimal
    import datetime

    if request.method == 'POST':
        profile = get_farmer_profile(request.user)
        split_id = request.POST.get('split_id')
        loss_qty_raw = request.POST.get('loss_quantity', '0')
        loss_reason = (request.POST.get('loss_reason') or 'Spoilage / Rot').strip()
        loss_date_raw = request.POST.get('loss_date')
        notes = (request.POST.get('notes') or '').strip()

        try:
            loss_qty = Decimal(str(loss_qty_raw))
        except Exception:
            loss_qty = Decimal('0')

        if loss_qty <= Decimal('0'):
            messages.error(request, "Please enter a valid loss quantity greater than zero.")
            return redirect(request.META.get('HTTP_REFERER', '/farmer/inventory/') + '#harvest_stock')

        try:
            split = CropSplit.objects.get(id=split_id, farm__farmer=profile)
        except CropSplit.DoesNotExist:
            messages.error(request, "Harvest stock record not found.")
            return redirect(request.META.get('HTTP_REFERER', '/farmer/inventory/') + '#harvest_stock')

        available = split.yield_tons or Decimal('0')
        if loss_qty > available:
            messages.error(request, f"Loss quantity ({loss_qty} Tons) cannot exceed available stock ({available} Tons).")
            return redirect(request.META.get('HTTP_REFERER', '/farmer/inventory/') + '#harvest_stock')

        try:
            loss_date = datetime.datetime.strptime(loss_date_raw, '%Y-%m-%d').date() if loss_date_raw else datetime.date.today()
        except Exception:
            loss_date = datetime.date.today()

        price = split.price_per_mt or Decimal('0')
        est_val_raw = request.POST.get('estimated_loss_value')
        try:
            est_val = Decimal(str(est_val_raw)) if est_val_raw else (loss_qty * price)
        except Exception:
            est_val = loss_qty * price

        # Deduct from split yield_tons
        split.yield_tons = max(Decimal('0'), available - loss_qty)
        if split.yield_tons <= Decimal('0.001'):
            split.yield_tons = Decimal('0')
            split.stage = 'Sold'
        if split.price_per_mt:
            split.total_sale_value = split.yield_tons * split.price_per_mt

        # Also deduct from ready batches in harvest_batches if present
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

        # Create CropHarvestLoss record
        CropHarvestLoss.objects.create(
            farmer=profile,
            split=split,
            loss_date=loss_date,
            loss_quantity=loss_qty,
            unit=split.expected_yield_unit or "Tons",
            loss_reason=loss_reason,
            estimated_loss_value=est_val,
            notes=notes
        )

        crop_name = split.crop.name if split.crop else (split.crop_category or 'Produce')
        try:
            FarmActivity.objects.create(
                farm=split.farm,
                activity_type="Crop Loss / Wastage",
                asset_name=f"{crop_name} - {loss_qty} {split.expected_yield_unit or 'Tons'}",
                quantity_metric=f"{loss_qty} {split.expected_yield_unit or 'Tons'}",
                operator_name=request.user.get_full_name() or request.user.username,
                notes=f"Reason: {loss_reason}. {notes}".strip(),
                amount=float(est_val)
            )
        except Exception:
            pass

        messages.success(request, f"Successfully recorded {loss_qty} Tons loss for {crop_name}. Remaining stock: {split.yield_tons} Tons.")
        return redirect(request.META.get('HTTP_REFERER', '/farmer/inventory/') + '#harvest_stock')

    return redirect('/farmer/inventory/#harvest_stock')

def crop_loss_history_page(request):
    from apps.farmer.models import CropHarvestLoss, Farm
    profile = get_farmer_profile(request.user)

    losses_qs = CropHarvestLoss.objects.filter(farmer=profile).select_related('split', 'split__farm', 'split__crop').order_by('-loss_date', '-id')

    total_loss_qty = sum(float(l.loss_quantity) for l in losses_qs)
    total_loss_val = sum(float(l.estimated_loss_value) for l in losses_qs)
    total_incidents = losses_qs.count()

    reasons_count = {}
    for l in losses_qs:
        r = l.loss_reason or 'Other'
        reasons_count[r] = reasons_count.get(r, 0) + 1
    most_common_reason = max(reasons_count, key=reasons_count.get) if reasons_count else "None"

    farms = Farm.objects.filter(farmer=profile).order_by('land_name')

    crop_names = []
    for l in losses_qs:
        cn = l.split.crop.name if l.split and l.split.crop else (l.split.crop_category if l.split else 'Produce')
        if cn and cn not in crop_names:
            crop_names.append(cn)
    crop_names.sort()

    pre_crop = request.GET.get('crop', '').strip()
    pre_farm = request.GET.get('farm', '').strip()

    context = {
        'all_crop_losses': losses_qs,
        'total_loss_qty': round(total_loss_qty, 2),
        'total_loss_val': round(total_loss_val, 2),
        'total_incidents': total_incidents,
        'most_common_reason': most_common_reason,
        'farms': farms,
        'crop_names': crop_names,
        'pre_crop': pre_crop,
        'pre_farm': pre_farm,
    }
    return render(request, "farmer/crop_loss_history.html", context)

def crop_loss_history_api(request):
    from apps.farmer.models import CropHarvestLoss
    from django.http import JsonResponse
    profile = get_farmer_profile(request.user)
    split_id = request.GET.get('split_id')
    qs = CropHarvestLoss.objects.filter(farmer=profile)
    if split_id:
        qs = qs.filter(split_id=split_id)
    losses_data = []
    for l in qs.select_related('split', 'split__farm', 'split__crop').order_by('-loss_date', '-id'):
        crop_title = l.split.crop.name if l.split.crop else (l.split.crop_category or 'Produce')
        losses_data.append({
            'id': l.id,
            'date': l.loss_date.strftime('%Y-%m-%d'),
            'farm': l.split.farm.land_name if l.split.farm else 'General Farm',
            'crop': crop_title,
            'quantity': float(l.loss_quantity),
            'unit': l.unit,
            'reason': l.loss_reason,
            'estimated_value': float(l.estimated_loss_value),
            'notes': l.notes or ''
        })
    return JsonResponse({'success': True, 'losses': losses_data})

def add_stock_log(request):
    from django.http import HttpResponseRedirect
    from django.contrib import messages
    from apps.farmer.models import FarmerInventory
    if request.method == 'POST':
        profile = get_farmer_profile(request.user)
        item_id = request.POST.get('item_id')
        action = request.POST.get('action')  # 'add', 'remove', 'adjust'
        quantity = request.POST.get('quantity', 0)
        notes = request.POST.get('notes', '').strip()
        log_date = request.POST.get('log_date', '')
        try:
            qty = float(quantity)
        except (ValueError, TypeError):
            qty = 0
        try:
            item = FarmerInventory.objects.get(id=item_id, farmer=profile)
            old_qty = float(item.quantity or 0)
            if action == 'add':
                item.quantity = old_qty + qty
            elif action == 'remove':
                if qty > old_qty:
                    messages.error(request, f"Cannot deduct {qty} {item.unit}. Only {old_qty} {item.unit} available in stock.")
                    return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/farmer/inventory/'))
                item.quantity = old_qty - qty
            elif action == 'adjust':
                item.quantity = qty
            new_qty = float(item.quantity)
            item.save()
            # Save the log entry
            from apps.farmer.models import InventoryStockLog
            import datetime
            InventoryStockLog.objects.create(
                inventory=item,
                action=action if action in ['add','remove'] else 'add',
                quantity=qty,
                notes=notes,
                log_date=log_date or datetime.date.today(),
                qty_before=old_qty,
                qty_after=new_qty,
            )
            messages.success(request, f"Stock log saved: {item.item_name} updated to {item.quantity} {item.unit}.")
        except FarmerInventory.DoesNotExist:
            messages.error(request, "Inventory item not found.")
    return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/farmer/inventory/'))


def inventory_stock_logs_api(request, item_id):
    from django.http import JsonResponse
    from django.db.models import Q
    from apps.farmer.models import FarmerInventory, InventoryStockLog, FarmActivity
    profile = get_farmer_profile(request.user)
    try:
        item = FarmerInventory.objects.get(id=item_id, farmer=profile)
    except FarmerInventory.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    logs = InventoryStockLog.objects.filter(inventory=item).select_related('farm', 'crop_split', 'crop_split__crop').order_by('-log_date', '-created_at')[:100]
    data = []
    seen_log_keys = set()
    for log in logs:
        farm_name = log.farm.land_name if log.farm else ''
        crop_name = (log.crop_split.crop.name if log.crop_split and log.crop_split.crop else '') or ''

        if (not farm_name or not crop_name) and log.notes:
            try:
                parts = log.notes.split('|')
                for p in parts:
                    if not farm_name and 'Farm:' in p:
                        farm_name = p.split('Farm:')[1].strip()
                    elif not crop_name and 'Crop:' in p:
                        crop_name = p.split('Crop:')[1].strip()
            except Exception:
                pass

        is_cons = bool(farm_name or crop_name or (log.notes and ('Farm' in log.notes or 'Consumption' in log.notes)))
        data.append({
            'id': log.id,
            'action': log.action,
            'quantity': log.quantity,
            'unit': item.unit,
            'notes': log.notes or '',
            'farm_name': farm_name,
            'crop_name': crop_name,
            'is_consumption': is_cons,
            'log_date': log.log_date.strftime('%d %b %Y') if log.log_date else '',
            'qty_before': log.qty_before,
            'qty_after': log.qty_after,
        })
        if log.log_date:
            seen_log_keys.add((log.log_date, log.quantity))

    # Also include any past FarmActivity recorded for this item
    past_activities = FarmActivity.objects.filter(
        farm__farmer=profile,
        activity_type='Resource Consumption'
    ).filter(
        Q(asset_name__iexact=item.item_name) | Q(notes__icontains=f"inv-{item.id}")
    ).select_related('farm').order_by('-created_at')[:30]

    for act in past_activities:
        act_date = act.created_at.date() if act.created_at else None
        q_val = 0.0
        try:
            q_val = float(act.quantity_metric.split()[0])
        except Exception:
            q_val = 0.0
        if (act_date, q_val) not in seen_log_keys and q_val > 0:
            c_name = act.farm.crop.name if act.farm and act.farm.crop else ""
            if "CropSplit #" in (act.notes or ''):
                try:
                    c_name = act.notes.split('(')[1].split(')')[0].strip()
                except Exception:
                    pass
            data.append({
                'id': f"act_{act.id}",
                'action': 'remove',
                'quantity': q_val,
                'unit': item.unit,
                'notes': act.notes or '',
                'farm_name': act.farm.land_name if act.farm else '',
                'crop_name': c_name,
                'is_consumption': True,
                'log_date': act_date.strftime('%d %b %Y') if act_date else '',
                'qty_before': '—',
                'qty_after': '—',
            })

    return JsonResponse({'item_name': item.item_name, 'unit': item.unit, 'current_qty': float(item.quantity), 'logs': data})

def inventory_out_logs(request):
    from apps.farmer.models import InventoryStockLog
    from django.shortcuts import render
    profile = get_farmer_profile(request.user)
    
    # Get all logs with action = 'remove' belonging to this farmer's inventory items
    out_logs = InventoryStockLog.objects.filter(
        inventory__farmer=profile, 
        action='remove'
    ).select_related('inventory').order_by('-created_at')
    
    context = {
        'out_logs': out_logs,
        'active_menu': 'inventory'
    }
    return render(request, 'farmer/inventory_out_logs.html', context)

def return_out_logs_api(request):
    from django.http import JsonResponse
    from apps.farmer.models import InventoryStockLog
    import json
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'})
        
    profile = get_farmer_profile(request.user)
    try:
        data = json.loads(request.body)
        log_ids = data.get('log_ids', [])
    except Exception:
        return JsonResponse({'success': False, 'message': 'Invalid JSON data.'})
        
    if not log_ids:
        return JsonResponse({'success': False, 'message': 'No logs selected.'})
        
    # Process each log
    success_count = 0
    logs = InventoryStockLog.objects.filter(id__in=log_ids, inventory__farmer=profile, action='remove')
    for log in logs:
        inventory = log.inventory
        old_qty = float(inventory.quantity or 0)
        qty_to_add = float(log.quantity or 0)
        
        # Update inventory quantity
        inventory.quantity = old_qty + qty_to_add
        inventory.save()
        
        # Update log to show it was marked 'In' (Returned)
        log.action = 'add'
        log.notes = (log.notes or "") + " (Returned to Stock)"
        log.qty_before = old_qty
        log.qty_after = inventory.quantity
        log.save()
        success_count += 1
        
    return JsonResponse({
        'success': True, 
        'message': f'Successfully returned {success_count} item(s) back to stock.'
    })

def add_inventory(request):
    from apps.farmer.models import Farm, FarmerInventory, FarmerVendor, LedgerTransaction
    if request.method == "POST":
        profile = get_farmer_profile(request.user)
        item_name = request.POST.get('item_name')
        category = request.POST.get('category')
        quantity = request.POST.get('quantity', 1)
        unit = request.POST.get('unit', 'Units')
        safety_stock = request.POST.get('safety_stock', 5)
        location = request.POST.get('location', 'Main Warehouse')
        status = request.POST.get('status', 'In Stock')
        license_plate = request.POST.get('license_plate', '')
        
        last_service_date = request.POST.get('last_service_date') or None
        next_service_date = request.POST.get('next_service_date') or None
        fuel_type = request.POST.get('fuel_type', '')
        
        insurance_file = request.FILES.get('insurance_file')
        insurance_expiry = request.POST.get('insurance_expiry') or None
        
        rc_file = request.FILES.get('rc_file')
        rc_expiry = request.POST.get('rc_expiry') or None
        
        pollution_file = request.FILES.get('pollution_file')
        pollution_expiry = request.POST.get('pollution_expiry') or None

        unit_price = request.POST.get('unit_price', 0) or 0
        rental_available = request.POST.get('rental_available') == 'on' or request.POST.get('rental_available') in ['true', '1', True]
        rental_price = request.POST.get('rental_price', 0) or 0
        rental_unit = request.POST.get('rental_unit', 'Per Day')
        purchase_date = request.POST.get('purchase_date') or None
        description = request.POST.get('description', '')

        # Vendor handling
        vendor_id = request.POST.get('vendor_id')
        vendor_instance = None
        if vendor_id:
            vendor_instance = FarmerVendor.objects.filter(id=vendor_id, farmer=profile).first()

        farm_id = request.POST.get('farm_id')
        farm_instance = Farm.objects.filter(id=farm_id, farmer=profile).first() if farm_id else None

        total_p = 0.0
        try:
            qty_f = float(quantity or 1)
            up_f = float(unit_price or 0)
            total_p = round(qty_f * up_f, 2)
        except Exception:
            total_p = 0.0
        if total_p == 0 and unit_price:
            try:
                total_p = float(unit_price)
            except Exception:
                pass

        item = FarmerInventory.objects.create(
            farmer=profile,
            farm=farm_instance,
            vendor=vendor_instance,
            item_name=item_name,
            category=category,
            quantity=quantity,
            unit=unit,
            safety_stock=safety_stock,
            location=location,
            status=status,
            license_plate=license_plate,
            last_service_date=last_service_date,
            next_service_date=next_service_date,
            fuel_type=fuel_type,
            insurance_file=insurance_file,
            insurance_expiry=insurance_expiry,
            rc_file=rc_file,
            rc_expiry=rc_expiry,
            pollution_file=pollution_file,
            pollution_expiry=pollution_expiry,
            unit_price=unit_price,
            total_price=total_p,
            rental_available=rental_available,
            rental_price=rental_price,
            rental_unit=rental_unit,
            purchase_date=purchase_date,
            description=description
        )
        log_action(request, "Inventory Added", f"Inventory item '{item_name}' ({quantity} {unit}) created.", icon_class="ri-archive-line", bg_color="success")

        # Record in Expenses if requested
        record_as_expense = request.POST.get('record_as_expense') in ['on', 'true', '1', True]
        if record_as_expense:
            try:
                qty_f = float(quantity or 1)
                up_f = float(unit_price or 0)
                total_cost = qty_f * up_f
            except (ValueError, TypeError):
                total_cost = 0.0

            if total_cost > 0:
                from django.utils import timezone
                import datetime
                tx_date = timezone.now().date()
                if purchase_date:
                    try:
                        tx_date = datetime.datetime.strptime(str(purchase_date), "%Y-%m-%d").date()
                    except Exception:
                        tx_date = timezone.now().date()

                v_info = f" from {vendor_instance.vendor_name}" if vendor_instance else ""
                LedgerTransaction.objects.create(
                    farmer=profile,
                    farm=farm_instance,
                    vendor=vendor_instance,
                    close_date=tx_date,
                    description=f"Inventory Purchase - {item_name} ({quantity} {unit}){v_info}",
                    payment_method="Cash",
                    category=category or "Inventory",
                    category_color="#f1732a",
                    tags=f"Outflow | Inventory ID: {item.id} | Inventory: {item_name}",
                    status="Cost",
                    amount=-abs(total_cost),
                    balance_due=0,
                    quantity=quantity,
                    unit=unit
                )
                from django.contrib import messages
                messages.success(request, f"Item added and ₹{total_cost:,.2f} recorded under Expenses!")
    return redirect('farmer-inventory')

@csrf_exempt
def update_inventory(request):
    from django.contrib import messages
    from django.http import JsonResponse
    from apps.farmer.models import Farm, FarmerInventory, LedgerTransaction

    if request.method == "POST":
        profile = get_farmer_profile(request.user)
        item_id = request.POST.get('item_id')
        if item_id and profile:
            try:
                item = FarmerInventory.objects.get(id=item_id, farmer=profile)
                original_item_name = item.item_name
                item.item_name = request.POST.get('item_name', item.item_name)
                item.category = request.POST.get('category', item.category)
                farm_id = request.POST.get('farm_id')
                if farm_id is not None:
                    item.farm = Farm.objects.filter(id=farm_id, farmer=profile).first() if farm_id else None
                if request.POST.get('quantity') is not None and request.POST.get('quantity') != '':
                    item.quantity = float(request.POST.get('quantity'))
                if request.POST.get('unit'):
                    item.unit = request.POST.get('unit')
                if request.POST.get('safety_stock') is not None and request.POST.get('safety_stock') != '':
                    item.safety_stock = int(request.POST.get('safety_stock'))
                if request.POST.get('location'):
                    item.location = request.POST.get('location')
                if request.POST.get('status'):
                    item.status = request.POST.get('status')
                if 'description' in request.POST:
                    item.description = request.POST.get('description')

                license_plate = request.POST.get('license_plate')
                if license_plate is not None:
                    item.license_plate = license_plate

                serv_date = request.POST.get('last_service_date')
                if serv_date:
                    item.last_service_date = serv_date
                elif serv_date == '':
                    item.last_service_date = None

                next_serv_date = request.POST.get('next_service_date')
                if next_serv_date:
                    item.next_service_date = next_serv_date
                elif next_serv_date == '':
                    item.next_service_date = None

                purch_date = request.POST.get('purchase_date')
                if purch_date:
                    item.purchase_date = purch_date
                elif purch_date == '':
                    item.purchase_date = None

                fuel = request.POST.get('fuel_type')
                if fuel is not None:
                    item.fuel_type = fuel

                price_val = request.POST.get('unit_price')
                if price_val is not None and price_val != '':
                    item.unit_price = price_val

                # The list and summary cards use the persisted total_price,
                # while the edit modal only calculates it for display. Keep
                # the stored value in sync whenever quantity or price changes.
                item.total_price = round(
                    Decimal(str(item.quantity or 0)) * Decimal(str(item.unit_price or 0)),
                    2,
                )

                if 'rental_available' in request.POST:
                    item.rental_available = request.POST.get('rental_available') in ['on', 'true', '1', True]
                elif item.category in ['Vehicles & Fleet', 'Machinery', 'Tools', 'Tool & Implements']:
                    item.rental_available = False

                rent_p = request.POST.get('rental_price')
                if rent_p is not None and rent_p != '':
                    item.rental_price = rent_p

                rent_u = request.POST.get('rental_unit')
                if rent_u is not None:
                    item.rental_unit = rent_u

                # Handle files and file expiries
                if 'insurance_file' in request.FILES:
                    item.insurance_file = request.FILES['insurance_file']
                ins_exp = request.POST.get('insurance_expiry')
                if ins_exp:
                    item.insurance_expiry = ins_exp
                elif ins_exp == '':
                    item.insurance_expiry = None

                if 'rc_file' in request.FILES:
                    item.rc_file = request.FILES['rc_file']
                rc_exp = request.POST.get('rc_expiry')
                if rc_exp:
                    item.rc_expiry = rc_exp
                elif rc_exp == '':
                    item.rc_expiry = None

                if 'pollution_file' in request.FILES:
                    item.pollution_file = request.FILES['pollution_file']
                poll_exp = request.POST.get('pollution_expiry')
                if poll_exp:
                    item.pollution_expiry = poll_exp
                vendor_id = request.POST.get('vendor_id')
                if vendor_id is not None:
                    if vendor_id in ['', '0', 'none']:
                        item.vendor = None
                    else:
                        from apps.farmer.models import FarmerVendor
                        item.vendor = FarmerVendor.objects.filter(id=vendor_id, farmer=profile).first()

                item.save()
                # Keep the automatic purchase expense on the same farm as its
                # inventory item.  The ID tag ensures that only this item's
                # generated expense is changed, never an unrelated expense.
                linked_expenses = LedgerTransaction.objects.filter(
                    farmer=profile,
                    tags__contains=f"Inventory ID: {item.id}",
                )
                if linked_expenses.exists():
                    linked_expenses.update(farm=item.farm)
                else:
                    # Entries created before the inventory ID tag was added may
                    # be linked only when there is exactly one unambiguous match.
                    # This avoids assigning a farm to a different repeat purchase.
                    legacy_expenses = LedgerTransaction.objects.filter(
                        farmer=profile,
                        farm__isnull=True,
                        tags=f"Outflow | Inventory: {original_item_name}",
                        description__startswith=f"Inventory Purchase - {original_item_name} (",
                    )
                    if legacy_expenses.count() == 1:
                        legacy_expenses.update(farm=item.farm)
                log_action(request, "Inventory Updated", f"Inventory item '{item.item_name}' was updated.", icon_class="ri-edit-line", bg_color="info")
                if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('is_ajax') == '1':
                    return JsonResponse({'success': True, 'message': 'Item updated successfully!'})
                messages.success(request, f"Item '{item.item_name}' updated successfully!")
                return redirect('farmer-inventory')
            except FarmerInventory.DoesNotExist:
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({'success': False, 'error': 'Item not found.'})
                messages.error(request, "Item not found.")
                return redirect('farmer-inventory')
    return redirect('farmer-inventory')
    return redirect('farmer-inventory')

@csrf_exempt
def delete_inventory(request):
    from django.contrib import messages
    from django.http import JsonResponse
    from apps.farmer.models import FarmerInventory

    if request.method == "POST":
        profile = get_farmer_profile(request.user)
        item_id = request.POST.get('item_id')
        if item_id and profile:
            try:
                item = FarmerInventory.objects.get(id=item_id, farmer=profile)
                name = item.item_name
                item.delete()
                log_action(request, "Inventory Deleted", f"Inventory item '{name}' was deleted.", icon_class="ri-delete-bin-line", bg_color="danger")
                if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('is_ajax') == '1':
                    return JsonResponse({'success': True, 'message': 'Item deleted successfully!'})
                messages.success(request, f"Item '{name}' deleted successfully!")
                return redirect('farmer-inventory')
            except FarmerInventory.DoesNotExist:
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({'success': False, 'error': 'Item not found.'})
                messages.error(request, "Item not found.")
    return redirect('farmer-inventory')

@csrf_exempt
def update_inventory_status(request):
    from django.http import JsonResponse
    from apps.farmer.models import FarmerInventory

    if request.method == "POST":
        profile = get_farmer_profile(request.user)
        item_id = request.POST.get('item_id')
        status = request.POST.get('status')
        if item_id and status and profile:
            try:
                item = FarmerInventory.objects.get(id=item_id, farmer=profile)
                item.status = status
                item.save()
                log_action(request, "Inventory Status Changed", f"Item '{item.item_name}' status changed to '{status}'.", icon_class="ri-refresh-line", bg_color="info")
                return JsonResponse({'success': True, 'status': status, 'message': f"Status updated to '{status}'"})
            except FarmerInventory.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Item not found.'})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@csrf_exempt
def toggle_inventory_rental(request):
    from django.http import JsonResponse
    from apps.farmer.models import FarmerInventory

    if request.method == "POST":
        profile = get_farmer_profile(request.user)
        item_id = request.POST.get('item_id')
        rental_available = request.POST.get('rental_available') in ['true', '1', True, 'on']
        rental_price = request.POST.get('rental_price')
        rental_unit = request.POST.get('rental_unit')

        if item_id and profile:
            try:
                item = FarmerInventory.objects.get(id=item_id, farmer=profile)
                item.rental_available = rental_available

                if rental_available:
                    if rental_price:
                        try:
                            item.rental_price = float(rental_price)
                        except (ValueError, TypeError):
                            pass
                    if rental_unit:
                        item.rental_unit = str(rental_unit).strip()
                    if item.status not in ['Available', 'Booked', 'Rented', 'Maintenance']:
                        item.status = 'Available'
                else:
                    if item.status in ['Available', 'Booked', 'Rented']:
                        item.status = 'Operational'

                item.save()
                rate_text = f" at ₹{item.rental_price} / {item.rental_unit}" if rental_available and item.rental_price else ""
                log_action(request, "Rental Status Changed", f"Item '{item.item_name}' rental availability changed to {'On' if rental_available else 'Off'}{rate_text}.", icon_class="ri-toggle-line", bg_color="info")
                return JsonResponse({
                    'success': True,
                    'rental_available': item.rental_available,
                    'rental_price': str(item.rental_price) if item.rental_price else '',
                    'rental_unit': item.rental_unit or 'Per Day',
                    'status': item.status,
                    'message': f"Rental {'enabled' if rental_available else 'disabled'} successfully"
                })
            except FarmerInventory.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Item not found.'})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})
@csrf_exempt
def quick_add_inventory_category(request):
    from apps.superadmin.models import InventoryCategory
    from django.http import JsonResponse
    if request.method == "POST":
        name = request.POST.get('name', '').strip()
        layout_type = request.POST.get('layout_type', 'Warehouse').strip()
        if name:
            cat, created = get_or_reactivate_master(InventoryCategory, name=name, defaults={'layout_type': layout_type, 'is_active': True})
            return JsonResponse({'success': True, 'name': cat.name, 'layout_type': cat.layout_type})
        return JsonResponse({'success': False, 'error': 'Category name is required.'})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})

@csrf_exempt
def quick_add_storage_location(request):
    from apps.superadmin.models import StorageLocation
    from django.http import JsonResponse
    if request.method == "POST":
        name = request.POST.get('name', '').strip()
        if name:
            loc, created = get_or_reactivate_master(StorageLocation, name=name, defaults={'is_active': True})
            return JsonResponse({'success': True, 'name': loc.name})
        return JsonResponse({'success': False, 'error': 'Location name is required.'})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})

@csrf_exempt
def quick_add_inventory_type(request):
    from apps.superadmin.models import ProductType
    from django.http import JsonResponse
    if request.method == "POST":
        name = request.POST.get('name', '').strip()
        if name:
            pt, created = get_or_reactivate_master(ProductType, name=name, defaults={'is_active': True})
            return JsonResponse({'success': True, 'name': pt.name})
        return JsonResponse({'success': False, 'error': 'Inventory type name is required.'})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})

def manage_employee(request):
    from collections import defaultdict
    from apps.farmer.models import FarmerStaff, StaffAdvance, StaffAttendance, StaffPayroll

    # Fetch staff
    farmer_prof = get_farmer_profile(request.user)
    staff_members = FarmerStaff.objects.filter(farmer=farmer_prof).select_related('designation')

    # 1. Daily Attendance Data (Selected Month or Current Month)
    month_param = request.GET.get('month')
    year_param = request.GET.get('year')
    today = date.today()

    if month_param and year_param:
        try:
            target_date = date(int(year_param), int(month_param), 1)
        except ValueError:
            target_date = today.replace(day=1)
    else:
        target_date = today.replace(day=1)

    # Prevent future months
    target_date = min(target_date, today.replace(day=1))

    is_latest_month = target_date.year == today.year and target_date.month == today.month

    if target_date.month == 1:
        prev_month = 12
        prev_year = target_date.year - 1
    else:
        prev_month = target_date.month - 1
        prev_year = target_date.year

    if target_date.month == 12:
        next_month = 1
        next_year = target_date.year + 1
    else:
        next_month = target_date.month + 1
        next_year = target_date.year

    current_month_name = target_date.strftime("%b").upper()

    import calendar
    _, last_day = calendar.monthrange(target_date.year, target_date.month)

    days = []
    for i in range(last_day):
        d = target_date + timedelta(days=i)
        days.append({
            "label": d.strftime("%b %d"),
            "key": d.strftime("%Y-%m-%d"),
            "date_obj": d,
            "is_future": d > today,
            "is_today": d == today,
            "is_sunday": d.weekday() == 6,
        })

    # Order staff members: Active (status='0') first, Suspended (status='1') at the bottom
    ordered_staff_members = staff_members.order_by('status', 'name')

    attend_employees = []
    for staff in ordered_staff_members:
        is_suspended = (str(staff.status) == "1")
        attendances = []
        for d in days:
            att = StaffAttendance.objects.filter(staff=staff, date=d['date_obj']).first()
            is_before_joining = bool(staff.date_of_joining and d['date_obj'] < staff.date_of_joining)
            is_disabled = d["is_future"] or is_suspended or is_before_joining
            if att:
                attendances.append({
                    "status": att.status,
                    "date": d['key'],
                    "is_future": d["is_future"],
                    "is_today": d["is_today"],
                    "is_before_joining": is_before_joining,
                    "is_disabled": is_disabled
                })
            else:
                attendances.append({
                    "status": "-",
                    "date": d['key'],
                    "is_future": d["is_future"],
                    "is_today": d["is_today"],
                    "is_before_joining": is_before_joining,
                    "is_disabled": is_disabled
                })
        role_display = "Role Manager" if staff.is_role_manager else (staff.role if staff.role else "Employee")
        attend_employees.append({
            "id": staff.id,
            "name": staff.name,
            "role": role_display,
            "raw_role": staff.role or "Employee",
            "is_role_manager": staff.is_role_manager,
            "designation": staff.designation.name if staff.designation else "",
            "status": staff.status,
            "is_suspended": is_suspended,
            "date_of_joining": staff.date_of_joining.strftime("%Y-%m-%d") if staff.date_of_joining else "",
            "attendance": attendances
        })

    # Collect distinct roles for filtering
    staff_roles = []
    if any(s.is_role_manager or s.role == "Manager" for s in staff_members):
        staff_roles.append("Role Manager")
    if any(not s.is_role_manager and s.role != "Manager" for s in staff_members):
        staff_roles.append("Employee")
    for s in staff_members:
        r = s.role
        if r and r not in ["Manager", "Employee", "Role Manager"] and r not in staff_roles:
            staff_roles.append(r)

    # 2. Current Payroll Run Sheet
    current_month_str = date.today().strftime("%B %Y").upper()
    payroll_run_sheet = []

    for staff in ordered_staff_members:
        is_suspended = (str(staff.status) == "1")
        last_payroll = StaffPayroll.objects.filter(staff=staff).order_by('-paid_on').first()

        # Only pull unpaid attendances and advances
        curr_month_att = StaffAttendance.objects.filter(staff=staff, is_paid=False)

        present_days = curr_month_att.filter(status='P').count()
        half_days = curr_month_att.filter(status='H').count()
        absent_days_count = curr_month_att.filter(status='A').count()

        # H is half day, let's treat it as 0.5 present and 0.5 absent
        total_worked = present_days + (half_days * 0.5)
        absent_days = absent_days_count + (half_days * 0.5)

        daily_rate = float(staff.base_daily_rate or 0)
        gross = round(daily_rate * total_worked, 2)

        advances = StaffAdvance.objects.filter(staff=staff, is_paid=False)
        total_advance = round(float(sum(a.amount for a in advances)), 2)

        net_pay = max(0.0, round(gross - total_advance, 2))

        # Only lock the button if there is nothing new to pay or if suspended
        already_paid = is_suspended or (total_worked == 0 and last_payroll and last_payroll.month == current_month_str)

        payroll_run_sheet.append({
            "id": staff.id,
            "name": staff.name,
            "role": staff.role,
            "status": staff.status,
            "is_suspended": is_suspended,
            "base_rate": round(float(staff.salary), 2) if staff.salary is not None else round(daily_rate, 2),
            "salary_frequency": staff.salary_frequency if staff.salary_frequency else "Daily",
            "present_days": total_worked,
            "absent_days": absent_days,
            "advance": total_advance,
            "gross": gross,
            "net_pay": net_pay,
            "already_paid": already_paid
        })

    # 3. Advances list
    advances_list = StaffAdvance.objects.filter(staff__farmer=farmer_prof).select_related('staff').order_by('-date', '-id')
    total_advance_amount = sum(float(a.amount or 0) for a in advances_list)
    pending_advances = [a for a in advances_list if not a.is_paid]
    recovered_advances = [a for a in advances_list if a.is_paid]
    total_advance_pending = sum(float(a.amount or 0) for a in pending_advances)
    total_advance_recovered = sum(float(a.amount or 0) for a in recovered_advances)
    active_advance_staff_count = len(set(a.staff_id for a in pending_advances))

    # 4. Historic Payroll
    historic_payrolls = StaffPayroll.objects.filter(staff__farmer=farmer_prof).order_by('-paid_on')
    grouped_payrolls = defaultdict(list)
    for p in historic_payrolls:
        grouped_payrolls[p.month].append({
            "name": p.staff.name,
            "method": p.payment_method,
            "date": p.paid_on.strftime("%Y-%m-%d"),
            "advance": p.advance_deduction,
            "incentive": p.incentives,
            "net": p.net_pay
        })
    payroll_data = [{"name": month, "transactions": trans, "id": month.replace(' ', '-').lower()} for month, trans in grouped_payrolls.items()]
    historic_payrolls_count = historic_payrolls.count()

    from apps.farmer.access import available_manager_permissions
    permissions = available_manager_permissions(farmer_prof)

    total_staff = staff_members.count()
    active_staff = staff_members.filter(status="0").count()
    suspended_staff = staff_members.filter(status="1").count()
    admins_staff = staff_members.filter(role__icontains="Manager").count() + staff_members.filter(role__icontains="Admin").count()

    from apps.superadmin.models import Designation, DocumentType
    designations = Designation.objects.filter(is_active=True).order_by('name')
    document_types = DocumentType.objects.filter(is_active=True).order_by('name')

    active_tab = request.GET.get('tab', 'users')
    if active_tab not in ['users', 'daily_attendance', 'paycheck_payroll', 'employee_advance']:
        active_tab = 'users'

    return render(request, "farmer/employee_list.html", {
        "active_tab": active_tab,
        "staff_members": staff_members,
        "attend_employees": attend_employees,
        "days": days,
        "payroll_run_sheet": payroll_run_sheet,
        "advances_list": advances_list,
        "total_advance_amount": round(total_advance_amount, 2),
        "total_advance_pending": round(total_advance_pending, 2),
        "total_advance_recovered": round(total_advance_recovered, 2),
        "active_advance_staff_count": active_advance_staff_count,
        "payroll_data": payroll_data,
        "historic_payrolls_count": historic_payrolls_count,
        "current_month_str": current_month_str,
        "selected_month": target_date.month,
        "selected_year": target_date.year,
        "prev_month": prev_month,
        "prev_year": prev_year,
        "next_month": next_month,
        "next_year": next_year,
        "current_month_name": current_month_name,
        "is_latest_month": is_latest_month,
        "permissions": permissions,
        "total_staff": total_staff,
        "active_staff": active_staff,
        "suspended_staff": suspended_staff,
        "admins_staff": admins_staff,
        "today_str": today.strftime("%Y-%m-%d"),
        "designations": designations,
        "document_types": document_types,
        "staff_roles": staff_roles,
    })

from django.views.decorators.csrf import csrf_exempt

@csrf_exempt
def update_attendance(request):
    from datetime import date, datetime
    from apps.farmer.models import FarmerStaff, StaffAttendance

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        emp_id = request.POST.get('employee_id')
        att_date = request.POST.get('date')
        status = request.POST.get('status')
        if emp_id and farmer_prof:
            try:
                parsed_date = datetime.strptime(att_date, "%Y-%m-%d").date()
                if parsed_date > date.today():
                    return JsonResponse({"success": False, "error": "Cannot mark attendance for future dates."})

                staff = FarmerStaff.objects.get(id=emp_id, farmer=farmer_prof)
                if str(staff.status) == "1":
                    return JsonResponse({"success": False, "error": "Cannot mark attendance for suspended/locked staff."})

                if staff.date_of_joining and parsed_date < staff.date_of_joining:
                    return JsonResponse({
                        "success": False,
                        "error": f"Cannot mark attendance before joining date ({staff.date_of_joining.strftime('%d-%m-%Y')})."
                    })

                if status == "-":
                    StaffAttendance.objects.filter(staff=staff, date=att_date).delete()
                else:
                    attendances = StaffAttendance.objects.filter(staff=staff, date=att_date)
                    if attendances.exists():
                        att = attendances.first()
                        if attendances.count() > 1:
                            attendances.exclude(id=att.id).delete()
                        att.status = status
                        att.save()
                    else:
                        StaffAttendance.objects.create(staff=staff, date=att_date, status=status)

                log_action(request, "Staff Attendance Marked", f"Attendance for '{staff.name}' on {att_date} marked as '{status}'.", icon_class="ri-calendar-check-line", bg_color="info")
                return JsonResponse({"success": True})
            except Exception as e:
                return JsonResponse({"success": False, "error": str(e)})
    return JsonResponse({"success": False})

@csrf_exempt
def bulk_update_attendance(request):
    from datetime import date, datetime
    from apps.farmer.models import FarmerStaff, StaffAttendance
    import json

    farmer_prof = get_farmer_profile(request.user)
    if request.method == "POST" and farmer_prof:
        att_date = request.POST.get('date')
        status = request.POST.get('status', 'P')
        staff_ids_json = request.POST.get('staff_ids')

        if not att_date:
            return JsonResponse({"success": False, "error": "Date is required."})

        try:
            parsed_date = datetime.strptime(att_date, "%Y-%m-%d").date()
            if parsed_date > date.today():
                return JsonResponse({"success": False, "error": "Cannot mark attendance for future dates."})

            active_staff = FarmerStaff.objects.filter(farmer=farmer_prof, status="0")
            if staff_ids_json:
                try:
                    s_ids = json.loads(staff_ids_json)
                    if isinstance(s_ids, list):
                        active_staff = active_staff.filter(id__in=s_ids)
                except Exception:
                    pass

            count = 0
            skipped_joining = 0
            for staff in active_staff:
                if staff.date_of_joining and parsed_date < staff.date_of_joining:
                    skipped_joining += 1
                    continue
                if status == "-":
                    StaffAttendance.objects.filter(staff=staff, date=att_date).delete()
                else:
                    att = StaffAttendance.objects.filter(staff=staff, date=att_date).first()
                    if att:
                        att.status = status
                        att.save()
                    else:
                        StaffAttendance.objects.create(staff=staff, date=att_date, status=status)
                count += 1

            log_action(request, "Bulk Attendance Updated", f"Bulk attendance for {count} staff on {att_date} set to '{status}'.", icon_class="ri-calendar-check-line", bg_color="success")
            msg = f"Bulk attendance updated for {count} staff."
            if skipped_joining > 0:
                msg += f" (Skipped {skipped_joining} staff before their joining date)"
            return JsonResponse({"success": True, "count": count, "date": att_date, "status": status, "skipped_joining": skipped_joining, "message": msg})
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    return JsonResponse({"success": False, "error": "Invalid request."})

@csrf_exempt
def add_advance(request):
    from apps.farmer.models import FarmerStaff, StaffAdvance

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        emp_id = request.POST.get('employee_id')
        raw_amount = request.POST.get('amount')
        method = request.POST.get('method')
        if emp_id and farmer_prof:
            try:
                staff = FarmerStaff.objects.get(id=emp_id, farmer=farmer_prof)
                if str(staff.status) == "1":
                    return JsonResponse({"success": False, "error": "Cannot issue advance to suspended/locked staff."})

                try:
                    amount_val = float(raw_amount or 0)
                except (ValueError, TypeError):
                    amount_val = 0.0

                if amount_val > 99999:
                    return JsonResponse({"success": False, "error": "Maximum advance amount allowed is ₹99,999."})

                amount = min(99999.0, max(0.0, amount_val))

                StaffAdvance.objects.create(
                    staff=staff,
                    amount=amount,
                    payment_method=method
                )
                log_action(request, "Staff Advance Issued", f"Advance payment of ₹{amount} issued to '{staff.name}'.", icon_class="ri-hand-coin-line", bg_color="warning")
                return JsonResponse({"success": True})
            except Exception as e:
                return JsonResponse({"success": False, "error": str(e)})
    return JsonResponse({"success": False})

@csrf_exempt
def delete_advance(request):
    from apps.farmer.models import StaffAdvance

    farmer_prof = get_farmer_profile(request.user)
    if request.method == "POST":
        advance_id = request.POST.get('advance_id')
        if advance_id and farmer_prof:
            try:
                adv = StaffAdvance.objects.get(id=advance_id, staff__farmer=farmer_prof)
                if adv.is_paid:
                    return JsonResponse({"success": False, "error": "Cannot void an advance that has already been deducted via payroll."})
                staff_name = adv.staff.name
                amount = adv.amount
                adv.delete()
                log_action(request, "Staff Advance Voided", f"Advance payment of ₹{amount} for '{staff_name}' was voided/deleted.", icon_class="ri-delete-bin-line", bg_color="danger")
                return JsonResponse({"success": True})
            except StaffAdvance.DoesNotExist:
                return JsonResponse({"success": False, "error": "Advance record not found."})
            except Exception as e:
                return JsonResponse({"success": False, "error": str(e)})
    return JsonResponse({"success": False, "error": "Invalid request."})

@csrf_exempt
def execute_paycheck(request):
    from apps.farmer.models import FarmerStaff, StaffPayroll

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        emp_id = request.POST.get('employee_id')
        raw_advance = request.POST.get('advance')
        raw_incentive = request.POST.get('incentive')
        method = request.POST.get('method')
        raw_net_pay = request.POST.get('net_pay')
        gross = request.POST.get('gross')
        present = request.POST.get('present')
        month_str = date.today().strftime("%B %Y").upper()

        try:
            advance = min(99999.0, max(0.0, float(raw_advance or 0)))
        except (ValueError, TypeError):
            advance = 0.0

        try:
            incentive = min(99999.0, max(0.0, float(raw_incentive or 0)))
        except (ValueError, TypeError):
            incentive = 0.0

        try:
            net_pay = min(99999999.0, max(0.0, float(raw_net_pay or 0)))
        except (ValueError, TypeError):
            net_pay = 0.0

        if emp_id and farmer_prof:
            try:
                staff = FarmerStaff.objects.get(id=emp_id, farmer=farmer_prof)
                if str(staff.status) == "1":
                    return JsonResponse({"success": False, "error": "Cannot execute paycheck for suspended/locked staff."})

                StaffPayroll.objects.create(
                    staff=staff,
                    month=month_str,
                    base_rate=staff.base_daily_rate,
                    present_days=float(present or 0),
                    advance_deduction=advance,
                    incentives=incentive,
                    net_pay=net_pay,
                    payment_method=method
                )

                from apps.farmer.models import StaffAdvance, StaffAttendance
                StaffAttendance.objects.filter(staff=staff, is_paid=False).update(is_paid=True)

                unpaid_advances = StaffAdvance.objects.filter(staff=staff, is_paid=False)
                total_unpaid = round(float(sum(a.amount for a in unpaid_advances)), 2)

                if advance > 0:
                    if total_unpaid == 0:
                        # Advance was entered directly in the payment run sheet / modal
                        StaffAdvance.objects.create(
                            staff=staff,
                            amount=advance,
                            payment_method=method or "Bank Transfer Wire",
                            is_paid=True
                        )
                    elif advance > total_unpaid:
                        unpaid_advances.update(is_paid=True)
                        diff = round(advance - total_unpaid, 2)
                        StaffAdvance.objects.create(
                            staff=staff,
                            amount=diff,
                            payment_method=method or "Bank Transfer Wire",
                            is_paid=True
                        )
                    else:
                        rem = advance
                        for adv in unpaid_advances.order_by('date', 'id'):
                            adv_amt = float(adv.amount or 0)
                            if rem >= adv_amt:
                                adv.is_paid = True
                                adv.save()
                                rem -= adv_amt
                            elif rem > 0:
                                adv.amount = round(adv_amt - rem, 2)
                                adv.save()
                                StaffAdvance.objects.create(
                                    staff=staff,
                                    amount=rem,
                                    payment_method=adv.payment_method,
                                    is_paid=True
                                )
                                rem = 0
                                break

                log_action(request, "Paycheck Executed", f"Payroll of ₹{net_pay} processed for '{staff.name}'.", icon_class="ri-bank-card-line", bg_color="success")
                return JsonResponse({"success": True})
            except Exception as e:
                return JsonResponse({"success": False, "error": str(e)})
    return JsonResponse({"success": False})

@csrf_exempt
def update_employee_rate(request):
    from apps.farmer.models import FarmerStaff

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        emp_id = request.POST.get('employee_id')
        new_rate = request.POST.get('rate')
        frequency = request.POST.get('frequency')
        if emp_id and farmer_prof:
            try:
                staff = FarmerStaff.objects.get(id=emp_id, farmer=farmer_prof)
                rate_val = float(new_rate)
                staff.salary = rate_val
                if frequency:
                    staff.salary_frequency = frequency
                
                # Calculate daily rate
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
                log_action(request, "Wage Rate Updated", f"Wage rate for '{staff.name}' updated to ₹{rate_val} ({freq}).", icon_class="ri-money-dollar-circle-line", bg_color="info")
                return JsonResponse({"success": True})
            except Exception as e:
                return JsonResponse({"success": False, "error": str(e)})
    return JsonResponse({"success": False})


def get_staff_payment_history(request):
    from apps.farmer.models import FarmerStaff, StaffAdvance, StaffPayroll

    farmer_prof = get_farmer_profile(request.user)
    if not farmer_prof:
        return JsonResponse({"success": False, "error": "Unauthorized farmer profile."})

    staff_id = request.GET.get('staff_id') or request.POST.get('staff_id')
    if not staff_id:
        return JsonResponse({"success": False, "error": "Staff ID is required."})

    try:
        staff = FarmerStaff.objects.get(id=staff_id, farmer=farmer_prof)
    except FarmerStaff.DoesNotExist:
        return JsonResponse({"success": False, "error": "Staff member not found."})

    advances_qs = StaffAdvance.objects.filter(staff=staff).order_by('-date', '-id')
    payrolls_qs = StaffPayroll.objects.filter(staff=staff).order_by('-paid_on', '-id')

    total_advance = sum(float(a.amount or 0) for a in advances_qs)
    pending_advance = sum(float(a.amount or 0) for a in advances_qs if not a.is_paid)
    settled_advance = sum(float(a.amount or 0) for a in advances_qs if a.is_paid)

    total_disbursed = sum(float(p.net_pay or 0) for p in payrolls_qs)
    total_incentives = sum(float(p.incentives or 0) for p in payrolls_qs)
    total_advance_deducted = sum(float(p.advance_deduction or 0) for p in payrolls_qs)

    advances_data = []
    for a in advances_qs:
        advances_data.append({
            "id": a.id,
            "amount": float(a.amount or 0),
            "date": a.date.strftime("%d %b, %Y") if a.date else "—",
            "payment_method": a.payment_method or "Bank Transfer",
            "is_paid": bool(a.is_paid),
            "status_label": "Settled via Payroll" if a.is_paid else "Pending Deduction"
        })

    payrolls_data = []
    for p in payrolls_qs:
        payrolls_data.append({
            "id": p.id,
            "month": p.month or "—",
            "paid_on": p.paid_on.strftime("%d %b, %Y") if p.paid_on else "—",
            "base_rate": float(p.base_rate or 0),
            "present_days": p.present_days,
            "absent_days": p.absent_days,
            "advance_deduction": float(p.advance_deduction or 0),
            "incentives": float(p.incentives or 0),
            "net_pay": float(p.net_pay or 0),
            "payment_method": p.payment_method or "Cash Handout"
        })

    profile_img_url = staff.profile_image.url if staff.profile_image else ""
    desig_name = staff.designation.name if staff.designation else "Not Assigned"

    return JsonResponse({
        "success": True,
        "staff": {
            "id": staff.id,
            "name": staff.name,
            "role": "Role Manager" if staff.is_role_manager else (staff.role or "Employee"),
            "designation": desig_name,
            "mobile": staff.mobile_number,
            "salary": float(staff.salary or 0),
            "salary_frequency": staff.salary_frequency or "Daily",
            "status": "Active" if staff.status == "0" else "Suspended",
            "profile_image": profile_img_url
        },
        "summary": {
            "total_advance": round(total_advance, 2),
            "pending_advance": round(pending_advance, 2),
            "settled_advance": round(settled_advance, 2),
            "total_disbursed": round(total_disbursed, 2),
            "total_incentives": round(total_incentives, 2),
            "total_advance_deducted": round(total_advance_deducted, 2),
            "advance_count": advances_qs.count(),
            "payroll_count": payrolls_qs.count()
        },
        "advances": advances_data,
        "payrolls": payrolls_data
    })


def manage_users(request):
    eco_modules = [
        {"name": "Dashboard", "icon": "ri-bar-chart-line", "active": True},
        {"name": "Farms", "icon": "ri-plant-line", "active": False},
    ]
    from apps.farmer.models import FarmerStaff
    farmer_prof = get_farmer_profile(request.user)
    from apps.farmer.access import available_manager_permissions
    permissions = available_manager_permissions(farmer_prof)
    staff_members = FarmerStaff.objects.filter(farmer=farmer_prof)
    total_staff = staff_members.count()
    active_staff = staff_members.filter(status="0").count()
    suspended_staff = staff_members.filter(status="1").count()
    admins_staff = staff_members.filter(role__icontains="Manager").count() + staff_members.filter(role__icontains="Admin").count()

    from apps.superadmin.models import Designation, DocumentType
    designations = Designation.objects.filter(is_active=True).order_by('name')
    document_types = DocumentType.objects.filter(is_active=True).order_by('name')

    return render(request, "farmer/manage_users.html",{
        "eco_modules": eco_modules,
        "permissions": permissions,
        "staff_members": staff_members,
        "total_staff": total_staff,
        "active_staff": active_staff,
        "suspended_staff": suspended_staff,
        "admins_staff": admins_staff,
        "designations": designations,
        "document_types": document_types,
    })

@csrf_exempt
def add_staff_api(request):
    import json
    import re

    from django.contrib.auth.models import User
    from django.http import JsonResponse

    from apps.farmer.models import FarmerStaff

    if request.method == "POST":
        try:
            farmer_prof = get_farmer_profile(request.user)

            status = request.POST.get('status', '0')
            is_manager = request.POST.get('is_manager') == 'true'
            role = "Manager" if is_manager else "Employee"

            # Check User Manager limit ONLY if adding a Manager. Regular Employees/Workers are Unlimited!
            if is_manager:
                active_sub = farmer_prof.subscriptions.filter(subscription_status='active').order_by('-id').first()
                if not active_sub or not active_sub.package:
                    return JsonResponse({'success': False, 'error': 'No active subscription found. Please subscribe to a package first.'})

                max_users_str = str(active_sub.package.my_users).lower() if active_sub.package.my_users else "unlimited"
                if max_users_str != "unlimited":
                    match_user = re.search(r'[\d]+', max_users_str)
                    if match_user:
                        max_managers = int(match_user.group())
                        current_manager_count = FarmerStaff.objects.filter(farmer=farmer_prof, is_role_manager=True).count()
                        if current_manager_count >= max_managers:
                            return JsonResponse({'success': False, 'error': f'Cannot add Manager. Your package allows a maximum of {max_managers} User Managers.'})

            name = (request.POST.get('name') or '').strip()
            mobile = (request.POST.get('mobile') or '').strip()

            if not name:
                return JsonResponse({'success': False, 'error': 'Full Name is required.'})

            if not mobile:
                return JsonResponse({'success': False, 'error': 'Mobile number is required.'})

            from apps.superadmin.phone_utils import check_farm_staff_mobile
            is_taken, err_msg = check_farm_staff_mobile(farmer_prof, mobile)
            if is_taken:
                return JsonResponse({'success': False, 'error': err_msg})

            dob = request.POST.get('dob')
            doj = request.POST.get('doj')
            gender = request.POST.get('gender')
            doc_type = request.POST.get('doc_type')
            doc_file = request.FILES.get('doc_file')
            profile_image = request.FILES.get('profile_image')

            from auth.helpers import validate_uploaded_file, ALLOWED_IMAGE_EXTENSIONS, ALLOWED_DOCUMENT_EXTENSIONS
            valid_doc, doc_err = validate_uploaded_file(doc_file, ALLOWED_DOCUMENT_EXTENSIONS)
            if not valid_doc:
                return JsonResponse({'success': False, 'error': doc_err})

            valid_img, img_err = validate_uploaded_file(profile_image, ALLOWED_IMAGE_EXTENSIONS)
            if not valid_img:
                return JsonResponse({'success': False, 'error': img_err})

            salary_frequency = request.POST.get('salary_frequency')
            salary = request.POST.get('salary')
            address = request.POST.get('address')
            designation_id = request.POST.get('designation')

            salary_val = 0.0
            if salary:
                try:
                    salary_val = float(salary)
                except ValueError:
                    pass

            base_daily_rate = 500.0
            if salary_frequency == "Daily":
                base_daily_rate = salary_val
            elif salary_frequency == "Weekly":
                base_daily_rate = round(salary_val / 6.0, 2)
            elif salary_frequency == "Monthly":
                base_daily_rate = round(salary_val / 30.0, 2)
            elif salary_val > 0:
                base_daily_rate = salary_val

            # Parse permissions: All Modules vs Selected Modules
            perm_all = request.POST.get('perm_all') == 'true'
            perms_raw = request.POST.get('permissions', '[]')
            try:
                selected_perms = json.loads(perms_raw) if isinstance(perms_raw, str) else request.POST.getlist('permissions')
            except Exception:
                selected_perms = request.POST.getlist('permissions')

            from apps.farmer.access import normalized_manager_permissions
            assigned_permissions = normalized_manager_permissions(
                farmer_prof, selected_perms if isinstance(selected_perms, list) else [], perm_all
            ) if is_manager else []

            # Auto-create User account linked to Mobile Number for login ONLY if Manager
            user_obj = None
            if is_manager:
                username = request.POST.get('username') or mobile
                password = request.POST.get('password')

                if username:
                    from apps.superadmin.phone_utils import find_profile_by_phone
                    existing_identity = find_profile_by_phone(username)
                    user_obj = existing_identity.user if existing_identity else User.objects.filter(username=username).first()
                    if not user_obj:
                        if password:
                            user_obj = User.objects.create_user(username=username, password=password)
                        else:
                            user_obj = User.objects.create_user(username=username)
                            user_obj.set_unusable_password()
                            user_obj.save()

                        if hasattr(user_obj, 'profile'):
                            user_obj.profile.mobile_number = mobile
                            user_obj.profile.role = 'staff'
                            user_obj.profile.save()

            staff_user = user_obj

            staff = FarmerStaff.objects.create(
                user=staff_user,
                farmer=farmer_prof,
                name=name,
                mobile_number=mobile,
                date_of_birth=dob if dob else None,
                date_of_joining=doj if doj else timezone.now().date(),
                gender=gender,
                document_type=doc_type,
                document_file=doc_file,
                profile_image=profile_image,
                status=status,
                is_role_manager=is_manager,
                role=role,
                permissions=assigned_permissions,
                salary_frequency=salary_frequency,
                salary=salary_val if salary else None,
                address=address,
                designation_id=designation_id if designation_id else None,
                base_daily_rate=base_daily_rate
            )
            log_action(request, "Staff/Manager Account Registered", f"Staff/Manager '{name}' was registered.", icon_class="ri-user-add-line", bg_color="success")
            return JsonResponse({'success': True, 'message': 'Staff/Manager added successfully'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request'})

@csrf_exempt
def update_staff_api(request):
    import json

    from django.contrib.auth.models import User
    from django.http import JsonResponse

    from apps.farmer.models import FarmerStaff

    if request.method == "POST":
        try:
            farmer_prof = get_farmer_profile(request.user)
            staff_id = request.POST.get('staff_id')
            staff = FarmerStaff.objects.get(id=staff_id, farmer=farmer_prof)

            new_name = (request.POST.get('name') or '').strip()
            new_mobile = (request.POST.get('mobile') or '').strip()

            if new_mobile:
                from apps.superadmin.phone_utils import check_farm_staff_mobile
                is_taken, err_msg = check_farm_staff_mobile(
                    farmer_prof,
                    new_mobile,
                    exclude_staff_id=staff.id
                )
                if is_taken:
                    return JsonResponse({'success': False, 'error': err_msg})
                staff.mobile_number = new_mobile

            if new_name:
                staff.name = new_name


            dob = request.POST.get('dob')
            if dob: staff.date_of_birth = dob
            elif request.POST.get('clear_dob') == 'true': staff.date_of_birth = None

            doj = request.POST.get('doj')
            if doj: staff.date_of_joining = doj
            elif request.POST.get('clear_doj') == 'true': staff.date_of_joining = None

            staff.gender = request.POST.get('gender')
            staff.document_type = request.POST.get('doc_type')
            from auth.helpers import validate_uploaded_file, ALLOWED_IMAGE_EXTENSIONS, ALLOWED_DOCUMENT_EXTENSIONS
            if request.FILES.get('doc_file'):
                valid_doc, doc_err = validate_uploaded_file(request.FILES.get('doc_file'), ALLOWED_DOCUMENT_EXTENSIONS)
                if not valid_doc:
                    return JsonResponse({'success': False, 'error': doc_err})
                staff.document_file = request.FILES.get('doc_file')
            elif request.POST.get('remove_doc_file') == 'true':
                staff.document_file = None

            if request.FILES.get('profile_image'):
                valid_img, img_err = validate_uploaded_file(request.FILES.get('profile_image'), ALLOWED_IMAGE_EXTENSIONS)
                if not valid_img:
                    return JsonResponse({'success': False, 'error': img_err})
                staff.profile_image = request.FILES.get('profile_image')
            elif request.POST.get('remove_profile_image') == 'true':
                staff.profile_image = None


            staff.status = request.POST.get('status', '0')
            is_manager = request.POST.get('is_manager') == 'true'

            # Check User Manager limit if promoting an employee to Manager
            if is_manager and not staff.is_role_manager:
                active_sub = farmer_prof.subscriptions.filter(subscription_status='active').order_by('-id').first()
                if active_sub and active_sub.package:
                    max_users_str = str(active_sub.package.my_users).lower() if active_sub.package.my_users else "unlimited"
                    if max_users_str != "unlimited":
                        import re
                        match_user = re.search(r'[\d]+', max_users_str)
                        if match_user:
                            max_managers = int(match_user.group())
                            current_manager_count = FarmerStaff.objects.filter(farmer=farmer_prof, is_role_manager=True).exclude(id=staff.id).count()
                            if current_manager_count >= max_managers:
                                return JsonResponse({'success': False, 'error': f'Cannot promote to Manager. Your package allows a maximum of {max_managers} User Managers.'})

            staff.is_role_manager = is_manager
            staff.role = "Manager" if is_manager else "Employee"

            salary_frequency = request.POST.get('salary_frequency')
            salary = request.POST.get('salary')
            address = request.POST.get('address')
            designation_id = request.POST.get('designation')

            staff.salary_frequency = salary_frequency
            if salary:
                try:
                    staff.salary = float(salary)
                except ValueError:
                    pass
            else:
                staff.salary = None
            staff.address = address
            if designation_id:
                staff.designation_id = designation_id
            else:
                staff.designation_id = None

            salary_val = float(staff.salary) if staff.salary else 0.0
            if salary_frequency == "Daily":
                staff.base_daily_rate = salary_val
            elif salary_frequency == "Weekly":
                staff.base_daily_rate = round(salary_val / 6.0, 2)
            elif salary_frequency == "Monthly":
                staff.base_daily_rate = round(salary_val / 30.0, 2)
            elif salary_val > 0:
                staff.base_daily_rate = salary_val

            # Parse permissions
            perm_all = request.POST.get('perm_all') == 'true'
            perms_raw = request.POST.get('permissions', '[]')
            try:
                selected_perms = json.loads(perms_raw) if isinstance(perms_raw, str) else request.POST.getlist('permissions')
            except Exception:
                selected_perms = request.POST.getlist('permissions')

            from apps.farmer.access import normalized_manager_permissions
            staff.permissions = normalized_manager_permissions(
                farmer_prof, selected_perms if isinstance(selected_perms, list) else [], perm_all
            ) if is_manager else []

            # Ensure user account is linked to mobile number for login ONLY for Managers
            if is_manager:
                username = request.POST.get('username') or staff.mobile_number
                password = request.POST.get('password')

                if staff.user:
                    if username and username != staff.user.username:
                        if not User.objects.filter(username=username).exclude(id=staff.user.id).exists():
                            staff.user.username = username
                    if password:
                        staff.user.set_password(password)
                    staff.user.save()
                elif username:
                    from apps.superadmin.phone_utils import find_profile_by_phone
                    existing_identity = find_profile_by_phone(username)
                    user_obj = existing_identity.user if existing_identity else User.objects.filter(username=username).first()
                    if not user_obj:
                        if password:
                            user_obj = User.objects.create_user(username=username, password=password)
                        else:
                            user_obj = User.objects.create_user(username=username)
                            user_obj.set_unusable_password()
                            user_obj.save()
                        user_obj.profile.role = 'staff'
                        user_obj.profile.mobile_number = staff.mobile_number
                        user_obj.profile.save(update_fields=['role', 'mobile_number'])
                    staff.user = user_obj
            staff.save()
            log_action(request, "Staff/Manager Account Updated", f"Staff/Manager '{staff.name}' details were updated.", icon_class="ri-user-settings-line", bg_color="info")
            return JsonResponse({'success': True, 'message': 'Staff/Manager updated successfully'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request'})

@csrf_exempt
def delete_staff_api(request):
    from django.http import JsonResponse
    from apps.farmer.models import FarmerStaff

    farmer_prof = get_farmer_profile(request.user)

    if request.method == "POST":
        try:
            staff_id = request.POST.get('staff_id')
            staff = FarmerStaff.objects.get(id=staff_id, farmer=farmer_prof)
            staff_name = staff.name
            staff.delete()
            log_action(request, "Staff/Manager Account Deleted", f"Staff/Manager '{staff_name}' was deleted.", icon_class="ri-user-shared-line", bg_color="danger")
            return JsonResponse({'success': True, 'message': 'Staff deleted successfully'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request'})


def check_staff_mobile(request):
    """
    Live AJAX check endpoint to verify whether a mobile number is already taken.
    """
    mobile = request.GET.get('mobile', '').strip()
    staff_id = request.GET.get('staff_id')
    if not mobile or len(mobile) < 7:
        return JsonResponse({'is_taken': False})
    farmer_prof = get_farmer_profile(request.user)
    from apps.superadmin.phone_utils import check_farm_staff_mobile
    is_taken, err_msg = check_farm_staff_mobile(farmer_prof, mobile, exclude_staff_id=staff_id)
    return JsonResponse({'is_taken': is_taken, 'error': err_msg if is_taken else ''})


@csrf_exempt
def quick_add_designation(request):
    from django.http import JsonResponse
    from apps.superadmin.models import Designation
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Designation name is required.'})

        desig = Designation.objects.filter(name__iexact=name).first()
        if not desig:
            desig = Designation.objects.create(name=name, description=description, is_active=True)
        elif not desig.is_active:
            desig.is_active = True
            desig.save()

        return JsonResponse({
            'success': True,
            'id': desig.id,
            'name': desig.name,
        })
    return JsonResponse({'success': False, 'error': 'Invalid request'})


@csrf_exempt
def quick_add_staff_document_type(request):
    from django.http import JsonResponse
    from apps.superadmin.models import DocumentType
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Document type name is required.'})

        dt = DocumentType.objects.filter(name__iexact=name).first()
        if not dt:
            dt = DocumentType.objects.create(name=name, description=description, is_active=True)
        elif not dt.is_active:
            dt.is_active = True
            dt.save()

        return JsonResponse({
            'success': True,
            'id': dt.id,
            'name': dt.name,
        })
    return JsonResponse({'success': False, 'error': 'Invalid request'})


def manage_market(request):
    from django.contrib import messages
    from django.shortcuts import redirect
    from apps.superadmin.models import (
        Crop,
        CropBlueprint,
        DepotCropPricing,
        DepotTerminal,
        Guideline,
        GuidelineCategory,
        LocationCategory,
        LogisticsWorker,
        MarketPriceIndex,
        NearbyLocation,
        RentalEquipment,
        HardwareClass,
        Designation,
        ManPowerWorkType,
        ExpenseUnit,
        State,
        City,
    )

    if request.method == "POST":
        action = request.POST.get("action")
        if action in ["create_nearby_location", "create_location"]:
            NearbyLocation.objects.create(
                name=request.POST.get("name"),
                service_class=request.POST.get("service_class"),
                contact_person=request.POST.get("contact_person"),
                designation=request.POST.get("designation"),
                contact_number=request.POST.get("phone"),
                email=request.POST.get("email"),
                website=request.POST.get("website"),
                address=request.POST.get("address"),
                notes=request.POST.get("notes")
            )
            messages.success(request, "Agri Location registered successfully!")
            return redirect('/farmer/manage_market/#nearby_location')

        elif action in ["create_worker", "farmer_create_worker"]:
            name = request.POST.get('name', '').strip()
            designation = request.POST.get('designation', '').strip()
            workforce_class = designation or request.POST.get('workforce_class', '').strip()
            specialty_skill = request.POST.get('specialty_skill', '').strip()
            address = request.POST.get('address', '').strip()
            state_raw = request.POST.get('state', '').strip()
            city_raw = request.POST.get('city', '').strip()
            notes = request.POST.get('notes', '').strip()
            wage_rate = request.POST.get('wage_rate') or 0
            wage_unit = request.POST.get('wage_unit', 'Day').strip()
            contact_phone = request.POST.get('contact_phone', '').strip()
            status = request.POST.get('status', 'Available').strip()

            state_val = state_raw
            if state_raw.isdigit():
                st = State.objects.filter(id=int(state_raw)).first()
                if st:
                    state_val = st.name
            city_val = city_raw
            if city_raw.isdigit():
                ct = City.objects.filter(id=int(city_raw)).first()
                if ct:
                    city_val = ct.name

            LogisticsWorker.objects.create(
                name=name,
                designation=designation,
                workforce_class=workforce_class,
                specialty_skill=specialty_skill,
                address=address,
                state=state_val,
                city=city_val,
                notes=notes,
                wage_rate=wage_rate,
                wage_unit=wage_unit,
                contact_phone=contact_phone,
                status=status
            )
            messages.success(request, f"Worker {name} successfully registered!")
            return redirect('/farmer/manage_market/#workers_availability')

        elif action in ["edit_worker", "farmer_edit_worker"]:
            worker_id = request.POST.get('worker_id')
            from django.shortcuts import get_object_or_404
            worker = get_object_or_404(LogisticsWorker, id=worker_id)
            worker.name = request.POST.get('name', '').strip()
            worker.designation = request.POST.get('designation', '').strip()
            worker.workforce_class = worker.designation or request.POST.get('workforce_class', '').strip()
            worker.specialty_skill = request.POST.get('specialty_skill', '').strip()
            worker.address = request.POST.get('address', '').strip()
            
            state_raw = request.POST.get('state', '').strip()
            city_raw = request.POST.get('city', '').strip()
            state_val = state_raw
            if state_raw.isdigit():
                st = State.objects.filter(id=int(state_raw)).first()
                if st:
                    state_val = st.name
            city_val = city_raw
            if city_raw.isdigit():
                ct = City.objects.filter(id=int(city_raw)).first()
                if ct:
                    city_val = ct.name
            
            worker.state = state_val
            worker.city = city_val
            worker.notes = request.POST.get('notes', '').strip()
            worker.wage_rate = request.POST.get('wage_rate') or 0
            worker.wage_unit = request.POST.get('wage_unit', 'Day').strip()
            worker.contact_phone = request.POST.get('contact_phone', '').strip()
            worker.status = request.POST.get('status', 'Available').strip()
            worker.save()
            messages.success(request, f"Worker {worker.name} successfully updated!")
            return redirect('/farmer/manage_market/#workers_availability')

        elif action in ["delete_worker", "farmer_delete_worker"]:
            worker_id = request.POST.get('worker_id')
            from django.shortcuts import get_object_or_404
            worker = get_object_or_404(LogisticsWorker, id=worker_id)
            name = worker.name
            worker.delete()
            messages.success(request, f"Worker {name} successfully deleted!")
            return redirect('/farmer/manage_market/#workers_availability')

        elif action in ["update_status", "update_worker_status"]:
            worker_id = request.POST.get('worker_id')
            status = request.POST.get('status')
            from django.shortcuts import get_object_or_404
            worker = get_object_or_404(LogisticsWorker, id=worker_id)
            worker.status = status
            worker.save()
            messages.success(request, f"Worker {worker.name} status updated to {status}!")
            return redirect('/farmer/manage_market/#workers_availability')



    eco_modules = [
        {"name": "Dashboard", "icon": "ri-bar-chart-line", "active": True},
        {"name": "Farms", "icon": "ri-plant-line", "active": False},
    ]

    from apps.farmer.models import CropSplit

    profile = get_farmer_profile(request.user)

    # Location matching & Crop personalization for the logged-in farmer
    farmer_city = ""
    farmer_state = ""
    if profile:
        if hasattr(profile, 'district') and profile.district:
            farmer_city = getattr(profile.district, 'name', '')
        if hasattr(profile, 'state') and profile.state:
            farmer_state = getattr(profile.state, 'name', '')

    farmer_crops = list(CropSplit.objects.filter(farm__farmer=profile).values_list('crop__name', flat=True).distinct()) if profile else []

    crops = MarketPriceIndex.objects.all()
    terminals = DepotTerminal.objects.all().order_by('-id')

    formatted_terminals = []
    for term in terminals:
        term_location_str = f"{term.location or ''} {term.district or ''}".strip().lower()
        is_city_match = bool(farmer_city and farmer_city.lower() in term_location_str)
        is_state_match = bool(farmer_state and farmer_state.lower() in term_location_str)

        crop_match_count = 0
        if isinstance(term.available_crops, list):
            for c in term.available_crops:
                c_name = c.get('name') if isinstance(c, dict) else c
                c_name_str = str(c_name or '').strip()
                if c_name_str:
                    c_name_lower = c_name_str.lower()
                    if any(fc and (fc.lower() in c_name_lower or c_name_lower in fc.lower()) for fc in farmer_crops):
                        crop_match_count += 1

        term.is_city_match = is_city_match
        term.is_state_match = is_state_match
        term.is_location_match = is_city_match or is_state_match
        term.crop_match_count = crop_match_count
        formatted_terminals.append(term)

    # Sort terminals: City match first, State match second, Crop match count third
    formatted_terminals.sort(key=lambda t: (t.is_city_match, t.is_state_match, t.crop_match_count), reverse=True)

    pricings = DepotCropPricing.objects.filter(trading_restricted=False).select_related('depot', 'crop_index').order_by('-id')
    all_crops = Crop.objects.filter(is_active=True)

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

    locations = NearbyLocation.objects.all().order_by('-created_at')
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

    workers = LogisticsWorker.objects.all().order_by('-created_at')
    formatted_workers = []
    for w in workers:
        desig = w.designation or w.workforce_class or ''
        formatted_workers.append({
            'id': w.id,
            'name': w.name,
            'designation': desig,
            'class': desig,
            'skill': w.specialty_skill or '',
            'wage': w.wage_rate.normalize() if w.wage_rate else '',
            'wage_val': str(w.wage_rate.normalize()) if w.wage_rate else '',
            'wage_unit': w.wage_unit or 'Day',
            'phone': w.contact_phone or '',
            'address': w.address or '',
            'city': w.city or '',
            'state': w.state or '',
            'notes': w.notes or '',
            'status': w.status or 'Available',
            'initials': "".join([x[0] for x in w.name.split() if x])[:2].upper()
        })


    rentals = RentalEquipment.objects.all().order_by('-created_at')
    formatted_rentals = []
    for r in rentals:
        formatted_rentals.append({
            'id': r.id,
            'name': r.equipment_name,
            'class': r.hardware_class,
            'price': r.price_per_day.normalize() if r.price_per_day else '',
            'rental_unit': 'Per Day',
            'unit_short': 'day',
            'owner': r.owner_name or 'Platform Fleet',
            'phone': r.contact_phone or '',
            'rating': r.condition_rating or 'Good',
            'status': r.status or 'Available',
            'is_own': False,
            'is_farmer': False
        })

    from apps.farmer.models import FarmerInventory
    farmer_rentals = FarmerInventory.objects.filter(rental_available=True).select_related('farmer', 'farmer__user').order_by('-created_at')
    for fr in farmer_rentals:
        condition = "Good"
        if fr.status in ["Maintenance", "Needs Repair"]:
            condition = "Needs Repair"
        elif fr.status in ["Available", "Operational"]:
            condition = "Good"

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

        is_own = False
        if profile and fr.farmer_id == profile.id:
            is_own = True
            owner_display = "You (Own)"
        else:
            owner_display = fr.farmer.company_name or (fr.farmer.user.get_full_name() if fr.farmer and fr.farmer.user else "Farmer")

        farmer_phone = fr.farmer.mobile_number if fr.farmer and hasattr(fr.farmer, 'mobile_number') and fr.farmer.mobile_number else (fr.farmer.user.username if fr.farmer and fr.farmer.user else '')

        formatted_rentals.append({
            'id': f"farmer_{fr.id}",
            'inventory_id': fr.id,
            'name': fr.item_name,
            'class': fr.category or ('Tool' if 'Tool' in (fr.category or '') else 'Machinery'),
            'price': fr.rental_price.normalize() if fr.rental_price else (fr.unit_price.normalize() if fr.unit_price else '0'),
            'rental_unit': unit_str,
            'unit_short': unit_short,
            'owner': owner_display,
            'phone': farmer_phone,
            'rating': condition,
            'status': fr.status if fr.status in ['Available', 'Booked', 'Rented', 'Maintenance', 'Operational'] else 'Available',
            'is_own': is_own,
            'is_farmer': True
        })

    guidelines_list = Guideline.objects.all().order_by('-created_at')
    guideline_categories = GuidelineCategory.objects.filter(is_active=True).order_by('name')

    return render(request, "farmer/manage_market.html",{
        "eco_modules": eco_modules,
        "crops": crops,
        "terminals": formatted_terminals,
        "pricings": pricings,
        "all_crops": all_crops,
        "farmer_city": farmer_city,
        "farmer_state": farmer_state,
        "farmer_crops": farmer_crops,
        "blueprints": blueprints,
        "selected_blueprint": selected_blueprint,
        "stages": stages,
        "guidelines": guidelines_list,
        "guideline_categories": guideline_categories,
        "locations": formatted_locations,
        "service_classes": LocationCategory.objects.filter(is_active=True).order_by('name'),
        "workers": formatted_workers,
        "rentals": formatted_rentals,
        "hardware_classes": HardwareClass.objects.filter(is_active=True).order_by('name'),
        "designations": Designation.objects.filter(is_active=True).order_by('name'),
        "work_types": ManPowerWorkType.objects.filter(is_active=True).order_by('name'),
        "wage_units": ExpenseUnit.objects.filter(is_active=True).order_by('name'),
        "states": State.objects.filter(country__sort_name='IN').order_by('name') if State.objects.filter(country__sort_name='IN').exists() else State.objects.all().order_by('name'),
    })


def quick_add_service_class(request):
    from django.http import JsonResponse
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

def quick_delete_service_class(request):
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

def manage_task(request):
    from apps.farmer.models import CropSplit, Farm, FarmerStaff, FarmTask
    from apps.superadmin.models import TaskCategory, TaskPriority
    farmer_prof = get_farmer_profile(request.user)

    eco_modules = [
        {"name": "Dashboard", "icon": "ri-bar-chart-line", "active": False},
        {"name": "Farms", "icon": "ri-plant-line", "active": False},
    ]

    # Seed default task categories if none exist
    if not TaskCategory.objects.exists():
        TaskCategory.objects.create(name="Harvesting", description="Crop harvest tasks")
        TaskCategory.objects.create(name="Sowing", description="Seed sowing tasks")
        TaskCategory.objects.create(name="Irrigation", description="Irrigation and watering")
        TaskCategory.objects.create(name="Fertilizing", description="Fertilizer applications")
        TaskCategory.objects.create(name="Pest Control", description="Pesticide spraying and weed control")
        TaskCategory.objects.create(name="Pruning", description="Pruning and crop care")
        TaskCategory.objects.create(name="Land Preparation", description="Ploughing and soil prep")
        TaskCategory.objects.create(name="General Chores", description="General field tasks")
        
    # Seed default task priorities if none exist
    if not TaskPriority.objects.exists():
        TaskPriority.objects.create(name="High Priority", description="Critical, needs immediate attention")
        TaskPriority.objects.create(name="Medium Priority", description="Important, schedule when possible")
        TaskPriority.objects.create(name="Low Priority", description="Low priority, standard chores")

    task_categories = TaskCategory.objects.filter(is_active=True).order_by('name')
    task_priorities = TaskPriority.objects.filter(is_active=True).order_by('id')

    from django.db.models import Q
    if farmer_prof:
        FarmTask.objects.filter(farmer__isnull=True, farm__farmer=farmer_prof).update(farmer=farmer_prof)
        FarmTask.objects.filter(farmer__isnull=True, associated_split__farm__farmer=farmer_prof).update(farmer=farmer_prof)

    tasks = FarmTask.objects.filter(
        Q(farmer=farmer_prof) | Q(farm__farmer=farmer_prof) | Q(associated_split__farm__farmer=farmer_prof)
    ).distinct().order_by('-created_at') if farmer_prof else FarmTask.objects.none()
    total_tasks = tasks.count()
    pending_tasks = tasks.filter(status='Pending').count()
    active_tasks = tasks.filter(status='In Progress').count()
    completed_tasks = tasks.filter(status='Completed').count()
    scheduled_tasks = tasks.filter(status='Scheduled').count()

    farms = Farm.objects.filter(farmer=farmer_prof) if farmer_prof else Farm.objects.none()
    workers = FarmerStaff.objects.filter(farmer=farmer_prof) if farmer_prof else FarmerStaff.objects.none()
    crop_splits = CropSplit.objects.filter(farm__farmer=farmer_prof) if farmer_prof else CropSplit.objects.none()

    # Blueprints logic
    blueprints = CropBlueprint.objects.filter(farmer=farmer_prof).order_by('-created_at') if farmer_prof else CropBlueprint.objects.none()

    selected_blueprint_id = request.GET.get('blueprint_id')
    selected_blueprint = None
    stages = []

    if blueprints.exists():
        if selected_blueprint_id:
            selected_blueprint = blueprints.filter(id=selected_blueprint_id).first()
        if not selected_blueprint:
            selected_blueprint = blueprints.first()

        if selected_blueprint:
            stages = selected_blueprint.stages.all().order_by('day_offset')

    return render(request, "farmer/manage_task.html", {
        "eco_modules": eco_modules,
        "tasks": tasks,
        "total_tasks": total_tasks,
        "pending_tasks": pending_tasks,
        "active_tasks": active_tasks,
        "completed_tasks": completed_tasks,
        "scheduled_tasks": scheduled_tasks,
        "farms": farms,
        "workers": workers,
        "crop_splits": crop_splits,
        "blueprints": blueprints,
        "selected_blueprint": selected_blueprint,
        "stages": stages,
        "task_categories": task_categories,
        "task_priorities": task_priorities
    })

def add_farmer_blueprint(request):
    farmer_prof = get_farmer_profile(request.user)
    if request.method == 'POST' and farmer_prof:
        name = request.POST.get('crop_name')
        season = request.POST.get('cultivation_season')
        days = request.POST.get('yield_cycle_days')

        blueprint = CropBlueprint.objects.create(
            farmer=farmer_prof,
            crop_name=name,
            cultivation_season=season,
            yield_cycle_days=days
        )

        # Pre-seed milestones
        BlueprintStage.objects.create(blueprint=blueprint, milestone_title='Soil Preparation & Sowing', activity_type='Sowing Activity', day_offset=0, priority_level='High')
        BlueprintStage.objects.create(blueprint=blueprint, milestone_title='Growth Inspection', activity_type='Irrigation Check', day_offset=int(int(days)/2), priority_level='Medium')
        BlueprintStage.objects.create(blueprint=blueprint, milestone_title='Harvest Gathering', activity_type='Harvesting', day_offset=int(days), priority_level='High')

        log_action(request, "Crop Blueprint Created", f"Blueprint '{name}' created.", icon_class="ri-draft-line", bg_color="success")
        messages.success(request, 'Blueprint added successfully.')
    return redirect('/farmer/manage_task/')

def delete_farmer_blueprint(request):
    farmer_prof = get_farmer_profile(request.user)
    if request.method == 'POST' and farmer_prof:
        blueprint_id = request.POST.get('blueprint_id')
        bp = CropBlueprint.objects.filter(id=blueprint_id, farmer=farmer_prof).first()
        bp_name = bp.crop_name if bp else "Blueprint"
        if bp: bp.delete()
        log_action(request, "Crop Blueprint Deleted", f"Blueprint '{bp_name}' deleted.", icon_class="ri-delete-bin-line", bg_color="danger")
        messages.success(request, 'Blueprint deleted successfully.')
    return redirect('/farmer/manage_task/')

def add_farmer_blueprint_stage(request):
    farmer_prof = get_farmer_profile(request.user)
    if request.method == 'POST' and farmer_prof:
        blueprint_id = request.POST.get('blueprint_id')
        blueprint = CropBlueprint.objects.filter(id=blueprint_id, farmer=farmer_prof).first()
        if blueprint:
            title = request.POST.get('milestone_title')
            activity = request.POST.get('activity_type')
            offset = request.POST.get('day_offset')
            priority = request.POST.get('priority_level')

            BlueprintStage.objects.create(
                blueprint=blueprint,
                milestone_title=title,
                activity_type=activity,
                day_offset=offset,
                priority_level=priority
            )
            log_action(request, "Blueprint Stage Added", f"Stage '{title}' added to blueprint '{blueprint.crop_name}'.", icon_class="ri-add-circle-line", bg_color="success")
            messages.success(request, 'Stage added successfully.')
        return redirect(f'/farmer/manage_task/?blueprint_id={blueprint_id}')
    return redirect('/farmer/manage_task/')

def edit_farmer_blueprint_stage(request):
    farmer_prof = get_farmer_profile(request.user)
    if request.method == 'POST' and farmer_prof:
        stage_id = request.POST.get('stage_id')
        blueprint_id = request.POST.get('blueprint_id')

        stage = BlueprintStage.objects.filter(id=stage_id, blueprint__farmer=farmer_prof).first()
        if stage:
            stage.milestone_title = request.POST.get('milestone_title')
            stage.activity_type = request.POST.get('activity_type')
            stage.day_offset = request.POST.get('day_offset')
            stage.priority_level = request.POST.get('priority_level')
            stage.save()
            log_action(request, "Blueprint Stage Updated", f"Stage '{stage.milestone_title}' updated.", icon_class="ri-edit-line", bg_color="info")
            messages.success(request, 'Stage updated successfully.')
        return redirect(f'/farmer/manage_task/?blueprint_id={blueprint_id}')
    return redirect('/farmer/manage_task/')

def delete_farmer_blueprint_stage(request):
    farmer_prof = get_farmer_profile(request.user)
    if request.method == 'POST' and farmer_prof:
        stage_id = request.POST.get('stage_id')
        blueprint_id = request.POST.get('blueprint_id')
        stage = BlueprintStage.objects.filter(id=stage_id, blueprint__farmer=farmer_prof).first()
        st_title = stage.milestone_title if stage else "Stage"
        if stage: stage.delete()
        log_action(request, "Blueprint Stage Deleted", f"Stage '{st_title}' deleted.", icon_class="ri-delete-bin-line", bg_color="danger")
        messages.success(request, 'Stage deleted successfully.')
        return redirect(f'/farmer/manage_task/?blueprint_id={blueprint_id}')
    return redirect('/farmer/manage_task/')
def update_profile(request):
    if request.method == "POST" and "pincode" in request.POST:
        from apps.farmer.validation import pincode_error
        error = pincode_error(request)
        if error is not None:
            return error

    if request.method == "POST":
        if request.user.is_authenticated:
            user = request.user
            active_role = request.session.get('active_role', 'farmer')
            is_manager_mode = (active_role == 'manager')
            is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('is_ajax') == '1'

            # ---------------------------------------------------------
            # MANAGER MODE: Update Manager's OWN staff details (NOT Employer)
            # ---------------------------------------------------------
            if is_manager_mode:
                from apps.farmer.models import FarmerStaff
                staff_obj = get_active_manager_staff(request)
                if not staff_obj:
                    return JsonResponse({'success': False, 'error': 'Select an active workspace.'}, status=403)

                name = (request.POST.get('name') or request.POST.get('first_name') or request.POST.get('farmer_name') or '').strip()
                if name:
                    user.first_name = name
                    user.save()
                    if staff_obj:
                        staff_obj.name = name

                mobile_number = (request.POST.get('mobile_number') or '').strip()
                if mobile_number and staff_obj:
                    staff_obj.mobile_number = mobile_number

                email = (request.POST.get('email') or '').strip()
                if email:
                    user.email = email
                    user.save()

                if 'gender' in request.POST and staff_obj:
                    staff_obj.gender = request.POST.get('gender', '').strip()

                dob_val = request.POST.get('date_of_birth') or request.POST.get('dob')
                if dob_val and staff_obj:
                    try:
                        staff_obj.date_of_birth = dob_val
                    except Exception:
                        pass

                if 'address' in request.POST and staff_obj:
                    staff_obj.address = request.POST.get('address', '').strip()

                if 'profile_image' in request.FILES and staff_obj:
                    img_file = request.FILES['profile_image']
                    if img_file.size <= 10 * 1024 * 1024:
                        staff_obj.profile_image = img_file

                if staff_obj:
                    staff_obj.save()

                # Sync user personal profile avatar/name if present
                personal_profile = getattr(user, 'profile', None)
                if personal_profile and hasattr(user, 'profile'):
                    if name:
                        personal_profile.user.first_name = name
                    if 'profile_image' in request.FILES:
                        personal_profile.profile_image = request.FILES['profile_image']
                    personal_profile.save()

                log_action(request, "Manager Profile Updated", "Manager personal profile details updated.", icon_class="ri-user-settings-line", bg_color="primary")

                if is_ajax:
                    return JsonResponse({
                        'success': True,
                        'status': 'success',
                        'message': 'Manager profile updated successfully!',
                        'profile_image_url': staff_obj.profile_image.url if staff_obj and staff_obj.profile_image else ''
                    })

                messages.success(request, "Manager profile updated successfully!")
                return redirect('/farmer/profile_view/#basic_info')

            # ---------------------------------------------------------
            # FARMER MODE: Update Farmer Owner profile
            # ---------------------------------------------------------
            profile = get_farmer_profile(user)
            if not profile:
                profile = getattr(user, 'profile', None)

            # 1. Update user fields
            full_name = (request.POST.get('full_name') or request.POST.get('name') or request.POST.get('first_name') or request.POST.get('farmer_name') or '').strip()
            if full_name:
                user.first_name = full_name
                if 'full_name' in request.POST or 'name' in request.POST or 'farmer_name' in request.POST:
                    user.last_name = ''
                user.save()

            last_name = request.POST.get('last_name')
            if last_name:
                user.last_name = last_name.strip()
                user.save()

            email = request.POST.get('email')
            if email:
                user.email = email.strip()
                user.save()
                if profile:
                    profile.email = email.strip()

            mobile_number = request.POST.get('mobile_number')
            if mobile_number and profile:
                profile.mobile_number = mobile_number.strip()

            # 2. Profile Image Upload
            if 'profile_image' in request.FILES and profile:
                img_file = request.FILES['profile_image']
                if img_file.size <= 10 * 1024 * 1024:
                    profile.profile_image = img_file

            # 3. Farmer Type (Individual vs Company)
            farmer_type = request.POST.get('farmer_type')
            if farmer_type and profile:
                profile.farmer_type = farmer_type

            # 4. Company Specific Information
            if profile:
                if 'company_name' in request.POST:
                    profile.company_name = request.POST.get('company_name', '').strip()
                if 'company_mobile_number' in request.POST:
                    profile.company_mobile_number = request.POST.get('company_mobile_number', '').strip()
                if 'msme_number' in request.POST:
                    profile.msme_number = request.POST.get('msme_number', '').strip()
                if 'msme_certificate' in request.FILES:
                    msme_file = request.FILES['msme_certificate']
                    if msme_file.size <= 10 * 1024 * 1024:
                        profile.msme_certificate = msme_file

                # 5. Personal KYC Details
                if 'aadhaar_number' in request.POST:
                    profile.aadhaar_number = request.POST.get('aadhaar_number', '').strip()
                if 'pmkisan_id' in request.POST:
                    profile.pmkisan_id = request.POST.get('pmkisan_id', '').strip()
                if 'pan_number' in request.POST:
                    profile.pan_number = request.POST.get('pan_number', '').strip()
                
                # Father's / Husband's / Guardian Name
                guardian_val = request.POST.get('guardian_name') or request.POST.get('father_name')
                if guardian_val is not None:
                    profile.guardian_name = guardian_val.strip()
                    profile.father_name = guardian_val.strip()

                if 'gender' in request.POST:
                    profile.gender = request.POST.get('gender', '').strip()
                
                dob_val = request.POST.get('date_of_birth') or request.POST.get('dob')
                if dob_val:
                    try:
                        profile.date_of_birth = dob_val
                        profile.dob = dob_val
                    except Exception:
                        pass

                if 'educational_qualification' in request.POST:
                    profile.educational_qualification = request.POST.get('educational_qualification', '').strip()

                # 6. Location / Address Details
                if 'state' in request.POST:
                    state_val = request.POST.get('state')
                    if state_val:
                        from apps.superadmin.models import State
                        st = State.objects.filter(id=state_val).first() or State.objects.filter(name__iexact=state_val).first()
                        profile.state = st
                    else:
                        profile.state = None

                if 'district' in request.POST:
                    district_val = request.POST.get('district')
                    if district_val:
                        from apps.superadmin.models import City
                        ct = City.objects.filter(id=district_val).first() or City.objects.filter(name__iexact=district_val).first()
                        profile.district = ct
                    else:
                        profile.district = None

                if 'taluka' in request.POST:
                    taluka_val = (request.POST.get('taluka') or '').strip()
                    if taluka_val:
                        if taluka_val.isdigit():
                            from apps.superadmin.models import Taluk
                            t_obj = Taluk.objects.filter(id=taluka_val).first()
                            profile.taluka = t_obj.name if t_obj else taluka_val
                        else:
                            profile.taluka = taluka_val
                    else:
                        profile.taluka = ""
                if 'village' in request.POST:
                    profile.village = request.POST.get('village', '').strip()
                if 'pincode' in request.POST:
                    profile.pincode = request.POST.get('pincode', '').strip()
                if 'address' in request.POST:
                    profile.address = request.POST.get('address', '').strip()

                profile.save()

            log_action(request, "Profile Updated", "Farmer profile information was updated.", icon_class="ri-user-settings-line", bg_color="primary")

            if is_ajax:
                return JsonResponse({
                    'success': True,
                    'status': 'success',
                    'message': 'Profile updated successfully!',
                    'profile_image_url': profile.profile_image.url if profile and profile.profile_image else ''
                })

            messages.success(request, "Profile updated successfully!")
            referer = request.META.get('HTTP_REFERER')
            if referer:
                return redirect(referer)
            return redirect('farmer-profile')

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    return redirect('farmer-profile')


def send_real_sms(mobile_number, otp_code):
    # Try MSG91 first (matching working superadmin settings)
    SMS_TEMPLATE = {
        'authkey': '155099Ajzgw9B8dfq6737236aP1',
        'sender_id': 'ELYSIU',
        'template_id': '1007049094377493090',
        'country_code': '91',
        'message_content': 'Dear User, Your One Time Password (OTP) for logging into the Elysium Academy Application is {#var#}'
    }

    formatted_mobile_number = SMS_TEMPLATE['country_code'] + str(mobile_number)
    message_content = SMS_TEMPLATE['message_content'].replace("{#var#}", str(otp_code))

    url = f"https://api.msg91.com/api/sendhttp.php?authkey={SMS_TEMPLATE['authkey']}&sender={SMS_TEMPLATE['sender_id']}&route=default&message={message_content}&mobiles={formatted_mobile_number}&DLT_TE_ID={SMS_TEMPLATE['template_id']}"

    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            res_text = res.text.strip()
            if res_text and len(res_text) > 10:
                return True
    except Exception as e:
        print(f"MSG91 SMS gateway error: {e}")

    # Fallback to Fast2SMS
    fast2sms_api_key = os.environ.get("FAST2SMS_API_KEY") or getattr(settings, "FAST2SMS_API_KEY", None)
    if fast2sms_api_key:
        try:
            url = "https://www.fast2sms.com/dev/bulkV2"
            payload = {
                "authorization": fast2sms_api_key,
                "message": f"Your AgriOT verification code is: {otp_code}",
                "route": "q",
                "numbers": mobile_number,
            }
            headers = {
                "authorization": fast2sms_api_key,
                "cache-control": "no-cache"
            }
            res = requests.get(url, params=payload, headers=headers, timeout=5)
            return res.json().get("return") == True
        except Exception as e:
            print(f"Fast2SMS error: {e}")

    # Fallback to Twilio
    twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    twilio_token = os.environ.get("TWILIO_AUTH_TOKEN")
    twilio_from = os.environ.get("TWILIO_FROM_NUMBER")
    if twilio_sid and twilio_token and twilio_from:
        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
            auth = (twilio_sid, twilio_token)
            data = {
                "From": twilio_from,
                "To": f"+91{mobile_number}" if not mobile_number.startswith("+") else mobile_number,
                "Body": f"Your AgriOT verification code is: {otp_code}"
            }
            res = requests.post(url, auth=auth, data=data, timeout=5)
            return res.status_code in [200, 201]
        except Exception as e:
            print(f"Twilio error: {e}")

    return False

def landing_page(request):
    packages = Package.objects.filter(is_active=True).order_by('price')
    return render(request, "farmer/landing_page.html", {"packages": packages})

def send_otp(request):
    if request.method == "POST":
        fullname = request.POST.get("fullname", "").strip()
        mobile_number = request.POST.get("mobile_number", "").strip()
        package_id = request.POST.get("package_id", "").strip()

        if not fullname or not mobile_number or not package_id:
            return JsonResponse({"status": "error", "message": "All fields are required."}, status=400)

        # Generate random 6-digit OTP
        otp_code = str(random.randint(100000, 999999))

        # Check if user already exists
        user = User.objects.filter(username=mobile_number).first()
        if not user:
            user = User.objects.create_user(
                username=mobile_number,
                first_name=fullname
            )
            user.set_unusable_password()
            user.save()
        else:
            user.first_name = fullname
            user.save()

        profile, created = Profile.objects.get_or_create(user=user)
        profile.role = 'farmer'
        profile.mobile_number = mobile_number
        profile.otp = otp_code
        profile.save()

        # Try sending real SMS
        sms_sent = send_real_sms(mobile_number, otp_code)

        response_data = {
            "status": "success",
            "message": "OTP sent successfully.",
            "mobile_number": mobile_number,
            "sms_sent": sms_sent
        }

        return JsonResponse(response_data)
    return JsonResponse({"status": "error", "message": "Invalid request method."}, status=400)

def verify_otp_and_payment(request):
    if request.method == "POST":
        mobile_number = request.POST.get("mobile_number", "").strip()
        otp = request.POST.get("otp", "").strip()
        package_id = request.POST.get("package_id", "").strip()

        if not mobile_number or not otp or not package_id:
            return JsonResponse({"status": "error", "message": "Verification parameters missing."}, status=400)

        user = User.objects.filter(username=mobile_number).first()
        if not user:
            return JsonResponse({"status": "error", "message": "User not found."}, status=404)

        profile, created = Profile.objects.get_or_create(user=user)
        if created or not profile.role:
            profile.role = 'farmer'
            profile.mobile_number = mobile_number
            profile.save()

        if profile.otp != otp:
            return JsonResponse({"status": "error", "message": "Invalid verification code."}, status=400)

        # Verify user
        profile.is_verified = True
        profile.save()

        # Fetch package
        package = Package.objects.filter(id=package_id).first()
        if not package:
            return JsonResponse({"status": "error", "message": "Selected plan not found."}, status=404)

        # Create Razorpay Order
        from apps.superadmin.razorpay_utils import create_razorpay_order, get_razorpay_credentials
        from apps.superadmin.subscription_utils import get_package_cycle_price, normalize_billing_cycle
        billing_cycle = normalize_billing_cycle(request.POST.get("billing_cycle", "monthly"))
        creds = get_razorpay_credentials()
        key_id = creds["key_id"]
        amount_paise = int(round(get_package_cycle_price(package, billing_cycle) * 100))
        order_id, is_live, err_msg = create_razorpay_order(
            amount_paise=amount_paise,
            receipt=f"rcpt_{profile.id}_{package.id}",
            notes={"package_name": package.name, "mobile_number": profile.mobile_number, "billing_cycle": billing_cycle}
        )

        return JsonResponse({
            "status": "otp_verified",
            "key_id": key_id,
            "order_id": order_id,
            "amount": amount_paise,
            "package_name": package.name,
            "fullname": user.first_name,
            "mobile_number": profile.mobile_number,
            "package_id": package.id,
            "billing_cycle": billing_cycle,
        })
    return JsonResponse({"status": "error", "message": "Invalid request method."}, status=400)


def verify_payment(request):
    if request.method == "POST":
        from apps.superadmin.subscription_utils import (
            billing_cycle_label,
            complete_farmer_subscription_payment,
            mark_pending_subscription_status,
            normalize_billing_cycle,
        )

        status_action = (request.POST.get("payment_status") or request.POST.get("status") or "").strip().lower()
        razorpay_payment_id = request.POST.get("razorpay_payment_id", "").strip()
        razorpay_order_id = request.POST.get("razorpay_order_id", "").strip()
        razorpay_signature = request.POST.get("razorpay_signature", "").strip()
        package_id = request.POST.get("package_id", "").strip()
        mobile_number = request.POST.get("mobile_number", "").strip()
        billing_cycle = normalize_billing_cycle(request.POST.get("billing_cycle", "monthly"))
        is_renewal = request.POST.get("is_renewal", "").strip().lower() in ["true", "1"]

        # Retrieve user and profile
        if request.user.is_authenticated:
            user = request.user
            profile = get_farmer_profile(user)
        else:
            from apps.superadmin.phone_utils import find_profile_by_phone
            profile = find_profile_by_phone(mobile_number, role='farmer')
            if profile:
                user = profile.user
            else:
                user = User.objects.filter(username=mobile_number).first()
                if not user:
                    return JsonResponse({"status": "error", "message": "User not found."}, status=404)
                profile = get_farmer_profile(user)

        if not profile:
            profile, created = Profile.objects.get_or_create(user=user)
            if created or not profile.role:
                profile.role = 'farmer'
                profile.mobile_number = mobile_number
                profile.save()

        # Explicit payment failure / dismissal notification from checkout UI
        if status_action in ("failed", "cancelled", "canceled"):
            mark_pending_subscription_status(profile, razorpay_order_id=razorpay_order_id, new_status=status_action)
            return JsonResponse({"status": status_action, "message": "Pending payment recorded as " + status_action + "."})

        if not razorpay_payment_id or not razorpay_order_id or not package_id or not mobile_number:
            return JsonResponse({"status": "error", "message": "Payment details missing."}, status=400)

        package = Package.objects.filter(id=package_id).first()
        if not package:
            return JsonResponse({"status": "error", "message": "Selected plan not found."}, status=404)

        success, err_msg, subscription, _ = complete_farmer_subscription_payment(
            farmer_profile=profile,
            package=package,
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
            billing_cycle=billing_cycle,
            is_renewal=is_renewal,
            mobile_number=mobile_number,
            request=request,
        )
        if not success or not subscription:
            return JsonResponse({"status": "error", "message": err_msg or "Payment verification failed."}, status=400)

        # Log the user in if not already authenticated
        if not request.user.is_authenticated:
            user.backend = 'django.contrib.auth.backends.ModelBackend'
            login(request, user)

        request.session['active_role'] = 'farmer'
        request.session['active_farmer_id'] = profile.id
        request.session['active_staff_id'] = None

        sub_cycle = normalize_billing_cycle(subscription.billing_cycle)
        return JsonResponse({
            "status": "success",
            "data": {
                "subscription_id": subscription.id,
                "fullname": user.get_full_name() or user.first_name,
                "mobile_number": profile.mobile_number,
                "package_name": subscription.package.name if subscription.package else package.name,
                "package_price": float(subscription.monthly_amount or 0),
                "amount_paid": float(subscription.effective_amount_paid or 0),
                "billing_cycle": sub_cycle,
                "billing_cycle_label": billing_cycle_label(sub_cycle),
                "start_date": subscription.start_date.strftime("%d-%b-%Y") if subscription.start_date else "",
                "end_date": subscription.end_date.strftime("%d-%b-%Y") if subscription.end_date else "",
                "dashboard_url": "/profile_view/#basic_info"
            }
        })
    return JsonResponse({"status": "error", "message": "Invalid request method."}, status=400)


# -------------------------
# FARMER REGISTRATION FLOW
# -------------------------

def farmer_register(request):
    """
    Renders the Farmer Registration onboarding page.
    Flow: Step 1: Mobile & OTP -> Step 2: Basic Profile Data -> Step 3: Subscription & Dashboard Access
    """
    from apps.superadmin.models import Package, State
    packages = Package.objects.filter(is_active=True).order_by('price')
    states = State.objects.filter(status="0").order_by('name')
    if not states.exists():
        states = State.objects.all().order_by('name')

    prefill_mobile = (request.GET.get('mobile') or '').strip()
    prefill_package_id = (request.GET.get('package_id') or '').strip()
    prefill_cycle = (request.GET.get('cycle') or 'monthly').strip().lower()

    context = {
        'packages': packages,
        'states': states,
        'prefill_mobile': prefill_mobile,
        'prefill_package_id': prefill_package_id,
        'prefill_cycle': prefill_cycle,
    }
    return render(request, "farmer/register.html", context)


def register_send_otp(request):
    """
    Step 1: Validates mobile number and sends SMS OTP for registration.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid request method."}, status=405)

    mobile = (request.POST.get('mobile') or '').strip()
    clean_digits = re.sub(r'[^0-9]', '', mobile)
    if len(clean_digits) > 10 and clean_digits.startswith('91'):
        clean_digits = clean_digits[2:]

    if not clean_digits or len(clean_digits) < 10 or len(clean_digits) > 15:
        return JsonResponse({"status": "error", "message": "Please enter a valid 10-digit mobile number."}, status=400)

    # Check if number is already registered as an active farmer
    from apps.superadmin.phone_utils import find_profile_by_phone, find_staff_by_phone
    existing_prof = find_profile_by_phone(clean_digits, role='farmer')
    if existing_prof:
        return JsonResponse({
            "status": "error",
            "already_registered": True,
            "message": "This mobile number is already registered. Please login instead."
        }, status=400)

    # Check cooldown (30s)
    last_otp_time = request.session.get('reg_otp_time', 0)
    current_time = timezone.now().timestamp()
    if current_time - last_otp_time < 30:
        remaining = max(1, int(30 - (current_time - last_otp_time)))
        return JsonResponse({
            "status": "error",
            "message": f"Please wait {remaining} seconds before requesting a new OTP."
        }, status=429)

    # Generate 6-digit OTP
    otp = str(random.randint(100000, 999999))

    # Send SMS
    from apps.superadmin.views import send_otp_sms
    success, message = send_otp_sms(clean_digits, otp)
    if not success:
        return JsonResponse({"status": "error", "message": f"Failed to send SMS: {message}"}, status=500)

    # Save to session
    request.session['reg_mobile'] = clean_digits
    request.session['reg_otp'] = otp
    request.session['reg_otp_time'] = current_time
    request.session['reg_verified'] = False

    return JsonResponse({
        "status": "success",
        "message": f"OTP sent successfully to +91 {clean_digits}.",
        "mobile": clean_digits
    })


def register_resend_otp(request):
    """
    Resends OTP for registration with cooldown checking.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid request method."}, status=405)

    mobile = request.session.get('reg_mobile')
    if not mobile:
        return JsonResponse({"status": "error", "message": "Session expired. Please enter your mobile number again."}, status=400)

    last_otp_time = request.session.get('reg_otp_time', 0)
    current_time = timezone.now().timestamp()
    if current_time - last_otp_time < 30:
        remaining = max(1, int(30 - (current_time - last_otp_time)))
        return JsonResponse({"status": "error", "message": f"Please wait {remaining}s before requesting new OTP."}, status=429)

    otp = str(random.randint(100000, 999999))
    from apps.superadmin.views import send_otp_sms
    success, message = send_otp_sms(mobile, otp)
    if not success:
        return JsonResponse({"status": "error", "message": f"Failed to send SMS: {message}"}, status=500)

    request.session['reg_otp'] = otp
    request.session['reg_otp_time'] = current_time

    return JsonResponse({
        "status": "success",
        "message": f"New OTP sent successfully to +91 {mobile}."
    })


def register_verify_otp(request):
    """
    Step 1 Completion: Verifies entered 6-digit OTP.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid request method."}, status=405)

    otp_entered = (request.POST.get('otp') or '').strip()
    session_otp = request.session.get('reg_otp')
    otp_time = request.session.get('reg_otp_time')
    mobile = request.session.get('reg_mobile')

    if not session_otp or not otp_time or not mobile:
        return JsonResponse({"status": "error", "message": "Session expired. Please start registration again."}, status=400)

    if timezone.now().timestamp() - otp_time > 600: # 10 mins
        return JsonResponse({"status": "error", "message": "OTP has expired. Please request a new one."}, status=400)

    if otp_entered != session_otp:
        return JsonResponse({"status": "error", "message": "Invalid OTP. Please check and try again."}, status=400)

    request.session['reg_verified'] = True

    return JsonResponse({
        "status": "success",
        "message": "Mobile number verified successfully!",
        "mobile": mobile
    })


def register_save_profile(request):
    """
    Step 2: Saves the basic profile data for the newly registered farmer.
    """
    if request.method == "POST":
        from apps.farmer.validation import pincode_error
        error = pincode_error(request)
        if error is not None:
            return error

    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid request method."}, status=405)

    if not request.session.get('reg_verified'):
        return JsonResponse({"status": "error", "message": "Please verify your mobile number with OTP first."}, status=403)

    mobile = request.session.get('reg_mobile')
    if not mobile:
        return JsonResponse({"status": "error", "message": "Session expired. Please start again."}, status=400)

    full_name = (request.POST.get('full_name') or request.POST.get('first_name') or '').strip()
    if not full_name:
        return JsonResponse({"status": "error", "message": "Full Name is required."}, status=400)


    email = (request.POST.get('email') or '').strip()
    farmer_type = (request.POST.get('farmer_type') or 'individual').strip().lower()
    company_name = (request.POST.get('company_name') or '').strip() if farmer_type == 'company' else ''
    state_id = request.POST.get('state_id') or None
    district_id = request.POST.get('district_id') or None
    taluk_val = (request.POST.get('taluk') or '').strip()
    village = (request.POST.get('village') or '').strip()
    pincode = (request.POST.get('pincode') or '').strip()
    address = (request.POST.get('address') or '').strip()

    from auth.models import Profile
    from apps.superadmin.models import State, City, Taluk

    state_obj = None
    if state_id:
        state_obj = State.objects.filter(id=state_id).first()

    district_obj = None
    if district_id:
        district_obj = City.objects.filter(id=district_id).first()

    taluk_name = taluk_val
    if taluk_val and taluk_val.isdigit():
        t_obj = Taluk.objects.filter(id=taluk_val).first()
        if t_obj:
            taluk_name = t_obj.name

    user = User.objects.filter(username=mobile).first()
    if not user:
        user = User.objects.create(username=mobile)
    user.first_name = full_name
    user.last_name = ''
    if email:
        user.email = email
    user.set_unusable_password()
    user.save()

    profile, created = Profile.objects.get_or_create(user=user)
    profile.role = 'farmer'
    profile.mobile_number = mobile
    if email:
        profile.email = email
    profile.farmer_type = farmer_type
    if company_name:
        profile.company_name = company_name
    profile.state = state_obj
    profile.district = district_obj
    profile.taluka = taluk_name
    profile.village = village
    profile.pincode = pincode
    profile.address = address
    profile.is_verified = True
    profile.status = 'Active'
    profile.save()

    try:
        if profile.email:
            from apps.superadmin.email_utils import send_welcome_email
            send_welcome_email(profile, request=request)
    except Exception as e:
        print(f"[Email Trigger Error - Welcome]: {e}")

    request.session['reg_user_id'] = user.id

    return JsonResponse({
        "status": "success",
        "message": "Basic profile saved successfully!",
        "data": {
            "user_id": user.id,
            "fullname": user.get_full_name() or user.first_name,
            "mobile": mobile
        }
    })


def register_create_order(request):
    """
    Step 3: Creates Razorpay order for the selected subscription package & billing cycle.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid request method."}, status=405)

    from apps.superadmin.models import Package
    from apps.superadmin.subscription_utils import initiate_farmer_subscription_order, normalize_billing_cycle

    package_id = request.POST.get('package_id')
    billing_cycle = normalize_billing_cycle(request.POST.get('billing_cycle') or 'monthly')

    package = Package.objects.filter(id=package_id).first()
    if not package:
        return JsonResponse({"status": "error", "message": "Selected plan not found."}, status=404)

    user_id = request.session.get('reg_user_id')
    mobile = request.session.get('reg_mobile')
    user = None
    if user_id:
        user = User.objects.filter(id=user_id).first()
    if not user and mobile:
        user = User.objects.filter(username=mobile).first()

    if not user:
        return JsonResponse({"status": "error", "message": "User session not found. Please complete profile details first."}, status=400)

    profile = getattr(user, 'profile', None)
    if not profile:
        from auth.models import Profile
        profile, _ = Profile.objects.get_or_create(user=user, defaults={'role': 'farmer', 'mobile_number': mobile or user.username})

    success, err_msg, _, order_payload = initiate_farmer_subscription_order(
        farmer_profile=profile,
        user=user,
        target_package=package,
        target_cycle=billing_cycle,
        is_renewal=False,
    )
    if not success or not order_payload:
        return JsonResponse({"status": "error", "message": err_msg}, status=400)

    return JsonResponse({
        "status": "success",
        "key_id": order_payload['key_id'],
        "order_id": order_payload['order_id'],
        "amount": order_payload['amount'],
        "price_inr": order_payload['net_payable'],
        "package_name": order_payload['package_name'],
        "package_id": order_payload['package_id'],
        "billing_cycle": order_payload['billing_cycle'],
        "fullname": order_payload['fullname'],
        "mobile_number": order_payload['mobile_number'],
    })


def guidelines(request):
    return redirect('/farmer/manage_market/#schedule_tracker')


def switch_plan(request):
    import json
    from django.http import JsonResponse

    from apps.superadmin.models import Package
    from apps.superadmin.subscription_utils import (
        calculate_plan_switch_quote,
        cancel_scheduled_subscription_switch,
        initiate_farmer_subscription_order,
        normalize_billing_cycle,
        schedule_subscription_switch,
    )

    if request.method == 'POST' and request.user.is_authenticated:
        try:
            if request.content_type and 'application/json' in request.content_type:
                data = json.loads(request.body)
            else:
                data = request.POST

            farmer_profile = get_farmer_profile(request.user)
            if not farmer_profile:
                return JsonResponse({'success': False, 'error': 'Farmer profile not found.'}, status=403)

            action = str(data.get('action') or 'initiate').strip().lower()

            if action == 'cancel_scheduled_switch':
                ok, msg = cancel_scheduled_subscription_switch(farmer_profile)
                return JsonResponse({'success': ok, 'message': msg} if ok else {'success': False, 'error': msg})

            package_id = data.get('package_id')
            is_renewal = str(data.get('is_renewal', False)).strip().lower() in ('true', '1', 'yes')
            billing_cycle = normalize_billing_cycle(data.get('billing_cycle') or 'monthly')
            package = Package.objects.filter(id=package_id).first()
            if not package:
                return JsonResponse({'success': False, 'error': 'Package not found'}, status=404)

            if action == 'quote':
                quote = calculate_plan_switch_quote(
                    farmer_profile, package, target_cycle=billing_cycle, is_renewal=is_renewal
                )
                return JsonResponse({'success': True, 'quote': quote})

            if action == 'schedule_switch':
                ok, msg, quote = schedule_subscription_switch(farmer_profile, package, target_cycle=billing_cycle)
                if not ok:
                    return JsonResponse({'success': False, 'error': msg, 'quote': quote}, status=400)
                return JsonResponse({'success': True, 'scheduled': True, 'message': msg, 'quote': quote})

            ok, err_msg, quote, order_payload = initiate_farmer_subscription_order(
                farmer_profile=farmer_profile,
                user=request.user,
                target_package=package,
                target_cycle=billing_cycle,
                is_renewal=is_renewal,
            )
            if not ok or not order_payload:
                return JsonResponse({'success': False, 'error': err_msg, 'quote': quote}, status=400)

            return JsonResponse({
                'success': True,
                **order_payload,
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)



# -------------------------
# DOCUMENT MANAGEMENT
# -------------------------
def manage_documents(request):
    from apps.superadmin.models import Document
    farmer = get_farmer_profile(request.user)

    if not farmer:
        messages.error(request, "Farmer profile required.")
        return redirect("farmer-dashboard")

    if request.method == "POST":
        is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or request.POST.get("is_ajax") == "1"
        action = request.POST.get("action", "upload")

        if action == "upload":
            title = request.POST.get("title", "").strip()
            category = request.POST.get("category", "General").strip()
            description = request.POST.get("description", "").strip()
            doc_file = request.FILES.get("file")

            if not title or not doc_file:
                if is_ajax:
                    return JsonResponse({"success": False, "status": "error", "message": "Document Title and File are required."}, status=400)
                messages.error(request, "Document Title and File are required.")
                return redirect(request.META.get('HTTP_REFERER', 'farmer-documents'))

            MAX_SIZE = 10 * 1024 * 1024
            if doc_file.size > MAX_SIZE:
                if is_ajax:
                    return JsonResponse({"success": False, "status": "error", "message": "File size exceeds the 10MB limit."}, status=400)
                messages.error(request, "File size exceeds the 10MB limit. Please upload a smaller file.")
                return redirect(request.META.get('HTTP_REFERER', 'farmer-documents'))

            ext = os.path.splitext(doc_file.name)[1].lower()

            doc = Document.objects.create(
                title=title,
                category=category,
                description=description,
                file=doc_file,
                file_size_bytes=doc_file.size,
                file_type=ext,
                uploaded_by=request.user,
                farmer=farmer
            )
            log_action(request, "Document Uploaded", f"Document '{title}' was uploaded.", icon_class="ri-file-upload-line", bg_color="success")
            
            if is_ajax:
                return JsonResponse({
                    "success": True,
                    "status": "success",
                    "message": "Document uploaded successfully!",
                    "doc": {
                        "id": doc.id,
                        "title": doc.title,
                        "file_url": doc.file.url,
                        "created_at": doc.created_at.strftime('%d-%b-%Y')
                    }
                })

            messages.success(request, "Document uploaded successfully!")

        elif action == "edit":
            doc_id = request.POST.get("id") or request.POST.get("document_id")
            try:
                doc = Document.objects.get(id=doc_id, farmer=farmer)
                doc.title = request.POST.get("title", doc.title).strip()
                doc.category = request.POST.get("category", doc.category).strip()
                doc.description = request.POST.get("description", doc.description).strip()

                new_file = request.FILES.get("file")
                if new_file:
                    if new_file.size > 10 * 1024 * 1024:
                        if is_ajax:
                            return JsonResponse({"success": False, "status": "error", "message": "File size exceeds 10MB."}, status=400)
                        messages.error(request, "File size exceeds the 10MB limit. Please upload a smaller file.")
                        return redirect(request.META.get('HTTP_REFERER', 'farmer-documents'))
                    doc.file = new_file
                    doc.file_size_bytes = new_file.size
                    doc.file_type = os.path.splitext(new_file.name)[1].lower()

                doc.save()
                if is_ajax:
                    return JsonResponse({"success": True, "status": "success", "message": "Document updated successfully!"})
                messages.success(request, "Document updated successfully!")
            except Document.DoesNotExist:
                if is_ajax:
                    return JsonResponse({"success": False, "status": "error", "message": "Document not found."}, status=404)
                messages.error(request, "Document not found.")

        elif action == "delete":
            doc_id = request.POST.get("id") or request.POST.get("document_id")
            try:
                doc = Document.objects.get(id=doc_id, farmer=farmer)
                if doc.file and os.path.isfile(doc.file.path):
                    try:
                        os.remove(doc.file.path)
                    except Exception:
                        pass
                doc.delete()
                if is_ajax:
                    return JsonResponse({"success": True, "status": "success", "message": "Document deleted successfully!"})
                messages.success(request, "Document deleted successfully!")
            except Document.DoesNotExist:
                if is_ajax:
                    return JsonResponse({"success": False, "status": "error", "message": "Document not found."}, status=404)
                messages.error(request, "Document not found.")

        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        return redirect("farmer-documents")

    documents = Document.objects.filter(farmer=farmer).order_by("-created_at")
    from apps.superadmin.models import DocumentCategory
    doc_categories = DocumentCategory.objects.filter(is_active=True).order_by("document_type", "name")

    return render(request, "farmer/documents/manage_documents.html", {
        "farmer": farmer,
        "documents": documents,
        "doc_categories": doc_categories,
    })


# -------------------------
# FARMER REPORTS
# -------------------------
def farmer_reports_dashboard(request):
    from apps.farmer.models import (
        FarmerCustomer, Farm, LedgerTransaction, FarmerVendor,
        StaffPayroll, StaffAttendance, StaffAdvance
    )

    farmer_prof = get_farmer_profile(request.user)

    customers_count = 0
    farms_count = 0
    total_area = 0.0
    expenses_count = 0
    total_expense_amount = 0.0
    vendors_count = 0
    payroll_count = 0
    total_salary_amount = 0.0
    attendance_count = 0
    advance_count = 0
    total_advance_amount = 0.0

    recent_customers = []
    recent_farms = []
    recent_expenses = []
    recent_vendors = []
    recent_payrolls = []
    recent_attendances = []
    recent_advances = []

    if farmer_prof:
        customers_qs = FarmerCustomer.objects.filter(farmer=farmer_prof).order_by('-id')
        customers_count = customers_qs.count()
        recent_customers = list(customers_qs[:4])

        farms_qs = Farm.objects.filter(farmer=farmer_prof).order_by('-id')
        farms_count = farms_qs.count()
        recent_farms = list(farms_qs[:4])
        for f in farms_qs:
            try:
                total_area += float(f.total_area or 0)
            except (ValueError, TypeError):
                pass

        expenses_qs = LedgerTransaction.objects.filter(farm__farmer=farmer_prof, amount__lt=0).order_by('-close_date', '-id')
        expenses_count = expenses_qs.count()
        recent_expenses = list(expenses_qs[:4])
        for e in expenses_qs:
            try:
                total_expense_amount += abs(float(e.amount or 0))
            except (ValueError, TypeError):
                pass

        vendors_qs = FarmerVendor.objects.filter(farmer=farmer_prof).order_by('-id')
        vendors_count = vendors_qs.count()
        recent_vendors = list(vendors_qs[:4])

        payroll_qs = StaffPayroll.objects.filter(staff__farmer=farmer_prof).order_by('-paid_on', '-id')
        payroll_count = payroll_qs.count()
        recent_payrolls = list(payroll_qs[:4])
        for p in payroll_qs:
            try:
                total_salary_amount += float(p.net_pay or 0)
            except (ValueError, TypeError):
                pass

        attendance_qs = StaffAttendance.objects.filter(staff__farmer=farmer_prof).order_by('-date', '-id')
        attendance_count = attendance_qs.count()
        recent_attendances = list(attendance_qs[:4])

        advance_qs = StaffAdvance.objects.filter(staff__farmer=farmer_prof).order_by('-date', '-id')
        advance_count = advance_qs.count()
        recent_advances = list(advance_qs[:4])
        for a in advance_qs:
            try:
                total_advance_amount += float(a.amount or 0)
            except (ValueError, TypeError):
                pass

    context = {
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
    return render(request, "farmer/reports/reports_dashboard.html", context)


def farmer_customer_report(request):
    from apps.farmer.models import FarmerCustomer
    farmer_prof = get_farmer_profile(request.user)
    records = FarmerCustomer.objects.filter(farmer=farmer_prof).order_by('-id') if farmer_prof else FarmerCustomer.objects.none()
    return render(request, "farmer/reports/customer_report.html", {"records": records})

def farmer_land_report(request):
    from apps.farmer.models import Farm
    farmer_prof = get_farmer_profile(request.user)
    records = Farm.objects.filter(farmer=farmer_prof).order_by('-id') if farmer_prof else Farm.objects.none()
    return render(request, "farmer/reports/land_report.html", {"records": records})

def farmer_expense_report(request):
    from apps.farmer.models import LedgerTransaction
    farmer_prof = get_farmer_profile(request.user)
    records = LedgerTransaction.objects.filter(farm__farmer=farmer_prof, amount__lt=0).order_by('-close_date') if farmer_prof else LedgerTransaction.objects.none()
    return render(request, "farmer/reports/expense_report.html", {"records": records})

def farmer_vendor_report(request):
    from apps.farmer.models import FarmerVendor
    farmer_prof = get_farmer_profile(request.user)
    records = FarmerVendor.objects.filter(farmer=farmer_prof).order_by('-id') if farmer_prof else FarmerVendor.objects.none()
    return render(request, "farmer/reports/vendor_report.html", {"records": records})

def farmer_salary_report(request):
    from apps.farmer.models import StaffPayroll
    farmer_prof = get_farmer_profile(request.user)
    records = StaffPayroll.objects.filter(staff__farmer=farmer_prof).order_by('-id') if farmer_prof else StaffPayroll.objects.none()
    return render(request, "farmer/reports/salary_report.html", {"records": records})

def farmer_attendance_report(request):
    from apps.farmer.models import StaffAttendance, FarmerStaff
    farmer_prof = get_farmer_profile(request.user)
    if not farmer_prof:
        return render(request, "farmer/reports/attendance_report.html", {
            "records": [],
            "staff_summary": [],
            "staff_list": [],
            "total_staff": 0,
            "total_present": 0,
            "total_absent": 0,
            "total_halfday": 0,
            "overall_rate": 0
        })

    staff_list = FarmerStaff.objects.filter(farmer=farmer_prof).order_by('name')
    records = StaffAttendance.objects.filter(staff__farmer=farmer_prof).select_related('staff', 'staff__designation').order_by('-date', '-id')

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
        "staff_summary": staff_summary,
        "staff_list": staff_list,
        "total_staff": staff_list.count(),
        "total_present": total_present_count,
        "total_absent": total_absent_count,
        "total_halfday": total_halfday_count,
        "overall_rate": overall_rate,
    }
    return render(request, "farmer/reports/attendance_report.html", context)

def farmer_advance_report(request):
    from apps.farmer.models import StaffAdvance
    farmer_prof = get_farmer_profile(request.user)
    records = StaffAdvance.objects.filter(staff__farmer=farmer_prof).order_by('-date', '-id') if farmer_prof else StaffAdvance.objects.none()
    return render(request, "farmer/reports/advance_report.html", {"records": records})



def share_document_view(request, token):
    from apps.superadmin.models import Document
    try:
        doc = Document.objects.get(uuid_token=token)
        return render(request, "documents/share_document.html", {"document": doc})
    except Document.DoesNotExist:
        return render(request, "documents/share_document.html", {"error": "Document link is invalid or has been removed."})
