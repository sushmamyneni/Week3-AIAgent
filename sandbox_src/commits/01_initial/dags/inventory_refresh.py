# DAG: inventory_refresh. Rebuilds the inventory snapshot used by the supply chain dashboards.
from datetime import datetime, timedelta

from airflow.sdk import DAG
from airflow.providers.standard.operators.python import PythonOperator

from acme_etl.inventory import build_snapshot, publish_snapshot

default_args = {"owner": "supply-chain-data", "retries": 1, "retry_delay": timedelta(minutes=10)}

with DAG(
    dag_id="inventory_refresh",
    start_date=datetime(2026, 8, 1),
    schedule="0 4 * * *",
    default_args=default_args,
    catchup=False,
) as dag:
    build_inventory_snapshot = PythonOperator(
        task_id="build_inventory_snapshot",
        python_callable=build_snapshot,  # loads the whole stock_levels table into memory
    )
    publish_inventory = PythonOperator(task_id="publish_inventory", python_callable=publish_snapshot)
    build_inventory_snapshot >> publish_inventory
