from flask import Flask, render_template, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from functools import wraps
import os, secrets
from datetime import datetime, date, timedelta

# ── App ──────────────────────────────────────────────────────
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key                        = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.permanent_session_lifetime        = timedelta(days=30)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['MAX_CONTENT_LENGTH']      = 5 * 1024 * 1024

# ── Database layer (SQLite local / PostgreSQL on Render) ─────
_DB_URL = os.environ.get('DATABASE_URL', '')
USE_PG  = 'postgres' in _DB_URL.lower()

if USE_PG:
    import psycopg2, psycopg2.extras
    _PG_URL = _DB_URL.replace('postgres://', 'postgresql://', 1)
else:
    import sqlite3 as _sq
    _SQ = os.path.join(os.path.dirname(__file__), 'study_tracker.db')


def _r2d(row):
    """Row → plain dict, converting date/datetime to ISO strings."""
    if row is None:
        return None
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, (datetime, date)):
            d[k] = v.isoformat()
    return d


class Db:
    """Unified SQLite / PostgreSQL context manager."""

    def __init__(self):
        if USE_PG:
            self._cn = psycopg2.connect(_PG_URL,
                                        cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            self._cn = _sq.connect(_SQ)
            self._cn.row_factory = _sq.Row
            self._cn.execute('PRAGMA foreign_keys = ON')

    # replace ? with %s for postgres
    def _q(self, sql):
        return sql.replace('?', '%s') if USE_PG else sql

    def execute(self, sql, p=()):
        if USE_PG:
            cur = self._cn.cursor()
            cur.execute(self._q(sql), p or ())
            return cur
        return self._cn.execute(sql, p or ())

    def insert(self, sql, p=()):
        """Run INSERT and return new row id."""
        if USE_PG:
            cur = self._cn.cursor()
            cur.execute(self._q(sql) + ' RETURNING id', p or ())
            return cur.fetchone()['id']
        cur = self._cn.execute(sql, p or ())
        return cur.lastrowid

    def commit(self):
        self._cn.commit()

    def rollback(self):
        try: self._cn.rollback()
        except Exception: pass

    def close(self):
        try: self._cn.close()
        except Exception: pass

    def __enter__(self): return self

    def __exit__(self, exc, *_):
        if exc: self.rollback()
        else:   self.commit()
        self.close()


def get_db(): return Db()


# ── Schema ───────────────────────────────────────────────────
_PG_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY, username TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
        user_type TEXT DEFAULT 'user', avatar TEXT DEFAULT '🎓',
        is_banned INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS categories (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL, color TEXT DEFAULT '#6366f1',
        icon TEXT DEFAULT '📚', sort_order INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS tasks (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
        status TEXT DEFAULT 'pending', time_limit INTEGER DEFAULT 25,
        time_spent INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT NOW(),
        completed_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS goals (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL, type TEXT DEFAULT 'daily',
        target_value REAL DEFAULT 4.0, current_value REAL DEFAULT 0.0,
        unit TEXT DEFAULT 'tasks', completed INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS study_sessions (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        task_id INTEGER, duration INTEGER DEFAULT 0,
        date DATE DEFAULT CURRENT_DATE, created_at TIMESTAMP DEFAULT NOW()
    )""",
]

_SQ_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
    user_type TEXT DEFAULT 'user', avatar TEXT DEFAULT '🎓',
    is_banned INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    name TEXT NOT NULL, color TEXT DEFAULT '#6366f1',
    icon TEXT DEFAULT '📚', sort_order INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    title TEXT NOT NULL, category_id INTEGER, status TEXT DEFAULT 'pending',
    time_limit INTEGER DEFAULT 25, time_spent INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime')), completed_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    title TEXT NOT NULL, type TEXT DEFAULT 'daily',
    target_value REAL DEFAULT 4.0, current_value REAL DEFAULT 0.0,
    unit TEXT DEFAULT 'tasks', completed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS study_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    task_id INTEGER, duration INTEGER DEFAULT 0,
    date TEXT DEFAULT (date('now','localtime')),
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""

_DEFAULT_CATS = [
    ('Core Study', '#6366f1', '📚'),
    ('Research',   '#10b981', '🔬'),
    ('Review',     '#f59e0b', '📝'),
    ('Practice',   '#a855f7', '⚡'),
]

def _seed_default_cats(db, uid):
    if not db.execute("SELECT id FROM categories WHERE user_id=?", (uid,)).fetchone():
        for name, color, icon in _DEFAULT_CATS:
            db.insert("INSERT INTO categories (user_id,name,color,icon) VALUES (?,?,?,?)",
                      (uid, name, color, icon))

def init_db():
    with get_db() as db:
        if USE_PG:
            for stmt in _PG_SCHEMA:
                db.execute(stmt)
        else:
            db._cn.executescript(_SQ_SCHEMA)
        # Seed admin
        admin = db.execute("SELECT id FROM users WHERE email=?",
                           ('sulimanhjksf@gmail.com',)).fetchone()
        if not admin:
            uid = db.insert(
                "INSERT INTO users (username,email,password,user_type,avatar) VALUES (?,?,?,?,?)",
                ('Suliman', 'sulimanhjksf@gmail.com',
                 generate_password_hash('1234'), 'admin', '👑')
            )
            _seed_default_cats(db, uid)
        else:
            _seed_default_cats(db, admin['id'])

# ── Auth helpers ─────────────────────────────────────────────
def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def login_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if 'user_id' not in session:
            return jsonify({'error': 'Please sign in'}), 401
        u = current_user()
        if not u:
            session.clear()
            return jsonify({'error': 'Session expired'}), 401   # <-- 401 not 403
        if u['is_banned']:
            session.clear()
            return jsonify({'error': 'Account suspended'}), 403
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


def _int(v):
    try: return int(v or 0)
    except Exception: return 0

# ── Pages ────────────────────────────────────────────────────
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

# ── Auth ─────────────────────────────────────────────────────
@app.route('/api/auth/me')
def auth_me():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'user': None})
    with get_db() as db:
        u = db.execute(
            "SELECT id,username,email,avatar,user_type,created_at FROM users WHERE id=?",
            (uid,)).fetchone()
    if not u:
        session.clear()
        return jsonify({'user': None})
    return jsonify({'user': _r2d(u)})


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
    with get_db() as db:
        if db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            return jsonify({'error': 'Email already registered'}), 409
        uid = db.insert(
            "INSERT INTO users (username,email,password,avatar) VALUES (?,?,?,?)",
            (username, email, generate_password_hash(password), '🎓')
        )
        for name, color, icon in [
            ('Core Study','#6366f1','📚'), ('Research','#10b981','🔬'),
            ('Review','#f59e0b','📝'),    ('Practice','#a855f7','⚡'),
        ]:
            db.insert("INSERT INTO categories (user_id,name,color,icon) VALUES (?,?,?,?)",
                      (uid, name, color, icon))
    session['user_id'] = uid
    session.permanent = True
    return jsonify({'ok': True, 'user': {
        'id': uid, 'username': username, 'email': email,
        'avatar': '🎓', 'user_type': 'user'
    }}), 201


@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    d = request.json or {}
    email    = (d.get('email')    or '').strip().lower()
    password = (d.get('password') or '')
    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400
    with get_db() as db:
        u = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not u or not check_password_hash(u['password'], password):
        return jsonify({'error': 'Invalid email or password'}), 401
    if u['is_banned']:
        return jsonify({'error': 'Your account has been suspended.'}), 403
    # Auto-seed default categories for users who have none (e.g. after DB migration)
    with get_db() as db:
        _seed_default_cats(db, u['id'])
    session['user_id'] = u['id']
    session.permanent = True
    return jsonify({'ok': True, 'user': {
        'id': u['id'], 'username': u['username'], 'email': u['email'],
        'avatar': u['avatar'], 'user_type': u['user_type']
    }})


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
    with get_db() as db:
        db.execute(f"UPDATE users SET {','.join(sets)} WHERE id=?", vals)
        u = db.execute(
            "SELECT id,username,email,avatar,user_type FROM users WHERE id=?", (uid,)
        ).fetchone()
    return jsonify({'ok': True, 'user': _r2d(u)})

# ── Categories ───────────────────────────────────────────────
@app.route('/api/categories', methods=['GET'])
@login_required
def get_categories():
    uid = session['user_id']
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM categories WHERE user_id=? ORDER BY sort_order,id", (uid,)
        ).fetchall()
    return jsonify([_r2d(r) for r in rows])


@app.route('/api/categories', methods=['POST'])
@login_required
def create_category():
    d = request.json or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Name required'}), 400
    uid = session['user_id']
    with get_db() as db:
        cid = db.insert(
            "INSERT INTO categories (user_id,name,color,icon) VALUES (?,?,?,?)",
            (uid, name, d.get('color', '#6366f1'), d.get('icon', '📚'))
        )
        row = db.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone()
    return jsonify(_r2d(row)), 201


@app.route('/api/categories/<int:cid>', methods=['PUT'])
@login_required
def update_category(cid):
    d = request.json or {}
    uid = session['user_id']
    sets, vals = [], []
    for f in ('name', 'color', 'icon', 'sort_order'):
        if f in d:
            sets.append(f'{f}=?'); vals.append(d[f])
    if not sets:
        return jsonify({'error': 'Nothing to update'}), 400
    vals.extend([cid, uid])
    with get_db() as db:
        db.execute(f"UPDATE categories SET {','.join(sets)} WHERE id=? AND user_id=?", vals)
        row = db.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone()
    return jsonify(_r2d(row))


@app.route('/api/categories/<int:cid>', methods=['DELETE'])
@login_required
def delete_category(cid):
    uid = session['user_id']
    with get_db() as db:
        db.execute("DELETE FROM categories WHERE id=? AND user_id=?", (cid, uid))
    return jsonify({'ok': True})

# ── Tasks ────────────────────────────────────────────────────
_TASK_SQL = """
    SELECT t.*, c.name as category_name, c.color as category_color, c.icon as category_icon
    FROM tasks t LEFT JOIN categories c ON t.category_id=c.id
"""

@app.route('/api/tasks', methods=['GET'])
@login_required
def get_tasks():
    uid = session['user_id']
    with get_db() as db:
        rows = db.execute(
            _TASK_SQL + "WHERE t.user_id=? ORDER BY t.created_at DESC", (uid,)
        ).fetchall()
    return jsonify([_r2d(r) for r in rows])


@app.route('/api/tasks', methods=['POST'])
@login_required
def create_task():
    d = request.json or {}
    title = (d.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Title required'}), 400
    uid = session['user_id']
    with get_db() as db:
        tid = db.insert(
            "INSERT INTO tasks (user_id,title,category_id,time_limit) VALUES (?,?,?,?)",
            (uid, title, d.get('category_id') or None, _int(d.get('time_limit', 25)))
        )
        row = db.execute(_TASK_SQL + "WHERE t.id=?", (tid,)).fetchone()
    return jsonify(_r2d(row)), 201


@app.route('/api/tasks/<int:tid>', methods=['PUT'])
@login_required
def update_task(tid):
    d = request.json or {}
    uid = session['user_id']
    sets, vals = [], []
    for f in ('title', 'category_id', 'status', 'time_spent'):
        if f in d:
            sets.append(f'{f}=?')
            vals.append(d[f] if f != 'category_id' else (d[f] or None))
    if 'status' in d and d['status'] == 'completed':
        sets.append('completed_at=?'); vals.append(datetime.now().isoformat())
    if not sets:
        return jsonify({'error': 'Nothing to update'}), 400
    vals.extend([tid, uid])
    with get_db() as db:
        db.execute(f"UPDATE tasks SET {','.join(sets)} WHERE id=? AND user_id=?", vals)
        row = db.execute(_TASK_SQL + "WHERE t.id=?", (tid,)).fetchone()
    return jsonify(_r2d(row))


@app.route('/api/tasks/<int:tid>', methods=['DELETE'])
@login_required
def delete_task(tid):
    uid = session['user_id']
    with get_db() as db:
        db.execute("DELETE FROM tasks WHERE id=? AND user_id=?", (tid, uid))
    return jsonify({'ok': True})

# ── Goals ────────────────────────────────────────────────────
@app.route('/api/goals', methods=['GET'])
@login_required
def get_goals():
    uid = session['user_id']
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM goals WHERE user_id=? ORDER BY type,id DESC", (uid,)
        ).fetchall()
    return jsonify([_r2d(r) for r in rows])


@app.route('/api/goals', methods=['POST'])
@login_required
def create_goal():
    d = request.json or {}
    title = (d.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Title required'}), 400
    uid = session['user_id']
    with get_db() as db:
        gid = db.insert(
            "INSERT INTO goals (user_id,title,type,target_value,unit) VALUES (?,?,?,?,?)",
            (uid, title, d.get('type', 'daily'), float(d.get('target_value', 4)),
             d.get('unit', 'tasks'))
        )
        row = db.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    return jsonify(_r2d(row)), 201


@app.route('/api/goals/<int:gid>', methods=['PUT'])
@login_required
def update_goal(gid):
    d = request.json or {}
    uid = session['user_id']
    sets, vals = [], []
    for f in ('title', 'current_value', 'target_value', 'completed'):
        if f in d:
            sets.append(f'{f}=?'); vals.append(d[f])
    if not sets:
        return jsonify({'error': 'Nothing to update'}), 400
    vals.extend([gid, uid])
    with get_db() as db:
        db.execute(f"UPDATE goals SET {','.join(sets)} WHERE id=? AND user_id=?", vals)
        row = db.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    return jsonify(_r2d(row))


@app.route('/api/goals/<int:gid>', methods=['DELETE'])
@login_required
def delete_goal(gid):
    uid = session['user_id']
    with get_db() as db:
        db.execute("DELETE FROM goals WHERE id=? AND user_id=?", (gid, uid))
    return jsonify({'ok': True})

# ── Sessions ─────────────────────────────────────────────────
@app.route('/api/sessions', methods=['POST'])
@login_required
def log_session():
    d = request.json or {}
    uid = session['user_id']
    with get_db() as db:
        db.insert(
            "INSERT INTO study_sessions (user_id,task_id,duration,date) VALUES (?,?,?,?)",
            (uid, d.get('task_id'), _int(d.get('duration', 0)),
             d.get('date', datetime.now().strftime('%Y-%m-%d')))
        )
    return jsonify({'ok': True})

# ── Stats ────────────────────────────────────────────────────
@app.route('/api/stats')
@login_required
def get_stats():
    uid   = session['user_id']
    today = datetime.now().strftime('%Y-%m-%d')
    with get_db() as db:
        weekly = []
        for i in range(6, -1, -1):
            day   = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            label = (datetime.now() - timedelta(days=i)).strftime('%a')
            row   = db.execute(
                "SELECT COALESCE(SUM(duration),0) as m FROM study_sessions WHERE user_id=? AND CAST(date AS TEXT)=?",
                (uid, day)).fetchone()
            weekly.append({'date': day, 'label': label, 'minutes': _int(row['m'] if row else 0)})

        cats = db.execute("""
            SELECT c.name as category, c.color, COALESCE(SUM(t.time_spent),0) as total
            FROM categories c
            LEFT JOIN tasks t ON t.category_id=c.id
            WHERE c.user_id=? GROUP BY c.id, c.name, c.color
        """, (uid,)).fetchall()
        cats = [_r2d(r) for r in cats if _int(r['total'] if r else 0) > 0]

        streak, check = 0, datetime.now()
        for _ in range(366):
            row = db.execute(
                "SELECT COUNT(*) as n FROM study_sessions WHERE user_id=? AND CAST(date AS TEXT)=?",
                (uid, check.strftime('%Y-%m-%d'))).fetchone()
            if row and _int(row['n']) > 0:
                streak += 1
                check -= timedelta(days=1)
            else:
                break

        def cnt(sql): return _int((db.execute(sql, (uid,)).fetchone() or {}).get('n', 0))
        done    = cnt("SELECT COUNT(*) as n FROM tasks WHERE user_id=? AND status='completed'")
        pending = cnt("SELECT COUNT(*) as n FROM tasks WHERE user_id=? AND status='pending'")
        expired = cnt("SELECT COUNT(*) as n FROM tasks WHERE user_id=? AND status='expired'")
        judged  = done + expired
        score   = int(done / judged * 100) if judged else 0

        week_start = (datetime.now() - timedelta(days=6)).strftime('%Y-%m-%d')
        wm = db.execute("SELECT COALESCE(SUM(duration),0) as m FROM study_sessions WHERE user_id=? AND CAST(date AS TEXT)>=?",
                        (uid, week_start)).fetchone()
        tm = db.execute("SELECT COALESCE(SUM(duration),0) as m FROM study_sessions WHERE user_id=? AND CAST(date AS TEXT)=?",
                        (uid, today)).fetchone()

    return jsonify({
        'weekly': weekly, 'categories': cats,
        'streak': streak, 'productivity_score': score,
        'week_minutes': _int(wm['m'] if wm else 0),
        'today_minutes': _int(tm['m'] if tm else 0),
        'tasks_done': done, 'tasks_pending': pending, 'tasks_expired': expired,
    })

# ── Admin ────────────────────────────────────────────────────
@app.route('/api/admin/stats')
@admin_required
def admin_stats():
    with get_db() as db:
        def cnt(sql):
            return _int((db.execute(sql).fetchone() or {}).get('n', 0))
        return jsonify({
            'total_users':    cnt("SELECT COUNT(*) as n FROM users"),
            'banned_users':   cnt("SELECT COUNT(*) as n FROM users WHERE is_banned=1"),
            'total_tasks':    cnt("SELECT COUNT(*) as n FROM tasks"),
            'total_sessions': cnt("SELECT COUNT(*) as n FROM study_sessions"),
        })


@app.route('/api/admin/users')
@admin_required
def admin_get_users():
    with get_db() as db:
        users = db.execute("""
            SELECT u.id, u.username, u.email, u.user_type, u.avatar,
                   u.is_banned, u.created_at,
                   (SELECT COUNT(*) FROM tasks WHERE user_id=u.id) as task_count
            FROM users u ORDER BY u.id DESC
        """).fetchall()
    return jsonify([_r2d(u) for u in users])


@app.route('/api/admin/users/<int:uid>/ban', methods=['POST'])
@admin_required
def admin_ban(uid):
    if uid == session['user_id']:
        return jsonify({'error': 'Cannot ban yourself'}), 400
    with get_db() as db:
        u = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            return jsonify({'error': 'Not found'}), 404
        if u['user_type'] == 'admin':
            return jsonify({'error': 'Cannot ban another admin'}), 403
        new_val = 0 if u['is_banned'] else 1
        db.execute("UPDATE users SET is_banned=? WHERE id=?", (new_val, uid))
    return jsonify({'ok': True, 'is_banned': bool(new_val)})


@app.route('/api/admin/users/<int:uid>', methods=['DELETE'])
@admin_required
def admin_delete_user(uid):
    if uid == session['user_id']:
        return jsonify({'error': 'Cannot delete yourself'}), 400
    with get_db() as db:
        u = db.execute("SELECT user_type FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            return jsonify({'error': 'Not found'}), 404
        if u['user_type'] == 'admin':
            return jsonify({'error': 'Cannot delete another admin'}), 403
        db.execute("DELETE FROM users WHERE id=?", (uid,))
    return jsonify({'ok': True})

# ── Boot ─────────────────────────────────────────────────────
init_db()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
