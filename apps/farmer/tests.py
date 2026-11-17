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

    def test_dashboard_zero_state_for_new_farmer(self):
        import json
        self.client.force_login(self.user)
        response = self.client.get(reverse('farmer-dashboard'))
        self.assertEqual(response.status_code, 200)
        
        ctx = response.context
        self.assertEqual(ctx['total_farms'], 0)
        self.assertEqual(ctx['active_crops'], 0)
        self.assertEqual(ctx['harvested_crops'], 0)
        self.assertEqual(ctx['total_tasks'], 0)
        self.assertEqual(ctx['total_staff'], 0)
        self.assertEqual(ctx['attendance_pct'], 0)
        self.assertEqual(ctx['farm_health_score'], 0)
        self.assertEqual(ctx['tools_count'], 0)
        self.assertEqual(ctx['fleet_uptime_pct'], 0)
        self.assertEqual(ctx['payroll_by_role'], [])
        self.assertEqual(ctx['recent_sales_orders'], [])
        self.assertEqual(ctx['crop_health_chips'], [])
        self.assertEqual(ctx['dashboard_farms'], [])
        self.assertEqual(ctx['dashboard_crops'], [])
        
        sales_data = json.loads(ctx['sales_analytics_json'])
        self.assertEqual(sales_data['raw_records'], [])
        
        expense_data = json.loads(ctx['expense_analytics_json'])
        self.assertEqual(expense_data['raw_records'], [])
        
        task_data = json.loads(ctx['task_analytics_json'])
        self.assertEqual(task_data['raw_records'], [])



class FuelConsumptionTests(TestCase):
    def setUp(self):
        from django.test import RequestFactory
        from apps.farmer.models import Farm, FarmerInventory
        self.user = User.objects.create_user(username='fuel-test-user')
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        self.farm = Farm.objects.create(farmer=self.profile, land_name='Test land', total_area='2')
        self.vehicle = FarmerInventory.objects.create(farmer=self.profile, item_name='Tractor', category='Machinery', quantity=3)
        self.fuel = FarmerInventory.objects.create(farmer=self.profile, item_name='Diesel', category='Fuel', unit='Liters', quantity=10, safety_stock=1)
        self.factory = RequestFactory()

    def submit(self, **overrides):
        import json
        from unittest.mock import patch
        from apps.farmer.views import add_farm_activity
        payload = {'farm_id': self.farm.pk, 'resource_type': 'Vehicle & Fleet', 'vehicle_id': self.vehicle.pk,
                   'inventory_item': self.fuel.pk, 'quantity': '2.5', 'quantity_metric': '2.5 Liters',
                   'asset_name': 'Tractor', 'amount': '250'}
        payload.update(overrides)
        request = self.factory.post('/', payload)
        request.user = self.user
        with patch('apps.farmer.views.get_farmer_profile', return_value=self.profile), patch('apps.farmer.views.log_action'):
            return json.loads(add_farm_activity(request).content)

    def test_vehicle_consumes_only_fractional_fuel(self):
        from apps.farmer.models import FarmActivity, InventoryStockLog, LedgerTransaction
        result = self.submit()
        self.assertTrue(result['success'])
        self.vehicle.refresh_from_db()
        self.fuel.refresh_from_db()
        self.assertEqual(self.vehicle.quantity, 3)
        self.assertAlmostEqual(self.fuel.quantity, 7.5)
        log = InventoryStockLog.objects.get(inventory=self.fuel)
        self.assertEqual(log.quantity, 2.5)
        self.assertIn('Tractor', log.notes)
        self.assertIn('Diesel', FarmActivity.objects.get(pk=result['activity_id']).notes)
        self.assertFalse(LedgerTransaction.objects.filter(farmer=self.profile).exists())

    def test_insufficient_fuel_does_not_change_stock_or_create_activity(self):
        from apps.farmer.models import FarmActivity, InventoryStockLog
        self.assertFalse(self.submit(quantity='10.5')['success'])
        self.fuel.refresh_from_db()
        self.assertEqual(self.fuel.quantity, 10)
        self.assertFalse(InventoryStockLog.objects.exists())
        self.assertFalse(FarmActivity.objects.exists())

    def test_machine_cannot_be_used_as_fuel(self):
        self.assertFalse(self.submit(inventory_item=self.vehicle.pk)['success'])
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.quantity, 3)

    def test_requires_vehicle_and_fuel(self):
        self.assertFalse(self.submit(vehicle_id='')['success'])
        self.assertFalse(self.submit(inventory_item='')['success'])

    def test_rejects_non_fuel_and_non_litre_stock(self):
        from apps.farmer.models import FarmerInventory
        seeds = FarmerInventory.objects.create(farmer=self.profile, item_name='Seeds', category='Seeds', unit='Liters', quantity=10)
        self.assertFalse(self.submit(inventory_item=seeds.pk)['success'])
        self.fuel.unit = 'Units'
        self.fuel.save(update_fields=['unit'])
        self.assertFalse(self.submit()['success'])
        self.fuel.refresh_from_db()
        self.assertEqual(self.fuel.quantity, 10)

    def test_rejects_wrong_fuel_for_vehicle(self):
        self.vehicle.fuel_type = 'Petrol'
        self.vehicle.save(update_fields=['fuel_type'])
        self.assertFalse(self.submit()['success'])
        self.fuel.refresh_from_db()
        self.assertEqual(self.fuel.quantity, 10)

    def test_mobile_consumption_rejects_machine_stock(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from rest_framework.exceptions import ValidationError
        from apps.farmer.api_views import ConsumptionViewSet
        view = ConsumptionViewSet()
        view.request = SimpleNamespace(user=self.user, data={'inventory_item': self.vehicle.pk, 'quantity': '2.5'})
        serializer = Mock(validated_data={'farm': self.farm})
        with patch('apps.farmer.api_views.get_farmer_profile', return_value=self.profile):
            with self.assertRaises(ValidationError):
                view.perform_create(serializer)
        serializer.save.assert_not_called()
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.quantity, 3)

    def test_stock_update_rolls_back_when_activity_creation_fails(self):
        from unittest.mock import patch
        from apps.farmer.models import InventoryStockLog
        with patch('apps.farmer.models.FarmActivity.objects.create', side_effect=RuntimeError('test error')):
            self.assertFalse(self.submit()['success'])
        self.fuel.refresh_from_db()
        self.assertEqual(self.fuel.quantity, 10)
        self.assertFalse(InventoryStockLog.objects.exists())

    def test_inventory_items_are_grouped_by_configured_category_layout(self):
        from types import SimpleNamespace
        from apps.farmer.inventory_consumption import inventory_layout
        layouts = {'warehouse stock': 'Warehouse', 'machinery': 'Machinery', 'farm tools': 'Tools'}
        self.assertEqual(inventory_layout(SimpleNamespace(category='Warehouse Stock'), layouts), 'warehouse')
        self.assertEqual(inventory_layout(SimpleNamespace(category='Machinery'), layouts), 'machinery')
        self.assertEqual(inventory_layout(SimpleNamespace(category='Farm Tools'), layouts), 'tools')
        # Item names must not move stock into another category.
        self.assertEqual(inventory_layout(SimpleNamespace(category='Warehouse Stock', item_name='Tractor oil'), layouts), 'warehouse')

class BatchSaleCustomerTests(TestCase):
    def setUp(self):
        from django.test import RequestFactory
        from apps.farmer.models import Farm, CropSplit
        self.user = User.objects.create_user(username='batch-customer-test')
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        self.farm = Farm.objects.create(farmer=self.profile, land_name='Test farm', total_area='2')
        self.split = CropSplit.objects.create(farm=self.farm, acreage=1, yield_tons=5, stage='Harvested',
            harvest_batches=[{'batch_no': 1, 'yield': 5, 'unit': 'Ton', 'price': 20, 'status': 'Ready'}])
        self.factory = RequestFactory()

    def submit_sale(self, **overrides):
        import json
        from unittest.mock import patch
        from apps.farmer.views import sell_crop_split
        data = {'split_id': self.split.pk, 'batch_index': '0', 'yield': '2', 'price': '20', 'total': '40',
                'paid': '20', 'customer': 'Direct Customer', 'direct_customer_name': 'Walk In Buyer',
                'direct_customer_phone': '919876543210', 'unit': 'Ton'}
        data.update(overrides)
        request = self.factory.post('/', data)
        request.user = self.user
        with patch('apps.farmer.views.get_farmer_profile', return_value=self.profile), patch('apps.farmer.views.log_action'):
            return json.loads(sell_crop_split(request).content)

    def test_direct_customer_details_are_recorded_on_sale(self):
        from apps.farmer.models import FarmerCustomer, FarmerOutstanding
        response = self.submit_sale()
        self.assertTrue(response['success'], response)
        customer = FarmerCustomer.objects.get(farmer=self.profile, name='Walk In Buyer')
        self.assertEqual(customer.contact_number, '919876543210')
        self.assertTrue(FarmerOutstanding.objects.filter(customer=customer).exists())
        self.split.refresh_from_db()
        self.assertEqual(self.split.harvest_batches[0]['sold_contact'], '919876543210')
        self.assertEqual(self.split.harvest_batches[1]['yield'], 3)

    def test_partial_sales_keep_original_batch_and_yield_price(self):
        self.split.harvest_batches.append({'batch_no': 2, 'yield': 4, 'unit': 'Ton', 'price': 30, 'saleValue': 120, 'quality': 'Grade 2', 'status': 'Ready'})
        self.split.save(update_fields=['harvest_batches'])
        self.assertTrue(self.submit_sale(price='50', total='100', paid='100')['success'])
        self.split.refresh_from_db()
        sold, balance, other = self.split.harvest_batches
        self.assertEqual((sold['batch_no'], balance['batch_no'], other['batch_no']), (1, 1, 2))
        self.assertEqual((sold['price'], sold['sold_at_price'], sold['sold_total']), (20, 50, 100))
        self.assertEqual((balance['yield'], balance['price']), (3, 20))
        self.assertTrue(self.submit_sale(batch_index='1', **{'yield': '1', 'price': '45', 'total': '45', 'paid': '45'})['success'])
        self.split.refresh_from_db()
        self.assertEqual([batch['batch_no'] for batch in self.split.harvest_batches], [1, 1, 1, 2])
        self.assertEqual(self.split.harvest_batches[2]['yield'], 2)

    def test_direct_customer_requires_name_and_valid_phone(self):
        from apps.farmer.models import FarmerCustomer
        self.assertFalse(self.submit_sale(direct_customer_name='')['success'])
        self.assertFalse(self.submit_sale(direct_customer_phone='12345678901234')['success'])
        self.assertFalse(self.submit_sale(direct_customer_phone='not-a-phone')['success'])
        self.split.refresh_from_db()
        self.assertEqual(self.split.harvest_batches[0]['status'], 'Ready')
        self.assertFalse(FarmerCustomer.objects.filter(farmer=self.profile).exists())

    def test_cannot_oversell_or_sell_same_batch_twice(self):
        self.assertFalse(self.submit_sale(**{'yield': '6'})['success'])
        self.assertTrue(self.submit_sale(**{'yield': '5', 'total': '100'})['success'])
        self.assertFalse(self.submit_sale()['success'])

    def test_quick_customer_phone_limit(self):
        import json
        from unittest.mock import patch
        from apps.farmer.views import quick_add_customer_api
        for phone, expected in [('12345678901234', False), ('12345abcde', False), ('9876543210', True), ('9198765432101', True)]:
            request = self.factory.post('/', {'name': 'Customer ' + phone, 'contact_number': phone})
            request.user = self.user
            with patch('apps.farmer.views.get_farmer_profile', return_value=self.profile):
                response = json.loads(quick_add_customer_api(request).content)
            self.assertEqual(response['success'], expected, response)

class CropStageHistoryTests(TestCase):
    def setUp(self):
        from django.test import RequestFactory
        from apps.farmer.models import Farm, CropSplit
        self.user = User.objects.create_user(username='stage-history-test')
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        self.farm = Farm.objects.create(farmer=self.profile, land_name='History Farm', total_area='2')
        self.split = CropSplit.objects.create(farm=self.farm, acreage=1, stage='Sown')
        self.factory = RequestFactory()

    def set_stage(self, stage):
        import json
        from unittest.mock import patch
        from apps.farmer.views import advance_crop_split
        request = self.factory.post('/', {'split_id': self.split.pk, 'stage': stage})
        request.user = self.user
        with patch('apps.farmer.views.get_farmer_profile', return_value=self.profile), \
             patch('apps.farmer.views.get_active_manager_staff', return_value=None), \
             patch('apps.farmer.views.get_actor_name', return_value='Farmer'), \
             patch('apps.farmer.views.log_action'):
            return json.loads(advance_crop_split(request).content)

    def test_repeated_stage_request_creates_only_one_event(self):
        from apps.farmer.models import FarmActivity
        self.assertTrue(self.set_stage('Vegetative')['success'])
        self.assertTrue(self.set_stage('Vegetative')['success'])
        self.split.refresh_from_db()
        self.assertEqual(self.split.stage, 'Vegetative')
        self.assertEqual(FarmActivity.objects.filter(farm=self.farm, asset_name='Crop Phase Update').count(), 1)

    def test_real_stage_changes_are_recorded(self):
        from apps.farmer.models import FarmActivity
        for stage in ['Vegetative', 'Flowering', 'Vegetative']:
            self.assertTrue(self.set_stage(stage)['success'])
        self.assertEqual(FarmActivity.objects.filter(farm=self.farm, asset_name='Crop Phase Update').count(), 3)

class HarvestBatchNumberTests(TestCase):
    def test_legacy_partial_sale_fragments_keep_original_number(self):
        from apps.farmer.harvest_batches import normalize_batch_numbers
        original = [
            {'yield': 60, 'price': 47.5, 'saleValue': 5700, 'unit': 'Ton', 'priceUnit': 'Ton', 'quality': 'Grade 1', 'flush_no': 1, 'status': 'Sold'},
            {'yield': 60, 'price': 47.5, 'saleValue': 2850, 'unit': 'Ton', 'priceUnit': 'Ton', 'quality': 'Grade 1', 'flush_no': 1, 'status': 'Ready', 'batch_no': 3},
            {'yield': 50, 'price': 32.2, 'saleValue': 1610, 'unit': 'Ton', 'priceUnit': 'Ton', 'quality': 'Grade 2', 'flush_no': 1, 'status': 'Ready'},
        ]
        result = normalize_batch_numbers(original)
        self.assertEqual([item['batch_no'] for item in result], [1, 1, 2])
        self.assertNotIn('batch_no', original[0])
        self.assertEqual(original[1]['batch_no'], 3)

    def test_different_original_batches_are_not_merged(self):
        from apps.farmer.harvest_batches import normalize_batch_numbers
        batches = [
            {'yield': 10, 'price': 5, 'saleValue': 100, 'unit': 'Ton', 'priceUnit': 'Ton', 'quality': 'Grade 1', 'flush_no': 1, 'status': 'Sold'},
            {'yield': 10, 'price': 5, 'saleValue': 50, 'unit': 'Ton', 'priceUnit': 'Ton', 'quality': 'Grade 1', 'flush_no': 1, 'status': 'Ready'},
        ]
        self.assertEqual([item['batch_no'] for item in normalize_batch_numbers(batches)], [1, 2])

class HarvestedCropTaskTests(TestCase):
    def setUp(self):
        from django.test import RequestFactory
        from apps.farmer.models import Farm, CropSplit
        self.user = User.objects.create_user(username='harvested-task-test')
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        self.farm = Farm.objects.create(farmer=self.profile, land_name='Test Farm', total_area='2')
        self.split = CropSplit.objects.create(farm=self.farm, acreage=1, stage='Harvested', harvest_type='multiple')
        self.factory = RequestFactory()

    def test_harvested_crop_rejects_tasks_for_each_harvest_type(self):
        import json
        from unittest.mock import patch
        from apps.farmer.views import add_farm_task
        from apps.farmer.models import FarmTask
        for harvest_type in ('single', 'multiple', 'Multiple Harvest'):
            self.split.harvest_type = harvest_type
            self.split.save(update_fields=['harvest_type'])
            request = self.factory.post('/', {'farm_id': self.farm.id, 'split_id': self.split.id, 'title': 'Test'})
            request.user = self.user
            with patch('apps.farmer.views.get_farmer_profile', return_value=self.profile):
                response = json.loads(add_farm_task(request).content)
            self.assertFalse(response['success'], harvest_type)
            self.assertIn('harvested', response['error'])
        self.assertFalse(FarmTask.objects.filter(associated_split=self.split).exists())

    def test_harvested_crop_rejects_consumption(self):
        import json
        from unittest.mock import patch
        from apps.farmer.views import add_farm_activity
        from apps.farmer.models import FarmActivity
        request = self.factory.post('/', {'farm_id': self.farm.id, 'crop_split_id': self.split.id,
                                          'activity_type': 'Resource Consumption', 'quantity': '2'})
        request.user = self.user
        with patch('apps.farmer.views.get_farmer_profile', return_value=self.profile):
            response = json.loads(add_farm_activity(request).content)
        self.assertFalse(response['success'])
        self.assertFalse(FarmActivity.objects.filter(farm=self.farm).exists())

    def test_harvested_crop_rejects_new_expense(self):
        import json
        from unittest.mock import patch
        from apps.farmer.views import add_transaction
        from apps.farmer.models import LedgerTransaction
        request = self.factory.post('/', {'farm_id': self.farm.id, 'crop_split_id': self.split.id,
                                          'description': 'Fertilizer', 'transaction_type': 'outflow', 'amount': '10'})
        request.user = self.user
        with patch('apps.farmer.views.get_farmer_profile', return_value=self.profile):
            response = json.loads(add_transaction(request).content)
        self.assertFalse(response['success'])
        self.assertFalse(LedgerTransaction.objects.filter(farmer=self.profile).exists())


class ManagerCropAssignmentTests(TestCase):
    def setUp(self):
        from django.test import RequestFactory
        from apps.farmer.models import CropSplit, Farm, FarmerStaff
        self.user = User.objects.create_user(username='manager-assignment-test')
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        self.manager = FarmerStaff.objects.create(
            farmer=self.profile, name='Test Manager', mobile_number='7548834011',
            role='Manager', is_role_manager=True
        )
        self.farm = Farm.objects.create(farmer=self.profile, land_name='Test 2', total_area='2')
        self.first_split = CropSplit.objects.create(farm=self.farm, acreage=1, stage='Harvested')
        self.second_split = CropSplit.objects.create(farm=self.farm, acreage=1, stage='Harvested')
        self.factory = RequestFactory()

    def save(self, split_ids):
        import json
        from unittest.mock import patch
        from apps.farmer.views import save_manager_crops_api
        request = self.factory.post(
            '/', data=json.dumps({'manager_id': self.manager.id, 'crop_split_ids': split_ids}),
            content_type='application/json'
        )
        request.user = self.user
        with patch('apps.farmer.views.get_farmer_profile', return_value=self.profile), \
             patch('apps.farmer.views.log_action'):
            response = save_manager_crops_api(request)
        return response, json.loads(response.content)

    def test_checked_crop_ids_are_saved_and_unchecked_ids_are_removed(self):
        response, result = self.save([self.first_split.id, self.second_split.id])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(result['success'], result)
        self.assertEqual(result['assigned_count'], 2)
        self.assertTrue(self.first_split.supervisors.filter(id=self.manager.id).exists())
        self.assertTrue(self.second_split.supervisors.filter(id=self.manager.id).exists())

        response, result = self.save([self.second_split.id])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(result['success'], result)
        self.assertEqual(result['assigned_count'], 1)
        self.assertFalse(self.first_split.supervisors.filter(id=self.manager.id).exists())
        self.assertTrue(self.second_split.supervisors.filter(id=self.manager.id).exists())

    def test_foreign_crop_id_is_not_assigned_or_counted(self):
        from apps.farmer.models import CropSplit, Farm
        other_user = User.objects.create_user(username='other-assignment-owner')
        other_profile, _ = Profile.objects.get_or_create(user=other_user)
        other_farm = Farm.objects.create(farmer=other_profile, land_name='Other', total_area='1')
        other_split = CropSplit.objects.create(farm=other_farm, acreage=1)

        response, result = self.save([self.first_split.id, other_split.id])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(result['success'], result)
        self.assertEqual(result['assigned_count'], 1)
        self.assertFalse(other_split.supervisors.filter(id=self.manager.id).exists())


class SubscriptionFlowTests(TestCase):
    def setUp(self):
        from apps.superadmin.models import Package
        self.client = Client()
        self.mobile = "9876501234"
        self.user = User.objects.create_user(username=self.mobile, first_name="Sub Farmer")
        self.profile = Profile.objects.get(user=self.user)
        self.profile.role = 'farmer'
        self.profile.mobile_number = self.mobile
        self.profile.save()

        self.trial_pkg = Package.objects.create(
            name="1-Month Free Trial",
            price=0,
            max_acre_support="Unlimited",
            is_active=True,
            is_active_monthly=True,
            is_active_quarterly=False,
            is_active_yearly=False,
        )
        self.eco_pkg = Package.objects.create(
            name="Eco-Starter Package",
            price=4067,
            max_acre_support="15",
            my_land="3",
            my_crop="10",
            my_users="5",
            is_active=True,
            is_active_monthly=True,
            is_active_quarterly=True,
            is_active_yearly=True,
        )
        self.pro_pkg = Package.objects.create(
            name="Bumper Yield Professional",
            price=10707,
            max_acre_support="100",
            my_land="10",
            my_crop="30",
            my_users="15",
            is_active=True,
            is_active_monthly=True,
            is_active_quarterly=True,
            is_active_yearly=True,
        )

    def _sign(self, order_id, payment_id):
        import hashlib
        import hmac
        from apps.superadmin.razorpay_utils import get_razorpay_credentials
        secret = get_razorpay_credentials()['key_secret']
        return hmac.new(
            secret.encode('utf-8'),
            f"{order_id}|{payment_id}".encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()

    def _login_session(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['active_role'] = 'farmer'
        session['active_farmer_id'] = self.profile.id
        session.save()

    def test_quarterly_subscription_displays_quarterly_across_reloads_and_logins(self):
        import json
        from datetime import timedelta
        from django.utils import timezone
        from apps.superadmin.models import Subscription

        today = timezone.now().date()
        Subscription.objects.create(
            farmer=self.profile,
            package=self.eco_pkg,
            monthly_amount=self.eco_pkg.price_quarterly,
            amount_paid=self.eco_pkg.price_quarterly,
            billing_cycle='quarterly',
            auto_renew=True,
            payment_status='success',
            subscription_status='active',
            start_date=today,
            end_date=today + timedelta(days=90),
        )

        self._login_session()
        res = self.client.get(reverse('farmer-profile'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['active_cycle'], 'quarterly')
        self.assertEqual(res.context['active_cycle_label'], 'Quarterly')
        self.assertContains(res, 'Quarterly Plan')
        self.assertContains(res, 'per Quarter (Quarterly Billing)')

        quotes = json.loads(res.context['package_quotes_json'])
        eco_q = quotes[str(self.eco_pkg.id)]
        self.assertTrue(eco_q['quarterly']['is_current'])
        self.assertFalse(eco_q['quarterly']['can_switch'])
        self.assertFalse(eco_q['monthly']['is_current'])
        self.assertEqual(eco_q['monthly']['action_type'], 'switch_cycle')
        self.assertFalse(eco_q['monthly']['can_switch'])
        self.assertFalse(eco_q['monthly']['is_scheduled_switch'])
        self.assertTrue(eco_q['monthly']['is_downgrade'])
        self.assertTrue(eco_q['yearly']['can_switch'])
        self.assertEqual(eco_q['yearly']['action_type'], 'upgrade')
        self.assertContains(res, 'Available after current plan ends')

        # Logout and login again -> persistence check
        self.client.logout()
        self._login_session()
        res_after = self.client.get(reverse('farmer-profile'))
        self.assertEqual(res_after.status_code, 200)
        self.assertEqual(res_after.context['active_cycle'], 'quarterly')
        self.assertEqual(res_after.context['active_subscription'].billing_cycle, 'quarterly')

    def test_monthly_to_quarterly_upgrade_with_prorata_and_payment_verification(self):
        import json
        from datetime import timedelta
        from unittest.mock import patch
        from django.utils import timezone
        from apps.superadmin.models import Subscription

        today = timezone.now().date()
        monthly_sub = Subscription.objects.create(
            farmer=self.profile,
            package=self.eco_pkg,
            monthly_amount=self.eco_pkg.price,
            amount_paid=self.eco_pkg.price,
            billing_cycle='monthly',
            auto_renew=True,
            payment_status='success',
            subscription_status='active',
            start_date=today,
            end_date=today + timedelta(days=30),
        )

        self._login_session()
        # Same monthly plan repurchase must be rejected
        dup_res = self.client.post(
            reverse('farmer-switch-plan'),
            data=json.dumps({'action': 'initiate', 'package_id': self.eco_pkg.id, 'billing_cycle': 'monthly'}),
            content_type='application/json',
        )
        self.assertEqual(dup_res.status_code, 400)

        # Monthly -> Quarterly upgrade creates pending order while keeping Monthly active
        with patch('apps.superadmin.razorpay_utils.create_razorpay_order', return_value=('order_live_qtr_1', True, None)):
            init_res = self.client.post(
                reverse('farmer-switch-plan'),
                data=json.dumps({'action': 'initiate', 'package_id': self.eco_pkg.id, 'billing_cycle': 'quarterly'}),
                content_type='application/json',
            )
        self.assertEqual(init_res.status_code, 200)
        init_data = init_res.json()
        self.assertTrue(init_data['success'])
        self.assertEqual(init_data['billing_cycle'], 'quarterly')
        self.assertEqual(init_data['order_id'], 'order_live_qtr_1')
        self.assertGreater(init_data['quote']['deduction_credit'], 0)
        self.assertEqual(init_data['net_payable'], round(12201.0 - init_data['quote']['deduction_credit'], 2))

        # Before payment verification, Monthly remains the active plan
        monthly_sub.refresh_from_db()
        self.assertEqual(monthly_sub.subscription_status, 'active')

        # Invalid signature fails verification and leaves Monthly active
        bad_verify = self.client.post(reverse('farmer-verify-payment'), {
            'razorpay_payment_id': 'pay_bad_1',
            'razorpay_order_id': 'order_live_qtr_1',
            'razorpay_signature': 'invalid_sig',
            'package_id': self.eco_pkg.id,
            'mobile_number': self.mobile,
            'billing_cycle': 'quarterly',
        })
        self.assertEqual(bad_verify.status_code, 400)
        monthly_sub.refresh_from_db()
        self.assertEqual(monthly_sub.subscription_status, 'active')

        # Re-initiate & complete valid payment
        with patch('apps.superadmin.razorpay_utils.create_razorpay_order', return_value=('order_live_qtr_2', True, None)):
            init_res2 = self.client.post(
                reverse('farmer-switch-plan'),
                data=json.dumps({'action': 'initiate', 'package_id': self.eco_pkg.id, 'billing_cycle': 'quarterly'}),
                content_type='application/json',
            )
        self.assertEqual(init_res2.status_code, 200)
        sig = self._sign('order_live_qtr_2', 'pay_live_qtr_2')
        ok_verify = self.client.post(reverse('farmer-verify-payment'), {
            'razorpay_payment_id': 'pay_live_qtr_2',
            'razorpay_order_id': 'order_live_qtr_2',
            'razorpay_signature': sig,
            'package_id': self.eco_pkg.id,
            'mobile_number': self.mobile,
            'billing_cycle': 'quarterly',
        })
        self.assertEqual(ok_verify.status_code, 200)
        self.assertEqual(ok_verify.json()['data']['billing_cycle'], 'quarterly')

        # Replay of same payment must be idempotent without creating duplicate active subscriptions
        replay_verify = self.client.post(reverse('farmer-verify-payment'), {
            'razorpay_payment_id': 'pay_live_qtr_2',
            'razorpay_order_id': 'order_live_qtr_2',
            'razorpay_signature': sig,
            'package_id': self.eco_pkg.id,
            'mobile_number': self.mobile,
            'billing_cycle': 'quarterly',
        })
        self.assertEqual(replay_verify.status_code, 200)

        active_qs = Subscription.objects.filter(farmer=self.profile, subscription_status='active')
        self.assertEqual(active_qs.count(), 1)
        active_sub = active_qs.first()
        self.assertEqual(active_sub.billing_cycle, 'quarterly')
        self.assertEqual((active_sub.end_date - active_sub.start_date).days, 90)
        monthly_sub.refresh_from_db()
        self.assertEqual(monthly_sub.subscription_status, 'cancelled')

    def test_high_to_low_blocked_while_active_and_allowed_after_completion(self):
        import json
        from datetime import timedelta
        from unittest.mock import patch
        from django.utils import timezone
        from rest_framework.test import APIClient
        from apps.superadmin.models import Subscription

        today = timezone.now().date()
        end_date = today + timedelta(days=90)
        qtr_sub = Subscription.objects.create(
            farmer=self.profile,
            package=self.eco_pkg,
            monthly_amount=self.eco_pkg.price_quarterly,
            amount_paid=self.eco_pkg.price_quarterly,
            billing_cycle='quarterly',
            auto_renew=False,
            payment_status='success',
            subscription_status='active',
            start_date=today,
            end_date=end_date,
        )

        self._login_session()
        api = APIClient()
        api.force_authenticate(user=self.user)

        # 1. Quarterly -> Monthly (same package, shorter cycle) blocked while active
        for action_name in ('initiate', 'schedule_switch'):
            res_cycle = self.client.post(
                reverse('farmer-switch-plan'),
                data=json.dumps({'action': action_name, 'package_id': self.eco_pkg.id, 'billing_cycle': 'monthly'}),
                content_type='application/json',
            )
            self.assertEqual(res_cycle.status_code, 400)
            self.assertFalse(res_cycle.json()['quote']['can_switch'])

        api_cycle = api.post(reverse('api-farmer-switch-plan'), {
            'action': 'initiate',
            'package_id': self.eco_pkg.id,
            'billing_cycle': 'monthly',
        }, format='json')
        self.assertEqual(api_cycle.status_code, 400)

        # 2. Switch up to Bumper Yield Quarterly -> Eco-Starter (all cycles) & Free Trial blocked while active
        qtr_sub.package = self.pro_pkg
        qtr_sub.monthly_amount = self.pro_pkg.price_quarterly
        qtr_sub.amount_paid = self.pro_pkg.price_quarterly
        qtr_sub.save()

        for cycle in ('monthly', 'quarterly', 'yearly'):
            res_down = self.client.post(
                reverse('farmer-switch-plan'),
                data=json.dumps({'action': 'initiate', 'package_id': self.eco_pkg.id, 'billing_cycle': cycle}),
                content_type='application/json',
            )
            self.assertEqual(res_down.status_code, 400)
            self.assertFalse(res_down.json()['quote']['can_switch'])
            self.assertTrue(res_down.json()['quote']['is_downgrade'])

        # Direct payment verify bypass attempt to downgrade while active must fail
        sig = self._sign('order_bypass_down', 'pay_bypass_down')
        bad_verify = self.client.post(reverse('farmer-verify-payment'), {
            'razorpay_payment_id': 'pay_bypass_down',
            'razorpay_order_id': 'order_bypass_down',
            'razorpay_signature': sig,
            'package_id': self.eco_pkg.id,
            'mobile_number': self.mobile,
            'billing_cycle': 'monthly',
        })
        self.assertEqual(bad_verify.status_code, 400)

        # Bumper Yield Quarterly still active and untouched
        qtr_sub.refresh_from_db()
        self.assertEqual(qtr_sub.subscription_status, 'active')
        self.assertEqual(qtr_sub.package_id, self.pro_pkg.id)
        self.assertEqual(qtr_sub.billing_cycle, 'quarterly')

        # 3. Once Bumper Yield Quarterly completes/expires, purchasing Eco-Starter Monthly IS allowed
        qtr_sub.end_date = today - timedelta(days=1)
        qtr_sub.save()

        with patch('apps.superadmin.razorpay_utils.create_razorpay_order', return_value=('order_after_done', True, None)):
            init_after = self.client.post(
                reverse('farmer-switch-plan'),
                data=json.dumps({'action': 'initiate', 'package_id': self.eco_pkg.id, 'billing_cycle': 'monthly'}),
                content_type='application/json',
            )
        self.assertEqual(init_after.status_code, 200)
        self.assertTrue(init_after.json()['success'])
        self.assertEqual(init_after.json()['billing_cycle'], 'monthly')

        sig_ok = self._sign('order_after_done', 'pay_after_done')
        verify_after = self.client.post(reverse('farmer-verify-payment'), {
            'razorpay_payment_id': 'pay_after_done',
            'razorpay_order_id': 'order_after_done',
            'razorpay_signature': sig_ok,
            'package_id': self.eco_pkg.id,
            'mobile_number': self.mobile,
            'billing_cycle': 'monthly',
        })
        self.assertEqual(verify_after.status_code, 200)
        new_active = Subscription.objects.get(farmer=self.profile, subscription_status='active')
        self.assertEqual(new_active.package_id, self.eco_pkg.id)
        self.assertEqual(new_active.billing_cycle, 'monthly')

    def test_expired_subscription_can_purchase_monthly_or_quarterly(self):
        import json
        from datetime import timedelta
        from unittest.mock import patch
        from django.utils import timezone
        from apps.superadmin.models import Subscription

        today = timezone.now().date()
        Subscription.objects.create(
            farmer=self.profile,
            package=self.eco_pkg,
            monthly_amount=self.eco_pkg.price,
            billing_cycle='monthly',
            auto_renew=False,
            payment_status='success',
            subscription_status='expired',
            start_date=today - timedelta(days=40),
            end_date=today - timedelta(days=10),
        )

        self._login_session()
        with patch('apps.superadmin.razorpay_utils.create_razorpay_order', return_value=('order_exp_qtr', True, None)):
            init_res = self.client.post(
                reverse('farmer-switch-plan'),
                data=json.dumps({'action': 'initiate', 'package_id': self.pro_pkg.id, 'billing_cycle': 'quarterly'}),
                content_type='application/json',
            )
        self.assertEqual(init_res.status_code, 200)
        sig = self._sign('order_exp_qtr', 'pay_exp_qtr')
        verify_res = self.client.post(reverse('farmer-verify-payment'), {
            'razorpay_payment_id': 'pay_exp_qtr',
            'razorpay_order_id': 'order_exp_qtr',
            'razorpay_signature': sig,
            'package_id': self.pro_pkg.id,
            'mobile_number': self.mobile,
            'billing_cycle': 'quarterly',
        })
        self.assertEqual(verify_res.status_code, 200)
        new_active = Subscription.objects.get(farmer=self.profile, subscription_status='active')
        self.assertEqual(new_active.package_id, self.pro_pkg.id)
        self.assertEqual(new_active.billing_cycle, 'quarterly')
        self.assertEqual((new_active.end_date - new_active.start_date).days, 90)

    def test_mobile_api_subscription_endpoints_and_no_hardcoded_defaults(self):
        from datetime import timedelta
        from unittest.mock import patch
        from django.utils import timezone
        from rest_framework.test import APIClient
        from apps.farmer.api_views import _mobile_dashboard_overview
        from apps.superadmin.models import Subscription

        api = APIClient()
        api.force_authenticate(user=self.user)

        # No subscription -> real empty state, no hardcoded Elite Plan / ₹2,499
        overview = _mobile_dashboard_overview(self.profile, timezone.localdate())
        self.assertEqual(overview['subscription']['plan_name'], 'No Active Plan')
        self.assertEqual(overview['subscription']['plan_price'], 0.0)
        self.assertFalse(overview['subscription']['has_subscription'])

        # Purchase Quarterly via mobile API
        with patch('apps.superadmin.razorpay_utils.create_razorpay_order', return_value=('order_api_qtr', True, None)):
            res_init = api.post(reverse('api-farmer-switch-plan'), {
                'package_id': self.eco_pkg.id,
                'billing_cycle': 'quarterly',
            }, format='json')
        self.assertEqual(res_init.status_code, 200)
        self.assertEqual(res_init.data['billing_cycle'], 'quarterly')

        # Cancelled checkout does not activate subscription
        res_cancel = api.post(reverse('api-farmer-verify-payment'), {
            'razorpay_order_id': 'order_api_qtr',
            'status': 'cancelled',
        }, format='json')
        self.assertEqual(res_cancel.status_code, 200)
        self.assertFalse(Subscription.objects.filter(farmer=self.profile, subscription_status='active').exists())

        # Re-initiate and verify payment via API
        with patch('apps.superadmin.razorpay_utils.create_razorpay_order', return_value=('order_api_qtr_2', True, None)):
            api.post(reverse('api-farmer-switch-plan'), {
                'package_id': self.eco_pkg.id,
                'billing_cycle': 'quarterly',
            }, format='json')
        sig = self._sign('order_api_qtr_2', 'pay_api_qtr_2')
        res_verify = api.post(reverse('api-farmer-verify-payment'), {
            'package_id': self.eco_pkg.id,
            'billing_cycle': 'quarterly',
            'razorpay_order_id': 'order_api_qtr_2',
            'razorpay_payment_id': 'pay_api_qtr_2',
            'razorpay_signature': sig,
        }, format='json')
        self.assertEqual(res_verify.status_code, 200)
        self.assertEqual(res_verify.data['billing_cycle'], 'quarterly')

        # GET subscription API + mobile overview reflect Quarterly
        res_get = api.get(reverse('api-farmer-subscription'))
        self.assertEqual(res_get.status_code, 200)
        self.assertEqual(res_get.data['billing_cycle'], 'quarterly')
        self.assertEqual(res_get.data['billing_cycle_label'], 'Quarterly')

        overview_after = _mobile_dashboard_overview(self.profile, timezone.localdate())
        self.assertEqual(overview_after['subscription']['billing_cycle'], 'quarterly')
        self.assertEqual(overview_after['subscription']['plan_price'], float(self.eco_pkg.price_quarterly))
        self.assertEqual(overview_after['subscription']['days_left'], 90)

    def test_pending_payment_does_not_activate_until_verified(self):
        import json
        from unittest.mock import patch
        from apps.superadmin.models import Subscription
        from apps.superadmin.subscription_utils import get_farmer_subscription_details

        self._login_session()
        with patch('apps.superadmin.razorpay_utils.create_razorpay_order', return_value=('order_pending_1', True, None)):
            init_res = self.client.post(
                reverse('farmer-switch-plan'),
                data=json.dumps({'action': 'initiate', 'package_id': self.eco_pkg.id, 'billing_cycle': 'quarterly'}),
                content_type='application/json',
            )
        self.assertEqual(init_res.status_code, 200)

        sub_info = get_farmer_subscription_details(self.profile)
        self.assertTrue(sub_info['is_pending'])
        self.assertFalse(sub_info['can_mutate'])

        # Attempt bypass with free_tier_bypass on paid order -> must fail
        bypass_res = self.client.post(reverse('farmer-verify-payment'), {
            'razorpay_payment_id': 'pay_free_mock',
            'razorpay_order_id': 'order_pending_1',
            'razorpay_signature': 'free_tier_bypass',
            'package_id': self.eco_pkg.id,
            'mobile_number': self.mobile,
            'billing_cycle': 'quarterly',
        })
        self.assertEqual(bypass_res.status_code, 400)
        self.assertFalse(Subscription.objects.filter(farmer=self.profile, subscription_status='active').exists())

        # Re-initiate and verify with valid signature -> activates Quarterly
        with patch('apps.superadmin.razorpay_utils.create_razorpay_order', return_value=('order_pending_2', True, None)):
            self.client.post(
                reverse('farmer-switch-plan'),
                data=json.dumps({'action': 'initiate', 'package_id': self.eco_pkg.id, 'billing_cycle': 'quarterly'}),
                content_type='application/json',
            )
        sig = self._sign('order_pending_2', 'pay_pending_2')
        ok_res = self.client.post(reverse('farmer-verify-payment'), {
            'razorpay_payment_id': 'pay_pending_2',
            'razorpay_order_id': 'order_pending_2',
            'razorpay_signature': sig,
            'package_id': self.eco_pkg.id,
            'mobile_number': self.mobile,
            'billing_cycle': 'quarterly',
        })
        self.assertEqual(ok_res.status_code, 200)
        sub_info_after = get_farmer_subscription_details(self.profile)
        self.assertTrue(sub_info_after['can_mutate'])
        self.assertEqual(sub_info_after['billing_cycle'], 'quarterly')

    def test_cross_tier_quarterly_to_monthly_upgrade_and_downgrade_blocked(self):
        import json
        from datetime import timedelta
        from django.utils import timezone
        from apps.superadmin.models import Subscription
        from apps.superadmin.subscription_utils import calculate_plan_switch_quote

        today = timezone.now().date()
        # Start on Eco-Starter Quarterly (₹12,201 paid)
        qtr_sub = Subscription.objects.create(
            farmer=self.profile,
            package=self.eco_pkg,
            monthly_amount=self.eco_pkg.price_quarterly,
            amount_paid=self.eco_pkg.price_quarterly,
            billing_cycle='quarterly',
            auto_renew=False,
            payment_status='success',
            subscription_status='active',
            start_date=today,
            end_date=today + timedelta(days=90),
        )

        # Higher tier Monthly (Bumper Yield ₹10,707) -> allowed as Low -> High tier upgrade, ₹0 net payable, extra days from excess credit
        up_quote = calculate_plan_switch_quote(self.profile, self.pro_pkg, target_cycle='monthly')
        self.assertEqual(up_quote['action_type'], 'upgrade')
        self.assertTrue(up_quote['can_switch'])
        self.assertEqual(up_quote['net_payable'], 0.0)
        self.assertGreater(up_quote['extra_days'], 0)
        self.assertGreater(up_quote['effective_duration_days'], 30)

        # Upgrade to Pro Quarterly, then verify downgrades to Eco (all cycles) are strictly blocked while active
        qtr_sub.package = self.pro_pkg
        qtr_sub.monthly_amount = self.pro_pkg.price_quarterly
        qtr_sub.save()

        self._login_session()
        for cycle in ('monthly', 'quarterly', 'yearly'):
            q = calculate_plan_switch_quote(self.profile, self.eco_pkg, target_cycle=cycle)
            self.assertFalse(q['can_switch'])
            self.assertTrue(q['is_downgrade'])
            self.assertFalse(q['is_scheduled_switch'])

            down_res = self.client.post(
                reverse('farmer-switch-plan'),
                data=json.dumps({'action': 'schedule_switch', 'package_id': self.eco_pkg.id, 'billing_cycle': cycle}),
                content_type='application/json',
            )
            self.assertEqual(down_res.status_code, 400)
            qtr_sub.refresh_from_db()
            self.assertIsNone(qtr_sub.scheduled_package_id)
            self.assertIsNone(qtr_sub.scheduled_billing_cycle)


