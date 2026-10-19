"""Domain errors. Each carries a short machine-readable ``code``; messages are safe to show to a model."""

from __future__ import annotations


class CmmsError(Exception):
    code = "error"


class NotFound(CmmsError):
    code = "not_found"


class Invalid(CmmsError):
    code = "invalid"


class Forbidden(CmmsError):
    code = "forbidden"


class Conflict(CmmsError):
    code = "conflict"
