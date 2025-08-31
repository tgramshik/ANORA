"""
Административные команды для бота Anora
Вынесены в отдельный файл для упрощения основного bot.py
"""

import logging
from datetime import datetime, timedelta
from typing import Optional
import io
import json

from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile

logger = logging.getLogger(__name__)

# Создаем роутер для административных команд
admin_router = Router()

# ID администраторов (добавьте свои)
ADMIN_IDS = [556828139]

def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь администратором"""
    return user_id in ADMIN_IDS

@admin_router.message(Command("users"))
async def export_users_command(message: Message):
    """
    Экспортирует список ID всех пользователей в текстовый файл.
    Используется для интеграции с внешними сервисами аналитики.
    """
    # Проверяем права доступа
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этой команде")
        return
    
    try:
        await message.answer("📊 Подготавливаю файл с ID пользователей...")
        
        # Получаем список всех пользователей
        async with admin_router.db.acquire() as conn:
            cursor = await conn.execute("""
                SELECT id, username, join_date, last_active, source
                FROM users
                ORDER BY id
            """)
            
            users_data = []
            async for row in cursor:
                users_data.append({
                    'id': row['id'],
                    'username': row['username'] or 'no_username',
                    'join_date': row['join_date'],
                    'last_active': row['last_active'],
                    'source': row['source'] or 'direct'
                })
        
        # Создаем два файла: простой список ID и JSON с полными данными
        
        # Файл 1: Простой список ID (для массовых операций)
        ids_content = '\n'.join(str(user['id']) for user in users_data)
        ids_file = BufferedInputFile(
            ids_content.encode('utf-8'),
            filename=f"user_ids_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        
        # Файл 2: JSON с полными данными (для аналитики)
        json_content = json.dumps(users_data, ensure_ascii=False, indent=2)
        json_file = BufferedInputFile(
            json_content.encode('utf-8'),
            filename=f"users_full_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        
        # Отправляем файлы
        await message.answer_document(
            ids_file,
            caption=f"📋 Список ID пользователей\n"
                   f"Всего пользователей: {len(users_data)}"
        )
        
        await message.answer_document(
            json_file,
            caption="📊 Полные данные пользователей (JSON)"
        )
        
        logger.info(f"[ADMIN] User {message.from_user.id} exported {len(users_data)} user IDs")
        
    except Exception as e:
        logger.error(f"[ADMIN] Error exporting users: {e}")
        await message.answer(f"❌ Ошибка при экспорте: {str(e)}")

@admin_router.message(Command("gift"))
async def gift_subscription_command(message: Message):
    """
    Выдает премиум подписку пользователю.
    Формат: /gift @username дней
    Пример: /gift @user123 30
    """
    # Проверяем права доступа
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этой команде")
        return
    
    try:
        # Парсим аргументы команды
        args = message.text.split()
        if len(args) != 3:
            await message.answer(
                "❌ Неверный формат команды\n\n"
                "Используйте: `/gift @username дней`\n"
                "Пример: `/gift @user123 30`",
                parse_mode="Markdown"
            )
            return
        
        username = args[1].lstrip('@')  # Убираем @ если есть
        try:
            days = int(args[2])
            if days <= 0:
                raise ValueError("Количество дней должно быть положительным")
        except ValueError as e:
            await message.answer(f"❌ Ошибка: {str(e)}")
            return
        
        # Ищем пользователя в базе данных
        async with admin_router.db.acquire() as conn:
            cursor = await conn.execute(
                "SELECT id, name FROM users WHERE username = ?",
                (username,)
            )
            user_row = await cursor.fetchone()
            
            if not user_row:
                await message.answer(
                    f"❌ Пользователь @{username} не найден в базе данных\n\n"
                    "Возможно, пользователь еще не начинал диалог с ботом"
                )
                return
            
            user_id = user_row['id']
            user_name = user_row['name'] or username
            
            # Проверяем текущую подписку
            cursor = await conn.execute(
                "SELECT expires_at FROM subscriptions WHERE user_id = ?",
                (user_id,)
            )
            sub_row = await cursor.fetchone()
            
            # Вычисляем новую дату окончания
            if sub_row and sub_row['expires_at']:
                try:
                    current_expires = datetime.fromisoformat(sub_row['expires_at'])
                    if current_expires > datetime.now():
                        # Продлеваем существующую подписку
                        new_expires = current_expires + timedelta(days=days)
                        action = "продлена"
                    else:
                        # Старая подписка истекла, выдаем новую
                        new_expires = datetime.now() + timedelta(days=days)
                        action = "выдана"
                except:
                    new_expires = datetime.now() + timedelta(days=days)
                    action = "выдана"
            else:
                # У пользователя нет подписки
                new_expires = datetime.now() + timedelta(days=days)
                action = "выдана"
            
            # Сохраняем подписку
            await conn.execute(
                "INSERT OR REPLACE INTO subscriptions (user_id, expires_at) VALUES (?, ?)",
                (user_id, new_expires.isoformat())
            )
        
        # Отправляем уведомление админу
        await message.answer(
            f"✅ **Подписка успешно {action}!**\n\n"
            f"👤 Пользователь: {user_name} (@{username})\n"
            f"🆔 ID: `{user_id}`\n"
            f"📅 Срок: {days} дней\n"
            f"⏰ Действует до: {new_expires.strftime('%d.%m.%Y %H:%M')}\n",
            parse_mode="Markdown"
        )
        
        # Пробуем отправить уведомление пользователю
        try:
            await admin_router.bot.send_message(
                user_id,
                f"🎁 **Поздравляем!**\n\n"
                f"Вам была подарена премиум подписка на {days} дней!\n\n"
                f"✨ Теперь вам доступны:\n"
                f"• Безлимитные сообщения\n"
                f"• Все премиум модели\n"
                f"• Генерация изображений (150/месяц)\n\n"
                f"Подписка действует до: {new_expires.strftime('%d.%m.%Y')}\n\n"
                f"Приятного общения с Анорой! 💋",
                parse_mode="Markdown"
            )
            await message.answer("📨 Пользователь уведомлен о подарке")
        except Exception as e:
            logger.warning(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
            await message.answer("⚠️ Не удалось отправить уведомление пользователю (возможно, заблокировал бота)")
        
        logger.info(
            f"[ADMIN] User {message.from_user.id} gifted {days} days subscription "
            f"to @{username} (ID: {user_id})"
        )
        
    except Exception as e:
        logger.error(f"[ADMIN] Error gifting subscription: {e}")
        await message.answer(f"❌ Ошибка при выдаче подписки: {str(e)}")

@admin_router.message(Command("db_clean"))
async def clean_database_command(message: Message):
    """
    Очищает контекст всех пользователей в БД для снижения нагрузки.
    Сохраняет пользователей и их настройки, но удаляет историю диалогов.
    """
    # Проверяем права доступа
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этой команде")
        return
    
    try:
        await message.answer("🧹 Начинаю очистку базы данных...")
        
        async with admin_router.db.acquire() as conn:
            # Подсчитываем сколько контекста будет очищено
            cursor = await conn.execute("""
                SELECT COUNT(*) as count
                FROM users
                WHERE context IS NOT NULL AND context != '[]'
            """)
            row = await cursor.fetchone()
            contexts_to_clean = row['count'] if row else 0
            
            # Подсчитываем количество сообщений для удаления (старше 7 дней)
            cursor = await conn.execute("""
                SELECT COUNT(*) as count
                FROM messages
                WHERE datetime(ts) < datetime('now', '-7 days')
            """)
            row = await cursor.fetchone()
            messages_to_delete = row['count'] if row else 0
            
            # Очищаем контексты всех пользователей
            await conn.execute("""
                UPDATE users
                SET context = '[]'
                WHERE context IS NOT NULL AND context != '[]'
            """)
            
            # Удаляем старые сообщения (старше 7 дней)
            await conn.execute("""
                DELETE FROM messages
                WHERE datetime(ts) < datetime('now', '-7 days')
            """)
            
            # Очищаем старые записи daily_messages (старше 30 дней)
            await conn.execute("""
                DELETE FROM daily_messages
                WHERE date < date('now', '-30 days')
            """)
            
            # Очищаем старые записи monthly_images (старше 3 месяцев)
            await conn.execute("""
                DELETE FROM monthly_images
                WHERE datetime(month || '-01') < datetime('now', '-3 months')
            """)
            
            # Оптимизируем базу данных
            await conn.execute("VACUUM")
            await conn.execute("ANALYZE")
            
            # Получаем размер БД после очистки
            cursor = await conn.execute("SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()")
            row = await cursor.fetchone()
            db_size_bytes = row['size'] if row else 0
            db_size_mb = db_size_bytes / (1024 * 1024)
        
        # Отправляем отчет
        report = (
            "✅ **Очистка базы данных завершена!**\n\n"
            f"🔹 Очищено контекстов: {contexts_to_clean}\n"
            f"🔹 Удалено старых сообщений: {messages_to_delete}\n"
            f"🔹 Размер БД после очистки: {db_size_mb:.2f} MB\n\n"
            "📝 База данных оптимизирована (VACUUM + ANALYZE)"
        )
        
        await message.answer(report, parse_mode="Markdown")
        
        logger.info(
            f"[ADMIN] User {message.from_user.id} cleaned DB: "
            f"{contexts_to_clean} contexts, {messages_to_delete} messages"
        )
        
    except Exception as e:
        logger.error(f"[ADMIN] Error cleaning database: {e}")
        await message.answer(f"❌ Ошибка при очистке БД: {str(e)}")

def setup_admin_commands(dp, database, bot_instance):
    """
    Регистрирует административные команды в диспетчере.
    Вызывается из основного bot.py при инициализации.
    """
    # Сохраняем ссылки на database и bot для использования в хендлерах
    admin_router.db = database
    admin_router.bot = bot_instance
    
    # Регистрируем роутер
    dp.include_router(admin_router)
    logger.info("[ADMIN] Administrative commands registered")