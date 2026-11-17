from django.core.mail import EmailMessage
from django.urls import reverse
from django.conf import settings

def send_email(subject, email, message):
    try:
        email_from = settings.EMAIL_HOST_USER
        recipient_list = [email]
        email = EmailMessage(subject, message, email_from, recipient_list)
        email.send()
    except Exception as e:
        print(f"Failed to send email: {e}")


def get_absolute_url(path):
    return settings.BASE_URL + path

def send_verification_email(email, token):
    subject = "Verify your email"
    verification_url = get_absolute_url(reverse('verify-email', kwargs={'token': token}))
    message = f"Hi,\n\nPlease verify your email using this link: {verification_url}"
    send_email(subject, email, message)

def send_password_reset_email(email, token):
    subject = "Reset your password"
    reset_url = get_absolute_url(reverse('reset-password', kwargs={'token': token}))
    message = f"Hi,\n\nPlease reset your password using this link: {reset_url}"
    send_email(subject, email, message)


import os

ALLOWED_DOCUMENT_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.doc', '.docx', '.xls', '.xlsx'}
ALLOWED_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB limit

def validate_uploaded_file(file_obj, allowed_extensions=None, max_size=MAX_UPLOAD_SIZE):
    """
    Validates uploaded file size and extension to prevent malicious uploads, DoS, and Stored XSS.
    """
    if not file_obj:
        return True, None
    if allowed_extensions is None:
        allowed_extensions = ALLOWED_DOCUMENT_EXTENSIONS

    if file_obj.size > max_size:
        max_mb = max_size // (1024 * 1024)
        return False, f"File size exceeds maximum allowed limit of {max_mb}MB."

    ext = os.path.splitext(file_obj.name)[1].lower()
    if ext not in allowed_extensions:
        return False, f"File extension '{ext}' is not permitted. Allowed: {', '.join(sorted(allowed_extensions))}"

    return True, None

