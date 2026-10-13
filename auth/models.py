from typing import Any, cast
from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

class Profile(models.Model):
    objects = models.Manager()
    DoesNotExist: Any = models.ObjectDoesNotExist
    ROLE_CHOICES = (
        ('superadmin', 'Superadmin'),
        ('farmer', 'Farmer'),
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='farmer')
    email = models.EmailField(max_length=100, unique=True, blank=True, null=True)
    mobile_number = models.CharField(max_length=15, unique=True, blank=True, null=True)
    profile_image = models.ImageField(upload_to='profiles/', blank=True, null=True)
    
    # Farmer specific fields
    farmer_type = models.CharField(max_length=20, default='individual', blank=True, null=True)
    company_name = models.CharField(max_length=255, blank=True, null=True)
    company_mobile_number = models.CharField(max_length=20, blank=True, null=True)
    msme_number = models.CharField(max_length=100, blank=True, null=True)
    msme_certificate = models.FileField(upload_to='msme_certificates/', blank=True, null=True)
    aadhaar_number = models.CharField(max_length=20, blank=True, null=True)
    pmkisan_id = models.CharField(max_length=50, blank=True, null=True)
    pan_number = models.CharField(max_length=20, blank=True, null=True)
    father_name = models.CharField(max_length=100, blank=True, null=True)
    guardian_name = models.CharField(max_length=100, blank=True, null=True)
    gender = models.CharField(max_length=20, blank=True, null=True)
    dob = models.DateField(blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    educational_qualification = models.CharField(max_length=150, blank=True, null=True)
    taluka = models.CharField(max_length=100, blank=True, null=True)
    village = models.CharField(max_length=100, blank=True, null=True)
    address = models.TextField(blank=True, null=True)

    state = models.ForeignKey('superadmin.State', on_delete=models.SET_NULL, null=True, blank=True)
    district = models.ForeignKey('superadmin.City', on_delete=models.SET_NULL, null=True, blank=True)
    pincode = models.CharField(max_length=10, blank=True, null=True)
    status = models.CharField(max_length=20, default='Active')
    email_token = models.CharField(max_length=100, blank=True, null=True)
    forget_password_token = models.CharField(max_length=100, blank=True, null=True)
    otp = models.CharField(max_length=6, blank=True, null=True)
    otp_expiry_at = models.DateTimeField(blank=True, null=True)
    is_verified = models.BooleanField(default=False)  # pyright: ignore[reportArgumentType]
    permissions = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        u: Any = self.user
        return str(u.username) if hasattr(u, 'username') else str(u)

    @property
    def permissions_json(self):
        import json
        perms = cast(Any, self.permissions)
        if perms is None:
            return "[]"
        if isinstance(perms, list):
            return json.dumps(perms)
        try:
            return json.dumps(list(perms))
        except Exception:
            return "[]"

    @receiver(post_save, sender=User)
    def create_profile(sender, instance, created, **kwargs):
        if created:
            email_val = instance.email if instance.email else None
            role_val = 'superadmin' if instance.is_superuser else 'farmer'
            Profile.objects.create(user=instance, email=email_val, role=role_val)


    class Meta:
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"

class SystemNotification(models.Model):
    objects = models.Manager()
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications', blank=True, null=True)
    role_target = models.CharField(max_length=20, choices=Profile.ROLE_CHOICES, blank=True, null=True)
    title = models.CharField(max_length=255)
    message = models.TextField()
    is_read = models.BooleanField(default=False)  # pyright: ignore[reportArgumentType]
    icon_class = models.CharField(max_length=50, default='ri-notification-3-line')
    bg_color = models.CharField(max_length=20, default='primary')
    link = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        u: Any = self.user
        user_name = str(u.username) if u and hasattr(u, 'username') else str(self.role_target)
        return f"{self.title} - {user_name}"

