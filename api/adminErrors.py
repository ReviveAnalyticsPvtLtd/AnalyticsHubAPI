class AdminApiError(Exception):
    def __init__(self, statusCode: int, message: str, errors: dict[str, str] | None = None):
        self.statusCode = statusCode
        self.message = message
        self.errors = errors
        super().__init__(message)


def requestValidationErrors(rawErrors: list[dict]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for error in rawErrors:
        location = [str(part) for part in error.get("loc", ()) if part not in ("body",)]
        field = ".".join(location) or "body"
        mapped.setdefault(field, error.get("msg", "Invalid value"))
    return mapped
