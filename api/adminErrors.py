class AdminApiError(Exception):
    def __init__(self, statusCode: int, message: str, errors: dict[str, str] | None = None):
        self.statusCode = statusCode
        self.message = message
        self.errors = errors
        super().__init__(message)


_LOCATION_PREFIXES = ("body", "query", "path")


def requestValidationErrors(rawErrors: list[dict]) -> dict[str, str]:
    """
    Flatten FastAPI validation errors into a field-to-message map.

    The request-source prefix is stripped so a query parameter reports as
    "limit" rather than "query.limit", matching how body fields are already
    reported. Clients then handle one key shape regardless of where the
    offending value came from.
    """
    mapped: dict[str, str] = {}
    for error in rawErrors:
        location = [
            str(part) for part in error.get("loc", ())
            if part not in _LOCATION_PREFIXES
        ]
        field = ".".join(location) or "body"
        mapped.setdefault(field, error.get("msg", "Invalid value"))
    return mapped
