import uuid
from django.db import models
from django.contrib.auth.models import User

class Country(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    name = models.CharField(max_length=255)
    sort_name = models.CharField(max_length=10, blank=True, null=True)
    phone_code = models.CharField(max_length=20, blank=True, null=True)
    status = models.CharField(max_length=10, default="0")

    def __str__(self):
        return str(self.name)

class State(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name='states')
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=10, default="0")

    def __str__(self):
        return str(self.name)

class City(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    state = models.ForeignKey(State, on_delete=models.CASCADE, related_name='cities')
    cid = models.CharField(max_length=50, blank=True, null=True)
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=10, default="0")

    def __str__(self):
        return str(self.name)

class Taluk(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    city = models.ForeignKey(City, on_delete=models.CASCADE, related_name='taluks')
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=10, default="0")

    def __str__(self):
        return str(self.name)

class GeneralSetting(models.Model):
    objects = models.Manager()
    logo = models.ImageField(upload_to='settings/logos/', blank=True, null=True)
    favicon = models.ImageField(upload_to='settings/favicons/', blank=True, null=True)
    title = models.CharField(max_length=255, default="Agriot Automation")
    website = models.CharField(max_length=255, default="www.agriot.com")
    contact_person = models.CharField(max_length=255, default="Nalina Kumar")
    email = models.EmailField(default="nalinakumar@gmail.com")
    date_format = models.CharField(max_length=50, default="dd-mm-yyyy")
    timezone = models.CharField(max_length=100, default="Asia/Kolkata")

    country = models.ForeignKey(Country, on_delete=models.SET_NULL, null=True, blank=True)
    state = models.ForeignKey(State, on_delete=models.SET_NULL, null=True, blank=True)
    city = models.ForeignKey(City, on_delete=models.SET_NULL, null=True, blank=True)

    street = models.CharField(max_length=255, default="Kovil Street")
    door_no = models.CharField(max_length=50, default="45A")
    pincode = models.CharField(max_length=20, default="624202")
    gst_number = models.CharField(max_length=100, blank=True, null=True)
    cin = models.CharField(max_length=100, blank=True, null=True)
    weather_api_key = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return str(self.title)


class Package(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    max_acre_support = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    modules = models.JSONField(default=list)
    features = models.JSONField(default=list)
    my_land = models.CharField(max_length=255, blank=True, null=True)
    my_crop = models.CharField(max_length=255, blank=True, null=True)
    my_users = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return str(self.name)

from auth.models import Profile

class Subscription(models.Model):
    objects = models.Manager()
    farmer = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='subscriptions')
    package = models.ForeignKey(Package, on_delete=models.SET_NULL, null=True)
    monthly_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    auto_renew = models.BooleanField(default=True)
    payment_status = models.CharField(max_length=50, default="pending")
    subscription_status = models.CharField(max_length=50, default="active")
    start_date = models.DateField(auto_now_add=True)
    end_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Subscription for {self.farmer.user.first_name}"

class ProductType(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class InventoryItem(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    product_type = models.ForeignKey(ProductType, on_delete=models.CASCADE, related_name="inventory_items", null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class HarvestingFrequency(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class ProductUnit(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class SoilType(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class IrrigationType(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class WaterSourceType(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class ElectricityType(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class FarmingType(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class FencingType(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class CropCategory(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class CropPattern(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class CropVariety(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class CropQuality(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class MaintenanceSchedule(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class CropType(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class Crop(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    crop_type = models.ForeignKey(CropType, on_delete=models.CASCADE, related_name='crops')
    image = models.ImageField(upload_to='crop_images/', blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class SalesDeduction(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class ScheduleActivityType(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class LocationCategory(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class ManPowerWorkType(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class Designation(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)


class GuidelineCategory(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class Guideline(models.Model):
    objects = models.Manager()
    title = models.CharField(max_length=255)
    farming_section = models.ForeignKey(GuidelineCategory, on_delete=models.SET_NULL, null=True, blank=True)
    author_name = models.CharField(max_length=255, blank=True, null=True)
    read_time = models.CharField(max_length=100, blank=True, null=True)
    search_tags = models.CharField(max_length=255, blank=True, null=True)
    detailed_content = models.TextField(blank=True, null=True)
    document_file = models.FileField(upload_to='guidelines/documents/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.title)

class DocumentCategory(models.Model):
    objects = models.Manager()
    DOCUMENT_TYPES = (
        ('My Land', 'My Land'),
        ('My Expenses', 'My Expenses'),
        ('My Inventory', 'My Inventory'),
    )
    document_type = models.CharField(max_length=50, choices=DOCUMENT_TYPES)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.document_type} - {self.name}"

class MarketPriceIndex(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    crop_name = models.CharField(max_length=255)
    base_price = models.DecimalField(max_digits=12, decimal_places=2, default=0.0)
    unit = models.CharField(max_length=50, default="Ton")
    trend_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.0)
    trend_direction = models.CharField(max_length=10, choices=[('up', 'Up'), ('down', 'Down')], default='up')

    def __str__(self):
        return str(self.crop_name)

class DepotTerminal(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    DEMAND_CHOICES = (
        ('High', 'High'),
        ('Medium', 'Medium'),
        ('Low', 'Low'),
    )
    name = models.CharField(max_length=255)
    terminal_id = models.CharField(max_length=50, blank=True, null=True)
    location = models.CharField(max_length=255)
    district = models.CharField(max_length=255, blank=True, null=True)
    distance_km = models.DecimalField(max_digits=8, decimal_places=2, default=0.0)
    demand_level = models.CharField(max_length=20, choices=DEMAND_CHOICES, default='Medium')
    sourcing_active = models.BooleanField(default=True)
    contact_person = models.CharField(max_length=255, blank=True, null=True)
    contact_phone = models.CharField(max_length=50, blank=True, null=True)
    available_crops = models.JSONField(default=list, blank=True, null=True)
    custom_crops = models.JSONField(default=list, blank=True, null=True)
    
    def save(self, *args, **kwargs):
        if not self.terminal_id:
            super().save(*args, **kwargs)
            self.terminal_id = f"m-{self.id}"  # type: ignore
            self.save()
        else:
            super().save(*args, **kwargs)

    def __str__(self):
        return str(self.name)

class DepotCropPricing(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    depot = models.ForeignKey(DepotTerminal, on_delete=models.CASCADE, related_name='crop_prices')
    crop_index = models.ForeignKey(MarketPriceIndex, on_delete=models.CASCADE, related_name='depot_prices')
    terminal_rate = models.DecimalField(max_digits=12, decimal_places=2, default=0.0)
    markup_offset = models.DecimalField(max_digits=12, decimal_places=2, default=0.0)
    trading_restricted = models.BooleanField(default=False)
    
    class Meta:
        unique_together = ('depot', 'crop_index')

    def __str__(self):
        return f"{self.depot.name} - {self.crop_index.crop_name}"

class DailyPriceHistory(models.Model):
    objects = models.Manager()
    depot = models.ForeignKey(DepotTerminal, on_delete=models.CASCADE, related_name='price_history')
    crop_index = models.ForeignKey(MarketPriceIndex, on_delete=models.CASCADE, related_name='price_history')
    price = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField()
    
    class Meta:
        unique_together = ('depot', 'crop_index', 'date')
        ordering = ['-date']

    def __str__(self):
        return f"{self.date} - {self.depot.name} - {self.crop_index.crop_name} - {self.price}"

class CropBlueprint(models.Model):
    objects = models.Manager()
    farmer = models.ForeignKey('accounts.Profile', on_delete=models.CASCADE, null=True, blank=True, related_name='crop_blueprints')
    crop_name = models.CharField(max_length=255)
    cultivation_season = models.CharField(max_length=100)
    yield_cycle_days = models.IntegerField(default=90)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.crop_name} Blueprint"

class BlueprintStage(models.Model):
    objects = models.Manager()
    blueprint = models.ForeignKey(CropBlueprint, on_delete=models.CASCADE, related_name='stages')
    milestone_title = models.CharField(max_length=255)
    activity_type = models.CharField(max_length=100)
    day_offset = models.IntegerField(default=0)
    priority_level = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.milestone_title)

class NearbyLocation(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    name = models.CharField(max_length=255)
    service_class = models.CharField(max_length=100)
    contact_person = models.CharField(max_length=255, blank=True, null=True)
    designation = models.CharField(max_length=100, blank=True, null=True)
    contact_number = models.CharField(max_length=50, blank=True, null=True)
    email = models.EmailField(max_length=255, blank=True, null=True)
    website = models.CharField(max_length=255, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    service_rating = models.DecimalField(max_digits=3, decimal_places=1, default=0.0, blank=True, null=True)
    distance_km = models.DecimalField(max_digits=8, decimal_places=2, default=0.0, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

class LogisticsWorker(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    name = models.CharField(max_length=255)
    designation = models.CharField(max_length=100, blank=True, null=True)
    workforce_class = models.CharField(max_length=100, blank=True, null=True)
    specialty_skill = models.CharField(max_length=100, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    wage_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    wage_unit = models.CharField(max_length=50, default='Day', blank=True, null=True)
    contact_phone = models.CharField(max_length=50, blank=True, null=True)
    rating = models.DecimalField(max_digits=3, decimal_places=1, default=0.0, blank=True, null=True)
    status = models.CharField(max_length=50, default='Available')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)



class RentalEquipment(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist
    equipment_name = models.CharField(max_length=255)
    hardware_class = models.CharField(max_length=100)
    price_per_day = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    owner_name = models.CharField(max_length=255, null=True, blank=True)
    contact_phone = models.CharField(max_length=50, null=True, blank=True)
    condition_rating = models.CharField(max_length=50, default="Excellent")
    status = models.CharField(max_length=50, default="Available")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.equipment_name} - {self.hardware_class}"


class Document(models.Model):
    objects = models.Manager()
    DoesNotExist = models.ObjectDoesNotExist

    uuid_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    title = models.CharField(max_length=255)
    category = models.CharField(max_length=100, default="General")
    description = models.TextField(blank=True, null=True)
    file = models.FileField(upload_to="documents/")
    file_size_bytes = models.BigIntegerField(default=0)
    file_type = models.CharField(max_length=100, blank=True, null=True)

    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="uploaded_documents")
    farmer = models.ForeignKey("accounts.Profile", on_delete=models.SET_NULL, null=True, blank=True, related_name="documents")

    is_public_shared = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.title} ({self.category})"


class HardwareClass(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)


class ExpenseType(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_vendor_based = models.BooleanField(default=False, help_text="Show Vendor and Inventory tracking fields")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

    class Meta:
        verbose_name = "Expense Type"
        verbose_name_plural = "Expense Types"


class ExpenseUnit(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

    class Meta:
        verbose_name = "Expense Unit"
        verbose_name_plural = "Expense Units"


class CustomerType(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

    class Meta:
        verbose_name = "Customer Type"
        verbose_name_plural = "Customer Types"


class InventoryCategory(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    layout_type = models.CharField(max_length=50, default="Warehouse") # Warehouse, Machinery, Tools
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

    class Meta:
        verbose_name = "Inventory Category"
        verbose_name_plural = "Inventory Categories"


class StorageLocation(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

    class Meta:
        verbose_name = "Storage Location"
        verbose_name_plural = "Storage Locations"


class TaskCategory(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_farm_required = models.BooleanField(default=False)
    is_crop_required = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

    class Meta:
        verbose_name = "Task Category"
        verbose_name_plural = "Task Categories"


class TaskPriority(models.Model):
    objects = models.Manager()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.name)

    class Meta:
        verbose_name = "Task Priority"
        verbose_name_plural = "Task Priorities"

