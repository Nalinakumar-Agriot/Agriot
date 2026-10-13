from django.core.management.base import BaseCommand
from apps.superadmin.models import Package

class Command(BaseCommand):
    help = 'Seeds standard pricing packages into the database'

    def handle(self, *args, **kwargs):
        packages_data = [
            {
                "id": 1,
                "name": "Eco-Starter Package",
                "price": 4067.00,
                "max_acre_support": "15",
                "description": "Perfect for smallholder and backyard organic farms aiming to digitize operations with smart wireless sensors.",
                "my_land": "3",
                "my_crop": "10",
                "my_users": "5",
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
                "id": 2,
                "name": "Bumper Yield Professional",
                "price": 10707.00,
                "max_acre_support": "100",
                "description": "The standard choice for medium-scale commercial farms that require active irrigation controls and weather stations.",
                "my_land": "8",
                "my_crop": "25",
                "my_users": "10",
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
            },
            {
                "id": 3,
                "name": "Industrial Mega-Farm IoT",
                "price": 28967.00,
                "max_acre_support": "Unlimited",
                "description": "Enterprise agricultural solution with full field coverage, GPS fleet tracking, and deep soil chemical index integrations.",
                "my_land": "20",
                "my_crop": "50",
                "my_users": "20",
                "features": [
                    "20+ Custom Sensor Nodes",
                    "Weather Station & Solar Radiance Probe",
                    "GPS Telemetry for 5 Fleet Vehicles",
                    "API Integrations for Custom ERPs",
                    "24/7 Agronomist Advisory Hotline",
                    "Interactive Dashboards",
                    "Farms & Crop Splits",
                    "Financials & Sales Records",
                    "Resource Consumption Insights",
                    "Employee Shifts & Team Accounts",
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
