#!/usr/bin/env python3
import requests, os, time

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
DATABASE_ID = "39e18306-969e-4fbe-8fee-35914297270f"
headers = {"Authorization": "Bearer " + NOTION_API_KEY, "Notion-Version": "2022-06-28", "Content-Type": "application/json"}

all_pages = []
has_more = True
start_cursor = None
while has_more:
    body = {"page_size": 100}
    if start_cursor:
        body["start_cursor"] = start_cursor
    r = requests.post("https://api.notion.com/v1/databases/" + DATABASE_ID + "/query", headers=headers, json=body)
    data = r.json()
    all_pages.extend(data.get("results", []))
    has_more = data.get("has_more", False)
    start_cursor = data.get("next_cursor")

print("Found " + str(len(all_pages)) + " pages")

def get_title(page):
    t = page["properties"].get("Job Post Title", {}).get("title", [])
    return t[0]["plain_text"] if t else "Unknown"

def get_block_text(block):
    bt = block.get("type", "")
    rt = block.get(bt, {}).get("rich_text", [])
    return "".join([r.get("plain_text", "") for r in rt])

total_deleted = 0
total_edited = 0
posts_changed = 0

for idx, page in enumerate(all_pages):
    title = get_title(page)
    blocks = []
    burl = "https://api.notion.com/v1/blocks/" + page["id"] + "/children?page_size=100"
    while burl:
        r = requests.get(burl, headers=headers)
        if r.status_code != 200:
            break
        data = r.json()
        blocks.extend(data.get("results", []))
        if data.get("has_more"):
            burl = "https://api.notion.com/v1/blocks/" + page["id"] + "/children?page_size=100&start_cursor=" + data["next_cursor"]
        else:
            burl = None
    
    blocks_to_delete = []
    blocks_to_edit = []
    in_quick_apply = False
    changes = 0
    
    for block in blocks:
        bt = block.get("type", "")
        text = get_block_text(block)
        
        if "quick apply" in text.lower():
            in_quick_apply = True
            blocks_to_delete.append(block["id"])
            changes += 1
            continue
        
        if in_quick_apply:
            blocks_to_delete.append(block["id"])
            changes += 1
            continue
        
        if bt == "bulleted_list_item" and "dedicated contact person" in text.lower():
            blocks_to_delete.append(block["id"])
            changes += 1
            continue
        
        if bt == "bulleted_list_item" and "biometric wristband" in text.lower():
            blocks_to_delete.append(block["id"])
            changes += 1
            continue
        
        needs_edit = False
        if "dedicated contact person" in text.lower() or "biometric wristband" in text.lower():
            needs_edit = True
        if " and a wristband" in text.lower() or " and a biometric wristband" in text.lower():
            needs_edit = True
        
        if needs_edit:
            rt = block.get(bt, {}).get("rich_text", [])
            new_rt = []
            changed = False
            for r in rt:
                content = r.get("text", {}).get("content", "")
                original = content
                content = content.replace(", dedicated contact person, biometric wristband", "")
                content = content.replace(", dedicated contact person", "")
                content = content.replace("dedicated contact person, ", "")
                content = content.replace("dedicated contact person", "")
                content = content.replace(", biometric wristband", "")
                content = content.replace("biometric wristband, ", "")
                content = content.replace("biometric wristband", "")
                content = content.replace(" and a biometric wristband", "")
                content = content.replace(" and a wristband", "")
                content = content.replace(", , ", ", ")
                content = content.strip()
                if content != original:
                    changed = True
                new_r = dict(r)
                new_r["text"] = dict(r.get("text", {}))
                new_r["text"]["content"] = content
                new_rt.append(new_r)
            if changed:
                remaining = "".join([r.get("text", {}).get("content", "") for r in new_rt]).strip()
                if remaining:
                    blocks_to_edit.append((block["id"], bt, new_rt))
                else:
                    blocks_to_delete.append(block["id"])
                changes += 1
    
    for block_id, bt, new_rt in blocks_to_edit:
        payload = {bt: {"rich_text": new_rt}}
        r = requests.patch("https://api.notion.com/v1/blocks/" + block_id, headers=headers, json=payload)
        if r.status_code == 200:
            total_edited += 1
        time.sleep(0.35)
    
    for block_id in reversed(blocks_to_delete):
        r = requests.delete("https://api.notion.com/v1/blocks/" + block_id, headers=headers)
        if r.status_code == 200:
            total_deleted += 1
        time.sleep(0.35)
    
    if changes > 0:
        posts_changed += 1
        if posts_changed <= 5 or posts_changed % 10 == 0:
            print("  [" + str(posts_changed) + "] " + title[:50] + " -- " + str(changes) + " changes")
    
    if (idx + 1) % 3 == 0:
        time.sleep(0.5)

print("\nDone: " + str(posts_changed) + " posts changed, " + str(total_deleted) + " blocks deleted, " + str(total_edited) + " blocks edited")
