#!/usr/bin/env python3
import requests
import pprint
import secrets
import string
from requests.exceptions import JSONDecodeError

AGNI_HOST = "https://ag.agni.arista.io"
AGNI_API_BASE = f"{AGNI_HOST}/api"
CVCUE_KEYLOGIN_PATH = "/cvcue/keyLogin"

API_KEY_ID = ""
API_KEY_VALUE = ""

TARGET_GUEST_LOGIN = "test-guest@altepro.cz"  # loginName/email


# -------------------------
# Helpery
# -------------------------

def generate_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def get_agni_cookie() -> str:
    url = f"{AGNI_HOST}{CVCUE_KEYLOGIN_PATH}"
    params = {
        "keyID": API_KEY_ID,
        "keyValue": API_KEY_VALUE,
    }

    print(f"=== LOGIN: GET {url} ===")
    resp = requests.get(
        url,
        params=params,
        headers={"Accept": "application/json"},
        timeout=10,
        verify=True,
    )
    print("Login status:", resp.status_code)
    resp.raise_for_status()

    data = resp.json()
    print("Login response JSON:", data)

    cookie_value = data.get("data", {}).get("cookie")
    if not cookie_value:
        raise RuntimeError("V odpovědi z keyLogin chybí data.cookie")

    print("Raw cookie from keyLogin:", cookie_value)
    cookie_for_header = cookie_value.split(";", 1)[0].strip()
    print("Cookie for API calls:", cookie_for_header)

    return cookie_for_header


def agni_post(path: str, payload: dict, cookie: str) -> dict:
    url = f"{AGNI_API_BASE}{path}"
    print(f"\n=== POST {url} ===")
    print("Request payload:")
    pprint.pp(payload)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Cookie": cookie,
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=10, verify=True)
    print("HTTP status:", resp.status_code)
    resp.raise_for_status()

    try:
        data = resp.json()
    except JSONDecodeError:
        print("!!! Response není validní JSON, raw text (first 400 chars):")
        print(resp.text[:400])
        raise

    text = str(data)
    print("Response JSON (zkráceně):")
    if len(text) > 1200:
        print(text[:1200] + " ... [zkráceno]")
    else:
        pprint.pp(data)

    # jen pokud error není prázdný string
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"API error: {data['error']}")

    return data


def get_org_id(cookie: str) -> str:
    data = agni_post("/org.info", {}, cookie)
    org_id = data.get("data", {}).get("orgID")
    if not org_id:
        raise RuntimeError("Nepodařilo se najít orgID v odpovědi /org.info")
    print(f"\n=== Detekované orgID: {org_id} ===")
    return org_id


def find_guest_user(cookie: str, org_id: str, login_name: str) -> dict:
    """
    Stáhneme jednoduše list userů (limit 50) a usera najdeme v Pythonu.
    Žádné filters – ty dělají chybu 'invalid field'.
    """
    payload = {
        "orgID": org_id,
        "limit": 50
    }

    data = agni_post("/identity.guest.user.list", payload, cookie)

    users = data.get("data", {}).get("users", [])
    for u in users:
        if u.get("loginName") == login_name or u.get("email") == login_name:
            print("\n>>> Nalezený guest user:")
            pprint.pp(u)
            return u

    raise RuntimeError(f"Guest user s loginName/email '{login_name}' nebyl nalezen v prvních {len(users)} uživatelích.")


def update_guest_password(cookie: str, org_id: str, user: dict, new_password: str) -> dict:
    """
    Poskládáme payload podle IdentityGuestUserUpdateReq:
      address, batchID, company, deviceLimit, email, loginName, name, notes,
      orgID, password, phone, portalID, pskPassphrase, sendEmail, status,
      userID, validFrom, validTo
    Všechno přebíráme z list response, jen password měníme.
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

    print(f"\n=== Update hesla pro userID {user['userID']} ===")
    data = agni_post("/identity.guest.user.update", payload, cookie)
    return data


# -------------------------
# MAIN
# -------------------------

def main():
    # 1) login → cookie
    cookie = get_agni_cookie()

    # 2) org.info → orgID
    org_id = get_org_id(cookie)

    # 3) najít našeho guest usera (bez filters, hledáme lokálně)
    user = find_guest_user(cookie, org_id, TARGET_GUEST_LOGIN)

    # 4) vygenerovat nové heslo
    new_pwd = generate_password(12)
    print(f"\n=== Generuji nové heslo: {new_pwd} ===")

    # 5) update hesla přes identity.guest.user.update
    update_resp = update_guest_password(cookie, org_id, user, new_pwd)

    print("\n=== Výsledek update ===")
    pprint.pp(update_resp)

    print("\n>>> HOTOVO, nové heslo pro "
          f"{TARGET_GUEST_LOGIN} je: {new_pwd}")


if __name__ == "__main__":
    main()
