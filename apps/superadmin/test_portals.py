from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.farmer.models import FarmerStaff


class AdminPortalIsolationTests(TestCase):
    def setUp(self):
        self.admin = self.make_user('operator', 'Field Manager')
        self.farmer = self.make_user('owner', 'farmer')
        self.staff = self.make_user('employee', 'staff')
        FarmerStaff.objects.create(user=self.staff, farmer=self.farmer.profile, name='Employee')
        self.client.force_login(self.admin)

    def make_user(self, username, role):
        user = User.objects.create_user(username=username, password='test-pass-123')
        user.profile.role = role
        if role not in {'farmer', 'staff'}:
            user.profile.permissions = ['Users Management']
        user.profile.save()
        return user

    def test_users_list_and_role_options_exclude_farm_accounts(self):
        response = self.client.get(reverse('manage_users'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['staff_users']), [self.admin])
        self.assertNotIn('staff', response.context['roles'])
        self.assertNotIn('farmer', response.context['roles'])

    def test_normal_admin_profile_modal_posts_to_admin_endpoint(self):
        response = self.client.get(reverse('manage_users'))
        self.assertContains(response, 'data-bs-target="#my_profile_update"')
        self.assertContains(response, 'action="' + reverse('update_my_profile') + '"')
        self.assertNotContains(response, 'href="' + reverse('farmer-profile') + '#basic_info"')
        response = self.client.post(reverse('update_my_profile'), {'first_name': 'Updated', 'email': 'operator@example.com'})
        self.assertRedirects(response, reverse('superadmin-dashboard'), fetch_redirect_response=False)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.first_name, 'Updated')
        self.assertEqual(self.admin.profile.role, 'Field Manager')

    def test_admin_cannot_enter_farmer_profile_or_workspace_aliases(self):
        for path in [reverse('farmer-profile'), '/farmer/profile_view/', reverse('select-workspace')]:
            response = self.client.get(path)
            self.assertRedirects(response, reverse('superadmin-dashboard'), fetch_redirect_response=False)
        self.assertNotIn('active_role', self.client.session)

    def test_users_actions_cannot_edit_or_delete_farm_users(self):
        for user in [self.farmer, self.staff]:
            for action in ['edit', 'delete']:
                response = self.client.post(reverse('manage_users'), {'action': action, 'id': user.pk})
                self.assertEqual(response.status_code, 404)
            self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_cannot_assign_reserved_farm_role_in_admin_users(self):
        response = self.client.post(reverse('manage_users'), {
            'action': 'edit', 'id': self.admin.pk, 'role': 'staff',
        })
        self.assertEqual(response.status_code, 302)
        self.admin.profile.refresh_from_db()
        self.assertEqual(self.admin.profile.role, 'Field Manager')

    def test_admin_login_rejects_farmer_and_staff(self):
        self.client.logout()
        for user in [self.farmer, self.staff]:
            response = self.client.post(reverse('home'), {'mobile': user.username, 'password': 'test-pass-123'})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('_auth_user_id', self.client.session)

    def test_superuser_with_legacy_farmer_profile_stays_in_admin(self):
        self.admin.is_superuser = True
        self.admin.save()
        self.admin.profile.role = 'farmer'
        self.admin.profile.save()
        response = self.client.get(reverse('manage_users'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'action="' + reverse('update_my_profile') + '"')

    def test_admin_permissions_and_legacy_staff_link_stay_in_admin_context(self):
        from web_project.template_tags.theme import has_permission
        FarmerStaff.objects.create(user=self.admin, farmer=self.farmer.profile, name='Legacy manager', is_role_manager=True)
        self.admin.profile.permissions = ['Reports']
        self.admin.profile.save()
        self.assertTrue(has_permission(self.admin, 'Reports'))
        self.assertFalse(has_permission(self.admin, 'Users Management'))
        response = self.client.get(reverse('superadmin-dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['current_workspace'])
        self.assertEqual(response.context['user_workspaces'], [])

    def test_admin_direct_urls_and_subscription_are_restricted_by_permissions(self):
        response = self.client.get(reverse('manage_subscription'))
        self.assertRedirects(response, reverse('superadmin-dashboard'), fetch_redirect_response=False)
        response = self.client.get(reverse('manage_farmer'))
        self.assertRedirects(response, reverse('superadmin-dashboard'), fetch_redirect_response=False)

        self.admin.profile.permissions = ['Farmers Management']
        self.admin.profile.save()
        response = self.client.get(reverse('manage_farmer'))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse('manage_users'))
        self.assertRedirects(response, reverse('superadmin-dashboard'), fetch_redirect_response=False)

    def test_suspended_account_gets_contact_administrator_message(self):
        self.client.logout()
        self.admin.is_active = False
        self.admin.save()
        response = self.client.post(reverse('home'), {
            'mobile': self.admin.username, 'password': 'test-pass-123',
        })
        self.assertContains(response, 'This account is suspended. Please contact the administrator.')
