# google-workspace-mcp

Local MCP server that exposes Gmail + Calendar (and friends) via Google's
official APIs. Reads the OAuth token from the `google-workspace` skill
(`C:\Data\Hermes\google_token.json` by default, overridable via
`GOOGLE_OAUTH_TOKEN_PATH`) and refreshes access tokens automatically when
they expire.

## Tools

| Tool | Purpose |
|---|---|
| `gws_health` | Token status, scopes, file location, expiry |
| `gws_gmail_search` | Search Gmail with a Gmail query string (e.g. `is:unread`) |
| `gws_gmail_get` | Fetch a single message by id (full payload) |
| `gws_gmail_send` | Compose + send a new email |
| `gws_gmail_reply` | Reply to an existing thread |
| `gws_gmail_list_labels` | List all labels |
| `gws_calendar_list_events` | List events in a date range |
| `gws_calendar_get_event` | Fetch a single event |
| `gws_calendar_create_event` | Create a new event |
| `gws_calendar_delete_event` | Delete an event |

## Storage

- Token: `C:\Data\Hermes\google_token.json` (env-overridable)
- Refresh tokens are persisted back to disk automatically after refresh
- No DB — credentials live in the existing google-workspace skill
