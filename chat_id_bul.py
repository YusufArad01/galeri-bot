"""Bota mesaj atan kişilerin Telegram numaralarını (chat_id) listeler.

Galerici bota bir kez "merhaba" yazar, sonra bu betik çalıştırılır:
    TELEGRAM_BOT_TOKEN=... python chat_id_bul.py
Bot ilan bildirimi için getUpdates kullanmadığı için canlı sistemi etkilemez.
"""
import os
import sys

import httpx

sys.stdout.reconfigure(encoding="utf-8")
token = os.environ.get("TELEGRAM_BOT_TOKEN") or (sys.argv[1] if len(sys.argv) > 1 else "")
if not token:
    sys.exit("TELEGRAM_BOT_TOKEN ortam değişkenini ya da ilk argüman olarak token'ı ver.")
data = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30).json()
seen: dict[int, str] = {}
for u in data.get("result", []):
    m = u.get("message") or u.get("edited_message") or {}
    c = m.get("chat") or {}
    if c.get("id"):
        ad = " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x) or c.get("title") or ""
        seen[c["id"]] = f"{ad} (@{c.get('username', '-')}) son mesaj: {m.get('text', '')[:40]!r}"
print("\n".join(f"{cid}  {info}" for cid, info in seen.items()) or "Hiç mesaj yok: galerici bota önce bir mesaj yazmalı.")
