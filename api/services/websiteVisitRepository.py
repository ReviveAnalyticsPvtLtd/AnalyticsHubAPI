"""Bounded PostgreSQL persistence for first website visits."""

import os
from datetime import datetime

import psycopg2
from psycopg2.extras import RealDictCursor


_STATEMENT_TIMEOUT_SQL = "set local statement_timeout = '5000ms'"


def _defaultConnection():
    databaseUrl = os.environ.get("DATABASE_URL")
    if not databaseUrl:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(
        databaseUrl,
        application_name="nubrix-website-visits",
        connect_timeout=5,
    )


class WebsiteVisitRepository:
    def __init__(self, connectionFactory=None):
        self.connectionFactory = connectionFactory or _defaultConnection

    def recordVisit(
        self,
        sessionId: str,
        path: str,
        userAgent: str | None,
        ipAddress: str | None,
    ) -> None:
        connection = self.connectionFactory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(_STATEMENT_TIMEOUT_SQL)
                cursor.execute(
                    """
                    insert into public.page_visits (session_id, path, user_agent, ip_address)
                    values (%s, %s, %s, %s)
                    on conflict (session_id) do nothing
                    """,
                    (sessionId, path, userAgent, ipAddress),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def countDailyVisits(
        self, rangeStart: datetime, rangeEnd: datetime
    ) -> list[dict]:
        connection = self.connectionFactory()
        try:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(_STATEMENT_TIMEOUT_SQL)
                cursor.execute(
                    """
                    select date_trunc('day', created_at at time zone 'UTC') at time zone 'UTC' as day,
                           count(*) as visits
                    from public.page_visits
                    where created_at >= %s and created_at < %s
                    group by 1
                    order by 1
                    """,
                    (rangeStart, rangeEnd),
                )
                rows = [dict(row) for row in cursor.fetchall()]
            connection.commit()
            return rows
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


_websiteVisitRepository: WebsiteVisitRepository | None = None


def getWebsiteVisitRepository() -> WebsiteVisitRepository:
    global _websiteVisitRepository
    if _websiteVisitRepository is None:
        _websiteVisitRepository = WebsiteVisitRepository()
    return _websiteVisitRepository
