# DAG: supplier_feed_ingest. Waits for the supplier's nightly file, then loads it.
from datetime import datetime

from airflow.sdk import DAG
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.standard.sensors.filesystem import FileSensor

from acme_etl.suppliers import load_feed

with DAG(
    dag_id="supplier_feed_ingest",
    start_date=datetime(2026, 8, 1),
    schedule="0 1 * * *",
    default_args={"owner": "supply-chain-data", "retries": 1},
    catchup=False,
) as dag:
    wait_for_supplier_file = FileSensor(
        task_id="wait_for_supplier_file",
        filepath="/data/inbound/supplier_feed.csv",
        mode="reschedule",
        poke_interval=30,
        timeout=60,  # give up after one minute
    )
    load_supplier_feed = PythonOperator(task_id="load_supplier_feed", python_callable=load_feed)
    wait_for_supplier_file >> load_supplier_feed
