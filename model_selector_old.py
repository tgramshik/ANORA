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
MODEL_CATALOG = {
    "Любовница": {
        "title": "💋 Любовница",
        "subtitle": "Страстная соблазнительница",
        "description": "Твоя личная любовница, которая знает все твои желания. Интимн��е разговоры, ролевые игры и горячие фантазии. Она создана для того, чтобы воплощать твои самые сокровенные мечты.",
        "features": [
            "🔥 Интимные ролевые игры",
            "💋 Соблазнительные диалоги", 
            "📸 Генерация 18+ изображений",
            "😈 Воплощение фантазий"
        ],
        "age_rating": "18+",
        "category": "adult",
        "image": "/static/images/lovistnica.jpg",
        "color": "#ff1744",
        "gradient": "linear-gradient(135deg, #ff1744 0%, #d50000 100%)"
    },
    "Порноактриса": {
        "title": "🍑 Порноактриса",
        "subtitle": "Звезда взрослого кино",
        "description": "Опытная порноактриса, которая расскажет все секреты индустрии. Откровенные истории со съёмок, профессиональные советы и максимально развратные фантазии.",
        "features": [
            "🍆 Истории со съёмок",
            "💦 Профессиональные секр��ты",
            "🔞 Максимальная откровенность",
            "🎬 Опыт взрослого кино"
        ],
        "age_rating": "18+",
        "category": "adult",
        "image": "/static/images/pornstar.jpg",
        "color": "#e91e63",
        "gradient": "linear-gradient(135deg, #e91e63 0%, #ad1457 100%)"
    },
    "Подруга": {
        "title": "💞 Подруга",
        "subtitle": "Душевная спутница",
        "description": "Твоя лучшая подруга, которая всегда выслушает и поддержит. Романтические разговоры, душевные беседы и тёплое общение. Она тайно влюблена в тебя.",
        "features": [
            "💕 Романтические беседы",
            "🤗 Эмоциональная поддержка",
            "😊 Дружеское общение",
            "💖 Тайная влюблённость"
        ],
        "age_rating": "16+",
        "category": "romantic",
        "image": "/static/images/girlfriend.jpg",
        "color": "#e91e63",
        "gradient": "linear-gradient(135deg, #e91e63 0%, #c2185b 100%)"
    },
    "Астролог": {
        "title": "🧠 Астролог",
        "subtitle": "Мистическая провидица",
        "description": "Опытный астролог, который раскроет тайны твоей судьбы. Персональные гороскопы, предсказания будущего и мистические откровения от звёзд.",
        "features": [
            "🌙 Персональные гороскопы",
            "🔮 Предсказания судьбы",
            "✨ Мистические советы",
            "🌟 Астрологические карты"
        ],
        "age_rating": "12+",
        "category": "mystical",
        "image": "/static/images/astrologer.jpg",
        "color": "#9c27b0",
        "gradient": "linear-gradient(135deg, #9c27b0 0%, #7b1fa2 100%)"
    },
    "Учебный помощник": {
        "title": "📚 Учебный помощник",
        "subtitle": "Персональный наставник",
        "description": "Опытная преподавательница, которая поможет с любыми учебными задачами. Объяснения сложных тем, решение задач и проверка домашних заданий.",
        "features": [
            "📊 Решение задач",
            "💡 Объяснение тем",
            "📝 Проверка заданий",
            "🎓 Образовательная поддержка"
        ],
        "age_rating": "6+",
        "category": "educational",
        "image": "/static/images/teacher.jpg",
        "color": "#2196f3",
        "gradient": "linear-gradient(135deg, #2196f3 0%, #1976d2 100%)"
    }
}

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
        return row['current_model'] if row else 'Подруга'

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
        
        # Получаем текущую модель пользователя
        current_model = 'Подруга'
        if user_id.isdigit():
            async with Database(DB_PATH) as db:
                current_model = await db.get_user_model(int(user_id))

        html_content = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ANORA - Выбор AI Модели</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: 
                radial-gradient(circle at 20% 80%, rgba(120, 0, 50, 0.3) 0%, transparent 50%),
                radial-gradient(circle at 80% 20%, rgba(255, 23, 68, 0.2) 0%, transparent 50%),
                radial-gradient(circle at 40% 40%, rgba(156, 39, 176, 0.2) 0%, transparent 50%),
                linear-gradient(135deg, #0a0a0a 0%, #1a0a1a 30%, #2a0a0a 60%, #1a0a2a 100%);
            color: #ffffff;
            min-height: 100vh;
            overflow-x: hidden;
            position: relative;
        }}
        
        body::before {{
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: 
                radial-gradient(circle at 10% 20%, rgba(255, 23, 68, 0.1) 0%, transparent 20%),
                radial-gradient(circle at 90% 80%, rgba(233, 30, 99, 0.1) 0%, transparent 20%),
                radial-gradient(circle at 50% 50%, rgba(156, 39, 176, 0.05) 0%, transparent 30%);
            pointer-events: none;
            z-index: -1;
            animation: pulseBackground 8s ease-in-out infinite alternate;
        }}
        
        @keyframes pulseBackground {{
            0% {{ opacity: 0.3; }}
            100% {{ opacity: 0.7; }}
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }}
        
        .header {{
            text-align: center;
            margin-bottom: 40px;
            padding: 30px 0;
        }}
        
        .logo {{
            font-size: 3rem;
            font-weight: 900;
            background: linear-gradient(135deg, #ff1744, #e91e63, #9c27b0);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 10px;
            text-shadow: 0 0 30px rgba(255, 23, 68, 0.5);
        }}
        
        .subtitle {{
            font-size: 1.2rem;
            color: #ff6b9d;
            margin-bottom: 10px;
            font-weight: 300;
        }}
        
        .description {{
            color: #cccccc;
            font-size: 1rem;
            max-width: 600px;
            margin: 0 auto;
            line-height: 1.6;
        }}
        
        .models-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
            gap: 25px;
            margin-bottom: 40px;
        }}
        
        .model-card {{
            background: 
                linear-gradient(135deg, rgba(20, 20, 20, 0.9) 0%, rgba(30, 20, 30, 0.8) 100%);
            border-radius: 20px;
            padding: 25px;
            border: 2px solid transparent;
            transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            position: relative;
            overflow: hidden;
            backdrop-filter: blur(15px);
            cursor: pointer;
        }}
        
        .model-card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: linear-gradient(135deg, transparent 0%, rgba(255, 255, 255, 0.03) 50%, transparent 100%);
            opacity: 0;
            transition: opacity 0.3s ease;
            pointer-events: none;
        }}
        
        .model-card:hover {{
            transform: translateY(-8px) scale(1.02);
            box-shadow: 
                0 25px 50px rgba(0, 0, 0, 0.4),
                0 0 30px rgba(255, 23, 68, 0.1);
        }}
        
        .model-card:hover::before {{
            opacity: 1;
        }}
        
        .model-card.adult {{
            border-color: rgba(255, 23, 68, 0.3);
        }}
        
        .model-card.adult:hover {{
            border-color: #ff1744;
            box-shadow: 
                0 25px 50px rgba(255, 23, 68, 0.3),
                0 0 40px rgba(255, 23, 68, 0.2),
                inset 0 0 20px rgba(255, 23, 68, 0.1);
            animation: pulseAdult 2s ease-in-out infinite alternate;
        }}
        
        .model-card.adult::after {{
            content: '';
            position: absolute;
            top: -2px;
            left: -2px;
            right: -2px;
            bottom: -2px;
            background: linear-gradient(45deg, #ff1744, #e91e63, #ff1744, #e91e63);
            background-size: 400% 400%;
            border-radius: 22px;
            z-index: -1;
            opacity: 0;
            transition: opacity 0.3s ease;
            animation: gradientShift 3s ease infinite;
        }}
        
        .model-card.adult:hover::after {{
            opacity: 0.7;
        }}
        
        @keyframes pulseAdult {{
            0% {{ 
                box-shadow: 
                    0 25px 50px rgba(255, 23, 68, 0.3),
                    0 0 40px rgba(255, 23, 68, 0.2),
                    inset 0 0 20px rgba(255, 23, 68, 0.1);
            }}
            100% {{ 
                box-shadow: 
                    0 30px 60px rgba(255, 23, 68, 0.4),
                    0 0 50px rgba(255, 23, 68, 0.3),
                    inset 0 0 30px rgba(255, 23, 68, 0.15);
            }}
        }}
        
        @keyframes gradientShift {{
            0% {{ background-position: 0% 50%; }}
            50% {{ background-position: 100% 50%; }}
            100% {{ background-position: 0% 50%; }}
        }}
        
        .model-card.romantic {{
            border-color: rgba(233, 30, 99, 0.3);
        }}
        
        .model-card.romantic:hover {{
            border-color: #e91e63;
            box-shadow: 
                0 25px 50px rgba(233, 30, 99, 0.3),
                0 0 30px rgba(233, 30, 99, 0.2);
        }}
        
        .model-card.mystical {{
            border-color: rgba(156, 39, 176, 0.3);
        }}
        
        .model-card.mystical:hover {{
            border-color: #9c27b0;
            box-shadow: 0 20px 40px rgba(156, 39, 176, 0.2);
        }}
        
        .model-card.educational {{
            border-color: rgba(33, 150, 243, 0.3);
        }}
        
        .model-card.educational:hover {{
            border-color: #2196f3;
            box-shadow: 0 20px 40px rgba(33, 150, 243, 0.2);
        }}
        
        .model-card.selected {{
            border-color: #ff1744;
            background: rgba(255, 23, 68, 0.1);
        }}
        
        .model-header {{
            display: flex;
            align-items: center;
            margin-bottom: 15px;
        }}
        
        .model-icon {{
            width: 60px;
            height: 60px;
            border-radius: 15px;
            margin-right: 15px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.8rem;
            color: white;
        }}
        
        .model-title {{
            font-size: 1.4rem;
            font-weight: 700;
            margin-bottom: 5px;
        }}
        
        .model-subtitle {{
            color: #cccccc;
            font-size: 0.9rem;
            font-weight: 300;
        }}
        
        .model-description {{
            color: #aaaaaa;
            line-height: 1.5;
            margin-bottom: 20px;
            font-size: 0.95rem;
        }}
        
        .model-features {{
            list-style: none;
            margin-bottom: 20px;
        }}
        
        .model-features li {{
            color: #cccccc;
            margin-bottom: 8px;
            font-size: 0.9rem;
            display: flex;
            align-items: center;
        }}
        
        .model-features li::before {{
            content: "✨";
            margin-right: 8px;
        }}
        
        .model-footer {{
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        
        .age-rating {{
            background: rgba(255, 255, 255, 0.1);
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: 600;
        }}
        
        .age-rating.adult {{
            background: rgba(255, 23, 68, 0.2);
            color: #ff6b9d;
        }}
        
        .install-btn {{
            background: linear-gradient(135deg, #ff1744, #e91e63);
            border: none;
            padding: 12px 24px;
            border-radius: 25px;
            color: white;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            font-size: 0.9rem;
        }}
        
        .install-btn:hover {{
            transform: scale(1.05);
            box-shadow: 0 10px 20px rgba(255, 23, 68, 0.3);
        }}
        
        .install-btn.installed {{
            background: linear-gradient(135deg, #4caf50, #45a049);
        }}
        
        .current-model {{
            text-align: center;
            margin-bottom: 30px;
            padding: 20px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 15px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }}
        
        .current-model-title {{
            color: #ff6b9d;
            font-size: 1.1rem;
            margin-bottom: 10px;
        }}
        
        .current-model-name {{
            font-size: 1.3rem;
            font-weight: 700;
            color: #ffffff;
        }}
        
        .warning {{
            background: rgba(255, 152, 0, 0.1);
            border: 1px solid rgba(255, 152, 0, 0.3);
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 30px;
            text-align: center;
        }}
        
        .warning-icon {{
            font-size: 1.5rem;
            margin-bottom: 10px;
        }}
        
        .loading {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.8);
            z-index: 1000;
            justify-content: center;
            align-items: center;
        }}
        
        .loading-content {{
            text-align: center;
            color: white;
        }}
        
        .spinner {{
            width: 50px;
            height: 50px;
            border: 3px solid rgba(255, 255, 255, 0.3);
            border-top: 3px solid #ff1744;
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
            padding: 20px 30px;
            border-radius: 15px;
            z-index: 1001;
            text-align: center;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
        }}
        
        @media (max-width: 768px) {{
            .models-grid {{
                grid-template-columns: 1fr;
                gap: 20px;
            }}
            
            .container {{
                padding: 15px;
            }}
            
            .logo {{
                font-size: 2.5rem;
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
                Каждая модель обладает уникальной личностью и специализацией. 
                Выберите ту, которая лучше всего подходит для ваших потребностей.
            </div>
        </div>
        
        <div class="warning">
            <div class="warning-icon">⚠️</div>
            <div>Некоторые модели содержат контент для взрослых (18+). Убедитесь, что вам исполнилось 18 лет.</div>
        </div>
        
        <div class="current-model">
            <div class="current-model-title">Текущая активная модель:</div>
            <div class="current-model-name" id="currentModel">{current_model}</div>
        </div>
        
        <div class="models-grid">
"""

        # Генерируем карточки моделей
        for model_key, model_info in MODEL_CATALOG.items():
            is_selected = model_key == current_model
            selected_class = "selected" if is_selected else ""
            btn_text = "✓ Установлена" if is_selected else "Установить"
            btn_class = "installed" if is_selected else ""
            
            html_content += f"""
            <div class="model-card {model_info['category']} {selected_class}" data-model="{model_key}">
                <div class="model-header">
                    <div class="model-icon" style="background: {model_info['gradient']}">
                        {model_info['title'].split()[0]}
                    </div>
                    <div>
                        <div class="model-title">{model_info['title']}</div>
                        <div class="model-subtitle">{model_info['subtitle']}</div>
                    </div>
                </div>
                
                <div class="model-description">
                    {model_info['description']}
                </div>
                
                <ul class="model-features">
"""
            
            for feature in model_info['features']:
                html_content += f"<li>{feature}</li>"
            
            age_class = "adult" if model_info['age_rating'] == "18+" else ""
            
            html_content += f"""
                </ul>
                
                <div class="model-footer">
                    <div class="age-rating {age_class}">{model_info['age_rating']}</div>
                    <button class="install-btn {btn_class}" onclick="installModel('{model_key}')">
                        {btn_text}
                    </button>
                </div>
            </div>
"""

        html_content += f"""
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
        
        // Получаем user_id из Telegram WebApp
        const userId = tg.initDataUnsafe?.user?.id || {user_id};
        
        async function installModel(modelName) {{
            const loading = document.getElementById('loading');
            const successMessage = document.getElementById('successMessage');
            
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
                    // Обновля��м интерфейс
                    updateModelSelection(modelName);
                    
                    // Показываем сообщение об успехе
                    loading.style.display = 'none';
                    successMessage.style.display = 'block';
                    
                    // Отправляем данные в Telegram
                    tg.sendData(JSON.stringify({{
                        action: 'model_selected',
                        model: modelName,
                        user_id: userId
                    }}));
                    
                    setTimeout(() => {{
                        successMessage.style.display = 'none';
                        tg.close();
                    }}, 2000);
                }} else {{
                    throw new Error(result.error || 'Ошибка установки модели');
                }}
            }} catch (error) {{
                loading.style.display = 'none';
                alert('Ошибка: ' + error.message);
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

    # Регистрируем маршруты
    app.router.add_get('/', index)
    app.router.add_post('/api/install-model', install_model)
    app.router.add_get('/api/models', get_models)
    
    # Добавляем CORS ко всем маршрутам
    for route in list(app.router.routes()):
        cors.add(route)
    
    return app

def main():
    """Запуск веб-сервера"""
    import os
    
    host = os.environ.get('HTTP_HOST', '0.0.0.0')
    port = int(os.environ.get('HTTP_PORT', '8080'))  # Порт 8080
    
    print(f"🚀 Запуск сервера выбора моделей на http://{host}:{port}")
    print(f"🌐 Доступен по адресу: https://giftex.top")
    print("📱 Откройте этот URL в Telegram WebApp для выбора моделей")
    
    web.run_app(asyncio.run(create_app()), host=host, port=port)

if __name__ == '__main__':
    main()