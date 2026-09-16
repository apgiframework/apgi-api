"""
Alerting System

Monitors critical errors and triggers alerts through configured notification channels.
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, cast

import httpx

from app.middleware.logging import StructuredLogger

logger = StructuredLogger(__name__)


class AlertSeverity(Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class Alert:
    """
    Represents an alert to be sent.
    """

    title: str
    message: str
    severity: AlertSeverity
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert alert to dictionary."""
        return {
            "title": self.title,
            "message": self.message,
            "severity": self.severity.value,
            "timestamp": self.timestamp.isoformat() + "Z",
            "metadata": self.metadata,
        }


class NotificationChannel(ABC):
    """
    Abstract base class for notification channels.
    """

    @abstractmethod
    async def send_alert(self, alert: Alert) -> bool:
        """
        Send alert through this channel.

        Args:
            alert: Alert to send

        Returns:
            True if alert was sent successfully
        """
        pass


class WebhookNotificationChannel(NotificationChannel):
    """
    Sends alerts via HTTP webhook.
    """

    def __init__(self, webhook_url: str, timeout: float = 10.0):
        """
        Initialize webhook notification channel.

        Args:
            webhook_url: URL to POST alerts to
            timeout: Request timeout in seconds
        """
        self.webhook_url = webhook_url
        self.timeout = timeout

    async def send_alert(self, alert: Alert) -> bool:
        """
        Send alert via webhook POST request.

        Args:
            alert: Alert to send

        Returns:
            True if webhook call succeeded
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.webhook_url, json=alert.to_dict(), timeout=self.timeout
                )

                if response.status_code < 300:
                    logger.info(
                        "Alert sent via webhook",
                        webhook_url=self.webhook_url,
                        alert_title=alert.title,
                        severity=alert.severity.value,
                    )
                    return True
                else:
                    logger.warning(
                        "Webhook returned non-success status",
                        webhook_url=self.webhook_url,
                        status_code=response.status_code,
                        alert_title=alert.title,
                    )
                    return False

        except Exception as e:
            logger.error(
                "Failed to send alert via webhook",
                webhook_url=self.webhook_url,
                alert_title=alert.title,
                error=str(e),
            )
            return False


class SlackNotificationChannel(NotificationChannel):
    """
    Sends alerts to Slack channels.
    """

    def __init__(
        self, webhook_url: str, channel: Optional[str] = None, username: str = "APGI Alert Bot"
    ):
        """
        Initialize Slack notification channel.

        Args:
            webhook_url: Slack webhook URL
            channel: Slack channel (optional, can be set in webhook)
            username: Bot username
        """
        self.webhook_url = webhook_url
        self.channel = channel
        self.username = username

    async def send_alert(self, alert: Alert) -> bool:
        """
        Send alert to Slack.

        Args:
            alert: Alert to send

        Returns:
            True if alert was sent successfully
        """
        try:
            # Format alert for Slack
            slack_payload = {
                "username": self.username,
                "text": f"*{alert.title}*\n{alert.message}",
                "attachments": [
                    {
                        "color": self._severity_to_color(alert.severity),
                        "fields": [
                            {"title": "Severity", "value": alert.severity.value, "short": True},
                            {
                                "title": "Timestamp",
                                "value": alert.timestamp.isoformat(),
                                "short": True,
                            },
                        ],
                    }
                ],
            }

            if self.channel:
                slack_payload["channel"] = self.channel

            # Add metadata as fields
            if alert.metadata:
                metadata_fields = [
                    {"title": key, "value": str(value), "short": True}
                    for key, value in alert.metadata.items()
                ]
                attachments = slack_payload.get("attachments")
                if (
                    attachments and isinstance(attachments, list) and len(attachments) > 0
                ):  # pragma: no branch
                    attachments[0]["fields"].extend(metadata_fields)

            async with httpx.AsyncClient() as client:
                response = await client.post(self.webhook_url, json=slack_payload, timeout=10.0)

                if response.status_code == 200:
                    logger.info(
                        "Alert sent to Slack",
                        alert_title=alert.title,
                        severity=alert.severity.value,
                    )
                    return True
                else:
                    logger.warning(
                        "Slack webhook returned non-success status",
                        status_code=response.status_code,
                        alert_title=alert.title,
                    )
                    return False

        except Exception as e:
            logger.error(
                "Failed to send alert to Slack",
                alert_title=alert.title,
                error=str(e),
            )
            return False

    def _severity_to_color(self, severity: AlertSeverity) -> str:
        """Convert alert severity to Slack color."""
        color_map = {
            AlertSeverity.INFO: "good",
            AlertSeverity.WARNING: "warning",
            AlertSeverity.ERROR: "danger",
            AlertSeverity.CRITICAL: "#ff0000",
        }
        return color_map.get(severity, "warning")


class EmailNotificationChannel(NotificationChannel):
    """
    Sends alerts via email (requires SMTP configuration).
    """

    def __init__(
        self,
        smtp_server: str,
        smtp_port: int = 587,
        smtp_username: str = "",
        smtp_password: str = "",  # nosec: B107 - empty default for optional auth
        from_email: str = "alerts@apgi-api.com",
        to_emails: Optional[List[str]] = None,
    ):
        """
        Initialize email notification channel.

        Args:
            smtp_server: SMTP server hostname
            smtp_port: SMTP server port
            smtp_username: SMTP username
            smtp_password: SMTP password
            from_email: From email address
            to_emails: List of recipient email addresses
        """
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.from_email = from_email
        self.to_emails = to_emails or []

    async def send_alert(self, alert: Alert) -> bool:
        """
        Send alert via email.

        Args:
            alert: Alert to send

        Returns:
            True if email was sent successfully
        """
        try:
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText

            if not self.to_emails:
                logger.warning("No email recipients configured for alert", alert_title=alert.title)
                return False

            # Create message
            msg = MIMEMultipart()
            msg["From"] = self.from_email
            msg["To"] = ", ".join(self.to_emails)
            msg["Subject"] = f"[{alert.severity.value.upper()}] {alert.title}"

            # Email body
            body = f"""
Alert Details:
- Title: {alert.title}
- Message: {alert.message}
- Severity: {alert.severity.value}
- Timestamp: {alert.timestamp.isoformat()}

Metadata:
{chr(10).join(f"- {k}: {v}" for k, v in alert.metadata.items())}
            """

            msg.attach(MIMEText(body, "plain"))

            # Send email
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            if self.smtp_username and self.smtp_password:  # pragma: no branch
                server.login(self.smtp_username, self.smtp_password)
            server.sendmail(self.from_email, self.to_emails, msg.as_string())
            server.quit()

            logger.info(
                "Alert sent via email",
                alert_title=alert.title,
                recipients=self.to_emails,
                severity=alert.severity.value,
            )
            return True

        except Exception as e:
            logger.error(
                "Failed to send alert via email",
                alert_title=alert.title,
                error=str(e),
            )
            return False


class PagerDutyNotificationChannel(NotificationChannel):
    """
    Sends alerts to PagerDuty.
    """

    def __init__(
        self,
        integration_key: str,
        source: str = "APGI API",
        component: str = "alerting",
        group: Optional[str] = None,
        class_: str = "alert",
    ):
        """
        Initialize PagerDuty notification channel.

        Args:
            integration_key: PagerDuty Events API v2 integration key
            source: Source of the alert
            component: Component that generated the alert
            group: Group the alert belongs to
            class_: Class/type of the alert
        """
        self.integration_key = integration_key
        self.source = source
        self.component = component
        self.group = group
        self.class_ = class_

    async def send_alert(self, alert: Alert) -> bool:
        """
        Send alert to PagerDuty.

        Args:
            alert: Alert to send

        Returns:
            True if alert was sent successfully
        """
        try:
            # Prepare PagerDuty event payload
            payload = {
                "routing_key": self.integration_key,
                "event_action": "trigger",
                "payload": {
                    "summary": alert.title,
                    "source": self.source,
                    "severity": self._severity_to_pagerduty_severity(alert.severity),
                    "component": self.component,
                    "group": self.group,
                    "class": self.class_,
                    "timestamp": alert.timestamp.isoformat() + "Z",
                    "custom_details": {
                        "message": alert.message,
                        "metadata": alert.metadata,
                    },
                },
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://events.pagerduty.com/v2/enqueue", json=payload, timeout=10.0
                )

                if response.status_code == 202:
                    logger.info(
                        "Alert sent to PagerDuty",
                        alert_title=alert.title,
                        severity=alert.severity.value,
                        dedup_key=(await response.json()).get("dedup_key"),
                    )
                    return True
                else:  # pragma: no cover
                    logger.warning(
                        "PagerDuty returned non-success status",
                        status_code=response.status_code,
                        alert_title=alert.title,
                        response_body=response.text,
                    )
                    return False

        except Exception as e:
            logger.error(
                "Failed to send alert to PagerDuty",
                alert_title=alert.title,
                error=str(e),
            )
            return False

    def _severity_to_pagerduty_severity(self, severity: AlertSeverity) -> str:
        """Convert alert severity to PagerDuty severity."""
        severity_map = {
            AlertSeverity.INFO: "info",
            AlertSeverity.WARNING: "warning",
            AlertSeverity.ERROR: "error",
            AlertSeverity.CRITICAL: "critical",
        }
        return severity_map.get(severity, "error")


class TeamsNotificationChannel(NotificationChannel):
    """
    Sends alerts to Microsoft Teams via webhook.
    """

    def __init__(self, webhook_url: str, title_prefix: str = "APGI Alert"):
        """
        Initialize Teams notification channel.

        Args:
            webhook_url: Microsoft Teams webhook URL
            title_prefix: Prefix for alert titles in Teams messages
        """
        self.webhook_url = webhook_url
        self.title_prefix = title_prefix

    async def send_alert(self, alert: Alert) -> bool:
        """
        Send alert to Microsoft Teams.

        Args:
            alert: Alert to send

        Returns:
            True if alert was sent successfully
        """
        try:
            # Format alert for Microsoft Teams
            teams_payload = {
                "@type": "MessageCard",
                "@context": "http://schema.org/extensions",
                "themeColor": self._severity_to_teams_color(alert.severity),
                "summary": f"{self.title_prefix}: {alert.title}",
                "sections": [
                    {
                        "activityTitle": f"{self.title_prefix}: {alert.title}",
                        "activitySubtitle": f"Severity: {alert.severity.value.upper()}",
                        "activityImage": self._get_severity_icon_url(alert.severity),
                        "facts": [
                            {"name": "Severity", "value": alert.severity.value},
                            {
                                "name": "Timestamp",
                                "value": alert.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC"),
                            },
                        ],
                        "text": alert.message,
                    }
                ],
            }

            # Add metadata as facts if present
            if alert.metadata:
                metadata_facts = [
                    {"name": key, "value": str(value)}
                    for key, value in alert.metadata.items()
                    if value is not None
                ]
                if metadata_facts:  # pragma: no branch
                    facts_list = cast(List[Dict[str, str]], teams_payload["sections"][0]["facts"])  # type: ignore
                    facts_list.extend(metadata_facts)

            async with httpx.AsyncClient() as client:
                response = await client.post(self.webhook_url, json=teams_payload, timeout=10.0)

                if response.status_code == 200:
                    logger.info(
                        "Alert sent to Microsoft Teams",
                        alert_title=alert.title,
                        severity=alert.severity.value,
                    )
                    return True
                else:
                    logger.warning(
                        "Teams webhook returned non-success status",
                        status_code=response.status_code,
                        alert_title=alert.title,
                        response_body=response.text,
                    )
                    return False

        except Exception as e:
            logger.error(
                "Failed to send alert to Microsoft Teams",
                alert_title=alert.title,
                error=str(e),
            )
            return False

    def _severity_to_teams_color(self, severity: AlertSeverity) -> str:
        """Convert alert severity to Teams theme color."""
        color_map = {
            AlertSeverity.INFO: "0078D7",  # Blue
            AlertSeverity.WARNING: "FFA500",  # Orange
            AlertSeverity.ERROR: "FF0000",  # Red
            AlertSeverity.CRITICAL: "8B0000",  # Dark Red
        }
        return color_map.get(severity, "FFA500")

    def _get_severity_icon_url(self, severity: AlertSeverity) -> str:
        """Get icon URL for severity level."""
        # Using Microsoft Office icons
        icon_map = {
            AlertSeverity.INFO: "https://static2.sharepointonline.com/files/fabric/assets/brand-icons/product/svg/outlook_16x1.svg",
            AlertSeverity.WARNING: "https://static2.sharepointonline.com/files/fabric/assets/brand-icons/product/svg/excel_16x1.svg",
            AlertSeverity.ERROR: "https://static2.sharepointonline.com/files/fabric/assets/brand-icons/product/svg/word_16x1.svg",
            AlertSeverity.CRITICAL: "https://static2.sharepointonline.com/files/fabric/assets/brand-icons/product/svg/powerpoint_16x1.svg",
        }
        return icon_map.get(severity, icon_map[AlertSeverity.WARNING])


class LogNotificationChannel(NotificationChannel):
    """
    Sends alerts to structured logger.
    """

    async def send_alert(self, alert: Alert) -> bool:
        """
        Send alert to logger.

        Args:
            alert: Alert to send

        Returns:
            True if alert was logged successfully
        """
        try:
            logger.error(
                "Alert triggered",
                alert_title=alert.title,
                alert_message=alert.message,
                severity=alert.severity.value,
                timestamp=alert.timestamp.isoformat(),
                metadata=alert.metadata,
            )
            return True
        except Exception as e:
            # Fallback to basic logging if structured logging fails
            print(f"Failed to log alert: {e}")
            return False


@dataclass
class AlertTemplate:
    """
    Template for formatting alerts.
    """

    name: str
    title_template: str
    message_template: str
    severity: AlertSeverity = AlertSeverity.ERROR

    def format_title(self, **kwargs: Any) -> str:
        """Format alert title using template."""
        return self.title_template.format(**kwargs)

    def format_message(self, **kwargs: Any) -> str:
        """Format alert message using template."""
        return self.message_template.format(**kwargs)


class AlertEscalationPolicy:
    """
    Defines how alerts should escalate over time.
    """

    def __init__(
        self,
        initial_severity: AlertSeverity,
        escalation_rules: Optional[List[Dict[str, Any]]] = None,
    ):
        """
        Initialize escalation policy.

        Args:
            initial_severity: Starting severity level
            escalation_rules: List of escalation rules with time thresholds
        """
        self.initial_severity = initial_severity
        self.escalation_rules = escalation_rules or []

    def get_severity_at_time(self, alert_age_seconds: float) -> AlertSeverity:
        """
        Get the appropriate severity based on alert age.

        Args:
            alert_age_seconds: How long the alert has been active

        Returns:
            Current severity level
        """
        current_severity = self.initial_severity

        for rule in self.escalation_rules:
            if alert_age_seconds >= rule.get("after_seconds", 0):
                new_severity = rule.get("severity")
                if isinstance(new_severity, str):
                    try:
                        new_severity = AlertSeverity(new_severity)
                    except ValueError:
                        continue  # Skip invalid severity values

                SEVERITY_WEIGHTS = {
                    AlertSeverity.INFO: 1,
                    AlertSeverity.WARNING: 2,
                    AlertSeverity.ERROR: 3,
                    AlertSeverity.CRITICAL: 4,
                }
                if new_severity is not None and SEVERITY_WEIGHTS.get(
                    new_severity, 0
                ) > SEVERITY_WEIGHTS.get(current_severity, 0):
                    current_severity = new_severity

        return current_severity


class AlertRule:
    """
    Defines a rule for triggering alerts based on metrics.
    """

    def __init__(
        self,
        name: str,
        condition: Callable[[], Any],
        template: AlertTemplate,
        escalation_policy: Optional[AlertEscalationPolicy] = None,
        cooldown_seconds: int = 300,
    ):
        """
        Initialize alert rule.

        Args:
            name: Rule name
            condition: Function that returns True if alert should trigger
            template: Alert template to use
            escalation_policy: How alert should escalate
            cooldown_seconds: Minimum time between alerts
        """
        self.name = name
        self.condition = condition
        self.template = template
        self.escalation_policy = escalation_policy
        self.cooldown_seconds = cooldown_seconds
        self.last_triggered: Optional[datetime] = None

    async def should_trigger(self) -> Optional[Alert]:
        """
        Check if this rule should trigger an alert.

        Returns:
            Alert if rule should trigger, None otherwise
        """
        # Check cooldown
        if self.last_triggered:
            time_since_last = (datetime.now(timezone.utc) - self.last_triggered).total_seconds()
            if time_since_last < self.cooldown_seconds:  # pragma: no branch
                return None

        # Check condition
        try:
            condition_result = await self.condition()
            if condition_result:  # pragma: no branch
                self.last_triggered = datetime.now(timezone.utc)

                # Create alert
                alert_data = condition_result if isinstance(condition_result, dict) else {}
                alert_data.setdefault("rule_name", self.name)

                title = self.template.format_title(**alert_data)
                message = self.template.format_message(**alert_data)

                return Alert(
                    title=title,
                    message=message,
                    severity=self.template.severity,
                    metadata=alert_data,
                )
        except Exception as e:
            logger.error(f"Error checking alert rule {self.name}", error=str(e))

        return None


class AlertManager:
    """
    Manages alert triggers and notification channels.

    Monitors error rates and triggers alerts when thresholds are exceeded.
    Supports advanced features like escalation, templates, and rule-based alerting.
    """

    def __init__(self) -> None:
        """Initialize alert manager."""
        self.channels: List[NotificationChannel] = []
        self.rules: List[AlertRule] = []
        self.active_alerts: Dict[str, Alert] = {}
        self.error_counts: Dict[str, List[datetime]] = {}
        self.alert_cooldowns: Dict[str, datetime] = {}
        self._lock = asyncio.Lock()

        # Alert thresholds
        self.error_rate_threshold = 10  # errors per minute
        self.error_rate_window = timedelta(minutes=1)
        self.alert_cooldown = timedelta(minutes=5)  # Don't spam alerts

    def add_channel(self, channel: NotificationChannel) -> None:
        """
        Add a notification channel.

        Args:
            channel: Notification channel to add
        """
        self.channels.append(channel)
        logger.info(
            "Notification channel added",
            channel_type=type(channel).__name__,
            total_channels=len(self.channels),
        )

    def add_rule(self, rule: AlertRule) -> None:
        """
        Add an alert rule.

        Args:
            rule: Alert rule to add
        """
        self.rules.append(rule)
        logger.info(
            "Alert rule added",
            rule_name=rule.name,
            total_rules=len(self.rules),
        )

    async def check_rules(self) -> None:
        """
        Check all alert rules and trigger alerts as needed.
        """
        for rule in self.rules:
            alert = await rule.should_trigger()
            if alert:
                await self._send_alert(alert)

    async def escalate_alerts(self) -> None:
        """
        Check active alerts and escalate them according to their policies.
        """
        now = datetime.now(timezone.utc)

        for alert_key, alert in list(self.active_alerts.items()):
            alert_age = (now - alert.timestamp).total_seconds()

            # Check if escalation policy exists
            escalation_policy = getattr(alert, "escalation_policy", None)
            if escalation_policy:  # pragma: no branch
                new_severity = escalation_policy.get_severity_at_time(alert_age)
                if new_severity != alert.severity:  # pragma: no branch
                    # Escalate alert
                    escalated_alert = Alert(
                        title=f"[ESCALATED] {alert.title}",
                        message=f"{alert.message}\n\nAlert has been escalated to {new_severity.value} severity after {alert_age:.0f} seconds.",
                        severity=new_severity,
                        timestamp=alert.timestamp,
                        metadata={
                            **alert.metadata,
                            "escalated_at": now.isoformat(),
                            "escalated_from": alert.severity.value,
                        },
                    )

                    await self._send_alert(escalated_alert)
                    alert.severity = new_severity

                    logger.info(
                        "Alert escalated",
                        alert_title=alert.title,
                        old_severity=alert.severity.value,
                        new_severity=new_severity.value,
                        alert_age=alert_age,
                    )

    async def record_error(
        self, error_type: str, error_message: str, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Record an error and check if alert should be triggered.

        Args:
            error_type: Type of error
            error_message: Error message
            metadata: Additional error metadata
        """
        async with self._lock:
            now = datetime.now(timezone.utc)

            # Initialize error tracking for this type
            if error_type not in self.error_counts:
                self.error_counts[error_type] = []

            # Add error timestamp
            self.error_counts[error_type].append(now)

            # Clean up old errors outside the window
            cutoff = now - self.error_rate_window
            self.error_counts[error_type] = [
                ts for ts in self.error_counts[error_type] if ts > cutoff
            ]

            # Check if we should trigger an alert
            error_count = len(self.error_counts[error_type])

            if error_count >= self.error_rate_threshold:
                await self._trigger_high_error_rate_alert(
                    error_type, error_count, error_message, metadata
                )

    async def _trigger_high_error_rate_alert(
        self,
        error_type: str,
        error_count: int,
        error_message: str,
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        """
        Trigger alert for high error rate.

        Args:
            error_type: Type of error
            error_count: Number of errors in window
            error_message: Recent error message
            metadata: Additional metadata
        """
        # Check cooldown to avoid alert spam
        alert_key = f"high_error_rate_{error_type}"
        now = datetime.now(timezone.utc)

        if alert_key in self.alert_cooldowns:  # pragma: no branch
            if now < self.alert_cooldowns[alert_key]:  # pragma: no branch
                # Still in cooldown period
                return

        # Set cooldown
        self.alert_cooldowns[alert_key] = now + self.alert_cooldown

        # Create alert
        alert = Alert(
            title=f"High Error Rate: {error_type}",
            message=f"Detected {error_count} errors of type '{error_type}' in the last minute. Recent error: {error_message}",
            severity=AlertSeverity.CRITICAL,
            metadata={
                "error_type": error_type,
                "error_count": error_count,
                "window_minutes": self.error_rate_window.total_seconds() / 60,
                "recent_error": error_message,
                **(metadata or {}),
            },
        )

        # Send alert through all channels
        await self._send_alert(alert)

    async def trigger_custom_alert(
        self,
        title: str,
        message: str,
        severity: AlertSeverity = AlertSeverity.ERROR,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Trigger a custom alert.

        Args:
            title: Alert title
            message: Alert message
            severity: Alert severity
            metadata: Additional metadata
        """
        alert = Alert(title=title, message=message, severity=severity, metadata=metadata or {})

        await self._send_alert(alert)

    async def _send_alert(self, alert: Alert) -> None:
        """
        Send alert through all configured channels.

        Args:
            alert: Alert to send
        """
        if not self.channels:
            logger.warning("No notification channels configured", alert_title=alert.title)
            return

        # Send to all channels concurrently
        tasks = [channel.send_alert(alert) for channel in self.channels]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log results
        success_count = sum(1 for r in results if r is True)
        logger.info(
            "Alert sent to channels",
            alert_title=alert.title,
            severity=alert.severity.value,
            channels_total=len(self.channels),
            channels_success=success_count,
        )


# Global alert manager instance
alert_manager = AlertManager()


def configure_alerting(
    webhook_urls: Optional[List[str]] = None,
    slack_webhook_urls: Optional[List[str]] = None,
    pagerduty_integration_keys: Optional[List[str]] = None,
    teams_webhook_urls: Optional[List[str]] = None,
    email_config: Optional[Dict[str, Any]] = None,
    enable_log_channel: bool = True,
    error_rate_threshold: int = 10,
    error_rate_window_minutes: int = 1,
    alert_cooldown_minutes: int = 5,
) -> None:
    """
    Configure the alerting system with advanced features.

    Args:
        webhook_urls: List of HTTP webhook URLs for alerts
        slack_webhook_urls: List of Slack webhook URLs for alerts
        pagerduty_integration_keys: List of PagerDuty integration keys
        teams_webhook_urls: List of Microsoft Teams webhook URLs
        email_config: Email configuration dict with SMTP settings
        enable_log_channel: Whether to enable log-based alerts
        error_rate_threshold: Number of errors to trigger alert
        error_rate_window_minutes: Time window for error rate calculation
        alert_cooldown_minutes: Cooldown period between alerts
    """
    # Configure thresholds
    alert_manager.error_rate_threshold = error_rate_threshold
    alert_manager.error_rate_window = timedelta(minutes=error_rate_window_minutes)
    alert_manager.alert_cooldown = timedelta(minutes=alert_cooldown_minutes)

    # Add HTTP webhook channels
    if webhook_urls:
        for url in webhook_urls:
            alert_manager.add_channel(WebhookNotificationChannel(url))

    # Add Slack webhook channels
    if slack_webhook_urls:
        for url in slack_webhook_urls:
            alert_manager.add_channel(SlackNotificationChannel(url))

    # Add PagerDuty channels
    if pagerduty_integration_keys:
        for key in pagerduty_integration_keys:
            alert_manager.add_channel(PagerDutyNotificationChannel(key))

    # Add Teams webhook channels
    if teams_webhook_urls:
        for url in teams_webhook_urls:
            alert_manager.add_channel(TeamsNotificationChannel(url))

    # Add email channel
    if email_config:
        alert_manager.add_channel(EmailNotificationChannel(**email_config))

    # Add log channel
    if enable_log_channel:
        alert_manager.add_channel(LogNotificationChannel())

    logger.info(
        "Alerting system configured with advanced features",
        error_rate_threshold=error_rate_threshold,
        error_rate_window_minutes=error_rate_window_minutes,
        alert_cooldown_minutes=alert_cooldown_minutes,
        channels=len(alert_manager.channels),
        webhooks=len(webhook_urls or []),
        slack_webhooks=len(slack_webhook_urls or []),
        pagerduty_integrations=len(pagerduty_integration_keys or []),
        teams_webhooks=len(teams_webhook_urls or []),
        email_configured=bool(email_config),
        log_channel=enable_log_channel,
    )
