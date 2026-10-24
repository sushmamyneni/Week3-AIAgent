# DAG: customer_segments_refresh. Recomputes each customer's segment from the last 90 days of orders.
from datetime import datetime

from airflow.sdk import DAG
from airflow.providers.standard.operators.python import PythonOperator

from acme_etl.loaders import run_sql_file

with DAG(
    dag_id="customer_segments_refresh",
    start_date=datetime(2026, 10, 21),
    schedule="0 3 * * *",
    default_args={"owner": "marketing-data", "retries": 1},
    catchup=False,
) as dag:
    refresh_segments = PythonOperator(
        task_id="refresh_segments",
        python_callable=run_sql_file,
        op_kwargs={"path": "sql/customer_segments.sql", "conn_id": "warehouse"},
    )
