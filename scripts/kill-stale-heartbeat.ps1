# Kill a stale (pre-commit) Harvey heartbeat and let the guard restart a fresh one.
#
# Why: the loop started by the scheduled task runs in session 0 under an S4U token
# whose DACL only grants BUILTIN\Administrators PROCESS_TERMINATE. From a normal
# (UAC-filtered) shell Stop-Process / taskkill / Stop-ScheduledTask all fail or
# only orphan the tree, and while the orphan lives it holds heartbeat-launch.log
# open exclusively, so every new task instance dies with exit code 1 before it
# can log a single byte. One elevated action is required.
#
# Scope: ONLY processes named harvey.exe (never other pythons - the standup bot
# shares this box); the orphaned cmd.exe/powershell.exe wrappers of the old guard
# exit by themselves once harvey.exe is gone.
#
# Attribution rule (learned the hard way: three separate investigations -
# 2026-10-29, 2026-11-01, 2026-11-03 - re-audited "why does the heartbeat keep
# dying" before finding every death was a deliberate kill like this one): every
# ops kill/restart of the heartbeat MUST leave a "[kill-stale]" line in
# heartbeat-guard.log, the file everyone greps first. Without it, a planned
# restart is indistinguishable from a spontaneous death. If you kill the loop
# with your own ad-hoc script instead of this one, append an equivalent line to
# heartbeat-guard.log yourself (see Write-GuardLog below).
#
# Usage (MUST be elevated):
#   powershell -NoProfile -ExecutionPolicy Bypass -File kill-stale-heartbeat.ps1
# or double-click restart-heartbeat-elevated.cmd

$ErrorActionPreference = 'Continue'
$logPath   = 'C:\Users\gaurk\harvey\kill-stale-heartbeat.log'
$runLog    = 'C:\Users\gaurk\harvey\heartbeat-run.log'
$guardLog  = 'C:\Users\gaurk\harvey\heartbeat-guard.log'
$taskName  = 'Harvey Heartbeat'
$startedAt = Get-Date
# Why this restart is happening, surfaced in the guard log. Set
# HARVEY_RESTART_REASON in the calling shell for anything more specific.
$reason = if ($env:HARVEY_RESTART_REASON) { $env:HARVEY_RESTART_REASON } else { 'stale/pre-commit loop' }

function Write-Log([string]$message) {
    $line = "{0} [kill-stale] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message
    Write-Host $line
    try { Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8 } catch {}
}

# Provenance line in the guard log so the next "no heartbeat running" start
# below our kill is attributable, not a mystery (see the attribution rule above).
function Write-GuardLog([string]$message) {
    $line = "{0} [kill-stale] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message
    try { Add-Content -LiteralPath $guardLog -Value $line -Encoding UTF8 } catch {}
}

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($id)
$elevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Log ("start: elevated={0} user={1}" -f $elevated, $id.Name)
if (-not $elevated) {
    Write-Log 'NOT ELEVATED - a normal shell cannot terminate the session-0 heartbeat. Re-run elevated.'
    exit 5
}

$running = @(Get-CimInstance Win32_Process -Filter "Name='harvey.exe'")
if ($running.Count -eq 0) {
    Write-Log 'no harvey.exe running - nothing to kill'
} else {
    foreach ($p in $running) {
        Write-Log ("killing pid {0} (created {1}, session {2})" -f $p.ProcessId, $p.CreationDate, $p.SessionId)
        Write-GuardLog ("deliberate ops kill of pid {0} (reason: {1}) - restart follows; the next 'no heartbeat running' line is EXPECTED" -f $p.ProcessId, $reason)
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Write-Log ("pid {0}: kill accepted" -f $p.ProcessId)
        } catch {
            Write-Log ("pid {0}: KILL FAILED: {1}" -f $p.ProcessId, $_.Exception.Message)
            Write-GuardLog ("pid {0}: kill FAILED ({1}) - loop may still be running" -f $p.ProcessId, $_.Exception.Message)
        }
    }
    Start-Sleep -Seconds 5
    $left = @(Get-CimInstance Win32_Process -Filter "Name='harvey.exe'")
    Write-Log ("after kill: {0} harvey.exe still alive" -f $left.Count)
    if ($left.Count -gt 0) {
        Write-Log ('survivors: ' + (($left | ForEach-Object { $_.ProcessId }) -join ','))
        exit 2
    }
}

# The old guard wrapper held heartbeat-launch.log exclusively. Wait for the lock
# to clear, otherwise the next task instance exits 1 without logging anything.
$launchLog = 'C:\Users\gaurk\harvey\heartbeat-launch.log'
$freed = $false
for ($i = 0; $i -lt 24; $i++) {
    try {
        $fs = [IO.FileStream]::new($launchLog, [IO.FileMode]::Append, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
        $fs.Close(); $freed = $true; break
    } catch { Start-Sleep -Seconds 5 }
}
Write-Log ("launch log writable: {0}" -f $freed)

Write-Log ("task state before start: {0}" -f (Get-ScheduledTask -TaskName $taskName).State)
try {
    Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
    Write-Log 'Start-ScheduledTask issued'
    Write-GuardLog ("deliberate ops restart issued (reason: {0})" -f $reason)
} catch {
    Write-Log ("Start-ScheduledTask FAILED: {0}" -f $_.Exception.Message)
    Write-GuardLog ("Start-ScheduledTask FAILED: {0}" -f $_.Exception.Message)
    exit 3
}

# Verify a NEW loop is alive and that it logged the online banner.
$found = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 5
    $now = @(Get-CimInstance Win32_Process -Filter "Name='harvey.exe'" |
             Where-Object { $_.CreationDate -gt $startedAt })
    if ($now.Count -gt 0) { $found = $now; break }
}
if ($null -eq $found) {
    Write-Log 'VERIFY FAILED: no new harvey.exe started within 150s'
    exit 4
}
Write-Log ('new heartbeat: ' + (($found | ForEach-Object { "$($_.ProcessId)@$($_.CreationDate)" }) -join ','))
Start-Sleep -Seconds 20
$tail = @(Get-Content -LiteralPath $runLog -Tail 40)
$online = @($tail | Where-Object { $_ -match 'Harvey is online' })
Write-Log ("run log tail lines: {0}; online banner seen: {1}" -f $tail.Count, ($online.Count -gt 0))
Write-Log 'done'
exit 0
