# Session Hub frontend

React + TypeScript interface shared by the local web view, Tauri desktop app, and team server. All rendering libraries, styles, icons, and KaTeX fonts are bundled; no CDN is used. Raw HTML in transcripts is not executed and remote Markdown images are not fetched automatically.

```powershell
npm ci
npm run build
npm test
```

`npm run dev` starts Vite on `127.0.0.1:5173`, proxying `/api` to `127.0.0.1:8765`. For local browser development, open the page with the local service's `?token=` once; it is moved to tab-scoped session storage and removed from the address bar. Production Tauri requests use the native bridge; neither the local service token nor team bearer credential is returned to JavaScript.

The native bridge supports `native_bridge`, `choose_files`, `native_asset`, `save_download`, `subscribe_activity`, and `unsubscribe_activity`; activity notifications arrive as `hub-activity`. Local team requests route through `/api/v1/remote/api`, and cloud pages use same-origin HttpOnly authentication.

Reader invariants: at most three mounted rounds; event pages replace one another with a maximum of 40 records; content fetches are bounded to 256 KiB and render in 24,000-character segments; tool bodies and stored images mount only when opened. Session lists, source discovery results, and round directories use cursor pagination. Updates display a banner without replacing the version currently being read. Comments and bookmarks reference immutable revision IDs.

Browser acceptance artifacts and customer session test copies belong only in ignored `output/`, `.playwright-cli/`, and `.runtime/` folders. Never commit transcript files or screenshots from real sessions.
