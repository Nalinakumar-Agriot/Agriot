import re
from typing import Optional, List
from django.contrib.auth.models import User
from auth.models import Profile


def normalize_phone_variants(phone_input: str) -> List[str]:
    """
    Returns a list of possible phone number string variants to search for in the database.
    Handles international format (+1234..., 001234...), national numbers (7548834011),
    and country-code prefixed numbers (917548834011, +917548834011).
    """
    if not phone_input:
        return []

    raw = str(phone_input).strip()
    # Remove whitespace, dashes, parens, dots
    cleaned = re.sub(r'[\s\-\(\)\.]', '', raw)
    digits = re.sub(r'\D', '', cleaned)

    if not digits:
        return [raw]

    variants = set()
    variants.add(raw)
    variants.add(cleaned)
    variants.add(digits)
    variants.add('+' + digits)

    # If starts with '00' (international call prefix, e.g. 00917548834011 or 0012025550123)
    if digits.startswith('00') and len(digits) > 2:
        without_00 = digits[2:]
        variants.add(without_00)
        variants.add('+' + without_00)

    # If starts with domestic trunk prefix '0' (e.g. 07548834011 or 07911123456)
    if digits.startswith('0') and len(digits) > 1:
        without_0 = digits[1:]
        variants.add(without_0)
        variants.add('+' + without_0)

    # Indian number heuristics (standard 10 digits or 91 + 10 digits)
    if len(digits) == 10:
        variants.add('91' + digits)
        variants.add('+91' + digits)
    elif len(digits) == 12 and digits.startswith('91'):
        variants.add(digits[2:])  # 10-digit without 91
        variants.add('+91' + digits[2:])

    return list(variants)


def find_profile_by_phone(phone_input: str, role: Optional[str] = None) -> Optional[Profile]:
    """
    Finds a Profile by matching against mobile_number, user__username, or user__email.
    Checks all normalized international & national phone variants.
    """
    if not phone_input:
        return None

    raw = str(phone_input).strip()
    variants = normalize_phone_variants(raw)

    qs = Profile.objects.select_related('user')
    if role:
        qs = qs.filter(role=role)

    # 1. Match mobile_number across variants
    profile = qs.filter(mobile_number__in=variants).first()
    if profile:
        return profile

    # 2. Match user username
    profile = qs.filter(user__username__in=variants).first()
    if profile:
        return profile

    # 3. Match email
    profile = qs.filter(user__email__iexact=raw).first() or qs.filter(email__iexact=raw).first()
    if profile:
        return profile

    return None


def find_staff_by_phone(phone_input: str):
    """
    Finds a FarmerStaff by matching against mobile_number or user__username across variants.
    """
    if not phone_input:
        return None
    from apps.farmer.models import FarmerStaff
    variants = normalize_phone_variants(phone_input)
    from django.db.models import Q
    matches = FarmerStaff.objects.filter(
        Q(mobile_number__in=variants) | Q(user__username__in=variants)
    ).select_related('user')
    active_managers = matches.filter(status='0').filter(
        Q(is_role_manager=True) | Q(role__iexact='manager')
    )
    return active_managers.order_by('pk').first() or matches.order_by('pk').first()


def format_phone_for_sms(phone_input: str, default_country_code: str = '91') -> str:
    """
    Formats the phone number for SMS delivery (e.g. MSG91).
    Returns digits with country code, without '+' sign.
    """
    raw = str(phone_input).strip()
    digits = re.sub(r'\D', '', raw)
    if raw.startswith('+'):
        return digits
    if len(digits) == 10:
        return default_country_code + digits
    return digits


def check_mobile_already_exists(phone_input: str, exclude_user_id=None, exclude_profile_id=None, exclude_staff_id=None):
    """
    Checks if a mobile number is already taken by ANY user or staff in the system:
    - FarmerStaff (Employee / Staff / Manager)
    - Profile (Farmer, Superadmin, Admin, Staff, etc.)
    - User (username matching phone)

    Returns:
      (True, message) if mobile number is already registered.
      (False, None) if mobile number is available.
    """
    if not phone_input:
        return False, None

    raw = str(phone_input).strip()
    variants = normalize_phone_variants(raw)
    if not variants:
        return False, None

    from apps.farmer.models import FarmerStaff
    from auth.models import Profile

    # 1. Check FarmerStaff (Employee / Manager)
    staff_qs = FarmerStaff.objects.filter(mobile_number__in=variants)
    if exclude_staff_id:
        try:
            staff_qs = staff_qs.exclude(id=int(exclude_staff_id))
        except (ValueError, TypeError):
            pass
    if exclude_user_id:
        try:
            staff_qs = staff_qs.exclude(user_id=int(exclude_user_id))
        except (ValueError, TypeError):
            pass
    existing_staff = staff_qs.first()
    if existing_staff:
        role_label = "Employee" if not existing_staff.is_role_manager else "Manager"
        return True, f"Mobile number '{raw}' is already registered with an existing {role_label}."

    # 2. Check Profile (Farmer / Admin / Staff / etc.)
    profile_qs = Profile.objects.filter(mobile_number__in=variants)
    if exclude_profile_id:
        try:
            profile_qs = profile_qs.exclude(id=int(exclude_profile_id))
        except (ValueError, TypeError):
            pass
    if exclude_user_id:
        try:
            profile_qs = profile_qs.exclude(user_id=int(exclude_user_id))
        except (ValueError, TypeError):
            pass
    existing_profile = profile_qs.select_related('user').first()
    if existing_profile:
        role_name = (existing_profile.role or 'User').capitalize()
        return True, f"Mobile number '{raw}' is already registered with an existing {role_name}."

    # 3. Check User (username matching phone)
    user_qs = User.objects.filter(username__in=variants)
    if exclude_user_id:
        try:
            user_qs = user_qs.exclude(id=int(exclude_user_id))
        except (ValueError, TypeError):
            pass
    existing_user = user_qs.first()
    if existing_user:
        return True, f"Mobile number '{raw}' is already registered with an existing User."

    return False, None


def check_farm_staff_mobile(farmer_prof, phone_input: str, exclude_staff_id=None):
    """
    Validates whether a mobile number can be added or updated as a staff/manager in a farm.

    Rules for multi-tenant and workspace architecture:
    1. The farm owner cannot add their own phone number as an employee/manager in their own farm.
    2. A farm cannot have duplicate staff members with the same phone number within the SAME farm.
    3. An existing Farmer (owning another farm), existing User, or staff at another farm
       CAN be added as a Manager or Employee! (Multi-workspace dual-role concept).
    4. Superadmin accounts are protected and cannot be added as farm staff.
    """
    if not phone_input:
        return False, None

    raw = str(phone_input).strip()
    variants = normalize_phone_variants(raw)
    if not variants:
        return False, None

    from apps.farmer.models import FarmerStaff
    from django.contrib.auth.models import User

    # 1. Farm owner self-add check
    if farmer_prof:
        owner_mobile = farmer_prof.mobile_number or (farmer_prof.user.username if farmer_prof.user else '')
        if owner_mobile:
            owner_variants = normalize_phone_variants(owner_mobile)
            if any(v in owner_variants for v in variants):
                return True, "You cannot add yourself (the farm owner) as a staff member in your own farm."

    # 2. Duplicate check within the SAME farm
    if farmer_prof:
        staff_qs = FarmerStaff.objects.filter(farmer=farmer_prof, mobile_number__in=variants)
        if exclude_staff_id:
            try:
                staff_qs = staff_qs.exclude(id=int(exclude_staff_id))
            except (ValueError, TypeError):
                pass
        existing_in_same_farm = staff_qs.first()
        if existing_in_same_farm:
            role_label = "Manager" if existing_in_same_farm.is_role_manager else "Staff Member"
            return True, f"Mobile number '{raw}' is already registered with an existing {role_label} in your farm."

    # 3. Superadmin protection
    if User.objects.filter(username__in=variants, is_superuser=True).exists():
        return True, f"Mobile number '{raw}' belongs to a system administrator and cannot be added."

    return False, None



