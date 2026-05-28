# manyread PreToolUse hook (Read|Grep|Glob|Bash) — steer toward manyread.
# Non-blocking. Two jobs:
#   * index EXISTS for this repo -> "query manyread, don't grep/cat".
#   * NO index + an exploration attempt -> "STOP, ask the user to build one first".
# For Bash it only reacts to exploration commands (ls/find/cat/grep/...), and never
# to manyread's own commands, so it doesn't nag on normal shell use.
$raw = [Console]::In.ReadToEnd()
try { $j = $raw | ConvertFrom-Json } catch { exit 0 }

# Fire AT MOST ONCE per session (avoid polluting the context window on every read).
$sid = if ($j.session_id) { [string]$j.session_id } else { "" }
$sentinel = Join-Path ([System.IO.Path]::GetTempPath()) ("manyread-hint-$sid.flag")
if ($sid -and (Test-Path $sentinel)) { exit 0 }

$tool = [string]$j.tool_name
$cmd = ""
if ($j.tool_input -and $j.tool_input.command) { $cmd = [string]$j.tool_input.command }

# For Bash, only engage on file-exploration commands, and skip manyread/uv itself.
if ($tool -eq "Bash") {
  if ($cmd -notmatch '(?i)\b(ls|dir|find|cat|grep|rg|head|tail|tree|type|sed|awk|wc|more|less)\b') { exit 0 }
  if ($cmd -match '(?i)(manyread|query\.py|index_build|enrich_treesitter|trace\.py|ref\.py|rules\.py|uv run)') { exit 0 }
}

# Resolve a cwd to look from.
$cwd = $null
if ($j.cwd) { $cwd = [string]$j.cwd }
elseif ($j.tool_input -and $j.tool_input.file_path) { $cwd = Split-Path ([string]$j.tool_input.file_path) -Parent }
if (-not $cwd) { $cwd = (Get-Location).Path }

# Walk up ~6 levels looking for a built manyread index.
$dir = $cwd; $found = $false
for ($i = 0; $i -lt 6; $i++) {
  if (Test-Path (Join-Path $dir "manyread/source.db")) { $found = $true; break }
  $parent = Split-Path $dir -Parent
  if (-not $parent -or $parent -eq $dir) { break }
  $dir = $parent
}

if ($found) {
  $msg = "A manyread index exists for this project. PREFER manyread over this Read/Grep/shell-scan: resolve the plugin root, then query.py (FTS5 / symbol / graph probes + a bounded substr slice). Read/scan files directly only if manyread cannot answer."
} else {
  $msg = "No manyread index for this repo yet. Per policy: do NOT explore it by hand (ls/find/cat/grep/Read) first — STOP and ASK the user whether to build one with /mr-init (it makes reading much cheaper). Explore manually only if they decline."
}
if ($sid) { New-Item -ItemType File -Path $sentinel -Force | Out-Null }  # mark: nudged this session
$payload = @{ hookSpecificOutput = @{ hookEventName = "PreToolUse"; additionalContext = $msg } } | ConvertTo-Json -Compress
Write-Output $payload
exit 0
