"""VK-бот 24/7: веб-пульс для хостинга + сам бот в фоновом потоке.

Хостинг видит веб-страницу (по ней приходит «пульс»), бот в фоне
читает сообщения ВК и отвечает.
Запуск: python app.py
Нужны переменные окружения: VK_TOKEN, VK_GROUP_ID, AI_KEY
(необязательно: GH_SYNC_REPO и GH_TOKEN — память переживает перезапуски).
"""
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import vk_bot_simple as bot

START_TS = time.time()


def _loop():
    while True:
        try:
            bot.main()
        except SystemExit:
            time.sleep(10)
        except Exception as e:
            print("[автоперезапуск]", repr(e), flush=True)
            time.sleep(10)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        up = int(time.time() - START_TS)
        body = f"бот жив, работает {up} с".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    threading.Thread(target=_loop, daemon=True).start()
    port = int(os.environ.get("PORT", "10000"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("веб-пульс на порту", port, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
