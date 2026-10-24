# DAG: daily_sales_report. Builds the daily sales report and publishes it to the finance portal.
from datetime import datetime

from airflow.sdk import DAG
from airflow.providers.standard.operators.python import PythonOperator

from acme_etl.finance import build_report, fetch_rates

default_args = {
    "owner": "finance-data",
    "retries": 0,
}

with DAG(
    dag_id="daily_sales_report",
    start_date=datetime(2026, 8, 1),
    schedule="0 6 * * *",
    default_args=default_args,
    catchup=False,
) as dag:
    fetch_exchange_rates = PythonOperator(task_id="fetch_exchange_rates", python_callable=fetch_rates)
    build_sales_report = PythonOperator(task_id="build_sales_report", python_callable=build_report)
    fetch_exchange_rates >> build_sales_report
