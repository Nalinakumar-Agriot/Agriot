import datetime
from django.utils import timezone
from apps.superadmin.models import Subscription


SETTLED_PAYMENT_STATUSES = {'paid', 'success', 'settled', 'captured', 'completed'}
PENDING_PAYMENT_STATUSES = {'pending', 'created', 'initiated', 'processing'}
FAILED_PAYMENT_STATUSES = {'failed', 'cancelled', 'canceled', 'error'}
CYCLE_RANK = {'monthly': 1, 'quarterly': 2, 'yearly': 3}


def normalize_billing_cycle(cycle):
    raw = str(cycle or 'monthly').strip().lower()
    if raw in ('quarterly', 'quarter'):
        return 'quarterly'
    if raw in ('yearly', 'annual', 'year'):
        return 'yearly'
    return 'monthly'


def billing_cycle_days(cycle):
    c = normalize_billing_cycle(cycle)
    if c == 'quarterly':
        return 90
    if c == 'yearly':
        return 365
    return 30


def billing_cycle_label(cycle):
    c = normalize_billing_cycle(cycle)
    if c == 'quarterly':
        return 'Quarterly'
    if c == 'yearly':
        return 'Annual'
    return 'Monthly'


def billing_cycle_unit_label(cycle):
    c = normalize_billing_cycle(cycle)
    if c == 'quarterly':
        return 'Quarter'
    if c == 'yearly':
        return 'Year'
    return 'Month'


def billing_cycle_duration_label(cycle):
    c = normalize_billing_cycle(cycle)
    if c == 'quarterly':
        return '3 Months (Quarterly)'
    if c == 'yearly':
        return '12 Months (Annual)'
    return '1 Month'


def is_payment_settled(payment_status):
    return str(payment_status or '').strip().lower() in SETTLED_PAYMENT_STATUSES


def is_payment_pending(payment_status):
    return str(payment_status or '').strip().lower() in PENDING_PAYMENT_STATUSES


def is_payment_failed(payment_status):
    return str(payment_status or '').strip().lower() in FAILED_PAYMENT_STATUSES


def get_package_cycle_price(package, billing_cycle='monthly'):
    if not package:
        return 0.0
    cycle = normalize_billing_cycle(billing_cycle)
    base_price = float(package.price or 0.0)
    if cycle == 'quarterly':
        if package.price_quarterly is not None and float(package.price_quarterly) > 0:
            return round(float(package.price_quarterly), 2)
        return round(base_price * 3, 2)
    if cycle == 'yearly':
        if package.price_yearly is not None and float(package.price_yearly) > 0:
            return round(float(package.price_yearly), 2)
        return round(base_price * 12, 2)
    return round(base_price, 2)


def is_package_cycle_active(package, billing_cycle='monthly'):
    if not package or not getattr(package, 'is_active', True):
        return False
    cycle = normalize_billing_cycle(billing_cycle)
    if cycle == 'quarterly':
        return bool(getattr(package, 'is_active_quarterly', True))
    if cycle == 'yearly':
        return bool(getattr(package, 'is_active_yearly', True))
    return bool(getattr(package, 'is_active_monthly', True))


def get_effective_end_date(sub):
    if not sub:
        return None
    if sub.end_date:
        return sub.end_date
    base = sub.start_date or (sub.created_at.date() if sub.created_at else timezone.now().date())
    return base + datetime.timedelta(days=billing_cycle_days(sub.billing_cycle))


def get_active_paid_subscription(farmer_profile, today=None):
    """
    Returns the farmer's currently active, payment-settled, non-expired Subscription,
    triggering auto-renewal if the current period just ended and auto_renew is enabled.
    """
    if not farmer_profile:
        return None
    if today is None:
        today = timezone.now().date()

    candidates = (
        Subscription.objects.filter(farmer=farmer_profile, subscription_status='active')
        .select_related('package', 'scheduled_package')
        .order_by('-end_date', '-id')
    )
    for sub in candidates:
        if not is_payment_settled(sub.payment_status):
            continue
        end_date = get_effective_end_date(sub)
        if end_date < today and sub.auto_renew:
            try:
                from apps.superadmin.razorpay_utils import auto_renew_subscription
                renewed, _ = auto_renew_subscription(sub, today=today)
                if renewed:
                    sub.refresh_from_db()
                    end_date = get_effective_end_date(sub)
            except Exception as exc:
                print(f"[AutoRenew in utils error]: {exc}")
        if end_date >= today:
            return sub
    return None


def get_farmer_subscription_details(farmer_profile):
    """
    Evaluates the subscription status for a farmer profile from the database.
    """
    empty_payload = {
        'subscription': None,
        'package_name': 'No Plan',
        'billing_cycle': '',
        'billing_cycle_label': 'None',
        'payment_status': 'none',
        'has_subscription': False,
        'has_no_plan': True,
        'is_pending': False,
        'is_expired': False,
        'is_expiring_soon': False,
        'days_left': 0,
        'start_date': None,
        'end_date': None,
        'status_label': 'No Plan',
        'badge_class': 'secondary',
        'can_mutate': False,
        'scheduled_package_name': None,
        'scheduled_billing_cycle': None,
        'scheduled_billing_cycle_label': None,
    }
    if not farmer_profile:
        return empty_payload

    today = timezone.now().date()
    all_subs = list(
        Subscription.objects.filter(farmer=farmer_profile)
        .select_related('package', 'scheduled_package')
        .order_by('-end_date', '-id')
    )
    if not all_subs:
        return empty_payload

    # 1. Prioritize an active, payment-settled subscription
    active_settled = [
        s for s in all_subs
        if s.subscription_status == 'active' and is_payment_settled(s.payment_status)
    ]
    sub = active_settled[0] if active_settled else None

    if not sub:
        # 2. Fall back to the latest settled/expired subscription (ignore failed/cancelled if settled exists)
        settled_past = [s for s in all_subs if is_payment_settled(s.payment_status)]
        if settled_past:
            sub = settled_past[0]
        else:
            # 3. Only unpaid pending/failed records exist
            latest = all_subs[0]
            cycle = normalize_billing_cycle(latest.billing_cycle)
            pending_flag = is_payment_pending(latest.payment_status)
            return {
                'subscription': latest,
                'package_name': latest.package.name if latest.package else 'Standard Plan',
                'billing_cycle': cycle,
                'billing_cycle_label': billing_cycle_label(cycle),
                'payment_status': latest.payment_status,
                'has_subscription': True,
                'has_no_plan': True,
                'is_pending': pending_flag,
                'is_expired': False,
                'is_expiring_soon': False,
                'days_left': 0,
                'start_date': latest.start_date,
                'end_date': get_effective_end_date(latest),
                'status_label': 'Payment Pending' if pending_flag else 'Payment Incomplete',
                'badge_class': 'warning' if pending_flag else 'danger',
                'can_mutate': False,
                'scheduled_package_name': None,
                'scheduled_billing_cycle': None,
                'scheduled_billing_cycle_label': None,
            }

    end_date = get_effective_end_date(sub)
    delta_days = (end_date - today).days

    if delta_days <= 0 and sub.subscription_status == 'active' and sub.auto_renew:
        try:
            from apps.superadmin.razorpay_utils import auto_renew_subscription
            renewed, _ = auto_renew_subscription(sub, today=today)
            if renewed:
                sub.refresh_from_db()
                end_date = get_effective_end_date(sub)
                delta_days = (end_date - today).days
        except Exception as e:
            print(f"[AutoRenew in utils error]: {e}")

    cycle = normalize_billing_cycle(sub.billing_cycle)
    cycle_lbl = billing_cycle_label(cycle)
    sched_pkg_name = sub.scheduled_package.name if sub.scheduled_package else None
    sched_cycle = normalize_billing_cycle(sub.scheduled_billing_cycle) if sub.scheduled_billing_cycle else None
    sched_cycle_lbl = billing_cycle_label(sched_cycle) if sched_cycle else None

    if delta_days < 0 or sub.subscription_status in ('cancelled', 'expired'):
        if sub.subscription_status == 'active' and delta_days < 0:
            try:
                sub.subscription_status = 'expired'
                sub.save(update_fields=['subscription_status'])
            except Exception:
                pass

        return {
            'subscription': sub,
            'package_name': sub.package.name if sub.package else 'Standard Plan',
            'billing_cycle': cycle,
            'billing_cycle_label': cycle_lbl,
            'payment_status': sub.payment_status,
            'has_subscription': True,
            'has_no_plan': False,
            'is_pending': False,
            'is_expired': True,
            'is_expiring_soon': False,
            'days_left': 0,
            'overdue_days': abs(delta_days),
            'start_date': sub.start_date,
            'end_date': end_date,
            'status_label': 'Plan Expired',
            'badge_class': 'danger',
            'can_mutate': False,
            'scheduled_package_name': sched_pkg_name,
            'scheduled_billing_cycle': sched_cycle,
            'scheduled_billing_cycle_label': sched_cycle_lbl,
        }

    if delta_days <= 2:
        label = (
            'Expires Today' if delta_days == 0
            else (f'Renewal Due in {delta_days} day' if delta_days == 1 else f'Renewal Due in {delta_days} days')
        )
        return {
            'subscription': sub,
            'package_name': sub.package.name if sub.package else 'Standard Plan',
            'billing_cycle': cycle,
            'billing_cycle_label': cycle_lbl,
            'payment_status': sub.payment_status,
            'has_subscription': True,
            'has_no_plan': False,
            'is_pending': False,
            'is_expired': False,
            'is_expiring_soon': True,
            'days_left': delta_days,
            'start_date': sub.start_date,
            'end_date': end_date,
            'status_label': label,
            'badge_class': 'warning',
            'can_mutate': True,
            'scheduled_package_name': sched_pkg_name,
            'scheduled_billing_cycle': sched_cycle,
            'scheduled_billing_cycle_label': sched_cycle_lbl,
        }

    return {
        'subscription': sub,
        'package_name': sub.package.name if sub.package else 'Standard Plan',
        'billing_cycle': cycle,
        'billing_cycle_label': cycle_lbl,
        'payment_status': sub.payment_status,
        'has_subscription': True,
        'has_no_plan': False,
        'is_pending': False,
        'is_expired': False,
        'is_expiring_soon': False,
        'days_left': delta_days,
        'start_date': sub.start_date,
        'end_date': end_date,
        'status_label': f'Active ({delta_days}d left)',
        'badge_class': 'success',
        'can_mutate': True,
        'scheduled_package_name': sched_pkg_name,
        'scheduled_billing_cycle': sched_cycle,
        'scheduled_billing_cycle_label': sched_cycle_lbl,
    }


def has_farmer_used_free_trial(farmer_profile=None, mobile_number=None):
    """
    Checks if a farmer (by Profile or mobile number) has ever claimed / activated
    a 1-Month Free Trial package. Free Trial is strictly 1-time per mobile number.
    Unpaid failed/cancelled checkout attempts do not lock out the trial.
    """
    from apps.superadmin.models import Subscription, Profile
    from django.db.models import Q

    profile_ids = set()
    mobiles = set()

    if farmer_profile:
        if hasattr(farmer_profile, 'id'):
            profile_ids.add(farmer_profile.id)
        if getattr(farmer_profile, 'mobile_number', None):
            mobiles.add(str(farmer_profile.mobile_number).strip())
        if getattr(farmer_profile, 'user', None) and getattr(farmer_profile.user, 'username', None):
            mobiles.add(str(farmer_profile.user.username).strip())

    if mobile_number:
        clean_mob = str(mobile_number).strip()
        if clean_mob:
            mobiles.add(clean_mob)
            matching_profiles = Profile.objects.filter(
                Q(mobile_number=clean_mob) | Q(user__username=clean_mob)
            ).values_list('id', flat=True)
            profile_ids.update(matching_profiles)

    if not profile_ids and not mobiles:
        return False

    trial_subs = Subscription.objects.filter(
        Q(farmer_id__in=profile_ids) |
        Q(farmer__mobile_number__in=mobiles) |
        Q(farmer__user__username__in=mobiles),
        Q(package__price=0) | Q(package__name__icontains='free trial') | Q(package__name__icontains='trial')
    )
    return any(
        is_payment_settled(s.payment_status) or s.subscription_status == 'active'
        for s in trial_subs
    )


def calculate_plan_switch_quote(farmer_profile, target_package=None, target_cycle='monthly', is_renewal=False):
    """
    Calculates switch/upgrade/downgrade eligibility, pro-rata credits, effective dates,
    and net payable amount across Monthly, Quarterly, and Annual billing cycles.
    """
    today = timezone.now().date()
    from apps.superadmin.models import Package

    if isinstance(target_package, (int, str)):
        target_package = Package.objects.filter(id=target_package).first()

    norm_target_cycle = normalize_billing_cycle(target_cycle)
    target_days = billing_cycle_days(norm_target_cycle)
    target_price = get_package_cycle_price(target_package, norm_target_cycle)
    target_cycle_active = is_package_cycle_active(target_package, norm_target_cycle) if target_package else True
    target_pkg_monthly = float(target_package.price or 0.0) if target_package else 0.0
    is_target_free_trial = bool(
        target_package and (target_pkg_monthly == 0.0 or 'trial' in (target_package.name or '').lower())
    )
    trial_claimed = has_farmer_used_free_trial(farmer_profile) if farmer_profile else False

    base_target_fields = {
        'target_package_id': target_package.id if target_package else None,
        'target_package_name': target_package.name if target_package else '',
        'target_billing_cycle': norm_target_cycle,
        'target_billing_cycle_label': billing_cycle_label(norm_target_cycle),
        'target_days': target_days,
        'target_price': target_price,
        'target_cycle_active': target_cycle_active,
        'is_target_free_trial': is_target_free_trial,
        'free_trial_claimed': trial_claimed,
    }

    active_sub = get_active_paid_subscription(farmer_profile, today=today) if farmer_profile else None

    if not active_sub or not active_sub.package:
        new_end = today + datetime.timedelta(days=target_days)
        block_reason = ''
        can_switch = True
        if target_package and not target_cycle_active:
            can_switch = False
            block_reason = f'{billing_cycle_label(norm_target_cycle)} billing is not available for {target_package.name}.'
        elif is_target_free_trial and trial_claimed:
            can_switch = False
            block_reason = 'The 1-Month Free Trial has already been claimed for your mobile number.'

        return {
            **base_target_fields,
            'has_active_plan': False,
            'current_subscription_id': None,
            'current_package_id': None,
            'current_package_name': '',
            'current_billing_cycle': '',
            'current_billing_cycle_label': '',
            'current_price': 0.0,
            'start_date': today.strftime('%Y-%m-%d'),
            'end_date': '',
            'end_date_formatted': '',
            'total_days': target_days,
            'remaining_days': 0,
            'daily_rate': 0.0,
            'deduction_credit': 0.0,
            'excess_credit': 0.0,
            'extra_days': 0,
            'effective_duration_days': target_days,
            'net_payable': target_price,
            'net_payable_paise': int(round(target_price * 100)),
            'effective_date': today.strftime('%Y-%m-%d'),
            'effective_date_formatted': today.strftime('%d-%b-%Y'),
            'new_end_date': new_end.strftime('%Y-%m-%d'),
            'new_end_date_formatted': new_end.strftime('%d-%b-%Y'),
            'action_type': 'new',
            'is_current': False,
            'is_downgrade': False,
            'is_upgrade': False,
            'is_scheduled_switch': False,
            'is_already_scheduled': False,
            'requires_payment': target_price > 0,
            'can_switch': can_switch,
            'block_reason': block_reason,
            'scheduled_package_id': None,
            'scheduled_package_name': None,
            'scheduled_billing_cycle': None,
            'scheduled_billing_cycle_label': None,
        }

    current_cycle = normalize_billing_cycle(active_sub.billing_cycle)
    current_pkg = active_sub.package
    current_pkg_monthly = float(current_pkg.price or 0.0)
    start_date = active_sub.start_date or (active_sub.created_at.date() if active_sub.created_at else today)
    end_date = get_effective_end_date(active_sub)

    total_days = max(1, (end_date - start_date).days)
    remaining_days = max(0, (end_date - today).days)

    current_price = float(
        active_sub.monthly_amount
        if active_sub.monthly_amount is not None
        else get_package_cycle_price(current_pkg, current_cycle)
    )
    daily_rate = round(current_price / total_days, 2)
    deduction_credit = min(current_price, round(daily_rate * remaining_days, 2))

    sched_pkg_id = active_sub.scheduled_package_id
    sched_pkg_name = active_sub.scheduled_package.name if active_sub.scheduled_package else None
    sched_cycle = normalize_billing_cycle(active_sub.scheduled_billing_cycle) if active_sub.scheduled_billing_cycle else None
    sched_cycle_label = billing_cycle_label(sched_cycle) if sched_cycle else None

    common_current = {
        **base_target_fields,
        'has_active_plan': True,
        'current_subscription_id': active_sub.id,
        'current_package_id': current_pkg.id,
        'current_package_name': current_pkg.name,
        'current_billing_cycle': current_cycle,
        'current_billing_cycle_label': billing_cycle_label(current_cycle),
        'current_price': current_price,
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': end_date.strftime('%Y-%m-%d'),
        'end_date_formatted': end_date.strftime('%d-%b-%Y'),
        'total_days': total_days,
        'remaining_days': remaining_days,
        'daily_rate': daily_rate,
        'scheduled_package_id': sched_pkg_id,
        'scheduled_package_name': sched_pkg_name,
        'scheduled_billing_cycle': sched_cycle,
        'scheduled_billing_cycle_label': sched_cycle_label,
    }

    if not target_package:
        return {
            **common_current,
            'deduction_credit': deduction_credit,
            'excess_credit': 0.0,
            'extra_days': 0,
            'effective_duration_days': target_days,
            'net_payable': 0.0,
            'net_payable_paise': 0,
            'effective_date': today.strftime('%Y-%m-%d'),
            'effective_date_formatted': today.strftime('%d-%b-%Y'),
            'new_end_date': end_date.strftime('%Y-%m-%d'),
            'new_end_date_formatted': end_date.strftime('%d-%b-%Y'),
            'action_type': 'current',
            'is_current': True,
            'is_downgrade': False,
            'is_upgrade': False,
            'is_scheduled_switch': False,
            'is_already_scheduled': False,
            'requires_payment': False,
            'can_switch': False,
            'block_reason': '',
        }

    is_same_pkg = (target_package.id == current_pkg.id)
    is_same_cycle = (norm_target_cycle == current_cycle)
    is_already_scheduled = bool(sched_pkg_id == target_package.id and sched_cycle == norm_target_cycle)

    if not target_cycle_active:
        return {
            **common_current,
            'deduction_credit': 0.0,
            'excess_credit': 0.0,
            'extra_days': 0,
            'effective_duration_days': target_days,
            'net_payable': 0.0,
            'net_payable_paise': 0,
            'effective_date': end_date.strftime('%Y-%m-%d'),
            'effective_date_formatted': end_date.strftime('%d-%b-%Y'),
            'new_end_date': end_date.strftime('%Y-%m-%d'),
            'new_end_date_formatted': end_date.strftime('%d-%b-%Y'),
            'action_type': 'unavailable',
            'is_current': False,
            'is_downgrade': False,
            'is_upgrade': False,
            'is_scheduled_switch': False,
            'is_already_scheduled': False,
            'requires_payment': False,
            'can_switch': False,
            'block_reason': f'{billing_cycle_label(norm_target_cycle)} billing is not available for {target_package.name}.',
        }

    # 1. Exact same package and same cycle
    if is_same_pkg and is_same_cycle:
        if is_renewal:
            if is_target_free_trial:
                return {
                    **common_current,
                    'deduction_credit': 0.0,
                    'excess_credit': 0.0,
                    'extra_days': 0,
                    'effective_duration_days': target_days,
                    'net_payable': 0.0,
                    'net_payable_paise': 0,
                    'effective_date': end_date.strftime('%Y-%m-%d'),
                    'effective_date_formatted': end_date.strftime('%d-%b-%Y'),
                    'new_end_date': end_date.strftime('%Y-%m-%d'),
                    'new_end_date_formatted': end_date.strftime('%d-%b-%Y'),
                    'action_type': 'current',
                    'is_current': True,
                    'is_downgrade': False,
                    'is_upgrade': False,
                    'is_scheduled_switch': False,
                    'is_already_scheduled': False,
                    'requires_payment': False,
                    'can_switch': False,
                    'block_reason': 'The 1-Month Free Trial cannot be renewed.',
                }
            new_end = end_date + datetime.timedelta(days=target_days)
            return {
                **common_current,
                'deduction_credit': 0.0,
                'excess_credit': 0.0,
                'extra_days': 0,
                'effective_duration_days': target_days,
                'net_payable': target_price,
                'net_payable_paise': int(round(target_price * 100)),
                'effective_date': end_date.strftime('%Y-%m-%d'),
                'effective_date_formatted': end_date.strftime('%d-%b-%Y'),
                'new_end_date': new_end.strftime('%Y-%m-%d'),
                'new_end_date_formatted': new_end.strftime('%d-%b-%Y'),
                'action_type': 'renew',
                'is_current': True,
                'is_downgrade': False,
                'is_upgrade': False,
                'is_scheduled_switch': False,
                'is_already_scheduled': False,
                'requires_payment': target_price > 0,
                'can_switch': True,
                'block_reason': '',
            }

        return {
            **common_current,
            'deduction_credit': 0.0,
            'excess_credit': 0.0,
            'extra_days': 0,
            'effective_duration_days': target_days,
            'net_payable': 0.0,
            'net_payable_paise': 0,
            'effective_date': today.strftime('%Y-%m-%d'),
            'effective_date_formatted': today.strftime('%d-%b-%Y'),
            'new_end_date': end_date.strftime('%Y-%m-%d'),
            'new_end_date_formatted': end_date.strftime('%d-%b-%Y'),
            'action_type': 'current',
            'is_current': True,
            'is_downgrade': False,
            'is_upgrade': False,
            'is_scheduled_switch': False,
            'is_already_scheduled': False,
            'requires_payment': False,
            'can_switch': False,
            'block_reason': f'You are already subscribed to {current_pkg.name} ({billing_cycle_label(current_cycle)}).',
        }

    # 2. Immediate Upgrade (Low -> High):
    #    - Higher tier package (including Eco-Starter Quarterly -> Bumper Yield Monthly)
    #    - OR same tier package upgrading to a longer cycle (Monthly -> Quarterly -> Annual)
    is_tier_upgrade = target_pkg_monthly > current_pkg_monthly
    is_same_tier_cycle_upgrade = (target_pkg_monthly == current_pkg_monthly) and (
        CYCLE_RANK[norm_target_cycle] > CYCLE_RANK[current_cycle]
    )

    if is_tier_upgrade or is_same_tier_cycle_upgrade:
        net_payable = max(0.0, round(target_price - deduction_credit, 2))
        excess_credit = max(0.0, round(deduction_credit - target_price, 2))
        target_daily = (target_price / target_days) if (target_price > 0 and target_days > 0) else 0.0
        extra_days = int(excess_credit // target_daily) if target_daily > 0 else 0
        effective_duration = target_days + extra_days
        new_end = today + datetime.timedelta(days=effective_duration)
        can_switch = not (is_target_free_trial and trial_claimed)
        block_reason = 'Free Trial already claimed.' if not can_switch else ''

        return {
            **common_current,
            'deduction_credit': deduction_credit,
            'excess_credit': excess_credit,
            'extra_days': extra_days,
            'effective_duration_days': effective_duration,
            'net_payable': net_payable,
            'net_payable_paise': int(round(net_payable * 100)),
            'effective_date': today.strftime('%Y-%m-%d'),
            'effective_date_formatted': today.strftime('%d-%b-%Y'),
            'new_end_date': new_end.strftime('%Y-%m-%d'),
            'new_end_date_formatted': new_end.strftime('%d-%b-%Y'),
            'action_type': 'upgrade',
            'is_current': False,
            'is_downgrade': False,
            'is_upgrade': True,
            'is_scheduled_switch': False,
            'is_already_scheduled': is_already_scheduled,
            'requires_payment': net_payable > 0,
            'can_switch': can_switch,
            'block_reason': block_reason,
        }

    # 3. High -> Low Blocked While Current Plan Is Active:
    #    - Any lower-priced tier package (e.g. Bumper Yield -> Eco-Starter, or Paid -> Free Trial)
    #    - Or same tier moving to a shorter billing cycle (e.g. Quarterly -> Monthly, Annual -> Quarterly/Monthly)
    #    Only allowed once the current paid subscription period has completed (expired).
    end_date_str = end_date.strftime('%d-%b-%Y')
    action_type = 'switch_cycle' if is_same_pkg else 'downgrade'
    if is_target_free_trial:
        block_reason = f'Free Trial is not available while {current_pkg.name} is active. Available after {end_date_str}.'
    elif is_same_pkg:
        block_reason = (
            f'Switching from {billing_cycle_label(current_cycle)} to {billing_cycle_label(norm_target_cycle)} '
            f'is only allowed after your current plan completes on {end_date_str}.'
        )
    else:
        block_reason = (
            f'Switching from {current_pkg.name} to {target_package.name} ({billing_cycle_label(norm_target_cycle)}) '
            f'is only allowed after your current plan completes on {end_date_str}.'
        )

    return {
        **common_current,
        'deduction_credit': 0.0,
        'excess_credit': 0.0,
        'extra_days': 0,
        'effective_duration_days': target_days,
        'net_payable': 0.0,
        'net_payable_paise': 0,
        'effective_date': end_date.strftime('%Y-%m-%d'),
        'effective_date_formatted': end_date_str,
        'new_end_date': end_date.strftime('%Y-%m-%d'),
        'new_end_date_formatted': end_date_str,
        'action_type': action_type,
        'is_current': False,
        'is_downgrade': True,
        'is_upgrade': False,
        'is_scheduled_switch': False,
        'is_already_scheduled': False,
        'requires_payment': False,
        'can_switch': False,
        'block_reason': block_reason,
    }


def schedule_subscription_switch(farmer_profile, target_package, target_cycle='monthly'):
    """
    Schedules a cycle switch (e.g. Quarterly -> Monthly) or package downgrade
    to take effect at the end of the current paid subscription without cancelling
    already-paid benefits or charging today.
    """
    from django.db import transaction

    quote = calculate_plan_switch_quote(
        farmer_profile, target_package, target_cycle=target_cycle, is_renewal=False
    )
    if not quote['has_active_plan']:
        return False, 'No active subscription found to schedule a switch.', quote
    if not quote['can_switch'] or not quote['is_scheduled_switch']:
        msg = quote.get('block_reason') or 'This plan change cannot be scheduled.'
        return False, msg, quote

    with transaction.atomic():
        active_sub = (
            Subscription.objects.select_for_update()
            .filter(id=quote['current_subscription_id'], farmer=farmer_profile)
            .first()
        )
        if not active_sub:
            return False, 'Active subscription record not found.', quote

        active_sub.scheduled_package = target_package
        active_sub.scheduled_billing_cycle = quote['target_billing_cycle']
        active_sub.auto_renew = True
        active_sub.save(update_fields=['scheduled_package', 'scheduled_billing_cycle', 'auto_renew', 'updated_at'])

    updated_quote = calculate_plan_switch_quote(
        farmer_profile, target_package, target_cycle=target_cycle, is_renewal=False
    )
    msg = (
        f"Scheduled switch to {target_package.name} ({quote['target_billing_cycle_label']}) "
        f"effective {quote['effective_date_formatted']}. Your current paid {quote['current_billing_cycle_label']} "
        f"plan remains active until {quote['end_date_formatted']}."
    )
    return True, msg, updated_quote


def cancel_scheduled_subscription_switch(farmer_profile):
    """Clears any pending end-of-cycle scheduled plan/cycle switch on the active subscription."""
    active_sub = get_active_paid_subscription(farmer_profile)
    if not active_sub:
        return False, 'No active subscription found.'
    if not active_sub.scheduled_package and not active_sub.scheduled_billing_cycle:
        return True, 'No scheduled switch to cancel.'
    active_sub.scheduled_package = None
    active_sub.scheduled_billing_cycle = None
    active_sub.save(update_fields=['scheduled_package', 'scheduled_billing_cycle', 'updated_at'])
    return True, 'Scheduled plan change cancelled. Your current plan will continue as-is.'


def initiate_farmer_subscription_order(farmer_profile, user, target_package, target_cycle='monthly', is_renewal=False):
    """
    Validates eligibility and creates a pending Subscription order + Razorpay order
    for new purchases, renewals, or immediate upgrades.
    """
    import random
    from decimal import Decimal
    from django.db import transaction
    from apps.superadmin.razorpay_utils import create_razorpay_order, get_razorpay_credentials

    quote = calculate_plan_switch_quote(
        farmer_profile, target_package, target_cycle=target_cycle, is_renewal=is_renewal
    )
    if not quote['can_switch']:
        return False, quote.get('block_reason') or 'Plan switch not allowed.', quote, None

    if quote['is_scheduled_switch']:
        return False, 'This change takes effect at the end of your current paid period and requires no payment today.', quote, None

    today = timezone.now().date()
    norm_cycle = quote['target_billing_cycle']
    cycle_price = quote['target_price']
    net_payable = quote['net_payable']
    deduction_credit = quote['deduction_credit']
    amount_paise = quote['net_payable_paise']

    if is_renewal and quote['has_active_plan']:
        start_date = datetime.date.fromisoformat(quote['start_date'])
    else:
        start_date = today
    end_date = datetime.date.fromisoformat(quote['new_end_date'])

    user_mobile = (
        farmer_profile.mobile_number
        if farmer_profile and farmer_profile.mobile_number
        else (user.username if user else '')
    )
    user_fullname = (
        (user.get_full_name() or user.first_name) if user else ''
    ) or (farmer_profile.company_name if farmer_profile else 'Farmer') or 'Farmer'

    if amount_paise <= 0:
        order_id = f"order_free_{random.randint(100000, 999999)}"
        key_id = 'free_tier'
    else:
        creds = get_razorpay_credentials()
        if not creds.get('enabled', True):
            return False, 'Razorpay payment gateway is currently disabled.', quote, None
        key_id = creds['key_id']
        order_id, _, _ = create_razorpay_order(
            amount_paise=amount_paise,
            receipt=f"rcpt_{user.id if user else farmer_profile.id}_{target_package.id}",
            notes={
                'package_name': target_package.name,
                'user_id': user.id if user else farmer_profile.id,
                'billing_cycle': norm_cycle,
            },
        )
        if not order_id:
            order_id = f"order_mock_{random.randint(100000, 999999)}"

    with transaction.atomic():
        # Cancel stale unverified pending attempts so duplicate pending rows don't pile up
        Subscription.objects.filter(
            farmer=farmer_profile,
            subscription_status='pending',
            payment_status='pending',
        ).update(subscription_status='cancelled', payment_status='cancelled')

        pending_sub = Subscription.objects.create(
            farmer=farmer_profile,
            package=target_package,
            monthly_amount=Decimal(str(cycle_price)),
            amount_paid=Decimal(str(net_payable)),
            credit_applied=Decimal(str(deduction_credit)),
            billing_cycle=norm_cycle,
            auto_renew=True,
            payment_status='pending',
            subscription_status='pending',
            razorpay_order_id=order_id,
            start_date=start_date,
            end_date=end_date,
        )

    order_payload = {
        'order_id': order_id,
        'amount': amount_paise,
        'net_payable': net_payable,
        'key_id': key_id,
        'package_name': target_package.name,
        'package_id': target_package.id,
        'billing_cycle': norm_cycle,
        'billing_cycle_label': billing_cycle_label(norm_cycle),
        'mobile_number': user_mobile,
        'fullname': user_fullname,
        'pending_subscription_id': pending_sub.id,
        'quote': quote,
    }
    return True, 'Order initiated.', quote, order_payload


def mark_pending_subscription_status(farmer_profile, razorpay_order_id='', new_status='failed'):
    """
    Marks an unactivated pending Subscription record as failed or cancelled
    without altering any existing active subscription.
    """
    if not farmer_profile or not razorpay_order_id:
        return False
    status_val = 'cancelled' if str(new_status).lower() in ('cancel', 'cancelled', 'canceled') else 'failed'
    updated = Subscription.objects.filter(
        farmer=farmer_profile,
        razorpay_order_id=razorpay_order_id,
        subscription_status='pending',
        payment_status='pending',
    ).update(payment_status=status_val, subscription_status='cancelled')
    return updated > 0


def complete_farmer_subscription_payment(
    farmer_profile,
    package,
    razorpay_order_id,
    razorpay_payment_id,
    razorpay_signature,
    billing_cycle='monthly',
    is_renewal=False,
    mobile_number='',
    request=None,
):
    """
    Verifies Razorpay signature / zero-cost eligibility, prevents duplicate activation,
    cancels superseded active subscriptions, and activates the paid subscription with
    exact billing_cycle, duration, and payment amounts.
    Returns (success, error_message, subscription, is_idempotent_replay)
    """
    from decimal import Decimal
    from django.db import transaction
    from apps.superadmin.razorpay_utils import verify_razorpay_signature

    norm_cycle = normalize_billing_cycle(billing_cycle)
    today = timezone.now().date()

    with transaction.atomic():
        locked_subs = Subscription.objects.select_for_update().filter(farmer=farmer_profile)

        # Idempotency: replay of an already verified payment or order
        if razorpay_payment_id and not razorpay_payment_id.startswith(('pay_free_mock', 'pay_mock')):
            existing_paid = locked_subs.filter(razorpay_payment_id=razorpay_payment_id).first()
            if existing_paid and is_payment_settled(existing_paid.payment_status):
                return True, '', existing_paid, True

        pending_sub = None
        if razorpay_order_id:
            pending_sub = locked_subs.filter(razorpay_order_id=razorpay_order_id).first()
            if pending_sub and is_payment_settled(pending_sub.payment_status) and pending_sub.subscription_status == 'active':
                return True, '', pending_sub, True

        if pending_sub:
            target_package = pending_sub.package or package
            norm_cycle = normalize_billing_cycle(pending_sub.billing_cycle or norm_cycle)
            cycle_price = float(pending_sub.monthly_amount or get_package_cycle_price(target_package, norm_cycle))
            expected_paid = float(pending_sub.amount_paid if pending_sub.amount_paid is not None else cycle_price)
            credit_applied = float(pending_sub.credit_applied or 0.0)
            start_date = pending_sub.start_date or today
            end_date = pending_sub.end_date or (today + datetime.timedelta(days=billing_cycle_days(norm_cycle)))
            existing_active = get_active_paid_subscription(farmer_profile, today=today)
            if existing_active and existing_active.id != pending_sub.id and target_package:
                guard_quote = calculate_plan_switch_quote(
                    farmer_profile, target_package, target_cycle=norm_cycle, is_renewal=is_renewal
                )
                if guard_quote.get('is_downgrade') and not guard_quote.get('can_switch'):
                    pending_sub.payment_status = 'failed'
                    pending_sub.subscription_status = 'cancelled'
                    pending_sub.save(update_fields=['payment_status', 'subscription_status', 'updated_at'])
                    return (
                        False,
                        guard_quote.get('block_reason') or 'High-to-low plan switches are only allowed after your current plan completes.',
                        None,
                        False,
                    )
            is_upgrade_email = bool(existing_active and target_package and existing_active.package_id != target_package.id)
        else:
            target_package = package
            if not target_package:
                return False, 'Selected plan not found.', None, False
            quote = calculate_plan_switch_quote(
                farmer_profile, target_package, target_cycle=norm_cycle, is_renewal=is_renewal
            )
            if not quote['can_switch'] and not quote.get('is_current'):
                return False, quote.get('block_reason') or 'Plan change is not permitted.', None, False
            if quote.get('is_current') and not is_renewal:
                return False, quote.get('block_reason') or 'This plan is already active.', None, False
            if quote.get('is_scheduled_switch'):
                return False, 'This plan switch must be scheduled for end of cycle.', None, False

            cycle_price = quote['target_price']
            expected_paid = quote['net_payable']
            credit_applied = quote['deduction_credit']
            start_date = datetime.date.fromisoformat(quote['start_date']) if (is_renewal and quote['has_active_plan']) else today
            end_date = datetime.date.fromisoformat(quote['new_end_date'])
            is_upgrade_email = bool(quote['has_active_plan'] and quote['current_package_id'] != target_package.id)

        if not target_package:
            return False, 'Selected plan not found.', None, False

        is_free_trial = bool(float(target_package.price or 0.0) == 0.0 or 'trial' in (target_package.name or '').lower())
        if is_free_trial and has_farmer_used_free_trial(farmer_profile, mobile_number):
            if pending_sub:
                pending_sub.payment_status = 'failed'
                pending_sub.subscription_status = 'cancelled'
                pending_sub.save(update_fields=['payment_status', 'subscription_status', 'updated_at'])
            return False, 'The 1-Month Free Trial is strictly limited to one per mobile number and has already been claimed.', None, False

        expected_paise = int(round(expected_paid * 100))
        if expected_paise > 0 and razorpay_signature == 'free_tier_bypass':
            if pending_sub:
                pending_sub.payment_status = 'failed'
                pending_sub.subscription_status = 'cancelled'
                pending_sub.save(update_fields=['payment_status', 'subscription_status', 'updated_at'])
            return False, 'Payment verification failed: invalid free bypass for a paid plan.', None, False

        if expected_paise > 0:
            is_valid_sig, sig_msg = verify_razorpay_signature(
                razorpay_order_id, razorpay_payment_id, razorpay_signature
            )
            if not is_valid_sig:
                if pending_sub:
                    pending_sub.payment_status = 'failed'
                    pending_sub.subscription_status = 'cancelled'
                    pending_sub.save(update_fields=['payment_status', 'subscription_status', 'updated_at'])
                return False, f'Payment verification failed: {sig_msg}', None, False

        # Deactivate prior active subscriptions only after payment is verified
        prior_active_qs = locked_subs.filter(subscription_status='active')
        if pending_sub:
            prior_active_qs = prior_active_qs.exclude(id=pending_sub.id)
        prior_active_qs.update(subscription_status='cancelled')

        if pending_sub:
            pending_sub.package = target_package
            pending_sub.billing_cycle = norm_cycle
            pending_sub.monthly_amount = Decimal(str(cycle_price))
            pending_sub.amount_paid = Decimal(str(expected_paid))
            pending_sub.credit_applied = Decimal(str(credit_applied))
            pending_sub.payment_status = 'success'
            pending_sub.subscription_status = 'active'
            pending_sub.razorpay_order_id = razorpay_order_id
            pending_sub.razorpay_payment_id = razorpay_payment_id
            pending_sub.razorpay_signature = razorpay_signature
            pending_sub.scheduled_package = None
            pending_sub.scheduled_billing_cycle = None
            pending_sub.start_date = start_date
            pending_sub.end_date = end_date
            pending_sub.save()
            subscription = pending_sub
        else:
            subscription = Subscription.objects.create(
                farmer=farmer_profile,
                package=target_package,
                monthly_amount=Decimal(str(cycle_price)),
                amount_paid=Decimal(str(expected_paid)),
                credit_applied=Decimal(str(credit_applied)),
                billing_cycle=norm_cycle,
                auto_renew=True,
                payment_status='success',
                subscription_status='active',
                razorpay_order_id=razorpay_order_id,
                razorpay_payment_id=razorpay_payment_id,
                razorpay_signature=razorpay_signature,
                start_date=start_date,
                end_date=end_date,
            )

    try:
        if farmer_profile.email:
            from apps.superadmin.email_utils import send_renewal_email, send_package_purchase_email
            if is_renewal:
                send_renewal_email(subscription, amount=expected_paid, request=request)
            else:
                send_package_purchase_email(
                    subscription, is_upgrade=is_upgrade_email, amount=expected_paid, request=request
                )
    except Exception as e:
        print(f'[Email Trigger Error in complete_farmer_subscription_payment]: {e}')

    return True, '', subscription, False

