from rest_framework import serializers

class FarmerSendOTPSerializer(serializers.Serializer):
    mobile = serializers.CharField(max_length=10, min_length=10, help_text="10-digit mobile number")

    def validate_mobile(self, value):
        if not value.isdigit():
            raise serializers.ValidationError("Mobile number must contain only digits.")
        return value

class FarmerVerifyOTPSerializer(serializers.Serializer):
    mobile = serializers.CharField(max_length=10, min_length=10, help_text="10-digit mobile number")
    otp = serializers.CharField(max_length=6, min_length=6, help_text="6-digit OTP received via SMS")

    def validate_mobile(self, value):
        if not value.isdigit():
            raise serializers.ValidationError("Mobile number must contain only digits.")
        return value

class FarmerProfileDetailSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    mobile_number = serializers.CharField()
    email = serializers.EmailField(allow_null=True)
    status = serializers.CharField()
    state_name = serializers.CharField(source='state.name', allow_null=True)
    district_name = serializers.CharField(source='district.name', allow_null=True)
    pincode = serializers.CharField(allow_null=True)
    land_count = serializers.SerializerMethodField()
    crop_count = serializers.SerializerMethodField()
    
    profile = serializers.SerializerMethodField()
    name = serializers.SerializerMethodField()
    farmer_id = serializers.SerializerMethodField()
    main_harvest_crop = serializers.SerializerMethodField()
    active_acreage = serializers.SerializerMethodField()
    soil_health_index = serializers.SerializerMethodField()
    date_registered = serializers.SerializerMethodField()

    # Personal KYC details (shared with website farmer_profile page)
    farmer_type = serializers.CharField(allow_null=True, required=False)
    company_name = serializers.CharField(allow_null=True, required=False)
    company_mobile_number = serializers.CharField(allow_null=True, required=False)
    msme_number = serializers.CharField(allow_null=True, required=False)
    msme_certificate = serializers.FileField(allow_null=True, required=False)
    aadhaar_number = serializers.CharField(allow_null=True, required=False)
    pan_number = serializers.CharField(allow_null=True, required=False)
    pmkisan_id = serializers.CharField(allow_null=True, required=False)
    father_name = serializers.CharField(allow_null=True, required=False)
    guardian_name = serializers.CharField(allow_null=True, required=False)
    gender = serializers.CharField(allow_null=True, required=False)
    date_of_birth = serializers.DateField(allow_null=True, required=False)
    educational_qualification = serializers.CharField(allow_null=True, required=False)
    taluka = serializers.CharField(allow_null=True, required=False)
    village = serializers.CharField(allow_null=True, required=False)
    address = serializers.CharField(allow_null=True, required=False)
    
    def get_profile(self, obj):
        if obj.profile_image:
            return obj.profile_image.url
        return ""
        
    def get_name(self, obj):
        return obj.user.first_name if obj.user.first_name else obj.user.username
        
    def get_farmer_id(self, obj):
        return obj.user.username
        
    def get_main_harvest_crop(self, obj):
        from apps.farmer.models import CropSplit
        split = CropSplit.objects.filter(farm__farmer=obj).exclude(stage__in=['Harvested', 'Sold']).first()
        if split and split.crop:
            return split.crop.name
        return "N/A"
        
    def get_active_acreage(self, obj):
        from apps.farmer.models import Farm
        farms = Farm.objects.filter(farmer=obj)
        total = sum([float(f.total_area) for f in farms if f.total_area and f.total_area.replace('.','',1).isdigit()])
        return f"{total} Acres"
        
    def get_soil_health_index(self, obj):
        from apps.farmer.models import Farm
        farm = Farm.objects.filter(farmer=obj).first()
        if farm and farm.health_indicator:
            return f"{farm.health_indicator}/100 index"
        return "N/A"
        
    def get_date_registered(self, obj):
        return obj.user.date_joined.strftime("%d-%b-%Y")
    
    def get_land_count(self, obj):
        from apps.farmer.models import Farm
        return Farm.objects.filter(farmer=obj).count()
        
    def get_crop_count(self, obj):
        from apps.farmer.models import CropSplit
        return CropSplit.objects.filter(farm__farmer=obj).exclude(stage__in=['Harvested', 'Sold']).count()
        
    def update(self, instance, validated_data):
        instance.email = validated_data.get('email', instance.email)
        instance.pincode = validated_data.get('pincode', instance.pincode)
        instance.save()
        return instance

class FarmerSubscriptionSerializer(serializers.Serializer):
    package_name = serializers.CharField(source='package.name', allow_null=True)
    package_price = serializers.DecimalField(source='package.price', max_digits=10, decimal_places=2, allow_null=True)
    monthly_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    payment_status = serializers.CharField()
    subscription_status = serializers.CharField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    
    max_acre_limit = serializers.CharField(source='package.max_acre_support', allow_null=True)
    balance_acre_limit = serializers.SerializerMethodField()
    max_land_limit = serializers.CharField(source='package.my_land', allow_null=True)
    balance_land_limit = serializers.SerializerMethodField()
    max_crop_limit = serializers.CharField(source='package.my_crop', allow_null=True)
    balance_crop_limit = serializers.SerializerMethodField()
    max_user_limit = serializers.CharField(source='package.my_users', allow_null=True)
    balance_user_limit = serializers.SerializerMethodField()

    def get_balance_acre_limit(self, obj):
        limit = obj.package.max_acre_support if obj.package else None
        if not limit:
            return None
        try:
            limit_val = float(limit)
        except ValueError:
            return limit
            
        from apps.farmer.models import Farm
        farms = Farm.objects.filter(farmer=obj.farmer)
        total_used = 0.0
        for f in farms:
            if f.total_area:
                try:
                    total_used += float(f.total_area)
                except ValueError:
                    pass
        balance = limit_val - total_used
        return str(round(balance, 2)) if balance > 0 else "0"

    def get_balance_land_limit(self, obj):
        limit = obj.package.my_land if obj.package else None
        if not limit:
            return None
        try:
            limit_val = int(limit)
        except ValueError:
            return limit
            
        from apps.farmer.models import Farm
        used = Farm.objects.filter(farmer=obj.farmer).count()
        balance = limit_val - used
        return str(balance) if balance > 0 else "0"

    def get_balance_crop_limit(self, obj):
        limit = obj.package.my_crop if obj.package else None
        if not limit:
            return None
        try:
            limit_val = int(limit)
        except ValueError:
            return limit
            
        from apps.farmer.models import CropSplit
        used = CropSplit.objects.filter(farm__farmer=obj.farmer).count()
        balance = limit_val - used
        return str(balance) if balance > 0 else "0"

    def get_balance_user_limit(self, obj):
        limit = obj.package.my_users if obj.package else None
        if not limit:
            return None
        try:
            limit_val = int(limit)
        except ValueError:
            return limit
            
        from apps.farmer.models import FarmerStaff
        used = FarmerStaff.objects.filter(farmer=obj.farmer).count()
        balance = limit_val - used
        return str(balance) if balance > 0 else "0"

from apps.farmer.models import Farm

class FarmSerializer(serializers.ModelSerializer):
    crop_type_name = serializers.CharField(source='crop_type.name', read_only=True, allow_null=True)
    state_name = serializers.CharField(source='state.name', read_only=True, allow_null=True)
    district_name = serializers.CharField(source='district.name', read_only=True, allow_null=True)
    taluk_name = serializers.CharField(source='taluk.name', read_only=True, allow_null=True)
    crop_count = serializers.SerializerMethodField()

    class Meta:
        model = Farm
        fields = '__all__'
        read_only_fields = ['farmer', 'created_at', 'updated_at']

    def get_crop_count(self, obj):
        return obj.splits.exclude(stage__in=['Harvested', 'Sold']).count()

from apps.farmer.models import CropSplit, FarmActivity, FarmTask


class RoundedDecimalField(serializers.DecimalField):
    """DecimalField that rounds input to decimal_places before validating,
    so high-precision doubles (e.g. GPS coordinates from map pickers)
    don't fail with digit-limit errors."""

    def to_internal_value(self, data):
        try:
            from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
            quant = Decimal(1).scaleb(-self.decimal_places)
            data = Decimal(str(data)).quantize(quant, rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError, TypeError):
            pass
        return super().to_internal_value(data)


class CropSplitSerializer(serializers.ModelSerializer):
    crop_name = serializers.CharField(source='crop.name', read_only=True, allow_null=True)
    market_price = serializers.SerializerMethodField()
    crop_latitude = RoundedDecimalField(
        max_digits=18, decimal_places=8, allow_null=True, required=False)
    crop_longitude = RoundedDecimalField(
        max_digits=18, decimal_places=8, allow_null=True, required=False)

    class Meta:
        model = CropSplit
        fields = '__all__'

    def get_market_price(self, obj):
        if not obj.crop or not obj.crop.name:
            return 0
        from apps.superadmin.models import DepotCropPricing, DepotTerminal, MarketPriceIndex
        from django.db.models import Q
        c_name = obj.crop.name
        clean_name = c_name.split('(')[0].strip()

        farmer_city = None
        if obj.farm and obj.farm.farmer and obj.farm.farmer.district:
            farmer_city = obj.farm.farmer.district.name.lower()

        market_price = 0.0

        if farmer_city:
            local_dcp = DepotCropPricing.objects.filter(
                crop_index__crop_name__icontains=clean_name,
                terminal_rate__gt=0
            ).filter(
                Q(depot__district__icontains=farmer_city) | Q(depot__location__icontains=farmer_city)
            ).first()
            if local_dcp and local_dcp.terminal_rate and float(local_dcp.terminal_rate) > 0:
                market_price = float(local_dcp.terminal_rate)

            if market_price == 0.0:
                local_terminals = DepotTerminal.objects.filter(
                    Q(district__icontains=farmer_city) | Q(location__icontains=farmer_city)
                )
                for terminal in local_terminals:
                    if isinstance(terminal.available_crops, list):
                        for crop_item in terminal.available_crops:
                            if isinstance(crop_item, dict):
                                item_name = crop_item.get('name', '')
                                try:
                                    price_val = float(crop_item.get('price', 0))
                                except (ValueError, TypeError):
                                    price_val = 0.0
                                if clean_name.lower() in item_name.lower() and price_val > 0:
                                    market_price = price_val
                                    break
                    if market_price > 0:
                        break

        if market_price == 0.0:
            mpi = MarketPriceIndex.objects.filter(crop_name__iexact=c_name).first()
            if not mpi:
                mpi = MarketPriceIndex.objects.filter(crop_name__icontains=clean_name).first()
            if mpi and mpi.base_price and float(mpi.base_price) > 0:
                market_price = float(mpi.base_price)

        if market_price > 0:
            return int(market_price) if market_price.is_integer() else market_price

        # fallback to previous split price
        prev = CropSplit.objects.filter(crop__name=c_name, price_per_mt__gt=0).order_by('-id').first()
        if prev and prev.price_per_mt:
            val = float(prev.price_per_mt)
            return int(val) if val.is_integer() else val
        return 0

class FarmActivitySerializer(serializers.ModelSerializer):
    inventory_item = serializers.IntegerField(required=False, write_only=True)
    crop_split_id = serializers.IntegerField(required=False, write_only=True)

    class Meta:
        model = FarmActivity
        fields = '__all__'

    def _cleaned(self, validated_data):
        # Both are mobile/web transport hints only: ConsumptionViewSet reads
        # them straight off request.data (stock decrement + crop tag in notes).
        validated_data.pop('inventory_item', None)
        validated_data.pop('crop_split_id', None)
        return validated_data

    def create(self, validated_data):
        return super().create(self._cleaned(validated_data))

    def update(self, instance, validated_data):
        return super().update(instance, self._cleaned(validated_data))

class FarmTaskSerializer(serializers.ModelSerializer):
    farm_name = serializers.CharField(source='farm.land_name', read_only=True, allow_null=True)
    crop_name = serializers.CharField(source='associated_split.crop.name', read_only=True, allow_null=True)
    task_category_name = serializers.CharField(source='task_category.name', read_only=True, allow_null=True)
    assigned_worker_name = serializers.SerializerMethodField()
    assigned_workers_names = serializers.SerializerMethodField()
    task_date = serializers.DateField(required=False)
    start_date = serializers.DateField(required=False, allow_null=True)
    end_date = serializers.DateField(required=False, allow_null=True)
    
    class Meta:
        model = FarmTask
        fields = '__all__'

    def get_assigned_worker_name(self, obj):
        if obj.assigned_worker:
            return obj.assigned_worker.name
        return None

    def get_assigned_workers_names(self, obj):
        workers = obj.assigned_workers.all()
        if workers.exists():
            return [w.name for w in workers]
        return []

    def to_representation(self, instance):
        for field_name in ('task_date', 'start_date', 'end_date'):
            val = getattr(instance, field_name, None)
            if val and hasattr(val, 'date'):
                setattr(instance, field_name, val.date())
        ret = super().to_representation(instance)
        return ret

from apps.farmer.models import LedgerTransaction, FarmerInventory

class LedgerTransactionSerializer(serializers.ModelSerializer):
    balance_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, write_only=True)
    crop_split_id = serializers.IntegerField(required=False, write_only=True)
    # Mobile payloads send the FK as `farm_id`; ModelSerializer only exposes `farm`.
    farm_id = serializers.IntegerField(required=False, write_only=True)

    class Meta:
        model = LedgerTransaction
        fields = '__all__'
        read_only_fields = ['farmer', 'created_at']

    def _cleaned(self, validated_data):
        farm_pk = validated_data.pop('farm_id', None)
        if farm_pk and not validated_data.get('farm'):
            validated_data['farm_id'] = farm_pk
        balance_amount = validated_data.pop('balance_amount', None)
        if balance_amount is not None:
            validated_data['balance_due'] = balance_amount
        validated_data.pop('crop_split_id', None)
        return validated_data

    def create(self, validated_data):
        return super().create(self._cleaned(validated_data))

    def update(self, instance, validated_data):
        return super().update(instance, self._cleaned(validated_data))

    def to_representation(self, instance):
        # Mirror web financials_sales logic (views.py): paid/balance come from
        # the linked FarmerOutstanding record, falling back to the tags written
        # at sale-create time. Mobile reads these as `paid_amount`/`balance_amount`.
        import re
        from django.db.models import Q
        from apps.farmer.models import FarmerOutstanding

        data = super().to_representation(instance)

        paid_amount = float(instance.amount)
        balance_amount = float(instance.balance_due or 0)

        out_rec = None
        if instance.id:
            out_rec = FarmerOutstanding.objects.filter(
                farmer=instance.farmer
            ).filter(
                Q(invoice_no__startswith=f"Sale #{instance.id}:") |
                Q(invoice_no__regex=rf"Sale #{instance.id}(?!\d)")
            ).first()

        if out_rec:
            paid_amount = float(out_rec.amount_paid)
            balance_amount = max(0.0, float(out_rec.amount_total) - float(out_rec.amount_paid))
        elif instance.tags:
            paid_match = re.search(r'Paid:\s*₹?([\d\.]+)', instance.tags)
            bal_match = re.search(r'Balance:\s*₹?([\d\.]+)', instance.tags)
            if paid_match:
                paid_amount = float(paid_match.group(1))
            if bal_match:
                balance_amount = float(bal_match.group(1))

        data['paid_amount'] = round(paid_amount, 2)
        data['balance_amount'] = round(balance_amount, 2)
        # Website parity: farm land name cell (financials_sales.html) — mobile
        # reads `farm_name`; without a farm the template falls back to
        # 'General Farm', which the client does.
        data['farm_name'] = instance.farm.land_name if instance.farm else None
        # Website parity: status is derived from the outstanding balance when a
        # linked FarmerOutstanding record exists (views.financials_sales).
        if out_rec:
            if balance_amount <= 0.001:
                data['status'] = 'Received'
            else:
                data['status'] = 'Partial' if paid_amount > 0 else 'Pending'
        return data

class FarmerInventorySerializer(serializers.ModelSerializer):
    class Meta:
        model = FarmerInventory
        fields = '__all__'
        read_only_fields = ['farmer', 'created_at']

from apps.farmer.models import FarmerStaff

class FarmerStaffSerializer(serializers.ModelSerializer):
    class Meta:
        model = FarmerStaff
        fields = '__all__'
        read_only_fields = ['farmer', 'created_at']

from apps.superadmin.models import (
    MarketPriceIndex, DepotTerminal, DepotCropPricing, 
    NearbyLocation, LogisticsWorker, RentalEquipment,
    CropBlueprint, BlueprintStage, Guideline, GuidelineCategory,
    DailyPriceHistory
)
from apps.farmer.models import StaffAttendance, StaffAdvance, StaffPayroll

class GuidelineCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = GuidelineCategory
        fields = '__all__'

class GuidelineSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    
    class Meta:
        model = Guideline
        fields = '__all__'

class StaffAttendanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffAttendance
        fields = '__all__'

class StaffAdvanceSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source='staff.name', read_only=True, allow_null=True)
    class Meta:
        model = StaffAdvance
        fields = '__all__'

class StaffPayrollSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='staff.name', read_only=True, allow_null=True)
    class Meta:
        model = StaffPayroll
        fields = '__all__'

class BlueprintStageSerializer(serializers.ModelSerializer):
    class Meta:
        model = BlueprintStage
        fields = '__all__'

class CropBlueprintSerializer(serializers.ModelSerializer):
    stages = BlueprintStageSerializer(many=True, read_only=True)
    
    class Meta:
        model = CropBlueprint
        fields = '__all__'

class MarketPriceIndexSerializer(serializers.ModelSerializer):
    class Meta:
        model = MarketPriceIndex
        fields = '__all__'

class DailyPriceHistorySerializer(serializers.ModelSerializer):
    market_name = serializers.CharField(source='depot.name', read_only=True)
    crop_name = serializers.CharField(source='crop_index.crop_name', read_only=True)

    class Meta:
        model = DailyPriceHistory
        fields = ['id', 'depot', 'market_name', 'crop_index', 'crop_name', 'price', 'date']

class DepotTerminalSerializer(serializers.ModelSerializer):
    class Meta:
        model = DepotTerminal
        fields = '__all__'

class DepotCropPricingSerializer(serializers.ModelSerializer):
    depot_name = serializers.CharField(source='depot.name', read_only=True)
    depot_terminal_id = serializers.CharField(source='depot.terminal_id', read_only=True)
    location = serializers.CharField(source='depot.location', read_only=True)
    distance_km = serializers.DecimalField(source='depot.distance_km', max_digits=8, decimal_places=2, read_only=True)
    demand_level = serializers.CharField(source='depot.demand_level', read_only=True)
    sourcing_active = serializers.BooleanField(source='depot.sourcing_active', read_only=True)
    
    crop_name = serializers.CharField(source='crop_index.crop_name', read_only=True)
    base_price = serializers.DecimalField(source='crop_index.base_price', max_digits=12, decimal_places=2, read_only=True)
    
    sourced_quote = serializers.SerializerMethodField()
    net_margin = serializers.SerializerMethodField()

    class Meta:
        model = DepotCropPricing
        fields = [
            'id', 'depot', 'crop_index', 'terminal_rate', 'markup_offset', 'trading_restricted',
            'depot_name', 'depot_terminal_id', 'location', 'distance_km', 'demand_level', 'sourcing_active',
            'crop_name', 'base_price', 'sourced_quote', 'net_margin'
        ]
        
    def get_sourced_quote(self, obj):
        if obj.trading_restricted:
            return None
        return float(obj.terminal_rate) + float(obj.markup_offset)
        
    def get_net_margin(self, obj):
        if obj.trading_restricted:
            return None
        return float(obj.markup_offset)

class NearbyLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = NearbyLocation
        fields = '__all__'

class LogisticsWorkerSerializer(serializers.ModelSerializer):
    class Meta:
        model = LogisticsWorker
        fields = '__all__'

class RentalEquipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = RentalEquipment
        fields = '__all__'

class CropHarvestSerializer(serializers.Serializer):
    yield_tons = serializers.DecimalField(max_digits=10, decimal_places=2, required=True)
    price_per_mt = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    total_sale_value = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    batches_json = serializers.JSONField(required=False, allow_null=True, help_text='List of harvest batch objects')

from apps.farmer.models import FarmerCustomer, FarmerVendor, FarmerOutstanding

class FarmerCustomerSerializer(serializers.ModelSerializer):
    state = serializers.CharField(max_length=100, required=False, allow_blank=True, write_only=True, help_text="State ID or Name")
    district = serializers.CharField(max_length=100, required=False, allow_blank=True, write_only=True, help_text="District ID or Name")
    pincode = serializers.CharField(max_length=20, required=False, allow_blank=True, write_only=True)

    class Meta:
        model = FarmerCustomer
        fields = '__all__'
        read_only_fields = ['farmer', 'created_at']

    @staticmethod
    def _lookup_state(value):
        """Return (state_id, state_name); legacy name values resolve to IDs."""
        from apps.superadmin.models import State
        if not value:
            return '', ''
        value = str(value)
        if value.isdigit():
            s = State.objects.filter(id=value).first()
            return value, (s.name if s else '')
        s = State.objects.filter(name__iexact=value).first()
        return (str(s.id) if s else value), (s.name if s else value)

    @staticmethod
    def _lookup_district(value):
        """Return (district_id, district_name); legacy name values resolve to IDs."""
        from apps.superadmin.models import City
        if not value:
            return '', ''
        value = str(value)
        if value.isdigit():
            c = City.objects.filter(id=value).first()
            return value, (c.name if c else '')
        c = City.objects.filter(name__iexact=value).first()
        return (str(c.id) if c else value), (c.name if c else value)

    def _farmer_profile(self):
        request = self.context.get('request')
        if not request or not getattr(request.user, 'is_authenticated', False):
            return None
        user = request.user
        if hasattr(user, 'staff_profile') and user.staff_profile:
            return user.staff_profile.farmer
        if hasattr(user, 'profile'):
            return user.profile
        return None

    def _validate_document(self, validated_data):
        if 'document' not in validated_data:
            return
        doc = validated_data['document']
        if doc is None:
            return
        profile = self._farmer_profile()
        if profile and doc.farmer_id != profile.id:
            raise serializers.ValidationError({'document': 'Invalid document.'})

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        addr = instance.address or ''
        state, district, pincode = '', '', ''
        # Legacy API rows store state|district|pincode in address (website keeps
        # values in model columns with address as free text) — support both.
        if addr and '|' in addr:
            parts = addr.split('|')
            if len(parts) >= 3:
                state, district, pincode = parts[0], parts[1], parts[2]
        if not state:
            state = instance.state or ''
        if not district:
            district = instance.district or ''
        if not pincode:
            pincode = instance.pincode or ''
        state_id, state_name = self._lookup_state(state)
        district_id, district_name = self._lookup_district(district)
        ret['state'] = state_id
        ret['district'] = district_id
        ret['pincode'] = pincode
        ret['state_name'] = state_name
        ret['district_name'] = district_name
        ret['document_type'] = instance.document.category if instance.document else ''
        ret['document_file'] = instance.document.file.name if instance.document and instance.document.file else ''
        # Website-formatted address (street, district, state, pincode)
        addr_parts = []
        if addr and '|' not in addr:
            addr_parts.append(addr.strip())
        if district_name:
            addr_parts.append(district_name)
        if state_name:
            addr_parts.append(state_name)
        if pincode:
            addr_parts.append(str(pincode).strip())
        ret['formatted_address'] = ', '.join(a for a in addr_parts if a) if addr_parts else (addr or '')
        return ret

    def create(self, validated_data):
        self._validate_document(validated_data)
        # Website parity (add_customer view): state/district/pincode in model
        # columns, address kept as free text.
        validated_data['state'] = self._lookup_state(validated_data.pop('state', '') or '')[0]
        validated_data['district'] = self._lookup_district(validated_data.pop('district', '') or '')[0]
        validated_data['pincode'] = validated_data.pop('pincode', '') or ''
        return super().create(validated_data)

    def update(self, instance, validated_data):
        self._validate_document(validated_data)
        if 'state' in validated_data:
            validated_data['state'] = self._lookup_state(validated_data.pop('state') or '')[0]
        if 'district' in validated_data:
            validated_data['district'] = self._lookup_district(validated_data.pop('district') or '')[0]
        if 'pincode' in validated_data:
            validated_data['pincode'] = validated_data.pop('pincode') or ''
        return super().update(instance, validated_data)

class FarmerVendorSerializer(serializers.ModelSerializer):
    state = serializers.CharField(max_length=100, required=False, allow_blank=True, help_text="State ID or Name")
    district = serializers.CharField(max_length=100, required=False, allow_blank=True, help_text="District ID or Name")

    class Meta:
        model = FarmerVendor
        fields = '__all__'
        read_only_fields = ['farmer', 'created_at']

    @staticmethod
    def _state_name(value):
        # Website parity (vendor view): select posts State.id, stored as name.
        from apps.superadmin.models import State
        if not value:
            return ''
        value = str(value)
        if value.isdigit():
            s = State.objects.filter(id=value).first()
            return s.name if s else value
        return value

    @staticmethod
    def _district_name(value):
        # Website parity (vendor view): select posts City.id, stored as name.
        from apps.superadmin.models import City
        if not value:
            return ''
        value = str(value)
        if value.isdigit():
            c = City.objects.filter(id=value).first()
            return c.name if c else value
        return value

    def _farmer_profile(self):
        request = self.context.get('request')
        if not request or not getattr(request.user, 'is_authenticated', False):
            return None
        user = request.user
        if hasattr(user, 'staff_profile') and user.staff_profile:
            return user.staff_profile.farmer
        if hasattr(user, 'profile'):
            return user.profile
        return None

    def _validate_document(self, validated_data):
        if 'document' not in validated_data:
            return
        doc = validated_data['document']
        if doc is None:
            return
        profile = self._farmer_profile()
        if profile and doc.farmer_id != profile.id:
            raise serializers.ValidationError({'document': 'Invalid document.'})

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret['document_type'] = instance.document.category if instance.document else ''
        ret['document_file'] = instance.document.file.name if instance.document and instance.document.file else ''
        # Website-formatted address (address, district, state, pincode)
        addr_parts = []
        if instance.address:
            addr_parts.append(instance.address.strip())
        if instance.district:
            addr_parts.append(str(instance.district).strip())
        if instance.state:
            addr_parts.append(str(instance.state).strip())
        if instance.pincode:
            addr_parts.append(str(instance.pincode).strip())
        ret['formatted_address'] = ', '.join(a for a in addr_parts if a) if addr_parts else ''
        return ret

    def create(self, validated_data):
        self._validate_document(validated_data)
        validated_data['state'] = self._state_name(validated_data.get('state', ''))
        validated_data['district'] = self._district_name(validated_data.get('district', ''))
        return super().create(validated_data)

    def update(self, instance, validated_data):
        self._validate_document(validated_data)
        if 'state' in validated_data:
            validated_data['state'] = self._state_name(validated_data.get('state', ''))
        if 'district' in validated_data:
            validated_data['district'] = self._district_name(validated_data.get('district', ''))
        return super().update(instance, validated_data)

class FarmerOutstandingSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.name', read_only=True, allow_null=True)
    balance_due = serializers.SerializerMethodField()

    class Meta:
        model = FarmerOutstanding
        fields = '__all__'
        read_only_fields = ['farmer', 'created_at']

    def get_balance_due(self, obj):
        return round(float(obj.amount_total) - float(obj.amount_paid), 2)

class CropSellSerializer(serializers.Serializer):
    yield_tons = serializers.DecimalField(max_digits=10, decimal_places=2, required=True, help_text='Quantity to sell in tons')
    price_per_mt = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True, help_text='Rate per metric ton')
    total_sale_value = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True, help_text='Total amount for this sale')
    customer_name = serializers.CharField(required=False, allow_blank=True, help_text='Customer name (must exist in your Customers list)')
    paid_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0, help_text='Amount already paid by the customer')
    batch_index = serializers.IntegerField(required=False, allow_null=True, help_text='Index of the batch to mark as sold')
    unit = serializers.CharField(required=False, allow_blank=True, default='MT', help_text='Unit of measurement (e.g. MT, Kg, Ton)')
    quality = serializers.CharField(required=False, allow_blank=True, default='Grade 1', help_text='Quality grade of the batch')
    payment_method = serializers.CharField(required=False, allow_blank=True, default='Cash', help_text='Payment method (Cash, UPI, Bank Transfer, Credit)')
    payment_status = serializers.CharField(required=False, allow_blank=True, default='Received', help_text='Payment status (Received, Pending)')

class FarmerDocumentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    file_size_display = serializers.SerializerMethodField()

    class Meta:
        from apps.superadmin.models import Document
        model = Document
        fields = ['id', 'title', 'category', 'description', 'file', 'file_url', 'file_size_bytes', 'file_size_display', 'file_type', 'uuid_token', 'created_at', 'updated_at']
        read_only_fields = ['id', 'uuid_token', 'file_size_bytes', 'file_type', 'created_at', 'updated_at']

    def get_file_url(self, obj):
        if obj.file:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.file.url)
            return obj.file.url
        return None

    def get_file_size_display(self, obj):
        size = obj.file_size_bytes or 0
        if size < 1024:
            return f'{size} B'
        elif size < 1024 * 1024:
            return f'{size / 1024:.1f} KB'
        else:
            return f'{size / (1024 * 1024):.1f} MB'


class DocumentCategorySerializer(serializers.ModelSerializer):
    class Meta:
        from apps.superadmin.models import DocumentCategory
        model = DocumentCategory
        fields = ['id', 'document_type', 'name', 'description', 'is_active']
        read_only_fields = ['id']


class FarmDashboardSerializer(serializers.Serializer):
    """Full farm dashboard data — same as what the web Farm Profile modal shows."""
    id = serializers.IntegerField()
    land_name = serializers.CharField()
    total_area = serializers.CharField()
    latitude = serializers.CharField(allow_null=True)
    longitude = serializers.CharField(allow_null=True)
    health_indicator = serializers.CharField()
    crop_count = serializers.IntegerField()
    splits = CropSplitSerializer(many=True)
    total_budget = serializers.FloatField()
    total_consumption_value = serializers.FloatField()
