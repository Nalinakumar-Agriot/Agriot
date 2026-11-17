"""Resolve ownership and permissions from one validated workspace."""
from django.db.models import Q
from auth.portals import is_admin_user

from apps.farmer.models import FarmerStaff
from apps.superadmin.phone_utils import normalize_phone_variants


def manager_assignments(user):
    if not user or not user.is_authenticated or not user.is_active or is_admin_user(user):
        return FarmerStaff.objects.none()
    profile = getattr(user, 'profile', None)
    phone = getattr(profile, 'mobile_number', None) or user.username
    # Legacy assignments may not have a user link. Never adopt another user's link.
    identity = Q(user=user) | Q(user__isnull=True, mobile_number__in=normalize_phone_variants(phone))
    return FarmerStaff.objects.filter(identity, status='0').filter(
        Q(is_role_manager=True) | Q(role__iexact='manager')
    ).exclude(farmer__status__in=['1', 'Inactive', 'inactive']).select_related('farmer', 'farmer__user')


def resolve_workspace(target):
    request = target if hasattr(target, 'user') else getattr(target, '_current_request', None)
    user = request.user if request is not None else target
    if not user or not getattr(user, 'is_authenticated', False) or not user.is_active or is_admin_user(user):
        return None, None
    token = getattr(request, 'auth', None)
    if token is not None and hasattr(token, 'get'):
        session = token
    else:
        session = getattr(request, 'session', {})
    role = session.get('active_role')
    profile = getattr(user, 'profile', None)
    owner = profile if profile and profile.role == 'farmer' and not farmer_is_inactive(profile) else None
    if role == 'farmer':
        if owner and str(owner.pk) == str(session.get('active_farmer_id')):
            return owner, None
        return None, None
    if role == 'manager':
        try:
            staff = manager_assignments(user).filter(
                pk=session.get('active_staff_id'), farmer_id=session.get('active_farmer_id')
            ).first()
        except (ValueError, TypeError):
            staff = None
        return (staff.farmer, staff) if staff else (None, None)
    if role:
        return None, None
    assignments = list(manager_assignments(user)[:2])
    # An ambiguous identity must select a workspace before accessing any data.
    if owner and not assignments:
        return owner, None
    if not owner and len(assignments) == 1:
        return assignments[0].farmer, assignments[0]
    return None, None


def farmer_is_inactive(profile):
    return bool(profile and str(profile.status).strip().lower() in {'1', 'inactive'})
