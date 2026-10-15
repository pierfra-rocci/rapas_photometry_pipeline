from flask import Flask, request
import sqlite3
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import secrets
import string
import os
import base64
import datetime
import re
import threading
from contextlib import contextmanager

try:
    import config
except ImportError:  # config.py is optional; SMTP features degrade gracefully.
    config = None

app = Flask(__name__)
app.config["PREFERRED_URL_SCHEME"] = "https"
CORS(app)

RECOVERY_MAX_FAILED_ATTEMPTS = 5
RECOVERY_WINDOW = datetime.timedelta(minutes=15)
_recovery_failures = {}
_recovery_lock = threading.Lock()


def _recovery_is_locked(email):
    key = email.strip().lower()
    with _recovery_lock:
        entry = _recovery_failures.get(key)
        if entry is None:
            return False
        count, first_seen = entry
        if datetime.datetime.now() - first_seen > RECOVERY_WINDOW:
            del _recovery_failures[key]
            return False
        return count >= RECOVERY_MAX_FAILED_ATTEMPTS


def _recovery_register_failure(email):
    key = email.strip().lower()
    with _recovery_lock:
        count, first_seen = _recovery_failures.get(key, (0, datetime.datetime.now()))
        _recovery_failures[key] = (count + 1, first_seen)


def _recovery_reset(email):
    with _recovery_lock:
        _recovery_failures.pop(email.strip().lower(), None)


# Use absolute path for database file
if os.getenv("APP_ENV") == "development":
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users_dev.db")
else:
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.db")


@contextmanager
def get_db_connection():
    """Context manager for database connections"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Create users table if it doesn't exist
def init_db():
    try:
        with get_db_connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                config_json TEXT
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS recovery_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                code TEXT NOT NULL,
                expires_at DATETIME NOT NULL
            )""")
            print("Database schema created successfully")

            # Verify the tables exist
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
            )
            if cursor.fetchone():
                print("Confirmed 'users' table exists")
            else:
                print("WARNING: 'users' table was not created!")

            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='recovery_codes'"
            )
            if cursor.fetchone():
                print("Confirmed 'recovery_codes' table exists")
            else:
                print("WARNING: 'recovery_codes' table was not created!")

    except Exception as e:
        print(f"Database initialization error: {e}")
        raise


init_db()


def is_valid_email(email):
    """Validate email format"""
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(pattern, email) is not None


def find_user_by_login(cur, identifier):
    """Return the user row matching a username or an email address.

    The username is matched verbatim. The email is matched case-insensitively
    and ignoring surrounding whitespace, because the address is stored as it
    was typed at registration while users rarely retype it with the exact same
    casing.
    """
    cur.execute(
        "SELECT * FROM users WHERE username = ? OR LOWER(email) = LOWER(?)",
        (identifier, identifier.strip()),
    )
    return cur.fetchone()


def cleanup_expired_codes():
    """Remove expired recovery codes from the database"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM recovery_codes WHERE expires_at < datetime('now')")
    except Exception as e:
        print(f"Error cleaning up expired codes: {e}")


def authenticate_request():
    """Return the account username for valid HTTP Basic credentials, else None.

    Both the username and the registration email are accepted as the Basic
    user name; the canonical username is always what gets returned.
    """
    auth = request.authorization
    if not auth or not auth.username or not auth.password:
        return None
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            row = find_user_by_login(cur, auth.username)
    except Exception as e:
        print(f"Authentication error: {e}")
        return None
    if row and check_password_hash(row["password"], auth.password):
        return row["username"]
    return None


# Helper send email (configure SMTP as needed)
def send_email(to_email, subject, body):
    if config is None:
        print("SMTP configuration not available.")
        return False, "Email service is not configured."

    smtp_server = config.SMTP_SERVER
    smtp_port = config.SMTP_PORT
    smtp_user = config.SMTP_USER
    smtp_pass_encoded = config.SMTP_PASS_ENCODED
    smtp_pass = (
        base64.b64decode(smtp_pass_encoded).decode() if smtp_pass_encoded else None
    )
    if not smtp_user or not smtp_pass:
        print("SMTP credentials not set.")
        return False, "Email service is not configured."

    try:
        msg = MIMEMultipart()
        msg["From"] = smtp_user
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True, "Email sent successfully."
    except smtplib.SMTPAuthenticationError as e:
        print(f"Failed to send email: SMTP authentication error - {e}")
        return False, "Failed to send email due to authentication error."
    except smtplib.SMTPConnectError as e:
        print(f"Failed to send email: Error connecting to the SMTP server - {e}")
        return False, "Failed to connect to the email server."
    except Exception as e:
        print(f"Failed to send email: An unexpected error occurred - {e}")
        return False, "An unexpected error occurred while sending the email."


@app.route("/register", methods=["POST"])
def register():
    data = request.form
    username = data.get("username")
    password = data.get("password")
    email = data.get("email")

    if not username or not password or not email:
        return "Username, password, and email required.", 400

    if not is_valid_email(email):
        return "Invalid email format.", 400

    if (
        len(password) < 8
        or not any(c.isupper() for c in password)
        or not any(c.islower() for c in password)
        or not any(c.isdigit() for c in password)
    ):
        return (
            "Password must be at least 8 characters long and contain at least one uppercase letter, one lowercase letter, and one digit.",
            400,
        )

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            # Check if username or email already exists
            cur.execute(
                "SELECT * FROM users WHERE username = ? OR email = ?", (username, email)
            )
            if cur.fetchone():
                return "Username or email is already taken.", 409

            hashed_pw = generate_password_hash(password)
            cur.execute(
                "INSERT INTO users (username, password, email) VALUES (?, ?, ?)",
                (username, hashed_pw, email),
            )
    except Exception as e:
        print(f"Registration error: {e}")
        return "An error occurred during registration.", 500

    # Send welcome email (don't fail registration if email fails)
    success, message = send_email(
        email,
        "Welcome to RAPAS Photometry Pipeline!",
        f"Hi {username},\n\nThank you for registering for the RAPAS Photometry Pipeline. Enjoy!\n\nBest,\nThe RPP Team",
    )
    if not success:
        print(f"Warning: Failed to send welcome email: {message}")

    return "Registered successfully.", 201


@app.route("/login", methods=["POST"])
def login():
    data = request.form
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return "Username and password required.", 400

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            user = find_user_by_login(cur, username)
    except Exception as e:
        print(f"Login error: {e}")
        return "An error occurred during login.", 500

    if user and check_password_hash(user["password"], password):
        headers = {"X-RPP-Username": user["username"]} if user["username"].isascii() else {}
        return "Logged in successfully.", 200, headers
    return "Invalid username or password.", 401


@app.route("/recover_request", methods=["POST"])
def recover_request():
    generic_recovery_message = (
        "If the email is registered, a recovery code has been sent."
    )
    data = request.form
    email = data.get("email")

    if not email:
        return "Email required.", 400

    if not is_valid_email(email):
        return "Invalid email format.", 400

    # Clean up old expired codes
    cleanup_expired_codes()

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT username, email FROM users WHERE LOWER(email) = LOWER(?)",
                (email.strip(),),
            )
            user = cur.fetchone()

            if not user:
                # Do not reveal whether the email is registered.
                return generic_recovery_message, 200

            cur.execute(
                "DELETE FROM recovery_codes WHERE LOWER(email) = LOWER(?)",
                (user["email"],),
            )
            code = "".join(secrets.choice(string.digits) for _ in range(6))
            expires_at = datetime.datetime.now() + datetime.timedelta(minutes=15)
            hashed_code = generate_password_hash(code)
            cur.execute(
                "INSERT INTO recovery_codes (email, code, expires_at) VALUES (?, ?, ?)",
                (user["email"], hashed_code, expires_at.strftime("%Y-%m-%d %H:%M:%S")),
            )
    except Exception as e:
        print(f"Recovery request error: {e}")
        return "An error occurred during recovery request.", 500

    success, message = send_email(
        user["email"], "Password Recovery Code", f"Your recovery code is: {code}"
    )
    if not success:
        return message, 500

    _recovery_reset(user["email"])
    return generic_recovery_message, 200


@app.route("/recover_confirm", methods=["POST"])
def recover_confirm():
    data = request.form
    email = data.get("email")
    code = data.get("code")
    new_password = data.get("new_password")

    if not email or not code or not new_password:
        return "Email, code, and new password required.", 400

    if not is_valid_email(email):
        return "Invalid email format.", 400

    if len(new_password) < 8:
        return "New password must be at least 8 characters long.", 400

    if _recovery_is_locked(email):
        return "Invalid or expired recovery code.", 400

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM users WHERE LOWER(email) = LOWER(?)",
                (email.strip(),),
            )
            user = cur.fetchone()
            if not user:
                return "Invalid or expired recovery code.", 400

            cur.execute(
                "SELECT * FROM recovery_codes WHERE LOWER(email) = LOWER(?)",
                (email.strip(),),
            )
            recovery_codes = cur.fetchall()

            valid_code_found = False
            recovery_code_id = None
            for recovery_code in recovery_codes:
                if check_password_hash(recovery_code["code"], code):
                    expires_at = datetime.datetime.strptime(
                        recovery_code["expires_at"], "%Y-%m-%d %H:%M:%S"
                    )
                    if datetime.datetime.now() > expires_at:
                        continue  # Expired code
                    valid_code_found = True
                    recovery_code_id = recovery_code["id"]
                    break

            if not valid_code_found:
                _recovery_register_failure(email)
                return "Invalid or expired recovery code.", 400

            hashed_pw = generate_password_hash(new_password)
            cur.execute(
                "UPDATE users SET password = ? WHERE id = ?", (hashed_pw, user["id"])
            )
            cur.execute(
                "DELETE FROM recovery_codes WHERE LOWER(email) = LOWER(?)",
                (email.strip(),),
            )
    except Exception as e:
        print(f"Recovery confirm error: {e}")
        return "An error occurred during password reset.", 500

    _recovery_reset(email)

    return "Password updated successfully.", 200


@app.route("/save_config", methods=["POST"])
def save_config():
    username = authenticate_request()
    if not username:
        return "Authentication required.", 401, {"WWW-Authenticate": "Basic"}

    data = request.get_json(silent=True) or {}
    config_json = data.get("config_json")

    if config_json is None:
        return "config_json required.", 400

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE users SET config_json = ? WHERE username = ?",
                (config_json, username),
            )
    except Exception as e:
        print(f"Save config error: {e}")
        return "An error occurred while saving configuration.", 500

    return "Config saved.", 200


@app.route("/get_config", methods=["GET"])
def get_config():
    username = authenticate_request()
    if not username:
        return "Authentication required.", 401, {"WWW-Authenticate": "Basic"}

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT config_json FROM users WHERE username = ?", (username,))
            row = cur.fetchone()
    except Exception as e:
        print(f"Get config error: {e}")
        return "An error occurred while retrieving configuration.", 500

    if row and row["config_json"]:
        return row["config_json"], 200
    else:
        return "{}", 200


@app.errorhandler(404)
def page_not_found(e):
    return {"error": "Page not found"}, 404


@app.errorhandler(500)
def internal_server_error(e):
    return {"error": "Internal server error"}, 500


if __name__ == "__main__":
    _debug = os.getenv("APP_ENV") == "development"
    app.run(port=5000, debug=_debug, use_reloader=_debug, threaded=True)
