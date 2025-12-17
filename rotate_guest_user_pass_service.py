import win32serviceutil
import win32service
import win32event

import logging
import sys
from pathlib import Path
from datetime import datetime, timedelta

from rotate_guest_user_pass import BASE_DIR, rotate_once, load_config

LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

SERVICE_LOG = LOGS_DIR / "service_rotate.log"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=[
            logging.FileHandler(SERVICE_LOG, encoding="utf-8"),
        ],
    )


setup_logging()
logger = logging.getLogger("AristaGuestPassRotateService")


# ---------------------------------------------------------------------------
# Schedule handling
# ---------------------------------------------------------------------------

def get_schedule_from_config():
    """
    Vrátí tuple (mode, value)

    mode == "interval"  → value = timedelta
    mode == "daily"     → value = (hour, minute)
    """
    try:
        cfg = load_config()
    except Exception as e:
        logger.error("Cannot load config.json, using fallback daily at 02:00: %s", e)
        return "daily", (2, 0)

    # TEST MODE – TEST_ROTATION_EVERY_MINUTES
    interval_minutes = int(cfg.get("TEST_ROTATION_EVERY_MINUTES", 0) or 0)
    if interval_minutes > 0:
        logger.info("Using test rotation interval every %s minutes", interval_minutes)
        return "interval", timedelta(minutes=interval_minutes)

    # STANDARD MODE – daily time
    hour = int(cfg.get("ROTATION_HOUR", 2))
    minute = int(cfg.get("ROTATION_MINUTE", 0))

    logger.info("Using daily rotation at %02d:%02d", hour, minute)
    return "daily", (hour, minute)


def compute_next_run(mode, value, from_time=None):
    now = from_time or datetime.now()

    if mode == "interval":
        return now + value

    hour, minute = value
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)

    return target


# ---------------------------------------------------------------------------
# Windows Service implementation
# ---------------------------------------------------------------------------

class AristaGuestPassRotateService(win32serviceutil.ServiceFramework):
    _svc_name_ = "AristaGuestPassRotate"
    _svc_display_name_ = "Arista Guest Portal Password Rotation Service"
    _svc_description_ = (
        "Rotuje guest portal heslo v Arista CloudVision AGNI "
        "a ukládá SSID / guest účet do složky data/ podle konfigurace."
    )

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.is_running = False

    def SvcStop(self):
        logger.info("Service stop requested")
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.is_running = False
        win32event.SetEvent(self.stop_event)
        logger.info("Service stop signalled")

    def SvcDoRun(self):
        setup_logging()
        logger.info("Service starting (SvcDoRun)")
        self.is_running = True
        self.main()
        logger.info("Service main() exited")

    def main(self):
        mode, value = get_schedule_from_config()
        next_run = compute_next_run(mode, value)

        logger.info(
            "Service loop starting — mode=%s, next_run=%s",
            mode,
            next_run.isoformat(),
        )

        while self.is_running:
            now = datetime.now()

            if now >= next_run:
                logger.info("Starting scheduled guest password rotation…")
                ok = rotate_once()

                if not ok:
                    logger.error("Guest password rotation FAILED — see rotate_guest_user_pass.log")
                else:
                    logger.info("Guest password rotation completed successfully")

                next_run = compute_next_run(mode, value, from_time=datetime.now())
                logger.info("Next rotation planned at %s", next_run.isoformat())

            # Sleep at most 60 seconds to react to stop events
            wait_seconds = max(
                1, min(60, int((next_run - datetime.now()).total_seconds()))
            )
            rc = win32event.WaitForSingleObject(self.stop_event, wait_seconds * 1000)
            if rc == win32event.WAIT_OBJECT_0:
                logger.info("Service stop detected — exiting loop")
                break


# ---------------------------------------------------------------------------
# Entry point for manual control
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(AristaGuestPassRotateService)
