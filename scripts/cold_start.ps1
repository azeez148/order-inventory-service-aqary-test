# Verify committed code in a disposable local clone with fresh service volumes.
$ErrorActionPreference = 'Stop'
$repoPath = Split-Path -Parent $PSScriptRoot
$runId = [Guid]::NewGuid().ToString('N').Substring(0, 12)
$projectName = "inventory-cold-$runId"
$checkoutPath = Join-Path ([IO.Path]::GetTempPath()) $projectName
$oldApiPort = $env:API_PORT
$oldPgPort = $env:PGBOUNCER_PORT
$started = $false
$pushed = $false

function Invoke-CheckedCompose {
    param([string[]] $ComposeArgs)
    & docker compose -p $projectName @ComposeArgs
    if ($LASTEXITCODE -ne 0) { throw "Compose failed: $($ComposeArgs -join ' ')" }
}

try {
    & git clone --no-hardlinks $repoPath $checkoutPath
    if ($LASTEXITCODE -ne 0) { throw 'Local clone failed' }
    Push-Location -LiteralPath $checkoutPath
    $pushed = $true
    Write-Host "Verifying commit $(& git rev-parse HEAD) in $projectName"
    # Docker assigns unused host ports; service-to-service URLs stay unchanged.
    $env:API_PORT = '0'
    $env:PGBOUNCER_PORT = '0'
    $started = $true
    Invoke-CheckedCompose @('up', '-d', '--build', '--wait', '--wait-timeout', '180')
    Invoke-CheckedCompose @('run', '--rm', '--no-deps', 'api', 'python', '-m', 'loadtest.smoke')
    Invoke-CheckedCompose @('stop', 'api')
    Invoke-CheckedCompose @('run', '--rm', '--no-deps', 'api', 'python', '-m', 'loadtest.outbox')
    Invoke-CheckedCompose @('up', '-d', '--wait', '--wait-timeout', '180')
    Invoke-CheckedCompose @('run', '--rm', '--no-deps', 'api', 'python', '-m', 'scripts.bulk_seed')
    Invoke-CheckedCompose @('run', '--rm', '--no-deps', 'api', 'python', '-m', 'loadtest.catalog')
    Invoke-CheckedCompose @('run', '--rm', '--no-deps', 'api', 'python', 'loadtest/loadtest.py', '--base-url', 'http://api:8000')
    Write-Host 'PASS: fresh-clone startup, smoke, outbox, bulk catalog and mixed-load gates'
}
finally {
    if ($started) {
        & docker compose -p $projectName logs --tail 10 api
        & docker compose -p $projectName down --volumes --remove-orphans
        if ($LASTEXITCODE -ne 0) { Write-Warning "Cleanup failed for $projectName" }
    }
    if ($pushed) { Pop-Location }
    $env:API_PORT = $oldApiPort
    $env:PGBOUNCER_PORT = $oldPgPort
    # Remove only the unique checkout created by this invocation, inside TEMP.
    $resolvedCheckout = [IO.Path]::GetFullPath($checkoutPath)
    $resolvedTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolvedCheckout.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing cleanup outside TEMP: $resolvedCheckout"
    }
    if (Test-Path -LiteralPath $resolvedCheckout) {
        Remove-Item -LiteralPath $resolvedCheckout -Recurse -Force
    }
}
