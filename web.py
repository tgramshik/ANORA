#!/usr/bin/env python3
import asyncio
from typing import List, Dict, Any
from datetime import datetime

import aiosqlite
from aiohttp import web
import html as html_lib

# Конфиг: путь к БД такой же, как в боте
from config import DB_PATH


def _humanize_time_ago(iso_str: str) -> str:
	try:
		if not iso_str:
			return "-"
		dt = datetime.fromisoformat(iso_str)
		now = datetime.now()
		delta = now - dt
		seconds = int(max(delta.total_seconds(), 0))
		if seconds < 60:
			return f"{seconds} сек назад"
		minutes = seconds // 60
		if minutes < 60:
			return f"{minutes} мин назад"
		hours = minutes // 60
		if hours < 24:
			return f"{hours} ч назад"
		days = hours // 24
		if days < 30:
			return f"{days} д назад"
		months = days // 30
		if months < 12:
			return f"{months} мес назад"
		years = months // 12
		return f"{years} г назад"
	except Exception:
		return iso_str or "-"


class Database:
	def __init__(self, db_path: str):
		self.db_path = str(db_path)
		self._conn: aiosqlite.Connection | None = None

	async def __aenter__(self):
		self._conn = await aiosqlite.connect(self.db_path, timeout=30.0, isolation_level=None, check_same_thread=False)
		self._conn.row_factory = aiosqlite.Row
		return self

	async def __aexit__(self, exc_type, exc, tb):
		if self._conn:
			await self._conn.close()
			self._conn = None

	async def get_all_users_basic(self) -> List[Dict[str, Any]]:
		assert self._conn is not None
		cursor = await self._conn.execute('SELECT id, username, name, last_active, current_model FROM users ORDER BY datetime(last_active) DESC')
		users: List[Dict[str, Any]] = []
		async for row in cursor:
			users.append({
				'id': row['id'],
				'username': row['username'],
				'name': row['name'],
				'last_active': row['last_active'],
				'current_model': row['current_model'],
			})
		return users

	async def get_user_with_context(self, user_id: int) -> Dict[str, Any] | None:
		assert self._conn is not None
		cursor = await self._conn.execute('SELECT * FROM users WHERE id = ?', (user_id,))
		row = await cursor.fetchone()
		if not row:
			return None
		context_raw = row['context'] or '[]'
		try:
			import json
			context = json.loads(context_raw)
		except Exception:
			context = []
		return {
			'id': row['id'],
			'username': row['username'],
			'name': row['name'],
			'last_active': row['last_active'],
			'current_model': row['current_model'],
			'context': context,
		}


async def create_app() -> web.Application:
	app = web.Application()

	async def index(request: web.Request) -> web.Response:
		raise web.HTTPFound('/dialogs')

	async def dialogs(request: web.Request) -> web.Response:
		user_id_q = request.rel_url.query.get('user_id')
		async with Database(DB_PATH) as db:
			users = await db.get_all_users_basic()
			selected_user = None
			messages: List[Dict[str, Any]] = []
			if user_id_q and user_id_q.isdigit():
				selected_user = await db.get_user_with_context(int(user_id_q))
				if selected_user:
					messages = selected_user.get('context', [])
			# Если пользователь не выбран явно — берём первого из списка
			if not selected_user and users:
				selected_user = await db.get_user_with_context(int(users[0]['id']))
				if selected_user:
					messages = selected_user.get('context', [])

		html: List[str] = []
		html.append("""
<!DOCTYPE html>
<html lang=\"ru\">
<head>
<meta charset=\"UTF-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>Anora — Диалоги</title>
<style>
:root { --bg:#f4f6f8; --panel:#ffffff; --panel-2:#f9fafb; --accent:#2a8cff; --border:#e5e7eb; --text:#111827; --muted:#6b7280; }
* { box-sizing: border-box; }
body { margin:0; font-family: Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; background: var(--bg); color:var(--text); }
.header { position:sticky; top:0; z-index:10; padding:14px 18px; background:var(--panel); border-bottom:1px solid var(--border); display:flex; align-items:center; gap:12px; }
.header .logo { width:24px; height:24px; background:var(--accent); border-radius:6px; box-shadow: 0 2px 8px rgba(42,140,255,.35); }
.header .title { font-weight:700; letter-spacing:.2px; }
.container { display:flex; height: calc(100vh - 56px); }
.sidebar { width:360px; border-right:1px solid var(--border); overflow:auto; background:var(--panel); }
.list { padding:8px; display:flex; flex-direction:column; gap:6px; }
.user { padding:10px 12px; border:1px solid var(--border); border-radius:12px; text-decoration:none; display:block; color:var(--text); background: var(--panel-2); transition:.12s ease; }
.user:hover { background:#eef2f7; }
.user.active { border-color:#d1d5db; background:#eef2f7; }
.user .name { font-weight:600; }
.user .meta { font-size:12px; color:var(--muted); margin-top:4px; display:flex; gap:8px; align-items:center; }
.user .chip { padding:2px 6px; background:#eef2f7; border:1px solid #dbe3ec; border-radius:999px; font-size:11px; color:#2563eb; }
.main { flex:1; display:flex; flex-direction:column; background: var(--panel); }
.topbar { padding:12px 16px; border-bottom:1px solid var(--border); display:flex; gap:12px; align-items:center; background:var(--panel); position:sticky; top:0; z-index:5; }
.topbar .title { font-weight:700; }
.badge { background:#eef2f7; border:1px solid #dbe3ec; color:#2563eb; padding:2px 8px; border-radius:999px; font-size:12px; }
.chat { flex:1; overflow:auto; padding:20px; display:flex; flex-direction:column; gap:12px; background:var(--panel-2); }
.msg { max-width:72%; padding:10px 12px; border-radius:14px; line-height:1.5; white-space:pre-wrap; word-break:break-word; border:1px solid var(--border); }
.msg.user { align-self:flex-start; background:#ffffff; }
.msg.assistant { align-self:flex-end; background:#e8f1ff; border-color:#cfe1ff; }
.time { display:block; margin-top:6px; font-size:11px; color:var(--muted); }
.placeholder { color:var(--muted); padding:24px; }
</style>
</head>
<body>
<div class=\"header\"><span class=\"logo\"></span><span class=\"title\">Anora · Диалоги</span></div>
<div class=\"container\">
<div class=\"sidebar\">
<div class=\"list\">
""")
		current_id = int(selected_user['id']) if selected_user else None
		for u in users:
			uid = u['id']
			name = (u['name'] or '').strip() or 'Пользователь'
			username = (u['username'] or '').strip()
			last_active = (u['last_active'] or '')
			model = (u['current_model'] or '')
			cls = "user active" if current_id == uid else "user"
			display = f"{name}"
			if username:
				display += f" (@{username})"
			rel = _humanize_time_ago(last_active)
			q = (f"{name}" + (f" @{username}" if username else "") + f" {model}").lower()
			html.append(
				f"<a class=\"{cls}\" data-q=\"{html_lib.escape(q)}\" href=\"/dialogs?user_id={uid}\">"
				f"<div class=\"name\">{html_lib.escape(display)}</div>"
				f"<div class=\"meta\">Последняя активность: {html_lib.escape(rel)} <span class=\"chip\">{html_lib.escape(model)}</span></div>"
				f"</a>"
			)
		html.append("""
</div>
</div>
<div class=\"main\">
""")
		# Top bar
		if selected_user:
			title_name = (selected_user.get('name') or 'Пользователь')
			model = selected_user.get('current_model') or ''
			html.append(f"<div class=\"topbar\"><span class=\"title\">Диалог с {html_lib.escape(title_name)}</span><span class=\"badge\">{html_lib.escape(model)}</span></div>")
		else:
			html.append('<div class="topbar"><span class="title">Выберите пользователя слева</span></div>')

		html.append('<div class="chat">')
		if not selected_user:
			html.append('<div class="placeholder">Нет выбранного пользователя. Нажмите на пользователя в списке слева, чтобы просмотреть историю.</div>')
		else:
			if not messages:
				html.append('<div class="placeholder">История пуста.</div>')
			else:
				for m in messages:
					role = m.get('role') or 'assistant'
					content = m.get('content') or ''
					ts = m.get('timestamp') or ''
					cls = 'assistant' if role != 'user' else 'user'
					html.append(f'<div class="msg {cls}">{html_lib.escape(content)}<span class="time">{html_lib.escape(ts)}</span></div>')

		html.append("""
</div>
</div>
</div>
</body>
</html>
""")
		return web.Response(text=''.join(html), content_type='text/html')

	app.router.add_get('/', index)
	app.router.add_get('/dialogs', dialogs)
	return app


def main():
	import os
	host = os.environ.get('HTTP_HOST', '127.0.0.1')
	port = int(os.environ.get('HTTP_PORT', '8090'))
	web.run_app(asyncio.run(create_app()), host=host, port=port)


if __name__ == '__main__':
	main() 