# 🤖 Универсальная WebApp система для Telegram Bot

## 📋 Описание

Эта система позволяет Telegram WebApp работать как с обычными кнопками клавиатуры (KeyboardButton), так и с инлайн кнопками (InlineKeyboardButton).

## 🔧 Архитектура

### Компоненты:
1. **webapp_handler.py** - Универсальный обработчик WebApp данных
2. **webapp_server.py** - Flask HTTP сервер для InlineKeyboardButton
3. **bolt/src/hooks/useTelegram.ts** - Универсальный JavaScript клиент

### Логика работы:
- **KeyboardButton** → `tg.sendData()` → бот получает `web_app_data`
- **InlineKeyboardButton** → `fetch()` → Flask сервер → `answerWebAppQuery` → бот получает сообщение

## ⚙️ Настройка

### 1. Конфигурация

Добавьте в `config.py`:
```python
# WebApp сервер
WEBAPP_PORT = 8080
```

### 2. Установка зависимостей

```bash
pip install flask>=2.3.3
```

### 3. Обновление URL в WebApp

В файле `bolt/src/hooks/useTelegram.ts` замените:
```typescript
const BACKEND_URL = 'http://localhost:8080';
```

На ваш реальный адрес сервера:
```typescript
const BACKEND_URL = 'https://your-domain.com:8080';
```

## 🚀 Развертывание

### Локальная разработка
1. Запустите бота: `python bot.py`
2. WebApp сервер автоматически запустится на порту 8080
3. Используйте ngrok для тестирования с внешним URL:
   ```bash
   ngrok http 8080
   ```

### Продакшн
1. Используйте HTTPS (обязательно для Telegram WebApp)
2. Настройте reverse proxy (nginx/apache)
3. Обновите `BACKEND_URL` в JavaScript коде
4. Настройте межсетевой экран для порта 8080

## 🔒 Безопасность

### Рекомендации:
- Используйте HTTPS в продакшн
- Настройте CORS политики
- Валидируйте все входящие данные
- Ограничьте доступ к порту WebApp сервера

### Пример nginx конфигурации:
```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;
    
    location /webapp/ {
        proxy_pass http://localhost:8080/webapp/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## 🧪 Тестирование

### Проверка работы системы:
1. Создайте обычную кнопку с WebApp
2. Создайте инлайн кнопку с WebApp
3. Убедитесь, что оба типа кнопок корректно передают данные

### Диагностика:
- Проверьте логи бота: `[WEBAPP]` префикс
- Проверьте логи Flask сервера: `[WEBAPP_SERVER]` префикс
- Health check: `GET /webapp/health`

## 📝 API Endpoints

### POST /webapp/inline
Обрабатывает данные от InlineKeyboardButton WebApp
```json
{
  "query_id": "string",
  "data": {
    "action": "string",
    "user_id": "number",
    ...
  }
}
```

### GET /webapp/health
Проверка состояния сервера
```json
{
  "status": "ok",
  "handler_initialized": true
}
```

## 🐛 Устранение неполадок

### Частые проблемы:
1. **Ошибка CORS** - Настройте заголовки в Flask
2. **Handler not initialized** - Проверьте порядок инициализации в bot.py
3. **Connection refused** - Убедитесь, что сервер запущен и порт доступен

### Отладка:
Включите подробное логирование:
```python
logging.basicConfig(level=logging.DEBUG)
```
