# DAG: marketing_export. Exports the customer list for the weekly email campaign.
from datetime import datetime

from airflow.sdk import DAG
from airflow.providers.standard.operators.python import PythonOperator

from acme_etl.loaders import run_sql_file

with DAG(
    dag_id="marketing_export",
    start_date=datetime(2026, 8, 1),
    schedule="0 7 * * 1",
    default_args={"owner": "marketing-data", "retries": 1},
    catchup=False,
) as dag:
    export_customers = PythonOperator(
        task_id="export_customers",
        python_callable=run_sql_file,
        op_kwargs={"path": "sql/marketing_export.sql", "conn_id": "warehouse"},
    )
