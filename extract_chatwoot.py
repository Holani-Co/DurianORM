import json
import httpx
import sys

# Configuration
SOURCE_URL = "http://localhost:3000"  # Change to your source Chatwoot URL
SOURCE_TOKEN = "your_source_api_token"  # Change to your source API access token
ACCOUNT_ID = 1  # Change to your source Account ID

headers = {
    "api_access_token": SOURCE_TOKEN,
    "Content-Type": "application/json"
}

def get_all_paged(client, endpoint):
    results = []
    page = 1
    while True:
        print(f"Fetching page {page} of {endpoint}...")
        r = client.get(
            f"{SOURCE_URL}/api/v1/accounts/{ACCOUNT_ID}/{endpoint}",
            headers=headers,
            params={"page": page}
        )
        if r.status_code != 200:
            print(f"Error fetching {endpoint}: {r.status_code} - {r.text}")
            break
        data = r.json()
        
        # Chatwoot paginated responses typically have a "payload" array or are direct arrays
        if isinstance(data, dict) and "payload" in data:
            payload = data["payload"]
        elif isinstance(data, list):
            payload = data
        elif isinstance(data, dict) and "data" in data and "payload" in data["data"]:
            payload = data["data"]["payload"]
        else:
            payload = []
            
        if not payload:
            break
            
        results.extend(payload)
        page += 1
        # If the response is not paginated or has fewer items than standard page size, stop
        if len(payload) < 15:
            break
            
    return results

def main():
    if SOURCE_TOKEN == "your_source_api_token":
        print("Please edit extract_chatwoot.py and set SOURCE_TOKEN and SOURCE_URL.")
        sys.exit(1)

    backup_data = {}
    
    with httpx.Client(timeout=30) as client:
        # 1. Fetch Inboxes
        print("Fetching inboxes...")
        r = client.get(f"{SOURCE_URL}/api/v1/accounts/{ACCOUNT_ID}/inboxes", headers=headers)
        backup_data["inboxes"] = r.json().get("payload", []) if r.status_code == 200 else []
        
        # 2. Fetch Webhooks
        print("Fetching webhooks...")
        r = client.get(f"{SOURCE_URL}/api/v1/accounts/{ACCOUNT_ID}/webhooks", headers=headers)
        backup_data["webhooks"] = r.json() if r.status_code == 200 else []
        
        # 3. Fetch Teams
        print("Fetching teams...")
        r = client.get(f"{SOURCE_URL}/api/v1/accounts/{ACCOUNT_ID}/teams", headers=headers)
        backup_data["teams"] = r.json() if r.status_code == 200 else []
        
        # 4. Fetch Agent Bots
        print("Fetching agent bots...")
        r = client.get(f"{SOURCE_URL}/api/v1/accounts/{ACCOUNT_ID}/agent_bots", headers=headers)
        backup_data["agent_bots"] = r.json() if r.status_code == 200 else []
        
        # 5. Fetch Contacts
        print("Fetching contacts...")
        backup_data["contacts"] = get_all_paged(client, "contacts")
        
        # 6. Fetch Conversations
        print("Fetching conversations...")
        conversations = get_all_paged(client, "conversations")
        backup_data["conversations"] = conversations
        
        # 7. Fetch Messages for each conversation
        print(f"Fetching messages for {len(conversations)} conversations...")
        backup_data["messages"] = {}
        for conv in conversations:
            conv_id = conv["id"]
            print(f"Fetching messages for conversation #{conv_id}...")
            r = client.get(
                f"{SOURCE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/messages",
                headers=headers
            )
            if r.status_code == 200:
                backup_data["messages"][str(conv_id)] = r.json().get("payload", [])
            else:
                backup_data["messages"][str(conv_id)] = []
                
    # Save to file
    with open("chatwoot_backup.json", "w") as f:
        json.dump(backup_data, f, indent=2)
        
    print("Backup completed successfully! Data saved to chatwoot_backup.json")

if __name__ == "__main__":
    main()
