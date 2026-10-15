"""Admin-key check shared by the write guard and paid on-demand endpoints.

The API is internet-facing and otherwise unauthenticated. Fail-closed: when
ADMIN_API_KEY is unset nobody is an admin, so a public deploy is safe by default.
"""
import os
import secrets

from fastapi import Request

ADMIN_HEADER = "x-admin-key"


def is_admin(request: Request) -> bool:
    expected = os.environ.get("ADMIN_API_KEY", "")
    provided = request.headers.get(ADMIN_HEADER, "")
    return bool(expected) and secrets.compare_digest(provided, expected)
