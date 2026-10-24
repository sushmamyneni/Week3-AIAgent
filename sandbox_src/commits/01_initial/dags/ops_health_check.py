# DAG: ops_health_check. Weekly report on table bloat in the warehouse.
from datetime import datetime

from airflow.sdk import DAG
from airflow.providers.standard.operators.python import PythonOperator

from acme_etl.loaders import run_sql_file

with DAG(
    dag_id="ops_health_check",
    start_date=datetime(2026, 8, 1),
    schedule="0 5 * * 0",
    default_args={"owner": "data-platform", "retries": 0},
    catchup=False,
) as dag:
    table_health_check = PythonOperator(
        task_id="table_health_check",
        python_callable=run_sql_file,
        op_kwargs={"path": "sql/health_check.sql", "conn_id": "warehouse", "statement_timeout_s": 120},
    )
