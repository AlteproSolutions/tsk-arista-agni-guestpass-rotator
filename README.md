# Arista AGNI Guest Portal – Password Rotator + Web UI

Nástroj pro **rotaci hesla jednoho Guest účtu v Arista CloudVision AGNI**  
a jednoduché **web UI** s QR kódem + přihlašovacími údaji.

Repozitář obsahuje:

- `rotate_guest_user_pass.py` – logika rotace hesla přes AGNI API + generování QR a `data/current_guest_pass.json`
- `status_server.py` – Flask web server pro zobrazení SSID / guest username / password / QR
- `rotate_guest_user_pass_service.py` – Windows služba pro plánovanou rotaci guest hesla
- `web_server_service.py` – Windows služba pro web UI
- `deploy.py` – instalační skript (vytvoří config, uloží AGNI API klíče do registrů, zaregistruje služby, spustí počáteční rotaci)
- `config_template.json` – šablona konfigurace (volitelně)
- `requirements.txt` – Python závislosti

> ❗ Všechno níže počítá s **Windows Server 2022/2025** a **Pythonem 3.12** nainstalovaným přes *Python Installation Manager* (`py`).

---

## 1. Jak získat soubory z GitHubu

Na cílovém serveru:

1. Otevři repozitář v prohlížeči (GitHub).
2. Klikni na **Code → Download ZIP**.
3. ZIP rozbal třeba do  
   `C:\AristaGuestPortal` nebo `C:\Users\<user>\Downloads\AGNI_GUEST`.

V návodu budu adresář nazývat prostě **`C:\AristaGuestPortal`**.

---

## 2. Přehled architektury

### 2.1 Rotace guest hesla (`rotate_guest_user_pass.py`)

Skript dělá:

1. Pomocí **Launchpad keyLogin** endpointu (`/cvcue/keyLogin`) získá z AGNI **session cookie**  
   – používá `keyID` / `keyValue` uložené v **HKLM\Software\AristaAgni**.
2. Přes `/api/org.info` zjistí `orgID`.
3. Přes `/api/identity.guest.user.list` načte seznam guest uživatelů a najde toho,  
   jehož `loginName` nebo `email` = `GUEST_LOGIN` z `config.json`.
4. Vygeneruje nové heslo ve formátu:

   ```text
   Slovo-Slovo7    (např. Grow-Customer0)
   ```

   - 2 anglická slova (top frekventovaná, `wordfreq`), první písmeno velké  
   - pomlčka mezi slovy  
   - na konci jedna číslice

5. Přes `/api/identity.guest.user.update` nastaví nové heslo (ostatní atributy kopíruje z list response).
6. Uloží stav do `data/current_guest_pass.json`:

   ```json
   {
     "ssid": "TSK-Guest",
     "guest_login": "test-guest@altepro.cz",
     "guest_password": "Grow-Customer0",
     "last_rotated_utc": "2025-12-17T12:11:40.000000+00:00",
     "qr_image": "wifi_qr_TSK-Guest.png"
   }
   ```

7. Vytvoří QR PNG `data/wifi_qr_<SSID>.png` s Wi-Fi payloadem pro **open SSID**:

   ```text
   WIFI:T:nopass;S:<SSID>;H:false;;
   ```

### 2.2 Web UI (`status_server.py`)

- Čte `data/current_guest_pass.json`.
- Na HTTP portu (z `config.json`, default **8081**) renderuje kartu:

  - SSID (open, bez hesla)
  - **Guest portal username** (ve výrazném badge)
  - **Guest portal password** (ve výrazném badge)
  - QR kód pro připojení k Wi-Fi SSID
  - „Last rotated: … UTC“

- Má interní endpoint `POST /_internal_shutdown`, který používá Windows služba k čistému ukončení Flasku.

### 2.3 Windows služby

- `AristaGuestPassRotate`  
  – periodicky volá `rotate_once()` z `rotate_guest_user_pass.py` (podle plánu v `config.json`).

- `AristaGuestPortalWeb`  
  – spouští Flask server ze `status_server.py` v samostatném vlákně
  – při stopu zavolá `/_internal_shutdown` a počká na ukončení.

### 2.4 AGNI API credentials

Launchpad API klíče jsou uložené v registru:

```text
HKLM\Software\AristaAgni
  AGNI_KEY_ID    (REG_SZ)  – keyID, např. KEY-ATN570317-3494
  AGNI_KEY_VALUE (REG_SZ)  – keyValue
```

Skript `rotate_guest_user_pass.py` je čte přes `winreg` – v `config.json` se **neukládají**.

---

## 3. Doporučené zabezpečení

### 3.1 Service account

Doporučeno vytvořit dedikovaný účet, např.:

```bat
net user svc_agni_guest SuperSilneHeslo123! /add
```

V **Local Security Policy** (`secpol.msc`) nastav:

- **Local Policies → User Rights Assignment → Log on as a service**  
  → přidej `.\svc_agni_guest` (případně doménový účet).

### 3.2 Práva na složku aplikace

Na `C:\AristaGuestPortal`:

- `Administrators` – Full control
- `svc_agni_guest` – Modify (čtení + zápis – logy, data)
- pokud možno odeber `Users` / `Everyone`.

### 3.3 Práva na registry

Klíč:

```text
HKLM\Software\AristaAgni
```

Doporučené ACL:

- `Administrators` – Full control
- `svc_agni_guest` – Read

Hodnoty `AGNI_KEY_ID` / `AGNI_KEY_VALUE` jsou **prostý text**, proto omez přístup pouze na adminy + service account.

---

## 4. Instalace Pythonu pro service account

### 4.1 První přihlášení jako service account

Poprvé je vhodné se přihlásit **přímo jako** `svc_agni_guest`:

- RDP, nebo lokálně přes „Switch user → Other user“.

Pak se Python i `pip` nainstalují do profilu tohoto uživatele.

### 4.2 Instalace Pythonu 3.12

V PowerShellu / CMD:

```bat
py install 3.12
py -0p
```

Měl bys vidět něco jako:

```text
 -V:3.12[-64]   C:\Users\svc_agni_guest\AppData\Local\Python\pythoncore-3.12-64\python.exe
```

### 4.3 Instalace Python závislostí

```bat
cd C:\AristaGuestPortal
py -3.12 -m pip install -r requirements.txt
```

Nainstaluje se např.:

- `requests`
- `flask`
- `pywin32`
- `qrcode`
- `wordfreq`
- atd.

---

## 5. Konfigurace `config.json`

Po prvním spuštění `deploy.py` se vytvoří `config.json` (pokud už neexistuje). Typická šablona:

```json
{
  "ARISTA_AGNI_URL": "https://ag02w03.agni.arista.io",
  "GUEST_LOGIN": "test-guest@altepro.cz",
  "SSID_PROFILE_NAME": "TSK-Guest",
  "BACKEND_PORT": 8081,
  "ROTATION_HOUR": 2,
  "ROTATION_MINUTE": 0,
  "TEST_ROTATION_EVERY_MINUTES": 0,
  "LOG_LEVEL": "INFO",
  "VERIFY_SSL": true
}
```

Vysvětlení:

- `ARISTA_AGNI_URL` – URL AGNI clusteru (bez `/api` na konci).
- `GUEST_LOGIN` – `loginName` nebo `email` guest účtu, kterému budeme rotovat heslo.
- `SSID_PROFILE_NAME` – SSID, které se zobrazí na webu a v QR (SSID je **open**, bez hesla).
- `BACKEND_PORT` – port web UI (Flask server).
- `ROTATION_HOUR`, `ROTATION_MINUTE` – denní plán rotace (lokální čas serveru).
- `TEST_ROTATION_EVERY_MINUTES` – **testovací režim** (např. `2` = každé 2 minuty).  
  Pro produkci nastav `0` nebo položku vynech.
- `LOG_LEVEL` – `INFO` / `DEBUG` / `WARNING` / `ERROR`.
- `VERIFY_SSL` – `true` = ověřovat TLS certifikáty AGNI (doporučeno),  
  `false` = vypne ověřování (jen pro lab, ne do produkce).

---

## 6. Uložení AGNI API klíčů do registru + deploy

Stále přihlášen jako **`svc_agni_guest`**:

```bat
cd C:\AristaGuestPortal
py -3.12 deploy.py
```

Skript:

1. Vytvoří `config.json` z `DEFAULT_CONFIG`, pokud ještě neexistuje.
2. Zeptá se na:

   - `AGNI KEY_ID` (např. `KEY-ATN570317-3494`)
   - `AGNI KEY_VALUE`

3. Uloží je do:

   ```text
   HKLM\Software\AristaAgni
     AGNI_KEY_ID
     AGNI_KEY_VALUE
   ```

   (pokud už existují, nabídne ponechání / přepsání).

4. Zkontroluje, že je k dispozici `pywin32`.
5. Zaregistruje Windows služby:

   - `AristaGuestPassRotate` (soubor `rotate_guest_user_pass_service.py`)
   - `AristaGuestPortalWeb` (soubor `web_server_service.py`)

6. Pokusí se je rovnou spustit.
7. Spustí **počáteční rotaci** (`rotate_once()`), aby vznikl `data/current_guest_pass.json` a QR PNG.

---

## 7. Nastavení služeb

Po `deploy.py` najdeš v **services.msc**:

- **Arista Guest Portal Password Rotation Service** (`AristaGuestPassRotate`)
- **Arista Guest Portal Web UI** (`AristaGuestPortalWeb`)

Zkontroluj u obou:

1. **Properties → Log On**:

   - účet: `.\svc_agni_guest`
   - heslo: stejné jako při `net user ...`

2. **Startup type**:

   - při prvním testování klidně **Manual**
   - po ověření přepni na **Automatic**.

---

## 8. Ověření provozu

### 8.1 Testovací režim rotace

Pro lab/test nastav v `config.json`:

```json
"TEST_ROTATION_EVERY_MINUTES": 2
```

Restartuj službu:

```bat
net stop AristaGuestPassRotate
net start AristaGuestPassRotate
```

Sleduj logy:

- `logs/service_rotate.log`
- `logs/rotate_guest_user_pass.log`

Očekávané zprávy:

```text
... Service loop starting — mode=interval, next_run=...
... Starting scheduled guest password rotation…
... Generated new guest password for test-guest@altepro.cz: Grow-Customer0
... Guest user password updated successfully
... State saved: ssid=TSK-Guest, guest_login=test-guest@altepro.cz, ...
```

### 8.2 Web UI

Zkontroluj, že běží služba **AristaGuestPortalWeb**.

V prohlížeči (na serveru nebo z LAN):

```text
http://<server-name>:8081/
```

Uvidíš kartu:

- **SSID** (např. `TSK-Guest`)
- **Guest portal username** (v badge)  
- **Guest portal password** (v badge, tvar `Word-Word7`)
- **QR kód** – otevřená Wi-Fi
- `Last rotated: YYYY-MM-DD HH:MM:SS UTC`

---

## 9. Produkční nastavení

Až přestaneš testovat:

1. V `config.json`:

   ```json
   "TEST_ROTATION_EVERY_MINUTES": 0,
   "ROTATION_HOUR": 2,
   "ROTATION_MINUTE": 0
   ```

2. Restartuj **AristaGuestPassRotate**:

   ```bat
   net stop AristaGuestPassRotate
   net start AristaGuestPassRotate
   ```

3. V `logs/service_rotate.log` ověř, že běží režim `daily` a plán odpovídá.

---

## 10. Firewall

Povol příchozí HTTP port pro web UI (dle `BACKEND_PORT`):

```bat
netsh advfirewall firewall add rule name="Arista Guest Portal Web UI" dir=in action=allow protocol=TCP localport=8081
```

---

## 11. Odinstalace

### 11.1 Zastavení a odstranění služeb

V adresáři aplikace:

```bat
cd C:\AristaGuestPortal
py -3.12 rotate_guest_user_pass_service.py remove
py -3.12 web_server_service.py remove
```

Nebo:

```bat
sc delete AristaGuestPassRotate
sc delete AristaGuestPortalWeb
```

### 11.2 Smazání registru

V RegEditu:

- smaž klíč `HKLM\Software\AristaAgni`.

### 11.3 Smazání aplikace

- smaž složku `C:\AristaGuestPortal`  
  (případně předtím archivuj logy v `logs\`).

---

## 12. Troubleshooting

### 12.1 Služba nejde spustit – Error 5: Access is denied

Zkontroluj:

- `svc_agni_guest` má právo **“Log on as a service”**.
- účet má **Read** na `HKLM\Software\AristaAgni`.
- účet má **Modify** na `C:\AristaGuestPortal` (logy, data).

### 12.2 Služba běží, ale heslo se nemění

- Podívej se do `logs/rotate_guest_user_pass.log` – typické chyby:

  - špatné `AGNI_KEY_ID` / `AGNI_KEY_VALUE`
  - špatné `ARISTA_AGNI_URL`
  - špatný `GUEST_LOGIN` (uživatel se nenajde v prvních 50 guest users)

### 12.3 Web UI ukazuje “WiFi status is not available yet”

- Zkontroluj, že proběhla aspoň jedna úspěšná rotace:
  - musí existovat `data/current_guest_pass.json`
  - musí existovat `data/wifi_qr_<SSID>.png`.

- Pokud ne, ručně spusť:

  ```bat
  py -3.12 rotate_guest_user_pass.py
  ```

  a sleduj `logs/rotate_guest_user_pass.log`.

---

Pokud je potřeba řešit více guest účtů nebo více SSID / portálů,  
řeší se to typicky víc instancemi nástroje nebo rozšířením skriptu –
to už je ale na separátní design.
