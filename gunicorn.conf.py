import os

# Bind to Render's injected $PORT
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

# Single worker on Render free tier (512 MB RAM)
workers = 1
worker_class = "sync"

# Load the Flask app in the master process before forking workers.
# load_data() reads several large CSVs at import time — this way it runs
# once in the master and workers fork instantly with the data already in RAM.
preload_app = True

# Give the worker plenty of time to handle long AI requests
timeout = 120
graceful_timeout = 30

# Log to stdout so Render captures it
accesslog = "-"
errorlog  = "-"
loglevel  = "info"
