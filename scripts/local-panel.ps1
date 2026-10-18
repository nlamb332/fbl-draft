param(
    [int]$Port = 8765,
    [string]$Python = '',
    [switch]$CheckOnly,
    [switch]$FoundationOnly,
    [switch]$PageRunner,
    [switch]$OpenPanel,
    [switch]$Details
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$panelUrl = "http://127.0.0.1:$Port"
$statusUrl = "$panelUrl/api/status"
$runtimeInfoUrl = "$panelUrl/api/runtime-info"
$database = Join-Path $repoRoot 'runtime\control.sqlite3'
# -CheckOnly is a diagnostic command, so it always prints the full status.
$showDetails = $CheckOnly -or $Details
$script:conflictReason = ''

function Test-PanelPort {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connection = $client.ConnectAsync('127.0.0.1', $Port)
        return ($connection.Wait(500) -and $client.Connected)
    }
    catch { return $false }
    finally { $client.Dispose() }
}

function Show-PanelStatus {
    param([switch]$QuietIfMissing)
    try {
        $status = Invoke-RestMethod -Uri $statusUrl -Method Get -TimeoutSec 3
        if ($null -eq $status -or $null -eq $status.sessions -or $status.sessions -isnot [System.Array]) {
            $script:conflictReason = 'foreign'
            if (-not $QuietIfMissing) { Write-Host "A service answered at $panelUrl but its /api/status response is not an FBL session list." -ForegroundColor Yellow }
            return $false
        }
        $runtime = Invoke-RestMethod -Uri $runtimeInfoUrl -Method Get -TimeoutSec 3
        if ($runtime.service -ne 'fbl-draft-control' -or $runtime.schema_version -ne 1 -or -not $runtime.startup_source.source_hashes_sha256 -or -not $runtime.policy.name) {
            $script:conflictReason = 'foreign'
            if (-not $QuietIfMissing) { Write-Host "A service answered at $panelUrl but its runtime identity is invalid." -ForegroundColor Yellow }
            return $false
        }
        $normalizedRoot = [System.IO.Path]::GetFullPath($repoRoot).TrimEnd('\', '/').ToLowerInvariant().Replace('\', '/')
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try { $expectedRootId = ([System.BitConverter]::ToString($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($normalizedRoot)))).Replace('-', '').ToLowerInvariant() }
        finally { $sha.Dispose() }
        if ($runtime.source_root_id -ne $expectedRootId) {
            $script:conflictReason = 'other-download'
            if (-not $QuietIfMissing) { Write-Host "A different FBL download is serving $panelUrl. Stop it and launch from this folder so the correct private setup is used." -ForegroundColor Yellow }
            return $false
        }
        $requestedPolicy = if ($FoundationOnly) { 'foundation' } else { 'marginal-lineup-v1' }
        if ($runtime.policy.name -ne $requestedPolicy) {
            $script:conflictReason = 'stale'
            if (-not $QuietIfMissing) { Write-Host "The service policy is $($runtime.policy.name), but this launch requests $requestedPolicy. Restart with the requested policy." -ForegroundColor Yellow }
            return $false
        }
        if ($PageRunner -and -not $runtime.page_runner_enabled) {
            $script:conflictReason = 'stale'
            if (-not $QuietIfMissing) { Write-Host "The service was started without the in-page runner, but this launch requests -PageRunner. Restart with -PageRunner." -ForegroundColor Yellow }
            return $false
        }
        foreach ($relativePath in @('draft_assistant/server.py', 'draft_assistant/coordinator.py', 'draft_assistant/ownership.py', 'draft_assistant/valuation.py', 'draft_assistant/marginal_lineup.py', 'draft_assistant/page_bundle.py')) {
            $sourcePath = Join-Path $repoRoot $relativePath
            $expectedHash = $runtime.startup_source.source_hashes_sha256.PSObject.Properties[$relativePath].Value
            $actualHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
            if (-not $expectedHash -or $expectedHash -ne $actualHash) {
                $script:conflictReason = 'stale'
                if (-not $QuietIfMissing) { Write-Host "The running service source for $relativePath does not match this checkout. Stop it and launch from this checkout." -ForegroundColor Yellow }
                return $false
            }
        }
    }
    catch {
        if (-not $QuietIfMissing) { Write-Host "No FBL panel answered at $panelUrl." -ForegroundColor Yellow }
        return $false
    }

    if ($showDetails) { Write-Host "Panel: $panelUrl" }
    if (-not $showDetails) { return $true }
    Write-Host "Serving process revision: $($runtime.startup_source.git_revision)"
    Write-Host "Loaded service policy: $($runtime.policy.name) (promotion_valid=$($runtime.policy.promotion_valid)); page_dispatch_enabled=$($runtime.page_dispatch_enabled); page_runner_enabled=$($runtime.page_runner_enabled)"
    if (-not $status.sessions -or $status.sessions.Count -eq 0) {
        Write-Host 'Session: none connected; no session revision or recommendation yet.'
    }
    else {
        foreach ($session in $status.sessions) {
            $recommendation = $session.recommendation
            $recommendationStatus = if ($recommendation -and $recommendation.lookahead_status) { $recommendation.lookahead_status } else { 'not evaluated yet' }
            $explanation = if ($recommendation -and $recommendation.explanation) { $recommendation.explanation } else { 'none' }
            Write-Host "Session $($session.session_id): state=$($session.state), revision=$($session.latest_revision), room_practice=$($session.room.practice), observed recommendation status=$recommendationStatus"
            Write-Host "Observed recommendation explanation: $explanation"
        }
    }
    return $true
}

function Test-PythonCandidate {
    param([string]$Exe, [string[]]$Prefix = @())
    $result = @{ Found = $false; Ok = $false; Version = ''; Exe = $Exe; Prefix = $Prefix }
    $cmd = Get-Command $Exe -ErrorAction SilentlyContinue
    if (-not $cmd) { return $result }
    $result.Exe = $cmd.Source
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $cmd.Source @Prefix -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        $exit = $LASTEXITCODE
    }
    catch { $exit = 1; $output = $null }
    finally { $ErrorActionPreference = $previous }
    # The Microsoft Store python.exe alias stub (under WindowsApps) only prints a
    # hint and fails instead of running Python, so it fails this check and is skipped.
    $text = ("$output").Trim()
    if ($exit -ne 0 -or $text -notmatch '^(\d+)\.(\d+)$') { return $result }
    $result.Found = $true
    $result.Version = $text
    $result.Ok = ([int]$Matches[1] -gt 3) -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 10)
    return $result
}

if ($CheckOnly) {
    if (-not (Show-PanelStatus)) { exit 1 }
    exit 0
}

if ($showDetails -and (Test-Path -LiteralPath $database)) {
    Write-Host 'Existing runtime database found; this launcher will reuse its durable session journal.'
}
if (Show-PanelStatus -QuietIfMissing) {
    Write-Host 'FBL Draft Advisor is already running from this folder.'
    Write-Host "  Panel: $panelUrl"
    if ($OpenPanel) { Write-Host '  Opening it now. Keep the original FBL window open while drafting.' }
    if ($OpenPanel) { Start-Process $panelUrl }
    exit 0
}
if ($script:conflictReason -eq 'stale') {
    Write-Host ''
    Write-Host 'An older FBL Draft Advisor from this folder is still running with different settings.' -ForegroundColor Yellow
    Write-Host '  Fix: close its window (or press Ctrl+C there), then double-click Launch Advisor.cmd again.'
    exit 2
}
if ($script:conflictReason -eq 'other-download' -or (Test-PanelPort)) {
    $altPort = $Port + 1
    Write-Host ''
    if ($script:conflictReason -eq 'other-download') {
        Write-Host "A different copy of FBL Draft Advisor is already using port $Port." -ForegroundColor Yellow
        Write-Host '  Fix: close the other FBL Draft Advisor window, then launch this one again.'
    }
    else {
        Write-Host "Another program is already using port $Port, so the advisor cannot start." -ForegroundColor Yellow
        Write-Host '  Fix: close that program, then launch again.'
    }
    Write-Host "  Or run from PowerShell:  scripts\local-panel.ps1 -PageRunner -OpenPanel -Port $altPort"
    Write-Host "  Note: the draft bookmark is tied to the port, so on port $altPort you must re-add the bookmark from the panel."
    exit 2
}

$candidates = @()
if ($Python) { $candidates += , (Test-PythonCandidate -Exe $Python) }
else {
    $candidates += , (Test-PythonCandidate -Exe 'python')
    $candidates += , (Test-PythonCandidate -Exe 'py' -Prefix @('-3'))
}
$chosen = $candidates | Where-Object { $_.Ok } | Select-Object -First 1
if (-not $chosen) {
    $tooOld = $candidates | Where-Object { $_.Found } | Select-Object -First 1
    Write-Host ''
    if ($tooOld) {
        Write-Host "FBL Draft Advisor needs Python 3.10 or newer, but found Python $($tooOld.Version)." -ForegroundColor Yellow
    }
    else {
        Write-Host 'FBL Draft Advisor needs Python 3.10 or newer, and Python was not found on this computer.' -ForegroundColor Yellow
    }
    Write-Host '  Option 1: install it from https://www.python.org/downloads/'
    Write-Host '            and tick "Add python.exe to PATH" on the first installer screen.'
    Write-Host '  Option 2: in a terminal run:  winget install Python.Python.3.12'
    Write-Host '  Then close this window and double-click Launch Advisor.cmd again.'
    exit 3
}
$pythonCommand = [pscustomobject]@{ Source = $chosen.Exe }
$pythonPrefix = $chosen.Prefix
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $database) | Out-Null
$leagueConfigPath = Join-Path $repoRoot 'runtime\league.json'
Set-Location -LiteralPath $repoRoot
if ($OpenPanel -and -not $Details) {
    Write-Host 'FBL Draft Advisor is running.'
    Write-Host "  Panel: $panelUrl"
    Write-Host '  Keep this window open while drafting. Close it (or press Ctrl+C) to stop.'
}
else {
    Write-Host "Starting the FBL local coordinator. Open $panelUrl"
    if ($FoundationOnly) { Write-Host 'Selection policy: foundation (--foundation-only).' }
    else { Write-Host 'Selection policy: accepted marginal-lineup-v1 artifact (default).' }
    Write-Host 'The service does not connect to ESPN or start a draft. Press Ctrl+C to stop it.'
}
# Assign arrays explicitly: `$x = if (...) { @('--flag') }` unrolls to a string
# and splatting a string passes it character by character.
$policyArgs = @()
if ($FoundationOnly) { $policyArgs = @('--foundation-only') }
$serveArgs = @()
if ($PageRunner) { $serveArgs = @('--page-runner') }
if ($OpenPanel) { $serveArgs += '--open-browser' }
if ($PageRunner -and $showDetails) { Write-Host 'In-page runner enabled: /page-runner.js is served and owned sessions accept page-origin operator requests.' }
$configArgs = @()
if (Test-Path -LiteralPath $leagueConfigPath) { $configArgs = @('--league-config', $leagueConfigPath) }
& $pythonCommand.Source @pythonPrefix -m draft_assistant --db $database @configArgs @policyArgs serve --host 127.0.0.1 --port $Port @serveArgs
exit $LASTEXITCODE
