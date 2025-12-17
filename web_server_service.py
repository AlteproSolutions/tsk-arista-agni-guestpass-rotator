import sys
import logging
import threading
from pathlib import Path

import win32event
import win32service
import win32serviceutil
import servicemanager
import requests

# ---------------------------------------------------------------------------
# Paths and Logging
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

SERVICE_LOG = LOGS_DIR / "service_web.log"

logger = logging.getLogger("AristaGuestPortalWebService")
logger.setLevel(logging.INFO)

if not logger.handlers:
    fh = logging.FileHandler(SERVICE_LOG, encoding="utf-8")
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

# potlačit spam z werkzeugu (access log)
werkzeug_logger = logging.getLogger("werkzeug")
werkzeug_logger.setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Add project root to sys.path so we can import status_server
# ---------------------------------------------------------------------------

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import status_server  # noqa: E402


# ---------------------------------------------------------------------------
# Windows Service Implementation
# ---------------------------------------------------------------------------

class AristaGuestPortalWebService(win32serviceutil.ServiceFramework):
    _svc_name_ = "AristaGuestPortalWeb"
    _svc_display_name_ = "Arista Guest Portal Web UI"
    _svc_description_ = (
        "Serves a simple guest WiFi QR / login page "
        "from data/current_guest_pass.json via Flask."
    )

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.flask_thread: threading.Thread | None = None
        self.port: int | None = None

    def SvcStop(self):
        logger.info("Web service stop requested")
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        logger.info("Web service stop signalled")

    def SvcDoRun(self):
        logger.info("Web service starting (SvcDoRun)")
        servicemanager.LogInfoMsg("AristaGuestPortalWeb service starting")

        # Zjistíme port z configu přes status_server
        self.port = status_server.load_config_port()
        logger.info("Flask will listen on port %s", self.port)

        def run_flask():
            try:
                logger.info("Starting Flask app on port %s", self.port)
                status_server.app.run(host="0.0.0.0", port=self.port)
            except Exception as e:  # pragma: no cover
                logger.exception("Fatal error inside Flask app.run(): %s", e)

        # Spustíme Flask v separátním threadu
        self.flask_thread = threading.Thread(
            target=run_flask,
            name="FlaskThread",
            daemon=True,
        )
        self.flask_thread.start()

        # Čekáme na stop event
        rc = win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)
        logger.info("Stop event received in SvcDoRun (rc=%s)", rc)

        # Pokusíme se Flask korektně vypnout přes interní endpoint
        if self.port is not None:
            try:
                url = f"http://127.0.0.1:{self.port}/_internal_shutdown"
                logger.info("Calling internal shutdown URL %s", url)
                requests.post(url, timeout=5)
            except Exception as e:
                logger.warning("Error calling internal shutdown: %s", e)

        if self.flask_thread and self.flask_thread.is_alive():
            logger.info("Waiting for Flask thread to exit…")
            self.flask_thread.join(timeout=15)
            if self.flask_thread.is_alive():
                logger.warning("Flask thread still alive after join timeout")

        logger.info("Web service exited")
        servicemanager.LogInfoMsg("AristaGuestPortalWeb service stopped")


# ---------------------------------------------------------------------------
# Entry point (install, start, stop, remove, ...)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(AristaGuestPortalWebService)
