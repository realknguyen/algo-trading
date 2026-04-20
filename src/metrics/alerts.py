"""Alerting system for the algo-trading platform.

This module provides:
- AlertManager for threshold-based alerts
- Configurable alert rules (latency spikes, error rates, downtime)
- Alert channels: log, webhook, email (placeholder)
"""

import asyncio
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Any, Callable, Set, Union
from datetime import datetime, timedelta

import httpx
import structlog

from src.metrics.collector import MetricsCollector
from src.metrics.dashboard import DashboardData, ExchangeHealth, HealthStatus


logger = structlog.get_logger(__name__)


class AlertSeverity(str, Enum):
    """Alert severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class AlertStatus(str, Enum):
    """Alert status enumeration."""

    FIRING = "firing"
    RESOLVED = "resolved"
    PENDING = "pending"
    SILENCED = "silenced"


class AlertRuleType(str, Enum):
    """Alert rule type enumeration."""

    THRESHOLD = "threshold"
    RATE_OF_CHANGE = "rate_of_change"
    ABSENCE = "absence"
    ANOMALY = "anomaly"


@dataclass
class AlertRule:
    """Alert rule definition."""

    id: str
    name: str
    description: str
    rule_type: AlertRuleType
    metric: str
    condition: str  # '>', '<', '>=', '<=', '==', '!='
    threshold: float
    duration: float  # Duration condition must be true before alerting (seconds)
    severity: AlertSeverity
    labels: Dict[str, str] = field(default_factory=dict)
    annotations: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    cooldown: float = 300.0  # Cooldown between alerts (seconds)
    auto_resolve: bool = True
    resolve_threshold: Optional[float] = None  # Optional different threshold for resolution


@dataclass
class Alert:
    """Active alert instance."""

    id: str
    rule_id: str
    rule_name: str
    severity: AlertSeverity
    status: AlertStatus
    metric: str
    current_value: float
    threshold: float
    condition: str
    labels: Dict[str, str]
    annotations: Dict[str, str]
    started_at: float
    updated_at: float
    resolved_at: Optional[float] = None
    message: str = ""


@dataclass
class AlertHistory:
    """Alert history entry."""

    alert_id: str
    rule_id: str
    rule_name: str
    severity: AlertSeverity
    status: AlertStatus
    started_at: float
    resolved_at: Optional[float]
    duration_seconds: float
    message: str


class AlertChannel(ABC):
    """Base class for alert channels."""

    @abstractmethod
    async def send(self, alert: Alert) -> bool:
        """Send an alert through this channel."""
        pass

    @abstractmethod
    async def send_resolution(self, alert: Alert) -> bool:
        """Send an alert resolution through this channel."""
        pass


class LogAlertChannel(AlertChannel):
    """Alert channel that sends alerts to logs."""

    def __init__(self, logger_name: str = "alerts"):
        self.logger = structlog.get_logger(logger_name)

    async def send(self, alert: Alert) -> bool:
        """Send alert to log."""
        log_method = {
            AlertSeverity.CRITICAL: self.logger.critical,
            AlertSeverity.HIGH: self.logger.error,
            AlertSeverity.MEDIUM: self.logger.warning,
            AlertSeverity.LOW: self.logger.info,
            AlertSeverity.INFO: self.logger.info,
        }.get(alert.severity, self.logger.warning)

        log_method(
            "alert_firing",
            alert_id=alert.id,
            rule=alert.rule_name,
            severity=alert.severity.value,
            metric=alert.metric,
            value=alert.current_value,
            threshold=alert.threshold,
            message=alert.message,
            labels=alert.labels,
        )
        return True

    async def send_resolution(self, alert: Alert) -> bool:
        """Send alert resolution to log."""
        self.logger.info(
            "alert_resolved",
            alert_id=alert.id,
            rule=alert.rule_name,
            severity=alert.severity.value,
            metric=alert.metric,
            final_value=alert.current_value,
            duration_seconds=time.time() - alert.started_at,
            labels=alert.labels,
        )
        return True


class WebhookAlertChannel(AlertChannel):
    """Alert channel that sends alerts via HTTP webhooks."""

    def __init__(
        self,
        webhook_url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
        retry_count: int = 3,
    ):
        self.webhook_url = webhook_url
        self.headers = headers or {"Content-Type": "application/json"}
        self.timeout = timeout
        self.retry_count = retry_count
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        """Initialize HTTP client."""
        self._client = httpx.AsyncClient(timeout=self.timeout)

    async def stop(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()

    async def send(self, alert: Alert) -> bool:
        """Send alert via webhook."""
        if not self._client:
            await self.start()

        payload = {
            "event": "alert_firing",
            "alert_id": alert.id,
            "rule_id": alert.rule_id,
            "rule_name": alert.rule_name,
            "severity": alert.severity.value,
            "status": alert.status.value,
            "metric": alert.metric,
            "current_value": alert.current_value,
            "threshold": alert.threshold,
            "condition": alert.condition,
            "labels": alert.labels,
            "annotations": alert.annotations,
            "message": alert.message,
            "started_at": datetime.utcfromtimestamp(alert.started_at).isoformat(),
            "timestamp": datetime.utcnow().isoformat(),
        }

        return await self._send_with_retry(payload)

    async def send_resolution(self, alert: Alert) -> bool:
        """Send alert resolution via webhook."""
        payload = {
            "event": "alert_resolved",
            "alert_id": alert.id,
            "rule_id": alert.rule_id,
            "rule_name": alert.rule_name,
            "severity": alert.severity.value,
            "status": AlertStatus.RESOLVED.value,
            "metric": alert.metric,
            "final_value": alert.current_value,
            "labels": alert.labels,
            "started_at": datetime.utcfromtimestamp(alert.started_at).isoformat(),
            "resolved_at": datetime.utcfromtimestamp(alert.resolved_at or time.time()).isoformat(),
            "duration_seconds": (alert.resolved_at or time.time()) - alert.started_at,
            "timestamp": datetime.utcnow().isoformat(),
        }

        return await self._send_with_retry(payload)

    async def _send_with_retry(self, payload: Dict[str, Any]) -> bool:
        """Send webhook with retry logic."""
        for attempt in range(self.retry_count):
            try:
                response = await self._client.post(
                    self.webhook_url, json=payload, headers=self.headers
                )
                response.raise_for_status()
                return True
            except Exception as e:
                logger.error(f"Webhook send failed (attempt {attempt + 1}): {e}")
                if attempt < self.retry_count - 1:
                    await asyncio.sleep(2**attempt)

        return False


class EmailAlertChannel(AlertChannel):
    """Alert channel that sends alerts via email (placeholder implementation).

    NOTE: This is a placeholder. In production, integrate with:
    - SMTP server
    - SendGrid API
    - AWS SES
    - Other email service
    """

    def __init__(
        self,
        smtp_host: str = "localhost",
        smtp_port: int = 587,
        username: Optional[str] = None,
        password: Optional[str] = None,
        from_address: str = "alerts@trading.local",
        to_addresses: Optional[List[str]] = None,
        use_tls: bool = True,
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.from_address = from_address
        self.to_addresses = to_addresses or []
        self.use_tls = use_tls

    async def send(self, alert: Alert) -> bool:
        """Send alert via email (placeholder)."""
        # Placeholder - implement with actual email library
        logger.info(
            "Email alert would be sent",
            to=self.to_addresses,
            subject=f"[{alert.severity.value.upper()}] Alert: {alert.rule_name}",
            alert_id=alert.id,
        )
        # TODO: Implement actual email sending
        return True

    async def send_resolution(self, alert: Alert) -> bool:
        """Send alert resolution via email (placeholder)."""
        logger.info(
            "Email resolution would be sent",
            to=self.to_addresses,
            subject=f"[RESOLVED] Alert: {alert.rule_name}",
            alert_id=alert.id,
        )
        # TODO: Implement actual email sending
        return True


class PagerDutyAlertChannel(AlertChannel):
    """Alert channel that sends alerts to PagerDuty."""

    def __init__(self, routing_key: str, severity_mapping: Optional[Dict[str, str]] = None):
        self.routing_key = routing_key
        self.severity_mapping = severity_mapping or {
            AlertSeverity.CRITICAL: "critical",
            AlertSeverity.HIGH: "error",
            AlertSeverity.MEDIUM: "warning",
            AlertSeverity.LOW: "info",
            AlertSeverity.INFO: "info",
        }
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        """Initialize HTTP client."""
        self._client = httpx.AsyncClient(timeout=10.0)

    async def stop(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()

    async def send(self, alert: Alert) -> bool:
        """Send alert to PagerDuty."""
        if not self._client:
            await self.start()

        payload = {
            "routing_key": self.routing_key,
            "event_action": "trigger",
            "dedup_key": alert.id,
            "payload": {
                "summary": alert.message,
                "severity": self.severity_mapping.get(alert.severity, "warning"),
                "source": alert.labels.get("exchange", "trading-system"),
                "custom_details": {
                    "metric": alert.metric,
                    "current_value": alert.current_value,
                    "threshold": alert.threshold,
                    "labels": alert.labels,
                },
            },
        }

        try:
            response = await self._client.post(
                "https://events.pagerduty.com/v2/enqueue", json=payload
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"PagerDuty alert failed: {e}")
            return False

    async def send_resolution(self, alert: Alert) -> bool:
        """Send alert resolution to PagerDuty."""
        if not self._client:
            await self.start()

        payload = {
            "routing_key": self.routing_key,
            "event_action": "resolve",
            "dedup_key": alert.id,
        }

        try:
            response = await self._client.post(
                "https://events.pagerduty.com/v2/enqueue", json=payload
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"PagerDuty resolution failed: {e}")
            return False


class AlertManager:
    """Alert manager for threshold-based alerting.

    Features:
    - Configurable alert rules
    - Multiple notification channels
    - Alert grouping and deduplication
    - Alert history and persistence
    - Silencing and inhibition
    """

    def __init__(
        self,
        collector: MetricsCollector,
        dashboard: Optional[DashboardData] = None,
        evaluation_interval: float = 10.0,
        max_history: int = 1000,
    ):
        """Initialize alert manager.

        Args:
            collector: Metrics collector instance
            dashboard: Optional dashboard data instance
            evaluation_interval: How often to evaluate rules (seconds)
            max_history: Maximum alert history entries
        """
        self.collector = collector
        self.dashboard = dashboard
        self.evaluation_interval = evaluation_interval
        self.max_history = max_history

        # Rules and alerts
        self.rules: Dict[str, AlertRule] = {}
        self.active_alerts: Dict[str, Alert] = {}
        self.alert_history: List[AlertHistory] = []

        # Channels
        self.channels: Dict[str, AlertChannel] = {}

        # Alert state tracking
        self._pending_alerts: Dict[str, Dict[str, Any]] = {}  # rule_id -> {started_at, value}
        self._last_alert_time: Dict[str, float] = {}  # rule_id -> timestamp
        self._silenced_rules: Dict[str, float] = {}  # rule_id -> until_timestamp
        self._silenced_alerts: Dict[str, float] = {}  # alert_id -> until_timestamp

        # Background task
        self._evaluation_task: Optional[asyncio.Task] = None
        self._running = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start alert manager."""
        if self._running:
            return

        self._running = True

        # Start channels
        for channel in self.channels.values():
            if hasattr(channel, "start"):
                await channel.start()

        # Start evaluation loop
        self._evaluation_task = asyncio.create_task(self._evaluation_loop())

        logger.info("Alert manager started")

    async def stop(self) -> None:
        """Stop alert manager."""
        self._running = False

        if self._evaluation_task:
            self._evaluation_task.cancel()
            try:
                await self._evaluation_task
            except asyncio.CancelledError:
                pass

        # Stop channels
        for channel in self.channels.values():
            if hasattr(channel, "stop"):
                await channel.stop()

        logger.info("Alert manager stopped")

    def add_rule(self, rule: AlertRule) -> None:
        """Add an alert rule."""
        self.rules[rule.id] = rule
        logger.info(f"Added alert rule: {rule.name} ({rule.id})")

    def remove_rule(self, rule_id: str) -> None:
        """Remove an alert rule."""
        if rule_id in self.rules:
            del self.rules[rule_id]
            logger.info(f"Removed alert rule: {rule_id}")

    def add_channel(self, name: str, channel: AlertChannel) -> None:
        """Add an alert channel."""
        self.channels[name] = channel
        logger.info(f"Added alert channel: {name}")

    def remove_channel(self, name: str) -> None:
        """Remove an alert channel."""
        if name in self.channels:
            del self.channels[name]
            logger.info(f"Removed alert channel: {name}")

    async def silence_rule(self, rule_id: str, duration_seconds: float) -> None:
        """Silence a rule for a duration."""
        self._silenced_rules[rule_id] = time.time() + duration_seconds
        logger.info(f"Silenced rule {rule_id} for {duration_seconds}s")

    async def silence_alert(self, alert_id: str, duration_seconds: float) -> None:
        """Silence a specific alert for a duration."""
        self._silenced_alerts[alert_id] = time.time() + duration_seconds

        # Update alert status
        if alert_id in self.active_alerts:
            self.active_alerts[alert_id].status = AlertStatus.SILENCED

        logger.info(f"Silenced alert {alert_id} for {duration_seconds}s")

    def get_active_alerts(self) -> List[Dict[str, Any]]:
        """Get list of active alerts."""
        return [self._alert_to_dict(a) for a in self.active_alerts.values()]

    def get_alert_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get alert history."""
        return [self._history_to_dict(h) for h in self.alert_history[-limit:]]

    async def evaluate_rules_now(self) -> None:
        """Manually trigger rule evaluation."""
        await self._evaluate_rules()

    async def _evaluation_loop(self) -> None:
        """Background task to evaluate alert rules."""
        while self._running:
            try:
                await self._evaluate_rules()
                await asyncio.sleep(self.evaluation_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in alert evaluation loop: {e}")
                await asyncio.sleep(self.evaluation_interval)

    async def _evaluate_rules(self) -> None:
        """Evaluate all alert rules."""
        metrics = await self.collector.get_all_metrics()
        now = time.time()

        # Clean up expired silences
        self._cleanup_silences(now)

        for rule in self.rules.values():
            if not rule.enabled:
                continue

            # Check if rule is silenced
            if rule.id in self._silenced_rules:
                continue

            # Get current value
            value = self._get_metric_value(rule.metric, metrics)
            if value is None:
                continue

            # Evaluate condition
            is_firing = self._evaluate_condition(value, rule.condition, rule.threshold)

            if is_firing:
                await self._handle_firing(rule, value, now)
            else:
                await self._handle_resolution(rule, value, now)

    def _get_metric_value(self, metric: str, metrics: Dict[str, Any]) -> Optional[float]:
        """Get metric value from metrics dictionary."""
        if metric in metrics:
            value = metrics[metric]
            if isinstance(value, (int, float)):
                return float(value)
            elif isinstance(value, dict) and value:
                # Return sum for counter metrics
                return sum(v for v in value.values() if isinstance(v, (int, float)))

        # Check for nested metrics (e.g., exchange-specific)
        for key, value in metrics.items():
            if metric in key:
                if isinstance(value, (int, float)):
                    return float(value)

        return None

    def _evaluate_condition(self, value: float, condition: str, threshold: float) -> bool:
        """Evaluate alert condition."""
        if condition == ">":
            return value > threshold
        elif condition == ">=":
            return value >= threshold
        elif condition == "<":
            return value < threshold
        elif condition == "<=":
            return value <= threshold
        elif condition == "==":
            return value == threshold
        elif condition == "!=":
            return value != threshold
        return False

    async def _handle_firing(self, rule: AlertRule, value: float, now: float) -> None:
        """Handle a firing alert condition."""
        alert_id = f"{rule.id}:{rule.metric}"

        # Check if already active
        if alert_id in self.active_alerts:
            # Update existing alert
            self.active_alerts[alert_id].current_value = value
            self.active_alerts[alert_id].updated_at = now
            return

        # Check cooldown
        if rule.id in self._last_alert_time:
            if now - self._last_alert_time[rule.id] < rule.cooldown:
                return

        # Check pending duration
        if rule.id not in self._pending_alerts:
            self._pending_alerts[rule.id] = {"started_at": now, "value": value}
            return

        pending = self._pending_alerts[rule.id]
        if now - pending["started_at"] < rule.duration:
            return

        # Create alert
        alert = Alert(
            id=alert_id,
            rule_id=rule.id,
            rule_name=rule.name,
            severity=rule.severity,
            status=AlertStatus.FIRING,
            metric=rule.metric,
            current_value=value,
            threshold=rule.threshold,
            condition=rule.condition,
            labels=rule.labels.copy(),
            annotations=rule.annotations.copy(),
            started_at=now,
            updated_at=now,
            message=self._format_alert_message(rule, value),
        )

        # Check if silenced
        if alert_id in self._silenced_alerts:
            alert.status = AlertStatus.SILENCED

        async with self._lock:
            self.active_alerts[alert_id] = alert
            self._last_alert_time[rule.id] = now
            del self._pending_alerts[rule.id]

        # Send notifications
        if alert.status != AlertStatus.SILENCED:
            await self._send_alert(alert)

        logger.warning(f"Alert firing: {alert.rule_name} ({alert.message})")

    async def _handle_resolution(self, rule: AlertRule, value: float, now: float) -> None:
        """Handle alert resolution."""
        alert_id = f"{rule.id}:{rule.metric}"

        # Clear pending if exists
        if rule.id in self._pending_alerts:
            del self._pending_alerts[rule.id]

        # Check if there's an active alert
        if alert_id not in self.active_alerts:
            return

        # Check auto-resolve threshold if specified
        if rule.resolve_threshold is not None:
            if not self._evaluate_condition(value, rule.condition, rule.resolve_threshold):
                return

        alert = self.active_alerts[alert_id]
        alert.status = AlertStatus.RESOLVED
        alert.resolved_at = now
        alert.current_value = value

        # Move to history
        async with self._lock:
            self._add_to_history(alert)
            del self.active_alerts[alert_id]

        # Send resolution notifications
        await self._send_resolution(alert)

        logger.info(f"Alert resolved: {alert.rule_name}")

    async def _send_alert(self, alert: Alert) -> None:
        """Send alert to all channels."""
        for channel in self.channels.values():
            try:
                await channel.send(alert)
            except Exception as e:
                logger.error(f"Failed to send alert to channel: {e}")

    async def _send_resolution(self, alert: Alert) -> None:
        """Send alert resolution to all channels."""
        for channel in self.channels.values():
            try:
                await channel.send_resolution(alert)
            except Exception as e:
                logger.error(f"Failed to send resolution to channel: {e}")

    def _cleanup_silences(self, now: float) -> None:
        """Clean up expired silences."""
        expired_rules = [rid for rid, until in self._silenced_rules.items() if now > until]
        for rid in expired_rules:
            del self._silenced_rules[rid]

        expired_alerts = [aid for aid, until in self._silenced_alerts.items() if now > until]
        for aid in expired_alerts:
            del self._silenced_alerts[aid]
            # Restore alert status if still active
            if aid in self.active_alerts:
                self.active_alerts[aid].status = AlertStatus.FIRING

    def _add_to_history(self, alert: Alert) -> None:
        """Add alert to history."""
        history_entry = AlertHistory(
            alert_id=alert.id,
            rule_id=alert.rule_id,
            rule_name=alert.rule_name,
            severity=alert.severity,
            status=alert.status,
            started_at=alert.started_at,
            resolved_at=alert.resolved_at,
            duration_seconds=(alert.resolved_at or time.time()) - alert.started_at,
            message=alert.message,
        )

        self.alert_history.append(history_entry)

        # Trim history
        if len(self.alert_history) > self.max_history:
            self.alert_history = self.alert_history[-self.max_history :]

    def _format_alert_message(self, rule: AlertRule, value: float) -> str:
        """Format alert message."""
        template = rule.annotations.get(
            "summary", f"{rule.name}: {rule.metric} is {rule.condition} {rule.threshold}"
        )
        return template.format(
            value=value, threshold=rule.threshold, metric=rule.metric, condition=rule.condition
        )

    def _alert_to_dict(self, alert: Alert) -> Dict[str, Any]:
        """Convert alert to dictionary."""
        return {
            "id": alert.id,
            "rule_id": alert.rule_id,
            "rule_name": alert.rule_name,
            "severity": alert.severity.value,
            "status": alert.status.value,
            "metric": alert.metric,
            "current_value": alert.current_value,
            "threshold": alert.threshold,
            "condition": alert.condition,
            "labels": alert.labels,
            "annotations": alert.annotations,
            "started_at": alert.started_at,
            "updated_at": alert.updated_at,
            "message": alert.message,
        }

    def _history_to_dict(self, history: AlertHistory) -> Dict[str, Any]:
        """Convert history entry to dictionary."""
        return {
            "alert_id": history.alert_id,
            "rule_id": history.rule_id,
            "rule_name": history.rule_name,
            "severity": history.severity.value,
            "status": history.status.value,
            "started_at": history.started_at,
            "resolved_at": history.resolved_at,
            "duration_seconds": history.duration_seconds,
            "message": history.message,
        }


def create_default_rules() -> List[AlertRule]:
    """Create default alert rules for trading system."""
    return [
        # High latency alert
        AlertRule(
            id="high_latency",
            name="High API Latency",
            description="API response time is too high",
            rule_type=AlertRuleType.THRESHOLD,
            metric="http_request_latency",
            condition=">",
            threshold=2.0,  # 2 seconds
            duration=60.0,
            severity=AlertSeverity.HIGH,
            labels={"category": "performance"},
            annotations={
                "summary": "API latency is high: {value}s (threshold: {threshold}s)",
                "runbook": "Check exchange API status and network connectivity",
            },
        ),
        # High error rate alert
        AlertRule(
            id="high_error_rate",
            name="High Error Rate",
            description="Too many API errors",
            rule_type=AlertRuleType.THRESHOLD,
            metric="http_errors",
            condition=">",
            threshold=10.0,
            duration=300.0,
            severity=AlertSeverity.CRITICAL,
            labels={"category": "reliability"},
            annotations={
                "summary": "High error rate detected",
                "runbook": "Check API credentials and rate limits",
            },
        ),
        # Rate limit alert
        AlertRule(
            id="rate_limit",
            name="Rate Limit Hit",
            description="Rate limit has been exceeded",
            rule_type=AlertRuleType.THRESHOLD,
            metric="rate_limit_hits",
            condition=">",
            threshold=0.0,
            duration=0.0,  # Immediate
            severity=AlertSeverity.MEDIUM,
            labels={"category": "rate_limiting"},
        ),
        # Low fill rate alert
        AlertRule(
            id="low_fill_rate",
            name="Low Order Fill Rate",
            description="Order fill rate is below threshold",
            rule_type=AlertRuleType.THRESHOLD,
            metric="fill_rate",
            condition="<",
            threshold=0.5,  # 50%
            duration=300.0,
            severity=AlertSeverity.HIGH,
            labels={"category": "trading"},
        ),
        # WebSocket disconnection alert
        AlertRule(
            id="ws_errors",
            name="WebSocket Errors",
            description="WebSocket connection errors detected",
            rule_type=AlertRuleType.THRESHOLD,
            metric="ws_errors",
            condition=">",
            threshold=5.0,
            duration=60.0,
            severity=AlertSeverity.MEDIUM,
            labels={"category": "connectivity"},
        ),
        # Connection failure alert
        AlertRule(
            id="connection_failures",
            name="Connection Failures",
            description="Multiple connection failures detected",
            rule_type=AlertRuleType.THRESHOLD,
            metric="connection_attempts",
            condition=">",
            threshold=3.0,
            duration=300.0,
            severity=AlertSeverity.HIGH,
            labels={"category": "connectivity"},
        ),
        # High order placement latency
        AlertRule(
            id="high_order_latency",
            name="High Order Placement Latency",
            description="Order placement is taking too long",
            rule_type=AlertRuleType.THRESHOLD,
            metric="order_placement_latency",
            condition=">",
            threshold=1.0,  # 1 second
            duration=60.0,
            severity=AlertSeverity.HIGH,
            labels={"category": "trading"},
        ),
    ]


# Global alert manager instance
alert_manager: Optional[AlertManager] = None


def init_alert_manager(
    collector: MetricsCollector, dashboard: Optional[DashboardData] = None, **kwargs
) -> AlertManager:
    """Initialize global alert manager with default rules."""
    global alert_manager
    alert_manager = AlertManager(collector, dashboard, **kwargs)

    # Add default rules
    for rule in create_default_rules():
        alert_manager.add_rule(rule)

    # Add default log channel
    alert_manager.add_channel("log", LogAlertChannel())

    return alert_manager


def get_alert_manager() -> Optional[AlertManager]:
    """Get global alert manager."""
    return alert_manager
