# DAG: finance_snowflake_sync. Pushes the finance summary tables to the Snowflake reporting warehouse.
from datetime import datetime

from airflow.sdk import DAG
from airflow.providers.standard.operators.python import PythonOperator

from acme_etl.finance import push_to_snowflake

with DAG(
    dag_id="finance_snowflake_sync",
    start_date=datetime(2026, 8, 1),
    schedule="0 8 * * *",
    default_args={"owner": "finance-data", "retries": 1},
    catchup=False,
) as dag:
    push_finance_summary = PythonOperator(
        task_id="push_finance_summary",
        python_callable=push_to_snowflake,
        op_kwargs={"conn_id": "snowflake_finance"},
    )
