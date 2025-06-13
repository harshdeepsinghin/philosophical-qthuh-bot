import aiohttp
import asyncio
import random

PHILOSOPHER_NAMES = {}

async def load_philosophers():
    url = "https://philosophersapi.com/api/philosophers"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            for item in data:
                PHILOSOPHER_NAMES[item["id"]] = item["name"]

async def fetch_random_quote():
    await load_philosophers()

    url = "https://philosophersapi.com/api/quotes"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            if not data:
                print("No quotes found.")
                return

            quote_data = random.choice(data)
            quote_text = quote_data.get("quote", "").strip()

            # ✅ Properly accessing nested philosopher ID
            philosopher_info = quote_data.get("philosopher", {})
            philosopher_id = philosopher_info.get("id", "")
            philosopher_name = PHILOSOPHER_NAMES.get(philosopher_id, "Unknown")

            print(f"_{quote_text}_\n\n*– {philosopher_name}*")

# Run the script
asyncio.run(fetch_random_quote())
