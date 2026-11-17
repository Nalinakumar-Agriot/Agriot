import datetime
import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.mail.backends.smtp import EmailBackend
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


def get_superadmin_smtp_config():
    """
    Retrieves the global / superadmin SMTP settings.
    Falls back to settings.py if not configured in DB.
    """
    from apps.superadmin.models import SMTPSettings
    config = SMTPSettings.objects.filter(is_default=True).first()
    if not config:
        config = SMTPSettings.objects.filter(user__isnull=True).first()
    if not config:
        config = SMTPSettings.objects.filter(user__is_superuser=True).first()

    return config


def get_farmer_smtp_config(farmer_profile_or_user):
    """
    Retrieves the farmer's custom SMTP configuration if set up and active.
    Returns None if not configured or incomplete.
    """
    if not farmer_profile_or_user:
        return None

    from django.contrib.auth.models import User
    from auth.models import Profile
    from apps.superadmin.models import SMTPSettings

    user = None
    if isinstance(farmer_profile_or_user, User):
        user = farmer_profile_or_user
    elif isinstance(farmer_profile_or_user, Profile):
        user = farmer_profile_or_user.user
    elif hasattr(farmer_profile_or_user, 'user'):
        user = getattr(farmer_profile_or_user, 'user')
    elif isinstance(farmer_profile_or_user, int):
        user = User.objects.filter(id=farmer_profile_or_user).first()

    if not user:
        return None

    config = SMTPSettings.objects.filter(user=user, is_active=True).first()
    if config and config.email_host_user and config.email_host_password:
        return config

    return None


def resolve_smtp_config(farmer_profile_or_user=None):
    """
    Resolves which SMTP configuration to use:
    1. If farmer has custom active SMTP credentials -> Use farmer's SMTP ('farmer')
    2. Else -> Use SuperAdmin global default SMTP ('superadmin')
    """
    if farmer_profile_or_user:
        farmer_config = get_farmer_smtp_config(farmer_profile_or_user)
        if farmer_config:
            return farmer_config, 'farmer'

    superadmin_config = get_superadmin_smtp_config()
    return superadmin_config, 'superadmin'


def get_smtp_backend_connection(smtp_config):
    """
    Builds a Django EmailBackend connection dynamically based on the SMTPSettings instance.
    If smtp_config is None, falls back to django settings.
    """
    if not smtp_config:
        return EmailBackend(
            host=getattr(settings, 'EMAIL_HOST', 'smtp.gmail.com'),
            port=getattr(settings, 'EMAIL_PORT', 587),
            username=getattr(settings, 'EMAIL_HOST_USER', ''),
            password=getattr(settings, 'EMAIL_HOST_PASSWORD', ''),
            use_tls=getattr(settings, 'EMAIL_USE_TLS', True),
            use_ssl=getattr(settings, 'EMAIL_USE_SSL', False),
            timeout=10,
            fail_silently=False
        )

    return EmailBackend(
        host=smtp_config.email_host or 'smtp.gmail.com',
        port=int(smtp_config.email_port or 587),
        username=smtp_config.email_host_user or '',
        password=smtp_config.email_host_password or '',
        use_tls=bool(smtp_config.email_use_tls),
        use_ssl=bool(smtp_config.email_use_ssl),
        timeout=10,
        fail_silently=False
    )


def send_smtp_email(to_email, subject, html_content, text_content=None, farmer_profile_or_user=None):
    """
    Sends an email to the specified recipient using either the farmer's own
    SMTP configuration (if provided) or falling back to the SuperAdmin global SMTP.
    
    If to_email is empty or None, it gracefully returns without sending.
    """
    if not to_email or not str(to_email).strip():
        logger.info("[send_smtp_email] No recipient email specified. Skipping.")
        return False, "Recipient email address is missing."

    to_email = str(to_email).strip()

    config, sender_source = resolve_smtp_config(farmer_profile_or_user)

    from_email_addr = ''
    from_name = 'AgriOT Platform'

    if config:
        from_email_addr = (config.from_email or config.email_host_user or '').strip()
        from_name = (config.from_name or 'AgriOT Platform').strip()

    if not from_email_addr:
        from_email_addr = getattr(settings, 'EMAIL_HOST_USER', 'noreply@agriot.com') or 'noreply@agriot.com'

    formatted_from = f"{from_name} <{from_email_addr}>" if from_name else from_email_addr

    if not text_content:
        text_content = strip_tags(html_content)

    try:
        connection = get_smtp_backend_connection(config)
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=formatted_from,
            to=[to_email],
            connection=connection
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=False)
        logger.info(f"[send_smtp_email] Successfully sent '{subject}' to {to_email} via {sender_source} SMTP.")
        return True, f"Email sent successfully via {sender_source} SMTP."
    except Exception as e:
        error_msg = str(e)
        logger.error(f"[send_smtp_email] Failed to send email to {to_email} via {sender_source} SMTP: {error_msg}")
        return False, error_msg


def test_smtp_connection(smtp_config, test_recipient):
    """
    Sends a test verification email using the supplied SMTPSettings.
    Returns (success: bool, message: str)
    """
    if not test_recipient:
        return False, "Please enter a test recipient email address."

    host = smtp_config.email_host or 'smtp.gmail.com'
    port = smtp_config.email_port or 587
    sender = smtp_config.from_email or smtp_config.email_host_user or 'noreply@agriot.com'
    sender_name = smtp_config.from_name or 'AgriOT Mail Test'

    context = {
        'host': host,
        'port': port,
        'sender': sender,
        'sender_name': sender_name,
        'timestamp': timezone.now().strftime("%d %b %Y, %I:%M %p"),
    }

    try:
        html_content = render_to_string('emails/test_email.html', context)
    except Exception:
        html_content = f"<h3>AgriOT SMTP Test</h3><p>Your SMTP settings on {host}:{port} are working properly!</p>"

    try:
        connection = get_smtp_backend_connection(smtp_config)
        formatted_from = f"{sender_name} <{sender}>"
        msg = EmailMultiAlternatives(
            subject="AgriOT Mail Integration Test - Success",
            body="Your SMTP settings are working properly!",
            from_email=formatted_from,
            to=[test_recipient.strip()],
            connection=connection
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=False)
        return True, f"Test email successfully sent to {test_recipient}!"
    except Exception as e:
        err = str(e)
        if "Username and Password not accepted" in err or "535" in err:
            err = "Authentication failed: Please check your Email & App Password. For Gmail, ensure 2-Step Verification is enabled and use a 16-character App Password."
        return False, f"SMTP Connection Failed: {err}"


# -------------------------------------------------------------
# SPECIFIC TEMPLATE TRIGGERS (Welcome, Renewal, Package Upgrade)
# -------------------------------------------------------------

def send_welcome_email(farmer_profile, request=None):
    """
    Trigger 1: Sends the Welcome Email when a farmer registers or account is created.
    Only sends if the farmer profile has an email address.
    """
    if not farmer_profile:
        return False, "No profile provided"

    email = (farmer_profile.email or (farmer_profile.user.email if farmer_profile.user else None) or '').strip()
    if not email:
        logger.info(f"[send_welcome_email] Skipping: Farmer {farmer_profile.id} has no email address.")
        return False, "No email address found for farmer."

    farmer_name = (
        farmer_profile.user.get_full_name()
        or farmer_profile.user.first_name
        or farmer_profile.company_name
        or "Farmer"
    ) if farmer_profile.user else "Farmer"

    mobile = farmer_profile.mobile_number or (farmer_profile.user.username if farmer_profile.user else "")

    login_url = "https://agriot.in/farmer/"
    if request:
        try:
            login_url = request.build_absolute_uri('/farmer/')
        except Exception:
            pass

    context = {
        'farmer_name': farmer_name,
        'mobile_number': mobile,
        'email': email,
        'login_url': login_url,
        'current_year': timezone.now().year,
    }

    try:
        html_content = render_to_string('emails/welcome_email.html', context)
    except Exception as e:
        logger.error(f"[send_welcome_email] Template render error: {e}")
        return False, str(e)

    subject = "Welcome to AgriOT - Smart Farm Management Made Easy! 🌾"
    return send_smtp_email(
        to_email=email,
        subject=subject,
        html_content=html_content,
        farmer_profile_or_user=farmer_profile
    )


def send_renewal_email(subscription, amount=None, request=None):
    """
    Trigger 2: Sends the Renewal Email when a subscription is renewed.
    Only sends if the farmer profile has an email address.
    """
    if not subscription or not subscription.farmer:
        return False, "Invalid subscription object"

    farmer_profile = subscription.farmer
    email = (farmer_profile.email or (farmer_profile.user.email if farmer_profile.user else None) or '').strip()
    if not email:
        logger.info(f"[send_renewal_email] Skipping: Farmer {farmer_profile.id} has no email address.")
        return False, "No email address found for farmer."

    farmer_name = (
        farmer_profile.user.get_full_name()
        or farmer_profile.user.first_name
        or farmer_profile.company_name
        or "Farmer"
    ) if farmer_profile.user else "Farmer"

    pkg_name = subscription.package.name if subscription.package else "AgriOT Plan"
    billing_cycle = (subscription.billing_cycle or "Monthly").capitalize()
    paid_amt = amount if amount is not None else subscription.monthly_amount

    start_date_str = subscription.start_date.strftime("%d %b %Y") if subscription.start_date else timezone.now().strftime("%d %b %Y")
    end_date_str = subscription.end_date.strftime("%d %b %Y") if subscription.end_date else "—"

    dashboard_url = "https://agriot.in/farmer/profile_view/#subscription"
    if request:
        try:
            dashboard_url = request.build_absolute_uri('/farmer/profile_view/#subscription')
        except Exception:
            pass

    context = {
        'farmer_name': farmer_name,
        'package_name': pkg_name,
        'billing_cycle': billing_cycle,
        'amount': f"{float(paid_amt):,.2f}",
        'start_date': start_date_str,
        'end_date': end_date_str,
        'email': email,
        'dashboard_url': dashboard_url,
        'current_year': timezone.now().year,
    }

    try:
        html_content = render_to_string('emails/renewal_email.html', context)
    except Exception as e:
        logger.error(f"[send_renewal_email] Template render error: {e}")
        return False, str(e)

    subject = f"Subscription Renewed Successfully: {pkg_name} | AgriOT"
    return send_smtp_email(
        to_email=email,
        subject=subject,
        html_content=html_content,
        farmer_profile_or_user=farmer_profile
    )


def send_package_purchase_email(subscription, is_upgrade=False, amount=None, request=None):
    """
    Trigger 3: Sends the Package Buy / Upgrade Email when a plan is bought or upgraded.
    Only sends if the farmer profile has an email address.
    """
    if not subscription or not subscription.farmer:
        return False, "Invalid subscription object"

    farmer_profile = subscription.farmer
    email = (farmer_profile.email or (farmer_profile.user.email if farmer_profile.user else None) or '').strip()
    if not email:
        logger.info(f"[send_package_purchase_email] Skipping: Farmer {farmer_profile.id} has no email address.")
        return False, "No email address found for farmer."

    farmer_name = (
        farmer_profile.user.get_full_name()
        or farmer_profile.user.first_name
        or farmer_profile.company_name
        or "Farmer"
    ) if farmer_profile.user else "Farmer"

    package = subscription.package
    pkg_name = package.name if package else "AgriOT Plan"
    max_acres = getattr(package, 'max_acre_support', None)
    billing_cycle = (subscription.billing_cycle or "Monthly").capitalize()
    paid_amt = amount if amount is not None else subscription.monthly_amount

    start_date_str = subscription.start_date.strftime("%d %b %Y") if subscription.start_date else timezone.now().strftime("%d %b %Y")
    end_date_str = subscription.end_date.strftime("%d %b %Y") if subscription.end_date else "—"

    dashboard_url = "https://agriot.in/farmer/dashboard/"
    if request:
        try:
            dashboard_url = request.build_absolute_uri('/farmer/dashboard/')
        except Exception:
            pass

    context = {
        'farmer_name': farmer_name,
        'package_name': pkg_name,
        'max_acre_support': max_acres,
        'billing_cycle': billing_cycle,
        'amount': f"{float(paid_amt):,.2f}",
        'start_date': start_date_str,
        'end_date': end_date_str,
        'is_upgrade': is_upgrade,
        'email': email,
        'dashboard_url': dashboard_url,
        'current_year': timezone.now().year,
    }

    try:
        html_content = render_to_string('emails/package_upgrade_email.html', context)
    except Exception as e:
        logger.error(f"[send_package_purchase_email] Template render error: {e}")
        return False, str(e)

    action_text = "Upgraded" if is_upgrade else "Activated"
    subject = f"Plan {action_text} Successfully: {pkg_name} | AgriOT 🚀"
    return send_smtp_email(
        to_email=email,
        subject=subject,
        html_content=html_content,
        farmer_profile_or_user=farmer_profile
    )


def send_recurring_expense_email(transaction, request=None):
    """
    Sends an email reminder to the farmer for a recurring expense transaction.
    """
    if not transaction or not transaction.farmer:
        return False, "No farmer or transaction provided"

    farmer_profile = transaction.farmer
    email = (farmer_profile.email or (farmer_profile.user.email if farmer_profile.user else None) or '').strip()
    if not email:
        logger.info(f"[send_recurring_expense_email] Skipping: Farmer {farmer_profile.id} has no email address.")
        return False, "No email address found for farmer."

    farmer_name = (
        farmer_profile.user.get_full_name()
        or farmer_profile.user.first_name
        or farmer_profile.company_name
        or "Farmer"
    ) if farmer_profile.user else "Farmer"

    expenses_url = "https://agriot.in/farmer/expenses/"
    if request:
        try:
            expenses_url = request.build_absolute_uri('/farmer/expenses/')
        except Exception:
            pass

    context = {
        'farmer_name': farmer_name,
        'description': transaction.description,
        'category': transaction.category,
        'farm_name': transaction.farm.land_name if transaction.farm else None,
        'vendor_name': transaction.vendor.vendor_name if transaction.vendor else None,
        'amount': transaction.actual_price,
        'balance_due': transaction.balance_due,
        'repeat_cycle': transaction.repeat_cycle,
        'reminder_type': transaction.reminder_type,
        'close_date': transaction.close_date.strftime('%d %b %Y') if transaction.close_date else '',
        'expenses_url': expenses_url,
        'current_year': timezone.now().year,
    }

    try:
        html_content = render_to_string('emails/recurring_expense_email.html', context)
    except Exception as e:
        logger.error(f"[send_recurring_expense_email] Template render error: {e}")
        return False, str(e)

    subject = f"🔔 Recurring Expense Reminder: {transaction.description} (₹{transaction.actual_price:.2f}) - AgriOT"
    return send_smtp_email(
        to_email=email,
        subject=subject,
        html_content=html_content,
        farmer_profile_or_user=farmer_profile
    )
