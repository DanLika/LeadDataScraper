import time
import sys

# Mock classes for Supabase Client
class MockResponse:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count

class MockQueryBuilder:
    def __init__(self, table_name, data_source, is_count=False):
        self.table_name = table_name
        self.data_source = data_source
        self.is_count = is_count
        self.filters = {}

    def select(self, *args, **kwargs):
        if 'count' in kwargs and kwargs['count'] == 'exact':
            self.is_count = True
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def execute(self):
        # Simulate network latency based on payload size
        base_latency = 0.05 # 50ms round trip

        filtered_data = []
        for row in self.data_source:
            match = True
            for k, v in self.filters.items():
                if row.get(k) != v:
                    match = False
                    break
            if match:
                filtered_data.append(row)

        if self.is_count:
            # Count query: fast, low bandwidth
            time.sleep(base_latency)
            return MockResponse(count=len(filtered_data))
        else:
            # Data query: latency scales with rows
            # Assume 1ms transfer time per row for large payloads (simulating subject/body)
            transfer_time = len(filtered_data) * 0.0001
            time.sleep(base_latency + transfer_time)
            return MockResponse(data=filtered_data)

class MockClient:
    def __init__(self, data_source):
        self.data_source = data_source

    def table(self, name):
        return MockQueryBuilder(name, self.data_source)

def main():
    print("Generating 50,000 dummy messages in memory...")
    # Generate 50k dummy messages
    messages = []
    statuses = ["pending", "sent", "delivered", "replied", "bounced"]
    campaign_id = "test-camp"
    for i in range(50000):
        messages.append({
            "campaign_id": campaign_id,
            "status": statuses[i % 5],
            "body": "A" * 500, # Simulate some payload size
            "subject": "Test"
        })

    mock_db = MockClient(messages)

    print("\n--- Running Baseline Measurement (Fetch All) ---")
    start_time = time.time()

    messages_resp = mock_db.table("campaign_messages").select("*").eq("campaign_id", campaign_id).execute()
    stats = {"pending": 0, "sent": 0, "delivered": 0, "replied": 0, "bounced": 0}
    for msg in (messages_resp.data or []):
        s = msg.get("status", "pending")
        if s in stats:
            stats[s] += 1

    baseline_time = time.time() - start_time
    print(f"Stats: {stats}")
    print(f"Baseline Time: {baseline_time:.4f} seconds")

    print("\n--- Running Optimized Measurement (5x Count) ---")
    start_time = time.time()

    stats2 = {"pending": 0, "sent": 0, "delivered": 0, "replied": 0, "bounced": 0}
    for status in stats2.keys():
        count_res = mock_db.table("campaign_messages").select("*", count="exact", head=True).eq("campaign_id", campaign_id).eq("status", status).execute()
        stats2[status] = count_res.count or 0

    optimized_time = time.time() - start_time
    print(f"Stats: {stats2}")
    print(f"Optimized Time: {optimized_time:.4f} seconds")

    print(f"\nImprovement: {baseline_time / optimized_time:.2f}x faster")

if __name__ == "__main__":
    main()
