import json
import sys
import subprocess
from pathlib import Path
import getpass
import winreg

# Základní cesty
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

# Registry cesta pro WM credentials (on-prem)
REG_PATH = r"SOFTWARE\AristaPskRotator"

# Výchozí konfigurace (on-prem varianta)
DEFAULT_CONFIG = {
    "WM_BASE_URL": "https://10.0.0.1",  # TODO: uprav podle on-prem WM
    "WM_LOCATION_ID": 0,                # vyplňte konkrétní location_id
    "WM_NODE_ID": 0,
    "SSID_PROFILE_NAME": "TSK_TEST",
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
    print("  - Uprav v config.json hodnoty WM_BASE_URL, WM_LOCATION_ID, WM_NODE_ID, SSID_PROFILE_NAME")
    print("  - VERIFY_SSL ponech na true, pokud máš validní certifikát.")


def configure_registry_credentials():
    """
    Uloží WM_KEY_ID a WM_KEY_VALUE do:
      HKLM\\SOFTWARE\\AristaPskRotator   (REG_SZ)

    On-prem:
      WM_KEY_ID    = username (např. api_user)
      WM_KEY_VALUE = password
    POZOR: musíš mít admin práva (zapisujeme do HKLM).
    """
    print()
    print("=== API credentials do Windows Registry (HKLM\\SOFTWARE\\AristaPskRotator) ===")
    print("Tyto hodnoty budou uloženy jako prostý text v registru,")
    print("takže doporučuji omezit ACL na klíči jen pro Administrators + účet služby.")
    print("Na on-prem instalaci zadej normální username / password pro WM API usera.")
    print()

    key_id = input("Zadej WM_KEY_ID (username pro WM): ").strip()
    key_value = getpass.getpass("Zadej WM_KEY_VALUE (password, nebude se zobrazovat): ").strip()

    if not key_id or not key_value:
        print("ERROR: WM_KEY_ID i WM_KEY_VALUE musí být vyplněné.")
        sys.exit(1)

    # create / open HKLM\SOFTWARE\AristaPskRotator
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

    winreg.SetValueEx(key, "WM_KEY_ID", 0, winreg.REG_SZ, key_id)
    winreg.SetValueEx(key, "WM_KEY_VALUE", 0, winreg.REG_SZ, key_value)
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


def main():
    ensure_windows()

    print("=== Arista PSK – deploy (SYSTEM PYTHON) ===")
    print(f"BASE_DIR = {BASE_DIR}")
    print(f"Použitý interpreter: {sys.executable}")
    print("Doporučení: spusť to jako admin na cílovém serveru.")

    write_config()
    configure_registry_credentials()
    check_pywin32()

    # dvě oddělené služby
    install_service(BASE_DIR / "arista_psk_rotator_service.py", "AristaPskRotate")
    install_service(BASE_DIR / "arista_psk_web_service.py", "AristaPskWeb")

    print("\n================================================")
    print("Hotovo.")
    print("- config.json je vytvořený (pokud už nebyl)")
    print(f"- WM_KEY_ID / WM_KEY_VALUE jsou v HKLM\\{REG_PATH}")
    print("- služby AristaPskRotate & AristaPskWeb jsou nainstalované")
    print("Logy služby rotátoru: logs/service_rotate.log")
    print("Logy rotace PSK:      logs/rotate.log")
    print("Logy web služby:      logs/service_web.log")
    print("Logy Flask webu:      logs/web.log")
    print("================================================")


if __name__ == "__main__":
    main()
