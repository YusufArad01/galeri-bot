"""Telegram'ı dinler: mesajdaki 👍/👎 düğmeleri ve /start.

- 👍/👎: `feedback` tablosuna yazılır (alert başına tek oy). Günlük yönetici özetinde sayılır; kural ayarı buna bakılarak yapılır.
- /start: kişiye sohbet numarasını söyler, yöneticiye "yeni kişi" bildirir (galerici ekleme: numara buradan alınır).
"""
from __future__ import annotations

import asyncio

from loguru import logger
from sqlalchemy import select

from src.models.domain import Alert, BotState, Feedback
from src.notifications import Telegram


async def _record_vote(session, cb: dict) -> None:
    parts = (cb.get("data") or "").split(":")
    if len(parts) != 3 or parts[0] != "f" or not parts[1].isdigit() or parts[2] not in ("0", "1"):
        return
    alert = await session.get(Alert, int(parts[1]))
    msg = cb.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    if alert is None or alert.chat_id != chat_id:   # başkasının mesajına oy verilemez
        return
    vote = int(parts[2])
    row = (await session.execute(select(Feedback).where(Feedback.alert_id == alert.id))).scalars().first()
    if row:
        row.vote = vote
    else:
        session.add(Feedback(alert_id=alert.id, dealer_id=alert.dealer_id, chat_id=chat_id, vote=vote))
    await session.commit()


async def handle_update(session, telegram: Telegram, update: dict, admin_chat: str) -> None:
    cb = update.get("callback_query")
    if cb:
        await _record_vote(session, cb)
        await telegram.call("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Teşekkürler, not aldım."})
        msg = cb.get("message") or {}
        if msg.get("message_id") and (cb.get("data") or "").endswith(("0", "1")):
            liked = (cb["data"]).endswith("1")
            await telegram.call("editMessageReplyMarkup", {
                "chat_id": msg["chat"]["id"], "message_id": msg["message_id"],
                "reply_markup": {"inline_keyboard": [[{"text": "👍 Beğendiniz" if liked else "👎 Beğenmediniz", "callback_data": "noop"}]]}})
        return
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    if (msg.get("text") or "").startswith("/start") and chat.get("id"):
        name = chat.get("first_name") or chat.get("title") or ""
        await telegram.send(str(chat["id"]), f"Merhaba {name}. İlan bildirimleri bu sohbete gelecek.\nSohbet numaranız: {chat['id']}")
        if str(chat["id"]) != str(admin_chat):
            await telegram.send(admin_chat, f"👤 Yeni /start: {name} (@{chat.get('username', '-')}) chat_id={chat['id']}")


async def poll_forever(telegram: Telegram, session_factory, admin_chat: str) -> None:
    """Uzun sorgulama (getUpdates). Hata olursa döngüyü düşürmez, bekleyip devam eder."""
    if not telegram.configured:
        return
    while True:
        try:
            async with session_factory() as session:
                row = await session.get(BotState, "tg_offset")
                offset = int(row.value) if row and row.value else 0
                resp = await telegram.call("getUpdates", {"offset": offset, "timeout": 25,
                                                           "allowed_updates": ["message", "callback_query"]}, timeout=25)
                if resp is None:
                    await asyncio.sleep(15)
                    continue
                for update in resp.get("result", []):
                    try:
                        await handle_update(session, telegram, update, admin_chat)
                    except Exception as exc:  # tek bir bozuk güncelleme dinlemeyi durdurmasın
                        logger.warning(f"Telegram güncellemesi işlenemedi: {type(exc).__name__}: {exc}")
                    offset = update["update_id"] + 1
                    if row:
                        row.value = str(offset)
                    else:
                        row = BotState(key="tg_offset", value=str(offset))
                        session.add(row)
                    await session.commit()
            await asyncio.sleep(0.5)   # boş cevap hızlı dönerse (sunucu/ara katman) sıkı döngüye girme
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"Telegram dinleme hatası: {type(exc).__name__}: {exc}")
            await asyncio.sleep(15)
