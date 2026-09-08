param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$viewerRoot = $PSScriptRoot
$viewerUrl = 'http://127.0.0.1:3132/'
$viewerState = Join-Path $viewerRoot '.local'
New-Item -ItemType Directory -Path $viewerState -Force | Out-Null

function Test-ViewerReady {
    try {
        $response = Invoke-WebRequest -Uri $viewerUrl -UseBasicParsing -TimeoutSec 2
        return ($response.StatusCode -eq 200 -and $response.Content.Contains('<title>Transcript Viewer</title>'))
    } catch {
        return $false
    }
}

if (-not (Test-ViewerReady)) {
    $viewerPython = 'C:\Users\XiaoyunLiu\AppData\Local\Programs\Python\Python312\python.exe'
    if (-not (Test-Path -LiteralPath $viewerPython)) {
        $viewerPython = (Get-Command python -ErrorAction Stop).Source
    }
    $viewerArguments = @(
        '-u', 'server.py', '--host', '127.0.0.1', '--port', '3132',
        '--cursor-db', ('"' + (Join-Path $env:APPDATA 'Cursor\User\globalStorage\state.vscdb') + '"'),
        '--cursor-projects-dir', ('"' + (Join-Path $env:USERPROFILE '.cursor\projects') + '"'),
        '--cursor-chats-dir', ('"' + (Join-Path $env:USERPROFILE '.cursor\chats') + '"'),
        '--projects-dir', '".local\unused\claude"',
        '--codex-home', '".local\unused\codex"',
        '--opencode-db', '".local\unused\opencode.db"',
        '--custom-names-file', '".local\names.json"'
    )
    $viewerProcess = Start-Process -FilePath $viewerPython -ArgumentList $viewerArguments `
        -WorkingDirectory $viewerRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $viewerState 'server.stdout.log') `
        -RedirectStandardError (Join-Path $viewerState 'server.stderr.log')
    Set-Content -LiteralPath (Join-Path $viewerState 'server.pid') -Value $viewerProcess.Id
    $viewerReady = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if (Test-ViewerReady) { $viewerReady = $true; break }
        $viewerProcess.Refresh()
        if ($viewerProcess.HasExited) {
            throw ('Viewer failed to start. See ' + (Join-Path $viewerState 'server.stderr.log'))
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not $viewerReady) { throw 'Viewer did not become ready on port 3132.' }
}

if (-not $NoBrowser) { Start-Process $viewerUrl }
Write-Output $viewerUrl
