"""
Webhook Manager Service

Handles webhook delivery and management for task completions.
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, cast

import aiohttp
from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import WebhookDelivery
from app.middleware.alerting import AlertSeverity, alert_manager

logger = logging.getLogger(__name__)


class WebhookManager:
    """Manager for webhook deliveries."""

    MAX_RESPONSE_SIZE = 1024 * 1024  # 1MB limit for response bodies

    # Private IP ranges to block
    PRIVATE_NETWORKS = [
        ipaddress.ip_network("10.0.0.0/8"),  # RFC 1918
        ipaddress.ip_network("172.16.0.0/12"),  # RFC 1918
        ipaddress.ip_network("192.168.0.0/16"),  # RFC 1918
        ipaddress.ip_network("127.0.0.0/8"),  # Loopback
        ipaddress.ip_network("169.254.0.0/16"),  # Link-local
        ipaddress.ip_network("::1/128"),  # IPv6 loopback
        ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
        ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
    ]

    @staticmethod
    def _validate_webhook_url(webhook_url: str) -> str:
        """
        Validate webhook URL for SSRF prevention and return resolved IP.

        Args:
            webhook_url: Webhook URL to validate

        Returns:
            Resolved IP address of the hostname

        Raises:
            ValueError: If URL is invalid or points to private/internal network
        """
        import ipaddress
        import socket
        from urllib.parse import urlparse

        try:
            parsed = urlparse(webhook_url)

            # Basic URL validation
            if not parsed.scheme or not parsed.netloc:
                raise ValueError("Invalid URL format")

            # Only allow HTTP/HTTPS
            if parsed.scheme not in ["http", "https"]:
                raise ValueError("Only HTTP and HTTPS URLs are allowed")

            hostname = parsed.hostname
            if not hostname:
                raise ValueError("URL must have a hostname")

            # Resolve hostname to IP addresses
            try:
                addrinfo = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                ip_addresses = [info[4][0] for info in addrinfo]
            except socket.gaierror as e:
                raise ValueError(f"Could not resolve hostname {hostname}: {e}")

            # Check each IP address against private networks and metadata IPs
            allowed_ip = None
            for ip_str in ip_addresses:
                try:
                    ip = ipaddress.ip_address(ip_str)

                    # Block cloud metadata IP ranges
                    if ip_str in ["169.254.169.254", "169.254.169.253", "169.254.169.252"]:
                        raise ValueError(f"Access to cloud metadata IP {ip_str} is blocked")

                    for private_net in WebhookManager.PRIVATE_NETWORKS:
                        if ip in private_net:
                            raise ValueError(f"URL points to private/internal IP address: {ip_str}")

                    # If we reach here, this IP is allowed. Use the first valid one.
                    if allowed_ip is None:
                        allowed_ip = ip_str

                except (
                    ipaddress.AddressValueError,
                    ipaddress.NetmaskValueError,
                ):  # pragma: no cover
                    # Invalid IP format, skip
                    continue

            if allowed_ip is None:
                raise ValueError("No valid IP address found for hostname")

            return cast(str, allowed_ip)

        except Exception as e:
            raise ValueError(f"Invalid webhook URL: {e}")

    def __init__(self) -> None:
        """Initialize webhook manager."""
        self.session = None

    async def __aenter__(self) -> "WebhookManager":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close the webhook manager."""
        if self.session:
            await self.session.close()
            self.session = None

    async def create_webhook_delivery(
        self, db: Session, task_id: str, webhook_url: str, payload: Dict[str, Any]
    ) -> str:
        """
        Create a webhook delivery record.

        Args:
            db: Database session
            task_id: Associated task ID
            webhook_url: Target webhook URL
            payload: Webhook payload data

        Returns:
            Delivery ID

        Raises:
            ValueError: If webhook URL is invalid or insecure
        """
        # Validate webhook URL to prevent SSRF
        resolved_ip = self._validate_webhook_url(webhook_url)

        delivery_id = str(uuid.uuid4())

        # Calculate retry schedule (exponential backoff)
        now = datetime.now(timezone.utc)
        retry_delays = [5, 30, 300, 1800, 3600]  # 5s, 30s, 5min, 30min, 1h

        delivery = WebhookDelivery(
            delivery_id=delivery_id,
            task_id=task_id,
            webhook_url=webhook_url,
            resolved_ip=resolved_ip,
            payload=payload,
            status="pending",
            attempts=0,
            next_retry_at=now + timedelta(seconds=retry_delays[0]),
        )

        try:
            db.add(delivery)
            db.commit()
            db.refresh(delivery)
            return delivery_id
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to create webhook delivery for task {task_id}: {e}")
            raise

    async def deliver_webhook(self, db: Session, delivery_id: str) -> bool:
        """
        Attempt to deliver a webhook.

        Args:
            db: Database session
            delivery_id: Delivery ID

        Returns:
            True if delivery successful, False otherwise
        """
        # Get delivery record
        delivery = (
            db.query(WebhookDelivery).filter(WebhookDelivery.delivery_id == delivery_id).first()
        )

        if not delivery:
            logger.warning(f"Webhook delivery {delivery_id} not found")
            return False

        # Skip if already delivered
        if delivery.status == "delivered":
            return True

        # Check retry attempts
        if delivery.attempts >= delivery.retry_count:
            delivery.status = "dead_letter"  # type: ignore[assignment]
            delivery.error_message = "Maximum retry attempts exceeded, moved to dead-letter queue"  # type: ignore[assignment]
            db.commit()
            # Alert about dead-letter webhook
            await alert_manager.trigger_custom_alert(
                title="Webhook Dead-Letter",
                message=f"Webhook delivery {delivery_id} for task {delivery.task_id} moved to dead-letter queue after {delivery.attempts} attempts",
                severity=AlertSeverity.WARNING,
                metadata={
                    "delivery_id": delivery_id,
                    "task_id": delivery.task_id,
                    "webhook_url": delivery.webhook_url,
                    "attempts": delivery.attempts,
                    "error_message": delivery.error_message,
                },
            )
            return False

        try:
            # Prepare headers with HMAC signature
            headers = {"Content-Type": "application/json"}
            if not settings.webhook_secret_key:
                raise ValueError(
                    "WEBHOOK_SECRET_KEY must be configured for webhook signature verification"
                )

            payload_str = json.dumps(delivery.payload, sort_keys=True, separators=(",", ":"))
            signature = hmac.new(
                settings.webhook_secret_key.encode(), payload_str.encode(), hashlib.sha256
            ).hexdigest()
            headers["X-Signature-256"] = f"sha256={signature}"

            # Use resolved IP to prevent DNS rebinding
            from urllib.parse import urlparse, urlunparse

            parsed_url = urlparse(str(delivery.webhook_url))

            # Replace hostname with resolved IP
            pinned_url = urlunparse(
                (
                    parsed_url.scheme,
                    f"{delivery.resolved_ip}:{parsed_url.port or (443 if parsed_url.scheme == 'https' else 80)}",
                    parsed_url.path,
                    parsed_url.params,
                    parsed_url.query,
                    parsed_url.fragment,
                )
            )

            # For HTTPS, we need to handle SSL verification carefully
            # Since we're pinning the IP, certificate hostname verification will fail
            # We disable hostname verification but keep certificate validation
            if parsed_url.scheme == "https":
                import ssl

                # Create SSL context that validates certificate but not hostname
                # This allows connecting to IP-pinned URLs while still validating the certificate
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False

                connector = aiohttp.TCPConnector(ssl=ssl_context)
            else:
                connector = aiohttp.TCPConnector()

            # Attempt delivery
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                    pinned_url,  # Use pinned IP URL
                    json=delivery.payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    delivery.attempts += 1  # type: ignore[assignment]
                    delivery.last_attempt_at = datetime.now(timezone.utc)  # type: ignore[assignment]

                    if response.status >= 200 and response.status < 300:
                        delivery.status = "delivered"  # type: ignore[assignment]
                        delivery.response_status = response.status  # type: ignore[assignment]
                        text = await response.text()
                        if len(text) > self.MAX_RESPONSE_SIZE:
                            text = text[: self.MAX_RESPONSE_SIZE] + "... (truncated)"
                        delivery.response_body = text  # type: ignore[assignment]
                        logger.info(f"Webhook delivery {delivery_id} successful")
                        db.commit()
                        return True
                    else:
                        # Check if retries are available
                        if delivery.attempts < delivery.retry_count:
                            delivery.status = "retry"  # type: ignore[assignment]
                            retry_delays = cast(
                                list[int], delivery.retry_delays or [5, 30, 300, 1800, 3600]
                            )
                            attempts_value = (
                                int(delivery.attempts) if delivery.attempts is not None else 0
                            )
                            delay_index = min(attempts_value, len(retry_delays) - 1)
                            delay_value = (
                                retry_delays[delay_index]
                                if delay_index < len(retry_delays)
                                else retry_delays[-1]
                            )
                            delivery.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=float(cast(float, delay_value)))  # type: ignore[assignment]
                            delivery.error_message = f"HTTP {response.status} - will retry"  # type: ignore[assignment]
                            db.commit()
                            return False
                        else:
                            delivery.status = "dead_letter"  # type: ignore[assignment]
                            delivery.error_message = "Maximum retry attempts exceeded, moved to dead-letter queue"  # type: ignore[assignment]
                            db.commit()
                        # Alert about dead-letter webhook
                        await alert_manager.trigger_custom_alert(
                            title="Webhook Dead-Letter",
                            message=f"Webhook delivery {delivery_id} for task {delivery.task_id} moved to dead-letter queue after {delivery.attempts} attempts",
                            severity=AlertSeverity.WARNING,
                            metadata={
                                "delivery_id": delivery_id,
                                "task_id": delivery.task_id,
                                "webhook_url": delivery.webhook_url,
                                "attempts": delivery.attempts,
                                "error_message": delivery.error_message,
                            },
                        )
                        return False

        except Exception as e:
            # Handle any unexpected errors during webhook delivery
            delivery.attempts += 1  # type: ignore[assignment]
            delivery.last_attempt_at = datetime.now(timezone.utc)  # type: ignore[assignment]
            delivery.error_message = f"Unexpected error: {str(e)}"  # type: ignore[assignment]

            # Check if retries are available
            if delivery.attempts < delivery.retry_count:
                delivery.status = "retry"  # type: ignore[assignment]
                retry_delays = cast(list[int], delivery.retry_delays or [5, 30, 300, 1800, 3600])
                delay_index = min(int(delivery.attempts), len(retry_delays) - 1)
                delivery.next_retry_at = datetime.now(timezone.utc) + timedelta(  # type: ignore[assignment]
                    seconds=float(cast(float, retry_delays[delay_index]))
                )
                db.commit()
                logger.error(
                    f"Unexpected error delivering webhook {delivery_id}: {e}, will retry",
                    exc_info=True,
                )
                return False
            else:
                delivery.status = "dead_letter"  # type: ignore[assignment]
                db.commit()
                logger.error(
                    f"Unexpected error delivering webhook {delivery_id}: {e}, max retries exceeded",
                    exc_info=True,
                )
                return False

    async def process_pending_deliveries(self, db: Session) -> int:
        now = datetime.now(timezone.utc)
        pending_deliveries = (
            db.query(WebhookDelivery)
            .filter(
                WebhookDelivery.status.in_(["pending", "retry"]),
                WebhookDelivery.next_retry_at <= now,
                WebhookDelivery.attempts < WebhookDelivery.retry_count,
            )
            .all()
        )

        processed = 0
        for delivery in pending_deliveries:
            await self.deliver_webhook(db, delivery.delivery_id)  # type: ignore[arg-type]
            processed += 1

        return processed
