"""Облачный дежурный запуск бота (для GitHub Actions).

Один запуск = ~4,5 минуты дежурства: бот просыпается, отвечает на всё
новое (текст и картинки), затем завершается. GitHub будит его каждые 5 минут.
"""
import os
import time

import vk_bot_simple as vbs

vbs.SETTINGS.update(vbs.load_settings())
if not (vbs.SETTINGS.get("vk_token") and vbs.SETTINGS.get("api_key")):
    raise SystemExit("[!] Нет ключей. Заполни секреты VK_TOKEN, VK_GROUP_ID, AI_KEY")

print("[i] Дежурный запуск запущен")
seen: dict = {}
first = True
deadline = time.time() + int(os.environ.get("RUN_SECONDS", "260"))
cycles = 0
while time.time() < deadline:
    vbs.check_reminders()
    vbs.poll_cycle(seen, first)
    first = False
    vbs.flush_buffers(90)
    cycles += 1
    time.sleep(8)
vbs.check_reminders()
vbs.flush_all_buffers(180)  # старые пачки — отвечаем, свежие — сохраняем в состояние
print(f"[i] Дежурство окончено (проходов: {cycles})")
