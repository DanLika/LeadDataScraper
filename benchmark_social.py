import asyncio
import time
import os
from src.processors.leadhunter import LeadHunter

async def main():
    lh = LeadHunter()

    async def mock_ddg_search(query):
        await asyncio.sleep(0.5) # Simulate network delay
        return "<html></html>"

    lh._ddg_search_async = mock_ddg_search

    start = time.time()
    await lh.trazi_social_linkove_async("test business")
    end = time.time()

    print(f"Time taken: {end - start:.2f} seconds")

if __name__ == "__main__":
    asyncio.run(main())
