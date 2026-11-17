"""Shared validation for address forms."""
import re


def valid_pincode(value):
    return bool(re.fullmatch(r"[0-9]{6}", (value or "").strip()))


def pincode_error(request):
    from django.http import JsonResponse
    if not valid_pincode(request.POST.get("pincode")):
        message = "Pincode is required and must contain exactly 6 digits."
        return JsonResponse({"success": False, "status": "error", "error": message, "message": message}, status=400)
    return None
