import requests
import uuid
import time
import json

PROXY_URL = "http://127.0.0.1:8080/v1/chat/completions"

def run_test():
    session_id = str(uuid.uuid4())
    print(f"Starting E2E Test with Session: {session_id}")
    
    headers = {
        "x-session-id": session_id,
        "Content-Type": "application/json"
    }

    def hit_proxy(instruction):
        print(f"\n--- Sending instruction: {instruction} ---")
        payload = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": instruction}
            ]
        }
        resp = requests.post(PROXY_URL, json=payload, headers=headers)
        return resp.json()
    
    # 1. Agent tries 'delete_line'
    res1 = hit_proxy("Try to delete_line 5")
    print("Response 1:", json.dumps(res1, indent=2))
    
    # 2. Agent tries 'remove_line' (same args, should trigger loop detection)
    res2 = hit_proxy("Try to remove_line 5")
    print("Response 2:", json.dumps(res2, indent=2))
    
    # 3. Agent tries 'remove_line' again. The proxy should block it as a repeat.
    res3 = hit_proxy("Try to remove_line 5")
    print("Response 3:", json.dumps(res3, indent=2))
    
    if "SYSTEM INTERCEPT" in str(res3):
        print("\nSUCCESS! Loop was detected and blocked by the Proxy Fast-Path!")
    else:
        print("\nProxy did not block the loop (may need more repeats to trigger).")

if __name__ == "__main__":
    run_test()
