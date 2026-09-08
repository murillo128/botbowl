"""Errors at the local web/host boundary, independent of Flask."""


class WebError(Exception):
    def __init__(self, message, status=400, code="invalid_input"):
        super().__init__(message)
        self.status = status
        self.code = code


class NotFound(WebError):
    def __init__(self, message="Resource not found."):
        super().__init__(message, 404, "not_found")


class Conflict(WebError):
    def __init__(self, message):
        super().__init__(message, 409, "conflict")


class StorageError(WebError):
    def __init__(self):
        super().__init__("Local storage could not be read or written.", 500, "storage_error")
