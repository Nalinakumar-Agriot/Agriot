class DedupeOriginMiddleware:
    """Collapse 'Origin: a,a' (duplicate header added by the proxy) into 'a'."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        origin = request.META.get("HTTP_ORIGIN")
        if origin and "," in origin:
            request.META["HTTP_ORIGIN"] = origin.split(",")[0].strip()
        return self.get_response(request)
