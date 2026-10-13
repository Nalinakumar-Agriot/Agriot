from django.core.management.base import BaseCommand
from apps.superadmin.models import (
    ProductType, InventoryItem, HarvestingFrequency, ProductUnit,
    SoilType, CropType, Crop, SalesDeduction, ScheduleActivityType,
    LocationCategory, ManPowerWorkType, GuidelineCategory,
    DocumentCategory, HardwareClass
)

class Command(BaseCommand):
    help = 'Seeds two records for every settings model'

    def handle(self, *args, **kwargs):
        # 1. ProductType
        pt1, _ = ProductType.objects.get_or_create(name="Fertilizers", description="Chemical and organic fertilizers")
        pt2, _ = ProductType.objects.get_or_create(name="Seeds", description="High-yield seeds")

        # 2. InventoryItem
        InventoryItem.objects.get_or_create(name="Urea", description="Nitrogen fertilizer", product_type=pt1)
        InventoryItem.objects.get_or_create(name="Tomato Seeds", description="Hybrid tomato seeds", product_type=pt2)

        # 3. HarvestingFrequency
        HarvestingFrequency.objects.get_or_create(name="Weekly", description="Harvest every week")
        HarvestingFrequency.objects.get_or_create(name="Monthly", description="Harvest every month")

        # 4. ProductUnit
        ProductUnit.objects.get_or_create(name="Kilogram", slug="kg")
        ProductUnit.objects.get_or_create(name="Liters", slug="l")

        # 5. SoilType
        SoilType.objects.get_or_create(name="Alluvial", description="Highly fertile soil")
        SoilType.objects.get_or_create(name="Red Soil", description="Rich in iron content")

        # 6. CropType
        ct1, _ = CropType.objects.get_or_create(name="Cereals", description="Grain crops")
        ct2, _ = CropType.objects.get_or_create(name="Vegetables", description="Fresh vegetables")

        # 7. Crop
        Crop.objects.get_or_create(name="Wheat", crop_type=ct1, description="Winter cereal grain")
        Crop.objects.get_or_create(name="Tomato", crop_type=ct2, description="Red edible fruit")

        # 8. SalesDeduction
        SalesDeduction.objects.get_or_create(name="Transport Fee", description="Cost for transporting goods")
        SalesDeduction.objects.get_or_create(name="Packaging Fee", description="Cost for packaging materials")

        # 9. ScheduleActivityType
        ScheduleActivityType.objects.get_or_create(name="Sowing", description="Planting seeds")
        ScheduleActivityType.objects.get_or_create(name="Irrigation", description="Watering the crops")

        # 10. LocationCategory
        LocationCategory.objects.get_or_create(name="Warehouse", description="Storage facility")
        LocationCategory.objects.get_or_create(name="Market", description="Local marketplace")

        # 11. ManPowerWorkType
        ManPowerWorkType.objects.get_or_create(name="Harvester", description="Worker for harvesting crops")
        ManPowerWorkType.objects.get_or_create(name="Driver", description="Tractor or truck driver")

        # 12. GuidelineCategory
        GuidelineCategory.objects.get_or_create(name="Pest Control", description="Managing pests")
        GuidelineCategory.objects.get_or_create(name="Irrigation Methods", description="Water management")

        # 13. DocumentCategory
        DocumentCategory.objects.get_or_create(document_type="My Land", name="Land Deed", description="Proof of ownership")
        DocumentCategory.objects.get_or_create(document_type="My Expenses", name="Fertilizer Bill", description="Receipt for fertilizer")

        # 14. HardwareClass
        HardwareClass.objects.get_or_create(name="Tractor", description="Heavy machinery")
        HardwareClass.objects.get_or_create(name="Water Pump", description="Irrigation equipment")

        self.stdout.write(self.style.SUCCESS("Successfully seeded two records for all settings models.")) # type: ignore
