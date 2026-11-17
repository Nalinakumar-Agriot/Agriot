import re

from django.db.models import Q

from apps.farmer.models import CropSplit, LedgerTransaction


def _has_split_reference(value, split_id):
    if not value:
        return False
    split_id = re.escape(str(split_id))
    return bool(
        re.search(rf"CropSplit\s*#\s*{split_id}(?!\d)", value, re.IGNORECASE)
        or re.search(
            rf"Crop\s+Split\s+ID\s*:\s*{split_id}(?!\d)",
            value,
            re.IGNORECASE,
        )
    )


def crop_split_delete_block_reason(split):
    if (split.stage or "").lower() == "sold" or any(
        str(batch.get("status", "")).lower() == "sold"
        for batch in (split.harvest_batches or [])
        if isinstance(batch, dict)
    ):
        return "This crop cannot be deleted because it has sales records."

    transactions = LedgerTransaction.objects.filter(
        farmer_id=split.farm.farmer_id,
    ).filter(
        Q(farm_id=split.farm_id)
        | Q(tags__icontains="CropSplit")
        | Q(tags__icontains="Crop Split ID:")
        | Q(description__icontains="CropSplit")
    ).values_list("tags", "description")
    if any(
        _has_split_reference(tags, split.id)
        or _has_split_reference(description, split.id)
        for tags, description in transactions
    ):
        return (
            "This crop cannot be deleted because it has associated expense "
            "or sales records."
        )
    return None


def farm_delete_block_reason(farm):
    transactions = LedgerTransaction.objects.filter(
        farmer_id=farm.farmer_id,
    ).filter(
        Q(farm_id=farm.id) | Q(tags__icontains=f"Farm ID: {farm.id}")
    ).filter(
        Q(status__in=["Cost", "Pending", "Paid", "Received", "Income", "Settled"])
        | Q(amount__lt=0)
        | Q(tags__icontains="Outflow")
        | Q(category__iexact="Sales")
        | Q(category__iexact="Harvest Trade")
    ).values_list("tags", "description", "farm_id")

    farm_reference = re.compile(
        rf"Farm\s+ID\s*:\s*{re.escape(str(farm.id))}(?!\d)",
        re.IGNORECASE,
    )
    if any(
        transaction_farm_id == farm.id
        or farm_reference.search(tags or "")
        or farm_reference.search(description or "")
        for tags, description, transaction_farm_id in transactions
    ):
        return (
            "This farm cannot be deleted because it has associated expense "
            "or sales records."
        )

    for split in CropSplit.objects.filter(farm=farm):
        if crop_split_delete_block_reason(split):
            return (
                "This farm cannot be deleted because it contains a crop with "
                "associated expense or sales records."
            )
    return None
