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
            is_farmer_portal_user = False
            if hasattr(request.user, 'staff_profile') and request.user.staff_profile:
                is_farmer_portal_user = True
            elif hasattr(request.user, 'profile') and request.user.profile.role == 'farmer':
                is_farmer_portal_user = True

            # Prevent going back to login pages
            if path == '/' or path == '/login/' or path == '/farmer/' or path == '/admin-panel/':
                if is_farmer_portal_user:
                    return redirect('/farmer/dashboard/')
                else:
                    return redirect('/admin-panel/dashboard/')
            
            # Restrict cross-panel access
            if is_farmer_portal_user:
                if path.startswith('/admin-panel/') and not path.startswith('/admin-panel/ajax/'):
                    return redirect('/farmer/dashboard/')
            else:
                if path.startswith('/farmer/'):
                    return redirect('/admin-panel/dashboard/')
        else:
            # User is NOT authenticated
            # Allow access to login, forgot password, etc.
            allowed_paths = [
                '/', 
                '/admin-panel/', 
                '/login/', 
                '/admin-panel/forgot-password/', 
                '/admin-panel/verify-otp/', 
                '/admin-panel/reset-password/',
                '/farmer/landing_page/',
                '/farmer/',
                '/farmer/otp-verify/',
                '/farmer/send_otp/',
                '/farmer/verify_otp_and_payment/',
                '/farmer/verify_payment/'
            ]
            if not any(path == p for p in allowed_paths) and not path.startswith(('/static/', '/media/', '/swagger/', '/redoc/', '/api/', '/swagger.json')):
                return redirect('/')


        response = self.get_response(request)

        # Prevent browser caching for all page responses (exclude static files)
        if not path.startswith('/static/'):
            response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'

        return response
