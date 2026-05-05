import sqlite3
import bcrypt
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
DATABASE = os.path.join(INSTANCE_DIR, 'game.db')


def get_db():
    """Получение соединения с БД"""
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Создание всех таблиц"""
    conn = get_db()
    cursor = conn.cursor()

    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            avatar TEXT DEFAULT 'default.png',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total_games INTEGER DEFAULT 0,
            total_wins INTEGER DEFAULT 0,
            total_score INTEGER DEFAULT 0
        )
    ''')

    # Таблица комнат
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rooms (
            id TEXT PRIMARY KEY,
            host_id INTEGER,
            status TEXT DEFAULT 'waiting',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (host_id) REFERENCES users (id)
        )
    ''')

    # Таблица игроков в комнате
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS room_players (
            room_id TEXT,
            user_id INTEGER,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (room_id, user_id),
            FOREIGN KEY (room_id) REFERENCES rooms (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    # Таблица для голосования за ведущего
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS room_votes (
            room_id TEXT,
            voter_id INTEGER,
            voted_for_id INTEGER,
            PRIMARY KEY (room_id, voter_id),
            FOREIGN KEY (room_id) REFERENCES rooms (id),
            FOREIGN KEY (voter_id) REFERENCES users (id),
            FOREIGN KEY (voted_for_id) REFERENCES users (id)
        )
    ''')

    # Таблица игровой статистики
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS game_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            room_id TEXT,
            score INTEGER DEFAULT 0,
            place INTEGER,
            played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    # Таблица сообщений чата
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    conn.commit()
    conn.close()

    # Создаем папку для аватарок
    avatars_dir = os.path.join(BASE_DIR, 'static', 'avatars')
    os.makedirs(avatars_dir, exist_ok=True)

    # Создаем аватарку по умолчанию
    default_avatar = os.path.join(avatars_dir, 'default.png')
    if not os.path.exists(default_avatar):
        try:
            from PIL import Image
            img = Image.new('RGB', (200, 200), color='#667eea')
            img.save(default_avatar)
        except:
            pass

    print("✅ База данных инициализирована")
    print(f"📁 База данных: {DATABASE}")


class User:
    @staticmethod
    def create(username, password):
        """Создание нового пользователя"""
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO users (username, password_hash) VALUES (?, ?)',
                (username, password_hash)
            )
            conn.commit()
            user_id = cursor.lastrowid
            print(f"✅ Создан пользователь: {username} (ID: {user_id})")
            return user_id
        except sqlite3.IntegrityError:
            print(f"❌ Пользователь {username} уже существует")
            return None
        finally:
            conn.close()

    @staticmethod
    def authenticate(username, password):
        """Проверка логина и пароля"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()
        conn.close()

        if user and bcrypt.checkpw(password.encode('utf-8'), user['password_hash']):
            print(f"✅ Успешный вход: {username}")
            return dict(user)
        else:
            if user:
                print(f"❌ Неверный пароль для: {username}")
            else:
                print(f"❌ Пользователь не найден: {username}")
            return None

    @staticmethod
    def get_by_id(user_id):
        """Получение пользователя по ID"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        user = cursor.fetchone()
        conn.close()
        return dict(user) if user else None

    @staticmethod
    def update_avatar(user_id, avatar_filename):
        """Обновление аватарки"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET avatar = ? WHERE id = ?', (avatar_filename, user_id))
        conn.commit()
        conn.close()
        print(f"✅ Обновлена аватарка для пользователя ID {user_id}")

    @staticmethod
    def update_username(user_id, new_username):
        """Обновление никнейма пользователя"""
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute('UPDATE users SET username = ? WHERE id = ?', (new_username, user_id))
            conn.commit()
            success = True
            print(f"✅ Никнейм изменен на {new_username} для ID {user_id}")
        except sqlite3.IntegrityError:
            success = False
            print(f"❌ Никнейм {new_username} уже занят")
        finally:
            conn.close()
        return success

    @staticmethod
    def update_stats(user_id, score, is_win=False):
        """Обновление статистики после игры"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE users SET total_games = total_games + 1, total_score = total_score + ? WHERE id = ?',
            (score, user_id)
        )
        if is_win:
            cursor.execute('UPDATE users SET total_wins = total_wins + 1 WHERE id = ?', (user_id,))
        conn.commit()
        conn.close()
        print(f"📊 Обновлена статистика для пользователя ID {user_id}: +{score} очков")


class Room:
    @staticmethod
    def create(room_id, host_id):
        """Создание новой комнаты"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO rooms (id, host_id) VALUES (?, ?)', (room_id, host_id))
        conn.commit()
        conn.close()
        print(f"🏠 Создана комната {room_id} (хост: ID {host_id})")

    @staticmethod
    def get(room_id):
        """Получение информации о комнате"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM rooms WHERE id = ?', (room_id,))
        room = cursor.fetchone()
        conn.close()
        return dict(room) if room else None

    @staticmethod
    def get_all_rooms():
        """Получение всех активных комнат"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id, host_id, status FROM rooms WHERE status = "waiting"')
        rooms = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rooms]

    @staticmethod
    def add_player(room_id, user_id):
        """Добавление игрока в комнату"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR IGNORE INTO room_players (room_id, user_id) VALUES (?, ?)',
            (room_id, user_id)
        )
        conn.commit()
        conn.close()
        print(f"👤 Игрок ID {user_id} добавлен в комнату {room_id}")

    @staticmethod
    def remove_player(room_id, user_id):
        """Удаление игрока из комнаты"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM room_players WHERE room_id = ? AND user_id = ?', (room_id, user_id))
        cursor.execute('DELETE FROM room_votes WHERE room_id = ? AND voter_id = ?', (room_id, user_id))
        conn.commit()
        conn.close()
        print(f"👋 Игрок ID {user_id} удален из комнаты {room_id}")

    @staticmethod
    def get_players(room_id):
        """Получение списка игроков в комнате"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT u.id, u.username, u.avatar
            FROM room_players rp
            JOIN users u ON rp.user_id = u.id
            WHERE rp.room_id = ?
            ORDER BY rp.joined_at
        ''', (room_id,))
        players = cursor.fetchall()
        conn.close()
        return [dict(p) for p in players]

    @staticmethod
    def get_host(room_id):
        """Получение хоста комнаты"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT host_id FROM rooms WHERE id = ?', (room_id,))
        result = cursor.fetchone()
        conn.close()
        return result['host_id'] if result else None

    @staticmethod
    def delete_room(room_id):
        """Удаление комнаты и всех связанных данных"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM room_players WHERE room_id = ?', (room_id,))
        cursor.execute('DELETE FROM chat_messages WHERE room_id = ?', (room_id,))
        cursor.execute('DELETE FROM room_votes WHERE room_id = ?', (room_id,))
        cursor.execute('DELETE FROM rooms WHERE id = ?', (room_id,))
        conn.commit()
        conn.close()
        print(f"🗑️ Комната {room_id} удалена")


class Chat:
    @staticmethod
    def save_message(room_id, user_id, username, message):
        """Сохранение сообщения в БД"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO chat_messages (room_id, user_id, username, message) VALUES (?, ?, ?, ?)',
            (room_id, user_id, username, message)
        )
        conn.commit()
        conn.close()

    @staticmethod
    def get_messages(room_id, limit=50):
        """Получение последних сообщений"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT username, message, created_at 
            FROM chat_messages 
            WHERE room_id = ? 
            ORDER BY created_at DESC LIMIT ?
        ''', (room_id, limit))
        messages = cursor.fetchall()
        conn.close()
        return list(reversed([dict(m) for m in messages]))


class Vote:
    @staticmethod
    def cast_vote(room_id, voter_id, voted_for_id):
        """Голосование за ведущего"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR REPLACE INTO room_votes (room_id, voter_id, voted_for_id) VALUES (?, ?, ?)',
            (room_id, voter_id, voted_for_id)
        )
        conn.commit()
        conn.close()

    @staticmethod
    def get_votes(room_id):
        """Получение результатов голосования"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT voted_for_id, COUNT(*) as vote_count
            FROM room_votes
            WHERE room_id = ?
            GROUP BY voted_for_id
            ORDER BY vote_count DESC
        ''', (room_id,))
        results = cursor.fetchall()
        conn.close()
        return [dict(r) for r in results]

    @staticmethod
    def clear_votes(room_id):
        """Очистка голосов в комнате"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM room_votes WHERE room_id = ?', (room_id,))
        conn.commit()
        conn.close()