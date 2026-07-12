import asyncio
from app.services.tms_client import tms_client

result = asyncio.run(tms_client.health_check())
print("Health check result:", result)