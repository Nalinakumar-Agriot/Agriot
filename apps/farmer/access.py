"""Server-side module permissions for the active farm workspace."""
from django.http import JsonResponse


MODULE_ALIASES = {
    'Dashboard': {'Dashboard', 'Dashboards'},
    'Farms': {'Farms', 'Farms & Crop Splits'},
    'Market & Resources Hub': {'Market & Resources Hub'},
    'Task': {'Task', 'Tasks', 'Task Management'},
    'Sales': {'Sales', 'Financials & Sales'},
    'Inventory': {'Inventory', 'Resource Consumption'},
    'Expenses': {'Expenses', 'Expense', 'Outstanding', 'Outstanding Liabilities', 'Financials & Sales'},
    'Employee': {'Employee', 'Employees', 'Employee Management', 'Employee Shifts'},
    'Reports': {'Reports'},
}


def subscription_modules(farmer):
    from apps.superadmin.models import Subscription
    subscription = Subscription.objects.filter(
        farmer=farmer, subscription_status='active'
    ).select_related('package').order_by('-id').first()
    if not subscription or not subscription.package:
        return set()
    package_modules = set(subscription.package.modules or [])
    allowed = set()
    for module, aliases in MODULE_ALIASES.items():
        if package_modules.intersection(aliases):
            allowed.add(module)
    return allowed


def available_manager_permissions(farmer):
    return [module for module in MODULE_ALIASES if module in subscription_modules(farmer)]


def normalized_manager_permissions(farmer, requested, all_modules=False):
    available = set(available_manager_permissions(farmer))
    if all_modules:
        return ['ALL']
    return [permission for permission in requested if permission in available]


def can_access_module(request, module):
    from apps.farmer.workspaces import resolve_workspace
    farmer, staff = resolve_workspace(request)
    if farmer is None:
        return False
    if staff is None:  # Owner access remains governed by their subscription.
        return module in subscription_modules(farmer) or module == 'Dashboard'
    available = subscription_modules(farmer)
    if module not in available and module != 'Dashboard':
        return False
    permissions = set(staff.permissions or [])
    if 'ALL' in permissions:
        return True
    aliases = MODULE_ALIASES.get(module, {module})
    return bool(permissions.intersection(aliases)) or module == 'Dashboard'


def denied_module_response(request, module):
    message = f'You do not have access to the {module} module.'
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
        return JsonResponse({'success': False, 'error': message}, status=403)
    from django.contrib import messages
    from django.shortcuts import redirect
    messages.error(request, message)
    return redirect('farmer-dashboard')
