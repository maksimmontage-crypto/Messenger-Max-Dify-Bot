# Messenger-Max-Dify-Bot
# MAX API Chatbot with Dify Integration

Этот проект представляет собой Python-скрипт, который действует как мост между [MAX API](https://dev.max.ru/) (чат-платформа) и локальным ИИ-агентом, развернутым на [Dify](https://dify.ai/). Бот получает сообщения из указанного чата MAX, отправляет их в Dify для обработки и генерации ответа, а затем возвращает ответ обратно в MAX.

## Особенности

*   **Получение сообщений:** Использует `GET /messages` для опроса (polling) сообщений из конкретного чата MAX.
*   **Отслеживание сообщений:** Использует локальную SQLite базу данных для хранения идентификаторов обработанных сообщений, предотвращая их повторную обработку.
*   **Интеграция с Dify:** Отправляет текстовые сообщения в API Dify в режиме `streaming` и корректно обрабатывает Server-Sent Events (SSE) для получения ответа.
*   **Управление сессиями:** Сохраняет и передает `conversation_id` между запросами к Dify, обеспечивая контекст разговора.
*   **Разбиение длинных сообщений:** Автоматически разбивает длинные ответы от Dify (превышающие 3000 символов) на несколько сообщений при отправке в MAX.
*   **Запуск как сервис:** 

## Требования

*   Python 3.7+
*   Внешние зависимости:
    *   `requests`
    *   `sqlite3` (обычно входит в стандартную библиотеку Python)

## Установка

1.  **Клонируйте репозиторий:**
    ```bash
    git clone https://github.com/maksimmontage-crypto/Messenger-Max-Dify-Bot.git
    cd Messenger-Max-Dify-Bot
    ```

2.  **Создайте и активируйте виртуальное окружение (рекомендуется):**
    ```bash
    python3 -m venv venv
    source venv/bin/activate # On Windows: venv\Scripts\activate
    ```

3.  **Установите зависимости:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Настройте конфигурацию:**
    *   Откройте файл `max-api.py`.
    *   Найдите строки с `YOUR_MAX_BOT_TOKEN_HERE`, `YOUR_DIFY_API_KEY_HERE`, `DIFY_CHATBOT_ENDPOINT`, `MAX_CHAT_ID_TO_LISTEN`.
    *   Замените `YOUR_MAX_BOT_TOKEN_HERE` на токен вашего бота MAX (его можно найти в разделе "Чат-боты" -> "Интеграция" -> "Получить токен" на платформе MAX).
    *   Замените `YOUR_DIFY_API_KEY_HERE` на API Key вашего приложения Dify (его можно сгенерировать в настройках приложения Dify).
    *   Укажите правильный `DIFY_CHATBOT_ENDPOINT` (например, `http://your_dify_host:port/v1/chat-messages`).
    *   Укажите `MAX_CHAT_ID_TO_LISTEN` - ID чата, который бот будет слушать.
    *   Убедитесь, что `MAX_ACCESS_TOKEN` используется **без** префикса `Bearer` для вызовов MAX API (скрипт добавляет его автоматически). Для Dify API используется `Bearer`.
    *   Убедитесь, что `DIFY_CHATBOT_ENDPOINT` указывает на ваш *локальный* сервер Dify.

## Запуск

1.  **Запуск вручную (для тестирования):**
    ```bash
    python3 max-api.py
    ```

2.  **Запуск как системный сервис (Linux):**
    *   Убедитесь, что вы настроили все пути и имя пользователя в файле `/etc/systemd/system/max-api.service`
    *   Скопируйте файл `max-api.service` в `/etc/systemd/system/`.
    *   Перезагрузите конфигурацию systemd: `sudo systemctl daemon-reload`.
    *   Включите сервис: `sudo systemctl enable max-api.service`.
    *   Запустите сервис: `sudo systemctl start max-api.service`.
    *   Проверьте статус: `sudo systemctl status max-api.service`.
    *   Просмотр логов: `sudo journalctl -u max-api.service -f`.

## Файлы

*   `max-api.py`: Основной скрипт бота.
*   `max_messages.db`: Локальная база данных SQLite для хранения ID обработанных сообщений (создается автоматически).
*   `README.md`: Этот файл.
*   `requirements.txt`: Список зависимостей Python.

## Важные замечания

*   **Токены безопасности:** Храните ваши `MAX_ACCESS_TOKEN` и `DIFY_API_KEY` в безопасности. Не публикуйте их в открытом репозитории.
*   **Ограничения API MAX:** Скрипт учитывает ограничение в 30 rps (requests per second) для API MAX.
*   **Режим Dify:** Скрипт настроен на работу с Dify в режиме `streaming`.
*   **Разбиение сообщений:** Ответы от Dify, превышающие 3000 символов, автоматически разбиваются на части при отправке в MAX.
