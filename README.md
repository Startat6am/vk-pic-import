# VK photo archive importer (cloud-only)

Imports photo attachments from a VK community wall into a private Telegram channel. It stores metadata and Telegram message/file identifiers in Neon Postgres. Run it from a phone using GitHub Actions; no local Python installation is needed.

## Important limitations

- The VK token must be able to read the target closed community wall. A community/group token often cannot call `wall.get`; use an authorized user token that has access to the closed group. Never commit tokens.
- This first version uploads originals as Telegram documents through the standard Bot API, whose bot upload limit is 50 MB. Larger files are recorded as errors and are not silently resized.
- The standard Bot API's `getFile` download limit is 20 MB. This importer currently archives files and their Telegram message metadata; it does **not** yet provide a safe public image URL for the Vercel gallery. Do not expose the bot token in browser code. A separate server-side delivery strategy is required before wiring the gallery.
- VK's largest returned photo size is not guaranteed to be the exact byte-for-byte original uploaded by the author.
- The workflow processes at most 500 wall posts per run and is safe to rerun for already archived photos. Historical pagination can shift when new posts are added; after the initial history import, run `sync` periodically and rerun `history` if you suspect a gap.
- Respect VK and Telegram terms and the group's access rules. Only archive content you are authorized to access and retain.

## 1. Create a private Telegram channel

1. Create a private channel in Telegram.
2. Create a bot with `@BotFather`.
3. Add the bot to the channel as an administrator with permission to post messages.
4. Obtain the numeric channel chat ID. For a channel it commonly starts with `-100`; verify it rather than guessing.

## 2. Prepare Neon

Open the SQL editor for the existing Neon database and execute the complete contents of [schema.sql](./schema.sql). Use the pooled connection string from Neon if available.

## 3. Add GitHub Actions secrets

In the repository on GitHub, open **Settings → Secrets and variables → Actions → New repository secret**. Add these secrets:

| Secret | Value |
|---|---|
| `VK_ACCESS_TOKEN` | User access token that can read the closed group's wall |
| `VK_GROUP_ID` | Numeric group ID only, without `club` and without a minus sign |
| `TELEGRAM_BOT_TOKEN` | Token from BotFather |
| `TELEGRAM_CHAT_ID` | Private channel chat ID |
| `DATABASE_URL` | Neon PostgreSQL connection string |

Never put any token or database URL in code, issues, screenshots, or commits. If a token was exposed, revoke/rotate it.

## 4. Run a small test from your phone

1. Open the repository in your mobile browser: https://github.com/Startat6am/vk-pic-import
2. Tap **Actions → Import VK photo archive → Run workflow**.
3. Select `history`, set `posts_per_run` to `5`, leave `restart_history` unchecked, then run it.
4. Open the workflow run and inspect its logs. Verify that files appear in the Telegram channel and rows appear in Neon.
5. If the first test succeeds, run `history` with 100–300 posts per run until history is exhausted. Repeat runs manually; each run resumes using the offset stored in Neon.
6. After the first history pass, choose `sync` to scan the most recent posts. Schedule or manually run this mode only after the initial test is verified.

If the closed wall returns VK API error 5 or 15, verify that the token belongs to an account that can view the group and that the token type is supported for `wall.get`. Do not paste the token into chat or logs.

## 5. Troubleshooting

- **VK API error 5/15/27:** token lacks permission or is the wrong token type.
- **Telegram chat not found / forbidden:** check the chat ID and that the bot is an administrator in the private channel.
- **Neon connection failure:** check the connection string, password, and whether the database allows connections.
- **Original larger than 50 MB:** the current importer records an error. Do not assume the file was archived.
- **Run stops after 25 minutes:** lower `posts_per_run` and rerun; the history offset is persisted after each page.

## Current database tables

- `vk_posts`: wall post metadata
- `images`: VK photo identity, dimensions, checksum and upload status
- `post_images`: links photos to posts
- `telegram_files`: Telegram message/file IDs
- `import_state`: resumable wall offset
