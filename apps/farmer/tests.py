from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from auth.models import Profile

class FarmerAuthTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.mobile = "9999999999"
        
        # We don't create the user here; we simulate the OTP flow that creates it.
        
    def test_login_page_renders(self):
        response = self.client.get(reverse('farmer-login'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'farmer/login.html')
        
    def test_otp_generation(self):
        # Post valid mobile number
        response = self.client.post(reverse('farmer-login'), {'mobile': self.mobile})
        
        # It should redirect to otp-verify
        self.assertRedirects(response, reverse('otp-verify'))
        
        # Check session
        session = self.client.session
        self.assertEqual(session['login_mobile'], self.mobile)
        self.assertTrue('login_otp' in session)
        self.assertTrue('login_otp_time' in session)
        
    def test_otp_verification_creates_user(self):
        # Simulate OTP generation
        session = self.client.session
        session['login_mobile'] = self.mobile
        session['login_otp'] = "123456"
        import time
        session['login_otp_time'] = time.time()
        session.save()
        
        # Verify OTP
        response = self.client.post(reverse('otp-verify'), {'otp': "123456"})
        
        # Should redirect to dashboard
        self.assertRedirects(response, reverse('farmer-dashboard'))
        
        # User and profile should be created
        user = User.objects.get(username=self.mobile)
        self.assertIsNotNone(user)
        self.assertTrue(hasattr(user, 'profile'))
        self.assertEqual(user.profile.role, 'farmer')

class FarmerDashboardTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create(username="8888888888")
        self.profile = Profile.objects.get(user=self.user)
        self.profile.role = 'farmer'
        self.profile.save()
        
    def test_dashboard_access_authenticated(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('farmer-dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'farmer/dashboard.html')

    def test_dashboard_access_unauthenticated(self):
        response = self.client.get(reverse('farmer-dashboard'))
        self.assertEqual(response.status_code, 302) # Should redirect to login
