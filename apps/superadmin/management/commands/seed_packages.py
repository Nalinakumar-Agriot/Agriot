from django.core.management.base import BaseCommand
from apps.superadmin.models import Package

class Command(BaseCommand):
    help = 'Seeds standard pricing packages into the database including 1-month free trial'

    def handle(self, *args, **kwargs):
        all_modules = [
            "Dashboards",
            "Farms",
            "Market & Resources Hub",
            "Task",
            "Sales",
            "Inventory",
            "Expenses",
            "Employee",
            "Reports",
            "Mail Settings",
            "Users Management",
            "Smart Farming Guide",
            "My Documents",
            "Profile & Subscription"
        ]

        packages_data = [
            {
                "id": 1,
                "name": "1-Month Free Trial",
                "price": 0.00,
                "original_price": 999.00,
                "max_acre_support": "Unlimited",
                "description": "Experience smart farming IoT features with a 1-month free trial for new farmers. All modules, unlimited acres, and all features unlocked.",
                "my_land": "Unlimited",
                "my_crop": "Unlimited",
                "my_users": "Unlimited",
                "discount": "100%",
                "show_badge": True,
                "badge_text": "FREE TRIAL",
                "is_active": True,
                "is_active_monthly": True,
                "is_active_quarterly": False,
                "is_active_yearly": False,
                "modules": all_modules,
                "features": [
                    "30 Days Full Access - All Modules Unlocked",
                    "Unlimited Acres Coverage",
                    "Unlimited Lands & Farms",
                    "Unlimited Crops & Segments",
                    "Unlimited Users & Team Access",
                    "Soil Moisture & IoT Sensors",
                    "Email & SMS Alerts",
                    "Weekly Weather Forecast Reports",
                    "Interactive Dashboards",
                    "Farms & Crop Splits",
                    "Financials & Sales Records",
                    "Inventory & Expense Management",
                    "Employee Shifts & Task Management",
                    "Smart Farming Guide",
                    "Mail Integration & Profile Management"
                ]
            },
            {
                "id": 2,
                "name": "Eco-Starter Package",
                "price": 4067.00,
                "original_price": 4999.00,
                "max_acre_support": "15",
                "description": "Perfect for smallholder and backyard organic farms aiming to digitize operations with smart wireless sensors.",
                "my_land": "3",
                "my_crop": "10",
                "my_users": "5",
                "discount": "18%",
                "show_badge": False,
                "badge_text": "",
                "is_active": True,
                "is_active_monthly": True,
                "is_active_quarterly": True,
                "is_active_yearly": True,
                "modules": [
                    "Dashboards",
                    "Farms",
                    "Market & Resources Hub",
                    "Task",
                    "Sales",
                    "Reports",
                    "Smart Farming Guide",
                    "My Documents",
                    "Profile & Subscription",
                    "Mail Settings"
                ],
                "features": [
                    "2 Soil Moisture Nodes",
                    "Email & SMS Alerts",
                    "Weekly Weather Forecast Reports",
                    "Interactive Dashboards",
                    "Farms & Crop Splits",
                    "Financials & Sales Records",
                    "Smart Farming Guide",
                    "Profile & Subscription Management"
                ]
            },
            {
                "id": 3,
                "name": "Bumper Yield Professional",
                "price": 10707.00,
                "original_price": 12999.00,
                "max_acre_support": "100",
                "description": "The standard choice for medium-scale commercial farms that require active irrigation controls and weather stations.",
                "my_land": "8",
                "my_crop": "25",
                "my_users": "10",
                "discount": "17%",
                "show_badge": True,
                "badge_text": "POPULAR",
                "is_active": True,
                "is_active_monthly": True,
                "is_active_quarterly": True,
                "is_active_yearly": True,
                "modules": [
                    "Dashboards",
                    "Farms",
                    "Market & Resources Hub",
                    "Task",
                    "Sales",
                    "Inventory",
                    "Expenses",
                    "Employee",
                    "Reports",
                    "Smart Farming Guide",
                    "My Documents",
                    "Profile & Subscription",
                    "Mail Settings"
                ],
                "features": [
                    "5 Soil Moisture Nodes",
                    "1 Weather Station Hub",
                    "2 Irrigation Valve Controllers",
                    "Advanced Crop Growth Analytics",
                    "Interactive Dashboards & Reports",
                    "Farms & Crop Splits",
                    "Financials & Sales Records",
                    "Sales Records (Legacy Support)",
                    "Resource Consumption Insights",
                    "Outstanding Liabilities Ledger",
                    "Employee Shifts & Shift Planners",
                    "Team Account Settings",
                    "Smart Farming Guide",
                    "Profile & Subscription Management"
                ]
            }
        ]

        for pkg_data in packages_data:
            pkg_id = pkg_data.pop("id")
            pkg, created = Package.objects.update_or_create(
                id=pkg_id,
                defaults=pkg_data
            )
            action = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{action} package: {pkg.name} (ID: {pkg.id})"))
