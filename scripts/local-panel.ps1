param(
    [int]$Port = 8765,
    [string]$Python = '',
    [switch]$CheckOnly,
    [switch]$FoundationOnly,
    [switch]$PageRunner,
    [switch]$OpenPanel
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$panelUrl = "http://127.0.0.1:$Port"
$statusUrl = "$panelUrl/api/status"
$runtimeInfoUrl = "$panelUrl/api/runtime-info"
$database = Join-Path $repoRoot 'runtime\control.sqlite3'

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
            Write-Host "A service answered at $panelUrl but its /api/status response is not an FBL session list." -ForegroundColor Yellow
            return $false
        }
        $runtime = Invoke-RestMethod -Uri $runtimeInfoUrl -Method Get -TimeoutSec 3
        if ($runtime.service -ne 'fbl-draft-control' -or $runtime.schema_version -ne 1 -or -not $runtime.startup_source.source_hashes_sha256 -or -not $runtime.policy.name) {
            Write-Host "A service answered at $panelUrl but its runtime identity is invalid." -ForegroundColor Yellow
            return $false
        }
        $normalizedRoot = [System.IO.Path]::GetFullPath($repoRoot).TrimEnd('\', '/').ToLowerInvariant().Replace('\', '/')
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try { $expectedRootId = ([System.BitConverter]::ToString($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($normalizedRoot)))).Replace('-', '').ToLowerInvariant() }
        finally { $sha.Dispose() }
        if ($runtime.source_root_id -ne $expectedRootId) {
            Write-Host "A different FBL download is serving $panelUrl. Stop it and launch from this folder so the correct private setup is used." -ForegroundColor Yellow
            return $false
        }
        $requestedPolicy = if ($FoundationOnly) { 'foundation' } else { 'marginal-lineup-v1' }
        if ($runtime.policy.name -ne $requestedPolicy) {
            Write-Host "The service policy is $($runtime.policy.name), but this launch requests $requestedPolicy. Restart with the requested policy." -ForegroundColor Yellow
            return $false
        }
        if ($PageRunner -and -not $runtime.page_runner_enabled) {
            Write-Host "The service was started without the in-page runner, but this launch requests -PageRunner. Restart with -PageRunner." -ForegroundColor Yellow
            return $false
        }
        foreach ($relativePath in @('draft_assistant/server.py', 'draft_assistant/coordinator.py', 'draft_assistant/ownership.py', 'draft_assistant/valuation.py', 'draft_assistant/marginal_lineup.py', 'draft_assistant/page_bundle.py')) {
            $sourcePath = Join-Path $repoRoot $relativePath
            $expectedHash = $runtime.startup_source.source_hashes_sha256.PSObject.Properties[$relativePath].Value
            $actualHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
            if (-not $expectedHash -or $expectedHash -ne $actualHash) {
                Write-Host "The running service source for $relativePath does not match this checkout. Stop it and launch from this checkout." -ForegroundColor Yellow
                return $false
            }
        }
    }
    catch {
        if (-not $QuietIfMissing) { Write-Host "No FBL panel answered at $panelUrl." -ForegroundColor Yellow }
        return $false
    }

    Write-Host "Panel: $panelUrl"
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

if ($CheckOnly) {
    if (-not (Show-PanelStatus)) { exit 1 }
    exit 0
}

if (Test-Path -LiteralPath $database) {
    Write-Host 'Existing runtime database found; this launcher will reuse its durable session journal.'
}
if (Show-PanelStatus -QuietIfMissing) {
    Write-Host 'The panel is already serving; no second coordinator was started.'
    if ($OpenPanel) { Start-Process $panelUrl }
    exit 0
}
if (Test-PanelPort) {
    throw "Port $Port is occupied by a service that failed FBL identity/source checks. Inspect and stop it before launching this checkout."
}

if ($Python) {
    $pythonCommand = Get-Command $Python -ErrorAction SilentlyContinue
    $pythonPrefix = @()
}
else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    $pythonPrefix = @()
    if (-not $pythonCommand) {
        $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
        $pythonPrefix = @('-3')
    }
}
if (-not $pythonCommand) {
    throw 'Python was not found on PATH. Pass -Python <path> to select the installed interpreter.'
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $database) | Out-Null
$leagueConfigPath = Join-Path $repoRoot 'runtime\league.json'
Set-Location -LiteralPath $repoRoot
Write-Host "Starting the FBL local coordinator. Open $panelUrl"
if ($FoundationOnly) { Write-Host 'Selection policy: foundation (--foundation-only).' }
else { Write-Host 'Selection policy: accepted marginal-lineup-v1 artifact (default).' }
Write-Host 'The service does not connect to ESPN or start a draft. Press Ctrl+C to stop it.'
# Assign arrays explicitly: `$x = if (...) { @('--flag') }` unrolls to a string
# and splatting a string passes it character by character.
$policyArgs = @()
if ($FoundationOnly) { $policyArgs = @('--foundation-only') }
$serveArgs = @()
if ($PageRunner) { $serveArgs = @('--page-runner') }
if ($OpenPanel) { $serveArgs += '--open-browser' }
if ($PageRunner) { Write-Host 'In-page runner enabled: /page-runner.js is served and owned sessions accept page-origin operator requests.' }
$configArgs = @()
if (Test-Path -LiteralPath $leagueConfigPath) { $configArgs = @('--league-config', $leagueConfigPath) }
& $pythonCommand.Source @pythonPrefix -m draft_assistant --db $database @configArgs @policyArgs serve --host 127.0.0.1 --port $Port @serveArgs
exit $LASTEXITCODE
