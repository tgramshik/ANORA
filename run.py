#!/usr/bin/env python3
"""
Точка входа для запуска Anora Bot
"""

import sys
import os
import logging

# Добавляем текущую директорию в путь Python
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def main():
    """Основная функция запуска бота"""
    try:
        from bot import run_bot
        run_bot()
    except KeyboardInterrupt:
        logging.info("Получен сигнал прерывания, завершение работы...")
        sys.exit(0)
    except Exception as e:
        logging.error(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()