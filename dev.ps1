<#
.SYNOPSIS
    Lekha local development commands (Windows PowerShell 5.1+ / PowerShell 7).

.DESCRIPTION
    ./dev.ps1 setup             Install Python deps and create .env with random local secrets
    ./dev.ps1 up                Start PostgreSQL, Redis, Qdrant and object storage
    ./dev.ps1 status            Show container health
    ./dev.ps1 migrate           Apply migrations, provision the API's database login and bucket
    ./dev.ps1 seed              Load the SYNTHETIC dev organisation and users (idempotent)
    ./dev.ps1 seed -Reset       Rebuild the synthetic organisation from scratch
    ./dev.ps1 worker            Run the background ingestion worker
    ./dev.ps1 run               Run the API on http://127.0.0.1:8000 with auto-reload
    ./dev.ps1 check             Format check, lint, type check, import boundaries, unit tests
    ./dev.ps1 test              Unit tests only (no Docker needed)
    ./dev.ps1 test-integration  Integration tests against the running local stack
    ./dev.ps1 eval              Golden-set evaluation with BGE-M3 + local Ollama (slow, ~1 hour)
    ./dev.ps1 backup            Back up PostgreSQL, Qdrant and object storage to ./backups/<stamp>
    ./dev.ps1 verify-backup -From X   Check a backup folder against its checksummed manifest
    ./dev.ps1 restore -From X   Restore a backup folder (REPLACES current data; needs -Yes)
    ./dev.ps1 logs              Follow container logs
    ./dev.ps1 down              Stop containers (data volumes are kept)
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'up', 'status', 'migrate', 'run', 'check', 'test', 'test-integration', 'eval',
                 'backup', 'verify-backup', 'restore', 'logs', 'down', 'seed', 'worker', 'help')]
    [string]$Command = 'help',
    [string]$From,
    [switch]$Yes,
    [switch]$Reset
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$EnvFile = Join-Path $Root '.env'
$ComposeFile = Join-Path $Root 'infra/local/docker-compose.yml'
$GeneratedDir = Join-Path $Root 'infra/local/generated'

function Invoke-Native {
    # Run a native command and stop on a non-zero exit code.
    param([string]$Exe, [string[]]$Arguments)
    # Windows PowerShell 5.1 turns native stderr output (e.g. progress messages) into
    # terminating errors under 'Stop'; rely on the exit code instead.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Exe @Arguments } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE" }
}

function Invoke-Compose {
    param([string[]]$Arguments)
    Assert-EnvFile
    Invoke-Native 'docker' (@('compose', '--env-file', $EnvFile, '-f', $ComposeFile) + $Arguments)
}

function Assert-EnvFile {
    if (-not (Test-Path $EnvFile)) { throw ".env not found. Run './dev.ps1 setup' first." }
}

function Read-EnvFile {
    $values = @{}
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$') { $values[$Matches[1]] = $Matches[2] }
    }
    return $values
}

function New-Secret {
    # 32 random bytes from the OS CSPRNG, hex-encoded (64 chars).
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Content)
    [System.IO.File]::WriteAllText($Path, $Content, (New-Object System.Text.UTF8Encoding($false)))
}

function Initialize-EnvFile {
    $template = Get-Content (Join-Path $Root '.env.example') -Raw
    if (Test-Path $EnvFile) {
        # Keep existing values (secrets are never rotated here); append keys that were
        # added to .env.example since this .env was created.
        $existing = Read-EnvFile
        $missing = @()
        foreach ($line in ($template -split "`r?`n")) {
            if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$' -and -not $existing.ContainsKey($Matches[1])) {
                $value = if ($Matches[2] -eq 'CHANGE_ME') { New-Secret } else { $Matches[2] }
                $missing += "$($Matches[1])=$value"
            }
        }
        if ($missing.Count -gt 0) {
            $current = (Get-Content $EnvFile -Raw).TrimEnd()
            Write-Utf8NoBom $EnvFile ($current + "`n" + ($missing -join "`n") + "`n")
            Write-Host "Added $($missing.Count) new setting(s) to .env."
        } else {
            Write-Host ".env is up to date; existing secrets kept."
        }
    } else {
        # Replace each CHANGE_ME with its own fresh secret.
        $content = [regex]::Replace($template, 'CHANGE_ME', { param($m) New-Secret })
        Write-Utf8NoBom $EnvFile $content
        Write-Host "Created .env with freshly generated local secrets."
    }
    # Object-storage credentials file, derived from .env (git-ignored).
    $vars = Read-EnvFile
    New-Item -ItemType Directory -Force $GeneratedDir | Out-Null
    $s3 = @{
        identities = @(@{
            name        = 'lekha-local'
            credentials = @(@{ accessKey = $vars['LEKHA_S3_ACCESS_KEY']; secretKey = $vars['LEKHA_S3_SECRET_KEY'] })
            actions     = @('Admin', 'Read', 'Write', 'List', 'Tagging')
        })
    }
    Write-Utf8NoBom (Join-Path $GeneratedDir 's3.json') ($s3 | ConvertTo-Json -Depth 6)
}

Push-Location $Root
try {
    switch ($Command) {
        'setup' {
            Invoke-Native 'uv' @('sync', '--locked')
            Initialize-EnvFile
            Write-Host "Setup complete. Next: ./dev.ps1 up ; ./dev.ps1 migrate ; ./dev.ps1 run"
        }
        'up' {
            Initialize-EnvFile
            Invoke-Compose @('up', '-d', '--wait')
            Invoke-Compose @('ps')
        }
        'status' { Invoke-Compose @('ps') }
        'migrate' {
            Invoke-Native 'uv' @('run', '--locked', 'alembic', 'upgrade', 'head')
            # Create/update the API's least-privilege login (password from .env).
            Invoke-Native 'uv' @('run', '--locked', 'python', '-m', 'apps.scripts.provision_db_app_user')
            # Create the document bucket in the local object store if needed.
            Invoke-Native 'uv' @('run', '--locked', 'python', '-m', 'apps.scripts.provision_storage')
        }
        'seed' {
            $seedArgs = @('run', '--locked', 'python', '-m', 'apps.scripts.seed_dev_data')
            if ($Reset) { $seedArgs += '--reset' }
            Invoke-Native 'uv' $seedArgs
        }
        'worker' {
            # Background ingestion worker (extract, chunk, embed, index). UTF-8 output, because
            # ARQ's log lines contain arrows the default Windows console encoding can't print.
            $env:PYTHONIOENCODING = 'utf-8'
            Invoke-Native 'uv' @('run', '--locked', 'arq', 'apps.worker.main.WorkerSettings')
        }
        'run' {
            Invoke-Native 'uv' @('run', '--locked', 'uvicorn', 'apps.api.main:create_app',
                '--factory', '--host', '127.0.0.1', '--port', '8000', '--reload')
        }
        'check' {
            Invoke-Native 'uv' @('run', '--locked', 'ruff', 'format', '--check', '.')
            Invoke-Native 'uv' @('run', '--locked', 'ruff', 'check', '.')
            Invoke-Native 'uv' @('run', '--locked', 'mypy')
            Invoke-Native 'uv' @('run', '--locked', 'lint-imports')
            Invoke-Native 'uv' @('run', '--locked', 'pytest')
        }
        'test' { Invoke-Native 'uv' @('run', '--locked', 'pytest') }
        'test-integration' {
            Assert-EnvFile
            Invoke-Native 'uv' @('run', '--locked', 'pytest', '-m', 'integration')
        }
        'eval' {
            # Real answer quality: BGE-M3 + the local Ollama model; writes docs/eval/*.md.
            Assert-EnvFile
            $env:LEKHA_EVAL_EMBEDDER = 'bge-m3'
            $env:LEKHA_EVAL_LLM = 'ollama'
            $env:LEKHA_EVAL_REPORT = 'docs/eval/golden-ollama-bge-m3.md'
            $env:PYTHONIOENCODING = 'utf-8'
            Invoke-Native 'uv' @('run', '--locked', 'pytest', '-m', 'integration',
                'tests/integration/test_golden.py', '-s')
        }
        'backup' {
            # PostgreSQL + Qdrant + object storage, with a checksummed manifest.
            Invoke-Native 'uv' @('run', '--locked', 'python', '-m', 'apps.scripts.backup', 'create')
        }
        'verify-backup' {
            if (-not $From) { throw "Usage: ./dev.ps1 verify-backup -From backups/<stamp>" }
            Invoke-Native 'uv' @('run', '--locked', 'python', '-m', 'apps.scripts.backup', 'verify', $From)
        }
        'restore' {
            if (-not $From) { throw "Usage: ./dev.ps1 restore -From backups/<stamp> -Yes" }
            if (-not $Yes) { throw "Restore REPLACES the current local data. Re-run with -Yes to confirm." }
            Invoke-Native 'uv' @('run', '--locked', 'python', '-m', 'apps.scripts.backup', 'restore', $From, '--yes')
        }
        'logs' { Invoke-Compose @('logs', '-f', '--tail', '100') }
        'down' { Invoke-Compose @('down') }
        default { Get-Help $PSCommandPath -Detailed }
    }
} finally {
    Pop-Location
}
