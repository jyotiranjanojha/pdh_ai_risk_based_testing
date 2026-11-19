import os

# Ensure unit & integration tests always run against the deterministic mock engine
os.environ["SNOWFLAKE_MOCK_MODE"] = "true"
