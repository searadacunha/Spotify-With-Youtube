# Optional playlist cache prewarming (no additional Spotify OAuth permissions)

The TV-only Spotify source cannot read future tracks from the user's queue.
Rather than request private playlist scopes, \`./smarttube prewarm\` accepts an
explicit **metadata-only** JSON export and resolves each unique track in advance:

\`\`\`json
[
  {"id":"AAAAAAAAAAAAAAAAAAAAAA","title":"Track title","artists":["Artist"],"duration_ms":200000,"album":"Album"}
]
\`\`\`

\`\`\`sh
./smarttube prewarm /path/to/playlist-metadata.json
\`\`\`

The command uses the existing matching policy and SQLite cache. It does **not**
read current playback, send ADB commands, touch SmartTube or create OAuth tokens.
The live bridge continues to own playback decisions. A speculative result will
not overwrite an already valid cache row created by the live service. Network
errors remain retryable instead of becoming negative cache entries.

The export must contain 1–500 entries and at most 2 MB, with no unexpected
metadata fields. Track IDs are ordinary Spotify track IDs, not API tokens.
Protect private playlist information as appropriate.

### Evidence and limits

Regression tests verify parsing, idempotent cache reuse, rejection of
unrelated candidates, and a simulated cache race. They do not measure live
playlist transitions or ensure the cache remains correct after external
catalogue changes. The user must supply metadata; automatic reading of a
Spotify playlist or queue is not implemented. Search and optional audio
verification still require live access to YouTube. No additional OAuth scopes
have been added.

Measure actual transition latency on Android TV separately, ideally with
wall-clock logs and an ADB media-session probe over at least 100 transitions,
including skips, network failures and manual overrides.
