#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ПРОСТОЙ ИИ-БОТ ДЛЯ ВКОНТАКТЕ (один файл, без установки библиотек)

Что умеет:
  • отвечает на сообщения через бесплатные ИИ (OpenRouter или Google Gemini)
  • помнит контекст разговора
  • показывает «печатает…», понимает картинки, команды /start /help /new /model

Как запустить:
  1) Установи Python с python.org (галочка "Add python.exe to PATH"!)
  2) В командной строке перейди в папку с этим файлом и выполни:
         python vk_bot_simple.py
  3) При первом запуске бот сам спросит ключи и сохранит их.
     Ключ VK:  Управление сообществом -> Настройки -> Ключи доступа
               (галочки: Сообщения, Фотографии)
     Ключ ИИ:  openrouter.ai/settings/keys  (работает без VPN)
               или aistudio.google.com/apikey (нужен VPN в РФ)
  4) Напиши сообществу /start
"""
import base64
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request

try:  # чтобы русские буквы в консоли Windows печатались без ошибок
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "bot_settings.json")
DB_FILE = os.path.join(SCRIPT_DIR, "bot_history.db")
VK_API = os.environ.get("VK_API_BASE", "https://api.vk.com/method/")

# Провайдеры ИИ: имя -> (адрес API, модель, запасные модели, где взять ключ)
PROVIDERS = {
    "openrouter": {
        "base": os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        "model": "nvidia/nemotron-3.5-lightning:free",
        "fallbacks": [
            "nvidia/nemotron-3-super-120b-a12b:free",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "qwen/qwen3.8-27b:free",
        ],
        "key_url": "https://openrouter.ai/settings/keys",
        "hint": "работает из России без VPN",
    },
    "gemini": {
        "base": os.environ.get("GEMINI_BASE_URL",
                               "https://generativelanguage.googleapis.com/v1beta/openai"),
        "model": "gemini-2.5-flash",
        "fallbacks": ["gemini-2.5-flash-lite"],
        "key_url": "https://aistudio.google.com/apikey",
        "hint": "в России нужен VPN",
    },
}
VISION_RE = ("gemini", "llama-4", "llama-4-maverick", "vision", "vl", "qwen2.5-vl")
# модели со «зрением»: первая — быстрая, без долгих размышлений
VISION_MODELS = [
    ("dots-studio/dots-3-note-preview:free", 4000),
    ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", 12000),
    ("google/gemma-4-31b-it:free", 4000),
]

# накопитель фото: присылаешь по одному — бот копит и отвечает на все сразу
PHOTO_BUFFERS: dict[int, dict] = {}

SYSTEM_PROMPT = (
    "Ты — дружелюбный ИИ-ассистент внутри ВКонтакте. Отвечай на языке "
    "пользователя (по умолчанию по-русски), по делу, без воды. Код оформляй "
    "блоками ``` с указанием языка. Не выдумывай факты."
)

# ------------------------------ настройки --------------------------------- #

def load_settings() -> dict:
    s: dict = {}
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                s = json.load(f)
        except Exception:
            pass
    # переменные окружения имеют приоритет (нужно для облачного запуска)
    for key, env in (("vk_token", "VK_TOKEN"), ("group_id", "VK_GROUP_ID"),
                     ("api_key", "AI_KEY"), ("provider", "PROVIDER")):
        if os.environ.get(env):
            s[key] = os.environ[env]
    if not s.get("provider"):
        s["provider"] = "openrouter"  # разумное значение по умолчанию
    return s


def save_settings(s: dict) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def setup_interactive(s: dict) -> dict:
    """Первый запуск: спрашиваем ключи и сохраняем."""
    print("=" * 60)
    print("  ПЕРВЫЙ ЗАПУСК — нужны три настройки (сохраню в файл)")
    print("=" * 60)
    if not s.get("vk_token"):
        print("\n1) Ключ сообщества VK (vk1.a...):")
        print("   Управление сообществом -> Настройки -> Ключи доступа")
        print("   (создай ключ с галочками «Сообщения» и «Фотографии»)")
        s["vk_token"] = input("   Вставь ключ и нажми Enter: ").strip()
    if not s.get("group_id"):
        s["group_id"] = input("2) ID сообщества (число из vk.ru/clubXXXX): ").strip()
    if not s.get("provider"):
        print("\n3) Откуда брать нейросеть:")
        print("   [1] OpenRouter — " + PROVIDERS["openrouter"]["hint"]
              + "  <-- рекомендую")
        print("   [2] Google Gemini — " + PROVIDERS["gemini"]["hint"])
        choice = input("   Выбери 1 или 2 (Enter = 1): ").strip()
        s["provider"] = "gemini" if choice == "2" else "openrouter"
    if not s.get("api_key"):
        p = PROVIDERS[s["provider"]]
        print(f"\n4) Ключ нейросети ({s['provider']}, взять тут: {p['key_url']})")
        s["api_key"] = input("   Вставь ключ и нажми Enter: ").strip()

    ok = s.get("vk_token") and s.get("group_id", "").isdigit() and s.get("api_key")
    if not ok:
        print("\n[!] Что-то не заполнено или ID не число. Запусти ещё раз.")
        sys.exit(1)
    save_settings(s)
    print("\n[+] Настройки сохранены в", SETTINGS_FILE)
    return s


# ------------------------------ HTTP -------------------------------------- #

def http_post(url: str, data=None, json_body=None, headers=None, timeout=60):
    if json_body is not None:
        body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        hdrs = {"Content-Type": "application/json"}
    else:
        body = urllib.parse.urlencode(data or {}).encode("utf-8")
        hdrs = {"Content-Type": "application/x-www-form-urlencoded"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def http_get_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "vk-bot"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


# ------------------------------ VK API ------------------------------------ #

def vk(method: str, **params):
    params["access_token"] = SETTINGS["vk_token"]
    params["v"] = "5.199"
    data = http_post(VK_API + method, data=params)
    if isinstance(data, dict) and "error" in data:
        err = data["error"]
        raise RuntimeError(f"VK {err.get('error_code')}: {err.get('error_msg')}")
    return data.get("response")


def vk_send(peer_id: int, text: str, keyboard: dict | None = None):
    params = {"peer_id": peer_id, "message": text, "random_id": int(time.time() * 1000) % 2**31}
    if keyboard:
        params["keyboard"] = json.dumps(keyboard, ensure_ascii=False)
    return vk("messages.send", **params)


def vk_edit(peer_id: int, conv_id: int, text: str) -> bool:
    try:
        vk("messages.edit", peer_id=peer_id, conversation_message_id=conv_id, message=text)
        return True
    except RuntimeError as e:
        print("   (не удалось отредактировать:", e, ")")
        return False


def vk_typing(peer_id: int) -> None:
    try:
        vk("messages.setActivity", peer_id=peer_id, type="typing")
    except RuntimeError:
        pass


# ------------------------------ история ----------------------------------- #

DB_LOCK = threading.Lock()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS h ("
        " uid INTEGER, role TEXT, content TEXT, id INTEGER PRIMARY KEY AUTOINCREMENT)"
    )
    return conn


def history_get(uid: int, limit: int = 12) -> list:
    with DB_LOCK:
        c = db()
        rows = c.execute(
            "SELECT role, content FROM (SELECT id, role, content FROM h "
            "WHERE uid=? ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (uid, limit),
        ).fetchall()
        c.close()
    return [{"role": r[0], "content": r[1]} for r in rows]


def history_add(uid: int, user_text: str, answer: str) -> None:
    with DB_LOCK:
        c = db()
        c.execute("INSERT INTO h (uid, role, content) VALUES (?,?,?)", (uid, "user", user_text))
        c.execute("INSERT INTO h (uid, role, content) VALUES (?,?,?)", (uid, "assistant", answer))
        c.execute(
            "DELETE FROM h WHERE uid=? AND id NOT IN "
            "(SELECT id FROM h WHERE uid=? ORDER BY id DESC LIMIT 24)", (uid, uid)
        )
        c.commit()
        c.close()


def history_clear(uid: int) -> None:
    with DB_LOCK:
        c = db()
        c.execute("DELETE FROM h WHERE uid=?", (uid,))
        c.commit()
        c.close()


# ------------------------------ нейросеть --------------------------------- #

def strip_think(text: str) -> str:
    import re
    text = re.sub(r"<(think|thinking|reasoning)>.*?</\1>", "", text, flags=re.S | re.I)
    text = re.sub(r"<(think|thinking|reasoning)>.*$", "", text, flags=re.S | re.I)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def md_to_text(text: str) -> str:
    """Превращаем Markdown в обычный текст (VK его не рисует)."""
    import re
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"```[\w+-]*\n?", "", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text, flags=re.S)
    text = re.sub(r"~~(.+?)~~", r"\1", text, flags=re.S)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"\1", text)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def ai_chat(messages: list) -> str:
    """Задаём вопрос нейросети (не стриминг — просто ждём ответ)."""
    p = PROVIDERS[SETTINGS["provider"]]
    models = [p["model"]] + p["fallbacks"]
    headers = {"Authorization": "Bearer " + SETTINGS["api_key"]}
    if SETTINGS["provider"] == "openrouter":
        headers["X-Title"] = "VK AI Bot"
    last_err = "unknown"
    saw_429 = False
    for model in models:
        body = {"model": model, "messages": messages, "max_tokens": 8000}
        url = p["base"] + "/chat/completions"
        try:
            data = http_post(url, json_body=body, headers=headers, timeout=90)
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            if content and content.strip():
                return content.strip()
            last_err = "пустой ответ"
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                detail = ""
            last_err = f"HTTP {e.code}: {detail}"
            if e.code == 429:
                saw_429 = True
                continue  # лимит — пробуем следующую модель
            if e.code in (401, 403):
                raise RuntimeError("Ключ нейросети не принят (401/403). Проверь его: "
                                   "удали файл bot_settings.json и запусти заново.")
            continue  # модель недоступна — пробуем следующую
        except Exception as e:
            last_err = repr(e)
    if saw_429:
        raise RuntimeError("Все бесплатные модели сейчас перегружены (лимит). "
                           "Подожди минуту-другую и попробуй снова.")
    raise RuntimeError(f"Нейросеть не ответила ({last_err}).")


def ai_vision(prompt: str, image_urls: list) -> str:
    """Вопрос про картинки: пробуем несколько моделей со «зрением» по очереди."""
    parts = [{"type": "text", "text": prompt or "Что на картинке?"}]
    loaded = 0
    for u in image_urls[:10]:
        try:
            raw = http_get_bytes(u)
            b64 = base64.b64encode(raw).decode()
            mime = "image/png" if ".png" in u.lower() else "image/jpeg"
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{mime};base64,{b64}"}})
            loaded += 1
        except Exception as e:
            print("   (картинка не скачалась:", e, ")")
    if not loaded:
        raise RuntimeError("Не удалось загрузить картинку, попробуй ещё раз.")
    p = PROVIDERS[SETTINGS["provider"]]
    headers = {"Authorization": "Bearer " + SETTINGS["api_key"],
               "Content-Type": "application/json"}
    if SETTINGS["provider"] == "openrouter":
        headers["X-Title"] = "VK AI Bot"

    last_err = "unknown"
    for model, max_tokens in VISION_MODELS:
        body = {"model": model, "messages": [{"role": "user", "content": parts}],
                "max_tokens": max_tokens}
        for attempt in range(2):  # две попытки на модель
            try:
                data = http_post(p["base"] + "/chat/completions", json_body=body,
                                 headers=headers, timeout=300)
                ch = (data.get("choices") or [{}])[0]
                content = (ch.get("message") or {}).get("content", "")
                if content and content.strip():
                    return content.strip()
                last_err = "пустой ответ"
                if ch.get("finish_reason") == "length" and max_tokens < 12000:
                    body["max_tokens"] = 12000  # размышления съели лимит — добавим
                    continue
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}"
                if e.code in (401, 403):
                    raise RuntimeError("Ключ нейросети не принят. Проверь AI_KEY.")
                break  # эта модель недоступна — следующая
            except Exception as e:
                last_err = repr(e)[:120]
            time.sleep(2)
    raise RuntimeError("Модели для картинок сейчас перегружены. "
                       "Подожди 5 минут и пришли фото снова. (" + last_err + ")")


def model_supports_vision() -> bool:
    p = PROVIDERS[SETTINGS["provider"]]
    return any(k in p["model"].lower() for k in VISION_RE)


# ------------------------------ обработка --------------------------------- #

WELCOME = (
    "Привет! Я ИИ-бот 🤖 Напиши мне что угодно — отвечу.\n\n"
    "📸 Можно кинуть СРАЗУ НЕСКОЛЬКО фото с заданиями + текст "
    "(например: «реши все») — отвечу на все одним сообщением.\n"
    "Фото без текста накапливаю и жду задание (~2 минуты).\n\n"
    "/new — начать разговор заново\n"
    "/model — какая модель работает\n"
    "Помню контекст разговора."
)


def typing_loop(peer_id: int, stop: threading.Event) -> None:
    while not stop.is_set():
        vk_typing(peer_id)
        stop.wait(4.0)


DEFAULT_PHOTO_TASK = ("На фотографиях задания. Реши ВСЕ по порядку кратко: "
                      "краткое условие, решение, ответ. По-русски.")


def answer_photos(peer_id: int, uid: int, urls: list, task: str) -> None:
    """Отвечает одним сообщением на пачку фото."""
    try:
        answer = ai_vision(task or DEFAULT_PHOTO_TASK, urls)
    except RuntimeError as e:
        vk_send(peer_id, "😔 " + str(e))
        return
    except Exception as e:
        vk_send(peer_id, "😔 Непредвиденная ошибка: " + repr(e))
        return
    for part in split_reply(answer):
        vk_send(peer_id, part)


def flush_buffers(max_age: float = 110.0) -> None:
    """Если задание так и не написали — отвечаем на накопленные фото сами."""
    now = time.time()
    for uid in list(PHOTO_BUFFERS):
        b = PHOTO_BUFFERS[uid]
        if b["urls"] and now - b["ts"] > max_age:
            PHOTO_BUFFERS.pop(uid, None)
            threading.Thread(
                target=answer_photos,
                args=(b.get("peer") or uid, uid, b["urls"], ""),
                daemon=True,
            ).start()


def flush_all_buffers() -> None:
    """Незакрытые пачки фото — отвечаем сразу (нужно в облаке перед выходом)."""
    for uid in list(PHOTO_BUFFERS):
        b = PHOTO_BUFFERS.pop(uid, None)
        if b and b["urls"]:
            try:
                answer_photos(b.get("peer") or uid, uid, b["urls"], "")
            except Exception as e:
                print("   (flush:", e, ")")


def split_reply(text: str, limit: int = 3900) -> list:
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else ["(пустой ответ)"]
    parts, rest = [], text
    while rest:
        if len(rest) <= limit:
            parts.append(rest)
            break
        cut = rest.rfind("\n\n", 0, limit)
        if cut < limit // 3:
            cut = rest.rfind("\n", 0, limit)
        if cut < limit // 3:
            cut = limit
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return [p for p in parts if p]


def handle_message(msg: dict) -> None:
    peer_id = msg.get("peer_id")
    uid = msg.get("from_id")
    if not peer_id or not uid or uid < 0:
        return
    text = (msg.get("text") or "").strip()
    photos = []
    for a in msg.get("attachments") or []:
        if a.get("type") == "photo":
            best, area = "", 0
            for s in (a.get("photo") or {}).get("sizes") or []:
                sq = int(s.get("width", 0)) * int(s.get("height", 0))
                if sq > area and s.get("url"):
                    best, area = s["url"], sq
            if best:
                photos.append(best)

    if text.lower() in ("/start", "начать"):
        vk_send(peer_id, WELCOME)
        return
    if text.lower() in ("/help", "?"):
        vk_send(peer_id, WELCOME)
        return
    if text.lower() in ("/new", "/reset"):
        history_clear(uid)
        vk_send(peer_id, "Начали заново! 🧹")
        return
    if text.lower() == "/model":
        p = PROVIDERS[SETTINGS["provider"]]
        vk_send(peer_id, f"Модель: {p['model']}\nПровайдер: {SETTINGS['provider']}")
        return
    if not text and not photos:
        return

    # фото без текста — копим, чтобы ответить на ВСЕ сразу одним ответом
    if photos and not text:
        buf = PHOTO_BUFFERS.setdefault(uid, {"urls": [], "peer": peer_id, "ts": 0})
        buf["urls"].extend(photos)
        buf["ts"] = time.time()
        n = len(buf["urls"])
        vk_send(peer_id,
                f"📸 Принял фото №{n}. Кидай следующие, а когда закончишь — "
                "напиши задание текстом (например: «реши все номера»). "
                "Отвечу на все фото сразу.\n"
                "Если задания не будет — сам отвечу через ~2 минуты.")
        return

    # есть текст: забираем накопленные фото (если были) — отвечаем на всё вместе
    buffered: list = []
    if text:
        buf = PHOTO_BUFFERS.pop(uid, None)
        if buf:
            buffered = buf["urls"]

    stop = threading.Event()
    raw_store: str | None = None
    t = threading.Thread(target=typing_loop, args=(peer_id, stop), daemon=True)
    t.start()

    conv_id = None
    try:
        sent = vk_send(peer_id, "⏳ Думаю…")
        if isinstance(sent, dict):
            conv_id = sent.get("conversation_message_id")
    except RuntimeError:
        pass

    try:
        if photos or buffered:
            prompt = text or DEFAULT_PHOTO_TASK
            answer = ai_vision(prompt, (photos + buffered)[:10])
            user_text = text or f"(фото: {len(photos or buffered)} шт.)"
        else:
            messages = ([{"role": "system", "content": SYSTEM_PROMPT}]
                        + history_get(uid) + [{"role": "user", "content": text}])
            raw_answer = ai_chat(messages)
            raw_store = raw_answer
            answer = md_to_text(strip_think(raw_answer))
            user_text = text
    except RuntimeError as e:
        answer = "😔 " + str(e)
        user_text = None
    except Exception as e:
        answer = "😔 Непредвиденная ошибка: " + repr(e)
        user_text = None
    finally:
        stop.set()

    parts = split_reply(answer)
    first_ok = False
    if conv_id:
        first_ok = vk_edit(peer_id, conv_id, parts[0])
    for i, part in enumerate(parts):
        if i == 0 and first_ok:
            continue
        vk_send(peer_id, part)

    if user_text:
        history_add(uid, user_text, raw_store or answer)


# ------------------------------ главный цикл ------------------------------- #

SETTINGS: dict = {}


def poll_cycle(seen: dict, first: bool) -> None:
    """Один проход: смотрим диалоги и отвечаем на новые сообщения."""
    try:
        resp = vk("messages.getConversations", count=20)
    except RuntimeError as e:
        print("   (опрос диалогов:", e, ")")
        return
    items = (resp or {}).get("items") or []
    for item in items:
        conv = item.get("conversation") or {}
        msg = item.get("last_message") or {}
        peer_id = (conv.get("peer") or {}).get("id") or conv.get("peer_id")
        mid = msg.get("id")
        if not peer_id or not mid:
            continue
        if first:
            seen[peer_id] = mid  # стартовую точку запомнили
            # но если сообщение свежее (моложе часа) — ответим и на него
            if msg.get("from_id", 0) > 0 and time.time() - msg.get("date", 0) < 3600:
                msg["peer_id"] = peer_id
                handle_message(msg)  # в облаке — сразу, без потока
            continue
        if mid != seen.get(peer_id) and msg.get("from_id", 0) > 0:
            seen[peer_id] = mid
            msg["peer_id"] = peer_id
            handle_message(msg)
            try:  # отметим прочитанным (необязательно)
                vk("messages.markAsRead", peer_id=peer_id)
            except RuntimeError:
                pass
    if first and not items:
        print("[i] Жду первое сообщение... Напиши сообществу /start")


def poll_loop() -> None:
    """Резервный режим: опрашиваем диалоги каждые ~2.5 сек.
    Работает без Long Poll — достаточно права «Сообщения»."""
    print("[i] Long Poll недоступен — включаю резервный режим (опрос диалогов).")
    print("[i] Всё работает, просто ответы приходят с задержкой 2-3 секунды.")
    seen: dict[int, int] = {}
    first = True
    while True:
        poll_cycle(seen, first)
        first = False
        flush_buffers()
        time.sleep(2.5)


def main() -> None:
    global SETTINGS
    SETTINGS = load_settings()
    if not (SETTINGS.get("vk_token") and SETTINGS.get("api_key")):
        SETTINGS = setup_interactive(SETTINGS)

    try:
        lp = vk("groups.getLongPollServer", group_id=int(SETTINGS["group_id"]))
    except RuntimeError as e:
        print("[i] Long Poll недоступен:", e)
        poll_loop()
        return

    ts = str(lp["ts"])
    print("[+] Бот запущен! Напиши сообществу /start")
    print("    Провайдер ИИ:", SETTINGS["provider"],
          "| модель:", PROVIDERS[SETTINGS["provider"]]["model"])
    print("    Остановить: Ctrl+C или закрой окно")

    while True:
        try:
            data = http_post(
                lp["server"],
                data={"act": "a_check", "key": lp["key"], "ts": ts,
                      "wait": 25, "mode": 2, "version": 3},
                timeout=45,
            )
        except Exception as e:
            print("    сеть моргнула:", e)
            time.sleep(3)
            lp = vk("groups.getLongPollServer", group_id=int(SETTINGS["group_id"]))
            ts = str(lp["ts"])
            continue

        if "failed" in data:
            if data.get("failed") == 2 and "ts" in data:
                ts = str(data["ts"])
            else:
                lp = vk("groups.getLongPollServer", group_id=int(SETTINGS["group_id"]))
                ts = str(lp["ts"])
            continue

        ts = str(data.get("ts", ts))
        for upd in data.get("updates") or []:
            if upd.get("type") == "message_new":
                msg = (upd.get("object") or {}).get("message") or {}
                threading.Thread(target=handle_message, args=(msg,), daemon=True).start()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nБот остановлен.")
    except RuntimeError as e:
        print("\n[ОШИБКА]", e)
        print("Подсказки:")
        print(" • VK 5: нет права «Сообщения» у ключа или не включён Long Poll API")
        print(" • VK 15: токен чужой/неверный; пересоздай ключ")
        input("Нажми Enter, чтобы закрыть окно...")
