from flask import Flask, render_template, request, jsonify
import sqlite3, os
from datetime import datetime, timedelta

app = Flask(__name__)
DB = os.environ.get('DATABASE_URL', os.path.join(os.path.dirname(__file__), 'study_tracker.db'))

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT DEFAULT 'Core',
            status TEXT DEFAULT 'pending',
            time_limit INTEGER DEFAULT 25,
            time_spent INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            type TEXT DEFAULT 'daily',
            target_value REAL DEFAULT 4.0,
            current_value REAL DEFAULT 0.0,
            unit TEXT DEFAULT 'tasks',
            completed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            duration INTEGER DEFAULT 0,
            date TEXT DEFAULT (date('now','localtime')),
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        """)

@app.route('/')
def index():
    return render_template('index.html')

# ── TASKS ──────────────────────────────────────────────────────────
@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    with db() as c:
        rows = c.execute('SELECT * FROM tasks ORDER BY created_at DESC').fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/tasks', methods=['POST'])
def create_task():
    d = request.json or {}
    title = (d.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    with db() as c:
        cur = c.execute(
            'INSERT INTO tasks (title, category, time_limit) VALUES (?,?,?)',
            (title, d.get('category', 'Core'), int(d.get('time_limit', 25)))
        )
        c.commit()
        row = c.execute('SELECT * FROM tasks WHERE id=?', (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201

@app.route('/api/tasks/<int:tid>', methods=['PUT'])
def update_task(tid):
    d = request.json or {}
    sets, vals = [], []
    for f in ('title', 'category', 'status', 'time_spent'):
        if f in d:
            sets.append(f'{f}=?')
            vals.append(d[f])
    if 'status' in d and d['status'] == 'completed':
        sets.append('completed_at=?')
        vals.append(datetime.now().isoformat())
    if not sets:
        return jsonify({'error': 'Nothing to update'}), 400
    vals.append(tid)
    with db() as c:
        c.execute(f'UPDATE tasks SET {",".join(sets)} WHERE id=?', vals)
        c.commit()
        row = c.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
    return jsonify(dict(row))

@app.route('/api/tasks/<int:tid>', methods=['DELETE'])
def delete_task(tid):
    with db() as c:
        c.execute('DELETE FROM tasks WHERE id=?', (tid,))
        c.commit()
    return jsonify({'ok': True})

# ── GOALS ──────────────────────────────────────────────────────────
@app.route('/api/goals', methods=['GET'])
def get_goals():
    with db() as c:
        rows = c.execute('SELECT * FROM goals ORDER BY type, created_at DESC').fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/goals', methods=['POST'])
def create_goal():
    d = request.json or {}
    title = (d.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    with db() as c:
        cur = c.execute(
            'INSERT INTO goals (title, type, target_value, unit) VALUES (?,?,?,?)',
            (title, d.get('type', 'daily'), float(d.get('target_value', 4)), d.get('unit', 'tasks'))
        )
        c.commit()
        row = c.execute('SELECT * FROM goals WHERE id=?', (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201

@app.route('/api/goals/<int:gid>', methods=['PUT'])
def update_goal(gid):
    d = request.json or {}
    sets, vals = [], []
    for f in ('title', 'current_value', 'target_value', 'completed'):
        if f in d:
            sets.append(f'{f}=?')
            vals.append(d[f])
    if not sets:
        return jsonify({'error': 'Nothing to update'}), 400
    vals.append(gid)
    with db() as c:
        c.execute(f'UPDATE goals SET {",".join(sets)} WHERE id=?', vals)
        c.commit()
        row = c.execute('SELECT * FROM goals WHERE id=?', (gid,)).fetchone()
    return jsonify(dict(row))

@app.route('/api/goals/<int:gid>', methods=['DELETE'])
def delete_goal(gid):
    with db() as c:
        c.execute('DELETE FROM goals WHERE id=?', (gid,))
        c.commit()
    return jsonify({'ok': True})

# ── SESSIONS ───────────────────────────────────────────────────────
@app.route('/api/sessions', methods=['POST'])
def log_session():
    d = request.json or {}
    with db() as c:
        c.execute(
            'INSERT INTO sessions (task_id, duration, date) VALUES (?,?,?)',
            (d.get('task_id'), int(d.get('duration', 0)),
             d.get('date', datetime.now().strftime('%Y-%m-%d')))
        )
        c.commit()
    return jsonify({'ok': True})

# ── STATS ──────────────────────────────────────────────────────────
@app.route('/api/stats')
def get_stats():
    with db() as c:
        weekly = []
        for i in range(6, -1, -1):
            day = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            label = (datetime.now() - timedelta(days=i)).strftime('%a')
            row = c.execute('SELECT COALESCE(SUM(duration),0) as m FROM sessions WHERE date=?', (day,)).fetchone()
            weekly.append({'date': day, 'label': label, 'minutes': row['m']})

        cats = c.execute(
            'SELECT category, SUM(time_spent) as total FROM tasks WHERE time_spent>0 GROUP BY category'
        ).fetchall()

        streak = 0
        check = datetime.now()
        while True:
            row = c.execute('SELECT COUNT(*) as n FROM sessions WHERE date=?', (check.strftime('%Y-%m-%d'),)).fetchone()
            if row['n'] > 0:
                streak += 1
                check -= timedelta(days=1)
            else:
                break

        done    = c.execute("SELECT COUNT(*) as n FROM tasks WHERE status='completed'").fetchone()['n']
        pending = c.execute("SELECT COUNT(*) as n FROM tasks WHERE status='pending'").fetchone()['n']
        expired = c.execute("SELECT COUNT(*) as n FROM tasks WHERE status='expired'").fetchone()['n']
        judged  = done + expired
        score   = int(done / judged * 100) if judged > 0 else 0

        week_start = (datetime.now() - timedelta(days=6)).strftime('%Y-%m-%d')
        week_mins  = c.execute('SELECT COALESCE(SUM(duration),0) as m FROM sessions WHERE date>=?', (week_start,)).fetchone()['m']
        today_mins = c.execute('SELECT COALESCE(SUM(duration),0) as m FROM sessions WHERE date=?', (datetime.now().strftime('%Y-%m-%d'),)).fetchone()['m']

    return jsonify({
        'weekly': weekly,
        'categories': [dict(r) for r in cats],
        'streak': streak,
        'productivity_score': score,
        'week_minutes': week_mins,
        'today_minutes': today_mins,
        'tasks_done': done,
        'tasks_pending': pending,
        'tasks_expired': expired,
    })

@app.route('/manifest.json')
def pwa_manifest():
    return app.send_static_file('manifest.json')

@app.route('/sw.js')
def service_worker():
    resp = app.send_static_file('sw.js')
    resp.headers['Content-Type'] = 'application/javascript'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5001)
