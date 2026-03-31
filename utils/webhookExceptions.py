"""
webhookExceptions.py

Specialized exceptions for webhook processing control flow.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["RetryableWebhookError"]


class RetryableWebhookError(Exception):
    """
    Raised when webhook processing cannot proceed due to a temporary or
    unresolved dependency and should be retried by the sender.
    """

    pass
