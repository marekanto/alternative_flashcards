# AltCards — project context

## Product
AltCards is a universal flashcard learning web app. Motto: **An alternative way to learn.**
It supports learning beyond languages through user-created decks, shared library decks and spaced repetition.

## Current implementation baseline
- Main application: single-file `index.html` (HTML/CSS/JavaScript).
- Hosting: GitHub Pages.
- Backend: Supabase Auth and PostgreSQL.
- Local/offline state is held in browser storage; sync is handled by the `Sync` object.
- Styling variables include `--ac`, `--sf`, `--bg`, `--tx`, `--mu`, `--bd`.
- UI translations are held in `TR`; languages include Polish, English and Spanish.
- `S` is the main application state.

## Source snapshot used to tailor this pack
- Uploaded source: 1300 lines.
- SHA-256: `6459dc0b4aac6d6849ab6fab57e232d64aecbf962d5148e457c261e36e6c1e2b`.
- The repository's checked-out `main/index.html` at workflow runtime is authoritative.

## Confirmed symbols in the supplied source
- State / defaults: `S`, `defaults`, `save`, `newLimit`, `daily`, `ratings`.
- Study: `vStudy`, `start`, `grade`, `ans`, `undoAnswer`.
- XP: `getLevelInfo`, `getRank`, `computeStreak`, `Sync.logXp`.
- Deck/card management: `vCards`, `renderCards`, `dlist`, `addDeck`, `delDeck`, `editDeckName`, `editCard`, `delCard`, `parseCSV`, `importCSV`, `vAdd`, `addCard`.
- Library: `vLibrary`, `renderLibGroups`, `copyDeckAt`, `Sync.copyDeck`.
- Statistics: `vStats`, `bkp`, `rst`.
- Settings: `vSettings`, `saveSettings`.
- Feedback: `openFbx`, `closeFbx`, `submitFbx`.
- Backend: `Sync` object with auth, download/upload, XP logging, shared decks, sign-out and account deletion.

## Invariants
- Preserve existing SRS scheduling and queue behavior.
- Preserve XP calculations and daily-limit accounting.
- Preserve undo behavior.
- Preserve local persistence and Supabase synchronization.
- Preserve authentication, shared-deck behavior and account deletion.
- Do not change data structures or database interactions unless the task explicitly requests it and the sensitive-change workflow option is enabled.
