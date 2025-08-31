"""
Веб-сервер для обработки вебхуков от Flyer Service
"""
import logging
import json
import hmac
import hashlib
from aiohttp import web
from typing import Optional
import asyncio

logger = logging.getLogger(__name__)

# Импортируем необходимые модули
try:
    from config import FLYER_WEBHOOK_SECRET
    from flyer_service import flyer_service
except ImportError:
    logger.error("Не удалось импортировать конфигурацию или flyer_service")
    FLYER_WEBHOOK_SECRET = None
    flyer_service = None

class FlyerWebhookHandler:
    """Обработчик вебхуков от Flyer"""
    
    def __init__(self, webhook_secret: str):
        self.webhook_secret = webhook_secret
        
    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """
        Проверка подписи вебхука
        
        Args:
            payload: тело запроса
            signature: подпись из заголовка
            
        Returns:
            True если подпись валидна
        """
        if not self.webhook_secret:
            logger.warning("Webhook secret не настроен, пропускаем проверку подписи")
            return True
            
        expected_signature = hmac.new(
            self.webhook_secret.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected_signature, signature)
    
    async def handle_webhook(self, request: web.Request) -> web.Response:
        """
        Обработка вебхука от Flyer
        
        Args:
            request: HTTP запрос
            
        Returns:
            HTTP ответ
        """
        try:
            # Читаем тело запроса
            payload = await request.read()
            
            # Проверяем подпись (если есть)
            signature = request.headers.get('X-Flyer-Signature', '')
            if signature and not self.verify_signature(payload, signature):
                logger.warning("Неверная подпись вебхука")
                return web.Response(status=401, text="Invalid signature")
            
            # Парсим JSON
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                logger.error("Невалидный JSON в вебхуке")
                return web.Response(status=400, text="Invalid JSON")
            
            # Логируем событие
            event_type = data.get('event', 'unknown')
            user_id = data.get('user_id')
            logger.info(f"Получен вебхук: event={event_type}, user_id={user_id}")
            
            # Обрабатываем вебхук через FlyerService
            if flyer_service:
                success = await flyer_service.handle_webhook(data)
                
                if success:
                    return web.Response(status=200, text="OK")
                else:
                    return web.Response(status=500, text="Processing failed")
            else:
                logger.error("FlyerService не инициализирован")
                return web.Response(status=503, text="Service unavailable")
                
        except Exception as e:
            logger.error(f"Ошибка обработки вебхука: {e}")
            return web.Response(status=500, text="Internal error")
    
    async def health_check(self, request: web.Request) -> web.Response:
        """Проверка состояния сервера"""
        return web.Response(text="OK", status=200)

def create_app(webhook_secret: str = None) -> web.Application:
    """
    Создание веб-приложения для вебхуков
    
    Args:
        webhook_secret: секретный ключ для проверки подписи
        
    Returns:
        Экземпляр веб-приложения
    """
    app = web.Application()
    
    # Создаем обработчик
    handler = FlyerWebhookHandler(webhook_secret or FLYER_WEBHOOK_SECRET)
    
    # Регистрируем маршруты
    app.router.add_post('/flyer_webhook', handler.handle_webhook)
    app.router.add_get('/health', handler.health_check)
    
    logger.info("Webhook сервер настроен")
    return app

async def start_webhook_server(host: str = '0.0.0.0', port: int = 8093):
    """
    Запуск webhook сервера
    
    Args:
        host: хост для привязки
        port: порт для прослушивания
    """
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    
    logger.info(f"Запускаем webhook сервер на {host}:{port}")
    await site.start()
    
    # Держим сервер запущенным
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Останавливаем webhook сервер")
    finally:
        await runner.cleanup()

if __name__ == '__main__':
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Запускаем сервер
    try:
        asyncio.run(start_webhook_server())
    except KeyboardInterrupt:
        logger.info("Webhook сервер остановлен")