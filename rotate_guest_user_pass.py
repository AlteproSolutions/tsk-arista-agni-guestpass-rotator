#!/usr/bin/env python3
import json
import logging
import sys
import secrets
import string
from datetime import datetime, timezone
from pathlib import Path

import requests
import qrcode
import urllib3
from requests.exceptions import JSONDecodeError
from urllib3.exceptions import InsecureRequestWarning
from wordfreq import top_n_list

import winreg

REG_PATH = r"SOFTWARE\AristaGuestPortal"
REG_VALUE_KEY_ID = "AGNI_KEY_ID"
REG_VALUE_KEY_VALUE = "AGNI_KEY_VALUE"


# ---------------------------------------------------------------------------
# Cesty a logging
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

LOG_FILE = LOGS_DIR / "rotate_guest_user_pass.log"


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
logger = logging.getLogger("guest_user_rotator")


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def load_config() -> dict:
    config_path = BASE_DIR / "config.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file {config_path} missing")

    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_credentials_from_registry() -> tuple[str, str]:
    """
    Čte AGNI_KEY_ID a AGNI_KEY_VALUE z:
      HKEY_LOCAL_MACHINE\\SOFTWARE\\AristaGuestPortal

    AGNI_KEY_ID    = keyID z Launchpadu
    AGNI_KEY_VALUE = keyValue z Launchpadu
    """
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            REG_PATH,
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            fr"Registry key HKLM\\{REG_PATH} not found. "
            r"Run deploy.py to create it."
        ) from e

    try:
        key_id, _ = winreg.QueryValueEx(key, REG_VALUE_KEY_ID)
        key_value, _ = winreg.QueryValueEx(key, REG_VALUE_KEY_VALUE)
    except FileNotFoundError as e:
        raise RuntimeError(
            f"Registry values {REG_VALUE_KEY_ID} / {REG_VALUE_KEY_VALUE} "
            f"not found under HKLM\\{REG_PATH}"
        ) from e
    finally:
        winreg.CloseKey(key)

    if not key_id or not key_value:
        raise RuntimeError(f"{REG_VALUE_KEY_ID} / {REG_VALUE_KEY_VALUE} in registry are empty")

    return str(key_id), str(key_value)


# ---------------------------------------------------------------------------
# Password generator – Texas-Figure3 styl
# ---------------------------------------------------------------------------

WORD_LIST = [
    w
    for w in top_n_list("en", 5000)
    if w.isalpha() and 3 <= len(w) <= 10
]


def generate_password() -> str:
    """
    Vygeneruje heslo typu: Texas-Figure3

    - 2 náhodná slova z WORD_LIST
    - první písmeno velké u každého slova
    - mezi slovy pomlčka
    - na konci jedna náhodná číslice
    """
    if not WORD_LIST:
        logger.warning("WORD_LIST is empty, falling back to random chars")
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(16))

    w1 = secrets.choice(WORD_LIST).capitalize()
    w2 = secrets.choice(WORD_LIST).capitalize()
    digit = secrets.choice(string.digits)
    return f"{w1}-{w2}{digit}"


# ---------------------------------------------------------------------------
# AGNI helpery
# ---------------------------------------------------------------------------


def get_agni_cookie(
    base_url: str,
    key_id: str,
    key_value: str,
    verify_ssl: bool,
) -> str:
    """
    Login přes CV-CUE Launchpad:
      GET /cvcue/keyLogin?keyID=...&keyValue=...

    Vrací cookie string vhodný do hlavičky Cookie: ...
    """
    url = f"{base_url.rstrip('/')}/cvcue/keyLogin"
    params = {
        "keyID": key_id,
        "keyValue": key_value,
    }

    logger.info("Calling keyLogin at %s", url)
    resp = requests.get(
        url,
        params=params,
        headers={"Accept": "application/json"},
        timeout=10,
        verify=verify_ssl,
    )
    logger.debug("keyLogin status: %s", resp.status_code)
    resp.raise_for_status()

    data = resp.json()
    logger.debug("keyLogin response JSON: %s", data)

    cookie_value = data.get("data", {}).get("cookie")
    if not cookie_value:
        raise RuntimeError("V odpovědi z keyLogin chybí data.cookie")

    logger.info("Got cookie from keyLogin")
    cookie_for_header = cookie_value.split(";", 1)[0].strip()
    return cookie_for_header


def agni_post(
    api_base_url: str,
    path: str,
    payload: dict,
    cookie: str,
    verify_ssl: bool,
) -> dict:
    """
    Obecný helper na POST na /api/...
    """
    url = f"{api_base_url.rstrip('/')}{path}"
    logger.debug("POST %s payload=%s", url, payload)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Cookie": cookie,
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=15, verify=verify_ssl)
    logger.debug("HTTP status: %s", resp.status_code)
    resp.raise_for_status()

    try:
        data = resp.json()
    except JSONDecodeError:
        logger.error("Response není validní JSON, raw text: %s", resp.text[:400])
        raise

    if isinstance(data, dict) and data.get("error"):
        # AGNI vrací HTTP 200 + error string
        raise RuntimeError(f"API error: {data['error']}")

    return data


def get_org_id(api_base_url: str, cookie: str, verify_ssl: bool) -> str:
    data = agni_post(api_base_url, "/org.info", {}, cookie, verify_ssl)
    org_id = data.get("data", {}).get("orgID")
    if not org_id:
        raise RuntimeError("Nepodařilo se najít orgID v odpovědi /org.info")

    logger.info("Detected orgID: %s", org_id)
    return org_id


def find_guest_user(
    api_base_url: str,
    cookie: str,
    org_id: str,
    login_name: str,
    verify_ssl: bool,
) -> dict:
    """
    Bez filtrování (filters dělají invalid field), stáhneme limit=50
    a najdeme usera v Pythonu podle loginName/email.
    """
    payload = {
        "orgID": org_id,
        "limit": 50,
    }

    data = agni_post(api_base_url, "/identity.guest.user.list", payload, cookie, verify_ssl)
    users = data.get("data", {}).get("users", []) or []

    for u in users:
        if u.get("loginName") == login_name or u.get("email") == login_name:
            logger.info("Guest user '%s' nalezen", login_name)
            logger.debug("Guest user object: %s", u)
            return u

    raise RuntimeError(
        f"Guest user s loginName/email '{login_name}' nebyl nalezen v prvních {len(users)} uživatelích."
    )


def update_guest_password(
    api_base_url: str,
    cookie: str,
    org_id: str,
    user: dict,
    new_password: str,
    verify_ssl: bool,
) -> dict:
    """
    Poskládá payload podle IdentityGuestUserUpdateReq:
      address, batchID, company, deviceLimit, email, loginName, name, notes,
      orgID, password, phone, portalID, pskPassphrase, sendEmail, status,
      userID, validFrom, validTo
    Všechno bere z list response, jen password mění.
    """

    payload = {
        "orgID":         org_id,
        "userID":        user["userID"],
        "loginName":     user["loginName"],
        "email":         user["email"],
        "name":          user.get("name", ""),
        "company":       user.get("company", ""),
        "address":       user.get("address", ""),
        "phone":         user.get("phone", ""),
        "notes":         user.get("notes", ""),
        "portalID":      user["portalID"],
        "batchID":       user.get("batchID", 0),
        "deviceLimit":   user.get("deviceLimit", 0),
        "status":        user.get("status", "enabled"),
        "userType":      user.get("userType", ""),
        "validFrom":     user["validFrom"],
        "validTo":       user["validTo"],
        "pskPassphrase": user.get("pskPassphrase", ""),
        "password":      new_password,
        "sendEmail":     False,
    }

    logger.info("Updating password for userID %s", user["userID"])
    data = agni_post(api_base_url, "/identity.guest.user.update", payload, cookie, verify_ssl)
    return data


def save_state(
    ssid: str,
    guest_login: str,
    guest_password: str,
):
    """
    Uloží JSON pro web + vygeneruje QR kód pro WiFi (SSID, open network).
    """
    ts = datetime.now(timezone.utc).isoformat()
    qr_filename = f"wifi_qr_{ssid}.png"

    out = {
        "ssid": ssid,
        "guest_login": guest_login,
        "guest_password": guest_password,
        "last_rotated_utc": ts,
        "qr_image": qr_filename,
    }

    # JSON
    state_path = DATA_DIR / "current_guest_pass.json"
    with state_path.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    # QR – open WiFi (nopass)
    qr_payload = f"WIFI:T:nopass;S:{ssid};H:false;;"
    img = qrcode.make(qr_payload)
    img.save(DATA_DIR / qr_filename)

    logger.info(
        "State saved: ssid=%s, guest_login=%s, last_rotated_utc=%s",
        ssid,
        guest_login,
        ts,
    )


# ---------------------------------------------------------------------------
# ROTATE
# ---------------------------------------------------------------------------


def rotate_once() -> bool:
    try:
        logger.info("Starting guest user password rotation...")
        cfg = load_config()

        base_url = cfg.get("ARISTA_AGNI_URL")
        if not base_url:
            raise RuntimeError("ARISTA_AGNI_URL missing in config.json")

        # credentials z registry
        key_id, key_value = get_credentials_from_registry()
        logger.debug("Got AGNI_KEY_ID/AGNI_KEY_VALUE from registry")

        guest_login = cfg.get("GUEST_LOGIN")
        if not guest_login:
            raise RuntimeError("GUEST_LOGIN missing in config.json")

        ssid = cfg.get("SSID_PROFILE_NAME")
        if not ssid:
            raise RuntimeError("SSID_PROFILE_NAME missing in config.json")

        verify_ssl = bool(cfg.get("VERIFY_SSL", True))
        if not verify_ssl:
            urllib3.disable_warnings(InsecureRequestWarning)
            logger.warning("VERIFY_SSL is false – TLS certificates will NOT be verified!")

        api_base_url = f"{base_url.rstrip('/')}/api"

        # login → cookie
        cookie = get_agni_cookie(base_url, key_id, key_value, verify_ssl)

        # org.info → orgID
        org_id = get_org_id(api_base_url, cookie, verify_ssl)

        # najít guest usera
        user = find_guest_user(api_base_url, cookie, org_id, guest_login, verify_ssl)

        # nové heslo (Texas-Figure3)
        new_pwd = generate_password()
        logger.info("Generated new guest password for %s: %s", guest_login, new_pwd)

        # update hesla
        _ = update_guest_password(api_base_url, cookie, org_id, user, new_pwd, verify_ssl)
        logger.info("Guest user password updated successfully")

        # uložit stav pro web (SSID + guest login/pass)
        save_state(ssid, guest_login, new_pwd)

        logger.info("Guest user rotation SUCCESS")
        return True

    except Exception as e:
        logger.exception("Guest user rotation FAILED: %s", e)
        return False


def main():
    if not rotate_once():
        sys.exit(1)


if __name__ == "__main__":
    main()
