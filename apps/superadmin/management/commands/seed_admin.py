from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from auth.models import Profile

class Command(BaseCommand):
    help = 'Seeds one admin user with mobile number and password'

    def handle(self, *args, **kwargs):
        mobile_number = "1234567890"
        password = "password123"
        username = "admin"

        if User.objects.filter(username=username).exists():
            self.stdout.write(getattr(self.style, 'WARNING')(f'Admin user "{username}" already exists.'))
            user = User.objects.get(username=username)
        else:
            user = User.objects.create_superuser(
                username=username,
                email="admin@agriot.com",
                password=password
            )
            self.stdout.write(getattr(self.style, 'SUCCESS')(f'Successfully created admin user: {username}'))

        # Update or create profile with mobile number and superadmin role
        profile, created = Profile.objects.get_or_create(user=user)
        profile.mobile_number = mobile_number
        profile.role = 'superadmin'
        profile.save()

        if created:
            self.stdout.write(getattr(self.style, 'SUCCESS')(f'Created profile for admin user with mobile number {mobile_number}.'))
        else:
            self.stdout.write(getattr(self.style, 'SUCCESS')(f'Updated profile for admin user with mobile number {mobile_number}.'))
