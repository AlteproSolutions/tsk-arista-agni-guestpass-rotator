import sys
import logging
from pathlib import Path

import win32event
import win32service
import win32serviceutil
import servicemanager

# ---------------------------------------------------------------------------
# Paths and Logging
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

SERVICE_LOG = LOGS_DIR / "service_web.log"

logger = logging.getLogger("AristaPskWebService")
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

import legacy.status_server as status_server  # noqa: E402


# ---------------------------------------------------------------------------
# Windows Service Implementation
# ---------------------------------------------------------------------------

class AristaPskWebService(win32serviceutil.ServiceFramework):
    _svc_name_ = "AristaPskWeb"
    _svc_display_name_ = "Arista WiFi PSK Web UI"
    _svc_description_ = (
        "Serves a simple WiFi QR / passphrase page from data/current_psk.json via Flask."
    )

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)

    def SvcStop(self):
        logger.info("Web service stop requested")
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        logger.info("Web service stop signalled")
        # Flask dev server nemá API na čisté ukončení – SCM proces ukončí
        # po návratu z této metody.

    def SvcDoRun(self):
        logger.info("Web service starting (SvcDoRun)")
        servicemanager.LogInfoMsg("AristaPskWeb service starting")

        try:
            logger.info("Starting Flask app (status_server.main())")
            status_server.main()  # blokující Flask app.run()
        except Exception as e:  # pragma: no cover
            logger.exception("Fatal error inside status_server.main(): %s", e)
            raise
        finally:
            logger.info("Web service exited")
            servicemanager.LogInfoMsg("AristaPskWeb service stopped")


# ---------------------------------------------------------------------------
# Entry point (install, start, stop, remove, ...)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(AristaPskWebService)
