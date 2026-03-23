import time
import psutil
import os
import sys

# Mock for Supabase response
class MockData:
    def __init__(self, count):
        # Let's say we have `count` messages
        self.data = [{"status": "sent", "id": i} for i in range(count)]

class MockResponse:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count

class MockQuery:
    def __init__(self, res):
        self.res = res
    def eq(self, *args, **kwargs):
        return self
    def limit(self, *args, **kwargs):
        return self
    def execute(self):
        return self.res

class MockTable:
    def __init__(self, name, rows_count):
        self.name = name
        self.rows_count = rows_count
    def select(self, *args, **kwargs):
        if self.name == "campaigns":
            return MockQuery(MockResponse([{"id": "1"}], 1))

        # for campaign_messages
        if "count" in kwargs:
            # It's a count query
            return MockQuery(MockResponse(None, self.rows_count))

        # It's a fetch query
        if args and args[0] == "status":
            # fetch only status
            return MockQuery(MockResponse([{"status": "sent"}] * self.rows_count))

        # fetch all columns
        return MockQuery(MockResponse([{"id": i, "campaign_id": "1", "lead_unique_key": f"lead_{i}", "channel": "email", "subject": "Test", "body": "Body", "status": "sent"} for i in range(self.rows_count)]))

class MockClient:
    def __init__(self, rows_count):
        self.rows_count = rows_count
    def table(self, name):
        return MockTable(name, self.rows_count)

class MockDb:
    def __init__(self, rows_count):
        self.client = MockClient(rows_count)

def original_get_campaign(db, campaign_id):
    campaign = db.client.table("campaigns").select("*").eq("id", campaign_id).execute()
    # original code doesn't have single() in mock
    messages = db.client.table("campaign_messages").select("*").eq("campaign_id", campaign_id).execute()

    stats = {"pending": 0, "sent": 0, "delivered": 0, "replied": 0, "bounced": 0}
    for msg in (messages.data or []):
        s = msg.get("status", "pending")
        if s in stats:
            stats[s] += 1

    return stats

def optimized_get_campaign(db, campaign_id):
    campaign = db.client.table("campaigns").select("*").eq("id", campaign_id).execute()

    # optimized implementation
    # instead of fetching all, fetch just the stats.
    # We could simulate 5 parallel requests or 5 serial requests
    stats = {"pending": 0, "sent": 0, "delivered": 0, "replied": 0, "bounced": 0}
    for status in stats.keys():
        # count query
        res = db.client.table("campaign_messages").select("id", count="exact").eq("campaign_id", campaign_id).eq("status", status).limit(1).execute()
        stats[status] = res.count or 0

    # limit payload for frontend
    messages = db.client.table("campaign_messages").select("*").eq("campaign_id", campaign_id).limit(50).execute()

    return stats

def run_benchmark(rows):
    db = MockDb(rows)
    process = psutil.Process(os.getpid())

    print(f"--- Benchmarking with {rows} rows ---")

    # Original
    mem_before = process.memory_info().rss
    t0 = time.time()
    original_get_campaign(db, "1")
    t1 = time.time()
    mem_after = process.memory_info().rss
    orig_mem = (mem_after - mem_before) / (1024 * 1024)
    orig_time = t1 - t0
    print(f"Original Time: {orig_time:.4f}s")
    print(f"Original Memory Increase: {orig_mem:.2f} MB")

    # Optimized
    mem_before = process.memory_info().rss
    t0 = time.time()
    optimized_get_campaign(db, "1")
    t1 = time.time()
    mem_after = process.memory_info().rss
    opt_mem = (mem_after - mem_before) / (1024 * 1024)
    opt_time = t1 - t0
    print(f"Optimized Time: {opt_time:.4f}s")
    print(f"Optimized Memory Increase: {opt_mem:.2f} MB")

    print("---------------------------------------\n")

if __name__ == "__main__":
    run_benchmark(10_000)
    run_benchmark(50_000)
    run_benchmark(200_000)
