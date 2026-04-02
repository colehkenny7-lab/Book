import os
import sqlite3
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (Flask, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production-please")

DATABASE     = os.path.join(os.path.dirname(__file__), "betting.db")
DATABASE_URL = os.environ.get("DATABASE_URL")  # set by Render automatically
STARTING_BALANCE = 0.0


# ---------------------------------------------------------------------------
# Database – dual SQLite / PostgreSQL support
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        if DATABASE_URL:
            import psycopg2
            import psycopg2.extras
            url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
            g.db      = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
            g.db_type = "postgres"
        else:
            g.db = sqlite3.connect(DATABASE)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA journal_mode=WAL")
            g.db.execute("PRAGMA foreign_keys=ON")
            g.db_type = "sqlite"
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def is_postgres():
    get_db()
    return g.db_type == "postgres"


def qmark(sql):
    """Convert ? placeholders to %s for PostgreSQL."""
    return sql.replace("?", "%s") if is_postgres() else sql


def db_exec(sql, params=()):
    db  = get_db()
    sql = qmark(sql)
    if is_postgres():
        cur = db.cursor()
        cur.execute(sql, params)
        return cur
    return db.execute(sql, params)


def db_one(sql, params=()):
    return db_exec(sql, params).fetchone()


def db_all(sql, params=()):
    return db_exec(sql, params).fetchall()


def db_commit():
    get_db().commit()


# ---------------------------------------------------------------------------
# DB initialisation
# ---------------------------------------------------------------------------

def init_db():
    if is_postgres():
        _init_postgres()
    else:
        _init_sqlite()
    _seed_defaults()


def _init_sqlite():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT    NOT NULL UNIQUE,
            password   TEXT    NOT NULL,
            balance    REAL    NOT NULL DEFAULT 0.0,
            is_admin   INTEGER NOT NULL DEFAULT 0,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS games (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            title         TEXT NOT NULL,
            team1         TEXT NOT NULL,
            team2         TEXT NOT NULL,
            odds1         REAL NOT NULL DEFAULT 2.0,
            odds2         REAL NOT NULL DEFAULT 2.0,
            odds_draw     REAL,
            spread1       REAL,
            spread2       REAL,
            spread_odds1  REAL DEFAULT 1.91,
            spread_odds2  REAL DEFAULT 1.91,
            min_bet       REAL DEFAULT 1.0,
            max_bet       REAL,
            game_time     TEXT    NOT NULL,
            status        TEXT    NOT NULL DEFAULT 'open',
            winner        TEXT,
            spread_result TEXT,
            is_prop       INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS bets (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            game_id    INTEGER NOT NULL REFERENCES games(id),
            pick       TEXT    NOT NULL,
            bet_type   TEXT    NOT NULL DEFAULT 'moneyline',
            amount     REAL    NOT NULL,
            odds       REAL    NOT NULL,
            payout     REAL,
            status     TEXT    NOT NULL DEFAULT 'pending',
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS site_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS parlays (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL REFERENCES users(id),
            amount        REAL    NOT NULL,
            combined_odds REAL    NOT NULL,
            status        TEXT    NOT NULL DEFAULT 'pending',
            payout        REAL,
            created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS parlay_legs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            parlay_id  INTEGER NOT NULL REFERENCES parlays(id),
            game_id    INTEGER NOT NULL REFERENCES games(id),
            pick       TEXT    NOT NULL,
            bet_type   TEXT    NOT NULL DEFAULT 'moneyline',
            odds       REAL    NOT NULL,
            status     TEXT    NOT NULL DEFAULT 'pending'
        );
    """)
    db.commit()
    # Migrate older SQLite DBs
    for table, col, defn in [
        ("games", "spread1",      "REAL"),
        ("games", "spread2",      "REAL"),
        ("games", "spread_odds1", "REAL DEFAULT 1.91"),
        ("games", "spread_odds2", "REAL DEFAULT 1.91"),
        ("games", "min_bet",      "REAL DEFAULT 1.0"),
        ("games", "max_bet",      "REAL"),
        ("games", "spread_result","TEXT"),
        ("games", "is_prop",      "INTEGER NOT NULL DEFAULT 0"),
        ("bets",  "bet_type",     "TEXT DEFAULT 'moneyline'"),
    ]:
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
            db.commit()
        except Exception:
            pass


def _init_postgres():
    db_exec("""
        CREATE TABLE IF NOT EXISTS users (
            id         SERIAL PRIMARY KEY,
            username   TEXT   NOT NULL UNIQUE,
            password   TEXT   NOT NULL,
            balance    FLOAT  NOT NULL DEFAULT 0.0,
            is_admin   BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    db_exec("""
        CREATE TABLE IF NOT EXISTS games (
            id            SERIAL PRIMARY KEY,
            title         TEXT  NOT NULL,
            team1         TEXT  NOT NULL,
            team2         TEXT  NOT NULL,
            odds1         FLOAT NOT NULL DEFAULT 2.0,
            odds2         FLOAT NOT NULL DEFAULT 2.0,
            odds_draw     FLOAT,
            spread1       FLOAT,
            spread2       FLOAT,
            spread_odds1  FLOAT DEFAULT 1.91,
            spread_odds2  FLOAT DEFAULT 1.91,
            min_bet       FLOAT DEFAULT 1.0,
            max_bet       FLOAT,
            game_time     TEXT  NOT NULL,
            status        TEXT  NOT NULL DEFAULT 'open',
            winner        TEXT,
            spread_result TEXT,
            is_prop       INTEGER NOT NULL DEFAULT 0,
            created_at    TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    db_exec("""
        CREATE TABLE IF NOT EXISTS bets (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            game_id    INTEGER NOT NULL REFERENCES games(id),
            pick       TEXT    NOT NULL,
            bet_type   TEXT    NOT NULL DEFAULT 'moneyline',
            amount     FLOAT   NOT NULL,
            odds       FLOAT   NOT NULL,
            payout     FLOAT,
            status     TEXT    NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    db_exec("""
        CREATE TABLE IF NOT EXISTS site_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    db_exec("""
        CREATE TABLE IF NOT EXISTS parlays (
            id            SERIAL PRIMARY KEY,
            user_id       INTEGER NOT NULL REFERENCES users(id),
            amount        FLOAT   NOT NULL,
            combined_odds FLOAT   NOT NULL,
            status        TEXT    NOT NULL DEFAULT 'pending',
            payout        FLOAT,
            created_at    TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    db_exec("""
        CREATE TABLE IF NOT EXISTS parlay_legs (
            id        SERIAL PRIMARY KEY,
            parlay_id INTEGER NOT NULL REFERENCES parlays(id),
            game_id   INTEGER NOT NULL REFERENCES games(id),
            pick      TEXT    NOT NULL,
            bet_type  TEXT    NOT NULL DEFAULT 'moneyline',
            odds      FLOAT   NOT NULL,
            status    TEXT    NOT NULL DEFAULT 'pending'
        )
    """)
    # Safe column migrations for existing Postgres DBs
    for table, col, defn in [
        ("games", "spread1",      "FLOAT"),
        ("games", "spread2",      "FLOAT"),
        ("games", "spread_odds1", "FLOAT DEFAULT 1.91"),
        ("games", "spread_odds2", "FLOAT DEFAULT 1.91"),
        ("games", "min_bet",      "FLOAT DEFAULT 1.0"),
        ("games", "max_bet",      "FLOAT"),
        ("games", "spread_result","TEXT"),
        ("games", "is_prop",      "INTEGER NOT NULL DEFAULT 0"),
        ("bets",  "bet_type",     "TEXT DEFAULT 'moneyline'"),
    ]:
        try:
            db_exec(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {defn}")
        except Exception:
            pass
    db_commit()


def _seed_defaults():
    defaults = [("max_exposure", None), ("registration_open", "1")]
    for key, val in defaults:
        if is_postgres():
            db_exec("INSERT INTO site_settings (key, value) VALUES (?, ?) ON CONFLICT DO NOTHING",
                    (key, val))
        else:
            db_exec("INSERT OR IGNORE INTO site_settings (key, value) VALUES (?, ?)",
                    (key, val))
    db_commit()

    if not db_one("SELECT id FROM users WHERE is_admin = ?", (True if is_postgres() else 1,)):
        db_exec(
            "INSERT INTO users (username, password, balance, is_admin) VALUES (?,?,?,?)",
            ("admin", generate_password_hash("admin123"), STARTING_BALANCE,
             True if is_postgres() else 1),
        )
        db_commit()


def get_setting(key):
    row = db_one("SELECT value FROM site_settings WHERE key=?", (key,))
    return row["value"] if row else None


def get_current_exposure():
    row = db_one(
        "SELECT COALESCE(SUM(amount * (odds - 1)), 0) AS exp FROM bets WHERE status='pending'"
    )
    return float(row["exp"])


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        user = db_one("SELECT is_admin FROM users WHERE id=?", (session["user_id"],))
        if not user or not user["is_admin"]:
            flash("Admin access required.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


def current_user():
    if "user_id" not in session:
        return None
    return db_one("SELECT * FROM users WHERE id=?", (session["user_id"],))


@app.before_request
def auto_close_expired_games():
    if request.endpoint in ("static", None):
        return
    try:
        now_est = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%dT%H:%M")
        db_exec(
            "UPDATE games SET status='closed' WHERE status='open' AND game_time <= ?",
            (now_est,),
        )
        db_commit()
    except Exception:
        pass  # never break a page load over this


@app.context_processor
def inject_user():
    return dict(current_user=current_user(), now=datetime.utcnow())


@app.template_filter("datefmt")
def datefmt(val, fmt="%Y-%m-%d"):
    """Format a date that may be a datetime object (Postgres) or string (SQLite)."""
    if val is None:
        return ""
    if isinstance(val, str):
        # SQLite returns strings like "2024-01-15 12:30:00"
        val = val.replace("T", " ")[:19]
        try:
            val = datetime.strptime(val, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return val[:10]
    return val.strftime(fmt)


# ---------------------------------------------------------------------------
# Routes – Auth
# ---------------------------------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if get_setting("registration_open") != "1":
        flash("Registration is currently closed. Ask the admin for access.", "warning")
        return redirect(url_for("login"))

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        confirm  = request.form["confirm"]

        if not username or not password:
            flash("Username and password are required.", "danger")
            return render_template("register.html")
        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("register.html")
        if db_one("SELECT id FROM users WHERE username=?", (username,)):
            flash("Username already taken.", "danger")
            return render_template("register.html")

        db_exec(
            "INSERT INTO users (username, password, balance) VALUES (?,?,?)",
            (username, generate_password_hash(password), STARTING_BALANCE),
        )
        db_commit()
        flash(f"Welcome, {username}! Your balance starts at $0 — ask the admin to load you up.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = db_one("SELECT * FROM users WHERE username=?", (username,))
        if not user or not check_password_hash(user["password"], password):
            flash("Invalid username or password.", "danger")
            return render_template("login.html")
        session.clear()
        session["user_id"]  = user["id"]
        session["username"] = user["username"]
        session["is_admin"] = bool(user["is_admin"])
        flash(f"Welcome back, {username}!", "success")
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current  = request.form.get("current_password", "")
        new_pw   = request.form.get("new_password", "")
        confirm  = request.form.get("confirm_password", "")
        user     = current_user()

        if not check_password_hash(user["password"], current):
            flash("Current password is incorrect.", "danger")
            return render_template("change_password.html")
        if len(new_pw) < 6:
            flash("New password must be at least 6 characters.", "danger")
            return render_template("change_password.html")
        if new_pw != confirm:
            flash("New passwords do not match.", "danger")
            return render_template("change_password.html")

        db_exec("UPDATE users SET password=? WHERE id=?",
                (generate_password_hash(new_pw), user["id"]))
        db_commit()
        flash("Password changed successfully.", "success")
        return redirect(url_for("index"))

    return render_template("change_password.html")


# ---------------------------------------------------------------------------
# Routes – Public
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    games = db_all("""
        SELECT g.*, COUNT(b.id) AS bet_count
        FROM games g
        LEFT JOIN bets b ON b.game_id = g.id
        WHERE g.status = 'open'
        GROUP BY g.id
        ORDER BY g.game_time ASC
    """)
    activity = db_all("""
        SELECT b.amount, b.pick, b.bet_type, b.created_at,
               u.username, g.title, g.team1, g.team2
        FROM bets b
        JOIN users u ON u.id = b.user_id
        JOIN games g ON g.id = b.game_id
        ORDER BY b.created_at DESC
        LIMIT 15
    """)
    return render_template("index.html", games=games, activity=activity)


@app.route("/leaderboard")
def leaderboard():
    users = db_all("""
        SELECT u.username, u.balance,
               COUNT(b.id)                                              AS total_bets,
               SUM(CASE WHEN b.status='won'  THEN 1 ELSE 0 END)        AS wins,
               SUM(CASE WHEN b.status='lost' THEN 1 ELSE 0 END)        AS losses,
               COALESCE(SUM(CASE WHEN b.status='won' THEN b.payout ELSE 0 END), 0) AS total_won
        FROM users u
        LEFT JOIN bets b ON b.user_id = u.id
        WHERE NOT u.is_admin
        GROUP BY u.id, u.username, u.balance
        ORDER BY u.balance DESC
    """)
    return render_template("leaderboard.html", users=users)


# ---------------------------------------------------------------------------
# Routes – Betting
# ---------------------------------------------------------------------------

@app.route("/games")
@login_required
def games():
    def with_counts(status_clause, order="ASC", limit=None):
        lim = f"LIMIT {limit}" if limit else ""
        return db_all(f"""
            SELECT g.*, COUNT(b.id) AS bet_count
            FROM games g
            LEFT JOIN bets b ON b.game_id = g.id
            WHERE {status_clause}
            GROUP BY g.id
            ORDER BY g.game_time {order}
            {lim}
        """)
    open_games    = with_counts("g.status='open'",    order="ASC")
    closed_games  = with_counts("g.status='closed'",  order="DESC")
    settled_games = with_counts("g.status='settled'", order="DESC", limit=20)
    return render_template("games.html", open_games=open_games,
                           closed_games=closed_games, settled_games=settled_games)


@app.route("/bet/<int:game_id>", methods=["GET", "POST"])
@login_required
def place_bet(game_id):
    game = db_one("SELECT * FROM games WHERE id=?", (game_id,))
    if not game:
        flash("Game not found.", "danger")
        return redirect(url_for("games"))
    if game["status"] != "open":
        flash("Betting for this game is closed.", "warning")
        return redirect(url_for("games"))

    user = current_user()

    if request.method == "POST":
        pick     = request.form.get("pick")
        bet_type = request.form.get("bet_type", "moneyline")
        amount_s = request.form.get("amount", "").strip()

        valid_ml = {game["team1"], game["team2"]}
        if game["odds_draw"]:
            valid_ml.add("Draw")
        valid_sp = {game["team1"], game["team2"]} if game["spread1"] is not None else set()

        if bet_type == "spread" and pick not in valid_sp:
            flash("Invalid spread pick.", "danger")
            return render_template("bet.html", game=game, user=user)
        if bet_type == "moneyline" and pick not in valid_ml:
            flash("Invalid pick.", "danger")
            return render_template("bet.html", game=game, user=user)

        try:
            amount = float(amount_s)
        except ValueError:
            flash("Enter a valid amount.", "danger")
            return render_template("bet.html", game=game, user=user)

        min_bet = game["min_bet"] or 1.0
        max_bet = game["max_bet"]
        if amount < min_bet:
            flash(f"Minimum bet for this game is ${min_bet:,.2f}.", "danger")
            return render_template("bet.html", game=game, user=user)
        if max_bet and amount > max_bet:
            flash(f"Maximum bet for this game is ${max_bet:,.2f}.", "danger")
            return render_template("bet.html", game=game, user=user)
        if amount > user["balance"]:
            flash("Insufficient balance.", "danger")
            return render_template("bet.html", game=game, user=user)

        if bet_type == "spread":
            odds = game["spread_odds1"] if pick == game["team1"] else game["spread_odds2"]
        elif pick == game["team1"]:
            odds = game["odds1"]
        elif pick == game["team2"]:
            odds = game["odds2"]
        else:
            odds = game["odds_draw"]

        max_exp_s = get_setting("max_exposure")
        if max_exp_s:
            max_exp     = float(max_exp_s)
            current_exp = get_current_exposure()
            new_profit  = amount * (odds - 1)
            if current_exp + new_profit > max_exp:
                remaining   = max(0, max_exp - current_exp)
                max_allowed = remaining / (odds - 1) if odds > 1 else 0
                flash(f"Site exposure limit reached. Max allowed bet: ${max_allowed:,.2f}.", "danger")
                return render_template("bet.html", game=game, user=user)

        db_exec("UPDATE users SET balance = balance - ? WHERE id=?", (amount, user["id"]))
        db_exec(
            "INSERT INTO bets (user_id, game_id, pick, bet_type, amount, odds) VALUES (?,?,?,?,?,?)",
            (user["id"], game_id, pick, bet_type, amount, odds),
        )
        db_commit()

        potential = round(amount * odds, 2)
        label = f"{pick} {'(spread)' if bet_type == 'spread' else ''}"
        flash(f"Bet placed! ${amount:,.2f} on {label} @ {odds}x  |  Potential payout: ${potential:,.2f}", "success")
        return redirect(url_for("my_bets"))

    return render_template("bet.html", game=game, user=user)


@app.route("/my-bets")
@login_required
def my_bets():
    user = current_user()
    bets = db_all("""
        SELECT b.*, g.title, g.team1, g.team2, g.status AS game_status, g.winner, g.spread_result
        FROM bets b
        JOIN games g ON g.id = b.game_id
        WHERE b.user_id = ?
        ORDER BY b.created_at DESC
    """, (user["id"],))
    parlays = db_all(
        "SELECT * FROM parlays WHERE user_id=? ORDER BY created_at DESC", (user["id"],)
    )
    parlay_legs_map = {}
    for p in parlays:
        parlay_legs_map[p["id"]] = db_all("""
            SELECT pl.*, g.title, g.team1, g.team2
            FROM parlay_legs pl
            JOIN games g ON g.id = pl.game_id
            WHERE pl.parlay_id = ?
        """, (p["id"],))
    return render_template("my_bets.html", bets=bets, parlays=parlays,
                           parlay_legs_map=parlay_legs_map, user=user)


# ---------------------------------------------------------------------------
# Routes – Admin
# ---------------------------------------------------------------------------

@app.route("/admin")
@admin_required
def admin():
    games        = db_all("SELECT * FROM games ORDER BY created_at DESC")
    users        = db_all("SELECT id, username, balance, is_admin, created_at FROM users ORDER BY created_at DESC")
    exposure          = get_current_exposure()
    max_exposure      = get_setting("max_exposure")
    registration_open = get_setting("registration_open") == "1"
    return render_template("admin.html", games=games, users=users,
                           exposure=exposure, max_exposure=max_exposure,
                           registration_open=registration_open)


@app.route("/admin/settings", methods=["POST"])
@admin_required
def admin_settings():
    max_exp = request.form.get("max_exposure", "").strip()
    if max_exp == "" or max_exp.lower() == "none":
        db_exec("UPDATE site_settings SET value=NULL WHERE key='max_exposure'")
        flash("Exposure limit removed.", "info")
    else:
        try:
            val = float(max_exp)
            if val < 0:
                raise ValueError
            db_exec("UPDATE site_settings SET value=? WHERE key='max_exposure'", (str(val),))
            flash(f"Exposure limit set to ${val:,.2f}.", "success")
        except ValueError:
            flash("Invalid exposure limit.", "danger")
    db_commit()
    return redirect(url_for("admin"))


@app.route("/admin/reset", methods=["POST"])
@admin_required
def admin_reset():
    action = request.form.get("action")

    if action == "clear_finished":
        # Delete bets on settled/closed games, then delete those games
        db_exec("""
            DELETE FROM bets WHERE game_id IN (
                SELECT id FROM games WHERE status IN ('settled', 'closed')
            )
        """)
        db_exec("DELETE FROM games WHERE status IN ('settled', 'closed')")
        db_commit()
        flash("All finished games and their bets have been removed.", "success")

    elif action == "reset_balances":
        db_exec("UPDATE users SET balance = 0 WHERE NOT is_admin")
        db_commit()
        flash("All player balances reset to $0.", "success")

    elif action == "full_reset":
        db_exec("""
            DELETE FROM bets WHERE game_id IN (
                SELECT id FROM games WHERE status IN ('settled', 'closed')
            )
        """)
        db_exec("DELETE FROM games WHERE status IN ('settled', 'closed')")
        db_exec("DELETE FROM bets")
        db_exec("UPDATE users SET balance = 0 WHERE NOT is_admin")
        db_commit()
        flash("All finished games cleared and all balances reset to $0.", "success")

    else:
        flash("Unknown action.", "danger")

    return redirect(url_for("admin"))


@app.route("/admin/toggle-registration", methods=["POST"])
@admin_required
def admin_toggle_registration():
    current = get_setting("registration_open")
    new_val = "0" if current == "1" else "1"
    db_exec("UPDATE site_settings SET value=? WHERE key='registration_open'", (new_val,))
    db_commit()
    state = "opened" if new_val == "1" else "locked"
    flash(f"Registration {state}.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/game/<int:game_id>/bets")
@admin_required
def admin_game_bets(game_id):
    game = db_one("SELECT * FROM games WHERE id=?", (game_id,))
    if not game:
        flash("Game not found.", "danger")
        return redirect(url_for("admin"))
    bets = db_all("""
        SELECT b.*, u.username
        FROM bets b
        JOIN users u ON u.id = b.user_id
        WHERE b.game_id = ?
        ORDER BY b.created_at DESC
    """, (game_id,))
    total_wagered  = sum(b["amount"] for b in bets)
    total_exposure = sum(b["amount"] * (b["odds"] - 1) for b in bets if b["status"] == "pending")
    return render_template("admin_game_bets.html", game=game, bets=bets,
                           total_wagered=total_wagered, total_exposure=total_exposure)


@app.route("/admin/game/new", methods=["GET", "POST"])
@admin_required
def admin_new_game():
    if request.method == "POST":
        data, error = _parse_game_form(request.form)
        if error:
            flash(error, "danger")
            return render_template("admin_game_form.html", game=None)
        db_exec("""
            INSERT INTO games
              (title, team1, team2, odds1, odds2, odds_draw,
               spread1, spread2, spread_odds1, spread_odds2,
               min_bet, max_bet, game_time, is_prop)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, data)
        db_commit()
        flash(f"Game '{data[0]}' created!", "success")
        return redirect(url_for("admin"))
    return render_template("admin_game_form.html", game=None)


@app.route("/admin/game/<int:game_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_game(game_id):
    game = db_one("SELECT * FROM games WHERE id=?", (game_id,))
    if not game:
        flash("Game not found.", "danger")
        return redirect(url_for("admin"))

    if request.method == "POST":
        data, error = _parse_game_form(request.form)
        if error:
            flash(error, "danger")
            return render_template("admin_game_form.html", game=game)
        status = request.form.get("status", game["status"])
        db_exec("""
            UPDATE games SET
              title=?, team1=?, team2=?, odds1=?, odds2=?, odds_draw=?,
              spread1=?, spread2=?, spread_odds1=?, spread_odds2=?,
              min_bet=?, max_bet=?, game_time=?, is_prop=?, status=?
            WHERE id=?
        """, (*data, status, game_id))
        db_commit()
        flash("Game updated.", "success")
        return redirect(url_for("admin"))

    return render_template("admin_game_form.html", game=game)


def _parse_game_form(form):
    title     = form.get("title", "").strip()
    team1     = form.get("team1", "").strip()
    team2     = form.get("team2", "").strip()
    game_time = form.get("game_time", "").strip()

    if not all([title, team1, team2, game_time]):
        return None, "Title, teams, and game time are required."

    try:
        odds1 = float(form["odds1"])
        odds2 = float(form["odds2"])
        if odds1 < 1 or odds2 < 1:
            raise ValueError
    except (KeyError, ValueError):
        return None, "Moneyline odds must be numbers ≥ 1.0."

    odds_draw = None
    if form.get("odds_draw", "").strip():
        try:
            odds_draw = float(form["odds_draw"])
            if odds_draw < 1:
                raise ValueError
        except ValueError:
            return None, "Draw odds must be a number ≥ 1.0."

    spread1 = spread2 = spread_odds1 = spread_odds2 = None
    if form.get("spread1", "").strip():
        try:
            spread1      = float(form["spread1"])
            spread2      = -spread1
            spread_odds1 = float(form.get("spread_odds1") or 1.91)
            spread_odds2 = float(form.get("spread_odds2") or 1.91)
            if spread_odds1 < 1 or spread_odds2 < 1:
                raise ValueError
        except ValueError:
            return None, "Spread odds must be numbers ≥ 1.0."

    min_bet = 1.0
    if form.get("min_bet", "").strip():
        try:
            min_bet = float(form["min_bet"])
        except ValueError:
            return None, "Min bet must be a number."

    max_bet = None
    if form.get("max_bet", "").strip():
        try:
            max_bet = float(form["max_bet"])
        except ValueError:
            return None, "Max bet must be a number."

    is_prop = 1 if form.get("is_prop") else 0

    return (title, team1, team2, odds1, odds2, odds_draw,
            spread1, spread2, spread_odds1, spread_odds2,
            min_bet, max_bet, game_time, is_prop), None


@app.route("/admin/game/<int:game_id>/settle", methods=["POST"])
@admin_required
def admin_settle_game(game_id):
    game = db_one("SELECT * FROM games WHERE id=?", (game_id,))
    if not game or game["status"] == "settled":
        flash("Game not found or already settled.", "warning")
        return redirect(url_for("admin"))

    winner        = request.form.get("winner")
    spread_result = request.form.get("spread_result")

    valid_ml = {game["team1"], game["team2"]}
    if game["odds_draw"]:
        valid_ml.add("Draw")
    if winner not in valid_ml:
        flash("Invalid winner selection.", "danger")
        return redirect(url_for("admin"))

    bets    = db_all("SELECT * FROM bets WHERE game_id=? AND status='pending'", (game_id,))
    settled = 0

    for bet in bets:
        if bet["bet_type"] == "spread":
            if not spread_result:
                continue
            if spread_result == "push":
                db_exec("UPDATE bets SET status='push', payout=? WHERE id=?",
                        (bet["amount"], bet["id"]))
                db_exec("UPDATE users SET balance = balance + ? WHERE id=?",
                        (bet["amount"], bet["user_id"]))
            elif bet["pick"] == spread_result:
                payout = round(bet["amount"] * bet["odds"], 2)
                db_exec("UPDATE bets SET status='won', payout=? WHERE id=?", (payout, bet["id"]))
                db_exec("UPDATE users SET balance = balance + ? WHERE id=?", (payout, bet["user_id"]))
            else:
                db_exec("UPDATE bets SET status='lost', payout=0 WHERE id=?", (bet["id"],))
        else:
            if bet["pick"] == winner:
                payout = round(bet["amount"] * bet["odds"], 2)
                db_exec("UPDATE bets SET status='won', payout=? WHERE id=?", (payout, bet["id"]))
                db_exec("UPDATE users SET balance = balance + ? WHERE id=?", (payout, bet["user_id"]))
            else:
                db_exec("UPDATE bets SET status='lost', payout=0 WHERE id=?", (bet["id"],))
        settled += 1

    db_exec("UPDATE games SET status='settled', winner=?, spread_result=? WHERE id=?",
            (winner, spread_result, game_id))
    db_commit()

    # Settle any parlay legs tied to this game
    _settle_parlay_legs(game_id, winner, spread_result)

    flash(f"Game settled! Winner: {winner}. {settled} bet(s) processed.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/game/<int:game_id>/close", methods=["POST"])
@admin_required
def admin_close_game(game_id):
    db_exec("UPDATE games SET status='closed' WHERE id=?", (game_id,))
    db_commit()
    flash("Betting closed for this game.", "info")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/adjust-balance", methods=["POST"])
@admin_required
def admin_adjust_balance(user_id):
    try:
        amount = float(request.form.get("amount", ""))
    except ValueError:
        flash("Invalid amount.", "danger")
        return redirect(url_for("admin"))
    user        = db_one("SELECT username, balance FROM users WHERE id=?", (user_id,))
    new_balance = max(0, user["balance"] + amount)
    db_exec("UPDATE users SET balance = ? WHERE id=?", (new_balance, user_id))
    db_commit()
    word = "Added" if amount >= 0 else "Removed"
    flash(f"{word} ${abs(amount):,.2f} {'to' if amount >= 0 else 'from'} {user['username']}. Balance: ${new_balance:,.2f}", "success")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/set-balance", methods=["POST"])
@admin_required
def admin_set_balance(user_id):
    try:
        amount = float(request.form.get("amount", ""))
        if amount < 0:
            raise ValueError
    except ValueError:
        flash("Invalid amount.", "danger")
        return redirect(url_for("admin"))
    user = db_one("SELECT username FROM users WHERE id=?", (user_id,))
    db_exec("UPDATE users SET balance = ? WHERE id=?", (amount, user_id))
    db_commit()
    flash(f"Set {user['username']}'s balance to ${amount:,.2f}.", "success")
    return redirect(url_for("admin"))


# ---------------------------------------------------------------------------
# Parlay helpers
# ---------------------------------------------------------------------------

def _settle_parlay_legs(game_id, winner, spread_result):
    """Called after a game is settled — resolves any parlay legs on this game."""
    legs = db_all(
        "SELECT * FROM parlay_legs WHERE game_id=? AND status='pending'", (game_id,)
    )
    if not legs:
        return

    for leg in legs:
        if leg["bet_type"] == "spread":
            if not spread_result:
                continue
            if spread_result == "push":
                db_exec("UPDATE parlay_legs SET status='push' WHERE id=?", (leg["id"],))
            elif leg["pick"] == spread_result:
                db_exec("UPDATE parlay_legs SET status='won' WHERE id=?", (leg["id"],))
            else:
                db_exec("UPDATE parlay_legs SET status='lost' WHERE id=?", (leg["id"],))
                db_exec("UPDATE parlays SET status='lost', payout=0 WHERE id=?", (leg["parlay_id"],))
        else:
            if leg["pick"] == winner:
                db_exec("UPDATE parlay_legs SET status='won' WHERE id=?", (leg["id"],))
            else:
                db_exec("UPDATE parlay_legs SET status='lost' WHERE id=?", (leg["id"],))
                db_exec("UPDATE parlays SET status='lost', payout=0 WHERE id=?", (leg["parlay_id"],))
    db_commit()

    # Check each affected parlay — pay out if all legs settled and none lost
    parlay_ids = list({leg["parlay_id"] for leg in legs})
    for pid in parlay_ids:
        parlay = db_one("SELECT * FROM parlays WHERE id=? AND status='pending'", (pid,))
        if not parlay:
            continue
        all_legs = db_all("SELECT * FROM parlay_legs WHERE parlay_id=?", (pid,))
        statuses  = [l["status"] for l in all_legs]
        if any(s == "pending" for s in statuses):
            continue  # still waiting on other games
        if any(s == "lost" for s in statuses):
            continue  # already marked lost above
        # All legs won or pushed — recalculate without pushed legs
        active = [l for l in all_legs if l["status"] == "won"]
        if not active:
            payout = parlay["amount"]  # all pushed → full refund
            db_exec("UPDATE parlays SET status='push', payout=? WHERE id=?", (payout, pid))
        else:
            combined = round(min(100.0, sum(1 for _ in active) and
                                 __import__("math").prod(l["odds"] for l in active)), 4)
            payout = round(parlay["amount"] * combined, 2)
            db_exec("UPDATE parlays SET status='won', payout=?, combined_odds=? WHERE id=?",
                    (payout, combined, pid))
        db_exec("UPDATE users SET balance = balance + ? WHERE id=?",
                (payout, parlay["user_id"]))
    db_commit()


# ---------------------------------------------------------------------------
# Routes – Parlay
# ---------------------------------------------------------------------------

MAX_PARLAY_LEGS = 5
MAX_PARLAY_ODDS = 100.0


@app.route("/parlay", methods=["GET", "POST"])
@login_required
def parlay():
    user       = current_user()
    open_games = db_all("SELECT * FROM games WHERE status='open' ORDER BY game_time ASC")

    if request.method == "POST":
        import json
        legs_raw = request.form.get("legs_json", "")
        amount_s = request.form.get("amount", "").strip()

        try:
            legs = json.loads(legs_raw)
        except (ValueError, TypeError):
            flash("Invalid parlay data.", "danger")
            return render_template("parlay.html", games=open_games, user=user)

        if len(legs) < 2:
            flash("A parlay needs at least 2 legs.", "danger")
            return render_template("parlay.html", games=open_games, user=user)
        if len(legs) > MAX_PARLAY_LEGS:
            flash(f"Maximum {MAX_PARLAY_LEGS} legs per parlay.", "danger")
            return render_template("parlay.html", games=open_games, user=user)

        # Validate — one pick per game, all games still open
        seen_games = set()
        validated  = []
        for leg in legs:
            gid  = int(leg.get("game_id", 0))
            pick = leg.get("pick", "")
            btype = leg.get("bet_type", "moneyline")
            if gid in seen_games:
                flash("Only one pick per game in a parlay.", "danger")
                return render_template("parlay.html", games=open_games, user=user)
            seen_games.add(gid)
            game = db_one("SELECT * FROM games WHERE id=? AND status='open'", (gid,))
            if not game:
                flash("One of the selected games is no longer open.", "warning")
                return render_template("parlay.html", games=open_games, user=user)
            # Determine odds
            if btype == "spread" and game["spread1"] is not None:
                odds = game["spread_odds1"] if pick == game["team1"] else game["spread_odds2"]
            elif pick == game["team1"]:
                odds = game["odds1"]
            elif pick == game["team2"]:
                odds = game["odds2"]
            elif pick == "Draw" and game["odds_draw"]:
                odds = game["odds_draw"]
            else:
                flash("Invalid pick detected.", "danger")
                return render_template("parlay.html", games=open_games, user=user)
            validated.append({"game_id": gid, "pick": pick, "bet_type": btype, "odds": odds})

        import math
        combined_odds = round(math.prod(l["odds"] for l in validated), 4)
        if combined_odds > MAX_PARLAY_ODDS:
            flash(f"Combined odds {combined_odds:.2f}x exceed the {MAX_PARLAY_ODDS}x limit.", "danger")
            return render_template("parlay.html", games=open_games, user=user)

        try:
            amount = float(amount_s)
        except ValueError:
            flash("Enter a valid bet amount.", "danger")
            return render_template("parlay.html", games=open_games, user=user)

        if amount < 1:
            flash("Minimum parlay bet is $1.", "danger")
            return render_template("parlay.html", games=open_games, user=user)
        if amount > user["balance"]:
            flash("Insufficient balance.", "danger")
            return render_template("parlay.html", games=open_games, user=user)

        # Deduct balance and save parlay
        db_exec("UPDATE users SET balance = balance - ? WHERE id=?", (amount, user["id"]))
        if is_postgres():
            parlay_id = db_one(
                "INSERT INTO parlays (user_id, amount, combined_odds) VALUES (?,?,?) RETURNING id",
                (user["id"], amount, combined_odds),
            )["id"]
        else:
            cur = db_exec(
                "INSERT INTO parlays (user_id, amount, combined_odds) VALUES (?,?,?)",
                (user["id"], amount, combined_odds),
            )
            parlay_id = cur.lastrowid

        for leg in validated:
            db_exec(
                "INSERT INTO parlay_legs (parlay_id, game_id, pick, bet_type, odds) VALUES (?,?,?,?,?)",
                (parlay_id, leg["game_id"], leg["pick"], leg["bet_type"], leg["odds"]),
            )
        db_commit()

        potential = round(amount * combined_odds, 2)
        flash(
            f"Parlay placed! {len(validated)} legs @ {combined_odds}x  |  "
            f"Bet: ${amount:,.2f}  |  Potential payout: ${potential:,.2f}",
            "success",
        )
        return redirect(url_for("my_bets"))

    return render_template("parlay.html", games=open_games, user=user)


# ---------------------------------------------------------------------------
# Routes – Analytics
# ---------------------------------------------------------------------------

@app.route("/analytics")
@login_required
def analytics():
    site = db_one("""
        SELECT
            COUNT(*)                                                          AS total_bets,
            COALESCE(SUM(amount), 0)                                          AS total_wagered,
            COALESCE(SUM(CASE WHEN status='won'  THEN payout  ELSE 0 END), 0) AS total_paid,
            COALESCE(SUM(CASE WHEN status='won'  THEN 1       ELSE 0 END), 0) AS wins,
            COALESCE(SUM(CASE WHEN status='lost' THEN 1       ELSE 0 END), 0) AS losses
        FROM bets WHERE status != 'pending'
    """)
    parlay_site = db_one("""
        SELECT
            COUNT(*)                                                          AS total_parlays,
            COALESCE(SUM(amount), 0)                                          AS total_wagered,
            COALESCE(SUM(CASE WHEN status='won'  THEN payout  ELSE 0 END), 0) AS total_paid,
            COALESCE(SUM(CASE WHEN status='won'  THEN 1       ELSE 0 END), 0) AS wins
        FROM parlays WHERE status != 'pending'
    """)
    players = db_all("""
        SELECT
            u.username,
            u.balance,
            COUNT(b.id)                                                            AS bets,
            COALESCE(SUM(b.amount), 0)                                             AS wagered,
            COALESCE(SUM(CASE WHEN b.status='won'  THEN b.payout ELSE 0 END), 0)  AS returned,
            COALESCE(SUM(CASE WHEN b.status='won'  THEN 1        ELSE 0 END), 0)  AS wins,
            COALESCE(SUM(CASE WHEN b.status='lost' THEN 1        ELSE 0 END), 0)  AS losses
        FROM users u
        LEFT JOIN bets b ON b.user_id = u.id AND b.status != 'pending'
        WHERE NOT u.is_admin
        GROUP BY u.id, u.username, u.balance
        ORDER BY wagered DESC
    """)
    top_games = db_all("""
        SELECT g.title, g.team1, g.team2, g.status,
               COUNT(b.id)               AS bet_count,
               COALESCE(SUM(b.amount),0) AS total_wagered
        FROM games g
        LEFT JOIN bets b ON b.game_id = g.id
        GROUP BY g.id, g.title, g.team1, g.team2, g.status
        ORDER BY total_wagered DESC
        LIMIT 10
    """)
    return render_template("analytics.html", site=site, parlay_site=parlay_site,
                           players=players, top_games=top_games)


# ---------------------------------------------------------------------------
# Routes – Admin (delete game)
# ---------------------------------------------------------------------------

@app.route("/admin/game/<int:game_id>/delete", methods=["POST"])
@admin_required
def admin_delete_game(game_id):
    game = db_one("SELECT * FROM games WHERE id=?", (game_id,))
    if not game:
        flash("Game not found.", "danger")
        return redirect(url_for("admin"))

    # Refund pending straight bets
    pending = db_all(
        "SELECT * FROM bets WHERE game_id=? AND status='pending'", (game_id,)
    )
    for bet in pending:
        db_exec("UPDATE users SET balance = balance + ? WHERE id=?",
                (bet["amount"], bet["user_id"]))

    # Void parlay legs on this game and refund those parlays
    legs = db_all(
        "SELECT * FROM parlay_legs WHERE game_id=? AND status='pending'", (game_id,)
    )
    for leg in legs:
        parlay = db_one(
            "SELECT * FROM parlays WHERE id=? AND status='pending'", (leg["parlay_id"],)
        )
        if parlay:
            db_exec("UPDATE users SET balance = balance + ? WHERE id=?",
                    (parlay["amount"], parlay["user_id"]))
            db_exec("UPDATE parlays SET status='void', payout=? WHERE id=?",
                    (parlay["amount"], parlay["parlay_id"]))

    db_exec("DELETE FROM parlay_legs WHERE game_id=?", (game_id,))
    db_exec("DELETE FROM bets WHERE game_id=?", (game_id,))
    db_exec("DELETE FROM games WHERE id=?", (game_id,))
    db_commit()
    flash(
        f"'{game['title']}' deleted. {len(pending)} bet(s) and {len(legs)} parlay leg(s) refunded.",
        "success",
    )
    return redirect(url_for("admin"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
