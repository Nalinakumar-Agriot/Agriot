from django.core.management.base import BaseCommand
from apps.farmer.recurring_utils import process_recurring_expenses


class Command(BaseCommand):
    help = "Processes recurring farm expenses due for the current cycle and triggers auto-payments/reminders."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starting recurring expense processing..."))
        count = process_recurring_expenses()
        self.stdout.write(self.style.SUCCESS(f"Successfully processed {count} recurring expense instances."))
