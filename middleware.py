from django.shortcuts import redirect

class RoleRedirectMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        # Redirect accidental "framer" spelling to "farmer"
        if 'framer' in path:
            new_path = path.replace('framer', 'farmer')
            return redirect(new_path)

        # If user is authenticated
        if request.user.is_authenticated:
            request.user._current_request = request

            is_farmer_portal_user = False
            if hasattr(request.user, 'staff_profile') and request.user.staff_profile:
                is_farmer_portal_user = True
            elif hasattr(request.user, 'profile') and request.user.profile.role in ['farmer', 'staff']:
                is_farmer_portal_user = True

            # If selecting workspace or switching workspace, permit directly
            if 'select-workspace' in path or 'switch-workspace' in path:
                return self.get_response(request)

            # Prevent going back to login pages
            if path in ['/', '/login/', '/farmer', '/farmer/', '/admin', '/admin/', '/admin-panel', '/admin-panel/']:
                if is_farmer_portal_user:
                    if not request.session.get('active_role'):
                        from django.contrib.auth import logout
                        logout(request)
                        request.session.flush()
                        return redirect('farmer-login')
                    return redirect('/farmer/dashboard/')
                else:
                    return redirect('/admin/dashboard/')
            
            # Restrict cross-panel access
            if is_farmer_portal_user:
                if path in ['/admin/logout/', '/admin-panel/logout/']:
                    from django.contrib.auth import logout
                    logout(request)
                    return redirect('farmer-login')

                if path.startswith('/admin/') or path.startswith('/admin-panel/') or path in ['/admin', '/admin-panel']:
                    return redirect('/farmer/dashboard/')

                # Enforce View-Only mode if farmer subscription is expired
                if request.method in ['POST', 'PUT', 'DELETE', 'PATCH']:
                    exempt_paths = [
                        '/farmer/logout/',
                        '/logout/',
                        '/farmer/switch_plan/',
                        '/switch_plan/',
                        '/farmer/verify_payment/',
                        '/verify_payment/',
                        '/farmer/verify_otp_and_payment/',
                        '/verify_otp_and_payment/',
                        '/farmer/send_otp/',
                        '/send_otp/',
                        '/farmer/register/',
                        '/register/',
                        '/api/notifications/mark-read/',
                        '/api/notifications/clear/'
                    ]
                    if not any(path.startswith(ep) for ep in exempt_paths):
                        from apps.farmer.views import get_farmer_profile
                        profile = get_farmer_profile(request.user)
                        if not profile:
                            profile = getattr(request.user, 'profile', None)
                        if profile:
                            from apps.superadmin.subscription_utils import get_farmer_subscription_details
                            sub_info = get_farmer_subscription_details(profile)
                            if not sub_info['can_mutate']:
                                from django.http import JsonResponse
                                from django.contrib import messages
                                if sub_info.get('has_no_plan'):
                                    msg = "You do not have an active subscription plan yet. Please purchase a plan to start adding or editing records."
                                else:
                                    msg = "Your subscription plan has expired! You are in View-Only Mode. Please renew your plan to make changes."

                                if request.headers.get('x-requested-with') == 'XMLHttpRequest' or (request.content_type and 'application/json' in request.content_type):
                                    return JsonResponse({
                                        'status': 'error',
                                        'expired': not sub_info.get('has_no_plan'),
                                        'has_no_plan': sub_info.get('has_no_plan', False),
                                        'message': msg,
                                        'redirect_url': '/farmer/profile_view/#subscription'
                                    }, status=403)
                                messages.error(request, msg)
                                return redirect('/farmer/profile_view/#subscription')
            else:
                if path in ['/farmer/logout/', '/logout/']:
                    from django.contrib.auth import logout
                    logout(request)
                    return redirect('home')

                if path.startswith('/farmer/') or path in ['/farmer', '/']:
                    return redirect('/admin/dashboard/')
        else:
            # User is NOT authenticated
            # Allow access to login, forgot password, etc.
            allowed_paths = [
                '/', 
                '/login/', 
                '/admin',
                '/admin/',
                '/admin-panel',
                '/admin-panel/', 
                '/admin/forgot-password/', 
                '/admin-panel/forgot-password/', 
                '/admin/verify-otp/', 
                '/admin-panel/verify-otp/', 
                '/admin/reset-password/',
                '/admin-panel/reset-password/',
                '/farmer/landing_page/',
                '/landing_page/',
                '/farmer',
                '/farmer/',
                '/farmer/register/',
                '/register/',
                '/farmer/otp-verify/',
                '/otp-verify/',
                '/farmer/resend-otp/',
                '/resend-otp/',
                '/farmer/send_otp/',
                '/send_otp/',
                '/farmer/verify_otp_and_payment/',
                '/verify_otp_and_payment/',
                '/farmer/verify_payment/',
                '/verify_payment/'
            ]
            from django.conf import settings
            public_prefixes = ['/static/', '/media/', '/api/', '/farmer/register/', '/register/', '/farmer/farms/get_']
            if getattr(settings, 'DEBUG', False):
                public_prefixes.extend(['/swagger/', '/redoc/', '/swagger.json'])

            if not any(path == p for p in allowed_paths) and not path.startswith(tuple(public_prefixes)):
                if path.startswith('/admin/') or path.startswith('/admin-panel/') or path in ['/admin', '/admin-panel']:
                    return redirect('/admin/')
                return redirect('/')


        response = self.get_response(request)

        # Prevent browser caching for all page responses (exclude static files)
        if not path.startswith('/static/'):
            response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'

        return response
