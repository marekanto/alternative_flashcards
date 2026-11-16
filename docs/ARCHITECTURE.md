# AltCards code map

The source is a single HTML file with embedded CSS and JavaScript. The runtime indexer finds top-level function blocks by their declaration boundaries and known subsystem anchors.

## Areas and symbols

| Area | Symbols / anchors |
|---|---|
| UI theme | CSS custom properties `--ac`, `--sf`, `--bg`, `--tx`; responsive CSS |
| Translations | `const TR` |
| XP / streak | `getLevelInfo`, `getRank`, `computeStreak` |
| State | `let Sync=null, S=null`; `const defaults`; `const save` |
| Feedback | `openFbx`, `closeFbx`, `submitFbx` |
| Supabase / auth | `Sync = { ... }` |
| Rendering | `render`, `vStudy` |
| Study queue | `start`, `grade`, `ans`, `undoAnswer` |
| Decks / cards | `vCards`, `renderCards`, `dlist`, `addDeck`, `delDeck`, `editDeckName`, `editCard`, `delCard` |
| CSV | `parseCSV`, `importCSV` |
| Add card | `vAdd`, `plink`, `suggest`, `addCard` |
| Public library | `vLibrary`, `renderLibGroups`, `copyDeckAt` |
| Stats | `vStats`, `bkp`, `rst` |
| Settings | `vSettings`, `saveSettings` |

## Context selection

- Prefer full top-level function blocks relevant to the task.
- For CSS tasks, include matching selectors and nearby media queries.
- For translations, include only relevant `TR` entries and the consuming function if found.
- Include a short state/invariant note, not the entire file.
- If no reliable match is found, stop and ask for a more precise task.
