import os
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (Flask, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production-please")

DATABASE = os.path.join(os.path.dirname(__file__), "betting.db")
STARTING_BALANCE = 0.0


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT    NOT NULL UNIQUE,
            password    TEXT    NOT NULL,
            balance     REAL    NOT NULL DEFAULT 0.0,
            is_admin    INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS games (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            title         TEXT    NOT NULL,
            team1         TEXT    NOT NULL,
            team2         TEXT    NOT NULL,
            odds1         REAL    NOT NULL DEFAULT 2.0,
            odds2         REAL    NOT NULL DEFAULT 2.0,
            odds_draw     REAL,
            spread1       REAL,
            spread2       REAL,
            spread_odds1  REAL    DEFAULT 1.91,
            spread_odds2  REAL    DEFAULT 1.91,
            min_bet       REAL    DEFAULT 1.0,
            max_bet       REAL,
            game_time     TEXT    NOT NULL,
            status        TEXT    NOT NULL DEFAULT 'open',
            winner        TEXT,
            spread_result TEXT,
            created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS bets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            game_id     INTEGER NOT NULL REFERENCES games(id),
            pick        TEXT    NOT NULL,
            bet_type    TEXT    NOT NULL DEFAULT 'moneyline',
            amount      REAL    NOT NULL,
            odds        REAL    NOT NULL,
            payout      REAL,
            status      TEXT    NOT NULL DEFAULT 'pending',
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS site_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    db.commit()

    # Migrate existing DBs – add columns if they don't exist yet
    migrations = [
        ("games",  "spread1",      "REAL"),
        ("games",  "spread2",      "REAL"),
        ("games",  "spread_odds1", "REAL DEFAULT 1.91"),
        ("games",  "spread_odds2", "REAL DEFAULT 1.91"),
        ("games",  "min_bet",      "REAL DEFAULT 1.0"),
        ("games",  "max_bet",      "REAL"),
        ("games",  "spread_result","TEXT"),
        ("bets",   "bet_type",     "TEXT DEFAULT 'moneyline'"),
    ]
    for table, col, defn in migrations:
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
            db.commit()
        except Exception:
            pass  # column already exists

    # Default site settings
    db.execute("INSERT OR IGNORE INTO site_settings (key, value) VALUES ('max_exposure', NULL)")
    db.commit()

    # Default admin
    admin = db.execute("SELECT id FROM users WHERE is_admin=1").fetchone()
    if not admin:
        db.execute(
            "INSERT INTO users (username, password, balance, is_admin) VALUES (?,?,?,1)",
            ("admin", generate_password_hash("admin123"), STARTING_BALANCE),
        )
        db.commit()


def get_setting(key):
    row = get_db().execute("SELECT value FROM site_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def get_current_exposure():
    """Total potential profit owed to bettors across all pending bets."""
    row = get_db().execute(
        "SELECT COALESCE(SUM(amount * (odds - 1)), 0) AS exp FROM bets WHERE status='pending'"
    ).fetchone()
    return row["exp"]


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
        db = get_db()
        user = db.execute("SELECT is_admin FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if not user or not user["is_admin"]:
            flash("Admin access required.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


def current_user():
    if "user_id" not in session:
        return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()


@app.context_processor
def inject_user():
    return dict(current_user=current_user(), now=datetime.utcnow())


# ---------------------------------------------------------------------------
# Routes – Auth
# ---------------------------------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
def register():
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

        db = get_db()
        if db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
            flash("Username already taken.", "danger")
            return render_template("register.html")

        db.execute(
            "INSERT INTO users (username, password, balance) VALUES (?,?,?)",
            (username, generate_password_hash(password), STARTING_BALANCE),
        )
        db.commit()
        flash(f"Welcome, {username}! Your balance starts at $0 — ask the admin to load you up.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        db   = get_db()
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
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


# ---------------------------------------------------------------------------
# Routes – Public
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    db    = get_db()
    games = db.execute("SELECT * FROM games WHERE status='open' ORDER BY game_time ASC").fetchall()
    return render_template("index.html", games=games)


@app.route("/leaderboard")
def leaderboard():
    db = get_db()
    users = db.execute("""
        SELECT u.username, u.balance,
               COUNT(b.id)                                              AS total_bets,
               SUM(CASE WHEN b.status='won'  THEN 1 ELSE 0 END)        AS wins,
               SUM(CASE WHEN b.status='lost' THEN 1 ELSE 0 END)        AS losses,
               COALESCE(SUM(CASE WHEN b.status='won' THEN b.payout ELSE 0 END), 0) AS total_won
        FROM users u
        LEFT JOIN bets b ON b.user_id = u.id
        WHERE u.is_admin = 0
        GROUP BY u.id
        ORDER BY u.balance DESC
    """).fetchall()
    return render_template("leaderboard.html", users=users)


# ---------------------------------------------------------------------------
# Routes – Betting
# ---------------------------------------------------------------------------

@app.route("/games")
@login_required
def games():
    db = get_db()
    open_games    = db.execute("SELECT * FROM games WHERE status='open'    ORDER BY game_time ASC").fetchall()
    closed_games  = db.execute("SELECT * FROM games WHERE status='closed'  ORDER BY game_time DESC").fetchall()
    settled_games = db.execute("SELECT * FROM games WHERE status='settled' ORDER BY game_time DESC LIMIT 20").fetchall()
    return render_template("games.html",
                           open_games=open_games,
                           closed_games=closed_games,
                           settled_games=settled_games)


@app.route("/bet/<int:game_id>", methods=["GET", "POST"])
@login_required
def place_bet(game_id):
    db   = get_db()
    game = db.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()

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

        # Validate pick
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

        # Parse amount
        try:
            amount = float(amount_s)
        except ValueError:
            flash("Enter a valid amount.", "danger")
            return render_template("bet.html", game=game, user=user)

        # Per-game min/max limits
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

        # Determine odds
        if bet_type == "spread":
            odds = game["spread_odds1"] if pick == game["team1"] else game["spread_odds2"]
        elif pick == game["team1"]:
            odds = game["odds1"]
        elif pick == game["team2"]:
            odds = game["odds2"]
        else:
            odds = game["odds_draw"]

        # Site-wide exposure limit check
        max_exp_s = get_setting("max_exposure")
        if max_exp_s:
            max_exp        = float(max_exp_s)
            current_exp    = get_current_exposure()
            new_bet_profit = amount * (odds - 1)
            if current_exp + new_bet_profit > max_exp:
                remaining = max(0, max_exp - current_exp)
                # Calculate max allowed bet given remaining exposure room
                max_allowed = remaining / (odds - 1) if odds > 1 else 0
                flash(
                    f"Site exposure limit reached. Max allowed bet on this pick: ${max_allowed:,.2f}.",
                    "danger",
                )
                return render_template("bet.html", game=game, user=user)

        # Record bet
        db.execute("UPDATE users SET balance = balance - ? WHERE id=?", (amount, user["id"]))
        db.execute(
            "INSERT INTO bets (user_id, game_id, pick, bet_type, amount, odds) VALUES (?,?,?,?,?,?)",
            (user["id"], game_id, pick, bet_type, amount, odds),
        )
        db.commit()

        potential = round(amount * odds, 2)
        label = f"{pick} {'(spread)' if bet_type == 'spread' else ''}"
        flash(f"Bet placed! ${amount:,.2f} on {label} @ {odds}x  |  Potential payout: ${potential:,.2f}", "success")
        return redirect(url_for("my_bets"))

    return render_template("bet.html", game=game, user=user)


@app.route("/my-bets")
@login_required
def my_bets():
    db   = get_db()
    user = current_user()
    bets = db.execute("""
        SELECT b.*, g.title, g.team1, g.team2, g.status AS game_status, g.winner, g.spread_result
        FROM bets b
        JOIN games g ON g.id = b.game_id
        WHERE b.user_id = ?
        ORDER BY b.created_at DESC
    """, (user["id"],)).fetchall()
    return render_template("my_bets.html", bets=bets, user=user)


# ---------------------------------------------------------------------------
# Routes – Admin
# ---------------------------------------------------------------------------

@app.route("/admin")
@admin_required
def admin():
    db    = get_db()
    games = db.execute("SELECT * FROM games ORDER BY created_at DESC").fetchall()
    users = db.execute("SELECT id, username, balance, is_admin, created_at FROM users ORDER BY created_at DESC").fetchall()
    exposure     = get_current_exposure()
    max_exposure = get_setting("max_exposure")
    return render_template("admin.html", games=games, users=users,
                           exposure=exposure, max_exposure=max_exposure)


@app.route("/admin/settings", methods=["POST"])
@admin_required
def admin_settings():
    max_exp = request.form.get("max_exposure", "").strip()
    db = get_db()
    if max_exp == "" or max_exp.lower() == "none":
        db.execute("UPDATE site_settings SET value=NULL WHERE key='max_exposure'")
        flash("Exposure limit removed.", "info")
    else:
        try:
            val = float(max_exp)
            if val < 0:
                raise ValueError
            db.execute("UPDATE site_settings SET value=? WHERE key='max_exposure'", (str(val),))
            flash(f"Exposure limit set to ${val:,.2f}.", "success")
        except ValueError:
            flash("Invalid exposure limit.", "danger")
    db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/game/<int:game_id>/bets")
@admin_required
def admin_game_bets(game_id):
    db   = get_db()
    game = db.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
    if not game:
        flash("Game not found.", "danger")
        return redirect(url_for("admin"))
    bets = db.execute("""
        SELECT b.*, u.username
        FROM bets b
        JOIN users u ON u.id = b.user_id
        WHERE b.game_id = ?
        ORDER BY b.created_at DESC
    """, (game_id,)).fetchall()

    # Summary stats
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
        db = get_db()
        db.execute("""
            INSERT INTO games
              (title, team1, team2, odds1, odds2, odds_draw,
               spread1, spread2, spread_odds1, spread_odds2,
               min_bet, max_bet, game_time)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, data)
        db.commit()
        flash(f"Game '{data[0]}' created!", "success")
        return redirect(url_for("admin"))
    return render_template("admin_game_form.html", game=None)


@app.route("/admin/game/<int:game_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_game(game_id):
    db   = get_db()
    game = db.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
    if not game:
        flash("Game not found.", "danger")
        return redirect(url_for("admin"))

    if request.method == "POST":
        data, error = _parse_game_form(request.form)
        if error:
            flash(error, "danger")
            return render_template("admin_game_form.html", game=game)
        status = request.form.get("status", game["status"])
        db.execute("""
            UPDATE games SET
              title=?, team1=?, team2=?, odds1=?, odds2=?, odds_draw=?,
              spread1=?, spread2=?, spread_odds1=?, spread_odds2=?,
              min_bet=?, max_bet=?, game_time=?, status=?
            WHERE id=?
        """, (*data, status, game_id))
        db.commit()
        flash("Game updated.", "success")
        return redirect(url_for("admin"))

    return render_template("admin_game_form.html", game=game)


def _parse_game_form(form):
    """Parse and validate game create/edit form. Returns (data_tuple, error_str)."""
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

    return (title, team1, team2, odds1, odds2, odds_draw,
            spread1, spread2, spread_odds1, spread_odds2,
            min_bet, max_bet, game_time), None


@app.route("/admin/game/<int:game_id>/settle", methods=["POST"])
@admin_required
def admin_settle_game(game_id):
    db   = get_db()
    game = db.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()

    if not game or game["status"] == "settled":
        flash("Game not found or already settled.", "warning")
        return redirect(url_for("admin"))

    winner       = request.form.get("winner")
    spread_result = request.form.get("spread_result")  # team1 | team2 | push | None

    valid_ml = {game["team1"], game["team2"]}
    if game["odds_draw"]:
        valid_ml.add("Draw")
    if winner not in valid_ml:
        flash("Invalid winner selection.", "danger")
        return redirect(url_for("admin"))

    bets = db.execute(
        "SELECT * FROM bets WHERE game_id=? AND status='pending'", (game_id,)
    ).fetchall()

    settled = 0
    for bet in bets:
        if bet["bet_type"] == "spread":
            if not spread_result:
                continue  # skip spread bets if no spread result given
            if spread_result == "push":
                # Refund the wager
                db.execute("UPDATE bets SET status='push', payout=? WHERE id=?",
                           (bet["amount"], bet["id"]))
                db.execute("UPDATE users SET balance = balance + ? WHERE id=?",
                           (bet["amount"], bet["user_id"]))
            elif bet["pick"] == spread_result:
                payout = round(bet["amount"] * bet["odds"], 2)
                db.execute("UPDATE bets SET status='won', payout=? WHERE id=?",
                           (payout, bet["id"]))
                db.execute("UPDATE users SET balance = balance + ? WHERE id=?",
                           (payout, bet["user_id"]))
            else:
                db.execute("UPDATE bets SET status='lost', payout=0 WHERE id=?", (bet["id"],))
        else:  # moneyline
            if bet["pick"] == winner:
                payout = round(bet["amount"] * bet["odds"], 2)
                db.execute("UPDATE bets SET status='won', payout=? WHERE id=?",
                           (payout, bet["id"]))
                db.execute("UPDATE users SET balance = balance + ? WHERE id=?",
                           (payout, bet["user_id"]))
            else:
                db.execute("UPDATE bets SET status='lost', payout=0 WHERE id=?", (bet["id"],))
        settled += 1

    db.execute(
        "UPDATE games SET status='settled', winner=?, spread_result=? WHERE id=?",
        (winner, spread_result, game_id),
    )
    db.commit()
    flash(f"Game settled! Winner: {winner}. {settled} bet(s) processed.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/game/<int:game_id>/close", methods=["POST"])
@admin_required
def admin_close_game(game_id):
    db = get_db()
    db.execute("UPDATE games SET status='closed' WHERE id=?", (game_id,))
    db.commit()
    flash("Betting closed for this game.", "info")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/adjust-balance", methods=["POST"])
@admin_required
def admin_adjust_balance(user_id):
    amount = request.form.get("amount", "").strip()
    try:
        amount = float(amount)
    except ValueError:
        flash("Invalid amount.", "danger")
        return redirect(url_for("admin"))
    db   = get_db()
    user = db.execute("SELECT username, balance FROM users WHERE id=?", (user_id,)).fetchone()
    new_balance = max(0, user["balance"] + amount)
    db.execute("UPDATE users SET balance = ? WHERE id=?", (new_balance, user_id))
    db.commit()
    word = "Added" if amount >= 0 else "Removed"
    flash(f"{word} ${abs(amount):,.2f} {'to' if amount >= 0 else 'from'} {user['username']}. Balance: ${new_balance:,.2f}", "success")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/set-balance", methods=["POST"])
@admin_required
def admin_set_balance(user_id):
    amount = request.form.get("amount", "").strip()
    try:
        amount = float(amount)
        if amount < 0:
            raise ValueError
    except ValueError:
        flash("Invalid amount.", "danger")
        return redirect(url_for("admin"))
    db   = get_db()
    user = db.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    db.execute("UPDATE users SET balance = ? WHERE id=?", (amount, user_id))
    db.commit()
    flash(f"Set {user['username']}'s balance to ${amount:,.2f}.", "success")
    return redirect(url_for("admin"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
