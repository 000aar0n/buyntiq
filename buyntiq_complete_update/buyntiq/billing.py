"""Temporary unrestricted-access shim.

Payments and subscriptions are intentionally disabled for now.  This module keeps
existing research/portfolio call sites stable while Buyntiq has no checkout,
Stripe API calls, subscription checks, paywalls, or application daily quotas.
"""
from contextlib import contextmanager
from dataclasses import dataclass


class BillingError(ValueError):
    """Retained for compatibility with existing UI error handling."""


@dataclass(frozen=True)
class Access:
    pro: bool = True
    status: str = "payments-disabled"
    expires_at: float | None = None
    customer: str | None = None
    message: str | None = None


def access(force=False):
    return Access()


def sync_access():
    return Access()


def limit_for(feature, pro=True):
    return None


def require_pro(feature):
    return Access()


@contextmanager
def action(feature):
    yield Access()


def paywall(feature):
    return None


class _OpenStore:
    def reserve(self, *args, **kwargs):
        return "unlimited"

    def refund(self, *args, **kwargs):
        return None

    def usage(self, *args, **kwargs):
        return {}


def _store():
    return _OpenStore()
