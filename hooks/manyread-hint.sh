#!/usr/bin/env bash
# manyread PreToolUse hook (Read|Grep|Glob|Bash) — bash fallback for mac/linux.
# index EXISTS -> "query manyread"; NO index + exploration -> "STOP, ask to build".
input=$(cat)
# Fire AT MOST ONCE per session (avoid polluting the context window on every read).
sid=$(printf '%s' "$input" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
sentinel="${TMPDIR:-/tmp}/manyread-hint-${sid}.flag"
[ -n "$sid" ] && [ -f "$sentinel" ] && exit 0
tool=$(printf '%s' "$input" | sed -n 's/.*"tool_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)

# For Bash, only react to exploration commands, and never to manyread/uv itself.
if [ "$tool" = "Bash" ]; then
  cmd=$(printf '%s' "$input" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\(.*\)".*/\1/p' | head -1)
  printf '%s' "$cmd" | grep -Eiq '\b(ls|dir|find|cat|grep|rg|head|tail|tree|sed|awk|wc|more|less)\b' || exit 0
  printf '%s' "$cmd" | grep -Eiq '(manyread|query\.py|index_build|enrich_treesitter|trace\.py|ref\.py|rules\.py|uv run)' && exit 0
fi

cwd=$(printf '%s' "$input" | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
if [ -z "$cwd" ]; then
  fp=$(printf '%s' "$input" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
  [ -n "$fp" ] && cwd=$(dirname "$fp")
fi
[ -z "$cwd" ] && cwd="$PWD"
cwd=${cwd//\\\\//}

dir="$cwd"; found=""
for _ in 1 2 3 4 5 6; do
  [ -f "$dir/manyread/source.db" ] && { found=1; break; }
  parent=$(dirname "$dir"); [ "$parent" = "$dir" ] && break; dir="$parent"
done

[ -n "$sid" ] && : > "$sentinel" 2>/dev/null  # mark: nudged this session
if [ -n "$found" ]; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"A manyread index exists for this project. PREFER manyread over this Read/Grep/shell-scan: resolve the plugin root, then query.py (FTS5 / symbol / graph probes + a bounded substr slice). Read/scan files directly only if manyread cannot answer."}}'
else
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"No manyread index for this repo yet. Per policy: do NOT explore by hand (ls/find/cat/grep/Read) first -- STOP and ASK the user whether to build one with /mr-init. Explore manually only if they decline."}}'
fi
exit 0
