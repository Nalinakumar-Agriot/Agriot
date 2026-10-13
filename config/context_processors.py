from django.conf import settings
import json, os

def my_setting(request):
    return {'MY_SETTING': settings}

def language_code(request):
    return {"LANGUAGE_CODE": request.LANGUAGE_CODE}

def get_cookie(request):
    return {"COOKIES": request.COOKIES}

# Add the 'ENVIRONMENT' setting to the template context
def environment(request):
    return {'ENVIRONMENT': settings.ENVIRONMENT}

# config/context_processors.py

def menu_data(request):
    """
    Loads the correct vertical_menu.json based on the current URL prefix.

    URL prefix rules:
      /farmer/*   → farmer menu JSON
      /admin-panel/* or /dashboard/* or anything else → superadmin menu JSON

    We use request.path instead of user.role because:
    - At login the user may not be authenticated yet.
    - The farmer app and superadmin app are served on separate URL namespaces.
    """

    path = request.path  # e.g. "/farmer/dashboard/" or "/dashboard/"
    base_dir = str(settings.BASE_DIR)

    if path.startswith('/farmer/'):
        json_path = os.path.join(
            base_dir,
            'templates',
            'layout',
            'partials',
            'menu',
            'farmer',
            'json',
            'vertical_menu.json',
        )
    else:
        json_path = os.path.join(
            base_dir,
            'templates',
            'layout',
            'partials',
            'menu',
            'superadmin',
            'json',
            'vertical_menu.json',
        )

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            menu = json.load(f)

        if path.startswith('/farmer/') and request.user.is_authenticated:
            from apps.superadmin.models import Subscription
            try:
                # Determine effective farmer profile (Direct Farmer or Manager/Staff under Farmer)
                farmer_profile = None
                staff_profile = None

                if hasattr(request.user, 'staff_profile') and request.user.staff_profile:
                    staff_profile = request.user.staff_profile
                    farmer_profile = staff_profile.farmer
                elif hasattr(request.user, 'profile') and request.user.profile.role == 'farmer':
                    farmer_profile = request.user.profile

                if farmer_profile:
                    active_sub = Subscription.objects.filter(
                        farmer=farmer_profile,
                        subscription_status='active'
                    ).order_by('-id').first()

                    if active_sub and active_sub.package:
                        allowed_modules = active_sub.package.modules or []

                        MODULE_MAPPING = {
                            # Legacy package names support
                            'Farms & Crop Splits': ['Farms'],
                            'Resource Consumption': ['Inventory'],
                            'Outstanding Liabilities': ['Outstanding', 'Expenses', 'Expense'],
                            'Employee Shifts': ['Employee Management', 'Employee', 'Employees'],
                            'Team Accounts': ['Users Management'],
                            'Sales Records (Legacy)': [],
                            
                            # New package names (matching menu exactly)
                            'Dashboards': ['Dashboards'],
                            'Farms': ['Farms'],
                            'Market & Resources Hub': ['Market & Resources Hub'],
                            'Task': ['Task', 'Task Management', 'Tasks'],
                            'Tasks': ['Task', 'Task Management', 'Tasks'],
                            'Task Management': ['Task', 'Task Management', 'Tasks'],
                            'Financials & Sales': ['Sales', 'Financials & Sales', 'Expenses', 'Expense'],
                            'Sales': ['Sales', 'Financials & Sales', 'Expenses', 'Expense'],
                            'Inventory': ['Inventory'],
                            'Outstanding': ['Outstanding', 'Expenses', 'Expense'],
                            'Expenses': ['Expenses', 'Expense'],
                            'Expense': ['Expenses', 'Expense'],
                            'Employee Management': ['Employee Management', 'Employee', 'Employees'],
                            'Employee': ['Employee Management', 'Employee', 'Employees'],
                            'Employees': ['Employee Management', 'Employee', 'Employees'],
                            'Users Management': ['Users Management'],
                            'Smart Farming Guide': ['Smart Farming Guide'],
                            'My Documents': ['My Documents'],
                            'Profile & Subscription': ['Profile & Subscription'],
                            'Reports': ['Reports']
                        }

                        allowed_menu_names = set(['Market & Resources Hub', 'Task Management', 'Task', 'Tasks', 'Reports'])
                        for mod in allowed_modules:
                            if mod in MODULE_MAPPING:
                                allowed_menu_names.update(MODULE_MAPPING[mod])

                        filtered_menu = []
                        for item in menu.get('menu', []):
                            if 'menu_header' in item:
                                filtered_menu.append(item)
                            else:
                                if item.get('name') in allowed_menu_names:
                                    filtered_menu.append(item)
                        menu['menu'] = filtered_menu

                # If logged-in user is a Staff/Manager, filter menu by staff permissions
                if staff_profile:
                    perms = staff_profile.permissions or []
                    # Standardize permission mapping aliases
                    ALIAS_MAP = {
                        'Finance': 'Sales',
                        'Financials & Sales': 'Sales',
                        'Sales': 'Financials & Sales',
                        'Assets': 'Inventory',
                        'Inventory': 'Assets',
                        'Guidelines': 'Smart Farming Guide',
                        'Smart Farming Guide': 'Guidelines',
                        'IOT Admin': 'Dashboards',
                        'Dashboards': 'IOT Admin',
                        'Documents': 'My Documents',
                        'My Documents': 'Documents',
                        'Employee': 'Employee Management',
                        'Employee Management': 'Employee',
                        'Task': 'Task Management',
                        'Tasks': 'Task',
                        'Task Management': 'Task',
                        'Reports': 'Reports',
                    }
                    expanded_perms = set(perms)
                    for p in perms:
                        if p in ALIAS_MAP:
                            expanded_perms.add(ALIAS_MAP[p])

                    if 'Financials & Sales' in expanded_perms or 'Finance' in expanded_perms:
                        expanded_perms.add('Expenses')
                        expanded_perms.add('Expense')

                    if 'Employee Management' in expanded_perms or 'Employee' in expanded_perms:
                        expanded_perms.add('Employee')
                        expanded_perms.add('Employee Management')

                    show_all = ("ALL" in perms) or (staff_profile.is_role_manager and not perms)

                    if not show_all:
                        staff_filtered_menu = []
                        for item in menu.get('menu', []):
                            if 'menu_header' in item:
                                staff_filtered_menu.append(item)
                            else:
                                item_name = item.get('name')
                                if item_name in expanded_perms or item_name in ['Dashboards', 'Reports']:
                                    staff_filtered_menu.append(item)
                        menu['menu'] = staff_filtered_menu
            except Exception as e:
                print(f"[MENU FILTER ERROR] {e}")
        else:
            if request.user.is_authenticated and hasattr(request.user, 'profile'):
                profile = request.user.profile
                is_primary_admin = request.user.is_superuser or request.user.email == 'admin@agriot.com' or profile.role == 'superadmin'
                if not is_primary_admin:
                    perms = profile.permissions or []
                    if "ALL" not in perms and perms:
                        admin_filtered_menu = []
                        for item in menu.get('menu', []):
                            if 'menu_header' in item:
                                admin_filtered_menu.append(item)
                            else:
                                item_name = item.get('name')
                                if item_name in perms or item_name == 'Dashboards':
                                    admin_filtered_menu.append(item)
                        menu['menu'] = admin_filtered_menu

    except FileNotFoundError:
        print(f"[MENU] JSON not found: {json_path}")
        menu = {"menu": []}
    except json.JSONDecodeError as e:
        print(f"[MENU] JSON parse error in {json_path}: {e}")
        menu = {"menu": []}

    return {"menu_data": menu}

def notifications_data(request):
    """
    Fetches the unread notifications for the current authenticated user.
    If the user has a specific role, also fetches broadcast notifications for that role.
    """
    if not request.user.is_authenticated:
        return {'notifications': [], 'unread_notifications_count': 0}

    from auth.models import SystemNotification
    from django.db.models import Q

    role = None
    if hasattr(request.user, 'profile'):
        role = request.user.profile.role

    if role == 'superadmin':
        # Admin can see ALL notifications across the platform
        query = Q()
    else:
        # Normal users see their own notifications + role broadcasts
        query = Q(user=request.user)
        if role:
            query |= Q(user__isnull=True, role_target=role)

    # Fetch top 10 notifications that match the user or role
    notifications = SystemNotification.objects.filter(query).order_by('-created_at')[:10]
    unread_count = SystemNotification.objects.filter(query, is_read=False).count()

    return {
        'notifications': notifications,
        'unread_notifications_count': unread_count,
    }
