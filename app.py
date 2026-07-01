from flask import Flask, render_template, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from functools import wraps
import sqlite3, os, secrets
from datetime import datetime, timedelta

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key                   = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.permanent_session_lifetime   = timedelta(days=30)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
DB = os.environ.get('DATABASE_URL', os.path.join(os.path.dirname(__file__), 'study_tracker.db'))

# ══════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with get_db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT    NOT NULL,
            email       TEXT    UNIQUE NOT NULL,
            password    TEXT    NOT NULL,
            user_type   TEXT    DEFAULT 'user',
            avatar      TEXT    DEFAULT '🎓',
            is_banned   INTEGER DEFAULT 0,
            created_at  TEXT    DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS categories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            name        TEXT    NOT NULL,
            color       TEXT    DEFAULT '#6366f1',
            icon        TEXT    DEFAULT '📚',
            sort_order  INTEGER DEFAULT 0,
            created_at  TEXT    DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            title        TEXT    NOT NULL,
            category_id  INTEGER,
            status       TEXT    DEFAULT 'pending',
            time_limit   INTEGER DEFAULT 25,
            time_spent   INTEGER DEFAULT 0,
            created_at   TEXT    DEFAULT (datetime('now','localtime')),
            completed_at TEXT,
            FOREIGN KEY (user_id)     REFERENCES users(id)      ON DELETE CASCADE,
            FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS goals (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            title         TEXT    NOT NULL,
            type          TEXT    DEFAULT 'daily',
            target_value  REAL    DEFAULT 4.0,
            current_value REAL    DEFAULT 0.0,
            unit          TEXT    DEFAULT 'tasks',
            completed     INTEGER DEFAULT 0,
            created_at    TEXT    DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS study_sessions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            task_id    INTEGER,
            duration   INTEGER DEFAULT 0,
            date       TEXT    DEFAULT (date('now','localtime')),
            created_at TEXT    DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """)
        # Seed super-admin
        admin = c.execute("SELECT id FROM users WHERE email=?", ('sulimanhjksf@gmail.com',)).fetchone()
        if not admin:
            c.execute(
                "INSERT INTO users (username,email,password,user_type,avatar) VALUES (?,?,?,?,?)",
                ('Suliman', 'sulimanhjksf@gmail.com', generate_password_hash('1234'), 'admin', '👑')
            )
            c.commit()

# ══════════════════════════════════════════════════════════
# AUTH MIDDLEWARE
# ══════════════════════════════════════════════════════════
def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    with get_db() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

def login_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if 'user_id' not in session:
            return jsonify({'error': 'Unauthorized', 'code': 401}), 401
        u = current_user()
        if not u or u['is_banned']:
            session.clear()
            return jsonify({'error': 'Account suspended', 'code': 403}), 403
        return f(*a, **kw)
    return dec

def admin_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if 'user_id' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        u = current_user()
        if not u or u['user_type'] != 'admin':
            return jsonify({'error': 'Forbidden'}), 403
        return f(*a, **kw)
    return dec

# ══════════════════════════════════════════════════════════
# PAGES
# ══════════════════════════════════════════════════════════
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/manifest.json')
def pwa_manifest():
    return app.send_static_file('manifest.json')

@app.route('/sw.js')
def service_worker():
    resp = app.send_static_file('sw.js')
    resp.headers['Content-Type'] = 'application/javascript'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp

# ══════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════
@app.route('/api/auth/me')
def auth_me():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'user': None})
    with get_db() as c:
        u = c.execute("SELECT id,username,email,avatar,user_type,created_at FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        session.clear()
        return jsonify({'user': None})
    return jsonify({'user': dict(u)})

@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    d = request.json or {}
    username = (d.get('username') or '').strip()
    email    = (d.get('email')    or '').strip().lower()
    password = (d.get('password') or '')
    if not username or not email or not password:
        return jsonify({'error': 'All fields are required'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    if '@' not in email:
        return jsonify({'error': 'Invalid email address'}), 400
    with get_db() as c:
        if c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            return jsonify({'error': 'Email already registered'}), 409
        cur = c.execute(
            "INSERT INTO users (username,email,password,avatar) VALUES (?,?,?,?)",
            (username, email, generate_password_hash(password), '🎓')
        )
        c.commit()
        uid = cur.lastrowid
        # Default categories
        for name, color, icon in [
            ('Core Study','#6366f1','📚'), ('Research','#10b981','🔬'),
            ('Review','#f59e0b','📝'),    ('Practice','#a855f7','⚡'),
        ]:
            c.execute("INSERT INTO categories (user_id,name,color,icon) VALUES (?,?,?,?)", (uid,name,color,icon))
        c.commit()
    session['user_id'] = uid
    session.permanent = True
    return jsonify({'ok': True, 'user': {'id': uid, 'username': username, 'email': email, 'avatar': '🎓', 'user_type': 'user'}}), 201

@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    d = request.json or {}
    email    = (d.get('email')    or '').strip().lower()
    password = (d.get('password') or '')
    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400
    with get_db() as c:
        u = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not u or not check_password_hash(u['password'], password):
        return jsonify({'error': 'Invalid email or password'}), 401
    if u['is_banned']:
        return jsonify({'error': 'Your account has been suspended. Contact the admin.'}), 403
    session['user_id'] = u['id']
    session.permanent = True
    return jsonify({'ok': True, 'user': {'id': u['id'], 'username': u['username'], 'email': u['email'], 'avatar': u['avatar'], 'user_type': u['user_type']}})

@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/auth/profile', methods=['PUT'])
@login_required
def auth_update_profile():
    d = request.json or {}
    uid = session['user_id']
    sets, vals = [], []
    if (d.get('username') or '').strip():
        sets.append('username=?'); vals.append(d['username'].strip())
    if 'avatar' in d:
        sets.append('avatar=?'); vals.append(d['avatar'])
    if d.get('password'):
        if len(d['password']) < 6:
            return jsonify({'error': 'Password too short'}), 400
        sets.append('password=?'); vals.append(generate_password_hash(d['password']))
    if not sets:
        return jsonify({'error': 'Nothing to update'}), 400
    vals.append(uid)
    with get_db() as c:
        c.execute(f"UPDATE users SET {','.join(sets)} WHERE id=?", vals)
        c.commit()
        u = c.execute("SELECT id,username,email,avatar,user_type FROM users WHERE id=?", (uid,)).fetchone()
    return jsonify({'ok': True, 'user': dict(u)})

# ══════════════════════════════════════════════════════════
# CATEGORIES
# ══════════════════════════════════════════════════════════
@app.route('/api/categories', methods=['GET'])
@login_required
def get_categories():
    uid = session['user_id']
    with get_db() as c:
        rows = c.execute("SELECT * FROM categories WHERE user_id=? ORDER BY sort_order,created_at", (uid,)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/categories', methods=['POST'])
@login_required
def create_category():
    d = request.json or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Name required'}), 400
    uid = session['user_id']
    with get_db() as c:
        cur = c.execute("INSERT INTO categories (user_id,name,color,icon) VALUES (?,?,?,?)",
                        (uid, name, d.get('color','#6366f1'), d.get('icon','📚')))
        c.commit()
        row = c.execute("SELECT * FROM categories WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201

@app.route('/api/categories/<int:cid>', methods=['PUT'])
@login_required
def update_category(cid):
    d = request.json or {}
    uid = session['user_id']
    sets, vals = [], []
    for f in ('name','color','icon','sort_order'):
        if f in d:
            sets.append(f'{f}=?'); vals.append(d[f])
    if not sets:
        return jsonify({'error': 'Nothing to update'}), 400
    vals.extend([cid, uid])
    with get_db() as c:
        c.execute(f"UPDATE categories SET {','.join(sets)} WHERE id=? AND user_id=?", vals)
        c.commit()
        row = c.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone()
    return jsonify(dict(row))

@app.route('/api/categories/<int:cid>', methods=['DELETE'])
@login_required
def delete_category(cid):
    uid = session['user_id']
    with get_db() as c:
        c.execute("DELETE FROM categories WHERE id=? AND user_id=?", (cid, uid))
        c.commit()
    return jsonify({'ok': True})

# ══════════════════════════════════════════════════════════
# TASKS
# ══════════════════════════════════════════════════════════
TASK_SELECT = """
    SELECT t.*, c.name as category_name, c.color as category_color, c.icon as category_icon
    FROM tasks t LEFT JOIN categories c ON t.category_id=c.id
"""

@app.route('/api/tasks', methods=['GET'])
@login_required
def get_tasks():
    uid = session['user_id']
    with get_db() as c:
        rows = c.execute(TASK_SELECT + "WHERE t.user_id=? ORDER BY t.created_at DESC", (uid,)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/tasks', methods=['POST'])
@login_required
def create_task():
    d = request.json or {}
    title = (d.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Title required'}), 400
    uid = session['user_id']
    with get_db() as c:
        cur = c.execute("INSERT INTO tasks (user_id,title,category_id,time_limit) VALUES (?,?,?,?)",
                        (uid, title, d.get('category_id') or None, int(d.get('time_limit', 25))))
        c.commit()
        row = c.execute(TASK_SELECT + "WHERE t.id=?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201

@app.route('/api/tasks/<int:tid>', methods=['PUT'])
@login_required
def update_task(tid):
    d = request.json or {}
    uid = session['user_id']
    sets, vals = [], []
    for f in ('title','category_id','status','time_spent'):
        if f in d:
            sets.append(f'{f}=?'); vals.append(d[f] if f != 'category_id' else (d[f] or None))
    if 'status' in d and d['status'] == 'completed':
        sets.append('completed_at=?'); vals.append(datetime.now().isoformat())
    if not sets:
        return jsonify({'error': 'Nothing to update'}), 400
    vals.extend([tid, uid])
    with get_db() as c:
        c.execute(f"UPDATE tasks SET {','.join(sets)} WHERE id=? AND user_id=?", vals)
        c.commit()
        row = c.execute(TASK_SELECT + "WHERE t.id=?", (tid,)).fetchone()
    return jsonify(dict(row))

@app.route('/api/tasks/<int:tid>', methods=['DELETE'])
@login_required
def delete_task(tid):
    uid = session['user_id']
    with get_db() as c:
        c.execute("DELETE FROM tasks WHERE id=? AND user_id=?", (tid, uid))
        c.commit()
    return jsonify({'ok': True})

# ══════════════════════════════════════════════════════════
# GOALS
# ══════════════════════════════════════════════════════════
@app.route('/api/goals', methods=['GET'])
@login_required
def get_goals():
    uid = session['user_id']
    with get_db() as c:
        rows = c.execute("SELECT * FROM goals WHERE user_id=? ORDER BY type,created_at DESC", (uid,)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/goals', methods=['POST'])
@login_required
def create_goal():
    d = request.json or {}
    title = (d.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Title required'}), 400
    uid = session['user_id']
    with get_db() as c:
        cur = c.execute("INSERT INTO goals (user_id,title,type,target_value,unit) VALUES (?,?,?,?,?)",
                        (uid, title, d.get('type','daily'), float(d.get('target_value',4)), d.get('unit','tasks')))
        c.commit()
        row = c.execute("SELECT * FROM goals WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201

@app.route('/api/goals/<int:gid>', methods=['PUT'])
@login_required
def update_goal(gid):
    d = request.json or {}
    uid = session['user_id']
    sets, vals = [], []
    for f in ('title','current_value','target_value','completed'):
        if f in d:
            sets.append(f'{f}=?'); vals.append(d[f])
    if not sets:
        return jsonify({'error': 'Nothing to update'}), 400
    vals.extend([gid, uid])
    with get_db() as c:
        c.execute(f"UPDATE goals SET {','.join(sets)} WHERE id=? AND user_id=?", vals)
        c.commit()
        row = c.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    return jsonify(dict(row))

@app.route('/api/goals/<int:gid>', methods=['DELETE'])
@login_required
def delete_goal(gid):
    uid = session['user_id']
    with get_db() as c:
        c.execute("DELETE FROM goals WHERE id=? AND user_id=?", (gid, uid))
        c.commit()
    return jsonify({'ok': True})

# ══════════════════════════════════════════════════════════
# SESSIONS (Time Tracking)
# ══════════════════════════════════════════════════════════
@app.route('/api/sessions', methods=['POST'])
@login_required
def log_session():
    d = request.json or {}
    uid = session['user_id']
    with get_db() as c:
        c.execute("INSERT INTO study_sessions (user_id,task_id,duration,date) VALUES (?,?,?,?)",
                  (uid, d.get('task_id'), int(d.get('duration',0)),
                   d.get('date', datetime.now().strftime('%Y-%m-%d'))))
        c.commit()
    return jsonify({'ok': True})

# ══════════════════════════════════════════════════════════
# STATS
# ══════════════════════════════════════════════════════════
@app.route('/api/stats')
@login_required
def get_stats():
    uid   = session['user_id']
    today = datetime.now().strftime('%Y-%m-%d')
    with get_db() as c:
        weekly = []
        for i in range(6, -1, -1):
            day   = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            label = (datetime.now() - timedelta(days=i)).strftime('%a')
            row   = c.execute("SELECT COALESCE(SUM(duration),0) as m FROM study_sessions WHERE user_id=? AND date=?", (uid, day)).fetchone()
            weekly.append({'date': day, 'label': label, 'minutes': row['m']})

        cats = c.execute("""
            SELECT c.name as category, c.color, COALESCE(SUM(t.time_spent),0) as total
            FROM categories c
            LEFT JOIN tasks t ON t.category_id=c.id
            WHERE c.user_id=? GROUP BY c.id HAVING total>0
        """, (uid,)).fetchall()

        streak, check = 0, datetime.now()
        while True:
            row = c.execute("SELECT COUNT(*) as n FROM study_sessions WHERE user_id=? AND date=?", (uid, check.strftime('%Y-%m-%d'))).fetchone()
            if row['n'] > 0:
                streak += 1; check -= timedelta(days=1)
            else:
                break

        done    = c.execute("SELECT COUNT(*) as n FROM tasks WHERE user_id=? AND status='completed'", (uid,)).fetchone()['n']
        pending = c.execute("SELECT COUNT(*) as n FROM tasks WHERE user_id=? AND status='pending'",   (uid,)).fetchone()['n']
        expired = c.execute("SELECT COUNT(*) as n FROM tasks WHERE user_id=? AND status='expired'",   (uid,)).fetchone()['n']
        judged  = done + expired
        score   = int(done / judged * 100) if judged > 0 else 0
        week_start = (datetime.now() - timedelta(days=6)).strftime('%Y-%m-%d')
        week_mins  = c.execute("SELECT COALESCE(SUM(duration),0) as m FROM study_sessions WHERE user_id=? AND date>=?", (uid, week_start)).fetchone()['m']
        today_mins = c.execute("SELECT COALESCE(SUM(duration),0) as m FROM study_sessions WHERE user_id=? AND date=?",  (uid, today)).fetchone()['m']

    return jsonify({'weekly': weekly, 'categories': [dict(r) for r in cats], 'streak': streak,
                    'productivity_score': score, 'week_minutes': week_mins, 'today_minutes': today_mins,
                    'tasks_done': done, 'tasks_pending': pending, 'tasks_expired': expired})

# ══════════════════════════════════════════════════════════
# ADMIN
# ══════════════════════════════════════════════════════════
@app.route('/api/admin/stats')
@admin_required
def admin_stats():
    with get_db() as c:
        return jsonify({
            'total_users':    c.execute("SELECT COUNT(*) as n FROM users").fetchone()['n'],
            'banned_users':   c.execute("SELECT COUNT(*) as n FROM users WHERE is_banned=1").fetchone()['n'],
            'total_tasks':    c.execute("SELECT COUNT(*) as n FROM tasks").fetchone()['n'],
            'total_sessions': c.execute("SELECT COUNT(*) as n FROM study_sessions").fetchone()['n'],
        })

@app.route('/api/admin/users')
@admin_required
def admin_get_users():
    with get_db() as c:
        users = c.execute("""
            SELECT u.id, u.username, u.email, u.user_type, u.avatar, u.is_banned, u.created_at,
                   (SELECT COUNT(*) FROM tasks WHERE user_id=u.id) as task_count
            FROM users u ORDER BY u.created_at DESC
        """).fetchall()
    return jsonify([dict(u) for u in users])

@app.route('/api/admin/users/<int:uid>/ban', methods=['POST'])
@admin_required
def admin_ban(uid):
    if uid == session['user_id']:
        return jsonify({'error': 'Cannot ban yourself'}), 400
    with get_db() as c:
        u = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            return jsonify({'error': 'Not found'}), 404
        if u['user_type'] == 'admin':
            return jsonify({'error': 'Cannot ban another admin'}), 403
        new_val = 0 if u['is_banned'] else 1
        c.execute("UPDATE users SET is_banned=? WHERE id=?", (new_val, uid))
        c.commit()
    return jsonify({'ok': True, 'is_banned': bool(new_val)})

@app.route('/api/admin/users/<int:uid>', methods=['DELETE'])
@admin_required
def admin_delete_user(uid):
    if uid == session['user_id']:
        return jsonify({'error': 'Cannot delete yourself'}), 400
    with get_db() as c:
        u = c.execute("SELECT user_type FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            return jsonify({'error': 'Not found'}), 404
        if u['user_type'] == 'admin':
            return jsonify({'error': 'Cannot delete another admin'}), 403
        c.execute("DELETE FROM users WHERE id=?", (uid,))
        c.commit()
    return jsonify({'ok': True})

# ══════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════
init_db()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
