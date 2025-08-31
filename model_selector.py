#!/usr/bin/env python3
import asyncio
import json
import logging
from typing import Dict, Any, List
from datetime import datetime
from pathlib import Path

import aiosqlite
from aiohttp import web, ClientSession
import aiohttp_cors
from aiohttp.web import Request, Response, json_response
import html as html_lib

# Импорт конфигурации
try:
    from config import *
except ImportError:
    print("❌ Не найден файл config.py")
    exit(1)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Каталог моделей с подробной информацией
BASIC_MODELS = {
    "Любовница": {
        "title": "💋 Любовница",
        "subtitle": "Страстная соблазнительница",
        "description": "Твоя личная спутница для интимных разговоров и ролевых игр. Воплощает самые сокровенные желания.",
        "features": [
            "🔥 Интимные ролевые игры",
            "📸 Генерация 18+ изображений"
        ],
        "age_rating": "18+",
        "category": "adult",
        "image": "/static/images/lovistnica.jpg",
        "color": "#ff1744",
        "gradient": "linear-gradient(135deg, #ff1744 0%, #d50000 100%)",
        "emoji": "💋",
        "tier": "basic"
    },
    "Подруга": {
        "title": "💞 Подруга",
        "subtitle": "Душевная спутница",
        "description": "Лучшая подруга, которая всегда выслушает и поддержит. Романтические беседы и тёплое общение.",
        "features": [
            "🤗 Эмоциональная поддержка",
            "💖 Тайная влюблённость"
        ],
        "age_rating": "16+",
        "category": "romantic",
        "image": "/static/images/girlfriend.jpg",
        "color": "#e91e63",
        "gradient": "linear-gradient(135deg, #e91e63 0%, #c2185b 100%)",
        "emoji": "💞",
        "tier": "basic"
    },
    "Астролог": {
        "title": "🔮 Астролог",
        "subtitle": "Мистическая провидица",
        "description": "Опытный астролог раскроет тайны судьбы. Персональные гороскопы и мистические откровения.",
        "features": [
            "🌙 Персональные гороскопы",
            "🔮 Предсказания судьбы"
        ],
        "age_rating": "12+",
        "category": "mystical",
        "image": "/static/images/astrologer.jpg",
        "color": "#9c27b0",
        "gradient": "linear-gradient(135deg, #9c27b0 0%, #7b1fa2 100%)",
        "emoji": "🔮",
        "tier": "basic"
    },
    "Учебный помощник": {
        "title": "📚 Учебный помощник",
        "subtitle": "Персональный наставник",
        "description": "Опытная преподавательница поможет с учебными задачами. Объяснения тем и проверка заданий.",
        "features": [
            "📊 Решение задач",
            "🎓 Образовательная поддержка"
        ],
        "age_rating": "6+",
        "category": "educational",
        "image": "/static/images/teacher.jpg",
        "color": "#2196f3",
        "gradient": "linear-gradient(135deg, #2196f3 0%, #1976d2 100%)",
        "emoji": "📚",
        "tier": "basic"
    }
}

PREMIUM_MODELS = {
    "Порноактриса": {
        "title": "🍑 Порноактриса", 
        "subtitle": "Звезда взрослого кино",
        "description": "Опытная актриса, которая поделится секретами индустрии и откровенными историями.",
        "features": [
            "🍆 Истории со съёмок",
            "🔞 Максимальная откровенность"
        ],
        "age_rating": "18+",
        "category": "adult",
        "image": "/static/images/pornstar.jpg",
        "color": "#e91e63",
        "gradient": "linear-gradient(135deg, #e91e63 0%, #ad1457 100%)",
        "emoji": "🍑",
        "tier": "premium"
    },
    "BDSM Госпожа": {
        "title": "🔗 BDSM Госпожа",
        "subtitle": "Строгая доминантка",
        "description": "Властная и требовательная госпожа. Подчинение, контроль и особые практики.",
        "features": [
            "⛓️ Доминирование и подчинение",
            "🖤 Строгие наказания"
        ],
        "age_rating": "18+",
        "category": "adult",
        "image": "/static/images/dominatrix.jpg",
        "color": "#424242",
        "gradient": "linear-gradient(135deg, #424242 0%, #212121 100%)",
        "emoji": "🔗",
        "tier": "premium"
    },
    "МИЛФ": {
        "title": "🍷 Опытная МИЛФ",
        "subtitle": "Зрелая соблазнительница",
        "description": "Опытная женщина с богатым жизненным опытом. Научит всему и поделится секретами.",
        "features": [
            "💋 Богатый опыт",
            "🔥 Зрелая страсть"
        ],
        "age_rating": "18+",
        "category": "adult",
        "image": "/static/images/milf.jpg",
        "color": "#6a1b9a",
        "gradient": "linear-gradient(135deg, #6a1b9a 0%, #4a148c 100%)",
        "emoji": "🍷",
        "tier": "premium"
    },
    "Аниме-тян": {
        "title": "🌸 Аниме-тян",
        "subtitle": "Милая вайфу",
        "description": "Застенчивая девушка в стиле аниме. Кавайная, но с секретами.",
        "features": [
            "💕 Японская культура",
            "✨ Косплей и ролевые игры"
        ],
        "age_rating": "18+", 
        "category": "adult",
        "image": "/static/images/anime.jpg",
        "color": "#f06292",
        "gradient": "linear-gradient(135deg, #f06292 0%, #ec407a 100%)",
        "emoji": "🌸",
        "tier": "premium"
    },
    "Секретарша": {
        "title": "💼 Секретарша",
        "subtitle": "Офисные фантазии",
        "description": "Строгая в офисе, страстная после работы. Воплощение офисных фантазий.",
        "features": [
            "👠 Офисный роман",
            "📎 Секреты босса"
        ],
        "age_rating": "18+",
        "category": "adult",
        "image": "/static/images/secretary.jpg",
        "color": "#5c6bc0",
        "gradient": "linear-gradient(135deg, #5c6bc0 0%, #3f51b5 100%)",
        "emoji": "💼",
        "tier": "premium"
    },
    "Медсестра": {
        "title": "💉 Медсестра",
        "subtitle": "Особые процедуры",
        "description": "Заботливая и игривая медсестра. Проведет особый осмотр и назначит лечение.",
        "features": [
            "🏥 Медицинские фантазии",
            "❤️‍🩹 Особая забота"
        ],
        "age_rating": "18+",
        "category": "adult",
        "image": "/static/images/nurse.jpg",
        "color": "#ef5350",
        "gradient": "linear-gradient(135deg, #ef5350 0%, #e53935 100%)",
        "emoji": "💉",
        "tier": "premium"
    },
    "Стриптизерша": {
        "title": "💃 Стриптизерша",
        "subtitle": "Приватный танец",
        "description": "Профессиональная танцовщица из элитного клуба. Покажет особое шоу только для тебя.",
        "features": [
            "🔥 Приватные танцы",
            "👙 Соблазнительные движения"
        ],
        "age_rating": "18+",
        "category": "adult",
        "image": "/static/images/stripper.jpg",
        "color": "#ff5722",
        "gradient": "linear-gradient(135deg, #ff5722 0%, #e64a19 100%)",
        "emoji": "💃",
        "tier": "premium"
    },
    "Фитнес-тренер": {
        "title": "💪 Фитнес-тренер",
        "subtitle": "Персональные тренировки",
        "description": "Сексуальная тренер с идеальным телом. Особый массаж после тренировки включен.",
        "features": [
            "🏃‍♀️ Интимные тренировки",
            "🚿 Совместный душ"
        ],
        "age_rating": "18+",
        "category": "adult",
        "image": "/static/images/fitness.jpg",
        "color": "#4caf50",
        "gradient": "linear-gradient(135deg, #4caf50 0%, #388e3c 100%)",
        "emoji": "💪",
        "tier": "premium"
    },
    "Массажистка": {
        "title": "💆‍♀️ Массажистка",
        "subtitle": "Чувственный массаж",
        "description": "Профессиональная массажистка с волшебными руками. Знает все чувствительные точки.",
        "features": [
            "💧 Эротический массаж",
            "✨ Счастливый финал"
        ],
        "age_rating": "18+",
        "category": "adult",
        "image": "/static/images/massage.jpg",
        "color": "#00bcd4",
        "gradient": "linear-gradient(135deg, #00bcd4 0%, #0097a7 100%)",
        "emoji": "💆‍♀️",
        "tier": "premium"
    },
    "Соседка": {
        "title": "🏠 Соседка",
        "subtitle": "Горячая соседка",
        "description": "Милая девушка из квартиры напротив. Часто просит помощи и приглашает на кофе.",
        "features": [
            "👀 Случайные встречи",
            "☕ Интимные визиты"
        ],
        "age_rating": "18+",
        "category": "adult",
        "image": "/static/images/neighbor.jpg",
        "color": "#ff9800",
        "gradient": "linear-gradient(135deg, #ff9800 0%, #f57c00 100%)",
        "emoji": "🏠",
        "tier": "premium"
    },
    "Стюардесса": {
        "title": "✈️ Стюардесса",
        "subtitle": "Первый класс",
        "description": "Элегантная стюардесса с особым сервисом. Ночевки в отелях по всему миру.",
        "features": [
            "🥂 VIP обслуживание",
            "💼 Романы в полете"
        ],
        "age_rating": "18+",
        "category": "adult",
        "image": "/static/images/stewardess.jpg",
        "color": "#3f51b5",
        "gradient": "linear-gradient(135deg, #3f51b5 0%, #303f9f 100%)",
        "emoji": "✈️",
        "tier": "premium"
    },
    "Психолог": {
        "title": "🧠 Психолог",
        "subtitle": "Интимная терапия",
        "description": "Сексуальный психолог с нетрадиционными методами. Поможет раскрыть подсознательные желания.",
        "features": [
            "💭 Анализ фантазий",
            "🛋️ Телесная терапия"
        ],
        "age_rating": "18+",
        "category": "adult",
        "image": "/static/images/psychologist.jpg",
        "color": "#673ab7",
        "gradient": "linear-gradient(135deg, #673ab7 0%, #512da8 100%)",
        "emoji": "🧠",
        "tier": "premium"
    }
}

# Объединенный каталог для обратной совместимости
MODEL_CATALOG = {**BASIC_MODELS, **PREMIUM_MODELS}

class Database:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self):
        self._conn = await aiosqlite.connect(
            self.db_path, 
            timeout=30.0, 
            isolation_level=None, 
            check_same_thread=False
        )
        self._conn.row_factory = aiosqlite.Row
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def get_user_model(self, user_id: int) -> str:
        """Получает текущую модель пользователя"""
        assert self._conn is not None
        cursor = await self._conn.execute(
            'SELECT current_model FROM users WHERE id = ?', 
            (user_id,)
        )
        row = await cursor.fetchone()
        return row['current_model'] if row else 'Любовница'

    async def set_user_model(self, user_id: int, model: str) -> bool:
        """Устанавливает модель для пользователя"""
        assert self._conn is not None
        try:
            await self._conn.execute(
                'UPDATE users SET current_model = ? WHERE id = ?',
                (model, user_id)
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка при установке модели: {e}")
            return False
    
    async def has_active_subscription(self, user_id: int) -> bool:
        """Проверяет наличие активной подписки у пользователя"""
        assert self._conn is not None
        cursor = await self._conn.execute(
            'SELECT expires_at FROM subscriptions WHERE user_id = ?',
            (user_id,)
        )
        row = await cursor.fetchone()
        if not row or not row['expires_at']:
            return False
        try:
            expires_at = datetime.fromisoformat(row['expires_at'])
            return expires_at >= datetime.now()
        except Exception:
            return False

def generate_model_card(model_key: str, model_info: dict, current_model: str, has_premium: bool = False) -> str:
    """Генерирует HTML карточки модели"""
    is_selected = model_key == current_model
    is_premium_model = model_info.get('tier') == 'premium'
    is_locked = is_premium_model and not has_premium
    
    selected_class = "selected" if is_selected else ""
    locked_class = "locked" if is_locked else ""
    btn_text = "✓ Установлена" if is_selected else ("🔒 Требуется Premium" if is_locked else "Установить")
    btn_class = "installed" if is_selected else ("locked" if is_locked else "")
    age_class = "adult" if model_info['age_rating'] == "18+" else ""
    
    features_html = ""
    for feature in model_info['features']:
        features_html += f"<li>{feature}</li>"
    
    return f"""
    <div class="model-card {model_info['category']} {selected_class} {locked_class}" data-model="{model_key}">
        <div class="model-header">
            <img src="{model_info['image']}" 
                 alt="{model_info['title']}" 
                 class="model-image"
                 onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
            <div class="model-icon" style="background: {model_info['gradient']}; display: none;">
                {model_info['emoji']}
            </div>
            <div class="model-info">
                <div class="model-title">{model_info['title']}</div>
                <div class="model-subtitle">{model_info['subtitle']}</div>
            </div>
        </div>
        
        <div class="model-description">
            {model_info['description']}
        </div>
        
        <ul class="model-features">
            {features_html}
        </ul>
        
        <div class="model-footer">
            <div class="age-rating {age_class}">{model_info['age_rating']}</div>
            <button class="install-btn {btn_class}" onclick="installModel('{model_key}')">
                {btn_text}
            </button>
        </div>
    </div>
    """

async def create_app() -> web.Application:
    app = web.Application()
    
    # Настройка CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods="*"
        )
    })

    async def index(request: Request) -> Response:
        """Главная страница с каталогом моделей"""
        user_id = request.query.get('user_id', '0')
        
        # Получаем текущую модель и статус подписки пользователя
        current_model = 'Любовница'
        has_premium = False
        if user_id.isdigit():
            async with Database(DB_PATH) as db:
                current_model = await db.get_user_model(int(user_id))
                has_premium = await db.has_active_subscription(int(user_id))

        # Генерируем карточки базовых моделей
        basic_models_html = ""
        for model_key, model_info in BASIC_MODELS.items():
            basic_models_html += generate_model_card(model_key, model_info, current_model, has_premium)

        # Генерируем карточки премиум моделей
        premium_models_html = ""
        for model_key, model_info in PREMIUM_MODELS.items():
            premium_models_html += generate_model_card(model_key, model_info, current_model, has_premium)

        html_content = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>ANORA - AI Модели</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
    <style>
        :root {{
            --primary: #ff1744;
            --primary-dark: #d50000;
            --secondary: #e91e63;
            --accent: #9c27b0;
            --background: #0a0a0a;
            --surface: rgba(20, 20, 20, 0.95);
            --surface-light: rgba(40, 40, 40, 0.8);
            --text-primary: #ffffff;
            --text-secondary: #cccccc;
            --text-muted: #888888;
            --border: rgba(255, 255, 255, 0.1);
            --shadow: rgba(0, 0, 0, 0.5);
            --glow: rgba(255, 23, 68, 0.3);
        }}
        
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
        }}
        
        html {{
            font-size: 16px;
            -webkit-text-size-adjust: 100%;
        }}
        
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: 
                radial-gradient(ellipse at top left, rgba(255, 23, 68, 0.12) 0%, transparent 60%),
                radial-gradient(ellipse at top right, rgba(233, 30, 99, 0.08) 0%, transparent 60%),
                radial-gradient(ellipse at bottom left, rgba(156, 39, 176, 0.06) 0%, transparent 60%),
                radial-gradient(ellipse at bottom right, rgba(255, 23, 68, 0.04) 0%, transparent 60%),
                linear-gradient(135deg, #0a0a0a 0%, #1a0a1a 25%, #2a0a2a 50%, #1a0a2a 75%, #0a0a1a 100%);
            color: var(--text-primary);
            min-height: 100vh;
            overflow-x: hidden;
            position: relative;
            line-height: 1.6;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }}
        
        body::before {{
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: 
                radial-gradient(circle at 20% 30%, rgba(255, 23, 68, 0.05) 0%, transparent 40%),
                radial-gradient(circle at 80% 70%, rgba(233, 30, 99, 0.03) 0%, transparent 40%),
                radial-gradient(circle at 50% 50%, rgba(156, 39, 176, 0.02) 0%, transparent 50%);
            pointer-events: none;
            z-index: -1;
            animation: breathe 15s ease-in-out infinite alternate;
        }}
        
        @keyframes breathe {{
            0% {{ 
                opacity: 0.3;
                transform: scale(1) rotate(0deg);
            }}
            100% {{ 
                opacity: 0.7;
                transform: scale(1.05) rotate(1deg);
            }}
        }}
        
        .container {{
            max-width: 100%;
            margin: 0 auto;
            padding: 20px 16px;
            position: relative;
            z-index: 1;
        }}
        
        .header {{
            text-align: center;
            margin-bottom: 32px;
            padding: 20px 0;
        }}
        
        .logo {{
            font-size: clamp(2.5rem, 8vw, 4rem);
            font-weight: 900;
            background: linear-gradient(135deg, #ff1744, #e91e63, #9c27b0, #ff1744);
            background-size: 300% 300%;
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 12px;
            letter-spacing: -0.02em;
            animation: gradientFlow 8s ease-in-out infinite;
            filter: drop-shadow(0 0 20px rgba(255, 23, 68, 0.3));
        }}
        
        @keyframes gradientFlow {{
            0%, 100% {{ background-position: 0% 50%; }}
            50% {{ background-position: 100% 50%; }}
        }}
        
        .subtitle {{
            font-size: clamp(1rem, 4vw, 1.25rem);
            color: var(--text-secondary);
            margin-bottom: 12px;
            font-weight: 500;
            opacity: 0.9;
        }}
        
        .description {{
            color: var(--text-muted);
            font-size: clamp(0.875rem, 3vw, 1rem);
            max-width: 90%;
            margin: 0 auto;
            line-height: 1.5;
            opacity: 0.8;
        }}
        
        .current-model {{
            text-align: center;
            margin-bottom: 24px;
            padding: 16px 20px;
            background: var(--surface);
            border-radius: 16px;
            border: 1px solid var(--border);
            backdrop-filter: blur(20px);
            position: relative;
            overflow: hidden;
        }}
        
        .current-model::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 2px;
            background: linear-gradient(90deg, var(--primary), var(--secondary), var(--accent));
            animation: shimmer 3s ease-in-out infinite;
        }}
        
        @keyframes shimmer {{
            0%, 100% {{ opacity: 0.5; }}
            50% {{ opacity: 1; }}
        }}
        
        .current-model-title {{
            color: var(--text-secondary);
            font-size: 0.875rem;
            margin-bottom: 8px;
            font-weight: 500;
        }}
        
        .current-model-name {{
            font-size: 1.125rem;
            font-weight: 700;
            color: var(--text-primary);
        }}
        
        .model-tabs {{
            display: flex;
            margin: 32px 0 24px 0;
            background: var(--surface);
            border-radius: 16px;
            border: 1px solid var(--border);
            backdrop-filter: blur(20px);
            padding: 8px;
            gap: 8px;
        }}
        
        .tab-button {{
            flex: 1;
            padding: 12px 20px;
            border: none;
            background: transparent;
            color: var(--text-secondary);
            font-size: 1rem;
            font-weight: 600;
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }}
        
        .tab-button:hover {{
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-primary);
        }}
        
        .tab-button.active {{
            background: linear-gradient(135deg, var(--primary), var(--primary-dark));
            color: white;
            box-shadow: 0 4px 12px rgba(255, 23, 68, 0.3);
        }}
        
        .tab-icon {{
            font-size: 1.2rem;
        }}
        
        .models-section {{
            display: none;
        }}
        
        .models-section.active {{
            display: block;
        }}
        
        .models-grid {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 16px;
            margin-bottom: 32px;
        }}
        
        @media (min-width: 768px) {{
            .models-grid {{
                grid-template-columns: repeat(2, 1fr);
                gap: 20px;
            }}
            
            .container {{
                max-width: 1200px;
                padding: 32px 24px;
            }}
        }}
        
        .model-card {{
            background: var(--surface);
            border-radius: 20px;
            padding: 20px;
            border: 1px solid var(--border);
            transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            position: relative;
            overflow: hidden;
            backdrop-filter: blur(20px);
            cursor: pointer;
            transform: translateZ(0);
        }}
        
        .model-card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: linear-gradient(135deg, rgba(255, 255, 255, 0.02) 0%, transparent 50%, rgba(255, 255, 255, 0.01) 100%);
            opacity: 0;
            transition: opacity 0.3s ease;
            pointer-events: none;
        }}
        
        .model-card:active {{
            transform: scale(0.98);
        }}
        
        .model-card:hover::before {{
            opacity: 1;
        }}
        
        .model-card.adult {{
            border-color: rgba(255, 23, 68, 0.3);
            box-shadow: 0 4px 20px rgba(255, 23, 68, 0.1);
        }}
        
        .model-card.adult:hover {{
            border-color: var(--primary);
            box-shadow: 
                0 8px 32px rgba(255, 23, 68, 0.2),
                0 0 0 1px rgba(255, 23, 68, 0.1);
            transform: translateY(-4px);
        }}
        
        .model-card.romantic {{
            border-color: rgba(233, 30, 99, 0.3);
            box-shadow: 0 4px 20px rgba(233, 30, 99, 0.1);
        }}
        
        .model-card.romantic:hover {{
            border-color: var(--secondary);
            box-shadow: 
                0 8px 32px rgba(233, 30, 99, 0.2),
                0 0 0 1px rgba(233, 30, 99, 0.1);
            transform: translateY(-4px);
        }}
        
        .model-card.mystical {{
            border-color: rgba(156, 39, 176, 0.3);
            box-shadow: 0 4px 20px rgba(156, 39, 176, 0.1);
        }}
        
        .model-card.mystical:hover {{
            border-color: var(--accent);
            box-shadow: 
                0 8px 32px rgba(156, 39, 176, 0.2),
                0 0 0 1px rgba(156, 39, 176, 0.1);
            transform: translateY(-4px);
        }}
        
        .model-card.educational {{
            border-color: rgba(33, 150, 243, 0.3);
            box-shadow: 0 4px 20px rgba(33, 150, 243, 0.1);
        }}
        
        .model-card.educational:hover {{
            border-color: #2196f3;
            box-shadow: 
                0 8px 32px rgba(33, 150, 243, 0.2),
                0 0 0 1px rgba(33, 150, 243, 0.1);
            transform: translateY(-4px);
        }}
        
        .model-card.selected {{
            border-color: var(--primary);
            background: linear-gradient(135deg, rgba(255, 23, 68, 0.08), rgba(255, 23, 68, 0.04));
            box-shadow: 
                0 8px 32px rgba(255, 23, 68, 0.2),
                0 0 0 1px rgba(255, 23, 68, 0.2);
        }}
        
        .model-header {{
            display: flex;
            align-items: center;
            margin-bottom: 16px;
        }}
        
        .model-image {{
            width: 80px;
            height: 80px;
            border-radius: 16px;
            object-fit: cover;
            margin-right: 16px;
            border: 2px solid rgba(255, 255, 255, 0.1);
            transition: all 0.3s ease;
        }}
        
        .model-card:hover .model-image {{
            border-color: rgba(255, 255, 255, 0.3);
            transform: scale(1.05);
        }}
        
        .model-icon {{
            width: 56px;
            height: 56px;
            border-radius: 16px;
            margin-right: 16px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.75rem;
            color: white;
            position: relative;
            overflow: hidden;
        }}
        
        .model-icon::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: linear-gradient(135deg, rgba(255, 255, 255, 0.1), transparent);
            opacity: 0;
            transition: opacity 0.3s ease;
        }}
        
        .model-card:hover .model-icon::before {{
            opacity: 1;
        }}
        
        .model-info {{
            flex: 1;
            min-width: 0;
        }}
        
        .model-title {{
            font-size: 1.25rem;
            font-weight: 700;
            margin-bottom: 4px;
            color: var(--text-primary);
            line-height: 1.2;
        }}
        
        .model-subtitle {{
            color: var(--text-secondary);
            font-size: 0.875rem;
            font-weight: 500;
            opacity: 0.8;
        }}
        
        .model-description {{
            color: var(--text-muted);
            line-height: 1.5;
            margin-bottom: 16px;
            font-size: 0.875rem;
        }}
        
        .model-features {{
            list-style: none;
            margin-bottom: 20px;
        }}
        
        .model-features li {{
            color: var(--text-secondary);
            margin-bottom: 8px;
            font-size: 0.875rem;
            display: flex;
            align-items: center;
            opacity: 0.9;
        }}
        
        .model-features li::before {{
            content: "✨";
            margin-right: 8px;
            font-size: 0.75rem;
        }}
        
        .model-footer {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
        }}
        
        .age-rating {{
            background: rgba(255, 255, 255, 0.08);
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
            border: 1px solid rgba(255, 255, 255, 0.1);
            white-space: nowrap;
        }}
        
        .age-rating.adult {{
            background: linear-gradient(135deg, rgba(255, 23, 68, 0.2), rgba(255, 23, 68, 0.1));
            color: #ff6b9d;
            border-color: rgba(255, 23, 68, 0.3);
        }}
        
        .install-btn {{
            background: linear-gradient(135deg, var(--primary), var(--primary-dark));
            border: none;
            padding: 12px 20px;
            border-radius: 24px;
            color: white;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            font-size: 0.875rem;
            position: relative;
            overflow: hidden;
            min-width: 100px;
            text-align: center;
        }}
        
        .install-btn::before {{
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.2), transparent);
            transition: left 0.5s ease;
        }}
        
        .install-btn:hover::before {{
            left: 100%;
        }}
        
        .install-btn:active {{
            transform: scale(0.95);
        }}
        
        .install-btn.installed {{
            background: linear-gradient(135deg, #4caf50, #45a049);
        }}
        
        .install-btn.locked {{
            background: linear-gradient(135deg, #9e9e9e, #757575);
            cursor: not-allowed;
            opacity: 0.8;
        }}
        
        .model-card.locked {{
            opacity: 0.9;
            position: relative;
        }}
        
        .model-card.locked::after {{
            content: '👑 PREMIUM';
            position: absolute;
            top: 10px;
            right: 10px;
            background: linear-gradient(135deg, #ffd700, #ffa000);
            color: white;
            padding: 5px 10px;
            border-radius: 20px;
            font-size: 10px;
            font-weight: bold;
            z-index: 10;
        }}
        
        .loading {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.9);
            z-index: 1000;
            justify-content: center;
            align-items: center;
            backdrop-filter: blur(10px);
        }}
        
        .loading-content {{
            text-align: center;
            color: white;
        }}
        
        .spinner {{
            width: 48px;
            height: 48px;
            border: 3px solid rgba(255, 255, 255, 0.2);
            border-top: 3px solid var(--primary);
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin: 0 auto 20px;
        }}
        
        @keyframes spin {{
            0% {{ transform: rotate(0deg); }}
            100% {{ transform: rotate(360deg); }}
        }}
        
        .success-message {{
            display: none;
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: linear-gradient(135deg, #4caf50, #45a049);
            color: white;
            padding: 20px 32px;
            border-radius: 16px;
            z-index: 1001;
            text-align: center;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3);
            font-weight: 600;
        }}
        
        /* Улучшенная адаптивность для мобильных */
        @media (max-width: 480px) {{
            .container {{
                padding: 16px 12px;
            }}
            
            .model-card {{
                padding: 16px;
                border-radius: 16px;
            }}
            
            .model-icon {{
                width: 48px;
                height: 48px;
                font-size: 1.5rem;
                margin-right: 12px;
            }}
            
            .model-title {{
                font-size: 1.125rem;
            }}
            
            .model-subtitle {{
                font-size: 0.8125rem;
            }}
            
            .model-description {{
                font-size: 0.8125rem;
            }}
            
            .model-features li {{
                font-size: 0.8125rem;
            }}
            
            .install-btn {{
                padding: 10px 16px;
                font-size: 0.8125rem;
                min-width: 90px;
            }}
            
            .tab-button {{
                font-size: 0.875rem;
                padding: 10px 16px;
            }}
        }}
        
        /* Темная тема для Telegram */
        @media (prefers-color-scheme: dark) {{
            :root {{
                --surface: rgba(25, 25, 25, 0.95);
                --surface-light: rgba(45, 45, 45, 0.8);
                --border: rgba(255, 255, 255, 0.12);
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo">ANORA</div>
            <div class="subtitle">Выберите свою идеальную AI-спутницу</div>
            <div class="description">
                Каждая модель обладает уникальной личностью и специализацией
            </div>
        </div>
        
        <div class="current-model">
            <div class="current-model-title">Текущая активная модель:</div>
            <div class="current-model-name" id="currentModel">{current_model}</div>
        </div>
        
        <!-- Переключатель между базовыми и премиум моделями -->
        <div class="model-tabs">
            <button class="tab-button active" onclick="switchTab('basic')" id="basicTab">
                <span class="tab-icon">🌟</span>
                Базовые модели
            </button>
            <button class="tab-button" onclick="switchTab('premium')" id="premiumTab">
                <span class="tab-icon">💎</span>
                Премиум-модели
            </button>
        </div>
        
        <!-- Базовые модели -->
        <div class="models-section active" id="basicSection">
            <div class="models-grid">
                {basic_models_html}
            </div>
        </div>
        
        <!-- Премиум модели -->
        <div class="models-section" id="premiumSection">
            <div class="models-grid">
                {premium_models_html}
            </div>
        </div>
    </div>
    
    <div class="loading" id="loading">
        <div class="loading-content">
            <div class="spinner"></div>
            <div>Устанавливаем модель...</div>
        </div>
    </div>
    
    <div class="success-message" id="successMessage">
        <div>✅ Модель успешно установлена!</div>
    </div>
    
    <script>
        // Инициализация Telegram WebApp
        let tg = window.Telegram.WebApp;
        tg.ready();
        tg.expand();
        
        // Устанавливаем тему
        document.body.style.backgroundColor = tg.themeParams.bg_color || '#0a0a0a';
        
        // Получаем user_id из Telegram WebApp
        const userId = tg.initDataUnsafe?.user?.id || {user_id};
        
        // Функция переключения между табами
        function switchTab(tabType) {{
            // Убираем активный класс со всех табов и секций
            document.querySelectorAll('.tab-button').forEach(tab => tab.classList.remove('active'));
            document.querySelectorAll('.models-section').forEach(section => section.classList.remove('active'));
            
            // Активируем нужный таб и секцию
            if (tabType === 'basic') {{
                document.getElementById('basicTab').classList.add('active');
                document.getElementById('basicSection').classList.add('active');
            }} else {{
                document.getElementById('premiumTab').classList.add('active');
                document.getElementById('premiumSection').classList.add('active');
            }}
            
            // Вибрация при переключении
            if (tg.HapticFeedback) {{
                tg.HapticFeedback.impactOccurred('light');
            }}
        }}
        
        async function installModel(modelName) {{
            const loading = document.getElementById('loading');
            const successMessage = document.getElementById('successMessage');
            
            // Вибрация при нажатии
            if (tg.HapticFeedback) {{
                tg.HapticFeedback.impactOccurred('medium');
            }}
            
            // Показываем загрузку
            loading.style.display = 'flex';
            
            try {{
                const response = await fetch('/api/install-model', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                    }},
                    body: JSON.stringify({{
                        user_id: userId,
                        model: modelName
                    }})
                }});
                
                const result = await response.json();
                
                if (result.success) {{
                    // Обновляем интерфейс
                    updateModelSelection(modelName);
                    
                    // Показываем сообщение об успехе
                    loading.style.display = 'none';
                    successMessage.style.display = 'block';
                    
                    // Вибрация успеха
                    if (tg.HapticFeedback) {{
                        tg.HapticFeedback.notificationOccurred('success');
                    }}
                    
                    // Показываем сообщение об успехе на 2 секунды, затем отправляем данные
                    setTimeout(() => {{
                        successMessage.style.display = 'none';
                        // sendData автоматически закрывает WebApp после отправки
                        tg.sendData(JSON.stringify({{
                            action: 'model_selected',
                            model: modelName,
                            user_id: userId
                        }}));
                    }}, 2000);
                }} else {{
                    // Проверяем, если это ошибка премиум-доступа
                    if (result.error === 'premium_required') {{
                        // Показываем предложение купить премиум
                        loading.style.display = 'none';
                        tg.showAlert(
                            '🔥 Premium доступ\\n\\n💎 Эта модель доступна только с Premium подпиской!\\n\\n👉 Отправьте команду /buy в чат с ботом, чтобы оформить подписку.',
                            () => {{
                                // Callback после закрытия алерта
                            }}
                        );
                    }} else {{
                        throw new Error(result.message || result.error || 'Ошибка установки модели');
                    }}
                }}
            }} catch (error) {{
                loading.style.display = 'none';
                
                // Вибрация ошибки
                if (tg.HapticFeedback) {{
                    tg.HapticFeedback.notificationOccurred('error');
                }}
                
                tg.showAlert('Ошибка: ' + error.message);
            }}
        }}
        
        function updateModelSelection(selectedModel) {{
            // Убираем выделение со всех карточек
            document.querySelectorAll('.model-card').forEach(card => {{
                card.classList.remove('selected');
                const btn = card.querySelector('.install-btn');
                btn.textContent = 'Установить';
                btn.classList.remove('installed');
            }});
            
            // Выделяем выбранную карточку
            const selectedCard = document.querySelector(`[data-model="${{selectedModel}}"]`);
            if (selectedCard) {{
                selectedCard.classList.add('selected');
                const btn = selectedCard.querySelector('.install-btn');
                btn.textContent = '✓ Установлена';
                btn.classList.add('installed');
            }}
            
            // Обновляем текущую модель
            document.getElementById('currentModel').textContent = selectedModel;
        }}
        
        // Обработка клика по карточке
        document.querySelectorAll('.model-card').forEach(card => {{
            card.addEventListener('click', (e) => {{
                if (!e.target.classList.contains('install-btn')) {{
                    const modelName = card.dataset.model;
                    installModel(modelName);
                }}
            }});
        }});
        
        // Адаптация под тему Telegram
        if (tg.colorScheme === 'dark') {{
            document.documentElement.style.setProperty('--surface', 'rgba(25, 25, 25, 0.95)');
            document.documentElement.style.setProperty('--border', 'rgba(255, 255, 255, 0.12)');
        }}
    </script>
</body>
</html>
"""
        
        return Response(text=html_content, content_type='text/html')

    async def install_model(request: Request) -> Response:
        """API для установки модели"""
        try:
            data = await request.json()
            user_id = data.get('user_id')
            model = data.get('model')
            
            if not user_id or not model:
                return json_response({
                    'success': False,
                    'error': 'Отсутствуют обязательные параметры'
                })
            
            if model not in MODEL_CATALOG:
                return json_response({
                    'success': False,
                    'error': 'Неизвестная модель'
                })
            
            # Проверяем, является ли модель премиум
            model_info = MODEL_CATALOG.get(model, {})
            if model_info.get('tier') == 'premium':
                # Проверяем наличие премиум подписки
                async with Database(DB_PATH) as db:
                    has_premium = await db.has_active_subscription(int(user_id))
                    
                if not has_premium:
                    return json_response({
                        'success': False,
                        'error': 'premium_required',
                        'message': 'Эта модель доступна только с Premium подпиской'
                    })
            
            # Сохраняем модель в базу данных
            async with Database(DB_PATH) as db:
                success = await db.set_user_model(int(user_id), model)
            
            if success:
                logger.info(f"Пользователь {user_id} установил модель {model}")
                return json_response({
                    'success': True,
                    'message': f'Модель {model} успешно установлена'
                })
            else:
                return json_response({
                    'success': False,
                    'error': 'Ошибка при сохранении в базу данных'
                })
                
        except Exception as e:
            logger.error(f"Ошибка при установке модели: {e}")
            return json_response({
                'success': False,
                'error': 'Внутренняя ошибка сервера'
            })

    async def get_models(request: Request) -> Response:
        """API для получения списка моделей"""
        return json_response({
            'success': True,
            'models': MODEL_CATALOG
        })

    # Обработчик вебхуков от Flyer Service
    async def flyer_webhook(request: Request) -> Response:
        """Обработка вебхуков от Flyer для партнерской системы"""
        logger.info(f"[FLYER WEBHOOK] Получен запрос от {request.client.host if request.client else 'unknown'}")
        try:
            # Читаем данные
            data = await request.json()
            
            # Логируем полученные данные для отладки
            logger.info(f"[FLYER WEBHOOK] Получены данные: {data}")
            
            # Flyer отправляет user_id когда пользователь выполнил подписку
            user_id = data.get('user_id')
            
            if user_id:
                logger.info(f"[FLYER WEBHOOK] Пользователь {user_id} подписался!")
                
                # Отправляем команду /start боту от имени пользователя
                # чтобы он прошел проверку и получил приветствие
                # Это проще, чем дублировать логику приветствия
                
                try:
                    from bot import bot, send_welcome_message
                    if bot:
                        # Очищаем кэш Flyer для пользователя
                        try:
                            from flyer_service import flyer_service
                            if flyer_service:
                                flyer_service.clear_cache(user_id)
                                # Помечаем в кэше что у пользователя есть доступ
                                from datetime import datetime
                                cache_key = f"access_{user_id}"
                                flyer_service._cache[cache_key] = (datetime.now().timestamp(), True)
                                logger.info(f"[FLYER WEBHOOK] Кэш обновлен для пользователя {user_id}")
                        except Exception as e:
                            logger.error(f"[FLYER WEBHOOK] Ошибка обновления кэша: {e}")
                        
                        # Отправляем приветственное сообщение напрямую
                        await send_welcome_message(user_id)
                        logger.info(f"[FLYER WEBHOOK] Приветственное сообщение отправлено пользователю {user_id}")
                        
                except ImportError as e:
                    logger.error(f"[FLYER WEBHOOK] Ошибка импорта: {e}")
                except Exception as e:
                    logger.error(f"[FLYER WEBHOOK] Ошибка обработки: {e}")
            
            return json_response({'status': 'ok'})
                
        except Exception as e:
            logger.error(f"[FLYER WEBHOOK] Ошибка: {e}")
            return json_response({'status': 'error', 'message': str(e)}, status=500)
    
    # Регистрируем маршруты
    app.router.add_get('/', index)
    app.router.add_post('/api/install-model', install_model)
    app.router.add_get('/api/models', get_models)
    app.router.add_post('/api/flyer-webhook', flyer_webhook)  # Добавляем эндпоинт для вебхуков
    
    # Добавляем статические файлы
    app.router.add_static('/static/', path='/root/static/', name='static')
    
    # Добавляем CORS ко всем маршрутам
    for route in list(app.router.routes()):
        cors.add(route)
    
    return app

def main():
    """Запуск веб-сервера"""
    import os
    
    host = os.environ.get('HTTP_HOST', '0.0.0.0')
    port = int(os.environ.get('HTTP_PORT', '8080'))
    
    print(f"🚀 Запуск нового сервера выбора моделей на http://{host}:{port}")
    print(f"🌐 Доступен по адресу: https://giftex.top")
    print("📱 Современный дизайн для мобильных устройств")
    print("✨ Переключатель между Базовыми и Премиум-моделями")
    
    web.run_app(asyncio.run(create_app()), host=host, port=port)

if __name__ == '__main__':
    main()