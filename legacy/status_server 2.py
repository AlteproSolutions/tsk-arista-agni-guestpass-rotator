import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, send_from_directory, render_template_string, abort

# === Paths ===
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

LOG_FILE = LOGS_DIR / "web.log"

# ---------------------------------------------------------------------------
# Load LOG LEVEL from config
# ---------------------------------------------------------------------------

def _get_log_level_from_config(default: int = logging.INFO) -> int:
    config_path = BASE_DIR / "config.json"
    try:
        with config_path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return default

    level_name = str(cfg.get("LOG_LEVEL", "")).upper()
    return {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }.get(level_name, default)


LOG_LEVEL = _get_log_level_from_config()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("psk_web")

# Disable werkzeug spam
werkzeug_logger = logging.getLogger("werkzeug")
werkzeug_logger.setLevel(logging.WARNING)

# === Flask app ===
app = Flask(__name__)

HTML_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>WiFi Access</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="30">
  <style>
    :root {
      --bg: #e5e7eb;
      --card-bg: #ffffff;
      --accent: #4f46e5;
      --accent-soft: #eef2ff;
      --text-main: #111827;
      --text-muted: #6b7280;
      --text-soft: #9ca3af;
    }

    body {
      margin: 0;
      font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
      background: radial-gradient(circle at top, #e5edff 0, #e5e7eb 45%, #e5e7eb 100%);
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
    }

    .card {
      background: var(--card-bg);
      padding: 56px 64px;
      border-radius: 32px;
      box-shadow: 0 35px 80px rgba(15, 23, 42, 0.25);
      text-align: center;
      max-width: 720px;
      width: 100%;
    }

    h1 {
      margin-bottom: 8px;
      font-size: 36px;
      font-weight: 700;
    }

    p.subtitle {
      margin-bottom: 40px;
      font-size: 18px;
      color: var(--text-muted);
    }

    .label {
      text-transform: uppercase;
      letter-spacing: .16em;
      font-size: 12px;
      color: var(--text-soft);
      margin-bottom: 6px;
    }

    .value {
      font-size: 22px;
      font-weight: 600;
      margin-bottom: 24px;
    }

    .psk-badge {
      display: inline-block;
      padding: 14px 28px;
      border-radius: 999px;
      background: var(--accent-soft);
      font-family: "JetBrains Mono", monospace;
      font-size: 24px;
      margin-bottom: 32px;
      border: 1px solid #e5e7eb;
      user-select: all;
    }

    .qr img {
      width: 340px;
      height: 340px;
      border-radius: 24px;
      box-shadow: 0 30px 60px rgba(15, 23, 42, 0.22);
    }

    .footer {
      font-size: 12px;
      color: var(--text-soft);
      margin-top: 10px;
    }

    .error {
      color: #b91c1c;
      font-size: 16px;
    }

    @media (max-width: 768px) {
      .card {
        padding: 32px 24px;
        margin: 24px;
      }
      .qr img {
        width: 260px;
        height: 260px;
      }
    }
  </style>
</head>

<body>
<div class="card">
  {% if error %}
    <h1>WiFi Access</h1>
    <p class="subtitle">Scan the QR code or type the password</p>
    <p class="error">{{ error }}</p>
  {% else %}
    <h1>WiFi Access</h1>
    <p class="subtitle">Scan the QR code or type the password</p>

    <div class="label">SSID</div>
    <div class="value">{{ ssid }}</div>

    <div class="label">Password</div>
    <div class="psk-badge">{{ psk }}</div>

    <div class="qr">
      <img src="/qr/{{ qr_image }}" alt="WiFi QR">
    </div>

    {% if last_rotated %}
      <div class="footer">
        Last rotated: {{ last_rotated }}
      </div>
    {% endif %}
  {% endif %}
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Load current state
# ---------------------------------------------------------------------------

def load_state():
    state_file = DATA_DIR / "current_psk.json"
    if not state_file.is_file():
        return None

    try:
        with state_file.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.exception("Error reading state file: %s", e)
        return None


@app.route("/")
def index():
    logger.debug("GET /")
    state = load_state()

    if not state:
        return render_template_string(
            HTML_TEMPLATE,
            error="WiFi status is not available yet. Please try again later.",
        )

    ssid = state.get("ssid")
    psk = state.get("psk")
    qr_image = state.get("qr_image")

    # Convert ISO timestamp
    last_rotated_raw = state.get("last_rotated_utc")
    last_rotated = None

    if last_rotated_raw:
        try:
            dt = datetime.fromisoformat(last_rotated_raw)
            last_rotated = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            last_rotated = last_rotated_raw

    return render_template_string(
        HTML_TEMPLATE,
        error=None,
        ssid=ssid,
        psk=psk,
        qr_image=qr_image,
        last_rotated=last_rotated,
    )


@app.route("/qr/<path:filename>")
def qr(filename: str):
    file_path = DATA_DIR / filename
    if not file_path.is_file():
        logger.warning("QR file not found: %s", file_path)
        abort(404)

    return send_from_directory(DATA_DIR, filename)


def load_config_port() -> int:
    config_path = BASE_DIR / "config.json"

    if not config_path.is_file():
        return 8081

    try:
        with config_path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        return int(cfg.get("BACKEND_PORT", 8081))
    except Exception:
        return 8081


def main():
    port = load_config_port()
    logger.info("Starting Flask web on port %s", port)
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
