"""
Модуль интеграции с Flyer Service API для партнерской системы монетизации
"""
import logging
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from flyerapi import Flyer
from aiogram import Bot, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiohttp
import json

logger = logging.getLogger(__name__)

class FlyerService:
    """Сервис для работы с Flyer API"""
    
    def __init__(self, api_key: str, bot: Bot):
        """
        Инициализация сервиса
        
        Args:
            api_key: API ключ Flyer Service
            bot: экземпляр бота для отправки сообщений
        """
        self.api_key = api_key
        self.bot = bot
        self.flyer = Flyer(api_key)
        self._cache = {}  # Кэш проверок доступа
        self._cache_ttl = 300  # 5 минут кэша
        
        # URL для вебхуков (нужно будет настроить на вашем сервере)
        self.webhook_url = "https://yourdomain.com/flyer_webhook"
        
        logger.info("FlyerService инициализирован")
    
    async def check_user_access(self, user_id: int, language: str = "ru", silent: bool = False) -> bool:
        """
        Проверка доступа пользователя через Flyer API
        
        Args:
            user_id: ID пользователя Telegram
            language: язык для сообщений (ru/en)
            silent: если True, не отправляет сообщения пользователю
            
        Returns:
            True если у пользователя есть доступ, False если нет
        """
        # Для команды /start всегда проверяем актуальный статус (без кэша)
        # Кэш используем только для других проверок
        cache_key = f"access_{user_id}"
        
        # Пропускаем кэш если это не silent запрос (т.е. основная проверка при /start)
        if silent and cache_key in self._cache:
            cached_time, cached_result = self._cache[cache_key]
            if datetime.now().timestamp() - cached_time < self._cache_ttl:
                logger.debug(f"Использую кэшированный результат для user {user_id}: {cached_result}")
                return cached_result
        
        try:
            # Если silent=True, передаем кастомное сообщение чтобы API не отправлял свое
            if silent:
                # Передаем кастомное сообщение - это заставит API вернуть True и не отправлять сообщение
                has_access = await self.flyer.check(
                    user_id, 
                    language_code=language,
                    message={"text": "", "button": ""}  # Пустое сообщение
                )
            else:
                # Обычная проверка - API может отправить свое сообщение
                has_access = await self.flyer.check(user_id, language_code=language)
            
            # Кэшируем результат
            self._cache[cache_key] = (datetime.now().timestamp(), has_access)
            
            logger.info(f"Проверка доступа user {user_id}: {'✅ разрешен' if has_access else '❌ запрещен'}")
            return has_access
            
        except Exception as e:
            logger.warning(f"Ошибка при проверке доступа user {user_id}: {e}")
            # В случае ошибки API даем доступ, чтобы не блокировать пользователей
            return True
    
    async def get_user_tasks(self, user_id: int, language: str = "ru", limit: int = 10) -> List[Dict]:
        """
        Получение списка доступных заданий для пользователя
        
        Args:
            user_id: ID пользователя
            language: язык
            limit: максимальное количество заданий
            
        Returns:
            Список заданий
        """
        try:
            tasks = await self.flyer.get_tasks(user_id, language, limit)
            logger.info(f"Получено {len(tasks)} заданий для user {user_id}")
            return tasks
        except Exception as e:
            error_msg = str(e)
            if "Prohibited method" in error_msg:
                # Этот метод не доступен для данного типа бота
                logger.debug(f"Метод get_tasks не доступен для этого типа бота: {error_msg}")
            else:
                logger.error(f"Ошибка при получении заданий для user {user_id}: {e}")
            return []  # Возвращаем пустой список в любом случае
    
    async def check_task_completion(self, user_id: int, task_signature: str) -> bool:
        """
        Проверка выполнения конкретного задания
        
        Args:
            user_id: ID пользователя
            task_signature: уникальная подпись задания
            
        Returns:
            True если задание выполнено
        """
        try:
            result = await self.flyer.check_task(task_signature)
            logger.info(f"Проверка задания {task_signature} для user {user_id}: {result}")
            return result
        except Exception as e:
            logger.error(f"Ошибка при проверке задания {task_signature}: {e}")
            return False
    
    def create_tasks_keyboard(self, tasks: List[Dict]) -> InlineKeyboardMarkup:
        """
        Создание клавиатуры с заданиями
        
        Args:
            tasks: список заданий от API
            
        Returns:
            Клавиатура с кнопками заданий
        """
        builder = InlineKeyboardBuilder()
        
        for task in tasks:
            # Формируем текст кнопки
            task_type = task.get('type', 'unknown')
            reward = task.get('reward', 0)
            title = task.get('title', 'Задание')
            
            # Иконка в зависимости от типа задания
            icon = self._get_task_icon(task_type)
            
            button_text = f"{icon} {title} (+{reward})"
            
            # URL или callback_data в зависимости от типа
            if task.get('url'):
                builder.add(InlineKeyboardButton(
                    text=button_text,
                    url=task['url']
                ))
            else:
                builder.add(InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"flyer_task_{task.get('id', 'unknown')}"
                ))
        
        # Добавляем кнопку проверки выполнения
        builder.add(InlineKeyboardButton(
            text="✅ Проверить выполнение",
            callback_data="flyer_check_tasks"
        ))
        
        # Кнопка обновления списка
        builder.add(InlineKeyboardButton(
            text="🔄 Обновить задания", 
            callback_data="flyer_refresh_tasks"
        ))
        
        builder.adjust(1)  # По одной кнопке в ряд
        return builder.as_markup()
    
    def _get_task_icon(self, task_type: str) -> str:
        """Получение иконки для типа задания"""
        icons = {
            'subscription': '📢',
            'view': '👁',
            'bot': '🤖',
            'click': '👆',
            'share': '📤',
            'invite': '👥',
            'default': '📋'
        }
        return icons.get(task_type, icons['default'])
    
    async def send_no_access_message(self, user_id: int, message: types.Message = None):
        """
        Отправка сообщения о необходимости выполнить задания
        
        Args:
            user_id: ID пользователя
            message: сообщение для ответа (опционально)
            
        Returns:
            True если были показаны задания, False если заданий нет
        """
        # Получаем список заданий
        tasks = await self.get_user_tasks(user_id)
        
        if not tasks:
            # Если заданий нет - просто пропускаем пользователя дальше
            logger.info(f"Нет доступных заданий для user {user_id}, предоставляем доступ автоматически")
            return False  # Возвращаем False, чтобы бот продолжил работу нормально
        else:
            text = (
                "🔒 <b>Доступ к боту ограничен</b>\n\n"
                "Для получения доступа выполните одно из заданий:\n\n"
                "• Подпишитесь на канал партнера\n"
                "• Выполните простое действие\n"
                "• Пригласите друга\n\n"
                "После выполнения нажмите кнопку проверки ✅"
            )
            keyboard = self.create_tasks_keyboard(tasks)
        
            if message:
                await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
            else:
                await self.bot.send_message(user_id, text, reply_markup=keyboard, parse_mode="HTML")
            return True  # Возвращаем True, если показали задания
    
    async def handle_webhook(self, data: Dict) -> bool:
        """
        Обработка вебхука от Flyer Service
        
        Args:
            data: данные вебхука
            
        Returns:
            True если обработка успешна
        """
        try:
            event_type = data.get('event')
            user_id = data.get('user_id')
            
            if event_type == 'access_granted':
                # Пользователь получил доступ
                logger.info(f"Вебхук: доступ предоставлен user {user_id}")
                
                # Очищаем кэш для пользователя
                cache_key = f"access_{user_id}"
                if cache_key in self._cache:
                    del self._cache[cache_key]
                
                # Отправляем приветственное сообщение
                await self.bot.send_message(
                    user_id,
                    "🎉 <b>Поздравляем!</b>\n\n"
                    "Вы успешно выполнили задание и получили доступ к боту!\n"
                    "Теперь вам доступны все функции.\n\n"
                    "Выберите модель для общения /change",
                    parse_mode="HTML"
                )
                return True
                
            elif event_type == 'task_completed':
                # Задание выполнено
                task_id = data.get('task_id')
                logger.info(f"Вебхук: задание {task_id} выполнено user {user_id}")
                
                # Можно отправить уведомление пользователю
                await self.bot.send_message(
                    user_id,
                    "✅ Задание выполнено! Проверяем доступ...",
                    parse_mode="HTML"
                )
                
                # Проверяем общий доступ
                has_access = await self.check_user_access(user_id)
                if has_access:
                    await self.bot.send_message(
                        user_id,
                        "🎉 Доступ к боту открыт! Можете начинать общение.",
                        parse_mode="HTML"
                    )
                
                return True
                
            else:
                logger.warning(f"Неизвестный тип вебхука: {event_type}")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка обработки вебхука: {e}")
            return False
    
    async def register_webhook(self, webhook_url: str = None):
        """
        Регистрация вебхука в Flyer Service
        
        Args:
            webhook_url: URL для вебхука (если не указан, используется self.webhook_url)
        """
        url = webhook_url or self.webhook_url
        
        try:
            # Здесь должен быть вызов API для регистрации вебхука
            # Пока это заглушка, так как точный метод API неизвестен
            logger.info(f"Регистрация вебхука: {url}")
            
            # Примерный код регистрации (нужно уточнить по документации API)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.flyerservice.io/webhook/register",
                    json={
                        "api_key": self.api_key,
                        "url": url,
                        "events": ["access_granted", "task_completed"]
                    }
                ) as response:
                    if response.status == 200:
                        logger.info("Вебхук успешно зарегистрирован")
                        return True
                    else:
                        logger.error(f"Ошибка регистрации вебхука: {response.status}")
                        return False
                        
        except Exception as e:
            logger.error(f"Ошибка при регистрации вебхука: {e}")
            return False
    
    def clear_cache(self, user_id: int = None):
        """
        Очистка кэша
        
        Args:
            user_id: если указан, очищает кэш только для этого пользователя
        """
        if user_id:
            cache_key = f"access_{user_id}"
            if cache_key in self._cache:
                del self._cache[cache_key]
                logger.debug(f"Кэш очищен для user {user_id}")
        else:
            self._cache.clear()
            logger.debug("Весь кэш очищен")
    
    async def monitor_user_access(self, user_id: int):
        """
        Мониторинг доступа пользователя и отправка приветствия после удаления сообщения Flyer
        
        Args:
            user_id: ID пользователя для мониторинга
        """
        logger.info(f"[MONITOR] Начинаем мониторинг доступа для пользователя {user_id}")
        
        # Запоминаем, что пользователь не имел доступа
        user_state_key = f"user_state_{user_id}"
        self._cache[user_state_key] = False
        
        # Ждем некоторое время, пока пользователь подписывается
        max_attempts = 60  # Проверяем в течение 5 минут
        check_interval = 5  # Проверяем каждые 5 секунд
        
        for attempt in range(max_attempts):
            logger.debug(f"[MONITOR] Попытка {attempt + 1}/{max_attempts} для пользователя {user_id}")
            await asyncio.sleep(check_interval)
            
            # Проверяем доступ молча (без отправки сообщений)
            has_access = await self.check_user_access(user_id, silent=True)
            logger.debug(f"[MONITOR] Результат проверки для {user_id}: {has_access}")
            
            if has_access:
                logger.info(f"[MONITOR] Пользователь {user_id} получил доступ, отправляем приветствие")
                # Отправляем приветственное сообщение напрямую
                try:
                    await self.send_welcome_to_user(user_id)
                    logger.info(f"[MONITOR] Приветственное сообщение успешно отправлено пользователю {user_id}")
                except Exception as e:
                    logger.error(f"[MONITOR] Ошибка при отправке приветствия пользователю {user_id}: {e}", exc_info=True)
                
                # Очищаем состояние
                if user_state_key in self._cache:
                    del self._cache[user_state_key]
                return True
        
        logger.info(f"[MONITOR] Пользователь {user_id} не получил доступ за отведенное время")
        # Очищаем состояние
        if user_state_key in self._cache:
            del self._cache[user_state_key]
        return False
    
    async def send_welcome_to_user(self, user_id: int):
        """
        Отправляет приветственное сообщение пользователю после подписки
        
        Args:
            user_id: ID пользователя
        """
        from aiogram.types import InlineKeyboardButton, WebAppInfo, FSInputFile
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        import os
        
        # URL для веб-приложения выбора моделей
        MODEL_SELECTOR_URL = "https://giftex.top"
        
        # Формируем inline-клавиатуру с кнопкой каталога
        builder = InlineKeyboardBuilder()
        builder.add(InlineKeyboardButton(
            text="🌐 Открыть каталог моделей",
            web_app=WebAppInfo(url=f"{MODEL_SELECTOR_URL}?user_id={user_id}")
        ))
        builder.add(InlineKeyboardButton(
            text="✅ Анора пишет первой", 
            callback_data="toggle_auto_message"
        ))
        builder.adjust(1)
        
        # Проверяем наличие файла с изображением
        image_path = "/root/tyan.jpg"
        if os.path.exists(image_path):
            # Отправляем приветственное изображение с клавиатурой
            photo = FSInputFile(image_path)
            await self.bot.send_photo(
                user_id,
                photo,
                caption=(
                    "<b>💋 Добро пожаловать в мир Аноры - твоего личного ИИ-соблазнителя!</b>\n\n"
                    "▫️ <i>Интимные разговоры и горячие фантазии</i>\n"
                    "▫️ <i>Душевные беседы и романтические отношения</i>\n"
                    "▫️ <i>Эксклюзивные ролевые сценарии</i>\n"
                    "▫️ <i>Генерация 18+ изображений</i>\n\n"
                    "<b>⚠️ ВНИМАНИЕ:</b> Некоторый контент предназначен только для лиц старше 18 лет!\n\n"
                    "✨ <b>Выберите свою идеальную Анору:</b>"
                ),
                parse_mode="HTML",
                reply_markup=builder.as_markup()
            )
        else:
            # Если изображения нет, отправляем просто текст
            await self.bot.send_message(
                user_id,
                (
                    "<b>💋 Добро пожаловать в мир Аноры - твоего личного ИИ-соблазнителя!</b>\n\n"
                    "▫️ <i>Интимные разговоры и горячие фантазии</i>\n"
                    "▫️ <i>Душевные беседы и романтические отношения</i>\n"
                    "▫️ <i>Эксклюзивные ролевые сценарии</i>\n"
                    "▫️ <i>Генерация 18+ изображений</i>\n\n"
                    "<b>⚠️ ВНИМАНИЕ:</b> Некоторый контент предназначен только для лиц старше 18 лет!\n\n"
                    "✨ <b>Выберите свою идеальную Анору:</b>"
                ),
                parse_mode="HTML",
                reply_markup=builder.as_markup()
            )


# Глобальный экземпляр сервиса (будет инициализирован в bot.py)
flyer_service: Optional[FlyerService] = None

def init_flyer_service(api_key: str, bot: Bot) -> FlyerService:
    """
    Инициализация глобального экземпляра FlyerService
    
    Args:
        api_key: API ключ Flyer
        bot: экземпляр бота
        
    Returns:
        Инициализированный FlyerService
    """
    global flyer_service
    flyer_service = FlyerService(api_key, bot)
    return flyer_service