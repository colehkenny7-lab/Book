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
STARTING_BALANCE = 0.0  # new users start with $0 until admin sets their balance


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
            balance     REAL    NOT NULL DEFAULT 1000.0,
            is_admin    INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS games (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT    NOT NULL,
            team1       TEXT    NOT NULL,
            team2       TEXT    NOT NULL,
            odds1       REAL    NOT NULL DEFAULT 2.0,
            odds2       REAL    NOT NULL DEFAULT 2.0,
            odds_draw   REAL,
            game_time   TEXT    NOT NULL,
            status      TEXT    NOT NULL DEFAULT 'open',
            winner      TEXT,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS bets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            game_id     INTEGER NOT NULL REFERENCES games(id),
            pick        TEXT    NOT NULL,
            amount      REAL    NOT NULL,
            odds        REAL    NOT NULL,
            payout      REAL,
            status      TEXT    NOT NULL DEFAULT 'pending',
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );
    """)
    db.commit()

    # Create default admin if none exists
    admin = db.execute("SELECT id FROM users WHERE is_admin=1").fetchone()
    if not admin:
        db.execute(
            "INSERT INTO users (username, password, balance, is_admin) VALUES (?,?,?,1)",
            ("admin", generate_password_hash("admin123"), STARTING_BALANCE),
        )
        db.commit()


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
        existing = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if existing:
            flash("Username already taken.", "danger")
            return render_template("register.html")

        db.execute(
            "INSERT INTO users (username, password, balance) VALUES (?,?,?)",
            (username, generate_password_hash(password), STARTING_BALANCE),
        )
        db.commit()
        flash(f"Welcome, {username}! You have ${STARTING_BALANCE:,.0f} in chips. Log in to start betting.", "success")
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
    games = db.execute(
        "SELECT * FROM games WHERE status='open' ORDER BY game_time ASC"
    ).fetchall()
    return render_template("index.html", games=games)


@app.route("/leaderboard")
def leaderboard():
    db = get_db()
    users = db.execute("""
        SELECT u.username, u.balance,
               COUNT(b.id)                        AS total_bets,
               SUM(CASE WHEN b.status='won' THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN b.status='lost' THEN 1 ELSE 0 END) AS losses,
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
    db    = get_db()
    open_games   = db.execute("SELECT * FROM games WHERE status='open'  ORDER BY game_time ASC").fetchall()
    closed_games = db.execute("SELECT * FROM games WHERE status='closed' ORDER BY game_time DESC").fetchall()
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
        pick   = request.form.get("pick")
        amount = request.form.get("amount", "").strip()

        valid_picks = {game["team1"], game["team2"]}
        if game["odds_draw"]:
            valid_picks.add("Draw")

        if pick not in valid_picks:
            flash("Invalid pick.", "danger")
            return render_template("bet.html", game=game, user=user)

        try:
            amount = float(amount)
        except ValueError:
            flash("Enter a valid amount.", "danger")
            return render_template("bet.html", game=game, user=user)

        if amount < 1:
            flash("Minimum bet is $1.", "danger")
            return render_template("bet.html", game=game, user=user)

        if amount > user["balance"]:
            flash("Insufficient balance.", "danger")
            return render_template("bet.html", game=game, user=user)

        # Determine odds for pick
        if pick == game["team1"]:
            odds = game["odds1"]
        elif pick == game["team2"]:
            odds = game["odds2"]
        else:
            odds = game["odds_draw"]

        # Deduct balance and record bet
        db.execute("UPDATE users SET balance = balance - ? WHERE id=?", (amount, user["id"]))
        db.execute(
            "INSERT INTO bets (user_id, game_id, pick, amount, odds) VALUES (?,?,?,?,?)",
            (user["id"], game_id, pick, amount, odds),
        )
        db.commit()

        potential = round(amount * odds, 2)
        flash(f"Bet placed! ${amount:,.2f} on {pick} @ {odds}x  |  Potential payout: ${potential:,.2f}", "success")
        return redirect(url_for("my_bets"))

    return render_template("bet.html", game=game, user=user)


@app.route("/my-bets")
@login_required
def my_bets():
    db   = get_db()
    user = current_user()
    bets = db.execute("""
        SELECT b.*, g.title, g.team1, g.team2, g.status AS game_status, g.winner
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
    return render_template("admin.html", games=games, users=users)


@app.route("/admin/game/new", methods=["GET", "POST"])
@admin_required
def admin_new_game():
    if request.method == "POST":
        title     = request.form["title"].strip()
        team1     = request.form["team1"].strip()
        team2     = request.form["team2"].strip()
        odds1     = request.form["odds1"]
        odds2     = request.form["odds2"]
        odds_draw = request.form.get("odds_draw", "").strip() or None
        game_time = request.form["game_time"]

        if not all([title, team1, team2, odds1, odds2, game_time]):
            flash("All fields except Draw Odds are required.", "danger")
            return render_template("admin_game_form.html", game=None)

        try:
            odds1 = float(odds1)
            odds2 = float(odds2)
            if odds_draw:
                odds_draw = float(odds_draw)
        except ValueError:
            flash("Odds must be numbers.", "danger")
            return render_template("admin_game_form.html", game=None)

        if odds1 < 1 or odds2 < 1 or (odds_draw and odds_draw < 1):
            flash("Odds must be at least 1.0.", "danger")
            return render_template("admin_game_form.html", game=None)

        db = get_db()
        db.execute(
            "INSERT INTO games (title, team1, team2, odds1, odds2, odds_draw, game_time) VALUES (?,?,?,?,?,?,?)",
            (title, team1, team2, odds1, odds2, odds_draw, game_time),
        )
        db.commit()
        flash(f"Game '{title}' created!", "success")
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
        title     = request.form["title"].strip()
        team1     = request.form["team1"].strip()
        team2     = request.form["team2"].strip()
        odds1     = request.form["odds1"]
        odds2     = request.form["odds2"]
        odds_draw = request.form.get("odds_draw", "").strip() or None
        game_time = request.form["game_time"]
        status    = request.form["status"]

        try:
            odds1 = float(odds1)
            odds2 = float(odds2)
            if odds_draw:
                odds_draw = float(odds_draw)
        except ValueError:
            flash("Odds must be numbers.", "danger")
            return render_template("admin_game_form.html", game=game)

        db.execute(
            "UPDATE games SET title=?,team1=?,team2=?,odds1=?,odds2=?,odds_draw=?,game_time=?,status=? WHERE id=?",
            (title, team1, team2, odds1, odds2, odds_draw, game_time, status, game_id),
        )
        db.commit()
        flash("Game updated.", "success")
        return redirect(url_for("admin"))

    return render_template("admin_game_form.html", game=game)


@app.route("/admin/game/<int:game_id>/settle", methods=["POST"])
@admin_required
def admin_settle_game(game_id):
    db     = get_db()
    game   = db.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()

    if not game:
        flash("Game not found.", "danger")
        return redirect(url_for("admin"))

    if game["status"] == "settled":
        flash("Game already settled.", "warning")
        return redirect(url_for("admin"))

    winner = request.form.get("winner")
    valid  = {game["team1"], game["team2"]}
    if game["odds_draw"]:
        valid.add("Draw")

    if winner not in valid:
        flash("Invalid winner selection.", "danger")
        return redirect(url_for("admin"))

    # Settle all pending bets for this game
    bets = db.execute(
        "SELECT * FROM bets WHERE game_id=? AND status='pending'", (game_id,)
    ).fetchall()

    for bet in bets:
        if bet["pick"] == winner:
            payout = round(bet["amount"] * bet["odds"], 2)
            db.execute(
                "UPDATE bets SET status='won', payout=? WHERE id=?",
                (payout, bet["id"]),
            )
            db.execute(
                "UPDATE users SET balance = balance + ? WHERE id=?",
                (payout, bet["user_id"]),
            )
        else:
            db.execute(
                "UPDATE bets SET status='lost', payout=0 WHERE id=?",
                (bet["id"],),
            )

    db.execute(
        "UPDATE games SET status='settled', winner=? WHERE id=?",
        (winner, game_id),
    )
    db.commit()

    flash(f"Game settled! Winner: {winner}. {len(bets)} bet(s) processed.", "success")
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

    db = get_db()
    user = db.execute("SELECT username, balance FROM users WHERE id=?", (user_id,)).fetchone()
    new_balance = max(0, user["balance"] + amount)
    db.execute("UPDATE users SET balance = ? WHERE id=?", (new_balance, user_id))
    db.commit()
    direction = "Added" if amount >= 0 else "Removed"
    flash(f"{direction} ${abs(amount):,.2f} {'to' if amount >= 0 else 'from'} {user['username']}. New balance: ${new_balance:,.2f}", "success")
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

    db = get_db()
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
