from django.db import IntegrityError, transaction


def get_or_reactivate_master(model, *, name, defaults=None):
    """Return a case-insensitive master-data match and ensure it is active."""
    defaults = defaults or {}
    obj = model.objects.filter(name__iexact=name).first()
    created = False

    if obj is None:
        try:
            with transaction.atomic():
                obj = model.objects.create(name=name, **defaults)
                created = True
        except IntegrityError:
            obj = model.objects.filter(name__iexact=name).first()
            if obj is None:
                raise

    if hasattr(obj, "is_active") and not obj.is_active:
        obj.is_active = True
        obj.save(update_fields=["is_active"])

    return obj, created
