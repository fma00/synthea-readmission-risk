"""The dashboard (PRD v3): reads BigQuery only through the authorized views of the `dashboard` dataset. v3 slice 2 added its
fixed queries (queries.py); slice 3 the Streamlit app (app.py, run as `streamlit run scripts/dashboard_app.py`) and its pure
helpers. Never imports mlflow, pyspark, sklearn, xgboost, matplotlib, the bundles, the scorer's pipeline or the models, and
never unpickles (PRD FR18; tests/dashboard/test_import_boundary.py).
"""
