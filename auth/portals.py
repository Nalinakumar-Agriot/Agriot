"""Keep administration accounts separate from farm owners and farm staff."""
from django.contrib.auth.models import User
from django.db.models import Q


def admin_users():
    # Custom administration role labels are supported by Manage Users.
    return User.objects.filter(
        Q(is_superuser=True) | (
            Q(profile__isnull=False) & ~Q(profile__role='')
            & ~Q(profile__role__iexact='farmer') & ~Q(profile__role__iexact='staff')
        )
    )


def is_admin_user(user):
    if not user or not user.is_authenticated:
        return False
    role = (getattr(getattr(user, 'profile', None), 'role', '') or '').strip().lower()
    return user.is_superuser or bool(role and role not in {'farmer', 'staff'})


def is_primary_admin(user):
    profile = getattr(user, 'profile', None)
    return bool(user and user.is_authenticated and (
        user.is_superuser or user.email == 'admin@agriot.com'
        or getattr(profile, 'role', '').lower() == 'superadmin'
    ))


def admin_can_access(user, permission):
    if is_primary_admin(user):
        return True
    if not is_admin_user(user):
        return False
    permissions = set(getattr(user.profile, 'permissions', None) or [])
    return permission == 'Dashboards' or 'ALL' in permissions or permission in permissions
