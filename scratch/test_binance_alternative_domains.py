import asyncio
import httpx
import time

async def test_domain(name, base_url):
    url = f"{base_url}/fapi/v1/premiumIndex?symbol=BTCUSDT"
    print(f"Testing {name} ({url})...")
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            elapsed = time.time() - start
            print(f"  [{name}] Success! Status: {response.status_code}, Elapsed: {elapsed:.2f}s")
            data = response.json()
            print(f"  [{name}] Funding Rate: {data.get('lastFundingRate')}")
            return True
    except Exception as e:
        print(f"  [{name}] Failed: {type(e).__name__}: {e}")
        return False

async def main():
    domains = {
        "fapi1": "https://fapi1.binance.com",
        "fapi3": "https://fapi3.binance.com",
        "fapi4": "https://fapi4.binance.com",
    }
    for name, base_url in domains.items():
        await test_domain(name, base_url)
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
