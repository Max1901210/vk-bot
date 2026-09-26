"""ДИАГНОСТИКА: печатает в журнал каждое сообщение и каждую ошибку."""
import threading
import time
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import vk_bot_simple as bot

START_TS = time.time()

_orig_handle = bot.handle_message


def _loud_handle(msg: dict) -> None:
    try:
        print(f"[СООБЩЕНИЕ] от {msg.get('from_id')}: "
              f"{(msg.get('text') or '')[:80]!r} "
              f"(вложений: {len(msg.get('attachments') or [])})", flush=True)
        _orig_handle(msg)
        print("[ОК] ответ отправлен без ошибок", flush=True)
    except Exception as e:
        print("[ОШИБКА ОТВЕТА]", repr(e)[:500], flush=True)


bot.handle_message = _loud_handle


def _loop():
    while True:
        try:
            bot.main()
        except SystemExit:
            time.sleep(5)
        except Exception as e:
            print("[перезапуск]", repr(e)[:300], flush=True)
            time.sleep(5)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = f"бот жив, работает {int(time.time() - START_TS)} с (диагностика)".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    threading.Thread(target=_loop, daemon=True).start()
    port = int(os.environ.get("PORT", "10000"))
    print("диагностика: веб-пульс на порту", port, flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
