"""
renewalLifecycleTask.py

Celery Beat scheduler for renewal reminder emails.

Runs daily and sends T-1 and due-today email reminders for
payment_pending annual renewal invoices. Uses idempotent send-log
keying (invoice + template + date) via SubscriptionLog and Redis
pre-flight locks.

Past-due and suspension transitions are handled by the dedicated
high-frequency pastDueSuspensionTask.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["RenewalLifecycleTask"]


from supabase import create_client
from utils.logger import logger
import datetime
import requests
import json
import os


_REMINDER_LOCK_TTL_SECONDS = 90


def _getSupabaseClient():
    """
    Create and return a Supabase client.

    Returns:
        Client: A Supabase client instance.
    """
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


class RenewalLifecycleTask:
    """
    Daily scheduler for renewal reminder emails on annual prepaid
    renewal invoices.
    """

    def __init__(self):
        self.client = _getSupabaseClient()
        import redis
        self.redisClient = redis.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", 6379)),
            password=os.environ.get("REDIS_PASSWORD", None),
            decode_responses=True,
        )

    def execute(self) -> dict:
        """
        Run the reminder sweep.

        Returns:
            dict: Counts for reminder operations.
        """
        logger.info("Renewal reminder task started")
        reminderResults = self._sweepReminders()
        logger.info(
            f"Renewal reminder task completed — "
            f"sent: {reminderResults['sent']}, "
            f"skipped: {reminderResults['skipped']}, "
            f"errors: {reminderResults['errors']}"
        )
        return {"reminders": reminderResults}

    def _sweepReminders(self) -> dict:
        """
        Send T-1 and due-today reminders for payment_pending invoices.

        Uses SubscriptionLog as a send-log to enforce idempotent
        delivery keyed by (invoice_id, template, date).

        Returns:
            dict: Counts of sent, skipped, and errored reminders.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        today = now.date()
        tomorrow = today + datetime.timedelta(days=1)

        invoices = (
            self.client.table("Invoices")
            .select("id, userId, subscription_id, due_date, total_amount, currency, shortUrl")
            .eq("status", "payment_pending")
            .eq("billing_reason", "renewal")
            .not_.is_("due_date", "null")
            .execute()
            .data
        )

        sent = 0
        skipped = 0
        errors = 0

        for invoice in invoices:
            dueDate = invoice.get("due_date", "")[:10]
            invoiceId = invoice["id"]
            userId = invoice.get("userId", "")
            template = None

            if dueDate == str(tomorrow):
                template = "renewal_reminder_t1"
            elif dueDate == str(today):
                template = "renewal_reminder_due_today"
            else:
                continue

            sendLogKey = f"{invoiceId}:{template}:{today}"
            lockKey = f"renewal-reminder:{sendLogKey}"
            lockAcquired = self.redisClient.set(
                lockKey, "1", nx=True, ex=_REMINDER_LOCK_TTL_SECONDS
            )
            if not lockAcquired:
                skipped += 1
                continue
            existingLog = (
                self.client.table("SubscriptionLog")
                .select("id")
                .eq("userId", userId)
                .eq("eventType", f"email.{template}")
                .eq("status", sendLogKey)
                .limit(1)
                .execute()
                .data
            )
            if existingLog:
                skipped += 1
                continue

            try:
                userRows = (
                    self.client.table("Users")
                    .select("email, fullName")
                    .eq("userId", userId)
                    .limit(1)
                    .execute()
                    .data
                )
                if not userRows:
                    skipped += 1
                    continue

                self._sendReminderEmail(
                    user=userRows[0],
                    invoice=invoice,
                    template=template,
                )

                self.client.table("SubscriptionLog").insert({
                    "userId": userId,
                    "eventType": f"email.{template}",
                    "status": sendLogKey,
                    "metadata": {
                        "invoiceId": invoiceId,
                        "template": template,
                        "sentAt": now.isoformat(),
                    },
                }).execute()
                sent += 1
            except Exception as e:
                logger.error(
                    f"Reminder send failed for invoice {invoiceId}, template {template}: {e}"
                )
                errors += 1

        return {"sent": sent, "skipped": skipped, "errors": errors}

    def _sendReminderEmail(self, user: dict, invoice: dict, template: str) -> None:
        """
        Send a renewal reminder email via the configured edge function.

        Args:
            user: User row (email, fullName).
            invoice: Invoice row (total_amount, currency, shortUrl, due_date).
            template: Template identifier (renewal_reminder_t1 / renewal_reminder_due_today).
        """
        emailUrl = os.environ.get("RENEWAL_REMINDER_EMAIL_URL")
        if not emailUrl:
            logger.info("Reminder email skipped: RENEWAL_REMINDER_EMAIL_URL not configured")
            return

        payload = {
            "email": user.get("email", ""),
            "name": user.get("fullName", ""),
            "template": template,
            "amount": invoice.get("total_amount", 0),
            "currency": invoice.get("currency", "INR"),
            "paymentUrl": invoice.get("shortUrl", ""),
            "dueDate": invoice.get("due_date", ""),
        }

        try:
            requests.post(
                url=emailUrl,
                data=json.dumps(payload),
                headers={"Authorization": f"Bearer {os.environ.get('SUPABASE_KEY', '')}"},
                timeout=10,
            )
        except Exception as e:
            logger.error(f"Reminder email send failed: {e}")
