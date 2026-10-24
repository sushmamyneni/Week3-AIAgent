# Spark job: sessionize_events. Groups raw click events into user sessions.
from pyspark.sql import SparkSession, Window, functions as F

spark = SparkSession.builder.appName("sessionize_events").getOrCreate()

clicks = spark.read.parquet("s3://acme-lake/clicks/dt=latest/")
window = Window.partitionBy("user_id").orderBy("event_time")
sessions = clicks.withColumn("gap", F.col("event_time") - F.lag("event_time").over(window))
sessions.write.mode("overwrite").parquet("s3://acme-lake/sessions/")
