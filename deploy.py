#!/usr/bin/env python3
import json
import sys
import subprocess
from pathlib import Path
import getpass
import winreg

# Základní cesty
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

# Registry cesta pro AGNI credentials
REG_PATH = r"Software\AristaAgni"
REG_VALUE_KEY_ID = "AGNI_KEY_ID"
REG_VALUE_KEY_VALUE = "AGNI_KEY_VALUE"

# Výchozí konfigurace (AGNI guest portal)
DEFAULT_CONFIG = {
    # URL na AGNI cluster (bez /api na konci)
    "ARISTA_AGNI_URL": "https://ag02w03.agni.arista.io",

    # loginName/email guest uživatele, kterému budeme rotovat heslo
    "GUEST_LOGIN": "test-guest@altepro.cz",

    # SSID, které bude v QR kódu (open WiFi, bez PSK)
    "SSID_PROFILE_NAME": "TSK-Guest",

    "BACKEND_PORT": 8081,
    "ROTATION_HOUR": 2,
    "ROTATION_MINUTE": 0,
    # pro testování lze manuálně přidat TEST_ROTATION_EVERY_MINUTES do configu
    "LOG_LEVEL": "INFO",    # INFO / DEBUG / WARNING / ERROR
    "VERIFY_SSL": True      # false = vypne ověřování TLS (NEBEZPEČNÉ)
}


def ensure_windows():
    if sys.platform != "win32":
        print("Tenhle deploy script je určený jen pro Windows.")
        sys.exit(1)


def write_config():
    if CONFIG_PATH.exists():
        print(f"config.json už existuje, nechávám ho být ({CONFIG_PATH})")
        return

    print(f"Vytvářím config.json v {CONFIG_PATH}")
    CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    print("config.json hotový.")
    print()
    print("POZOR:")
    print("  - Uprav v config.json hodnoty ARISTA_AGNI_URL, GUEST_LOGIN, SSID_PROFILE_NAME")
    print("  - VERIFY_SSL ponech na true, pokud máš validní certifikát.")


def configure_registry_credentials():
    """
    Uloží AGNI_KEY_ID a AGNI_KEY_VALUE do:
      HKLM\\SOFTWARE\\AristaGuestPortal   (REG_SZ)

    AGNI_KEY_ID    = keyID z Launchpadu (např. KEY-ATN570317-3494)
    AGNI_KEY_VALUE = keyValue z Launchpadu

    POZOR: musíš mít admin práva (zapisujeme do HKLM).
    """
    print()
    print(f"=== AGNI API credentials do Windows Registry (HKLM\\{REG_PATH}) ===")

    # Zkusíme zjistit, jestli už něco v registru je
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            REG_PATH,
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        )
        existing_id, _ = winreg.QueryValueEx(key, REG_VALUE_KEY_ID)
        existing_val, _ = winreg.QueryValueEx(key, REG_VALUE_KEY_VALUE)
        winreg.CloseKey(key)
    except FileNotFoundError:
        existing_id = existing_val = ""

    if existing_id and existing_val:
        print("Už existují credentials v HKLM\\{0}:".format(REG_PATH))
        print(f"  {REG_VALUE_KEY_ID} = {existing_id}")
        print("  {0} = ********".format(REG_VALUE_KEY_VALUE))
        ans = input("Ponechat existující hodnoty? [Y/n]: ").strip().lower()
        if ans in ("", "y", "yes"):
            print("Ponechávám existující credentials.")
            return
        else:
            print("Přepisuji credentials novými hodnotami.")

    print()
    print("Tyto hodnoty budou uloženy jako prostý text v registru,")
    print("takže doporučuji omezit ACL na klíči jen pro Administrators + účet služby.")
    print()

    key_id = input("Zadej AGNI KEY_ID (např. KEY-ATNxxxxxx-xxxx): ").strip()
    key_value = getpass.getpass("Zadej AGNI KEY_VALUE (nebude se zobrazovat): ").strip()

    if not key_id or not key_value:
        print("ERROR: AGNI_KEY_ID i AGNI_KEY_VALUE musí být vyplněné.")
        sys.exit(1)

    # create / open HKLM\Software\AristaAgni
    try:
        key = winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE,
            REG_PATH,
            0,
            winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY,
        )
    except PermissionError:
        print()
        print("ERROR: Nemám právo zapisovat do HKLM – spusť deploy jako Administrator.")
        sys.exit(1)

    winreg.SetValueEx(key, REG_VALUE_KEY_ID, 0, winreg.REG_SZ, key_id)
    winreg.SetValueEx(key, REG_VALUE_KEY_VALUE, 0, winreg.REG_SZ, key_value)
    winreg.CloseKey(key)

    print(f"Credentials uloženy do HKLM\\{REG_PATH}.")


def check_pywin32():
    print("\nKontroluji pywin32 (win32serviceutil)...")
    try:
        import win32serviceutil  # noqa: F401
    except Exception as e:
        print("ERROR: pywin32 není nainstalované nebo nejde importnout.")
        print(f"Detail: {e}")
        print("Nainstaluj ho do systémového Pythonu, např.:")
        print("  py -3.12 -m pip install pywin32")
        sys.exit(1)
    print("pywin32 OK.")


def install_service(script: Path, svc_name: str):
    if not script.is_file():
        print(f"ERROR: {script} neexistuje, nemůžu nainstalovat službu {svc_name}.")
        sys.exit(1)

    print(f"\n=== Instalace Windows služby {svc_name} ===")
    print(f"Používám interpreter: {sys.executable}")

    # install
    subprocess.check_call([sys.executable, str(script), "install"])

    # start – když to spadne, neukončuj deploy
    try:
        subprocess.check_call([sys.executable, str(script), "start"])
    except subprocess.CalledProcessError as e:
        print(f"POZOR: start služby {svc_name} hlásí chybu: {e}")
        print("Zkontroluj services.msc a logy v logs/.")


def initial_rotate():
    """
    Provede první rotaci guest hesla, aby web měl data v data/current_guest_pass.json.
    """
    print("\n=== Spouštím počáteční rotaci guest hesla ===")
    try:
        # Importujeme až tady, aby měl config + registry vše připravené
        from rotate_guest_user_pass import rotate_once
    except Exception as e:
        print("ERROR: Nelze importovat rotate_guest_user_pass.rotate_once:")
        print(f"  {e}")
        return

    try:
        ok = rotate_once()
    except Exception as e:
        print("ERROR: Počáteční rotace selhala výjimkou:")
        print(f"  {e}")
        return

    if ok:
        print("Počáteční rotace guest hesla proběhla úspěšně.")
    else:
        print("POZOR: Počáteční rotace guest hesla SELHALA – zkontroluj logy.")
        print("  - logs/rotate_guest_user_pass.log")


def main():
    ensure_windows()

    print("=== Arista Guest Portal – deploy (SYSTEM PYTHON) ===")
    print(f"BASE_DIR = {BASE_DIR}")
    print(f"Použitý interpreter: {sys.executable}")
    print("Doporučení: spusť to jako admin na cílovém serveru.")

    write_config()
    configure_registry_credentials()
    check_pywin32()

    # dvě oddělené služby
    install_service(BASE_DIR / "rotate_guest_user_pass_service.py", "AristaGuestPassRotate")
    install_service(BASE_DIR / "web_server_service.py", "AristaGuestPortalWeb")

    # počáteční rotace, aby měl web JSON / QR
    initial_rotate()

    print("\n================================================")
    print("Hotovo.")
    print("- config.json je vytvořený (pokud už nebyl)")
    print(f"- AGNI_KEY_ID / AGNI_KEY_VALUE jsou v HKLM\\{REG_PATH}")
    print("- služby AristaGuestPassRotate & AristaGuestPortalWeb jsou nainstalované")
    print("Logy rotate služby:   logs/service_rotate.log")
    print("Logy rotace hesla:    logs/rotate_guest_user_pass.log")
    print("Logy web služby:      logs/service_web.log")
    print("Logy Flask webu:      logs/web.log")
    print("================================================")


if __name__ == "__main__":
    main()
