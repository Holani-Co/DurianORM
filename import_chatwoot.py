import json
import httpx
import sys

# Configuration
TARGET_URL = "http://localhost:3000"  # Change to your target Chatwoot URL
TARGET_TOKEN = "your_target_api_token"  # Change to your target API access token
ACCOUNT_ID = 1  # Change to your target Account ID

headers = {
    "api_access_token": TARGET_TOKEN,
    "Content-Type": "application/json"
}

def main():
    if TARGET_TOKEN == "your_target_api_token":
        print("Please edit import_chatwoot.py and set TARGET_TOKEN.")
        sys.exit(1)

    try:
        with open("chatwoot_backup.json", "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        print("Error: chatwoot_backup.json not found. Run extract_chatwoot.py first.")
        sys.exit(1)

    with httpx.Client(timeout=30) as client:
        # 1. Recreate Teams
        print("Recreating teams...")
        team_id_map = {}
        for team in data.get("teams", []):
            print(f"Creating team: {team.get('name')}...")
            r = client.post(
                f"{TARGET_URL}/api/v1/accounts/{ACCOUNT_ID}/teams",
                headers=headers,
                json={"name": team.get("name"), "description": team.get("description")}
            )
            if r.status_code in (200, 201):
                team_id_map[team["id"]] = r.json()["id"]
            else:
                print(f"Failed to create team {team.get('name')}: {r.text}")

        # 2. Recreate Inboxes
        print("Recreating inboxes...")
        inbox_id_map = {}
        for inbox in data.get("inboxes", []):
            print(f"Creating inbox: {inbox.get('name')}...")
            channel_type = inbox.get("channel_type")
            
            # Recreate as API channel by default or corresponding channel
            channel_payload = {"type": "api", "webhook_url": ""}
            r = client.post(
                f"{TARGET_URL}/api/v1/accounts/{ACCOUNT_ID}/inboxes",
                headers=headers,
                json={"name": inbox.get("name"), "channel": channel_payload}
            )
            if r.status_code in (200, 201):
                inbox_id_map[inbox["id"]] = r.json()["id"]
            else:
                print(f"Failed to create inbox {inbox.get('name')}: {r.text}")

        # 3. Recreate Contacts
        print("Recreating contacts...")
        contact_id_map = {}
        contact_inbox_source_map = {}
        for contact in data.get("contacts", []):
            print(f"Creating contact: {contact.get('name')}...")
            
            # Find the contact's inbox and use the mapped target inbox_id
            contact_inboxes = contact.get("contact_inboxes", [])
            target_inbox_id = None
            source_id = None
            if contact_inboxes:
                old_inbox_id = contact_inboxes[0].get("inbox_id")
                target_inbox_id = inbox_id_map.get(old_inbox_id)
                source_id = contact_inboxes[0].get("source_id")

            if not target_inbox_id:
                # Fallback to the first inbox or skip if no inbox mapping
                target_inbox_id = list(inbox_id_map.values())[0] if inbox_id_map else 1

            payload = {
                "inbox_id": target_inbox_id,
                "name": contact.get("name"),
                "email": contact.get("email"),
                "phone_number": contact.get("phone_number"),
                "identifier": contact.get("identifier"),
                "custom_attributes": contact.get("custom_attributes", {})
            }
            r = client.post(
                f"{TARGET_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts",
                headers=headers,
                json=payload
            )
            if r.status_code in (200, 201, 422):
                if r.status_code == 422:
                    # Contact already exists, retrieve it
                    search_id = contact.get("identifier") or contact.get("email") or contact.get("name")
                    s = client.get(
                        f"{TARGET_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/search",
                        headers=headers,
                        params={"q": search_id}
                    )
                    hits = s.json().get("payload", []) if s.status_code == 200 else []
                    if hits:
                        contact_id_map[contact["id"]] = hits[0]["id"]
                        target_ci = (hits[0].get("contact_inboxes") or [{}])[0]
                        contact_inbox_source_map[contact["id"]] = target_ci.get("source_id")
                else:
                    res_contact = r.json().get("payload", {}).get("contact", {})
                    contact_id_map[contact["id"]] = res_contact["id"]
                    target_ci = (res_contact.get("contact_inboxes") or [{}])[0]
                    contact_inbox_source_map[contact["id"]] = target_ci.get("source_id")

        # 4. Recreate Conversations & Messages
        print("Recreating conversations and messages...")
        for conv in data.get("conversations", []):
            old_conv_id = conv["id"]
            old_inbox_id = conv.get("inbox_id")
            old_contact_id = conv.get("contact_id")
            
            target_inbox_id = inbox_id_map.get(old_inbox_id)
            target_contact_id = contact_id_map.get(old_contact_id)
            source_id = contact_inbox_source_map.get(old_contact_id) or f"conv_{old_conv_id}"

            if not target_inbox_id or not target_contact_id:
                print(f"Skipping conversation #{old_conv_id} - missing inbox or contact mapping.")
                continue

            print(f"Creating conversation for old ID #{old_conv_id}...")
            # Create conversation
            r = client.post(
                f"{TARGET_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations",
                headers=headers,
                json={
                    "source_id": source_id,
                    "inbox_id": target_inbox_id,
                    "contact_id": target_contact_id,
                    "additional_attributes": conv.get("additional_attributes", {}),
                    "custom_attributes": conv.get("custom_attributes", {})
                }
            )
            if r.status_code in (200, 201):
                new_conv_id = r.json()["id"]
                
                # Import messages for this conversation
                messages = data.get("messages", {}).get(str(old_conv_id), [])
                # Order by creation time (ascending) to keep transcript history correct
                messages_sorted = sorted(messages, key=lambda m: m.get("created_at", 0))
                
                for msg in messages_sorted:
                    # Skip system activity messages if any
                    if msg.get("message_type") == 2:
                        continue
                    
                    message_type = "incoming" if msg.get("message_type") == 0 else "outgoing"
                    client.post(
                        f"{TARGET_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{new_conv_id}/messages",
                        headers=headers,
                        json={
                            "content": msg.get("content"),
                            "message_type": message_type,
                            "private": msg.get("private", False),
                            "content_attributes": msg.get("content_attributes", {})
                        }
                    )
                
                # Assign team if applicable
                old_team_id = conv.get("team_id")
                target_team_id = team_id_map.get(old_team_id)
                if target_team_id:
                    client.post(
                        f"{TARGET_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{new_conv_id}/assignments",
                        headers=headers,
                        json={"team_id": target_team_id}
                    )
            else:
                print(f"Failed to create conversation: {r.text}")

    print("Data backfill completed successfully!")

if __name__ == "__main__":
    main()
