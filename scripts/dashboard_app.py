"""Entrypoint of the dashboard: `streamlit run scripts/dashboard_app.py` (v3 slice 3, owner decision S11). Kept outside the
package so `streamlit run` puts scripts/ on sys.path, not src/readmission_risk/dashboard/ (whose module names would shadow
top-level imports). The app is readmission_risk.dashboard.app."""

from readmission_risk.dashboard.app import main

main()
