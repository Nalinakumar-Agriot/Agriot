import datetime
import calendar
import logging
from django.utils import timezone
from django.db.models import Q

logger = logging.getLogger(__name__)


def calculate_next_recurring_date(start_date, cycle):
    """
    Computes the next cycle date based on start_date and cycle interval:
    Daily, Weekly, Monthly, Yearly.
    """
    if not start_date:
        start_date = datetime.date.today()
    if isinstance(start_date, datetime.datetime):
        start_date = start_date.date()

    cycle = (cycle or '').strip().capitalize()

    if cycle == 'Daily':
        return start_date + datetime.timedelta(days=1)
    elif cycle == 'Weekly':
        return start_date + datetime.timedelta(days=7)
    elif cycle == 'Monthly':
        year = start_date.year
        month = start_date.month + 1
        if month > 12:
            month = 1
            year += 1
        max_days = calendar.monthrange(year, month)[1]
        day = min(start_date.day, max_days)
        return datetime.date(year, month, day)
    elif cycle == 'Yearly':
        try:
            return start_date.replace(year=start_date.year + 1)
        except ValueError:
            return start_date + datetime.timedelta(days=365)
    else:
        # Default fallback: 1 month
        return start_date + datetime.timedelta(days=30)


def process_recurring_expenses(farmer_profile=None, request=None):
    """
    Scans for active recurring expenses that are due and:
    1. Automatically creates the new cycle LedgerTransaction entry.
    2. Creates associated FarmerOutstanding if there is an unpaid balance.
    3. Dispatches email reminder to the farmer via SMTP.
    4. Creates in-app SystemNotification.
    5. Advances the master transaction's next_due_date.
    """
    from apps.farmer.models import LedgerTransaction, FarmerOutstanding
    from apps.superadmin.email_utils import send_recurring_expense_email
    from auth.models import SystemNotification

    today = datetime.date.today()

    qs = LedgerTransaction.objects.filter(is_recurring=True)
    if farmer_profile:
        qs = qs.filter(farmer=farmer_profile)

    processed_count = 0

    for master_tx in qs:
        # Ensure next_due_date is initialized
        if not master_tx.next_due_date:
            cycle = master_tx.repeat_cycle or 'Monthly'
            master_tx.next_due_date = calculate_next_recurring_date(master_tx.close_date, cycle)
            master_tx.save(update_fields=['next_due_date'])

        # Process due instances
        max_loop = 15  # Safety cap to avoid infinite loops if dates are far behind
        while master_tx.next_due_date and master_tx.next_due_date <= today and max_loop > 0:
            max_loop -= 1
            due_date = master_tx.next_due_date

            # Prevent duplicate creation for this exact parent & date
            tag_marker = f"RecurringParent #{master_tx.id}"
            already_exists = LedgerTransaction.objects.filter(
                farmer=master_tx.farmer,
                description=master_tx.description,
                close_date=due_date,
                tags__icontains=tag_marker
            ).exists()

            if not already_exists:
                clean_tags = (master_tx.tags or '').replace('Outflow', '').strip()
                new_tags = f"Outflow | {tag_marker}"
                if master_tx.farm_id:
                    new_tags += f" | Farm ID: {master_tx.farm_id}"
                if clean_tags:
                    new_tags += f" | {clean_tags}"

                new_tx = LedgerTransaction.objects.create(
                    farmer=master_tx.farmer,
                    farm=master_tx.farm,
                    vendor=master_tx.vendor,
                    close_date=due_date,
                    description=master_tx.description,
                    payment_method=master_tx.payment_method,
                    category=master_tx.category,
                    category_color=master_tx.category_color,
                    tags=new_tags,
                    status=master_tx.status or 'Cost',
                    amount=master_tx.amount,
                    balance_due=master_tx.balance_due,
                    quantity=master_tx.quantity,
                    unit=master_tx.unit,
                    is_recurring=False,  # child is generated instance
                    repeat_cycle=master_tx.repeat_cycle,
                    reminder_type=master_tx.reminder_type,
                )

                # Outstanding liability if balance due exists
                if master_tx.balance_due and float(master_tx.balance_due) > 0:
                    vendor_name = master_tx.vendor.vendor_name if master_tx.vendor else ''
                    inv_ref = f"Vendor: {vendor_name} - {master_tx.description}" if vendor_name else f"Expense: {master_tx.description}"
                    FarmerOutstanding.objects.create(
                        farmer=master_tx.farmer,
                        vendor=master_tx.vendor,
                        invoice_no=f"{inv_ref} (Cycle {due_date})",
                        due_date=due_date,
                        amount_total=master_tx.balance_due,
                        amount_paid=0,
                        status='Unpaid'
                    )

                # In-app notification
                if master_tx.farmer and master_tx.farmer.user:
                    try:
                        SystemNotification.objects.create(
                            user=master_tx.farmer.user,
                            title=f"Recurring Expense Created: {master_tx.description}",
                            message=f"A recurring expense of ₹{master_tx.actual_price:.2f} has been automatically logged for {due_date.strftime('%d %b %Y')}.",
                            icon_class="ri-repeat-2-line",
                            bg_color="warning"
                        )
                    except Exception as e:
                        logger.error(f"[process_recurring_expenses] SystemNotification error: {e}")

                # Send email reminder
                try:
                    send_recurring_expense_email(new_tx, request=request)
                except Exception as e:
                    logger.error(f"[process_recurring_expenses] Email send error: {e}")

                processed_count += 1

            # Advance to next cycle
            master_tx.last_recurring_date = due_date
            master_tx.next_due_date = calculate_next_recurring_date(due_date, master_tx.repeat_cycle)
            master_tx.save(update_fields=['last_recurring_date', 'next_due_date'])

    return processed_count
