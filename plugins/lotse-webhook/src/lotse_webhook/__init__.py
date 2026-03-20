"""Lotse webhook plugin — send classified items to webhook URLs."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from lotse.plugins.spec import hookimpl

logger = logging.getLogger(__name__)

# Timeout for webhook requests
REQUEST_TIMEOUT = 10.0


@hookimpl
def on_routed(path: str, destination: str, route_name: str) -> None:
    """Send a notification when an item is routed to a webhook route."""
    # This hook fires for ALL routes. The custom_route hook handles
    # the actual webhook delivery. This is just for logging/observability.
    logger.debug("Webhook plugin saw routing: %s → %s (%s)", path, destination, route_name)


@hookimpl
def custom_route(path: str, classification: object) -> dict | None:
    """Handle webhook route type — POST classified item data to a URL."""
    # We need access to route config, which isn't passed to custom_route.
    # The plugin reads its config from the classification context.
    # For now, this hook is a no-op — webhook routing is handled by
    # the WebhookRouter registered in the route system.
    return None


def send_webhook(
    url: str,
    item_data: dict,
    timeout: float = REQUEST_TIMEOUT,
) -> bool:
    """Send item data to a webhook URL. Returns True on success."""
    payload = _format_payload(url, item_data)
    headers = _headers_for_url(url)

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            logger.info("Webhook delivered to %s (status=%d)", _mask_url(url), resp.status_code)
            return True
    except httpx.HTTPStatusError as e:
        logger.warning(
            "Webhook %s returned %d: %s",
            _mask_url(url),
            e.response.status_code,
            e.response.text[:200],
        )
        return False
    except httpx.RequestError as e:
        logger.error("Webhook request to %s failed: %s", _mask_url(url), e)
        return False


def _format_payload(url: str, item_data: dict) -> dict:
    """Format the payload based on the webhook destination."""
    timestamp = datetime.now(UTC).isoformat()

    if _is_slack_url(url):
        return _format_slack(item_data, timestamp)
    elif _is_discord_url(url):
        return _format_discord(item_data, timestamp)
    else:
        return _format_generic(item_data, timestamp)


def _format_generic(item_data: dict, timestamp: str) -> dict:
    """Generic JSON payload."""
    return {
        "event": "item_routed",
        "item": item_data,
        "timestamp": timestamp,
    }


def _format_slack(item_data: dict, timestamp: str) -> dict:
    """Slack-compatible message payload."""
    category = item_data.get("category", "unknown")
    summary = item_data.get("summary", "No summary")
    confidence = item_data.get("confidence", 0)
    tags = item_data.get("tags", [])

    tag_str = ", ".join(f"`{t}`" for t in tags) if tags else "none"

    return {
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Lotse* classified a new item\n\n"
                        f">*{summary}*\n"
                        f">Category: `{category}` "
                        f"({confidence:.0%} confidence)\n"
                        f">Tags: {tag_str}"
                    ),
                },
            },
        ],
    }


def _format_discord(item_data: dict, timestamp: str) -> dict:
    """Discord-compatible embed payload."""
    category = item_data.get("category", "unknown")
    summary = item_data.get("summary", "No summary")
    confidence = item_data.get("confidence", 0)

    return {
        "embeds": [
            {
                "title": "Lotse — New Item Classified",
                "description": summary,
                "color": 3447003,  # Blue
                "fields": [
                    {"name": "Category", "value": f"`{category}`", "inline": True},
                    {
                        "name": "Confidence",
                        "value": f"{confidence:.0%}",
                        "inline": True,
                    },
                ],
                "timestamp": timestamp,
            }
        ],
    }


def _is_slack_url(url: str) -> bool:
    return "hooks.slack.com" in url


def _is_discord_url(url: str) -> bool:
    return "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url


def _headers_for_url(url: str) -> dict[str, str]:
    """Return appropriate headers for the webhook URL."""
    headers = {"Content-Type": "application/json", "User-Agent": "Lotse/0.1"}
    return headers


def _mask_url(url: str) -> str:
    """Mask sensitive parts of webhook URLs for logging."""
    if len(url) > 40:
        return url[:30] + "..." + url[-7:]
    return url
