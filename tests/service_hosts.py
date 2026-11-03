"""The Cloud Run hosts of the dashboard service (v3 slice 4, Interfaces §12 in
notes/eg-new-feature/v3-slice4-cloud-run-deploy-2026-11-03.md): server.allowedHosts minus the dev hosts (owner decision S9),
and the hosts deploy.yml, ci.yml's dashboard-image job and the Makefile's DASHBOARD_URLS check. Streamlit-free, so the Python 3.12
pipeline-dataproc-runtime job (no streamlit) can collect tests/scripts. The legacy host joins at L2 (F6)."""

SERVICE_HOSTS = ("dashboard-271458030145.us-central1.run.app",)
