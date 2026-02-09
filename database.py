import aiosqlite
import os

# Railway Volumeのマウントパス（環境変数で指定がなければローカルのdataフォルダ）
DB_PATH = os.getenv("DB_PATH", "./data/bot.db")

class Database:
    def __init__(self):
        # ディレクトリがない場合は作成
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self.db_path = DB_PATH

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            # イベント管理テーブル
            await db.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    message_id INTEGER PRIMARY KEY,
                    channel_id INTEGER,
                    guild_id INTEGER,
                    owner_id INTEGER,
                    title TEXT,
                    date_str TEXT,
                    location TEXT,
                    required_num INTEGER,
                    status TEXT DEFAULT 'RECRUITING'
                )
            """)
            # 参加者テーブル
            await db.execute("""
                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_message_id INTEGER,
                    user_id INTEGER,
                    FOREIGN KEY(event_message_id) REFERENCES events(message_id)
                )
            """)
            await db.commit()

    async def create_event(self, message_id, channel_id, guild_id, owner_id, title, date_str, location, required_num):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO events (message_id, channel_id, guild_id, owner_id, title, date_str, location, required_num)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (message_id, channel_id, guild_id, owner_id, title, date_str, location, required_num))
            await db.commit()

    async def add_participant(self, message_id, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            # 重複チェック
            async with db.execute("SELECT id FROM participants WHERE event_message_id = ? AND user_id = ?", (message_id, user_id)) as cursor:
                if await cursor.fetchone():
                    return False
            
            await db.execute("INSERT INTO participants (event_message_id, user_id) VALUES (?, ?)", (message_id, user_id))
            await db.commit()
            return True

    async def remove_participant(self, message_id, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM participants WHERE event_message_id = ? AND user_id = ?", (message_id, user_id))
            await db.commit()

    async def get_event_data(self, message_id):
        """イベント情報と参加者リストをまとめて取得"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM events WHERE message_id = ?", (message_id,)) as cursor:
                event = await cursor.fetchone()
                if not event:
                    return None
            
            async with db.execute("SELECT user_id FROM participants WHERE event_message_id = ?", (message_id,)) as cursor:
                rows = await cursor.fetchall()
                participants = [row['user_id'] for row in rows]
            
            return dict(event), participants

    async def delete_event(self, message_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM events WHERE message_id = ?", (message_id,))
            await db.execute("DELETE FROM participants WHERE event_message_id = ?", (message_id,))
            await db.commit()

db = Database()