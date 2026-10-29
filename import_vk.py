import hashlib
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import requests

VK_API = "https://api.vk.com/method"
VK_TOKEN = os.environ["VK_ACCESS_TOKEN"]
VK_GROUP_ID = int(os.environ["VK_GROUP_ID"])
VK_API_VERSION = os.getenv("VK_API_VERSION", "5.199")
TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
DATABASE_URL = os.environ["DATABASE_URL"]
PAGE_SIZE = 100
MAX_TELEGRAM_UPLOAD = 50 * 1024 * 1024
HTTP = requests.Session()


def db_connect():
    return psycopg.connect(DATABASE_URL, connect_timeout=15)


def vk_call(method, **params):
    params.update({"access_token": VK_TOKEN, "v": VK_API_VERSION})
    for attempt in range(5):
        response = HTTP.get(f"{VK_API}/{method}", params=params, timeout=40)
        response.raise_for_status()
        data = response.json()
        if "error" not in data:
            return data["response"]
        error = data["error"]
        code = error.get("error_code")
        if code in (6, 9, 10, 29) and attempt < 4:
            time.sleep(min(2 ** attempt, 15))
            continue
        raise RuntimeError(f"VK API {method} error {code}: {error.get('error_msg')}")
    raise RuntimeError(f"VK API {method}: retries exhausted")


def largest_photo(photo):
    sizes = photo.get("sizes") or []
    if not sizes:
        return None
    # Prefer the largest pixel area; use file size as a tie-breaker.
    best = max(sizes, key=lambda s: (int(s.get("width", 0)) * int(s.get("height", 0)), int(s.get("type") == "w")))
    return {
        "url": best.get("url"),
        "width": int(best.get("width") or 0),
        "height": int(best.get("height") or 0),
    }


def iter_photos(post, depth=0):
    if depth > 5:
        return
    for attachment in post.get("attachments", []):
        if attachment.get("type") == "photo":
            photo = attachment.get("photo") or {}
            largest = largest_photo(photo)
            if largest and largest["url"]:
                yield photo, largest
    # Reposts can contain their own photo attachments.
    for copied in post.get("copy_history", []) or []:
        yield from iter_photos(copied, depth + 1)


def telegram_send_document(path, caption):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument"
    with open(path, "rb") as file_handle:
        response = HTTP.post(
            url,
            data={"chat_id": TG_CHAT_ID, "caption": caption[:900]},
            files={"document": (Path(path).name, file_handle, "application/octet-stream")},
            timeout=(20, 180),
        )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram sendDocument failed: {payload}")
    message = payload["result"]
    document = message.get("document")
    if not document:
        raise RuntimeError("Telegram returned no document metadata")
    return message, document


def get_or_create_post(conn, post):
    owner_id = -VK_GROUP_ID
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO vk_posts (vk_owner_id, vk_post_id, post_date, text)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (vk_owner_id, vk_post_id)
            DO UPDATE SET post_date = EXCLUDED.post_date, text = EXCLUDED.text
            RETURNING id
            """,
            (owner_id, int(post["id"]), datetime.fromtimestamp(post.get("date", 0), tz=timezone.utc), post.get("text", "")),
        )
        return cur.fetchone()[0]


def save_photo(conn, post_db_id, photo, largest, position):
    photo_owner = int(photo.get("owner_id", -VK_GROUP_ID))
    photo_id = int(photo["id"])
    access_key = photo.get("access_key")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO images (vk_photo_owner_id, vk_photo_id, access_key, width, height, source_url)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (vk_photo_owner_id, vk_photo_id)
            DO UPDATE SET access_key = COALESCE(EXCLUDED.access_key, images.access_key),
                          width = GREATEST(images.width, EXCLUDED.width),
                          height = GREATEST(images.height, EXCLUDED.height),
                          source_url = EXCLUDED.source_url
            RETURNING id, upload_status
            """,
            (photo_owner, photo_id, access_key, largest["width"], largest["height"], largest["url"]),
        )
        image_db_id, status = cur.fetchone()
        cur.execute(
            "INSERT INTO post_images (post_id, image_id, position) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (post_db_id, image_db_id, position),
        )
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT file_id FROM telegram_files WHERE image_id = %s", (image_db_id,))
        if cur.fetchone():
            return "already_uploaded"

    try:
        response = HTTP.get(largest["url"], stream=True, timeout=(20, 90))
        response.raise_for_status()
        content_length = int(response.headers.get("Content-Length", "0") or 0)
        if content_length and content_length > MAX_TELEGRAM_UPLOAD:
            raise RuntimeError(f"Original exceeds Telegram Bot API 50 MB upload limit ({content_length} bytes)")
        digest = hashlib.sha256()
        total = 0
        with tempfile.NamedTemporaryFile(prefix="vk-photo-", suffix=".bin", delete=False) as tmp:
            temp_path = tmp.name
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_TELEGRAM_UPLOAD:
                    raise RuntimeError("Original exceeds Telegram Bot API 50 MB upload limit")
                digest.update(chunk)
                tmp.write(chunk)
        try:
            filename = f"vk_{photo_owner}_{photo_id}.jpg"
            renamed = str(Path(temp_path).with_name(filename))
            os.replace(temp_path, renamed)
            try:
                message, document = telegram_send_document(renamed, f"VK photo {photo_owner}_{photo_id}")
            finally:
                try:
                    os.unlink(renamed)
                except FileNotFoundError:
                    pass
        finally:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE images SET size_bytes=%s, sha256=%s, upload_status='uploaded', last_error=NULL WHERE id=%s",
                (total, digest.hexdigest(), image_db_id),
            )
            cur.execute(
                """
                INSERT INTO telegram_files (image_id, chat_id, message_id, file_id, file_unique_id, file_name)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (image_id) DO UPDATE SET
                  chat_id=EXCLUDED.chat_id, message_id=EXCLUDED.message_id,
                  file_id=EXCLUDED.file_id, file_unique_id=EXCLUDED.file_unique_id,
                  file_name=EXCLUDED.file_name, uploaded_at=NOW()
                """,
                (image_db_id, str(message["chat"]["id"]), int(message["message_id"]), document["file_id"], document.get("file_unique_id"), filename),
            )
        conn.commit()
        print(f"Uploaded photo {photo_owner}_{photo_id} ({total} bytes)")
        return "uploaded"
    except Exception as exc:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE images SET upload_status='error', last_error=%s WHERE vk_photo_owner_id=%s AND vk_photo_id=%s",
                (str(exc)[:2000], photo_owner, photo_id),
            )
        conn.commit()
        print(f"ERROR photo {photo_owner}_{photo_id}: {exc}")
        return "error"


def get_offset(conn, restart):
    with conn.cursor() as cur:
        if restart:
            cur.execute(
                "INSERT INTO import_state(key,value) VALUES ('wall_offset','0') "
                "ON CONFLICT (key) DO UPDATE SET value='0', updated_at=NOW()"
            )
            conn.commit()
            return 0
        cur.execute("SELECT value FROM import_state WHERE key='wall_offset'")
        row = cur.fetchone()
        return int(row[0]) if row else 0


def set_offset(conn, offset):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO import_state(key,value) VALUES ('wall_offset',%s) "
            "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
            (str(offset),),
        )
    conn.commit()


def main():
    batch_size = max(1, min(int(os.getenv("POSTS_PER_RUN", "100")), 500))
    restart = os.getenv("RESTART_IMPORT", "false").lower() == "true"
    # VK expects a positive community id in configuration; wall.get requires a negative owner_id.
    with db_connect() as conn:
        offset = get_offset(conn, restart)
        processed = 0
        print(f"Starting VK wall import: group={VK_GROUP_ID}, offset={offset}, batch={batch_size}")
        while processed < batch_size:
            count = min(PAGE_SIZE, batch_size - processed)
            result = vk_call("wall.get", owner_id=-VK_GROUP_ID, count=count, offset=offset, filter="all")
            posts = result.get("items", [])
            if not posts:
                set_offset(conn, 0)
                print("Reached end of wall. Offset reset to 0; next run can sync recent posts.")
                break
            for post in posts:
                post_db_id = get_or_create_post(conn, post)
                conn.commit()
                for position, (photo, largest) in enumerate(iter_photos(post)):
                    save_photo(conn, post_db_id, photo, largest, position)
                processed += 1
            offset += len(posts)
            set_offset(conn, offset)
            print(f"Progress: processed {processed}/{batch_size} posts this run; next offset={offset}")
            if len(posts) < count:
                set_offset(conn, 0)
                print("Reached end of wall. Offset reset to 0.")
                break
            time.sleep(0.35)
        print("Import run completed.")


if __name__ == "__main__":
    main()
