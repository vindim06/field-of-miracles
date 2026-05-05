from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import string
import os
from datetime import datetime
from werkzeug.utils import secure_filename
from PIL import Image

from database import init_db, User, Room, Chat, Vote, get_db

app = Flask(__name__)
app.secret_key = 'pole-chudes-secret-key-2024-change-this'
socketio = SocketIO(app, cors_allowed_origins="*")

# Настройки для загрузки аватарок
UPLOAD_FOLDER = 'static/avatars'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# Инициализация БД
init_db()

# Хранилище игровых сессий (в памяти)
game_sessions = {}


class GameSession:
    def __init__(self, room_id):
        self.room_id = room_id
        self.players = []  # {'name': str, 'sid': str, 'user_id': int, 'score': int}
        self.leader = None
        self.turn_order = []
        self.current_turn_index = 0
        self.hidden_word = ''
        self.display_word = ''
        self.question = ''
        self.used_letters = set()
        self.round_points = 0
        self.round_type = 'points'
        self.prize_mode = False
        self.plus_mode = False
        self.word_set = False  # Флаг: загадано ли слово
        self.status = 'waiting'

    def add_player(self, username, sid, user_id):
        self.players.append({
            'name': username,
            'sid': sid,
            'user_id': user_id,
            'score': 0
        })

    def get_player_by_sid(self, sid):
        for p in self.players:
            if p['sid'] == sid:
                return p
        return None

    def get_player_by_name(self, name):
        for p in self.players:
            if p['name'] == name:
                return p
        return None

    def get_player_by_user_id(self, user_id):
        for p in self.players:
            if p['user_id'] == user_id:
                return p
        return None

    def set_leader_by_vote(self, winner_user_id):
        """Установка ведущего по результатам голосования"""
        winner = self.get_player_by_user_id(winner_user_id)
        if winner:
            self.leader = winner['name']
            other_players = [p['name'] for p in self.players if p['name'] != self.leader]
            random.shuffle(other_players)
            self.turn_order = other_players
            self.current_turn_index = 0
            print(f"🎲 По голосованию ведущий: {self.leader}, Очередь: {self.turn_order}")
            return True
        return False

    def randomize_leader_and_order(self):
        """Рандомный выбор ведущего (если голосование не дало результатов)"""
        player_names = [p['name'] for p in self.players]
        self.leader = random.choice(player_names)
        other_players = [p for p in player_names if p != self.leader]
        random.shuffle(other_players)
        self.turn_order = other_players
        self.current_turn_index = 0
        print(f"🎲 Случайный ведущий: {self.leader}, Очередь: {self.turn_order}")

    def get_current_player(self):
        if self.current_turn_index < len(self.turn_order):
            return self.turn_order[self.current_turn_index]
        return None

    def next_turn(self):
        self.current_turn_index += 1
        if self.current_turn_index >= len(self.turn_order):
            self.current_turn_index = 0
        return self.get_current_player()

    def check_letter(self, letter):
        letter = letter.upper()
        if letter in self.used_letters:
            return {'success': False, 'message': 'Эта буква уже была!'}

        self.used_letters.add(letter)

        if letter in self.hidden_word:
            display_list = list(self.display_word)
            for i, char in enumerate(self.hidden_word):
                if char == letter:
                    display_list[i] = letter
            self.display_word = ''.join(display_list)
            return {'success': True, 'message': f'Буква {letter} есть в слове!', 'display_word': self.display_word}
        else:
            return {'success': False, 'message': f'Буквы {letter} нет в слове!'}

    def is_word_guessed(self):
        return '_' not in self.display_word


# ============== HTTP РОУТЫ ==============

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('lobby_menu'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user = User.authenticate(username, password)
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('lobby_menu'))
        else:
            return render_template('login.html', error='Неверное имя или пароль')

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm = request.form['confirm_password']

        if password != confirm:
            return render_template('register.html', error='Пароли не совпадают')

        if len(password) < 4:
            return render_template('register.html', error='Пароль слишком короткий (минимум 4 символа)')

        user_id = User.create(username, password)
        if user_id:
            return redirect(url_for('login'))
        else:
            return render_template('register.html', error='Пользователь уже существует')

    return render_template('register.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/lobby')
def lobby_menu():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('lobby_menu.html', username=session['username'])


@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = User.get_by_id(session['user_id'])

    if request.method == 'POST':
        if 'avatar' in request.files:
            file = request.files['avatar']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{session['user_id']}_{file.filename}")
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

                img = Image.open(file)
                img.thumbnail((200, 200))
                img.save(filepath)

                User.update_avatar(session['user_id'], filename)
                return redirect(url_for('profile'))

    return render_template('profile.html', user=user)


@app.route('/change_username', methods=['POST'])
def change_username():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Не авторизован'})

    new_username = request.json.get('username', '').strip()

    if len(new_username) < 3:
        return jsonify({'success': False, 'error': 'Никнейм должен быть не менее 3 символов'})

    if len(new_username) > 20:
        return jsonify({'success': False, 'error': 'Никнейм не должен превышать 20 символов'})

    success = User.update_username(session['user_id'], new_username)

    if success:
        session['username'] = new_username
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Пользователь с таким именем уже существует'})


@app.route('/room/<room_id>')
def room_page(room_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    room_data = Room.get(room_id)
    if not room_data:
        return redirect(url_for('lobby_menu'))

    return render_template('room.html', room_id=room_id, username=session['username'])


@app.route('/create_room')
def create_room():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    room_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    Room.create(room_id, session['user_id'])
    Room.add_player(room_id, session['user_id'])

    return redirect(url_for('room_page', room_id=room_id))


@app.route('/game/<room_id>')
def game_page(room_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if room_id not in game_sessions:
        return redirect(url_for('room_page', room_id=room_id))

    return render_template('game.html', room_id=room_id, username=session['username'])


@app.route('/api/chat_history/<room_id>')
def chat_history(room_id):
    if 'user_id' not in session:
        return jsonify([])

    messages = Chat.get_messages(room_id)
    return jsonify([{
        'username': m['username'],
        'message': m['message'],
        'time': datetime.strptime(m['created_at'], '%Y-%m-%d %H:%M:%S').strftime('%H:%M:%S')
    } for m in messages])


# ============== SOCKET.IO СОБЫТИЯ ==============

@socketio.on('connect')
def handle_connect():
    print(f'✅ Клиент подключился: {request.sid}')


@socketio.on('disconnect')
def handle_disconnect():
    print(f'❌ Клиент отключился: {request.sid}')
    socketio.emit('players_update', {}, broadcast=True)


@socketio.on('join_room')
def handle_join_room(data):
    room_id = data['room_id']
    user_id = session.get('user_id')
    username = session.get('username')

    if not user_id:
        emit('error', {'message': 'Не авторизован'})
        return

    room_data = Room.get(room_id)
    if not room_data:
        emit('error', {'message': 'Комната не найдена!'})
        return

    Room.add_player(room_id, user_id)
    join_room(room_id)

    players = Room.get_players(room_id)
    host_id = Room.get_host(room_id)

    emit('players_update', {
        'players': [{'username': p['username'], 'avatar': p['avatar'], 'user_id': p['id']} for p in players],
        'host_id': host_id
    }, room=room_id)

    is_host = (host_id == user_id)
    emit('role_assigned', {
        'is_host': is_host,
        'players_count': len(players)
    }, room=request.sid)

    # Уведомление в чат
    emit('new_message', {
        'username': 'Система',
        'message': f'{username} присоединился к комнате',
        'time': datetime.now().strftime('%H:%M:%S')
    }, room=room_id)

    print(f"👤 {username} присоединился к комнате {room_id}")


@socketio.on('leave_room')
def handle_leave_room(data):
    room_id = data['room_id']
    user_id = session.get('user_id')
    username = session.get('username')

    host_id = Room.get_host(room_id)
    is_host = (host_id == user_id)

    if is_host:
        players = Room.get_players(room_id)

        for p in players:
            Room.remove_player(room_id, p['id'])

        Room.delete_room(room_id)

        if room_id in game_sessions:
            del game_sessions[room_id]

        emit('room_closed', {'message': 'Создатель покинул комнату. Комната закрыта.'}, room=room_id)
        print(f"🏠 Комната {room_id} закрыта (хост {username} вышел)")
    else:
        Room.remove_player(room_id, user_id)
        leave_room(room_id)

        emit('new_message', {
            'username': 'Система',
            'message': f'{username} покинул комнату',
            'time': datetime.now().strftime('%H:%M:%S')
        }, room=room_id)

        players = Room.get_players(room_id)
        new_host_id = Room.get_host(room_id)
        emit('players_update', {
            'players': [{'username': p['username'], 'avatar': p['avatar'], 'user_id': p['id']} for p in players],
            'host_id': new_host_id
        }, room=room_id)
        print(f"👋 {username} покинул комнату {room_id}")

        if len(players) == 0:
            Room.delete_room(room_id)
            print(f"🏠 Комната {room_id} удалена (пустая)")

    emit('redirect_to_lobby', {}, room=request.sid)


@socketio.on('send_message')
def handle_send_message(data):
    room_id = data['room_id']
    message = data['message'].strip()
    user_id = session.get('user_id')
    username = session.get('username')

    if not message or len(message) > 200:
        return

    Chat.save_message(room_id, user_id, username, message)

    emit('new_message', {
        'username': username,
        'message': message,
        'time': datetime.now().strftime('%H:%M:%S')
    }, room=room_id)


@socketio.on('cast_vote')
def handle_cast_vote(data):
    room_id = data['room_id']
    voted_for_id = data['voted_for_id']
    user_id = session.get('user_id')

    Vote.cast_vote(room_id, user_id, voted_for_id)

    votes = Vote.get_votes(room_id)
    emit('votes_update', {'votes': votes}, room=room_id)
    print(f"🗳️ Голос в комнате {room_id} от {user_id} за {voted_for_id}")


@socketio.on('start_game_from_room')
def handle_start_game(data):
    room_id = data['room_id']
    user_id = session.get('user_id')
    username = session.get('username')

    host_id = Room.get_host(room_id)
    if host_id != user_id:
        emit('error', {'message': 'Только создатель может начать игру!'})
        return

    players_data = Room.get_players(room_id)

    if len(players_data) < 2:
        emit('error', {'message': 'Нужно минимум 2 игрока для начала игры!'})
        return

    game_session = GameSession(room_id)

    for p in players_data:
        game_session.add_player(p['username'], None, p['id'])

    # Проверяем результаты голосования
    votes = Vote.get_votes(room_id)
    if votes:
        winner_id = votes[0]['voted_for_id']
        winner = game_session.get_player_by_user_id(winner_id)
        if winner:
            game_session.set_leader_by_vote(winner_id)
            emit('leader_selected', {'leader': winner['name']}, room=room_id)
        else:
            game_session.randomize_leader_and_order()
    else:
        game_session.randomize_leader_and_order()

    Vote.clear_votes(room_id)

    game_session.status = 'game'
    game_sessions[room_id] = game_session

    emit('game_ready', {'game_url': f'/game/{room_id}'}, room=room_id)
    print(f"🚀 Игра началась в комнате {room_id}")


@socketio.on('join_game')
def handle_join_game(data):
    room_id = data['room_id']
    user_id = session.get('user_id')
    username = session.get('username')

    game = game_sessions.get(room_id)
    if not game:
        emit('error', {'message': 'Игровая сессия не найдена'})
        return

    for p in game.players:
        if p['user_id'] == user_id:
            p['sid'] = request.sid
            break

    join_room(room_id)

    # Отправляем состояние игры с флагом word_set
    emit('game_state', {
        'leader': game.leader,
        'turn_order': game.turn_order,
        'current_player': game.get_current_player(),
        'word_set': game.word_set,
        'players': [{'name': p['name'], 'score': p['score'], 'avatar': 'default.png'} for p in game.players]
    }, room=request.sid)

    if game.hidden_word:
        emit('round_started', {
            'question': game.question,
            'word_length': len(game.hidden_word),
            'display_word': game.display_word
        }, room=room_id)

    print(f"🎮 {username} подключился к игровой сессии {room_id}")


@socketio.on('set_word_and_question')
def handle_set_word_and_question(data):
    room_id = data['room_id']
    word = data['word'].upper().strip()
    question = data['question'].strip()
    username = session.get('username')

    game = game_sessions.get(room_id)
    if not game:
        emit('error', {'message': 'Игровая сессия не найдена'})
        return

    if game.leader != username:
        emit('error', {'message': 'Только ведущий может загадывать слово!'})
        return

    if game.word_set:
        emit('error', {'message': 'Слово уже загадано!'})
        return

    game.hidden_word = word
    game.display_word = '_' * len(word)
    game.question = question
    game.used_letters = set()
    game.word_set = True

    # Отправляем подтверждение ведущему, что слово загадано
    emit('word_set_confirmation', {'message': 'Слово загадано! Вы переключены в режим наблюдателя.'}, room=request.sid)

    # Отправляем всем остальным начало раунда
    emit('round_started', {
        'question': question,
        'word_length': len(word),
        'display_word': game.display_word
    }, room=room_id, skip_sid=request.sid)

    # Обновляем состояние игры для всех (особенно для ведущего, который теперь наблюдатель)
    emit('game_state_update', {
        'word_set': True
    }, room=room_id)

    print(f"📝 Ведущий {username} загадал слово '{word}' в комнате {room_id}")


@socketio.on('spin_wheel')
def handle_spin_wheel(data):
    room_id = data['room_id']
    username = session.get('username')

    game = game_sessions.get(room_id)
    if not game:
        return

    current_player = game.get_current_player()
    if current_player != username:
        emit('error', {'message': 'Сейчас не твой ход!'})
        return

    # Реальные сектора как в Поле Чудес
    sectors = [
        {'type': 'points', 'value': 50, 'name': '50'},
        {'type': 'points', 'value': 100, 'name': '100'},
        {'type': 'points', 'value': 150, 'name': '150'},
        {'type': 'points', 'value': 200, 'name': '200'},
        {'type': 'points', 'value': 250, 'name': '250'},
        {'type': 'points', 'value': 300, 'name': '300'},
        {'type': 'points', 'value': 350, 'name': '350'},
        {'type': 'points', 'value': 400, 'name': '400'},
        {'type': 'points', 'value': 450, 'name': '450'},
        {'type': 'points', 'value': 500, 'name': '500'},
        {'type': 'prize', 'value': 0, 'name': 'ПРИЗ'},
        {'type': 'bankrupt', 'value': 0, 'name': 'БАНКРОТ'},
        {'type': 'plus', 'value': 0, 'name': '+'},
        {'type': 'zero', 'value': 0, 'name': '0'},
        {'type': 'double', 'value': 2, 'name': 'x2'}
    ]

    selected = random.choice(sectors)
    game.round_points = selected['value']
    game.round_type = selected['type']

    # Специальная обработка для разных секторов
    if selected['type'] == 'bankrupt':
        emit('wheel_result', {
            'player': username,
            'points': 0,
            'sector': selected['name'],
            'type': 'bankrupt',
            'message': '💸 БАНКРОТ! Очки в этом раунде сгорают!'
        }, room=room_id)
        return

    elif selected['type'] == 'zero':
        next_player = game.next_turn()
        emit('wheel_result', {
            'player': username,
            'points': 0,
            'sector': selected['name'],
            'type': 'zero',
            'message': '🎯 Выпал 0! Ход переходит к следующему игроку!',
            'next_player': next_player
        }, room=room_id)
        return

    elif selected['type'] == 'prize':
        emit('wheel_result', {
            'player': username,
            'points': 0,
            'sector': selected['name'],
            'type': 'prize',
            'message': '🎁 ПРИЗ! Вы можете открыть любую букву бесплатно!'
        }, room=room_id)
        game.prize_mode = True
        return

    elif selected['type'] == 'plus':
        emit('wheel_result', {
            'player': username,
            'points': 0,
            'sector': selected['name'],
            'type': 'plus',
            'message': '✨ Сектор +! Вы можете открыть любую букву!'
        }, room=room_id)
        game.plus_mode = True
        return

    elif selected['type'] == 'double' and game.round_points > 0:
        game.round_points = game.round_points * 2
        emit('wheel_result', {
            'player': username,
            'points': game.round_points,
            'sector': selected['name'],
            'type': 'double',
            'message': f'✨ x2! Очки удвоены! Теперь у вас {game.round_points} очков!'
        }, room=room_id)
        return

    # Обычный сектор с очками
    emit('wheel_result', {
        'player': username,
        'points': selected['value'],
        'sector': selected['name'],
        'type': 'points'
    }, room=room_id)

    print(f"🎲 {username} выиграл {selected['value']} очков (сектор: {selected['name']})")


@socketio.on('guess_letter')
def handle_guess_letter(data):
    room_id = data['room_id']
    letter = data['letter'].upper().strip()
    username = session.get('username')

    game = game_sessions.get(room_id)
    if not game or len(letter) != 1 or not letter.isalpha():
        return

    current_player = game.get_current_player()
    if current_player != username:
        emit('error', {'message': 'Сейчас не твой ход!'})
        return

    # Обработка режима prize (бесплатная буква)
    prize_mode = game.prize_mode
    game.prize_mode = False
    plus_mode = game.plus_mode
    game.plus_mode = False

    result = game.check_letter(letter)

    if result['success']:
        player = game.get_player_by_name(username)
        if not prize_mode and not plus_mode:
            player['score'] += game.round_points
        elif prize_mode:
            emit('prize_used', {'message': '🎁 Использован ПРИЗ! Буква открыта бесплатно!'}, room=room_id)

        emit('players_update_game', {
            'players': [{'name': p['name'], 'score': p['score']} for p in game.players]
        }, room=room_id)

        if game.is_word_guessed():
            for p in game.players:
                User.update_stats(p['user_id'], p['score'], p['name'] == current_player)

            emit('game_over', {
                'winner': current_player,
                'word': game.hidden_word,
                'scores': {p['name']: p['score'] for p in game.players}
            }, room=room_id)

            if room_id in game_sessions:
                del game_sessions[room_id]
            return

        emit('letter_guessed', {
            'player': current_player,
            'letter': letter,
            'display_word': game.display_word,
            'points_earned': 0 if (prize_mode or plus_mode) else game.round_points,
            'total_score': player['score']
        }, room=room_id)
    else:
        next_player = game.next_turn()
        emit('letter_missed', {
            'player': current_player,
            'letter': letter,
            'message': result['message'],
            'next_player': next_player
        }, room=room_id)

    print(f"🔤 {username} назвал букву '{letter}': {'угадал' if result['success'] else 'ошибся'}")


@socketio.on('guess_word')
def handle_guess_word(data):
    room_id = data['room_id']
    guessed_word = data['word'].upper().strip()
    username = session.get('username')

    game = game_sessions.get(room_id)
    if not game:
        return

    current_player = game.get_current_player()
    if current_player != username:
        emit('error', {'message': 'Сейчас не твой ход!'})
        return

    if guessed_word == game.hidden_word:
        player = game.get_player_by_name(username)
        player['score'] += game.round_points * 2

        for p in game.players:
            User.update_stats(p['user_id'], p['score'], p['name'] == current_player)

        emit('game_over', {
            'winner': current_player,
            'word': game.hidden_word,
            'scores': {p['name']: p['score'] for p in game.players}
        }, room=room_id)

        if room_id in game_sessions:
            del game_sessions[room_id]
    else:
        next_player = game.next_turn()
        emit('word_missed', {
            'player': current_player,
            'guessed_word': guessed_word,
            'next_player': next_player
        }, room=room_id)

    print(
        f"💬 {username} назвал слово '{guessed_word}': {'угадал' if guessed_word == game.hidden_word else 'не угадал'}")


if __name__ == '__main__':
    print("=" * 50)
    print("🎡 Поле Чудес - Сервер запущен!")
    print("📍 Локальный адрес: http://localhost:5000")
    print("📍 Для друга: http://[ваш-IP-адрес]:5000")
    print("=" * 50)
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)