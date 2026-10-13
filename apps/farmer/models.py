from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class FarmerStaff(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="staff_profile")
    farmer = models.ForeignKey('accounts.Profile', on_delete=models.CASCADE, related_name="staffs")
    name = models.CharField(max_length=100)
    mobile_number = models.CharField(max_length=20)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=20, null=True, blank=True)
    document_type = models.CharField(max_length=100, null=True, blank=True)
    document_file = models.FileField(upload_to="staff_documents/", null=True, blank=True)
    profile_image = models.FileField(upload_to="staff_profiles/", null=True, blank=True)
    is_role_manager = models.BooleanField(default=False)
    role = models.CharField(max_length=100, default="Staff")
    status = models.CharField(max_length=20, default="0")
    permissions = models.JSONField(default=list, blank=True)
    base_daily_rate = models.DecimalField(max_digits=10, decimal_places=2, default=500)
    salary_frequency = models.CharField(max_length=50, null=True, blank=True)
    salary = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    designation = models.ForeignKey('superadmin.Designation', on_delete=models.SET_NULL, null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

    @property
    def permissions_json(self):
        import json
        from typing import Any, cast
        perms = cast(Any, self.permissions)
        if perms is None:
            return "[]"
        if isinstance(perms, list):
            return json.dumps(perms)
        try:
            return json.dumps(list(perms))
        except Exception:
            return "[]"

class FarmerRole(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    farmer = models.ForeignKey('accounts.Profile', on_delete=models.CASCADE, related_name="custom_roles")
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class Farm(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    farmer = models.ForeignKey('accounts.Profile', on_delete=models.CASCADE, related_name="farms")
    land_name = models.CharField(max_length=255)
    total_area = models.CharField(max_length=100)
    crop_type = models.ForeignKey('superadmin.CropType', on_delete=models.SET_NULL, null=True, blank=True)
    latitude = models.CharField(max_length=50, blank=True, null=True)
    longitude = models.CharField(max_length=50, blank=True, null=True)
    state = models.ForeignKey('superadmin.State', on_delete=models.SET_NULL, null=True, blank=True)
    district = models.ForeignKey('superadmin.City', on_delete=models.SET_NULL, null=True, blank=True)
    taluk = models.ForeignKey('superadmin.Taluk', on_delete=models.SET_NULL, null=True, blank=True)
    street = models.CharField(max_length=255, blank=True, null=True)
    door = models.CharField(max_length=100, blank=True, null=True)
    pincode = models.CharField(max_length=20, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    soil_type = models.CharField(max_length=100, blank=True, null=True)
    soil_ph = models.CharField(max_length=50, blank=True, null=True)
    irrigation_type = models.CharField(max_length=100, blank=True, null=True)
    health_indicator = models.CharField(max_length=100, default="Good")
    notes = models.TextField(blank=True, null=True)
    farming_type = models.CharField(max_length=100, blank=True, null=True)
    organic_registration_number = models.CharField(max_length=255, blank=True, null=True)
    fencing_type = models.CharField(max_length=100, blank=True, null=True)
    fencing_maintenance_schedule = models.CharField(max_length=100, blank=True, null=True)
    fencing_details = models.TextField(blank=True, null=True)
    land_allocation_blueprint = models.FileField(upload_to="farm_blueprints/", null=True, blank=True)
    land_allocation_description = models.TextField(blank=True, null=True)
    water_sources = models.JSONField(default=list, blank=True, null=True)
    electricity_details = models.JSONField(default=list, blank=True, null=True)
    farm_documents = models.JSONField(default=list, blank=True, null=True)
    geo_marks = models.JSONField(default=list, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return str(self.land_name)

    @property
    def water_sources_json(self):
        import json
        try:
            return json.dumps(self.water_sources or [])
        except Exception:
            return "[]"

    @property
    def electricity_details_json(self):
        import json
        try:
            return json.dumps(self.electricity_details or [])
        except Exception:
            return "[]"

    @property
    def farm_documents_json(self):
        import json
        try:
            return json.dumps(self.farm_documents or [])
        except Exception:
            return "[]"

class CropSplit(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    farm = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name="splits")
    crop = models.ForeignKey('superadmin.Crop', on_delete=models.SET_NULL, null=True)
    crop_category = models.CharField(max_length=100, blank=True, null=True)
    crop_pattern = models.CharField(max_length=100, blank=True, null=True)
    crop_variety = models.CharField(max_length=100, blank=True, null=True)
    harvest_type = models.CharField(max_length=50, default="single", blank=True, null=True)
    total_flushes = models.IntegerField(default=1, blank=True, null=True, help_text="Total planned flushes for multiple harvest")
    acreage = models.DecimalField(max_digits=10, decimal_places=2)
    has_advanced_details = models.BooleanField(default=False)
    crop_latitude = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    crop_longitude = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    row_count = models.IntegerField(null=True, blank=True)
    plant_count = models.IntegerField(null=True, blank=True)
    growth_duration = models.IntegerField(help_text="Growth duration in days", default=90)
    duration_min = models.IntegerField(null=True, blank=True)
    duration_max = models.IntegerField(null=True, blank=True)
    cultivation_method = models.CharField(max_length=255, blank=True, null=True)
    supervisor = models.ForeignKey(FarmerStaff, on_delete=models.SET_NULL, null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    stage = models.CharField(max_length=50, default="Sown")
    expected_yield = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    expected_yield_unit = models.CharField(max_length=50, default="Ton", blank=True, null=True)
    crop_notes = models.TextField(blank=True, null=True)
    harvest_batches = models.JSONField(default=list, blank=True, null=True)
    seed_details = models.CharField(max_length=255, blank=True, null=True)
    plant_spacing = models.CharField(max_length=100, blank=True, null=True)
    geo_marks = models.JSONField(default=list, blank=True, null=True)
    yield_tons = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_per_mt = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    total_sale_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.crop.name if self.crop else 'Unknown'} - {self.acreage} Ac"

class FarmActivity(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    farm = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name="activities")
    activity_type = models.CharField(max_length=100)
    asset_name = models.CharField(max_length=255)
    quantity_metric = models.CharField(max_length=100)
    operator_name = models.CharField(max_length=255)
    notes = models.TextField(blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.activity_type} - {self.asset_name}"

class StaffAttendance(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    staff = models.ForeignKey(FarmerStaff, on_delete=models.CASCADE, related_name="attendances")
    date = models.DateField()
    status = models.CharField(max_length=10) # 'P', 'A', 'H', '-'
    is_paid = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

class StaffAdvance(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    staff = models.ForeignKey(FarmerStaff, on_delete=models.CASCADE, related_name="advances")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField(auto_now_add=True)
    payment_method = models.CharField(max_length=50)
    is_paid = models.BooleanField(default=False)

class StaffPayroll(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    staff = models.ForeignKey(FarmerStaff, on_delete=models.CASCADE, related_name="payrolls")
    month = models.CharField(max_length=20) # e.g. June 2026
    base_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    present_days = models.IntegerField(default=0)
    absent_days = models.IntegerField(default=0)
    advance_deduction = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    incentives = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    net_pay = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=50)
    paid_on = models.DateField(auto_now_add=True)

class FarmTask(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    farm = models.ForeignKey(Farm, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    farmer = models.ForeignKey('accounts.Profile', on_delete=models.CASCADE, related_name="tasks", null=True, blank=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    priority = models.CharField(max_length=50, default="Medium Priority")
    priority_level = models.ForeignKey('superadmin.TaskPriority', on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    task_category = models.ForeignKey('superadmin.TaskCategory', on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    work_timeline = models.CharField(max_length=50, default="Full Day")
    associated_split = models.ForeignKey(CropSplit, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    assigned_worker = models.ForeignKey(FarmerStaff, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_tasks")
    assigned_workers = models.ManyToManyField(FarmerStaff, related_name="multi_assigned_tasks", blank=True)
    status = models.CharField(max_length=50, default="Pending") # Pending, In Progress, Completed
    task_date = models.DateField(default=timezone.now)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    total_hours = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    actual_hours = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class LedgerTransaction(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist

    farmer = models.ForeignKey('accounts.Profile', on_delete=models.CASCADE, related_name="ledger_transactions")
    farm = models.ForeignKey(Farm, on_delete=models.SET_NULL, null=True, blank=True, related_name="ledger_transactions")
    vendor = models.ForeignKey('FarmerVendor', on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions")
    close_date = models.DateField()
    description = models.CharField(max_length=255)
    payment_method = models.CharField(max_length=100, blank=True, null=True)
    category = models.CharField(max_length=100)
    category_color = models.CharField(max_length=20, default="#6c757d")
    tags = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=50)  # Cost, Income, Pending, In Transit, Finished
    amount = models.DecimalField(max_digits=12, decimal_places=2)  # negative for outflow
    balance_due = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # outstanding balance
    quantity = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    unit = models.CharField(max_length=50, blank=True, null=True)
    is_recurring = models.BooleanField(default=False)
    repeat_cycle = models.CharField(max_length=20, blank=True, null=True)  # Weekly, Monthly, Yearly
    reminder_type = models.CharField(max_length=30, blank=True, null=True)  # On Due Date, 1 Day Before etc.
    reminder_custom_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def document(self):
        if self.tags and 'Doc ID:' in self.tags:
            try:
                import re
                match = re.search(r'Doc ID:\s*(\d+)', self.tags)
                if match:
                    doc_id = int(match.group(1))
                    from apps.superadmin.models import Document
                    return Document.objects.filter(id=doc_id).first()
            except Exception:
                pass
        return None

class FarmerCustomer(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    farmer = models.ForeignKey('accounts.Profile', on_delete=models.CASCADE, related_name="customers")
    name = models.CharField(max_length=255)
    company_name = models.CharField(max_length=255, blank=True, null=True)
    contact_number = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    district = models.CharField(max_length=100, blank=True, null=True)
    pincode = models.CharField(max_length=20, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    gst_number = models.CharField(max_length=50, blank=True, null=True)
    customer_type = models.ForeignKey('superadmin.CustomerType', on_delete=models.SET_NULL, null=True, blank=True, related_name="customers")
    document = models.ForeignKey('superadmin.Document', on_delete=models.SET_NULL, null=True, blank=True, related_name="customer_documents")
    status = models.CharField(max_length=50, default="Active")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        if self.company_name:
            return f"{self.name} ({self.company_name})"
        return self.name

class FarmerOutstanding(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist

    farmer = models.ForeignKey('accounts.Profile', on_delete=models.CASCADE, related_name="outstandings")
    customer = models.ForeignKey(FarmerCustomer, on_delete=models.SET_NULL, null=True, blank=True)
    vendor = models.ForeignKey('FarmerVendor', on_delete=models.SET_NULL, null=True, blank=True, related_name="outstandings")
    invoice_no = models.CharField(max_length=100)
    due_date = models.DateField()
    amount_total = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=50, default="Unpaid") # Unpaid, Paid, Partial
    created_at = models.DateTimeField(auto_now_add=True)

class FarmerVendor(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    farmer = models.ForeignKey('accounts.Profile', on_delete=models.CASCADE, related_name="vendors")
    vendor_name = models.CharField(max_length=255)
    business_name = models.CharField(max_length=255, blank=True, null=True)
    mobile_number = models.CharField(max_length=20, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    district = models.CharField(max_length=100, blank=True, null=True)
    pincode = models.CharField(max_length=20, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    gst_number = models.CharField(max_length=50, blank=True, null=True)
    shop_name = models.CharField(max_length=255, blank=True, null=True)
    document = models.ForeignKey('superadmin.Document', on_delete=models.SET_NULL, null=True, blank=True, related_name="vendor_documents")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.vendor_name} - {self.business_name}"

    @property
    def total_payables(self):
        from django.db.models import Q
        outstandings = FarmerOutstanding.objects.filter(
            Q(vendor=self) | Q(farmer=self.farmer, invoice_no__icontains=self.vendor_name)
        ).exclude(status='Paid')
        return sum(o.amount_total - o.amount_paid for o in outstandings)

    @property
    def total_receivables(self):
        from functools import reduce
        import operator
        from django.db.models import Sum, Q
        txs = self.get_all_transactions().filter(
            reduce(operator.or_, [Q(amount__gt=0), Q(tags='Inflow'), Q(status='Received')])
        )
        total = txs.aggregate(total=Sum('amount'))['total'] or 0
        return float(abs(total))

    def get_all_transactions(self):
        from django.db.models import Q
        return LedgerTransaction.objects.filter(
            Q(vendor=self) | Q(farmer=self.farmer, description__icontains=self.vendor_name)
        ).order_by('-close_date', '-id')


class FarmerInventory(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    farmer = models.ForeignKey('accounts.Profile', on_delete=models.CASCADE, related_name="inventories")
    farm = models.ForeignKey(Farm, on_delete=models.SET_NULL, null=True, blank=True, related_name="inventories")
    item_name = models.CharField(max_length=255)
    category = models.CharField(max_length=100)
    quantity = models.IntegerField(default=1)
    unit = models.CharField(max_length=50, default="Units")
    safety_stock = models.IntegerField(default=5)
    location = models.CharField(max_length=255, default="Main Warehouse")
    status = models.CharField(max_length=50, default="In Stock")
    license_plate = models.CharField(max_length=50, blank=True, null=True)
    last_service_date = models.DateField(blank=True, null=True)
    next_service_date = models.DateField(blank=True, null=True)
    fuel_type = models.CharField(max_length=50, blank=True, null=True)
    
    insurance_file = models.FileField(upload_to="inventory/insurance/", blank=True, null=True)
    insurance_expiry = models.DateField(blank=True, null=True)
    
    rc_file = models.FileField(upload_to="inventory/rc/", blank=True, null=True)
    rc_expiry = models.DateField(blank=True, null=True)
    
    pollution_file = models.FileField(upload_to="inventory/pollution/", blank=True, null=True)
    pollution_expiry = models.DateField(blank=True, null=True)
    
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Price per unit of this inventory item")
    rental_available = models.BooleanField(default=False)
    rental_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, blank=True, null=True)
    rental_unit = models.CharField(max_length=50, default="Per Day", blank=True, null=True)
    purchase_date = models.DateField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.item_name)


class InventoryStockLog(models.Model):
    ACTION_CHOICES = [('add', 'In (Return/Restock)'), ('remove', 'Out (Usage/Deduction)')]
    inventory = models.ForeignKey(FarmerInventory, on_delete=models.CASCADE, related_name='stock_logs')
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    quantity = models.FloatField()
    notes = models.TextField(blank=True, null=True)
    log_date = models.DateField()
    qty_before = models.FloatField(default=0)
    qty_after = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-log_date', '-created_at']

    def __str__(self):
        return f"{self.inventory.item_name} — {self.action} {self.quantity}"


# --- Global CRUD Notifications for Farmer Models ---
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

# List of models we want to track CRUD for automatically
TRACKED_MODELS = [
    'Farm', 'CropSplit', 'FarmerVendor', 'FarmerInventory',
    'LedgerTransaction', 'FarmTask', 'FarmerCustomer'
]

def get_farmer_user(instance):
    if hasattr(instance, 'farmer') and instance.farmer:
        return instance.farmer.user
    if hasattr(instance, 'farm') and instance.farm and instance.farm.farmer:
        return instance.farm.farmer.user
    if hasattr(instance, 'split') and instance.split and instance.split.farm and instance.split.farm.farmer:
        return instance.split.farm.farmer.user
    return None

def trigger_crud_notification(instance, action):
    model_name = instance.__class__.__name__
    if model_name not in TRACKED_MODELS:
        return

    user = get_farmer_user(instance)
    if not user:
        return

    # Pretty name formatting
    pretty_name = ''.join([' ' + c if c.isupper() else c for c in model_name]).strip()

    # Customize message
    display_name = str(instance) if str(instance) and len(str(instance)) < 50 else f"A {pretty_name} record"

    title = f"{pretty_name} {action}d"
    message = f"{display_name} was successfully {action.lower()}d."

    icon_class = "ri-check-line" if action == "Create" else "ri-pencil-line" if action == "Update" else "ri-delete-bin-line"
    bg_color = "success" if action == "Create" else "info" if action == "Update" else "warning"

    from auth.models import SystemNotification
    SystemNotification.objects.create(
        user=user,
        title=title,
        message=message,
        icon_class=icon_class,
        bg_color=bg_color
    )

@receiver(post_save)
def notify_on_save(sender, instance, created, **kwargs):
    if sender.__module__ == 'apps.farmer.models':
        action = "Create" if created else "Update"
        if not getattr(instance, '_crud_notified', False):
            trigger_crud_notification(instance, action)
            instance._crud_notified = True

@receiver(post_delete)
def notify_on_delete(sender, instance, **kwargs):
    if sender.__module__ == 'apps.farmer.models':
        trigger_crud_notification(instance, "Delete")
