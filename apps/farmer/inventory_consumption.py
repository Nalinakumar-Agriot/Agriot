"""Validation and stock updates for consumable inventory."""
import math

from apps.farmer.models import FarmerInventory, InventoryStockLog
from django.utils import timezone


def is_equipment(item):
    category = (item.category or '').lower()
    return bool(item.rental_available or item.license_plate) or any(
        word in category for word in ('vehicle', 'fleet', 'machin', 'tool', 'equipment', 'implement', 'tractor')
    )


def is_fuel(item):
    category = (item.category or '').lower()
    name = (item.item_name or '').lower()
    return not is_equipment(item) and any(word in category or word in name for word in ('fuel', 'diesel', 'petrol'))


def inventory_layout(item, category_layouts=None):
    """Return the configured inventory UI group without guessing from item names."""
    category = (item.category or '').strip().lower()
    configured = (category_layouts or {}).get(category, '')
    layout = str(configured or '').strip().lower()
    if layout:
        if 'tool' in layout or 'implement' in layout:
            return 'tools'
        if any(word in layout for word in ('machin', 'vehicle', 'fleet')):
            return 'machinery'
        return 'warehouse'
    if any(word in category for word in ('tool', 'implement')):
        return 'tools'
    if any(word in category for word in ('machin', 'vehicle', 'fleet', 'tractor', 'equipment')):
        return 'machinery'
    return 'warehouse'


def consume_stock(*, farmer, item_id, quantity, farm, crop_split=None, fuel_only=False, vehicle=None):
    """Call inside an atomic transaction; equipment itself is never consumed."""
    try:
        qty = float(quantity)
    except (TypeError, ValueError):
        raise ValueError('Enter a valid consumption quantity.')
    if not math.isfinite(qty) or qty <= 0:
        raise ValueError('Enter a valid consumption quantity greater than zero.')
    item = FarmerInventory.objects.select_for_update().filter(pk=item_id, farmer=farmer).first()
    if not item:
        raise ValueError('The selected inventory item was not found.')
    if is_equipment(item):
        raise ValueError('A vehicle or tool cannot be consumed as stock. Select a fuel inventory item instead.')
    if fuel_only and not is_fuel(item):
        raise ValueError('Select a petrol or diesel fuel inventory item.')
    if fuel_only and vehicle:
        vehicle_fuel = (vehicle.fuel_type or '').strip().lower()
        fuel_name = (item.item_name or '').lower()
        if vehicle_fuel in ('diesel', 'petrol') and any(
            other in fuel_name for other in ('diesel', 'petrol') if other != vehicle_fuel
        ):
            raise ValueError(f'{vehicle.item_name} uses {vehicle.fuel_type}; select matching fuel stock.')
    if fuel_only and (item.unit or '').strip().lower() not in ('l', 'ltr', 'ltrs', 'litre', 'litres', 'liter', 'liters'):
        raise ValueError('The fuel inventory unit must be litres.')
    old_qty = float(item.quantity or 0)
    if qty > old_qty:
        raise ValueError(f'Not enough {item.item_name} in stock. Available: {old_qty:g} {item.unit}.')
    item.quantity = round(old_qty - qty, 6)
    item.status = 'Out of Stock' if item.quantity <= 0 else ('Low Stock' if item.quantity <= (item.safety_stock or 0) else 'In Stock')
    item.save(update_fields=['quantity', 'status'])
    notes = f'Used in Farm: {farm.land_name}'
    if vehicle:
        notes += f' | Vehicle: {vehicle.item_name}'
    if crop_split and crop_split.crop:
        notes += f' | Crop: {crop_split.crop.name}'
    InventoryStockLog.objects.create(inventory=item, farm=farm, crop_split=crop_split, action='remove',
        quantity=qty, notes=notes, log_date=timezone.localdate(), qty_before=old_qty, qty_after=item.quantity)
    return item
