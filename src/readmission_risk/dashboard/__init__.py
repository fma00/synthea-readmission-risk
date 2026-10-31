"""The dashboard (PRD v3): reads BigQuery only through the authorized views of the `dashboard` dataset. v3 slice 2 adds its fixed
queries (queries.py); slice 3 adds the Streamlit app. Never imports mlflow, pyspark, sklearn, xgboost, the bundles, the scorer's
pipeline or the models, and never unpickles (PRD FR18; tests/dashboard/test_import_boundary.py).
"""
