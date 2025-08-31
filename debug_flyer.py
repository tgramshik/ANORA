#!/usr/bin/env python3
"""
Диагностика проблемы с двойными сообщениями
"""
import asyncio
import logging
from aiogram import Bot
from config import API_TOKEN, FLYER_API_KEY
from flyer_service import init_flyer_service

# Включаем подробное логирование
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test():
    print("🔍 Диагностика Flyer Service\n")
    print("=" * 50)
    
    # Создаем бота
    bot = Bot(token=API_TOKEN)
    
    # Инициализируем FlyerService
    flyer = init_flyer_service(FLYER_API_KEY, bot)
    
    # Тестируем с вашим ID
    test_user_id = 556828139
    
    print(f"\n📝 Тестирование для вашего user_id: {test_user_id}")
    print("-" * 30)
    
    # 1. Проверяем доступ
    print("\n1️⃣ Проверка доступа через check()...")
    try:
        has_access = await flyer.check_user_access(test_user_id)
        print(f"   Результат check_user_access: {has_access}")
        print(f"   Тип результата: {type(has_access)}")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
    
    # 2. Проверяем задания
    print("\n2️⃣ Получение заданий через get_tasks()...")
    try:
        tasks = await flyer.get_user_tasks(test_user_id)
        print(f"   Количество заданий: {len(tasks)}")
        print(f"   Задания: {tasks}")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
    
    # 3. Проверяем прямой вызов API
    print("\n3️⃣ Прямой вызов Flyer API...")
    try:
        from flyerapi import Flyer
        direct_flyer = Flyer(FLYER_API_KEY)
        
        # Проверяем check
        print("   Вызов check()...")
        result = await direct_flyer.check(test_user_id, language_code="ru")
        print(f"   Результат: {result}")
        
        # Проверяем get_tasks (может вызвать ошибку)
        print("   Вызов get_tasks()...")
        try:
            tasks = await direct_flyer.get_tasks(test_user_id, language_code="ru", limit=5)
            print(f"   Задания: {tasks}")
        except Exception as e:
            print(f"   Ошибка get_tasks (ожидаемо): {e}")
            
    except Exception as e:
        print(f"   ❌ Ошибка прямого вызова: {e}")
    
    # Закрываем сессию
    await bot.session.close()
    
    print("\n" + "=" * 50)
    print("✅ Диагностика завершена")

if __name__ == "__main__":
    asyncio.run(test())