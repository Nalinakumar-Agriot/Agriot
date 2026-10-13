from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from auth.models import Profile

class SuperAdminAuthTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create(username="admin_user")
        self.user.set_password("adminpass123")
        self.user.save()
        
        self.profile = Profile.objects.get(user=self.user)
        self.profile.role = 'superadmin'
        self.profile.mobile_number = '1234567890'
        self.profile.save()

    def test_superadmin_login_renders(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)

    def test_superadmin_login_success(self):
        response = self.client.post(reverse('home'), {
            'mobile': '1234567890',
            'password': 'adminpass123'
        })
        self.assertRedirects(response, reverse('superadmin-dashboard'))

    def test_superadmin_login_invalid(self):
        response = self.client.post(reverse('home'), {
            'mobile': '1234567890',
            'password': 'wrongpassword'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Invalid mobile number or password')

class SuperAdminDashboardTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create(username="admin_user")
        self.user.set_password("adminpass123")
        self.user.save()
        
        self.profile = Profile.objects.get(user=self.user)
        self.profile.role = 'superadmin'
        self.profile.mobile_number = '1234567890'
        self.profile.save()

    def test_dashboard_access_authenticated(self):
        self.client.login(username='admin_user', password='adminpass123')
        response = self.client.get(reverse('superadmin-dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'superadmin/dashboard.html')

    def test_dashboard_access_unauthenticated(self):
        response = self.client.get(reverse('superadmin-dashboard'))
        self.assertEqual(response.status_code, 302) # Should redirect to login
