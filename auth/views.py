from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from .models import SystemNotification

@login_required
def mark_all_notifications_read(request):
    role = None
    if hasattr(request.user, 'profile'):
        role = request.user.profile.role

    if role == 'superadmin':
        query = Q(is_read=False)
    else:
        query = Q(user=request.user, is_read=False)
        if role:
            query |= Q(user__isnull=True, role_target=role, is_read=False)
            
    SystemNotification.objects.filter(query).update(is_read=True)
    return JsonResponse({'status': 'success'})

@login_required
def clear_all_notifications(request):
    role = None
    if hasattr(request.user, 'profile'):
        role = request.user.profile.role

    if role == 'superadmin':
        query = Q()
    else:
        query = Q(user=request.user)
        if role:
            query |= Q(user__isnull=True, role_target=role)
            
    SystemNotification.objects.filter(query).delete()
    return JsonResponse({'status': 'success'})
