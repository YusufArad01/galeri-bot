"""Telegram Bot API notifier for detected opportunities."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import httpx

from core.config import settings
from core.logger import logger

_API_BASE: Final[str] = "https://api.telegram.org"
_MDV2_SPECIAL: Final[re.Pattern[str]] = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")
_MAX_MESSAGE_LEN: Final[int] = 4096


def escape_markdown_v2(value: Any) -> str:
    """Escape text for Telegram MarkdownV2."""
    return _MDV2_SPECIAL.sub(r"\\\1", str(value))


def _escape_url(url: str) -> str:
    # Inside (...) of an inline link only ')' and '\' must be escaped.
    return url.replace("\\", "\\\\").replace(")", "\\)")


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def format_try(value: Any) -> str:
    """Format a number Turkish-style without decimals: 1250000 -> '1.250.000'."""
    amount = _to_decimal(value)
    if amount is None:
        return "-"
    return f"{int(amount.to_integral_value()):,}".replace(",", ".")


class TelegramNotifier:
    """Sends MarkdownV2-formatted opportunity alerts to a Telegram chat."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        timeout: float = 15.0,
        max_retries: int = 3,
    ) -> None:
        token = bot_token
        if token is None and settings.TELEGRAM_BOT_TOKEN is not None:
            token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
        self._token: str | None = token or None
        self._chat_id: str | None = chat_id or settings.TELEGRAM_CHAT_ID
        self._timeout: float = timeout
        self._max_retries: int = max(1, max_retries)

    @property
    def is_configured(self) -> bool:
        return bool(self._token and self._chat_id)

    @staticmethod
    def format_message(data: Mapping[str, Any]) -> str:
        e = escape_markdown_v2
        price = _to_decimal(data.get("price"))
        market = _to_decimal(data.get("market_average_price"))
        deviation = _to_decimal(data.get("deviation_percentage"))
        saving = (market - price) if price is not None and market is not None else None
        deviation_text = f"{deviation:.2f}".replace(".", ",") if deviation is not None else "-"
        km = data.get("km")
        km_text = f"{format_try(km)} km" if km is not None else "-"

        lines = [
            "🚗 *FIRSAT TESPİT EDİLDİ*",
            "",
            f"*{e(data.get('title', '-'))}*",
            "",
            f"▫️ *Araç:* {e(data.get('brand', '-'))} {e(data.get('model', '-'))} \\({e(data.get('year', '-'))}\\)",
            f"▫️ *Kilometre:* {e(km_text)}",
            f"▫️ *Şehir:* {e(data.get('city') or '-')}",
            "",
            f"💰 *İlan Fiyatı:* {e(format_try(price))} TL",
            f"📊 *Piyasa Ortalaması:* {e(format_try(market))} TL",
            f"📉 *İndirim:* %{e(deviation_text)} \\(≈ {e(format_try(saving))} TL\\)",
        ]
        sample_size = data.get("sample_size")
        if sample_size is not None:
            lines.append(f"🔢 *Örneklem:* {e(sample_size)} ilan")
        url = data.get("url")
        if url:
            lines += ["", f"🔗 [İlana Git]({_escape_url(str(url))})"]

        message = "\n".join(lines)
        return message[:_MAX_MESSAGE_LEN]

    async def send_alert(self, opportunity_data: Mapping[str, Any]) -> bool:
        """Send a single alert. Returns True when Telegram accepted the message."""
        if not self.is_configured:
            logger.warning("Telegram is not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID); alert skipped")
            return False

        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": self.format_message(opportunity_data),
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": False,
        }
        endpoint = f"{_API_BASE}/bot{self._token}/sendMessage"
        label = opportunity_data.get("opportunity_id", opportunity_data.get("source_listing_id", "?"))

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(1, self._max_retries + 1):
                try:
                    response = await client.post(endpoint, json=payload)
                except httpx.HTTPError as exc:
                    # Never log the exception's request URL: it embeds the bot token.
                    logger.warning("Telegram request error (attempt {}/{}): {}", attempt, self._max_retries, type(exc).__name__)
                    await asyncio.sleep(2.0 * attempt)
                    continue

                body = self._safe_json(response)
                if response.status_code == 200 and body.get("ok") is True:
                    logger.info("Telegram alert sent for opportunity {}", label)
                    return True

                description = body.get("description", response.text[:200])
                if response.status_code == 429:
                    retry_after = float(body.get("parameters", {}).get("retry_after", 5))
                    logger.warning("Telegram rate limited; retrying in {}s", retry_after)
                    await asyncio.sleep(retry_after + 0.5)
                    continue
                if response.status_code >= 500:
                    logger.warning("Telegram server error {} (attempt {}): {}", response.status_code, attempt, description)
                    await asyncio.sleep(2.0 * attempt)
                    continue

                logger.error("Telegram rejected alert {} (HTTP {}): {}", label, response.status_code, description)
                return False

        logger.error("Telegram alert {} failed after {} attempts", label, self._max_retries)
        return False

    @staticmethod
    def _safe_json(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
