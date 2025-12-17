import json
import logging
import sys
import secrets
import string
from pathlib import Path

import requests
from wordfreq import top_n_list  # slovník slov
import winreg
import urllib3
from urllib3.exceptions import InsecureRequestWarning

# ---------------------------------------------------------------------------
# Cesty a logging
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

LOG_FILE = LOGS_DIR / "rotate.log"


def setup_logging():
    config_path = BASE_DIR / "config.json"
    log_level = logging.INFO

    if config_path.is_file():
        try:
            with config_path.open("r", encoding="utf-8") as f:
                cfg = json.load(f)
            lvl = cfg.get("LOG_LEVEL", "INFO").upper()
            log_level = getattr(logging, lvl, logging.INFO)
        except Exception:
            pass

    handlers = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
    if sys.stdout and sys.stdout.isatty():
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=handlers,
    )


setup_logging()
logger = logging.getLogger("psk_rotator")

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def load_config() -> dict:
    config_path = BASE_DIR / "config.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file {config_path} missing")

    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Čtení WM_KEY_ID / WM_KEY_VALUE z registry (HKLM\SOFTWARE\AristaPskRotator)
# ---------------------------------------------------------------------------


def get_credentials_from_registry() -> tuple[str, str]:
    """
    Čte WM_KEY_ID a WM_KEY_VALUE z:
      HKEY_LOCAL_MACHINE\SOFTWARE\AristaPskRotator

    Na on-prem:
      WM_KEY_ID    = username (např. api_user)
      WM_KEY_VALUE = password
    """
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\AristaPskRotator",
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            r"Registry key HKLM\SOFTWARE\AristaPskRotator not found. "
            r"Run deploy.py to create it."
        ) from e

    try:
        key_id, _ = winreg.QueryValueEx(key, "WM_KEY_ID")
        key_value, _ = winreg.QueryValueEx(key, "WM_KEY_VALUE")
    except FileNotFoundError as e:
        raise RuntimeError(
            "Registry values WM_KEY_ID / WM_KEY_VALUE not found "
            r"under HKLM\SOFTWARE\AristaPskRotator"
        ) from e

    if not key_id or not key_value:
        raise RuntimeError("WM_KEY_ID / WM_KEY_VALUE in registry are empty")

    return str(key_id), str(key_value)


# ---------------------------------------------------------------------------
# Passphrase generator (Gentle-Winter-Planet7)
# ---------------------------------------------------------------------------

# předpočítáme seznam rozumných slov z wordfreq
# (top 5000 EN slov, jenom písmena, délka 3–10 znaků)
WORD_LIST = [
    w
    for w in top_n_list("en", 5000)
    if w.isalpha() and 3 <= len(w) <= 10
]


def generate_psk() -> str:
    """
    Vygeneruje passphrase typu: Gentle-Winter-Planet7

    - 3 náhodná slova z WORD_LIST
    - první písmeno velké
    - mezi slovy pomlčka
    - na konci jedna náhodná číslice
    """
    if not WORD_LIST:
        # fallback, kdyby se něco pokazilo s wordfreq
        logger.warning("WORD_LIST is empty, falling back to random chars")
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(16))

    words = [secrets.choice(WORD_LIST).capitalize() for _ in range(3)]
    digit = secrets.choice(string.digits)
    return f"{words[0]}-{words[1]}-{words[2]}{digit}"


# ---------------------------------------------------------------------------
# WM Login / API
# ---------------------------------------------------------------------------


def login_to_wm(
    session: requests.Session,
    base_url: str,
    username: str,
    password: str,
    version: str = "latest",
):
    """
    On-prem: přihlášení přes username/password credentials.
    """
    url = f"{base_url.rstrip('/')}/wifi/api/session"
    payload = {
        "type": "usernamepasswordcredentials",
        "username": username,
        "password": password,
        "timeout": 3600,
        "clientIdentifier": "psk-rotator-simple",
    }

    headers = {"Content-Type": "application/json", "Version": version}
    resp = session.post(url, json=payload, headers=headers, timeout=15)
    if not resp.ok:
        raise RuntimeError(f"Login failed: {resp.status_code} {resp.text}")


def fetch_ssid_profiles(
    session: requests.Session,
    base_url: str,
    location_id: int,
    node_id: int,
    version: str = "17",
):
    url = f"{base_url.rstrip('/')}/wifi/api/deviceconfiguration/ssidprofiles"
    params = {"locationid": location_id, "nodeid": node_id}
    headers = {"Content-Type": "application/json", "Version": version}
    resp = session.get(url, params=params, headers=headers, timeout=20)
    if not resp.ok:
        raise RuntimeError(f"GET ssidprofiles failed: {resp.status_code} {resp.text}")
    return resp.json()


def update_profile_psk(profile: dict, new_psk: str):
    try:
        profile["wirelessProfile"]["securityMode"]["pskPassphrase"] = new_psk
    except KeyError as e:
        raise RuntimeError("Profile missing PSK field") from e


def put_profile(
    session: requests.Session,
    base_url: str,
    profile: dict,
    version: str = "17",
):
    url = f"{base_url.rstrip('/')}/wifi/api/deviceconfiguration/ssidprofiles"
    headers = {"Content-Type": "application/json", "Version": version}
    resp = session.put(url, json=profile, headers=headers, timeout=20)
    if not resp.ok:
        raise RuntimeError(f"PUT failed: {resp.status_code} {resp.text}")


def logout_from_wm(session: requests.Session, base_url: str, version: str = "latest"):
    """
    Ukončí session, aby se nehromadily.
    Zkusí DELETE /session, když neprojde, zkusí POST /logout.
    Chyby ignoruje (např. pokud endpoint není k dispozici).
    """
    headers = {"Content-Type": "application/json", "Version": version}
    try:
        url = f"{base_url.rstrip('/')}/wifi/api/session"
        r = session.delete(url, headers=headers, timeout=10)
        if r.ok:
            return
    except Exception:
        pass

    try:
        url = f"{base_url.rstrip('/')}/wifi/api/logout"
        session.post(url, headers=headers, timeout=10)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Save State for Web UI
# ---------------------------------------------------------------------------


def save_state(ssid: str, psk: str):
    from datetime import datetime, timezone
    import qrcode

    ts = datetime.now(timezone.utc).isoformat()
    out = {
        "ssid": ssid,
        "psk": psk,
        "last_rotated_utc": ts,
        "qr_image": f"wifi_qr_{ssid}.png",
    }

    # JSON
    with (DATA_DIR / "current_psk.json").open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    # QR
    img = qrcode.make(f"WIFI:T:WPA;S:{ssid};P:{psk};H:false;;")
    img.save(DATA_DIR / out["qr_image"])


# ---------------------------------------------------------------------------
# ROTATE
# ---------------------------------------------------------------------------


def rotate_once() -> bool:
    session: requests.Session | None = None
    try:
        logger.info("Starting PSK rotation...")
        cfg = load_config()

        base_url = cfg["WM_BASE_URL"].rstrip("/")
        ssid_name = cfg["SSID_PROFILE_NAME"]
        node_id = int(cfg["WM_NODE_ID"])

        # on-prem: location id je dáno přímo v configu
        if "WM_LOCATION_ID" not in cfg:
            raise RuntimeError(
                "WM_LOCATION_ID is missing in config.json "
                "(on-prem mode requires explicit location id)."
            )
        location_id = int(cfg["WM_LOCATION_ID"])

        # VERIFY_SSL (default True)
        verify_ssl = bool(cfg.get("VERIFY_SSL", True))
        if not verify_ssl:
            urllib3.disable_warnings(InsecureRequestWarning)
            logger.warning(
                "VERIFY_SSL is set to false – TLS certs will NOT be verified!"
            )

        # credentials z registry (username/password)
        username, password = get_credentials_from_registry()
        logger.debug("Got WM_KEY_ID/WM_KEY_VALUE from registry (used as username/password)")

        new_psk = generate_psk()
        logger.info("Generated new PSK passphrase: %s", new_psk)

        session = requests.Session()
        session.verify = verify_ssl

        # přihlášení
        login_to_wm(
            session,
            base_url,
            username,
            password,
            cfg.get("WM_SESSION_VERSION", "latest"),
        )

        # načíst profily
        profiles = fetch_ssid_profiles(
            session,
            base_url,
            location_id,
            node_id,
            cfg.get("WM_DEVICECONFIG_VERSION", "17"),
        )

        profile = next(
            (
                p
                for p in profiles
                if p.get("templateName") == ssid_name or p.get("ssid") == ssid_name
            ),
            None,
        )
        if not profile:
            raise RuntimeError(
                f"SSID profile '{ssid_name}' not found "
                f"(location_id={location_id}, node_id={node_id})"
            )

        update_profile_psk(profile, new_psk)
        put_profile(session, base_url, profile, cfg.get("WM_DEVICECONFIG_VERSION", "17"))

        ssid = profile.get("ssid", ssid_name)
        save_state(ssid, new_psk)

        logger.info("PSK rotation SUCCESS: %s", new_psk)
        return True

    except Exception as e:
        logger.exception("PSK rotation FAILED: %s", e)
        return False
    finally:
        if session is not None:
            try:
                logout_from_wm(
                    session,
                    base_url,  # type: ignore[name-defined]
                    cfg.get("WM_SESSION_VERSION", "latest"),  # type: ignore[name-defined]
                )
            except Exception:
                # nechceme přepsat původní chybu
                pass
            session.close()


def main():
    if not rotate_once():
        sys.exit(1)


if __name__ == "__main__":
    main()
