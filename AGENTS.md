# Repository Instructions

## Protected `context/` folder

Treat every file inside `context/` as user-owned, read-only source material.

- Never create, edit, format, overwrite, rename, move, or delete any non-HTML
  file inside `context/`.
- HTML files inside `context/` are the only exception and may be created or
  modified when the user requests documentation or visual synchronization.
- Reading and inspecting any file inside `context/` is allowed.
- If an implementation or documentation task requires a change to a protected
  Markdown, text, JSON, or other non-HTML context file, stop and tell the user
  what needs to change. The user must make that source-file change.
- Never automatically synchronize information back into protected context
  source files. Synchronization flows from those sources into allowed HTML or
  files outside `context/`, not the other way around.

This protection is permanent and takes priority over normal documentation-sync
or cleanup behavior.
