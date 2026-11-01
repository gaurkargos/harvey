# Pushes local-only ops work to the PRIVATE backup repo gaurkargos/harvey-ops.
#
# The local pre-push guard (.git/hooks/pre-push) blocks ops/* branches, any
# history containing c948575, and new reports/ commits on EVERY remote - it is
# deliberately left untouched and remains the enforcement for all manual
# pushes. This script is the ONLY sanctioned path to the private backup: it
# verifies via gh that the exact target repo exists and is PRIVATE, then pushes
# with --no-verify (the guard's own documented bypass) to the remote it itself
# registered from the constant below. It never takes a remote as an argument,
# so it cannot target anything public.
#
# Before pushing, new/changed files under reports/ are committed onto the ops
# branch ops/2026-10-26-local-work (reports/ is gitignored on the working
# branches, so the backup used to miss anything written after the last manual
# commit). That commit is built with pure plumbing against a throwaway index:
# read-tree -> update-index -> write-tree -> commit-tree -> update-ref.
# Nothing is checked out and the real index and working tree are untouched;
# the ops branch only advances when the resulting tree actually differs, and
# only ever via update-ref on the named local branch below.
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ops-backup.ps1
# PS 5.1 note: native stderr is left unredirected on purpose - under
# $ErrorActionPreference='Stop' a redirected stderr line becomes a
# terminating NativeCommandError. Failures are caught via $LASTEXITCODE.
$ErrorActionPreference = 'Continue'
$repo       = 'gaurkargos/harvey-ops'
$remoteName = 'opsbackup'
$opsBranch  = 'ops/2026-10-26-local-work'

Set-Location (Join-Path $PSScriptRoot '..')

# 1. The target must exist and be private - check before anything else.
$json = gh repo view $repo --json visibility
if ($LASTEXITCODE -ne 0 -or -not $json) {
    throw "gh cannot see $repo (missing or not authed); refusing to push."
}
if (($json | ConvertFrom-Json).visibility -ne 'PRIVATE') {
    throw "$repo is not PRIVATE; refusing to push."
}

# 2. Auto-commit new/changed reports/ files onto the ops branch (no checkout).
#    The temp index starts from the ops branch's full tree, so files only on
#    that branch survive and nothing from the current branch leaks in.
$idxFile = Join-Path $env:TEMP ("git-index-opsbackup." + [guid]::NewGuid().ToString())
$env:GIT_INDEX_FILE = $idxFile
try {
    git read-tree $opsBranch
    if ($LASTEXITCODE -ne 0) { throw "read-tree $opsBranch failed" }

    if (Test-Path 'reports') {
        $root = (Get-Location).Path
        foreach ($f in (Get-ChildItem 'reports' -File -Recurse)) {
            $rel = $f.FullName.Substring($root.Length + 1).Replace('\', '/')
            git update-index --add -- $rel
            if ($LASTEXITCODE -ne 0) { throw "update-index failed: $rel" }
        }
    }

    $newTree = git write-tree
    if ($LASTEXITCODE -ne 0) { throw "write-tree failed" }
    $oldTree = git rev-parse ($opsBranch + '^{tree}')

    if ($newTree -ne $oldTree) {
        $changed = @(git diff-index --cached --name-only $opsBranch)
        $parent  = git rev-parse $opsBranch
        $msg  = "reports: auto-commit before backup push ($($changed.Count) file(s))"
        $msg += "`n`n" + ($changed -join "`n")
        $commit = git commit-tree $newTree -p $parent -m $msg
        if ($LASTEXITCODE -ne 0) { throw "commit-tree failed" }
        git update-ref "refs/heads/$opsBranch" $commit $parent
        if ($LASTEXITCODE -ne 0) { throw "update-ref failed: $opsBranch" }
        Write-Host "auto-committed $($changed.Count) reports/ file(s) onto $opsBranch ($($commit.Substring(0, 12)))"
    } else {
        Write-Host "reports/: nothing new to commit on $opsBranch"
    }
} finally {
    Remove-Item Env:\GIT_INDEX_FILE -ErrorAction SilentlyContinue
    Remove-Item $idxFile -ErrorAction SilentlyContinue
}

# 3. Register the remote if needed (URL is derived from the constant above).
$have = @(git remote)
if (-not ($have -contains $remoteName)) {
    git remote add $remoteName "https://github.com/$repo.git"
    if ($LASTEXITCODE -ne 0) { throw "git remote add failed" }
}

# 4. Push main first (so it becomes the default branch), then every ops/* branch.
$branches = @('main') + @(git for-each-ref --format='%(refname:short)' refs/heads/ops/)
foreach ($b in $branches) {
    git push --no-verify $remoteName "${b}:refs/heads/${b}"
    if ($LASTEXITCODE -ne 0) { throw "push failed: $b" }
    Write-Host "pushed ${b}"
}

# 5. Keep the default branch pinned (idempotent).
gh repo edit $repo --default-branch main | Out-Null
if ($LASTEXITCODE -ne 0) { throw "could not set default branch on $repo" }
Write-Host "ops backup complete: $branches -> $repo (private)"
