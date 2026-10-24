# Spark job: daily_aggregates. Aggregates the previous day's events into the daily summary tables.
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("daily_aggregates").getOrCreate()

events = spark.read.parquet("s3://acme-lake/events/dt=latest/")
summary = events.groupBy("customer_id", "event_type").count()
summary.write.mode("overwrite").parquet("s3://acme-lake/summaries/daily/")
