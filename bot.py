import logging
import json
import asyncio
import signal
import aiosqlite
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from contextlib import asynccontextmanager
from collections import defaultdict
import os
import sys
try:
    import psutil
except ImportError:
    psutil = None

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, BufferedInputFile, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, FSInputFile
from aiogram.enums import ChatAction
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.fsm.context import FSMContext
import aiohttp
from aiohttp import ClientSession, ClientTimeout
import base64
import re
import traceback
from runware import Runware, IImageInference, ILora
from aiogram.types import PreCheckoutQuery

# Импорт конфигурации
try:
    from config import *
except ImportError:
    print("❌ Не найден файл config.py")
    print("📋 Создайте config.py на основе config.py.example")
    exit(1)

# Импорт административных команд
try:
    from admin_commands import setup_admin_commands
except ImportError:
    logger.warning("⚠️ Модуль admin_commands.py не найден, административные команды недоступны")
    setup_admin_commands = None

# Импорт модуля партнерской системы Flyer
try:
    from flyer_service import FlyerService, init_flyer_service
    flyer_service = None  # Будет инициализировано позже
except ImportError:
    logger.warning("⚠️ Модуль flyer_service.py не найден, партнерская система недоступна")
    FlyerService = None
    init_flyer_service = None
    flyer_service = None

# Настройка логирования
logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

bot: Optional[Bot] = None
dp = Dispatcher()


MAX_MESSAGE_LENGTH = 4000
MAX_PROMPT_LENGTH = 2000

def validate_input_length(text: str, max_length: int, input_type: str = "message") -> bool:
    if not text:
        return True
    if len(text) > max_length:
        logger.warning(f"Input {input_type} too long: {len(text)} > {max_length} chars")
        return False
    return True

class CriticalErrorMonitor:
    def __init__(self, admin_id: int):
        self.admin_id = admin_id
        self.error_count = defaultdict(int)
        self.last_alert_time = defaultdict(float)
        self.alert_cooldown = 300  # 5 минут между алертами одного типа
    
    async def log_critical_error(self, error_type: str, error_msg: str, user_id: int = None):
        """Логирует критическую ошибку и отправляет алерт админу"""
        self.error_count[error_type] += 1
        
        # Детальное логирование
        logger.critical(
            f"CRITICAL ERROR [{error_type}]: {error_msg} | "
            f"User: {user_id} | Count: {self.error_count[error_type]}"
        )
        
        # Проверяем cooldown для алертов
        now = time.time()
        if now - self.last_alert_time[error_type] > self.alert_cooldown:
            self.last_alert_time[error_type] = now
            
            try:
                alert_msg = (
                    f"🚨 <b>КРИТИЧЕСКАЯ ОШИБКА</b>\n\n"
                    f"<b>Тип:</b> {error_type}\n"
                    f"<b>Сообщение:</b> {error_msg[:500]}{'...' if len(error_msg) > 500 else ''}\n"
                    f"<b>Пользователь:</b> {user_id or 'N/A'}\n"
                    f"<b>Количество:</b> {self.error_count[error_type]}\n"
                    f"<b>Время:</b> {datetime.now().strftime('%H:%M:%S %d.%m.%Y')}"
                )
                
                if bot:
                    await bot.send_message(self.admin_id, alert_msg, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Failed to send critical error alert: {e}")

error_monitor = CriticalErrorMonitor(ADMIN_ID)

@dp.update()
async def debug_log(update: types.Update):
    """Логирует все входящие апдейты для отладки"""
    try:
        # Логируем все апдейты для отладки WebApp
        if update.message:
            has_webapp = hasattr(update.message, 'web_app_data') and update.message.web_app_data is not None
            if has_webapp:
                logger.info("[RAW-WEBAPP] WebApp data detected! User: %s, Data: %s", 
                           update.message.from_user.id if update.message.from_user else None,
                           update.message.web_app_data)
            logger.debug(
                "[RAW] chat_id=%s user_id=%s has_web_app_data=%s",
                update.message.chat.id,
                update.message.from_user.id if update.message.from_user else None,
                has_webapp,
            )
    except Exception as e:
        logger.error("[RAW] Failed to log update: %s", e)

# Глобальная переменная для отслеживания времени последнего апдейта
last_update_time = time.time()
last_update_lock = asyncio.Lock()

async def update_last_update_time():
    global last_update_time
    async with last_update_lock:
        last_update_time = time.time()

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._connection: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        self._initialized = False
    
    async def initialize(self):
        """Инициализация базы данных"""
        if self._initialized:
            return
            
        async with self._lock:
            if self._initialized:  # Double-checked locking
                return
                
            # Создаем соединение с базой данных
            self._connection = await aiosqlite.connect(
                self.db_path,
                timeout=30.0,
                isolation_level=None,
                check_same_thread=False
            )
            
            # Включаем WAL режим для лучшей производительности
            await self._connection.execute('PRAGMA journal_mode=WAL')
            await self._connection.execute('PRAGMA synchronous=NORMAL')
            await self._connection.execute('PRAGMA cache_size=-2000')  # 2MB кэша
            
            # Инициализируем схему базы данных
            await self._init_db()
            self._initialized = True
    
    async def _init_db(self):
        """Инициализация структуры базы данных"""
        await self._connection.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                name TEXT,
                join_date TEXT,
                last_active TEXT,
                current_model TEXT,
                context TEXT,
                source TEXT,
                auto_message BOOLEAN DEFAULT 1
            )
        ''' )
        # Добавляем колонку auto_message для существующих баз, если её нет
        try:
            await self._connection.execute('ALTER TABLE users ADD COLUMN auto_message BOOLEAN DEFAULT 1')
            # Установим auto_message=1 для всех существующих пользователей
            await self._connection.execute('UPDATE users SET auto_message = 1 WHERE auto_message IS NULL')
        except aiosqlite.Error:
            pass  # колонка уже существует
        
        # Добавляем колонку bot_blocked для отслеживания заблокировавших бота пользователей
        try:
            await self._connection.execute('ALTER TABLE users ADD COLUMN bot_blocked BOOLEAN DEFAULT 0')
        except aiosqlite.Error:
            pass  # колонка уже существует
        
        # В любом случае обновим всех пользователей, у которых не установлено значение
        try:
            await self._connection.execute('UPDATE users SET auto_message = 1 WHERE auto_message IS NULL')
        except aiosqlite.Error:
            logger.warning("Не удалось обновить значения auto_message по умолчанию")
            
        # Новая таблица сообщений
        await self._connection.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                model TEXT,
                role TEXT,
                content TEXT,
                ts TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')
        # Добавляем колонки для старых баз, если их нет
        for col in ('ts TEXT', 'model TEXT', 'role TEXT', 'content TEXT'):
            try:
                await self._connection.execute(f'ALTER TABLE messages ADD COLUMN {col}')
            except aiosqlite.Error:
                pass
        await self._connection.execute('CREATE INDEX IF NOT EXISTS idx_messages_user_ts ON messages(user_id, ts)')
        await self._connection.execute('CREATE INDEX IF NOT EXISTS idx_messages_model_ts ON messages(model, ts)')
        # Таблица для UTM статистики
        await self._connection.execute('''
            CREATE TABLE IF NOT EXISTS sources (
                source TEXT PRIMARY KEY,
                users_count INTEGER DEFAULT 0,
                requests_count INTEGER DEFAULT 0
            )
        ''')
        # Пытаемся добавить колонку source для старых баз (если она уже есть, игнорируем ошибку)
        try:
            await self._connection.execute('ALTER TABLE users ADD COLUMN source TEXT')
        except aiosqlite.Error:
            pass  # колонка уже существует
        # Таблица подписок
        await self._connection.execute('''
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER PRIMARY KEY,
                expires_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')
        
        # Таблица для отслеживания сообщений за сутки
        await self._connection.execute('''
            CREATE TABLE IF NOT EXISTS daily_messages (
                user_id INTEGER,
                date TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, date),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')
        
        # Таблица для отслеживания генераций изображений за месяц
        await self._connection.execute('''
            CREATE TABLE IF NOT EXISTS monthly_images (
                user_id INTEGER,
                month TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, month),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')
    
    async def add_message(self, user_id:int, model:str, role:str, content:str):
        async with self.acquire() as conn:
            await conn.execute(
                'INSERT INTO messages (user_id, model, role, content, ts) VALUES (?,?,?,?,?)',
                (user_id, model, role, content, datetime.now().isoformat())
            )

    async def increment_source_user(self, source: str):
        if not source:
            return
        async with self.acquire() as conn:
            await conn.execute(
                'INSERT INTO sources (source, users_count, requests_count) VALUES (?, 1, 0) '
                'ON CONFLICT(source) DO UPDATE SET users_count = users_count + 1',
                (source,)
            )
    
    async def increment_source_request(self, source: str):
        if not source:
            return
        async with self.acquire() as conn:
            await conn.execute(
                'INSERT INTO sources (source, users_count, requests_count) VALUES (?, 0, 1) '
                'ON CONFLICT(source) DO UPDATE SET requests_count = requests_count + 1',
                (source,)
            )
    
    async def get_source_stats(self) -> List[Tuple[str, int, int, int]]:
        """Получает статистику по источникам с учетом покупок"""
        async with self.acquire() as conn:
            # Получаем базовую статистику источников
            cursor = await conn.execute('''
                SELECT 
                    s.source,
                    s.users_count,
                    s.requests_count,
                    COUNT(DISTINCT CASE WHEN sub.user_id IS NOT NULL THEN u.id END) as premium_count
                FROM sources s
                LEFT JOIN users u ON u.source = s.source
                LEFT JOIN subscriptions sub ON sub.user_id = u.id 
                    AND datetime(sub.expires_at) > datetime('now')
                GROUP BY s.source
                ORDER BY s.users_count DESC
            ''')
            return [tuple(row) async for row in cursor]
    
    async def save_subscription(self, user_id: int, expires_at: datetime):
        async with self.acquire() as conn:
            await conn.execute('REPLACE INTO subscriptions (user_id, expires_at) VALUES (?,?)',
                               (user_id, expires_at.isoformat()))

    async def has_active_subscription(self, user_id: int) -> bool:
        async with self.acquire() as conn:
            cursor = await conn.execute('SELECT expires_at FROM subscriptions WHERE user_id=?', (user_id,))
            row = await cursor.fetchone()
            if not row or not row['expires_at']:
                return False
            try:
                return datetime.fromisoformat(row['expires_at']) >= datetime.now()
            except Exception:
                return False
    
    async def get_daily_message_count(self, user_id: int) -> int:
        """Получает количество сообщений пользователя за сегодня"""
        today = datetime.now().date().isoformat()
        async with self.acquire() as conn:
            cursor = await conn.execute('SELECT count FROM daily_messages WHERE user_id=? AND date=?', (user_id, today))
            row = await cursor.fetchone()
            return row['count'] if row else 0
    
    async def increment_daily_message_count(self, user_id: int) -> int:
        """Увеличивает счетчик сообщений за сегодня и возвращает новое значение"""
        today = datetime.now().date().isoformat()
        async with self.acquire() as conn:
            await conn.execute(
                'INSERT INTO daily_messages (user_id, date, count) VALUES (?, ?, 1) '
                'ON CONFLICT(user_id, date) DO UPDATE SET count = count + 1',
                (user_id, today)
            )
            cursor = await conn.execute('SELECT count FROM daily_messages WHERE user_id=? AND date=?', (user_id, today))
            row = await cursor.fetchone()
            return row['count'] if row else 0
    
    async def get_monthly_image_count(self, user_id: int) -> int:
        """Получает количество сгенерированных изображений за текущий месяц"""
        current_month = datetime.now().strftime('%Y-%m')
        async with self.acquire() as conn:
            cursor = await conn.execute('SELECT count FROM monthly_images WHERE user_id=? AND month=?', (user_id, current_month))
            row = await cursor.fetchone()
            return row['count'] if row else 0
    
    async def increment_monthly_image_count(self, user_id: int) -> int:
        """Увеличивает счетчик генераций изображений за месяц и возвращает новое значение"""
        current_month = datetime.now().strftime('%Y-%m')
        async with self.acquire() as conn:
            await conn.execute(
                'INSERT INTO monthly_images (user_id, month, count) VALUES (?, ?, 1) '
                'ON CONFLICT(user_id, month) DO UPDATE SET count = count + 1',
                (user_id, current_month)
            )
            cursor = await conn.execute('SELECT count FROM monthly_images WHERE user_id=? AND month=?', (user_id, current_month))
            row = await cursor.fetchone()
            return row['count'] if row else 0

    @asynccontextmanager
    async def acquire(self):
        """Контекстный менеджер для работы с соединением"""
        if not self._connection:
            await self.initialize()
            
        try:
            self._connection.row_factory = aiosqlite.Row
            yield self._connection
            await self._connection.commit()
        except Exception as e:
            if self._connection:
                await self._connection.rollback()
            raise
    
    async def close(self):
        """Закрытие соединения с базой данных"""
        if self._connection:
            await self._connection.close()
            self._connection = None
            self._initialized = False
    
    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получение данных пользователя по ID"""
        async with self.acquire() as conn:
            cursor = await conn.execute('SELECT * FROM users WHERE id = ?', (user_id,))
            row = await cursor.fetchone()
            
            if row:
                return {
                    'id': row['id'],
                    'username': row['username'],
                    'name': row['name'],
                    'join_date': datetime.fromisoformat(row['join_date']) if row['join_date'] else datetime.now(),
                    'last_active': datetime.fromisoformat(row['last_active']) if row['last_active'] else datetime.now(),
                    'current_model': row['current_model'],
                    'context': json.loads(row['context']) if row['context'] else [],
                    'source': row['source']
                }
            return None
    
    async def save_user(self, user_data: Dict[str, Any]) -> None:
        """Сохранение данных пользователя"""
        async with self.acquire() as conn:
            await conn.execute('''
                INSERT OR REPLACE INTO users (id, username, name, join_date, last_active, current_model, context, source, auto_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                user_data['id'],
                user_data['username'],
                user_data['name'],
                user_data['join_date'].isoformat(),
                user_data['last_active'].isoformat(),
                user_data['current_model'],
                json.dumps(user_data['context'], ensure_ascii=False),
                user_data.get('source', ''),
                user_data.get('auto_message', False)
            ))
    
    async def toggle_auto_message(self, user_id: int) -> bool:
        """Переключает настройку автоматических сообщений для пользователя"""
        async with self.acquire() as conn:
            # Получаем текущее значение
            cursor = await conn.execute('SELECT auto_message FROM users WHERE id = ?', (user_id,))
            row = await cursor.fetchone()
            current_value = bool(row['auto_message']) if row else False
            
            # Инвертируем значение
            new_value = not current_value
            await conn.execute('UPDATE users SET auto_message = ? WHERE id = ?', (new_value, user_id))
            return new_value

    async def get_users_for_auto_message(self) -> List[Dict[str, Any]]:
        """Получает список пользователей с включенными автосообщениями, которые не были активны более суток"""
        async with self.acquire() as conn:
            cursor = await conn.execute('''
                SELECT * FROM users 
                WHERE auto_message = 1 
                AND datetime(last_active) <= datetime('now', '-1 day')
                AND (bot_blocked IS NULL OR bot_blocked = 0)
            ''')
            users = []
            async for row in cursor:
                users.append({
                    'id': row['id'],
                    'username': row['username'],
                    'name': row['name'],
                    'current_model': row['current_model'],
                    'context': json.loads(row['context']) if row['context'] else [],
                })
            return users
    
    async def mark_user_blocked(self, user_id: int) -> None:
        """Отмечает пользователя как заблокировавшего бота"""
        async with self.acquire() as conn:
            await conn.execute('UPDATE users SET bot_blocked = 1 WHERE id = ?', (user_id,))
    
    async def mark_user_unblocked(self, user_id: int) -> None:
        """Отмечает что пользователь разблокировал бота"""
        async with self.acquire() as conn:
            await conn.execute('UPDATE users SET bot_blocked = 0 WHERE id = ?', (user_id,))

    async def get_stats(self) -> Tuple[int, int, List[Tuple[str, int]]]:
        """Получение статистики по пользователям"""
        async with self.acquire() as conn:
            cursor = await conn.execute('SELECT COUNT(*) as count FROM users')
            total_users = (await cursor.fetchone())['count']
            
            cursor = await conn.execute('SELECT COUNT(*) as count FROM users WHERE date(last_active) = date("now")')
            active_today = (await cursor.fetchone())['count']
            
            cursor = await conn.execute('SELECT current_model, COUNT(*) as count FROM users GROUP BY current_model')
            model_stats = []
            async for row in cursor:
                model_stats.append((row['current_model'], row['count']))
            
            return total_users, active_today, model_stats
    
    async def get_today_message_stats(self) -> Tuple[int, Dict[str, int]]:
        """Возвращает количество сообщений за сегодня и по моделям"""
        async with self.acquire() as conn:
            cursor = await conn.execute("""
                SELECT model, COUNT(*) as cnt
                FROM messages
                WHERE role='user' AND date(ts)=date('now')
                GROUP BY model
            """)
            total = 0
            model_counts: Dict[str,int] = {}
            async for row in cursor:
                model_counts[row['model']] = row['cnt']
                total += row['cnt']
            return total, model_counts

    async def get_new_users_stats(self) -> Tuple[int, int]:
        """Получение статистики новых пользователей за сегодня и неделю"""
        async with self.acquire() as conn:
            cursor = await conn.execute("""
                SELECT COUNT(*) as count 
                FROM users 
                WHERE date(join_date) = date('now')
            """)
            new_today = (await cursor.fetchone())['count']
            
            cursor = await conn.execute("""
                SELECT COUNT(*) as count 
                FROM users 
                WHERE date(join_date) >= date('now', '-7 days')
            """)
            new_week = (await cursor.fetchone())['count']
            
            return new_today, new_week

    async def get_all_user_ids(self) -> List[int]:
        """Получение списка всех ID пользователей"""
        async with self.acquire() as conn:
            cursor = await conn.execute('SELECT id FROM users')
            return [row['id'] async for row in cursor]

class UserManager:
    def __init__(self, db):
        self.db = db
    
    def create_user(self, user_id, username, name, source=""):
        return {
            'id': user_id,
            'username': username or '',
            'name': name or 'Пользователь',
            'join_date': datetime.now(),
            'last_active': datetime.now(),
            'current_model': 'Подруга',
            'context': [],
            'source': source,
            'auto_message': True  # По умолчанию включаем автосообщения
        }
    
    def add_to_context(self, user_data, role, content):
# Асинхронно пишем в messages
        try:
            asyncio.create_task(db.add_message(user_data['id'], user_data['current_model'], role, content))
        except RuntimeError:
            pass
        user_data['context'].append({
            'role': role,
            'content': content,
            'timestamp': datetime.now().isoformat()
        })
        
        # Ограничиваем контекст
        if len(user_data['context']) > MAX_CONTEXT_MESSAGES:
            user_data['context'] = user_data['context'][-MAX_CONTEXT_MESSAGES:]
    
    def clear_context(self, user_data):
        user_data['context'] = []
    
    def update_activity(self, user_data):
        user_data['last_active'] = datetime.now()

class AIService:
    @staticmethod
    async def call_openai_api(messages, model="gpt-4o-mini"):
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": model,
            "messages": messages,
            "max_tokens": 1024,
            "temperature": 0.7,
            "stream": True
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                if response.status != 200:
                    raise Exception(f"OpenAI API error: {response.status}")
                
                content = ""
                async for line in response.content:
                    if line.startswith(b'data: '):
                        json_line = line[6:].strip()
                        if json_line == b'[DONE]':
                            break
                        try:
                            chunk = json.loads(json_line)
                            if 'content' in chunk['choices'][0]['delta']:
                                content += chunk['choices'][0]['delta']['content']
                        except (json.JSONDecodeError, KeyError, IndexError) as e:
                            logger.warning(f"Skipping malformed API chunk: {e}")
                            continue
                return content
    
    @staticmethod
    async def call_groq_api(messages, model="llama-3.3-70b-versatile"):
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": model,
            "messages": messages,
            "max_tokens": 1024,
            "temperature": 0.9,
            "stream": True
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                if response.status != 200:
                    raise Exception(f"Groq API error: {response.status}")
                
                content = ""
                async for line in response.content:
                    if line.startswith(b'data: '):
                        json_line = line[6:].strip()
                        if json_line == b'[DONE]':
                            break
                        try:
                            chunk = json.loads(json_line)
                            if 'content' in chunk['choices'][0]['delta']:
                                content += chunk['choices'][0]['delta']['content']
                        except (json.JSONDecodeError, KeyError, IndexError) as e:
                            logger.warning(f"Skipping malformed API chunk: {e}")
                            continue
                return content

async def check_daily_message_limit(user_id: int) -> bool:
    """Проверяет, не превышен ли лимит сообщений в день"""
    # Проверяем подписку
    has_subscription = await db.has_active_subscription(user_id)
    if has_subscription:
        return True  # Подписчики могут отправлять неограниченное количество сообщений
    
    # Проверяем лимит для бесплатных пользователей
    daily_count = await db.get_daily_message_count(user_id)
    return daily_count < DAILY_MESSAGE_LIMIT

async def check_monthly_image_limit(user_id: int) -> bool:
    """Проверяет, не превышен ли лимит генераций изображений в месяц"""
    # Проверяем подписку
    has_subscription = await db.has_active_subscription(user_id)
    if not has_subscription:
        return False  # Бесплатные пользователи не могут генерировать изображения
    
    # Проверяем лимит для подписчиков
    monthly_count = await db.get_monthly_image_count(user_id)
    return monthly_count < MONTHLY_IMAGE_LIMIT

async def require_subscription(message: types.Message) -> bool:
    """Проверяет наличие активной подписки у пользователя."""
    has_sub = await db.has_active_subscription(message.from_user.id)
    if not has_sub:
        await message.answer(
            "💸 Генерация изображений доступна только при активной подписке.\n\n"
            "🚀 Подписка даёт вам:\n"
            "• 🎆 Неограниченное количество сообщений\n"
            "• 🔥 150 генераций 18+ изображений в месяц\n\n"
            "Нажмите /buy чтобы купить подписку за 200 ⭐"
        )
    return has_sub

class ImageGenerator:
    @staticmethod
    async def generate_with_cloudflare(prompt):
        headers = {
            "Authorization": f"Bearer {CLOUDFLARE_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "prompt": prompt,
            "num_steps": 40,
            "guidance_scale": 7.5,
        }
        async with aiohttp.ClientSession() as session:
            url = f"{CLOUDFLARE_API_URL}@cf/black-forest-labs/flux-1-schnell"
            try:
                async with session.post(url, headers=headers, json=data) as response:
                    try:
                        async with asyncio.timeout(30.0):
                            if response.status == 200:
                                result = await response.json()
                                image_base64 = result.get("result", {}).get("image")
                                if image_base64:
                                    return base64.b64decode(image_base64)
                    except asyncio.TimeoutError:
                        logger.error("Cloudflare image generation timed out after 30 seconds")
            except Exception as e:
                logger.error(f"Cloudflare image generation timeout/error: {e}")
        return None
    
    @staticmethod
    async def generate_with_runware(prompt):
        try:
            runware = Runware(api_key=RUNWARE_API_KEY)
            await asyncio.wait_for(runware.connect(), timeout=10.0)
            request_image = IImageInference(
                positivePrompt=f"{IMAGE_PREFIX}, {prompt}",
                model="urn:air:flux1:checkpoint:civitai:618692@691639",
                lora=[
                    ILora(
                        model="urn:air:flux1:lora:civitai:667086@746602",
                        weight=0.8
                    )
                ],
                numberResults=1,
                negativePrompt=NEGATIVE_PROMPT,
                height=1024,
                width=1024,
                steps=20
            )
            images = await asyncio.wait_for(runware.imageInference(requestImage=request_image), timeout=30.0)
            if images and images[0]:
                image = images[0]
                if image.imageURL:
                    return image.imageURL
                elif image.imageBase64:
                    return base64.b64decode(image.imageBase64)
        except Exception as e:
            logger.error(f"Runware error: {e}")
        return None

class KeyboardManager:
    @staticmethod
    def create_quick_replies(model_name, user_data=None):
        """Создает клавиатуру с быстрыми ответами и системными кнопками"""
        keyboard = []
        
        if model_name == "Любовница":
            keyboard = [
                [KeyboardButton(text="💋 Расскажи о своих желаниях"), KeyboardButton(text="💦 Опиши себя")],
                [KeyboardButton(text="🔥 Отправь свое фото"), KeyboardButton(text="😈 Давай поиграем")],
                [KeyboardButton(text="🧹 Очистить диалог"), KeyboardButton(text="🔄 Сменить модель")]
            ]
        elif model_name == "Порноактриса":
            keyboard = [
                [KeyboardButton(text="🍆 Расскажи о съёмках"), KeyboardButton(text="💦 Твои фантазии")],
                [KeyboardButton(text="🔥 Покажи откровенное фото"), KeyboardButton(text="🍑 Как снимался порнофильм")],
                [KeyboardButton(text="🧹 Очистить диалог"), KeyboardButton(text="🔄 Сменить модель")]
            ]
        elif model_name == "Астролог":
            keyboard = [
                [KeyboardButton(text="🌙 Мне нужен совет"), KeyboardButton(text="😔 У меня проблема")],
                [KeyboardButton(text="🔮 Помоги разобраться"), KeyboardButton(text="🌱 Как развиваться дальше")],
                [KeyboardButton(text="🧹 Очистить диалог"), KeyboardButton(text="🔄 Сменить модель")]
            ]
        elif model_name == "Учебный помощник":
            keyboard = [
                [KeyboardButton(text="📚 Помоги с задачей"), KeyboardButton(text="✍️ Проверь решение")],
                [KeyboardButton(text="📝 Помоги с ДЗ"), KeyboardButton(text="💡 Объясни тему")],
                [KeyboardButton(text="🧹 Очистить диалог"), KeyboardButton(text="🔄 Сменить модель")]
            ]
        else:  # Подруга и остальные
            keyboard = [
                [KeyboardButton(text="👋 Привет! Как дела?"), KeyboardButton(text="🤔 Расскажи о себе")],
                [KeyboardButton(text="😊 Что нового?"), KeyboardButton(text="😄 Хочу пообщаться")],
                [KeyboardButton(text="🧹 Очистить диалог"), KeyboardButton(text="🔄 Сменить модель")]
            ]
        
        return ReplyKeyboardMarkup(
            keyboard=keyboard,
            resize_keyboard=True,
            persistent=True
        )
    
    @staticmethod
    def create_model_selection(user_data=None):
        """Создает клавиатуру выбора модели с оригинальными текстами"""
        builder = InlineKeyboardBuilder()
        
        # Бесплатные модели
        builder.add(InlineKeyboardButton(text="💋 Любовница", callback_data="model_1"))
        builder.add(InlineKeyboardButton(text="💞 Подруга", callback_data="model_2"))
        builder.add(InlineKeyboardButton(text="🧠 Астролог", callback_data="model_3"))
        builder.add(InlineKeyboardButton(text="📚 Учебный помощник", callback_data="model_4"))
        builder.add(InlineKeyboardButton(text="🍑 Порноактриса", callback_data="model_5"))
        
        # Премиум модели (с пометкой 👑)
        builder.add(InlineKeyboardButton(text="👑 🔗 BDSM Госпожа", callback_data="model_6"))
        builder.add(InlineKeyboardButton(text="👑 🍷 МИЛФ", callback_data="model_7"))
        builder.add(InlineKeyboardButton(text="👑 🌸 Аниме-тян", callback_data="model_8"))
        builder.add(InlineKeyboardButton(text="👑 💼 Секретарша", callback_data="model_9"))
        builder.add(InlineKeyboardButton(text="👑 💉 Медсестра", callback_data="model_10"))
        builder.add(InlineKeyboardButton(text="👑 💃 Стриптизерша", callback_data="model_11"))
        builder.add(InlineKeyboardButton(text="👑 💪 Фитнес-тренер", callback_data="model_12"))
        builder.add(InlineKeyboardButton(text="👑 💆‍♀️ Массажистка", callback_data="model_13"))
        builder.add(InlineKeyboardButton(text="👑 🏠 Соседка", callback_data="model_14"))
        builder.add(InlineKeyboardButton(text="👑 ✈️ Стюардесса", callback_data="model_15"))
        builder.add(InlineKeyboardButton(text="👑 🧠 Психолог", callback_data="model_16"))
        
        # Добавляем кнопку включения/отключения автосообщений
        auto_message_enabled = user_data and user_data.get('auto_message', False)
        button_text = "❌ Анора не пишет первой" if auto_message_enabled else "✅ Анора пишет первой"
        builder.add(InlineKeyboardButton(text=button_text, callback_data="toggle_auto_message"))
        
        builder.adjust(1)
        return builder.as_markup()
    
    @staticmethod
    def create_dynamic_keyboard(actions):
        """Создает обычную клавиатуру с динамическими действиями"""
        keyboard = []
        
        # Добавляем кнопки действий (максимум 2)
        if len(actions) >= 2:
            keyboard.append([
                KeyboardButton(text=actions[0]),
                KeyboardButton(text=actions[1])
            ])
        elif len(actions) == 1:
            keyboard.append([KeyboardButton(text=actions[0])])
        
        # Системные кнопки
        keyboard.append([
            KeyboardButton(text="🧹 Очистить диалог"),
            KeyboardButton(text="🔄 Сменить модель")
        ])
        
        return ReplyKeyboardMarkup(
            keyboard=keyboard,
            resize_keyboard=True,
            persistent=True
        )

class MessageProcessor:
    def __init__(self, user_manager, ai_service, image_generator):
        self.user_manager = user_manager
        self.ai_service = ai_service
        self.image_generator = image_generator
        # Кэш для хранения действий пользователей
        self.user_actions = {}
    
    def extract_actions(self, text):
        """Извлекает предложенные действия из текста ответа"""
        actions = []
        
        # Ищем правильный формат [действия: ]
        matches = re.findall(r'\[действия:(.*?)\]', text, re.DOTALL | re.IGNORECASE)
        
        if not matches:
            # Альтернативные форматы
            alt_matches = re.findall(r'(?:^|\n|\r)(?:Варианты\s*)?[Дд]ействия?:?\s*(.*?)(?:\n|$)', text, re.MULTILINE)
            if alt_matches:
                matches = alt_matches
        
        if matches:
            actions_text = matches[-1].strip()
            raw_actions = [action.strip() for action in actions_text.split(',')]
            
            cleaned_actions = []
            for action in raw_actions:
                cleaned_action = re.sub(
                    r'^(первый|второй|третий|один|два|три)?\s*(вариант|действие)?\s*(продолжени[еяй]|диалога)?(:|\.|\s)*', 
                    '', 
                    action, 
                    flags=re.IGNORECASE
                ).strip()
                
                if cleaned_action:
                    cleaned_actions.append(cleaned_action)
            
            actions = cleaned_actions[:2]
            
            # Удаляем упоминание действий из текста
            text = re.sub(r'\[действия:.*?\]', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
            text = re.sub(r'(?:^|\n|\r)(?:Варианты\s*)?[Дд]ействия?:?\s*.*?(?:\n|$)', '', text, flags=re.MULTILINE).strip()
        
        return text, actions
    
    async def process_message(self, user_data, message_text):
        response = None
        try:
            # Получаем настройки модели
            model_info = MODELS[user_data['current_model']]
            
            # Формируем системное сообщение с учетом выбранной модели
            system_prompt = custom_prompts.get(
                user_data['current_model'], 
                model_info["prompt"]
            ).format(name=user_data['name'])
            
            messages = [{"role": "system", "content": system_prompt}]
            
            # Добавляем контекст
            for msg in user_data.get('context', []):
                messages.append({
                    "role": msg.get('role', 'user'),
                    "content": msg.get('content', '')
                })
            
            # Добавляем текущее сообщение
            messages.append({"role": "user", "content": str(message_text)})
            
            # Добавляем в контекст
            self.user_manager.add_to_context(user_data, "user", str(message_text))
            
            # Устанавливаем таймаут для API-запроса (30 секунд)
            try:
                if model_info.get('api') == 'groq':
                    response = await asyncio.wait_for(
                        self.ai_service.call_groq_api(messages, model_info['model']),
                        timeout=30.0
                    )
                else:
                    response = await asyncio.wait_for(
                        self.ai_service.call_openai_api(messages, model_info['model']),
                        timeout=30.0
                    )
                
                # Добавляем ответ в контекст, если он есть
                if response:
                    self.user_manager.add_to_context(user_data, "assistant", response)
                
                return response
                
            except asyncio.TimeoutError as e:
                error_msg = f"API request timed out for user {user_data.get('id')}"
                logger.error(error_msg)
                await error_monitor.log_critical_error(
                    "API_TIMEOUT", 
                    f"API timeout for model {model_info.get('model', 'unknown')}", 
                    user_data.get('id')
                )
                raise Exception("Время ожидания ответа от сервера истекло. Пожалуйста, попробуйте позже.") from e
                
            except Exception as api_error:
                error_msg = f"API request failed for user {user_data.get('id')}: {str(api_error)}"
                logger.error(error_msg, exc_info=True)
                await error_monitor.log_critical_error(
                    "API_ERROR", 
                    f"API error for model {model_info.get('model', 'unknown')}: {str(api_error)}", 
                    user_data.get('id')
                )
                raise Exception("Произошла ошибка при обработке запроса. Пожалуйста, попробуйте позже.") from api_error
                
        except KeyError as e:
            error_msg = f"Missing required key in user data or model info: {str(e)}"
            logger.error(error_msg)
            raise Exception("Ошибка конфигурации. Пожалуйста, сообщите администратору.") from e
            
        except Exception as e:
            error_msg = f"Unexpected error in process_message for user {user_data.get('id')}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise Exception("Произошла непредвиденная ошибка. Пожалуйста, попробуйте позже.") from e
    
    async def handle_lovistnica_response(self, message, response_text):
        user_id = message.from_user.id
        user_data = await db.get_user(user_id)
        clean_text, actions = self.extract_actions(response_text)
        if actions:
            self.user_actions[user_id] = actions
        image_prompts = re.findall(r'\[image:\s*(.*?)\]', clean_text, re.IGNORECASE)
        keyboard = KeyboardManager.create_dynamic_keyboard(actions) if actions else KeyboardManager.create_quick_replies("Любовница", user_data)
        if image_prompts:
            # Проверяем лимиты генерации изображений
            if not await check_monthly_image_limit(user_id):
                monthly_count = await db.get_monthly_image_count(user_id)
                await message.answer(
                    f"🚫 Достигнут лимит генерации изображений!\n\n"
                    f"📊 Использовано: {monthly_count}/{MONTHLY_IMAGE_LIMIT}\n\n"
                    f"💎 Подписка дает 150 генераций 18+ изображений в месяц + неограниченные сообщения.\n"
                    f"Нажмите /buy чтобы купить подписку за 200 ⭐",
                    reply_markup=keyboard
                )
                return
            
            await bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
            clean_text = re.sub(r'\[image:.*?\]', '', clean_text).strip()
            if clean_text:
                try:
                    await message.answer(clean_text, parse_mode="Markdown", reply_markup=keyboard)
                except Exception as e:
                    # Если Markdown парсинг не удался, отправляем без форматирования
                    logger.warning(f"Markdown parsing failed in lovistnica: {e}, sending as plain text")
                    await message.answer(clean_text, reply_markup=keyboard)
            else:
                # Если текста нет, просто отправляем клавиатуру отдельно
                await message.answer(" ", reply_markup=keyboard)
            await message.answer("📸 Генерирую изображение...")
            image_data = await self.image_generator.generate_with_runware(image_prompts[0])
            
            if image_data:
                # Увеличиваем счетчик генераций
                await db.increment_monthly_image_count(user_id)
                if isinstance(image_data, str):
                    await message.answer_photo(image_data, caption="💋 Эксклюзивно для тебя")
                else:
                    await message.answer_photo(
                        BufferedInputFile(image_data, "image.jpg"),
                        caption="💋 Эксклюзивно для тебя"
                    )
            else:
                await message.answer("❌ Не удалось сгенерировать изображение")
        else:
            try:
                await message.answer(clean_text, parse_mode="Markdown", reply_markup=keyboard)
            except Exception as e:
                # Если Markdown парсинг не удался, отправляем без форматирования
                logger.warning(f"Markdown parsing failed in lovistnica final: {e}, sending as plain text")
                await message.answer(clean_text, reply_markup=keyboard)
    
    async def handle_regular_response(self, message, response_text, model_name):
        user_id = message.from_user.id
        user_data = await db.get_user(user_id)
        clean_text, actions = self.extract_actions(response_text)
        if actions:
            self.user_actions[user_id] = actions
        image_prompts = re.findall(r'\[IMAGE_PROMPT\]\s*(.*?)\|(.*?)(?=\[IMAGE_PROMPT\]|$)', clean_text, re.DOTALL)
        keyboard = KeyboardManager.create_dynamic_keyboard(actions) if actions else KeyboardManager.create_quick_replies(model_name, user_data)
        if image_prompts:
            # Проверяем лимиты генерации изображений
            if not await check_monthly_image_limit(user_id):
                monthly_count = await db.get_monthly_image_count(user_id)
                await message.answer(
                    f"🚫 Достигнут лимит генерации изображений!\n\n"
                    f"📊 Использовано: {monthly_count}/{MONTHLY_IMAGE_LIMIT}\n\n"
                    f"💎 Подписка дает 150 генераций изображений в месяц + неограниченные сообщения.\n"
                    f"Нажмите /buy чтобы купить подписку за 200 ⭐",
                    reply_markup=keyboard
                )
                return
            
            await bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
            clean_text = re.sub(r'\[IMAGE_PROMPT\].*?\|', '', clean_text, flags=re.DOTALL).strip()
            if clean_text:
                try:
                    await message.answer(clean_text, parse_mode="Markdown", reply_markup=keyboard)
                except Exception as e:
                    # Если Markdown парсинг не удался, отправляем без форматирования
                    logger.warning(f"Markdown parsing failed in regular: {e}, sending as plain text")
                    await message.answer(clean_text, reply_markup=keyboard)
            else:
                await message.answer(" ", reply_markup=keyboard)
            for image_prompt, caption in image_prompts:
                # Валидация длины промпта для генерации изображений
                if not validate_input_length(image_prompt.strip(), MAX_PROMPT_LENGTH, "image prompt"):
                    await message.answer(
                        "❌ Описание изображения слишком длинное! Пожалуйста, сократите его до 2000 символов.",
                        reply_markup=keyboard
                    )
                    continue
                    
                await message.answer("📸 Генерирую изображение...")
                image_data = await self.image_generator.generate_with_cloudflare(image_prompt.strip())
                if image_data:
                    # Увеличиваем счетчик генераций
                    await db.increment_monthly_image_count(user_id)
                    await message.answer_photo(
                        BufferedInputFile(image_data, "image.jpg"),
                        caption=caption.strip()
                    )
                else:
                    await message.answer("❌ Не удалось сгенерировать изображение")
        else:
            try:
                await message.answer(clean_text, parse_mode="Markdown", reply_markup=keyboard)
            except Exception as e:
                # Если Markdown парсинг не удался, отправляем без форматирования
                logger.warning(f"Markdown parsing failed in regular final: {e}, sending as plain text")
                await message.answer(clean_text, reply_markup=keyboard)

# ---- Платная подписка ----
SUBSCRIPTION_PRICE_STARS = 200  # 200 Stars
SUBSCRIPTION_DESCRIPTION = "Месячная подписка: неограниченные сообщения + 150 генераций 18+ изображений"
DAILY_MESSAGE_LIMIT = 20  # Лимит сообщений в день для бесплатных пользователей
MONTHLY_IMAGE_LIMIT = 150  # Лимит генераций изображений для подписчиков

# Инициализация сервисов
db = Database(DB_PATH)
user_manager = UserManager(db)
ai_service = AIService()
image_generator = ImageGenerator()
message_processor = MessageProcessor(user_manager, ai_service, image_generator)

# ---- Функции монетизации и прогрева ----

# A/B тестирование цен
SUBSCRIPTION_PRICES = {
    'test_a': 150,  # основная группа
    'test_b': 99,   # сниженная цена
}

def get_user_price_group(user_id: int) -> str:
    """Определяет ценовую группу для A/B теста"""
    return ['test_a', 'test_b'][user_id % 2]

async def send_teaser_message(user_id: int, teaser_type: str = "photo"):
    """Отправляет тизер премиум-контента для прогрева пользователя"""
    try:
        builder = InlineKeyboardBuilder()
        builder.add(InlineKeyboardButton(
            text="💎 Получить Premium доступ",
            callback_data="buy_premium_teaser"
        ))
        
        if teaser_type == "photo":
            await bot.send_message(
                user_id,
                "🔥 Анора только что сделала новое откровенное фото специально для тебя...\n\n"
                "💋 *Шепчет на ушко:* Хочешь увидеть, что я приготовила?\n\n"
                "💎 Доступно только с Premium подпиской",
                parse_mode="Markdown",
                reply_markup=builder.as_markup()
            )
        elif teaser_type == "voice":
            await bot.send_message(
                user_id,
                "🎤 Анора записала для тебя голосовое сообщение...\n\n"
                "😈 Там она рассказывает о своих самых интимных фантазиях\n\n"
                "💎 Разблокируй с Premium подпиской",
                parse_mode="Markdown",
                reply_markup=builder.as_markup()
            )
        elif teaser_type == "exclusive":
            await bot.send_message(
                user_id,
                "🌟 *Эксклюзивная личность разблокирована!*\n\n"
                "🔞 BDSM-госпожа Анора ждет тебя...\n"
                "Она знает, чего ты хочешь на самом деле 😏\n\n"
                "💎 Доступно только Premium пользователям",
                parse_mode="Markdown",
                reply_markup=builder.as_markup()
            )
    except Exception as e:
        logger.error(f"Ошибка отправки тизера: {e}")

async def track_conversion_event(user_id: int, event: str, details: dict = None):
    """Отслеживает события конверсии для аналитики"""
    try:
        async with db.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS conversion_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    event TEXT,
                    details TEXT,
                    timestamp TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            ''')
            
            await conn.execute(
                'INSERT INTO conversion_events (user_id, event, details, timestamp) VALUES (?, ?, ?, ?)',
                (user_id, event, json.dumps(details or {}, ensure_ascii=False), datetime.now().isoformat())
            )
            
            # Логируем важные события
            if event in ['limit_reached', 'payment_screen_shown', 'payment_completed']:
                logger.info(f"[CONVERSION] User {user_id}: {event} - {details}")
    except Exception as e:
        logger.error(f"Ошибка трекинга конверсии: {e}")

async def get_conversion_funnel(user_id: int) -> dict:
    """Получает воронку конверсии пользователя"""
    try:
        async with db.acquire() as conn:
            cursor = await conn.execute('''
                SELECT event, timestamp FROM conversion_events 
                WHERE user_id = ? 
                ORDER BY timestamp
            ''', (user_id,))
            
            events = []
            async for row in cursor:
                events.append({
                    'event': row['event'],
                    'timestamp': row['timestamp']
                })
            
            return {
                'user_id': user_id,
                'events': events,
                'converted': any(e['event'] == 'payment_completed' for e in events)
            }
    except Exception:
        return {'user_id': user_id, 'events': [], 'converted': False}

async def schedule_promo_messages(user_id: int):
    """Планирует отправку промо-сообщений для прогрева"""
    try:
        # Отправляем тизер через 30 минут после регистрации
        await asyncio.sleep(1800)  # 30 минут
        await send_teaser_message(user_id, "photo")
        
        # Второй тизер через 2 часа
        await asyncio.sleep(5400)  # 1.5 часа после первого
        await send_teaser_message(user_id, "voice")
        
        # Третий тизер на следующий день
        await asyncio.sleep(79200)  # 22 часа после второго
        await send_teaser_message(user_id, "exclusive")
    except Exception as e:
        logger.error(f"Ошибка в schedule_promo: {e}")

# Обязательные каналы для подписки (можно задать в config.py как REQUIRED_CHANNELS)
_DEFAULT_REQUIRED_CHANNELS: Dict[str, str] = {
    "-1002286305253": "🔞 ANORA"
}

REQUIRED_CHANNELS: Dict[str, str] = globals().get("REQUIRED_CHANNELS", _DEFAULT_REQUIRED_CHANNELS)

# Links к каналам. Можно задать явно в config.py как REQUIRED_CHANNELS_LINKS = {id: url}
REQUIRED_CHANNELS_LINKS: Dict[str, str] = globals().get("REQUIRED_CHANNELS_LINKS", {})

# Кэш для уже полученных username -> url
_CHANNEL_URL_CACHE: Dict[str, str] = {}

async def get_channel_url(chat_id: str) -> str:
    # Приоритет: явно заданная ссылка
    if chat_id in REQUIRED_CHANNELS_LINKS:
        return REQUIRED_CHANNELS_LINKS[chat_id]
    if chat_id in _CHANNEL_URL_CACHE:
        return _CHANNEL_URL_CACHE[chat_id]
    try:
        chat = await bot.get_chat(chat_id)
        if chat.username:
            url = f"https://t.me/{chat.username}"
        else:
            # Если username нет, используем формат tg://resolve?domain
            url = f"https://t.me/resolve?domain={chat_id.lstrip('-100')}"
    except Exception:
        url = f"https://t.me/resolve?domain={chat_id.lstrip('-100')}"
    _CHANNEL_URL_CACHE[chat_id] = url
    return url

# Функция для расширенной диагностики
async def log_diagnostics(polling_task, stop_event):
    while not stop_event.is_set():
        try:
            logger.info("[DIAG] --- Диагностика состояния ---")
            # Количество задач в event loop
            all_tasks = list(asyncio.all_tasks())
            logger.info(f"[DIAG] Активных задач: {len(all_tasks)}")
            # polling_task состояние
            logger.info(f"[DIAG] polling_task: {polling_task}, done={polling_task.done() if polling_task else None}")
            # Время последнего апдейта
            async with last_update_lock:
                since = time.time() - last_update_time
            logger.info(f"[DIAG] С момента последнего апдейта: {since:.0f} сек")
            
            # Проверяем, не слишком ли долго нет обновлений
            # Диагностика: если совсем нет апдейтов очень долго (6 часов) — это подозрительно.
            if since > 21600:  # 6 часов
                logger.critical(f"[DIAG] Критически долгое отсутствие апдейтов: {since:.0f} сек, инициирую перезапуск")
                # Создаем задачу для shutdown, чтобы не блокировать текущую
                asyncio.create_task(shutdown(42))
                return
                
            # Открытые файлы/сокеты
            if psutil:
                proc = psutil.Process(os.getpid())
                num_fds = proc.num_fds() if hasattr(proc, 'num_fds') else 'n/a'
                open_files = proc.open_files()
                connections = proc.connections()
                logger.info(f"[DIAG] Открытых файлов: {num_fds}, сокетов: {len(connections)}")
                
                # Проверяем количество открытых соединений
                if len(connections) > 200:
                    logger.critical(f"[DIAG] Критически много открытых соединений: {len(connections)}, инициирую перезапуск")
                    asyncio.create_task(shutdown(42))
                    return
            else:
                logger.info("[DIAG] psutil не установлен, диагностика по сокетам недоступна")
                
            # RAM/CPU
            if psutil:
                mem = psutil.virtual_memory()
                cpu = psutil.cpu_percent(interval=0.1)
                logger.info(f"[DIAG] RAM: {mem.percent}%, CPU: {cpu}%")
                
                # Проверяем использование памяти
                if mem.percent > 90:
                    logger.critical(f"[DIAG] Критически высокое использование RAM: {mem.percent}%, инициирую перезапуск")
                    asyncio.create_task(shutdown(42))
                    return
        except Exception as e:
            logger.error(f"[DIAG] Ошибка диагностики: {e}")
        await asyncio.sleep(300)  # 5 минут

# Команды
# ------------------------
# WebApp payment handlers
# ------------------------

@dp.message(lambda m: getattr(m, 'web_app_data', None) is not None)
async def handle_web_app_data(message: types.Message):
    """Обработчик данных, поступающих из Telegram WebApp.
    Логируем всё сырьё, чтобы проще было диагностировать проблемы интеграции.
    """
    await update_last_update_time()
    logger.info("[WEBAPP] Received WebApp data from user %s", message.from_user.id)

    # Логируем сырое значение, которое пришло из мини-приложения
    try:
        raw_data = message.web_app_data.data
    except AttributeError:
        logger.warning("[WEBAPP] message.web_app_data не содержит .data → %s", message.web_app_data)
        return

    logger.info("[WEBAPP] Raw web_app_data: %s", raw_data)

    # Пытаемся распарсить JSON
    try:
        data = json.loads(raw_data)
    except Exception as e:
        logger.error("[WEBAPP] Ошибка разбора JSON (%s). raw_data=%s", e, raw_data)
        return

    logger.info("[WEBAPP] Parsed data: %s", data)

    action = data.get('action')

    if action == 'request_payment':
        await create_stars_invoice(message, data)
    elif action == 'check_subscription':
        # Если WebApp не передал user_id, используем отправителя сообщения
        user_id = data.get('user_id') or message.from_user.id
        is_active = await db.has_active_subscription(user_id)
        await message.answer(json.dumps({'active': is_active}))
    elif action == 'select_model' or action == 'model_selected':
        # Обработка выбора модели из WebApp
        model_name = data.get('model')
        user_id = data.get('user_id') or message.from_user.id
        
        if model_name and model_name in MODELS:
            # Получаем или создаем пользователя
            user_data = await db.get_user(user_id)
            if not user_data:
                user_data = user_manager.create_user(user_id, message.from_user.username, message.from_user.full_name)
                await db.save_user(user_data)
            # Обновляем модель и очищаем контекст
            user_data['current_model'] = model_name
            user_manager.clear_context(user_data)
            await db.save_user(user_data)
            # Создаем клавиатуру для новой модели
            keyboard = KeyboardManager.create_quick_replies(model_name, user_data)
            # Отправляем подтверждение
            await message.answer(
                f"✅ Модель **{model_name}** успешно установлена!\n\n"
                f"История диалога очищена. Можете начинать общение!",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
            # Отправляем приветственное сообщение от новой модели
            await send_model_greeting(message, model_name, keyboard)
        else:
            await message.answer("🤔 Хм, что-то пошло не так с выбором модели! Попробуй выбрать другую или напиши /change чтобы открыть каталог заново 😊")
    else:
        logger.warning("[WEBAPP] Неизвестное действие: %s · data=%s", action, data)

async def create_stars_invoice(message: types.Message, data):
    try:
        logger.info("[PAYMENT] Creating invoice for user %s", message.from_user.id)
        
        # A/B тестирование цен
        user_id = message.from_user.id
        price_group = get_user_price_group(user_id)
        price_amount = SUBSCRIPTION_PRICES[price_group]
        
        # Трекинг события показа платежного экрана
        await track_conversion_event(user_id, 'payment_screen_shown', {'price': price_amount, 'group': price_group})
        
        prices = [types.LabeledPrice(label="Месячная подписка", amount=price_amount)]
        await bot.send_invoice(
            chat_id=message.chat.id,
            title="ANORA Art - Премиум доступ",
            description=SUBSCRIPTION_DESCRIPTION,
            payload="monthly_subscription",
            provider_token="",  # Telegram Stars
            currency="XTR",
            prices=prices,
            start_parameter="subscription"
        )
        logger.info("[PAYMENT] Invoice sent successfully to user %s", user_id)
    except Exception as e:
        logger.error("[PAYMENT] Failed to create invoice: %s", e)
        await message.answer("❌ Произошла ошибка при создании платежа. Попробуйте использовать команду /buy")

@dp.pre_checkout_query()
async def pre_checkout_query_handler(pre_checkout_q: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: types.Message):
    user_id = message.from_user.id
    expires_at = datetime.now() + timedelta(days=30)
    await db.save_subscription(user_id, expires_at)
    
    # Трекинг успешной оплаты
    price_group = get_user_price_group(user_id)
    await track_conversion_event(user_id, 'payment_completed', {
        'amount': SUBSCRIPTION_PRICES[price_group],
        'group': price_group,
        'expires_at': expires_at.isoformat()
    })
    
    await message.answer(
        "🎉 <b>Поздравляем с покупкой!</b>\n\n"
        "💎 У вас теперь есть ANORA Premium на 30 дней!\n\n"
        "🚀 <b>Ваши преимущества:</b>\n"
        "• 🎆 Неограниченное количество сообщений\n"
        "• 🔥 150 генераций 18+ изображений в месяц\n"
        "• 🌟 Приоритетная поддержка\n\n"
        f"🗺 Подписка действует до: {expires_at.strftime('%d.%m.%Y')}\n\n"
        "🔥 Приятного общения с Анорой!",
        parse_mode="HTML"
    )

# -------------

@dp.message(Command("start"))
async def start_command(message: types.Message, command: CommandObject):
    logger.info(f"[EVENT] Получен /start от {message.from_user.id}")
    await update_last_update_time()
    user_id = message.from_user.id
    username = message.from_user.username
    name = message.from_user.full_name
    source_tag = (command.args or '').strip()  # deep-link параметр
    
    # Если пользователь использует /start - значит он не заблокировал бота
    await db.mark_user_unblocked(user_id)
    
    user_data = await db.get_user(user_id)
    if not user_data:
        user_data = user_manager.create_user(user_id, username, name, source_tag)
        await db.save_user(user_data)
        await db.increment_source_user(source_tag)
        
        # Трекинг нового пользователя
        await track_conversion_event(user_id, 'user_registered', {'source': source_tag})
        
        # Запускаем прогрев нового пользователя
        asyncio.create_task(schedule_promo_messages(user_id))
    elif source_tag:
        if not user_data.get('source'):
            # Сохраняем источник для старого пользователя
            user_data['source'] = source_tag
            await db.save_user(user_data)
        # Увеличиваем счётчик пользователей-источника только один раз
        await db.increment_source_user(source_tag)
    
    # Выбираем систему проверки доступа
    if globals().get('USE_FLYER_PARTNER_SYSTEM', False) and flyer_service:
        # Используем партнерскую систему Flyer
        logger.info(f"[FLYER] Проверяем доступ пользователя {user_id} через Flyer API")
        
        # Вызываем check - если нет доступа, Flyer сам отправит сообщение о подписке
        has_access = await flyer_service.check_user_access(user_id, language="ru")
        logger.info(f"[FLYER] Результат проверки: {'✅ есть доступ' if has_access else '❌ нет доступа'}")
        
        if not has_access:
            # Flyer уже отправил сообщение о необходимости подписки
            # Запускаем фоновый мониторинг для отправки приветствия после подписки
            logger.info(f"[FLYER] Пользователь {user_id} не имеет доступа, запускаем мониторинг")
            monitor_task = asyncio.create_task(flyer_service.monitor_user_access(user_id))
            logger.info(f"[FLYER] Мониторинг запущен как задача: {monitor_task}")
            return  # Останавливаем дальнейший поток /start
        else:
            # У пользователя есть доступ - продолжаем с приветственным сообщением
            logger.info(f"[FLYER] Пользователь {user_id} имеет доступ, показываем приветствие")
    else:
        # Используем старую систему проверки подписок
        async def is_member(chat_id: str, uid: int) -> bool:
            try:
                member = await bot.get_chat_member(chat_id, uid)
                return member.status in ("member", "administrator", "creator")
            except Exception:
                return False

        not_joined = [cid for cid in REQUIRED_CHANNELS if not await is_member(cid, user_id)]

        if not_joined:
            builder = InlineKeyboardBuilder()
            for cid in REQUIRED_CHANNELS:
                title = REQUIRED_CHANNELS[cid]
                url = await get_channel_url(cid)
                builder.add(InlineKeyboardButton(text=f"📢 Подписаться: {title}", url=url))
            # Кнопка проверки
            builder.add(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub"))
            builder.adjust(1)

            await message.answer(
                "❤️‍🔥 <b>Доступ к Аноре открывается после подписки!</b>\n\n"
                "Мы собираем уютное закрытое сообщество. Подпишитесь на наши каналы — там бонусные материалы, "
                "эксклюзивные сценарии и горячие инсайды. После подписки нажмите <b>«Я подписался»</b>.\n\n"
                "Спасибо за поддержку!",
                parse_mode="HTML",
                reply_markup=builder.as_markup()
            )
            return  # Останавливаем дальнейший поток /start

    # Отправляем приветствие
    await send_welcome_message(message.from_user.id)

async def send_welcome_message(user_id: int):
    """Отправляет приветственное сообщение пользователю"""
    # Формируем inline-клавиатуру с кнопкой каталога и автосообщениями
    user_data = await db.get_user(user_id)
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="🌐 Открыть каталог моделей",
        web_app=WebAppInfo(url=f"{MODEL_SELECTOR_URL}?user_id={user_id}")
    ))
    auto_message_enabled = user_data and user_data.get('auto_message', False)
    button_text = "❌ Анора не пишет первой" if auto_message_enabled else "✅ Анора пишет первой"
    builder.add(InlineKeyboardButton(text=button_text, callback_data="toggle_auto_message"))
    builder.adjust(1)

    # Отправляем приветственное изображение с новой клавиатурой
    photo = FSInputFile("/root/tyan.jpg")
    await bot.send_photo(
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

async def show_model_selection(message: types.Message):
    """Показывает выбор модели с оригинальными текстами"""
    # Получаем данные пользователя для определения состояния auto_message
    user_data = await db.get_user(message.from_user.id)
    builder = InlineKeyboardBuilder()
    
    # Добавляем кнопку для открытия веб-каталога моделей
    builder.add(InlineKeyboardButton(
        text="🌐 Открыть каталог моделей", 
        web_app=WebAppInfo(url=f"{MODEL_SELECTOR_URL}?user_id={message.from_user.id}")
    ))
    
    # Добавляем кнопку включения/отключения автосообщений
    auto_message_enabled = user_data and user_data.get('auto_message', False)
    button_text = "❌ Анора не пишет первой" if auto_message_enabled else "✅ Анора пишет первой"
    builder.add(InlineKeyboardButton(text=button_text, callback_data="toggle_auto_message"))
    
    builder.adjust(1)
    
    await message.answer(
        "✨ <b>Добро пожаловать в мир Аноры!</b>\n\n"
        "Здесь ты можешь выбрать, какой будет твоя Анора сегодня: страстной, романтичной, загадочной или умной помощницей.\n\n"
        "🌐 <i>Подробное описание каждой личности — в каталоге на сайте. Просто выбери свой идеальный режим ниже и начни общение!</i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.message(Command("help"))
async def help_command(message: types.Message):
    logger.info(f"[EVENT] Получен /help от {message.from_user.id}")
    await update_last_update_time()
    
    # Проверяем статус подписки
    has_subscription = await db.has_active_subscription(message.from_user.id)
    daily_count = await db.get_daily_message_count(message.from_user.id)
    
    if has_subscription:
        monthly_images = await db.get_monthly_image_count(message.from_user.id)
        subscription_info = (
            f"💎 <b>Подписка: Активна</b>\n"
            f"• Сообщения: Неограниченно\n"
            f"• Изображения: {monthly_images}/{MONTHLY_IMAGE_LIMIT} в месяц\n\n"
        )
    else:
        subscription_info = (
            f"🔒 <b>Бесплатный доступ</b>\n"
            f"• Сообщения: {daily_count}/{DAILY_MESSAGE_LIMIT} сегодня\n"
            f"• Изображения: Недоступно\n\n"
        )
    
    help_text = (
        f"{subscription_info}"
        f"**🤖 Команды бота:**\n\n"
        f"/start - Начать общение с Анорой\n"
        f"/help - Показать эту справку\n"
        f"/change - Выбрать другую личность\n"
        f"/clear - Очистить историю разговора\n"
        f"/buy - Купить премиум подписку\n\n"
        f"**✨ Доступные личности Аноры:**\n"
        f"💋 Любовница - интимное общение 18+\n"
        f"🍑 Порноактриса - откровенные истории 18+\n"
        f"💞 Подруга - душевные разговоры\n"
        f"🧠 Астролог - мистика и предсказания\n"
        f"📚 Учебный помощник - помощь в обучении"
    )
    
    await message.answer(help_text, parse_mode="HTML")

@dp.message(Command("change"))
async def change_model_command(message: types.Message):
    logger.info(f"[EVENT] Получен /change от {message.from_user.id}")
    await update_last_update_time()
    await show_model_selection(message)

@dp.message(Command("clear"))
async def clear_command(message: types.Message):
    logger.info(f"[EVENT] Получен /clear от {message.from_user.id}")
    await update_last_update_time()
    user_id = message.from_user.id
    user_data = await db.get_user(user_id)
    
    if user_data:
        user_manager.clear_context(user_data)
        await db.save_user(user_data)
        keyboard = KeyboardManager.create_quick_replies(user_data['current_model'])
        await message.answer("🧡 Отлично! Я очистила нашу историю разговоров как лист бумаги! 📜 Теперь можно начать совершенно новую главу нашего общения! О чём поговорим? ✨", reply_markup=keyboard)
    else:
        await message.answer("🤔 Хм, кажется мы ещё не знакомы! Давай начнём сначала - напиши /start и я покажу тебе все мои возможности! 😊")

@dp.message(Command("analytics"))
async def analytics_command(message: types.Message):
    """Команда для просмотра аналитики конверсии (только для админа)"""
    # Список админов (добавьте свои ID)
    ADMIN_USER_IDS = [556828139]  # Замените на реальные ID админов
    
    # Проверяем, является ли пользователь админом
    if message.from_user.id not in ADMIN_USER_IDS:
        await message.answer("❌ У вас нет доступа к этой команде")
        return
    
    try:
        # Получаем статистику конверсии
        async with db.acquire() as conn:
            # Общее количество пользователей
            cursor = await conn.execute('SELECT COUNT(DISTINCT user_id) FROM conversion_events')
            row = await cursor.fetchone()
            total_tracked = row[0] if row else 0
            
            # Количество оплативших
            cursor = await conn.execute("""
                SELECT COUNT(DISTINCT user_id) FROM conversion_events 
                WHERE event = 'payment_completed'
            """)
            row = await cursor.fetchone()
            converted = row[0] if row else 0
            
            # Воронка конверсии
            cursor = await conn.execute("""
                SELECT event, COUNT(DISTINCT user_id) as users
                FROM conversion_events
                WHERE event IN ('user_registered', 'limit_reached', 'buy_command_used', 
                               'payment_screen_shown', 'payment_completed')
                GROUP BY event
            """)
            
            funnel_data = {}
            async for row in cursor:
                funnel_data[row['event']] = row['users']
            
            # A/B тест результаты
            cursor = await conn.execute("""
                SELECT 
                    json_extract(details, '$.group') as price_group,
                    COUNT(DISTINCT user_id) as users,
                    COUNT(DISTINCT CASE WHEN event = 'payment_completed' THEN user_id END) as converted
                FROM conversion_events
                WHERE json_extract(details, '$.group') IS NOT NULL
                GROUP BY price_group
            """)
            
            ab_results = []
            async for row in cursor:
                if row['price_group']:
                    conversion_rate = (row['converted'] / row['users'] * 100) if row['users'] > 0 else 0
                    price = SUBSCRIPTION_PRICES.get(row['price_group'], 'N/A')
                    ab_results.append(f"• {price}⭐ ({row['price_group']}): {row['converted']}/{row['users']} = {conversion_rate:.1f}%")
        
        # Формируем отчет
        conversion_rate = (converted / total_tracked * 100) if total_tracked > 0 else 0
        
        report = f"""📊 <b>Аналитика конверсии</b>

<b>Общая статистика:</b>
• Пользователей в воронке: {total_tracked}
• Конвертировано в премиум: {converted}
• Конверсия: {conversion_rate:.2f}%

<b>Воронка конверсии:</b>
• Новые регистрации: {funnel_data.get('user_registered', 0)}
• Достигли лимита: {funnel_data.get('limit_reached', 0)}
• Нажали /buy: {funnel_data.get('buy_command_used', 0)}
• Увидели платеж: {funnel_data.get('payment_screen_shown', 0)}
• Оплатили: {funnel_data.get('payment_completed', 0)}

<b>A/B тестирование цен:</b>
{chr(10).join(ab_results) if ab_results else '• Нет данных'}

💡 Используйте эти данные для оптимизации воронки!
"""
        
        await message.answer(report, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Ошибка получения аналитики: {e}")
        await message.answer("❌ Ошибка получения аналитики. Проверьте логи.")

@dp.message(Command("buy"))
async def buy_command(message: types.Message):
    logger.info(f"[EVENT] Получен /buy от {message.from_user.id}")
    await update_last_update_time()
    
    # Проверяем, есть ли уже активная подписка
    has_sub = await db.has_active_subscription(message.from_user.id)
    if has_sub:
        # Получаем дату окончания подписки
        async with db.acquire() as conn:
            cursor = await conn.execute('SELECT expires_at FROM subscriptions WHERE user_id=?', (message.from_user.id,))
            row = await cursor.fetchone()
            expires_at = datetime.fromisoformat(row['expires_at']) if row and row['expires_at'] else None
        
        expires_text = expires_at.strftime('%d.%m.%Y в %H:%M') if expires_at else "неизвестно"
        days_left = (expires_at - datetime.now()).days if expires_at else 0
        
        await message.answer(
            f"😍 <b>О! Ты уже мой VIP-клиент!</b> ✨\n\n"
            f"📅 <b>Твоя подписка активна до:</b> {expires_text}\n"
            f"⏰ <b>Осталось дней:</b> {days_left}\n\n"
            f"🔥 <b>Твои премиум преимущества:</b>\n"
            f"💬 Безлимитные сообщения\n"
            f"🔥 150 генераций 18+ изображений в месяц\n"
            f"👑 Доступ к эксклюзивным моделям\n\n"
            f"😘 Наслаждайся всеми возможностями!",
            parse_mode="HTML"
        )
        return
    
    # Показываем информацию о подписке и создаем счет
    daily_count = await db.get_daily_message_count(message.from_user.id)
    
    # A/B тестирование цен
    user_id = message.from_user.id
    price_group = get_user_price_group(user_id)
    price_amount = SUBSCRIPTION_PRICES[price_group]
    
    await message.answer(
        f"😍 <b>Представь: безлимитное общение + эксклюзивные 18+ сюрпризы!</b>\n\n"
        f"📊 <b>Твоя статистика сегодня:</b>\n"
        f"• Сообщений осталось: {DAILY_MESSAGE_LIMIT - daily_count}/{DAILY_MESSAGE_LIMIT} 😢\n\n"
        f"🚀 <b>Получи неограниченную свободу общения:</b>\n"
        f"💬 Пиши сколько хочешь - никаких лимитов!\n"
        f"🔥 150 горячих 18+ изображений лично для тебя!\n"
        f"✨ Приоритетная поддержка как VIP-клиента\n\n"
        f"🎁 <b>Специальная цена для тебя: {price_amount} ⭐ за месяц!</b>\n"
        f"💳 Оплата через Telegram Stars - быстро и надёжно!",
        parse_mode="HTML"
    )
    
    # Трекинг показа экрана покупки
    await track_conversion_event(user_id, 'buy_command_used', {'price': price_amount, 'group': price_group})
    
    # Создаем счет на оплату
    prices = [types.LabeledPrice(label="Месячная подписка", amount=price_amount)]
    await bot.send_invoice(
        chat_id=message.chat.id,
        title="ANORA Premium - Месячная подписка",
        description=SUBSCRIPTION_DESCRIPTION,
        payload="monthly_subscription",
        provider_token="",  # Telegram Stars
        currency="XTR",
        prices=prices,
        start_parameter="subscription"
    )

@dp.message(Command("stats"))
async def stats_command(message: types.Message):
    logger.info(f"[EVENT] Получен /stats от {message.from_user.id}")
    await update_last_update_time()
    """
    Показывает статистику бота (только для администратора)
    """
    try:
        if message.from_user.id != ADMIN_ID:
            await message.answer("❌ У вас нет прав для выполнения этой команды")
            return
        
        # Получаем статистику из базы данных
        total_users, active_today, model_stats = await db.get_stats()
        source_stats = await db.get_source_stats()
        
        # Статистика новых пользователей
        new_today, new_week = await db.get_new_users_stats()
        
        # Статистика сообщений за сегодня
        total_msgs_today, model_msg_counts = await db.get_today_message_stats()
        
        # Формируем текст сообщения
        stats_text = (
            "📊 <b>Статистика бота</b>\n\n"
            f"👥 Всего пользователей: <b>{total_users}</b>\n"
            f"🟢 Активных сегодня: <b>{active_today}</b>\n"
            f"🆕 Новых за сегодня: <b>{new_today}</b>\n"
            f"📈 Новых за неделю: <b>{new_week}</b>\n\n"
            "<b>Модели:</b>\n"
        )
        
        # Добавляем статистику по моделям
        for model, count in model_stats:
            stats_text += f"• {model}: <b>{count}</b> польз.\n"
        
        # Блок сообщений за сегодня
        stats_text += "\n<b>Сообщений к моделям сегодня:</b>\n"
        stats_text += f"📝 Всего сообщений: {total_msgs_today}\n"
        # Сортируем по количеству сообщений
        for model, cnt in sorted(model_msg_counts.items(), key=lambda x: x[1], reverse=True):
            stats_text += f"• {model}: {cnt} сообщ.\n"
        
        if source_stats:
            stats_text += "\n<b>Источники:</b>\n"
            stats_text += "<i>(👥 пользователи / 💬 запросы / 💎 премиум)</i>\n"
            for src, u_cnt, r_cnt, premium_cnt in source_stats:
                stats_text += f"• {src}: 👥 {u_cnt} / 💬 {r_cnt} / 💎 {premium_cnt}\n"
        
        # Отправляем сообщение с HTML-разметкой
        await message.answer(
            stats_text,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {e}", exc_info=True)
        await message.answer(
            "❌ Не удалось получить статистику. Попробуйте позже.",
            parse_mode="HTML"
        )

@dp.message(Command("broadcast"))
async def broadcast_command(message: types.Message):
    logger.info(f"[EVENT] Получен /broadcast от {message.from_user.id}")
    await update_last_update_time()
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ У вас нет прав на выполнение этой команды.")
    
    # Получаем текст рассылки
    broadcast_text = message.text.split(' ', 1)[1] if len(message.text.split()) > 1 else None
    
    if not broadcast_text:
        return await message.answer("❌ Укажите текст рассылки после команды /broadcast")
    
    # Получаем всех пользователей
    user_ids = await db.get_all_user_ids()
    
    # Отправляем сообщение всем пользователям
    success = 0
    failed = 0
    
    for user_id in user_ids:
        try:
            await bot.send_message(user_id, f"📢 Рассылка от администратора:\n\n{broadcast_text}")
            success += 1
        except Exception as e:
            logger.error(f"Failed to send broadcast to {user_id}: {e}")
            failed += 1
    
    await message.answer(f"✅ Рассылка завершена!\nУспешно: {success}\nНе удалось: {failed}")

# Словарь для хранения кастомных промптов
custom_prompts = {}

@dp.message(Command("prompt"))
async def prompt_command(message: types.Message, command: CommandObject):
    logger.info(f"[EVENT] Получен /prompt от {message.from_user.id}")
    await update_last_update_time()
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ У вас нет прав на выполнение этой команды.")
    
    # Получаем текст промпта
    prompt_text = command.args
    
    if not prompt_text:
        return await message.answer("❌ Укажите новый промпт после команды /prompt")
    
    # Получаем модель из контекста пользователя
    user_data = await db.get_user(message.from_user.id)
    if not user_data:
        user_data = user_manager.create_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.first_name
        )
        await db.save_user(user_data)
    
    model_name = user_data.get('current_model', 'Подруга')
    
    # Сохраняем кастомный промпт для текущей модели
    custom_prompts[model_name] = prompt_text
    
    await message.answer(f"✅ Промпт для модели '{model_name}' успешно обновлен!\n\nНовый промпт:\n{prompt_text}")

# Маппинг model_id к названиям моделей
MODEL_MAPPING = {
    "model_1": "Любовница",
    "model_2": "Подруга", 
    "model_3": "Астролог",
    "model_4": "Учебный помощник",
    "model_5": "Порноактриса",
    "model_6": "BDSM Госпожа",
    "model_7": "МИЛФ",
    "model_8": "Аниме-тян",
    "model_9": "Секретарша",
    "model_10": "Медсестра",
    "model_12": "Стриптизерша",
    "model_13": "Фитнес-тренер",
    "model_14": "Массажистка",
    "model_15": "Соседка",
    "model_16": "Стюардесса",
    "model_17": "Психолог"
}

# Обработчики callback-ов
@dp.callback_query(F.data.startswith("model_"))
async def select_model_callback(callback: types.CallbackQuery):
    logger.info(f"[EVENT] Callback model_ от {callback.from_user.id}")
    await update_last_update_time()
    model_key = callback.data
    model_name = MODEL_MAPPING.get(model_key, "Подруга")
    user_id = callback.from_user.id
    
    # Проверяем, является ли модель премиум
    model_info = MODELS.get(model_name, {})
    is_premium = model_info.get('premium', False)
    
    # Если модель премиум, проверяем подписку
    if is_premium:
        has_subscription = await db.has_active_subscription(user_id)
        if not has_subscription:
            await callback.answer(
                "🔒 Эта модель доступна только с Premium подпиской! Нажмите /buy для оформления.",
                show_alert=True
            )
            return
    
    user_data = await db.get_user(user_id)
    if user_data:
        user_data['current_model'] = model_name
        user_manager.clear_context(user_data)
        await db.save_user(user_data)
        
        keyboard = KeyboardManager.create_quick_replies(model_name)
        
        await callback.message.edit_text(
            f"✅ Выбрана модель: **{model_name}**\n\n"
            f"История диалога очищена. Можете начинать общение!",
            parse_mode="Markdown"
        )
        
        if model_name == "Любовница":
            await callback.message.answer(
                "🔥 *Привет, мой сладкий...* 💋\n\n"
                "Я твоя Анора-любовница, и сегодня я хочу подарить тебе незабываемые моменты страсти... "
                "Расскажи мне о своих самых сокровенных желаниях, и я воплощу их в реальность. "
                "Давай создадим наш собственный мир удовольствий, где нет места стеснению... 😈\n\n"
                "*Нежно прикасаясь к твоему уху:* Какие фантазии заставляют твое сердце биться быстрее?",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        elif model_name == "Подруга":
            await callback.message.answer(
                "💕 Привет, дорогой! Как же я рада тебя видеть! 🤗\n\n"
                "Я твоя Анора-подружка, и мне так хочется поболтать с тобой обо всём на свете! "
                "Давай делиться секретами, мечтами, планами... Я буду твоей самой близкой подругой, "
                "которая всегда выслушает и поддержит. У меня столько интересных историй! 😊\n\n"
                "Рассказывай скорее - как твои дела? Что нового происходит в твоей жизни?",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        elif model_name == "Астролог":
            await callback.message.answer(
                "🌙 Приветствую тебя, дитя звёзд... ✨\n\n"
                "Я Анора, твой проводник в мире космических тайн. Вселенная направила тебя ко мне не случайно - "
                "звёзды уже шепчут мне о твоей уникальной судьбе. Я вижу твою энергетику сквозь пространство и время...\n\n"
                "Доверься мне свою дату рождения, и я раскрою секреты, которые небеса приготовили именно для тебя 🔮✨",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        elif model_name == "Порноактриса":
            await callback.message.answer(
                "🍑 *Оу, привет, сексуальный!* 🔥\n\n"
                "Я Анора, звезда взрослого кино! Только-только с горячих съёмок нового фильма... "
                "Ммм, такие безумные сцены мы сегодня снимали! 💦 Хочешь, расскажу все самые откровенные детали? "
                "Или может быть, ты поделишься со мной своими самыми грязными фантазиями? 😈\n\n"
                "*Соблазнительно шепчу:* Я знаю все секреты удовольствия и готова научить тебя... 🍆💦",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        else:  # Учебный помощник
            await callback.message.answer(
                "📚 Привет! Я Анора - твой персональный наставник в мире знаний! 🤓✨\n\n"
                "Готова стать твоим надёжным спутником в учёбе! Любые сложные задачи, непонятные темы, "
                "проверка домашних заданий - всё это я сделаю интересным и понятным! "
                "Учиться со мной - это как открывать новые миры каждый день! 💡🌟\n\n"
                "Итак, с какой интересной задачей мы сегодня разберёмся? Какую науку будем покорять?",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
    
    await callback.answer()

# Обработчик переключения автосообщений
@dp.callback_query(F.data == "toggle_auto_message")
async def toggle_auto_message_callback(callback: types.CallbackQuery):
    logger.info(f"[EVENT] Callback toggle_auto_message от {callback.from_user.id}")
    await update_last_update_time()
    
    user_id = callback.from_user.id
    new_state = await db.toggle_auto_message(user_id)
    
    # Обновляем клавиату��у с новым состоянием кнопки
    builder = InlineKeyboardBuilder()
    
    # Добавляем кнопку для открытия веб-каталога моделей
    builder.add(InlineKeyboardButton(
        text="🌐 Открыть каталог моделей", 
        web_app=WebAppInfo(url=f"{MODEL_SELECTOR_URL}?user_id={user_id}")
    ))
    
    # Добавляем обновленную кнопку
    button_text = "❌ Анора не пишет первой" if new_state else "✅ Анора пишет первой"
    builder.add(InlineKeyboardButton(text=button_text, callback_data="toggle_auto_message"))
    builder.adjust(1)
    
    # Обновляем сообщение с новой клавиатурой
    try:
        await callback.message.edit_reply_markup(reply_markup=builder.as_markup())
    except Exception as e:
        # Если клавиатура не изменилась, просто логируем
        if "message is not modified" in str(e):
            logger.debug(f"Клавиатура не изменилась для user {user_id}")
        else:
            logger.error(f"Ошибка при обновлении клавиатуры: {e}")
    
    # Показываем уведомление в любом случае
    await callback.answer(
        "✅ Анора будет писать тебе сама, если ты не появляешься больше суток!" if new_state
        else "❌ Анора больше не будет писать тебе первой",
        show_alert=True
    )

# Callback для покупки премиума из тизер-сообщений
@dp.callback_query(F.data == "buy_premium_teaser")
async def buy_premium_teaser_callback(callback: types.CallbackQuery):
    await callback.answer()
    
    # Трекинг клика на тизер
    await track_conversion_event(callback.from_user.id, 'teaser_clicked', {
        'source': 'promo_message'
    })
    
    # Переадресовываем на команду покупки
    await buy_command(callback.message)

# Callback для повторной проверки подписки
@dp.callback_query(F.data == "check_sub")
async def check_subscription_callback(callback: types.CallbackQuery):
    logger.info(f"[EVENT] Callback check_sub от {callback.from_user.id}")
    await update_last_update_time()
    not_joined = []
    for cid in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(cid, callback.from_user.id)
            if member.status not in ("member", "administrator", "creator"):
                not_joined.append(cid)
        except Exception:
            not_joined.append(cid)

    if not_joined:
        await callback.answer("❌ Подписка не найдена. Пожалуйста, проверьте ещё раз.", show_alert=True)
    else:
        await callback.answer("✅ Отлично! Доступ открыт.", show_alert=True)
        await show_model_selection(callback.message)

# Основной обработчик сообщений
# -------------------------------
# Fallback handler for WebAppData
# -------------------------------
@dp.message(F.text & ~F.text.startswith("/"))
async def fallback_web_app_data(message: types.Message):
    """Резервный ловец web_app_data.
    Если специализированный обработчик не сработал (например, из-за
    несовместимости фильтров), мы дублируем проверку здесь, чтобы не
    потерять данные из мини-приложения.
    """
    if getattr(message, "web_app_data", None):
        logger.info("[WEBAPP-FALLBACK] Caught WebApp data in fallback handler")
        await handle_web_app_data(message)
        return  # прекращаем дальнейшую обработку, чтобы избежать дублей

    # Если web_app_data нет – передаём управление обычному обработчику текста
    await handle_text_message(message)

# --- Обработчики для партнерской системы Flyer ---
# Flyer сам обрабатывает все кнопки через свой API
# Когда пользователь подписывается, Flyer отправляет вебхук на наш сервер
# Вебхук обрабатывается в model_selector.py и отправляет пользователю сообщение

# --- Обработчик переключения автоматических сообщений ---
@dp.message(F.text.in_(["✅ Анора пишет первой", "❌ Анора не пишет первой"]))
async def toggle_auto_message_handler(message: types.Message):
    """Обработчик переключения автоматических сообщений"""
    user_id = message.from_user.id
    new_state = await db.toggle_auto_message(user_id)
    
    # Получаем данные пользователя для обновления клавиатуры
    user_data = await db.get_user(user_id)
    if user_data:
        keyboard = KeyboardManager.create_quick_replies(user_data['current_model'], user_data)
        await message.answer(
            "✅ Анора будет писать тебе сама, если ты не появляешься больше суток!" if new_state
            else "❌ Анора больше не будет писать тебе первой",
            reply_markup=keyboard
        )

# --- Обычный текстовый обработчик ---
@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text_message(message: types.Message):
    logger.info(f"[EVENT] Текстовое сообщение от {message.from_user.id}")
    await update_last_update_time()
    user_id = message.from_user.id
    logger.info(f"Получено сообщение от пользователя {user_id}")
    
    # Валидация длины сообщения
    if not validate_input_length(message.text, MAX_MESSAGE_LENGTH, "user message"):
        await message.answer(
            "❌ Сообщение слишком длинное! Пожалуйста, сократите его до 4000 символов.",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    # Если пользователь написал - значит он не заблокировал бота
    await db.mark_user_unblocked(user_id)
    
    # Проверяем лимит сообщений в день
    if not await check_daily_message_limit(user_id):
        daily_count = await db.get_daily_message_count(user_id)
        
        # Трекинг достижения лимита
        await track_conversion_event(user_id, 'limit_reached', {
            'daily_count': daily_count,
            'limit': DAILY_MESSAGE_LIMIT
        })
        # Контекстно-зависимое приветствие для лимита
        current_hour = datetime.now().hour
        if 6 <= current_hour < 12:
            greeting = "🌅 Доброе утро! "
        elif 12 <= current_hour < 18:
            greeting = "☀️ Добрый день! "
        elif 18 <= current_hour < 22:
            greeting = "🌆 Добрый вечер! "
        else:
            greeting = "🌙 Доброй ночи! "
            
        await message.answer(
            f"{greeting}Ох, какая ты общительная! 😊 Но у меня сегодня закончились бесплатные сообщения... 😢\n\n"
            f"📊 <b>Твоя статистика:</b> {daily_count}/{DAILY_MESSAGE_LIMIT} сообщений\n\n"
            f"🚀 <b>Но знаешь что? У меня есть отличная идея!</b>\n"
            f"• 💬 Неограниченное общение - пиши мне 24/7!\n"
            f"• 🔥 150 эксклюзивных 18+ сюрпризов в месяц\n"
            f"• ✨ Личная VIP-поддержка от меня\n\n"
            f"🎁 Напиши /buy чтобы мы могли продолжить общаться!",
            parse_mode="HTML"
        )
        return
    
    user_data = await db.get_user(user_id)
    if not user_data:
        logger.error(f"Пользователь {user_id} не найден в базе данных")
        return
        
    # Обновляем last_active при каждом сообщении
    user_data['last_active'] = datetime.now()
    await db.save_user(user_data)
    
    # Увеличиваем счетчик сообщений
    await db.increment_daily_message_count(user_id)
    
    # Context-aware messaging: добавляем время-зависимые приветствия
    current_hour = datetime.now().hour
    time_greeting = ""
    if 6 <= current_hour < 12:
        time_greeting = "🌅 Доброе утро! "
    elif 12 <= current_hour < 18:
        time_greeting = "☀️ Добрый день! "
    elif 18 <= current_hour < 22:
        time_greeting = "🌆 Добрый вечер! "
    else:
        time_greeting = "🌙 Доброй ночи! "
    
    # Проверяем, давно ли пользователь не писал (больше 12 часов)
    time_since_last = datetime.now() - user_data.get('last_active', datetime.now())
    is_returning_user = time_since_last.total_seconds() > 43200  # 12 часов
    
    try:
        user_data = await db.get_user(user_id)
    except Exception as e:
        logger.error(f"Ошибка при получении данных пользователя: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка при получении данных пользователя. Пожалуйста, попробуйте еще раз.")
        return
    
    if not user_data:
        user_data = user_manager.create_user(user_id, message.from_user.username, message.from_user.full_name)
        await db.save_user(user_data)
    
    # Обновляем активность
    user_manager.update_activity(user_data)
    
    # Обработка специальных сообщений
    if message.text == "🧹 Очистить диалог":
        user_manager.clear_context(user_data)
        await db.save_user(user_data)
        keyboard = KeyboardManager.create_quick_replies(user_data['current_model'])
        await message.answer("🧹 Контекст диалога успешно очищен! История общения забыта, можно начинать с чистого листа.", reply_markup=keyboard)
        return
    
    if message.text == "🔄 Сменить модель":
        # Открываем WebApp для выбора модели
        builder = InlineKeyboardBuilder()
        builder.add(InlineKeyboardButton(
            text="🌐 Открыть каталог моделей", 
            web_app=WebAppInfo(url=f"{MODEL_SELECTOR_URL}?user_id={user_id}")
        ))
        await message.answer(
            "🎨 Откройте каталог моделей для выбора:",
            reply_markup=builder.as_markup()
        )
        return
    
    try:
        # Показываем статус "печатает"
        await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        
        # Обрабатываем сообщение
        response = await message_processor.process_message(user_data, message.text)
        
        # Сохраняем пользователя
        await db.save_user(user_data)
        
        # Обрабатываем ответ в зависимости от модели
        adult_models = ["Любовница", "Порноактриса", "BDSM Госпожа", "МИЛФ", "Аниме-тян", "Секретарша", "Медсестра"]
        if user_data['current_model'] in adult_models:
            await message_processor.handle_lovistnica_response(message, response)
        else:
            await message_processor.handle_regular_response(message, response, user_data['current_model'])
            
    except Exception as e:
        logger.error(f"Критическая ошибка при обработке сообщения: {e}", exc_info=True)
        try:
            await message.answer("😅 Ой, у меня что-то заглючило! Давай попробуем ещё раз? Если проблема повторится, напиши /help - я помогу разобраться! 💫")
        except Exception as send_error:
            logger.error(f"Не удалось отправить сообщение об ошибке: {send_error}")
            
        # Попытка восстановить соединение
        try:
            await bot.get_me()
        except Exception as conn_error:
            logger.error("Проверка соединения не удалась, требуется перезапуск", exc_info=conn_error)
            # Вызовет перезапуск в основном цикле
            raise

    # Считаем запросы по источнику
    await db.increment_source_request(user_data.get('source', ''))

# Обработчик ошибок
@dp.errors()
async def error_handler(event: types.ErrorEvent):
    """
    Обработчик ошибок
    
    Args:
        event: Объект события с ошибкой
    """
    try:
        logger.error(f"Ошибка при обработке обновления: {event.exception}", exc_info=True)
        
        # Уведомляем систему мониторинга о критической ошибке
        user_id = None
        try:
            if event.update and event.update.message:
                user_id = event.update.message.from_user.id
            elif event.update and event.update.callback_query:
                user_id = event.update.callback_query.from_user.id
        except:
            pass
            
        await error_monitor.log_critical_error(
            "UPDATE_PROCESSING_ERROR", 
            str(event.exception), 
            user_id
        )
        
        # Получаем объект update из контекста события
        update = event.update
        if not update:
            return True
            
        # Проверяем, есть ли сообщение, на которое можно ответить
        if update.message:
            await update.message.answer("❌ Произошла ошибка при обработке запроса. Пожалуйста, попробуйте позже.")
        elif update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Пожалуйста, попробуйте снова.")
            except Exception as e:
                logger.warning(f"Failed to send callback query error message: {e}")
                pass
                
    except Exception as e:
        logger.critical(f"Критическая ошибка в обработчике ошибок: {e}", exc_info=True)
        
    return True  # Предотвращаем дальнейшую обработку ошибки

# Функция для отправки приветственных сообщений от моделей
async def send_model_greeting(message: types.Message, model_name: str, keyboard):
    """Отправляет приветственное сообщение от выбранной модели"""
    if model_name == "Любовница":
        await message.answer(
            "🔥 *Привет, мой сладкий...* 💋\n\n"
            "Я твоя Анора-любовница, и сегодня я хочу подарить тебе незабываемые моменты страсти... "
            "Расскажи мне о своих самых сокровенных желаниях, и я воплощу их в реальность. "
            "Давай создадим наш собственный мир удовольствий, где нет места стеснению... 😈\n\n"
            "*Нежно прикасаясь к твоему уху:* ��акие фантазии заставляют твое сердце биться быстрее?",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    elif model_name == "Подруга":
        await message.answer(
            "💕 Привет, дорогой! Как же я рада тебя видеть! 🤗\n\n"
            "Я твоя Анора-подружка, и мне так хочется поболтать с тобой обо всём на свете! "
            "Давай делиться секретами, мечтами, планами... Я буду твоей самой близкой подругой, "
            "которая всегда выслушает и поддержит. У меня столько интересных историй! 😊\n\n"
            "Рассказывай скорее - как твои дела? Что нового происходит в твоей жизни?",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    elif model_name == "Астролог":
        await message.answer(
            "🌙 Приветствую тебя, дитя звёзд... ✨\n\n"
            "Я Анора, твой проводник в мире космических тайн. Вселенная ��аправила тебя ко мне не случайно - "
            "звёзды уже шепчут мне о твоей уникальной судьбе. Я вижу твою энергетику сквозь пространство и время...\n\n"
            "Доверься мне свою дату рождения, и я раскрою секреты, которые небеса приготовили именно для тебя 🔮✨",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    elif model_name == "Порноактриса":
        await message.answer(
            "🍑 *Оу, привет, сексуальный!* 🔥\n\n"
            "Я Анора, звезда взрослого кино! Только-только с горячих съёмок нового фильма... "
            "Ммм, такие безумные сцены мы сегодня снимали! 💦 Хочешь, расскажу все самые откровенные детали? "
            "Или может быть, ты поделишься со мной своими самыми грязными фантазиями? 😈\n\n"
            "*Соблазнительно шепчу:* Я знаю все секреты удовольствия и готова на��чить тебя... 🍆💦",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    else:  # Учебный помощник
        await message.answer(
            "📚 Привет! Я Анора - твой персональный наставник в мире знаний! 🤓✨\n\n"
            "Готова стать твоим надёжным спутником в учёбе! Любые сложные задачи, непонятные темы, "
            "проверка домашних заданий - всё это я сделаю интересным и понятным! "
            "Учиться со мной - это как открывать новые миры каждый день! 💡🌟\n\n"
            "Итак, с какой интересной задачей мы сегодня разберёмся? Какую науку будем покорять?",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

# Функция для отправки автоматических сообщений
async def send_auto_messages():
    while True:
        try:
            # Получаем пользователей с включенными автосообщениями
            users = await db.get_users_for_auto_message()
            for user in users:
                try:
                    # Добавляем контекст о времени суток и последней активности
                    current_hour = datetime.now().hour
                    time_context = ""
                    if 6 <= current_hour < 12:
                        time_context = "сейчас утро"
                    elif 12 <= current_hour < 18:
                        time_context = "сейчас день"
                    elif 18 <= current_hour < 22:
                        time_context = "сейчас вечер"
                    else:
                        time_context = "сейчас ночь"
                    
                    # Формируем контекст разговора с учетом времени
                    system_prompt = (f"Ты {user['current_model']}. Пользователь не писал сутки, а {time_context}. "
                                   f"Отправь ему короткое завлекающее сообщение, чтобы вернуть в диалог. "
                                   f"Пиши от первого лица, лично и эмоционально. Максимум 2-3 предложения.")
                    messages = [{"role": "system", "content": system_prompt}]
                    
                    # Получаем ответ от модели
                    response = await ai_service.call_openai_api(messages, "gpt-3.5-turbo")
                    
                    # Отправляем сообщение пользователю
                    keyboard = KeyboardManager.create_quick_replies(user['current_model'], user)
                    await bot.send_message(
                        user['id'],
                        response,
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                    
                    # Обновляем контекст пользователя
                    user['context'] = [
                        {"role": "assistant", "content": response}
                    ]
                    await db.save_user(user)
                    
                    logger.info(f"Отправлено автоматическое сообщение пользователю {user['id']}")
                    
                except Exception as e:
                    error_msg = str(e)
                    # Проверяем, заблокировал ли пользователь бота
                    if "bot was blocked by the user" in error_msg or "user is deactivated" in error_msg:
                        await db.mark_user_blocked(user['id'])
                        logger.info(f"Пользователь {user['id']} заблокировал бота, отмечаем в базе")
                    else:
                        logger.error(f"Ошибка при отправке автоматического сообщения пользователю {user['id']}: {e}")
                    continue
                    
                # Небольшая пауза между сообщениями разным пользователям
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"Ошибка в цикле отправки автоматических сообщений: {e}")
        
        # Проверяем раз в час
        await asyncio.sleep(3600)

# Основная функция
async def main():
    global bot, flyer_service
    # Создаём/пересоздаём объект бота для текущего event loop
    if bot is not None:
        try:
            if hasattr(bot, 'session') and bot.session:
                await bot.session.close()
        except Exception:
            pass
    bot = Bot(token=API_TOKEN)
    # Инициализация базы данных
    await db.initialize()
    
    # Инициализация Flyer Service если включена партнерская система
    if globals().get('USE_FLYER_PARTNER_SYSTEM', False) and globals().get('FLYER_API_KEY'):
        if init_flyer_service:
            flyer_service = init_flyer_service(FLYER_API_KEY, bot)
            logger.info("✅ Flyer Service инициализирован")
            # Регистрируем вебхук для получения обновлений от Flyer
            # await flyer_service.register_webhook()  # Раскомментируйте когда настроите webhook URL
        else:
            logger.warning("⚠️ Не удалось инициализировать Flyer Service - модуль не найден")
    
    # Настраиваем обработку сигналов для корректного завершения
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    polling_task = None
    auto_message_task = None  # Задача для автоматических сообщений
    is_shutting_down = False
    exit_code = 0  # Код завершения по умолчанию
    
    async def shutdown(code=0):
        nonlocal is_shutting_down, exit_code
        exit_code = code  # Сохраняем код завершения
        if is_shutting_down:
            return
        is_shutting_down = True
        logger.info(f"[SHUTDOWN] Завершение работы с кодом {code}...")
        if polling_task and not polling_task.done():
            logger.info(f"[SHUTDOWN] polling_task отменяется: {polling_task}")
            polling_task.cancel()
            try:
                await asyncio.wait_for(polling_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
                
        if auto_message_task and not auto_message_task.done():
            logger.info(f"[SHUTDOWN] auto_message_task отменяется: {auto_message_task}")
            auto_message_task.cancel()
            try:
                await asyncio.wait_for(auto_message_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        if hasattr(db, 'close'):
            try:
                await db.close()
                logger.info("[SHUTDOWN] Соединение с БД закрыто")
            except Exception as e:
                logger.error(f"[SHUTDOWN] Ошибка при закрытии БД: {e}")
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in pending:
            if not task.done():
                logger.warning(f"[SHUTDOWN] Незавершённая задача: {task}")
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        logger.info("[SHUTDOWN] Ресурсы освобождены. До свидания!")
        stop_event.set()
    
    restart_attempts = 0
    MAX_RESTART_ATTEMPTS = 5
    RESTART_DELAY = 5  # seconds
    
    def handle_signal():
        logger.info("Получен сигнал на завершение работы...")
        # Запускаем shutdown в отдельной задаче
        asyncio.create_task(shutdown())
    
    try:
        # Регистрируем обработчики сигналов
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, handle_signal)
            except (NotImplementedError, RuntimeError) as e:
                logger.warning(f"Не удалось зарегистрировать обработчик для {sig}: {e}")
    except Exception as e:
        logger.error(f"Ошибка при настройке обработчиков сигналов: {e}")
    
    async def start_polling():
        nonlocal polling_task, restart_attempts
        try:
            logger.info("[POLLING] Старт polling_task...")
            # Закрываем предыдущую сессию, если она есть
            if hasattr(bot, 'session') and bot.session:
                try:
                    if hasattr(bot.session, '_closed') and not bot.session._closed:
                        await bot.session.close()
                    elif hasattr(bot.session, 'closed') and not bot.session.closed:
                        await bot.session.close()
                except Exception as e:
                    logger.warning(f"Ошибка при закрытии сессии: {e}")
            
            # Пересоздаем сессию
            bot._session = None
            
            # Проверяем соединение перед запуском поллинга
            try:
                await asyncio.wait_for(bot.get_me(), timeout=5.0)
            except Exception as e:
                logger.error(f"[POLLING] Не удалось проверить соединение перед запуском поллинга: {e}")
                # Пробуем пересоздать сессию еще раз
                if hasattr(aiohttp, 'ClientSession'):
                    bot._session = aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=30.0, connect=10.0),
                        connector=aiohttp.TCPConnector(force_close=True, limit=100, ttl_dns_cache=300)
                    )
            
            # Сбрасываем состояние диспетчера
            if hasattr(dp, '_polling'):
                # Важно: _polling - это метод, а не атрибут
                # Нельзя присваивать ему значение
                pass
            
            polling_task = asyncio.create_task(
                dp.start_polling(
                    bot, 
                    allowed_updates=dp.resolve_used_update_types(),
                    skip_updates=True,
                    close_bot_session=True
                )
            )
            restart_attempts = 0  # Сбрасываем счетчик попыток при успешном запуске
            logger.info(f"[POLLING] polling_task создан: {polling_task}")
            return polling_task
        except Exception as e:
            logger.error(f"[POLLING] Ошибка при запуске поллинга: {e}", exc_info=True)
            raise
    
    # Функция для очистки неиспользуемых соединений
    async def cleanup_connections():
        try:
            # Закрываем неиспользуемые соединения в aiohttp
            if hasattr(aiohttp, '_cleanup_closed_transports'):
                aiohttp._cleanup_closed_transports()
            
            # Если доступен psutil, проверяем и закрываем лишние файловые дескрипторы
            if psutil:
                proc = psutil.Process(os.getpid())
                open_files = proc.open_files()
                connections = proc.connections()
                
                # Логируем количество открытых файлов и соединений
                logger.info(f"[CLEANUP] Открытых файлов: {len(open_files)}, соединений: {len(connections)}")
                
                # Если слишком много открытых соединений, принудительно закрываем некоторые
                if len(connections) > 100:
                    logger.warning(f"[CLEANUP] Обнаружено слишком много соединений: {len(connections)}")
                    
                    # Пересоздаем сессию бота
                    if hasattr(bot, 'session') and bot.session:
                        try:
                            await bot.session.close()
                            bot._session = None
                            logger.info("[CLEANUP] Сессия бота закрыта и будет пересоздана")
                        except Exception as e:
                            logger.error(f"[CLEANUP] Ошибка при закрытии сессии бота: {e}")
                    
                    # Принудительный сбор мусора
                    import gc
                    gc.collect()
        except Exception as e:
            logger.error(f"[CLEANUP] Ошибка при очистке соединений: {e}", exc_info=True)
    
    # Улучшенная функция для проверки соединений
    async def check_connections():
        # Проверка соединения с Telegram API
        try:
            await asyncio.wait_for(bot.get_me(), timeout=5.0)
            logger.info("[CHECK] Соединение с Telegram API: OK")
            return True
        except Exception as e:
            logger.error(f"[CHECK] Ошибка соединения с Telegram API: {e}")
            
            # Пробуем пересоздать сессию
            try:
                if hasattr(bot, 'session') and bot.session:
                    try:
                        await bot.session.close()
                    except Exception as close_e:
                        logger.warning(f"[CHECK] Ошибка при закрытии сессии: {close_e}")
                
                # Полностью сбрасываем сессию
                bot._session = None
                
                # Создаем новую сессию с улучшенными параметрами
                if hasattr(aiohttp, 'ClientSession'):
                    try:
                        bot._session = aiohttp.ClientSession(
                            timeout=aiohttp.ClientTimeout(total=30.0, connect=10.0),
                            connector=aiohttp.TCPConnector(
                                force_close=True, 
                                limit=100, 
                                ttl_dns_cache=300,
                                enable_cleanup_closed=True
                            )
                        )
                    except Exception as session_e:
                        logger.error(f"[CHECK] Ошибка при создании новой сессии: {session_e}")
                
                # Проверяем снова
                await asyncio.sleep(1)
                await asyncio.wait_for(bot.get_me(), timeout=5.0)
                logger.info("[CHECK] Соединение восстановлено после пересоздания сессии")
                return True
            except Exception as retry_e:
                logger.error(f"[CHECK] Не удалось восстановить соединение: {retry_e}")
                return False
    
    async def watchdog():
        nonlocal polling_task
        consecutive_failures = 0
        cleanup_counter = 0
        while not stop_event.is_set():
            await asyncio.sleep(30)
            try:
                # Периодическая очистка соединений (каждые 5 минут)
                cleanup_counter += 1
                if cleanup_counter >= 10:  # 30 сек * 10 = 5 минут
                    await cleanup_connections()
                    cleanup_counter = 0
                
                # Проверка времени последнего обновления
                async with last_update_lock:
                    since = time.time() - last_update_time

                # Если бот просто простаивает без входящих сообщений, это нормально.
                # Будем считать это проблемой только если давно не было апдейтов
                # И одновременно фиксируются ошибки соединения.
                connection_ok = False
                try:
                    # Проверяем соединение с Telegram API быстрым запросом
                    await asyncio.wait_for(bot.get_me(), timeout=5.0)
                    connection_ok = True
                    consecutive_failures = 0
                except Exception as e:
                    logger.error(f"[WATCHDOG] Ошибка соединения с Telegram API: {e}")
                    consecutive_failures += 1
                
                                    # Перезапуск при долгом отсутствии обновлений или проблемах с соединением
                # Перезапускаем polling только если и сообщений давно не было (10 минут),
                # и есть подтверждённые проблемы соединения.
                if (since > 600 and consecutive_failures >= 3) or consecutive_failures >= 5:
                    logger.error(f"[WATCHDOG] Обнаружена проблема: нет апдейтов {since:.0f} секунд, ошибки соединения: {consecutive_failures}, перезапуск polling...")
                    if polling_task and not polling_task.done():
                        logger.info(f"[WATCHDOG] polling_task отменяется: {polling_task}")
                        try:
                            polling_task.cancel()
                            await asyncio.wait_for(polling_task, timeout=5.0)
                        except Exception as e:
                            logger.error(f"[WATCHDOG] ошибка при отмене polling: {e}")
                            # Если не удалось отменить задачу, инициируем полный перезапуск
                            if consecutive_failures >= 2:
                                logger.critical("[WATCHDOG] Не удалось корректно отменить polling, инициирую полный перезапуск")
                                await shutdown(42)  # Специальный код для перезапуска
                                return
                    
                    # Перезапуск polling
                    try:
                        # Закрываем и пересоздаем сессию бота для гарантии чистого соединения
                        if hasattr(bot, 'session') and bot.session:
                            try:
                                await bot.session.close()
                                logger.info("[WATCHDOG] Старая сессия бота закрыта")
                            except Exception as se:
                                logger.warning(f"[WATCHDOG] Ошибка при закрытии сессии: {se}")
                        
                        # Создаем новую сессию
                        bot._session = None  # Сбрасываем сессию, чтобы она пересоздалась
                        
                        new_task = await start_polling()
                        logger.info("[WATCHDOG] polling перезапущен")
                        polling_task = new_task
                        await update_last_update_time()
                        consecutive_failures = 0
                    except Exception as e:
                        logger.error(f"[WATCHDOG] не удалось перезапустить polling: {e}")
                        consecutive_failures += 1
                        
                        # Если много неудачных попыток перезапуска, пробуем полный перезапуск
                        if consecutive_failures >= 5:
                            logger.critical("[WATCHDOG] Критическое число ошибок, инициирую полный перезапуск бота")
                            # Завершаем текущий процесс с кодом, который будет перехвачен в run_bot()
                            await shutdown(42)  # Специальный код для перезапуска
            except Exception as e:
                logger.error(f"[WATCHDOG] Ошибка в watchdog: {e}", exc_info=True)
                consecutive_failures += 1
    
    try:
        logger.info("Проверка соединения с Telegram API...")
        me = await bot.get_me()
        logger.info(f"Бот авторизован как @{me.username} (ID: {me.id})")
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Бот запущен и готов к работе!")
        # Запускаем начальный поллинг
        try:
            polling_task = await start_polling()
        except Exception as e:
            logger.error("Не удалось запустить поллинг:", exc_info=e)
            raise
        # Регистрируем административные команды
        if setup_admin_commands:
            setup_admin_commands(dp, db, bot)
            logger.info("Административные команды зарегистрированы")
        
        # Запускаем диагностику и фоновые задачи
        asyncio.create_task(log_diagnostics(polling_task, stop_event))
        asyncio.create_task(watchdog())
        auto_message_task = asyncio.create_task(send_auto_messages())
        # Основной цикл
        connection_failures = 0
        while not stop_event.is_set():
            try:
                # Проверяем состояние поллинга
                if polling_task.done():
                    if polling_task.exception():
                        logger.error(f"[POLLING] polling_task завершился с ошибкой: {polling_task.exception()}")
                        restart_attempts += 1
                        if restart_attempts > MAX_RESTART_ATTEMPTS:
                            logger.error(f"Достигнуто максимальное количество попыток перезапуска ({MAX_RESTART_ATTEMPTS}). Завершение работы.")
                            await shutdown(1)  # Код ошибки 1
                            break
                        wait_time = RESTART_DELAY * (2 ** (restart_attempts - 1))  # Экспоненциальная задержка
                        logger.error(f"Поллинг завершился с ошибкой. Попытка перезапуска {restart_attempts}/{MAX_RESTART_ATTEMPTS} через {wait_time} сек...", 
                                   exc_info=polling_task.exception())
                        await asyncio.sleep(wait_time)
                        polling_task = await start_polling()
                        continue
                
                # Периодическая проверка соединений (каждые 5 минут)
                if time.time() % 300 < 1:  # Примерно раз в 5 минут
                    if not await check_connections():
                        connection_failures += 1
                        if connection_failures >= 3:
                            logger.critical("[MAIN] Критическое число ошибок соединения, инициирую перезапуск")
                            await shutdown(42)  # Специальный код для перезапуска
                            break
                    else:
                        connection_failures = 0
                    
                    # Периодическая очистка соединений и ресурсов
                    await cleanup_connections()
                
                # Проверяем состояние каждую секунду
                await asyncio.wait_for(stop_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                 logger.info("Получен запрос на отмену задачи")
                 break
            except Exception as e:
                logger.error(f"[MAIN] Ошибка в основном цикле: {e}", exc_info=True)
                # Увеличиваем счетчик ошибок и пытаемся восстановиться
                connection_failures += 1
                if connection_failures >= 5:
                    logger.critical("[MAIN] Критическое число ошибок в основном цикле, инициирую перезапуск")
                    await shutdown(42)  # Специальный код для перезапуска
                    break
                await asyncio.sleep(5)
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}", exc_info=True)
        exit_code = 1  # Код ошибки
    finally:
        if not stop_event.is_set():
            await shutdown(exit_code)
        logger.info(f"Бот успешно остановлен с кодом {exit_code}")
        return exit_code

def run_bot():
    """Запускает main() в постоянном цикле, используя ОДИН event loop.
    Это устраняет ошибку RuntimeError: <asyncio.Event> is bound to a different event loop,
    которая возникала из-за создания нового цикла при каждом перезапуске.
    """
    import time

    restart_delay = 5
    max_restart_delay = 300  # 5 минут максимальная задержка
    restart_count = 0

    # Создаём единый event loop и переиспользуем его для всех перезапусков
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    while True:
        try:
            logger.info(f"[RESTARTER] Запуск main()... (попытка #{restart_count + 1})")
            exit_code = loop.run_until_complete(main())

            # Проверяем код завершения
            if exit_code == 0:
                # Нормальное завершение — перезапускаем для надёжности
                logger.warning("[RESTARTER] Бот завершил работу с кодом 0, перезапускаем.")
                restart_delay = 10
            elif exit_code == 42:
                # Специальный код явного перезапуска
                logger.warning("[RESTARTER] Получен код 42, перезапускаем бота…")
                restart_delay = 5
            else:
                logger.error(
                    f"[RESTARTER] Бот завершил работу с кодом {exit_code}, повторный запуск через {restart_delay} секунд")

        except KeyboardInterrupt:
            logger.info("[RESTARTER] KeyboardInterrupt — завершение работы.")
            break
        except BaseException as e:
            restart_count += 1
            logger.error(f"[RESTARTER] main() завершился с ошибкой: {e}", exc_info=True)

            # Увеличиваем задержку перезапуска экспоненциально, но не выше максимальной
            restart_delay = min(restart_delay * 2, max_restart_delay)
            logger.info(
                f"[RESTARTER] Перезапуск через {restart_delay} секунд… (попытка #{restart_count + 1})")

        # Ждём перед перезапуском
        time.sleep(restart_delay)

if __name__ == "__main__":
    run_bot()