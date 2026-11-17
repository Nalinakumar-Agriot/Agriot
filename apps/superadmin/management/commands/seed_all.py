from django.core.management import call_command
from django.core.management.base import BaseCommand
from apps.superadmin.models import GeneralSetting, SMTPSettings


class Command(BaseCommand):
    help = 'Runs all seed commands and sets up initial database data'

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.NOTICE('----------------------------------------'))
        self.stdout.write(self.style.NOTICE('Starting Full Database Seeding Process...'))
        self.stdout.write(self.style.NOTICE('----------------------------------------'))

        # 1. Admin
        self.stdout.write(self.style.MIGRATE_HEADING('\n[1/6] Seeding SuperAdmin User...'))
        try:
            call_command('seed_admin')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error seeding admin: {e}'))

        # 2. Packages
        self.stdout.write(self.style.MIGRATE_HEADING('\n[2/6] Seeding Subscription Packages...'))
        try:
            call_command('seed_packages')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error seeding packages: {e}'))

        # 3. Settings & Master Categories
        self.stdout.write(self.style.MIGRATE_HEADING('\n[3/6] Seeding Settings & Categories...'))
        try:
            call_command('seed_settings')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error seeding settings: {e}'))

        # 4. Locations
        self.stdout.write(self.style.MIGRATE_HEADING('\n[4/6] Seeding Locations (Countries, States, Cities)...'))
        try:
            call_command('seed_locations')
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'Note on locations: {e}'))

        # 5. Taluks
        self.stdout.write(self.style.MIGRATE_HEADING('\n[5/6] Seeding Tamil Nadu Taluks...'))
        try:
            call_command('seed_tamil_nadu_taluks')
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'Note on taluks: {e}'))

        # 6. GeneralSetting & SMTP
        self.stdout.write(self.style.MIGRATE_HEADING('\n[6/6] Seeding General & SMTP Settings...'))
        try:
            setting, _ = GeneralSetting.objects.get_or_create(id=1)
            setting.title = "Agriot Automation"
            setting.email = "tumail987654321@gmail.com"
            setting.razorpay_key_id = "rzp_test_Trc3reEy8k4YoJ"
            setting.razorpay_key_secret = "0ofru9WKnCbUgDoiCkH5mAlY"
            setting.razorpay_enabled = True
            setting.razorpay_auto_renew = True
            setting.save()

            smtp, _ = SMTPSettings.objects.get_or_create(is_default=True)
            smtp.email_host = "smtp.gmail.com"
            smtp.email_port = 587
            smtp.email_host_user = "tumail987654321@gmail.com"
            smtp.email_host_password = "fqkujpofzowdlpkx"
            smtp.email_use_tls = True
            smtp.email_use_ssl = False
            smtp.from_email = "tumail987654321@gmail.com"
            smtp.from_name = "AgriOT Platform"
            smtp.is_active = True
            smtp.save()
            self.stdout.write(self.style.SUCCESS('Successfully configured General & Global SMTP settings.'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error configuring settings: {e}'))

        self.stdout.write(self.style.NOTICE('\n----------------------------------------'))
        self.stdout.write(self.style.SUCCESS('ALL SEEDS & DATA COMPLETED SUCCESSFULLY!'))
        self.stdout.write(self.style.NOTICE('----------------------------------------'))
