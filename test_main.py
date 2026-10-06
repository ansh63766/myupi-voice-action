import asyncio
from httpx import AsyncClient
import uvicorn
import threading
import time

def run_server():
    from app.main import app
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="error")

async def test():
    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    time.sleep(3) # wait for server
    
    async with AsyncClient() as client:
        print("--- Registering ---")
        # seed first
        import os
        os.system("python -m db.seed")
        
        res = await client.post("http://127.0.0.1:8001/api/auth/login", json={"mobile": "1234567890", "pin": "1234", "fingerprint": "xyz"})
        token = res.json()["token"]
        
        print("--- Sending Raise Chargeback ---")
        res = await client.post(f"http://127.0.0.1:8001/api/chat?token={token}", json={"conversation_id": "test-1", "message": "Raise a chargeback", "selected_entity": None, "user_confirmed": False})
        print(res.json().get("disambiguation_options", {}).keys())
        
        print("--- Sending Show mandates ---")
        res = await client.post(f"http://127.0.0.1:8001/api/chat?token={token}", json={"conversation_id": "test-1", "message": "Show me all active mandates", "selected_entity": None, "user_confirmed": False})
        print("Error:", res.json().get("error"))
        print("Disambiguation:", res.json().get("disambiguation_options"))

if __name__ == "__main__":
    asyncio.run(test())
