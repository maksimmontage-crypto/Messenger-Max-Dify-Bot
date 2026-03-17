import requests
import time
import json
import logging
import sqlite3
import os
from datetime import datetime
import io
import re

# --- Настройки ---
# ЗАПОЛНИТЕ ЭТИ ЗНАЧЕНИЯ СВОИМИ
MAX_ACCESS_TOKEN = "MAX_ACCESS_TOKEN"  # <<< ТОКЕН БОТА MAX
DIFY_API_KEY = "DIFY_API_KEY"      # <<< API KEY DIFY APP
# <<< ВСТАВЬТЕ URL API DIFY (обычно /v1/chat-messages или/chat-messages)
# Пример: http://192.168.0.111:5001/v1/chat-messages
# Пример: http://192.168.0.121/api/v1/chat-messages
DIFY_CHATBOT_ENDPOINT = "DIFY_CHATBOT_ENDPOINT" 

MAX_CHAT_ID_TO_LISTEN =        # ID чата, который бот должен слушать
BASE_MAX_URL = "https://platform-api.max.ru"
DB_PATH = "max_messages.db"                  # Путь к локальной базе данных SQLite
MAX_STORED_MESSAGES = 50                     # Максимальное количество сообщений для хранения в базе

# Логирование - INFO
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s') 
logger = logging.getLogger(__name__)

# Хранилище сессий
# Связывает chat_id в MAX с conversation_id в Dify
session_store = {}

def init_db():
    # Инициализирует SQLite базу данных.
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT UNIQUE, -- mid из MAX
            timestamp INTEGER,      -- Unix timestamp
            sender_id INTEGER,
            text TEXT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Индекс для ускорения поиска по message_id
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_message_id ON messages (message_id)')
    # Индекс для ускорения поиска по timestamp
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON messages (timestamp DESC)')
    conn.commit()
    conn.close()
    logger.info(f"База данных {DB_PATH} инициализирована.")

def get_known_message_ids(limit=MAX_STORED_MESSAGES):
    # Получает последние N message_id из базы данных.
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT message_id FROM messages ORDER BY timestamp DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    known_ids = set(row[0] for row in rows)
    conn.close()
    logger.debug(f"Загружено {len(known_ids)} известных message_id из базы.")
    return known_ids

def store_message_in_db(message_data):
    # Сохраняет сообщение в базу данных.
    message_id = message_data.get('body', {}).get('mid')
    timestamp = message_data.get('timestamp')
    sender_id = message_data.get('sender', {}).get('user_id')
    text = message_data.get('body', {}).get('text')

    if not message_id or not timestamp or not text:
        logger.warning(f"Неполные данные сообщения для сохранения: {message_data}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT OR IGNORE INTO messages (message_id, timestamp, sender_id, text)
            VALUES (?, ?, ?, ?)
        ''', (message_id, timestamp, sender_id, text))
        conn.commit()
        logger.debug(f"Сообщение {message_id} сохранено в базу.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка сохранения сообщения {message_id} в базу: {e}")
    finally:
        conn.close()

def cleanup_old_messages():
    # Удаляет старые сообщения из базы, оставляя только последние N.
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM messages')
    total_count = cursor.fetchone()[0]

    if total_count > MAX_STORED_MESSAGES:
        delete_count = total_count - MAX_STORED_MESSAGES
        logger.info(f"Удаляю {delete_count} старых сообщений из базы (лимит {MAX_STORED_MESSAGES}).")
        cursor.execute(f'''
            DELETE FROM messages WHERE id IN (
                SELECT id FROM messages ORDER BY timestamp ASC LIMIT ?
            )
        ''', (delete_count,))
        conn.commit()
    conn.close()

def make_request(method, url, headers=None, params=None, json_data=None, timeout=10):
    # Универсальная функция для выполнения HTTP-запросов к API MAX.
    full_url = url if url.startswith("http") else BASE_MAX_URL + url
    headers = headers or {}
    
    logger.debug(f"make_request: Выполняю {method} {full_url}") # Логируем только метод и URL
    
    try:
        response = requests.request(
            method=method,
            url=full_url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=timeout
        )
        logger.debug(f"make_request: Ответ: {response.status_code}") # Логируем только статус
        return response
    except requests.exceptions.Timeout as e:
        logger.error(f"make_request: Таймаут при выполнении {method} {full_url}: {e}")
        return None
    except requests.exceptions.ConnectionError as e:
        logger.error(f"make_request: Ошибка подключения при выполнении {method} {full_url}: {e}")
        return None
    except requests.exceptions.HTTPError as e:
        logger.error(f"make_request: HTTP ошибка при выполнении {method} {full_url}: {e.response.status_code} - {e.response.text}")
        return e.response
    except requests.exceptions.RequestException as e:
        logger.error(f"make_request: Общая ошибка HTTP-запроса при выполнении {method} {full_url}: {e}", exc_info=True)
        return None
    # Любое другое исключение (не связанное с requests) будет выброшено дальше
    # и поймано в main()


def get_new_messages_from_chat(chat_id, known_ids):
    # Получает сообщения из чата и возвращает только те, которых нет в known_ids.
    headers = {"Authorization": f"{MAX_ACCESS_TOKEN}"} # <<< ИСПРАВЛЕНО: Убран Bearer
    params = {"chat_id": chat_id, "count": 100} # Запрашиваем до 100, чтобы учесть, что часть может быть известна

    logger.debug(f"get_new_messages_from_chat: Запрашиваю сообщения для чата {chat_id}")
    response = make_request("GET", "/messages", headers=headers, params=params)

    if response and response.status_code == 200:
        try:
            data = response.json()
            messages = data.get("messages", [])
            logger.info(f"get_new_messages_from_chat: Получено {len(messages)} сообщений из чата {chat_id}")

            # Сортируем сообщения по timestamp (возможно, API возвращает в обратном порядке)
            # Сортировка по возрастанию (от старых к новым) важна для правильного отслеживания
            sorted_messages = sorted(messages, key=lambda x: x.get('timestamp', 0))

            new_messages = []
            for msg in sorted_messages:
                msg_id = msg.get('body', {}).get('mid')
                if msg_id and msg_id not in known_ids:
                    new_messages.append(msg)
                    logger.debug(f"get_new_messages_from_chat: Найдено новое сообщение {msg_id}")

            logger.info(f"get_new_messages_from_chat: Найдено {len(new_messages)} новых сообщений.")
            return new_messages
        except json.JSONDecodeError:
            logger.error("get_new_messages_from_chat: Не удалось распарсить JSON ответа от /messages")
            return []
    elif response:
        logger.warning(f"get_new_messages_from_chat: Ошибка /messages: {response.status_code}, {response.text}")
        # Проверяем, является ли ошибка 401 (Unauthorized)
        if response.status_code == 401:
             logger.error("get_new_messages_from_chat: Ошибка 401 Unauthorized. Проверьте MAX_ACCESS_TOKEN и его формат (без 'Bearer').")
             # Возможно, стоит завершить работу скрипта
             raise RuntimeError("get_new_messages_from_chat: Токен MAX недействителен или неправильно сформирован (проверьте формат).")
    else:
        logger.error("get_new_messages_from_chat: Ошибка соединения при получении /messages (response is None).")

    return []


def send_to_dify(query_text, user_id, conversation_id=None):
    # Отправляет текст запроса в Dify (режим streaming) и возвращает собранный ответ и новый conversation_id.
    # Корректно обрабатывает SSE (Server-Sent Events) формат потока.
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}", # <<< Это для DIFY, используем Bearer
        "Content-Type": "application/json"
    }

    payload = {
        "inputs": {},
        "query": query_text,
        "response_mode": "streaming", # <<< Используем streaming
        "conversation_id": conversation_id, # <<< Продолжаем сессию, если есть ID
        "user": str(user_id) # <<< Передаем ID пользователя MAX в Dify
        # "files": [...] # <<< Добавьте, если нужно отправлять файлы
    }

    logger.info(f"send_to_dify: Отправляю в Dify (streaming): Query='{query_text}', ConvID='{conversation_id}', User='{user_id}'")
    
    try:
        # ВАЖНО: Устанавливаем stream=True для потокового чтения
        response = requests.post(
            DIFY_CHATBOT_ENDPOINT, 
            headers=headers,
            json=payload,
            timeout=30,
            stream=True 
        )

        if response.status_code != 200:
            logger.error(f"send_to_dify: Ошибка API Dify: {response.status_code}, {response.text}")
            return f"Ошибка сервиса ИИ (HTTP {response.status_code}).", conversation_id


        # Инициализируем переменные для сборки ответа и обновления conversation_id
        full_answer_parts = []
        new_conversation_id = conversation_id # Если не получим новый, останется старый или None

        # Читаем поток по строкам
        # SSE использует формат: data: <json_data>\n\n
        # Или: event: <event_name>\n data: <json_data>\n\n
        for raw_line in response.iter_lines(decode_unicode=True):
            if raw_line is None:
                # Конец потока?
                logger.debug("send_to_dify: Получен None в iter_lines, возможно, конец потока.")
                break

            # --- Обработка строки SSE ---
            # Примеры строк:
            # data: {"event": "agent_message", "answer": "часть ответа", "conversation_id": "..."}
            # data: {"event": "message_end", "data": {"conversation_id": "...", ...}}

            # Используем простое разделение по ':', как в предыдущем варианте
            if ':' in raw_line:
                parts = raw_line.split(':', 1) # Разделяем на первую ":" и остаток
                if len(parts) == 2:
                    field_name = parts[0].strip()
                    field_value = parts[1].strip()

                    if field_name == "event":
                        # Обработка имени события (например, 'ping', 'text_chunk', 'agent_message', 'message_end')
                        event_name = field_value
                        logger.debug(f"send_to_dify: Получено SSE событие: '{event_name}'")
                        if event_name == "ping":
                            # Игнорируем ping
                            continue
                        elif event_name == "heartbeat":
                            # Игнорируем heartbeat, если есть
                            continue
                        # Другие события можно обработать по необходимости
                    elif field_name == "data":
                        # Обработка данных
                        try:
                            # Убираем ' ' и парсим JSON
                            json_str = field_value
                            # Некоторые SSE могут оборачивать JSON в кавычки, хотя это не по спецификации
                            # Попробуем удалить внешние кавычки, если они есть и это валидный JSON после удаления
                            if json_str.startswith('"') and json_str.endswith('"'):
                                try:
                                    # Пытаемся распарсить строку как JSON, возможно, она была экранирована
                                    potential_json = json.loads(json_str)
                                    if isinstance(potential_json, str):
                                        # Если после loads получили строку, возможно, это и был JSON, просто в виде строки
                                        json_str = potential_json
                                    else:
                                        # Если loads дал не строку, то кавычки были внешними
                                        json_str = potential_json
                                except json.JSONDecodeError:
                                    # Если не получилось распарсить, оставляем как есть
                                    pass
                            
                            chunk_data = json.loads(json_str)
                            
                            # Пример структуры chunk_data для streaming Dify (может отличаться в зависимости от версии Dify и типа агента):
                            # {"event": "text_chunk", "data": {"text": "часть ответа"}}
                            # {"event": "agent_message", "answer": "часть ответа", ...}
                            # {"event": "message_end", "data": {"conversation_id": "...", "task_id": "...", ...}}
                            
                            event_type = chunk_data.get("event")
                            
                            if event_type == "text_chunk":
                                text_part = chunk_data.get("data", {}).get("text", "")
                                if text_part:
                                    full_answer_parts.append(text_part)
                                    logger.info(f"send_to_dify: ПОЛУЧЕНА ЧАСТЬ ТЕКСТА (text_chunk): '{text_part}'")
                            
                            elif event_type == "agent_message":
                                # Обработка ответа от агента
                                answer_part = chunk_data.get("answer", "") # Извлекаем 'answer' из 'agent_message'
                                if answer_part is not None: # Добавляем даже пустую строку, если она есть
                                    full_answer_parts.append(answer_part)
                                    logger.info(f"send_to_dify: ПОЛУЧЕНА ЧАСТЬ ТЕКСТА (agent_message.answer): '{answer_part}'")
                                # Также можно извлечь conversation_id из agent_message, если он там есть
                                possible_new_cid_from_agent_msg = chunk_data.get("conversation_id")
                                if possible_new_cid_from_agent_msg:
                                     logger.debug(f"send_to_dify: Получен conversation_id из agent_message: {possible_new_cid_from_agent_msg}")
                                     # Обычно conversation_id обновляется в message_end, но на всякий случай
                                     # new_conversation_id = possible_new_cid_from_agent_msg

                            elif event_type == "message_end":
                                # В конце потока Dify может отправить сообщение с итоговой информацией
                                end_data = chunk_data.get("data", {})
                                possible_new_cid = end_data.get("conversation_id")
                                if possible_new_cid:
                                    new_conversation_id = possible_new_cid
                                    logger.info(f"send_to_dify: ПОЛУЧЕН НОВЫЙ conversation_id: {new_conversation_id}")
                                # Можно также получить task_id, ответ агента и т.д. из end_data
                                logger.debug(f"send_to_dify: Получено событие message_end") # Логируем для диагностики
                                
                            # Добавьте обработку других типов событий (например, agent_thought), если нужно
                            else:
                                logger.debug(f"send_to_dify: Получено неизвестное событие SSE: '{event_type}', данные: {chunk_data}") # Логируем для диагностики
                                
                        except json.JSONDecodeError as je:
                            logger.error(f"send_to_dify: Ошибка парсинга JSON из поля 'data' SSE: {je}, строка: {field_value}")
                            # Продолжаем читать, возможно, следующая строка будет корректной
                            continue
                        except Exception as e:
                            logger.error(f"send_to_dify: Неожиданная ошибка при обработке поля 'data' SSE: {e}")
                            # Прерываем чтение потока при критической ошибке
                            break 
                    else:
                        logger.debug(f"send_to_dify: Неподдерживаемое поле SSE: '{field_name}', значение: '{field_value}'")
                else:
                    # Строка содержит ':', но не разделилась на 2 части (например, начинается с ':')
                    logger.debug(f"send_to_dify: Непонятная строка SSE с двоеточием: '{raw_line}'")
            else:
                # Строка не содержит ':', возможно, это просто строка данных без указания поля (редко для SSE, но возможно?)
                # Или это пустая строка, которая могла быть разделителем в оригинальном протоколе.
                logger.debug(f"send_to_dify: Строка SSE без двоеточия: '{raw_line}'")
                # Попробуем распарсить как JSON целиком, если она не пустая
                if raw_line.strip():
                    try:
                        chunk_data = json.loads(raw_line)
                        # Предполагаем, что это может быть полный объект без поля 'event' или 'data'
                        # Это не стандартное поведение SSE, но проверим.
                        event_type = chunk_data.get("event")
                        if event_type == "text_chunk":
                            text_part = chunk_data.get("data", {}).get("text", "")
                            if text_part:
                                full_answer_parts.append(text_part)
                                logger.info(f"send_to_dify: ПОЛУЧЕНА ЧАСТЬ ТЕКСТА (из raw JSON text_chunk): '{text_part}'")
                        elif event_type == "agent_message":
                             answer_part = chunk_data.get("answer", "")
                             if answer_part is not None: # Добавляем даже пустую строку, если она есть
                                full_answer_parts.append(answer_part)
                                logger.info(f"send_to_dify: ПОЛУЧЕНА ЧАСТЬ ТЕКСТА (из raw JSON agent_message.answer): '{answer_part}'")
                        elif event_type == "message_end":
                            end_data = chunk_data.get("data", {})
                            possible_new_cid = end_data.get("conversation_id")
                            if possible_new_cid:
                                new_conversation_id = possible_new_cid
                                logger.info(f"send_to_dify: ПОЛУЧЕН НОВЫЙ conversation_id (из raw JSON): {new_conversation_id}")
                            logger.debug(f"send_to_dify: Получено событие message_end (из raw JSON)")
                        else:
                            logger.debug(f"send_to_dify: Получен неизвестный объект (из raw JSON): {chunk_data}")
                    except json.JSONDecodeError:
                        logger.debug(f"send_to_dify: Строка без двоеточия не является JSON: '{raw_line}'")


        # Собираем полный ответ
        full_answer = "".join(full_answer_parts)
        
        if not full_answer:
             logger.warning("send_to_dify: От Dify получен поток, но текст ответа пуст.")
             full_answer = "Ответ от ИИ пуст."

        logger.info(f"send_to_dify: Полный ответ от Dify собран. Длина: {len(full_answer)}. ConvID: {new_conversation_id}")
        return full_answer, new_conversation_id

    except requests.exceptions.Timeout:
        logger.error("send_to_dify: Таймаут при ожидании ответа от Dify.")
        return "Таймаут соединения с ИИ.", conversation_id
    except requests.exceptions.ConnectionError as ce:
        logger.error(f"send_to_dify: Ошибка подключения к Dify: {ce}")
        return "Ошибка подключения к ИИ.", conversation_id
    except Exception as e:
        logger.error(f"send_to_dify: Неожиданная ошибка при вызове Dify API: {e}")
        logger.exception(e) # Дополнительно логируем traceback
        return "Произошла внутренняя ошибка при запросе к ИИ.", conversation_id


def send_message_to_max(chat_id, text, format_type=None):
    # Отправляет сообщение пользователю в MAX.
    # Если текст длиннее 3000 символов, разбивает его и отправляет по частям.
    headers = {
        "Authorization": f"{MAX_ACCESS_TOKEN}", 
        "Content-Type": "application/json"
    }

    # --- НАЧАЛО ИЗМЕНЕНИЯ ---
    max_length = 3000
    text_parts = []
    if len(text) <= max_length:
        text_parts = [text]
    else:
        # Разбиваем текст на части по 3000 символов
        text_parts = [text[i:i + max_length] for i in range(0, len(text), max_length)]

    for part in text_parts:
        payload = {"text": part}
        if format_type:
            payload["format"] = format_type # Используем переданный формат

        params = {"chat_id": chat_id}

        logger.info(f"send_message_to_max: Отправляю в MAX: '{part[:50]}...' -> ChatID: {chat_id}")
        response = make_request("POST", "/messages", headers=headers, json_data=payload, params=params)

        if response:
            if response.status_code == 200:
                logger.info("send_message_to_max: Сообщение успешно отправлено в MAX.")
                # В идеале, можно проверить, что именно отправлено, но для простоты считаем успехом
            else:
                logger.error(f"send_message_to_max: Ошибка отправки в MAX: {response.status_code}, {response.text}")
                # Если одна часть не отправилась, можно либо остановиться, либо продолжить с другими.
                # Пока продолжим, но логируем ошибку.
        else:
            logger.error("send_message_to_max: Ошибка соединения при отправке в MAX.")
            # Аналогично, ошибка соединения для одной части не останавливает отправку других.

    # Возвращаем True, если хотя бы одна часть была отправлена (предполагаем, что все были отправлены)
    # Возвращаем False, если не было попыток отправки (text был пустой)
    # Или можно ввести более сложную логику возврата в зависимости от числа отправленных/неотправленных частей.
    # Для простоты, возвращаем True, если текст не пустой.
    return bool(text_parts) # Вернёт False только если text_parts пустой (т.е. text был "")
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---


def handle_message(message_data):
    # Обрабатывает одно полученное сообщение (message_data).
    # recipient может быть чатом или пользователем
    chat_id_from_recipient = message_data.get("recipient", {}).get("chat_id")
    user_id_from_recipient = message_data.get("recipient", {}).get("user_id") # Для диалога
    sender_id = message_data.get("sender", {}).get("user_id")

    # Проверяем, что сообщение из нужного чата
    # Используем chat_id_from_recipient, так как update приходит в чат
    if chat_id_from_recipient != MAX_CHAT_ID_TO_LISTEN:
        logger.debug(f"handle_message: Сообщение из другого чата {chat_id_from_recipient}, игнорирую.")
        return

    # Извлекаем текст сообщения из body
    message_body = message_data.get("body", {})
    user_message_text = message_body.get("text")

    if not user_message_text:
        logger.info("handle_message: Получено сообщение без текста (например, медиа), пропускаю.")
        return # Игнорируем сообщения без текста (например, медиа)

    # Проверяем, что сообщение не от самого бота (во избежание зацикливания)
    # Нужно получить id бота
    headers = {"Authorization": f"{MAX_ACCESS_TOKEN}"} # <<< ИСПРАВЛЕНО: Убран Bearer
    bot_info_resp = make_request("GET", "/me", headers=headers)
    if bot_info_resp and bot_info_resp.status_code == 200:
        try:
            bot_info = bot_info_resp.json()
            bot_id = bot_info.get("user_id")
            if sender_id == bot_id:
                 logger.debug(f"handle_message: Получено сообщение от самого бота, игнорирую.")
                 return # Игнорируем сообщения от себя
        except json.JSONDecodeError:
             logger.error("handle_message: Не удалось получить info о боте для проверки sender, продолжаю обработку.")
             # Продолжаем обработку, даже если не смогли проверить, чтобы не терять сообщения

    logger.info(f"handle_message: Получено сообщение от User {sender_id} в Chat {chat_id_from_recipient}: '{user_message_text}'")

    # Получаем ID сессии Dify для этого чата
    # Используем chat_id_from_recipient как ключ сессии
    session_key = str(chat_id_from_recipient)
    dify_conversation_id = session_store.get(session_key)

    # Отправляем сообщение в Dify
    dify_response_text, new_dify_conversation_id = send_to_dify(user_message_text, sender_id, dify_conversation_id)

    # Сохраняем новый ID сессии Dify, если он изменился
    if new_dify_conversation_id:
        session_store[session_key] = new_dify_conversation_id
        logger.debug(f"handle_message: Обновлен ID сессии для Chat {chat_id_from_recipient}: {new_dify_conversation_id}")

    # Отправляем ответ от Dify обратно в MAX
    success = send_message_to_max(chat_id_from_recipient, dify_response_text, format_type="markdown") # Можно указать html
    if success:
        logger.info("handle_message: Ответ успешно отправлен пользователю в MAX.")
    else:
        logger.error("handle_message: Не удалось отправить ответ пользователю в MAX.")

def main_loop():
    # Основной цикл работы бота.
    logger.info(f"main: Запуск бота. Слушаю чат: {MAX_CHAT_ID_TO_LISTEN}, Dify на: {DIFY_CHATBOT_ENDPOINT.split('/')[2]}")
    
    # Инициализация базы данных
    init_db()

    while True:
        try:
            # Загружаем известные message_id из базы данных
            known_message_ids = get_known_message_ids()

            # Получаем новые сообщения из чата
            new_messages = get_new_messages_from_chat(MAX_CHAT_ID_TO_LISTEN, known_message_ids)

            if new_messages:
                logger.info(f"main: Обрабатываю {len(new_messages)} новых сообщений.")
                for msg in new_messages:
                    # Сохраняем сообщение в базу ДО обработки, чтобы избежать дубликатов
                    # в случае ошибки обработки.
                    store_message_in_db(msg)
                    # Обрабатываем сообщение
                    handle_message(msg)
                
                # После обработки всех новых сообщений, очищаем базу
                cleanup_old_messages()
            else:
                logger.debug("main: Нет новых сообщений.")

            # Пауза между polling-запросами
            time.sleep(10) # Пауза 10 секунд перед следующим опросом

        except KeyboardInterrupt:
            logger.info("main: Работа бота остановлена пользователем.")
            break
        except RuntimeError as e: # Перехватываем ошибку с токеном
             logger.critical(e)
             break # Останавливаем скрипт при критической ошибке
        except Exception as e:
            logger.error(f"main: Неожиданная ошибка в основном цикле: {e}")
            logger.exception(e) # Дополнительно логируем traceback
            time.sleep(5) # Пауза перед повтором

if __name__ == "__main__":
    main_loop()
