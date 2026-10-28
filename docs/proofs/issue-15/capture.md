# Frozen candidate capture progress

This task branch currently starts at accepted workspace main `8c97716`; its provisional migration `0007_candidates` must be renumbered onto the accepted #19 migration head before integration. No #15 completion or runtime deployment is claimed.

The first implementation captures candidate/source draft revision/generation identity under the existing draft lock and current write authorization, with an idempotent request key/digest and atomic audit. A repeated request resolves its original capture before checking the latest draft revision, so later edits cannot silently recapture different data. Source and latest revisions are returned separately. Database triggers reject capture-identity mutation and deletion.

Four tests ran against isolated real PostgreSQL database `verdant_issue15_test`: retained capture after a membership edit, conflicting/stale input rejection, competing capture producing one audit/identity, denied read-only capture, audit rollback, and direct persistence mutation/deletion rejection. Ruff passed. These tests cover capture only. Bounded preparation, immutable generation membership and interpretation pins, sealing, candidate UI/MCP/REST, current-draft conflict behavior, browser proof, and delivery remain unfinished.
