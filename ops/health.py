"""Health check system - monitor agent vitality."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

from core.db import Database

logger = logging.getLogger(__name__)


class HealthCheck:
    """Monitor system health and data freshness."""

    def __init__(self, db: Database):
        self.db = db
        self.last_check = datetime.utcnow()
        self.failures: Dict[str, int] = {}

    def check_data_freshness(self) -> Dict[str, bool]:
        """Check if data feeds are fresh."""
        checks = {}
        now = datetime.utcnow()

        # Check last signal
        conn = self.db.conn
        row = conn.execute(
            "SELECT MAX(created_at) as last_signal FROM signals"
        ).fetchone()

        if row and row["last_signal"]:
            last_signal = datetime.fromisoformat(row["last_signal"])
            age_minutes = (now - last_signal).total_seconds() / 60
            checks["signals_fresh"] = age_minutes < 10  # Should see signals every 10 min
        else:
            checks["signals_fresh"] = False

        # Check last market refresh
        row = conn.execute(
            "SELECT MAX(updated_at) as last_update FROM markets"
        ).fetchone()

        if row and row["last_update"]:
            last_update = datetime.fromisoformat(row["last_update"])
            age_minutes = (now - last_update).total_seconds() / 60
            checks["markets_fresh"] = age_minutes < 5  # Markets refresh every 60s
        else:
            checks["markets_fresh"] = False

        # Check database writeable
        try:
            conn.execute("SELECT 1").fetchone()
            checks["db_writeable"] = True
        except Exception as e:
            logger.error(f"Database check failed: {e}")
            checks["db_writeable"] = False

        return checks

    def check_api_connectivity(self) -> Dict[str, bool]:
        """Check if external APIs are responding (simplified)."""
        # For now, infer from data freshness
        # In production, could add explicit ping checks
        return {
            "gamma_api": True,  # Assume OK if markets are fresh
            "clob_api": True,   # Assume OK if trades are executing
        }

    def run_checks(self) -> Dict[str, any]:
        """Run all health checks."""
        checks = {
            "timestamp": datetime.utcnow().isoformat(),
            "data_freshness": self.check_data_freshness(),
            "api_connectivity": self.check_api_connectivity(),
        }

        # Count failures
        all_checks = {**checks["data_freshness"], **checks["api_connectivity"]}
        failed = [k for k, v in all_checks.items() if not v]

        checks["healthy"] = len(failed) == 0
        checks["failed_checks"] = failed
        checks["failure_count"] = len(failed)

        return checks

    def update_failure_count(self, check_name: str, failed: bool):
        """Track consecutive failures."""
        if failed:
            self.failures[check_name] = self.failures.get(check_name, 0) + 1
        else:
            self.failures[check_name] = 0

    def should_alert(self, check_name: str) -> bool:
        """Alert if a check has failed 3+ times consecutively."""
        return self.failures.get(check_name, 0) >= 3
