"""Keep ledger deletion and its related balances consistent."""
from django.db import transaction
from django.db.models import Q


@transaction.atomic
def delete_ledger_transaction(item):
    from apps.farmer.models import LedgerTransaction, FarmerOutstanding
    from apps.farmer.views import restore_crop_inventory

    item = LedgerTransaction.objects.select_for_update().get(pk=item.pk, farmer=item.farmer)
    is_sale = item.category != 'Customer Payment' and (
        'Inflow' in (item.tags or '') or item.amount > 0 or item.status == 'Received'
    )
    if is_sale and item.quantity and item.quantity > 0 and item.category:
        restore_crop_inventory(item.farmer, item.category, float(item.quantity), item.farm)

    # A numeric boundary prevents sale #1 from matching sale #10.
    sale_reference = rf'Sale #{item.pk}([^0-9]|$)'
    FarmerOutstanding.objects.filter(
        farmer=item.farmer, invoice_no__regex=sale_reference
    ).delete()
    LedgerTransaction.objects.filter(farmer=item.farmer).filter(
        Q(tags__regex=rf'Linked Sale #{item.pk}([^0-9]|$)') |
        Q(category='Customer Payment', description__regex=rf'Invoice Sale #{item.pk}([^0-9]|$)')
    ).exclude(pk=item.pk).delete()
    item.delete()
