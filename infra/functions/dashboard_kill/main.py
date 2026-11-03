"""Entry point of the dashboard-kill Cloud Run function (v3 slice 4, FR24): Eventarc delivers each Pub/Sub message of the
dashboard budget topic here; kill.py decides and acts. See notes/eg-new-feature/v3-slice4-cloud-run-deploy-2026-11-03.md §8."""

import logging

import functions_framework
import kill

# Without a handler Python's lastResort passes only WARNING and up, so the INFO outcome lines would never reach Cloud Logging
# (which collects the function's stderr).
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


@functions_framework.cloud_event
def on_budget_message(cloud_event):
    kill.handle(cloud_event.data)
