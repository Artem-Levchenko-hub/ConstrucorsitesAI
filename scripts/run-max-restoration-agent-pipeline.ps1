[CmdletBinding()]
param(
    [ValidateRange(1, 8)]
    [int]$MaxIterations = 4,
    [ValidateRange(1, 2)]
    [int]$MaxContractPasses = 2,
    [string]$AstraModel = "gpt-6-astra",
    [string]$SolModel = "gpt-5.6-sol",
    [ValidateRange(5, 240)]
    [int]$AgentTimeoutMinutes = 120,
    [ValidateRange(5, 360)]
    [int]$GateTimeoutMinutes = 180,
    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pipelineRoot = Join-Path $repoRoot ".codex\pipelines\max-restoration"
$artifactRoot = Join-Path $repoRoot ".artifacts\max-restoration-pipeline"
$verdictSchema = Join-Path $pipelineRoot "verdict.schema.json"

function Invoke-Native {
    param([Parameter(Mandatory)][string]$FilePath, [Parameter(Mandatory)][string[]]$Arguments)
    $output = & $FilePath @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE`n$($output -join [Environment]::NewLine)"
    }
    return $output
}

function Stop-ProcessTree {
    param([Parameter(Mandatory)][int]$ProcessId)
    if ($IsWindows) {
        & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
        return
    }
    & pkill -TERM -P $ProcessId 2>$null
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Invoke-RedirectedProcess {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$Arguments,
        [string]$InputPath,
        [Parameter(Mandatory)][string]$StandardOutputPath,
        [Parameter(Mandatory)][string]$StandardErrorPath,
        [Parameter(Mandatory)][int]$TimeoutMilliseconds
    )
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in $Arguments) { $startInfo.ArgumentList.Add($argument) }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $deadline = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $process.Start() | Out-Null
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if ($InputPath) {
            $process.StandardInput.Write((Get-Content -LiteralPath $InputPath -Raw))
        }
        $process.StandardInput.Close()
        $completed = $process.WaitForExit($TimeoutMilliseconds)
        if (-not $completed) {
            Stop-ProcessTree -ProcessId $process.Id
            $process.WaitForExit(5000) | Out-Null
        }
        $remainingMilliseconds = [Math]::Max(
            0,
            $TimeoutMilliseconds - [int][Math]::Min($deadline.ElapsedMilliseconds, [int]::MaxValue)
        )
        $outputTasks = [System.Threading.Tasks.Task[]]@($stdoutTask, $stderrTask)
        $outputDrained = $remainingMilliseconds -gt 0 -and [System.Threading.Tasks.Task]::WaitAll(
            $outputTasks,
            $remainingMilliseconds
        )
        if (-not $completed -or -not $outputDrained) {
            Stop-ProcessTree -ProcessId $process.Id
            $process.StandardOutput.Dispose()
            $process.StandardError.Dispose()
            throw [System.TimeoutException]::new("Process or redirected output exceeded the shared deadline")
        }
        $utf8 = [System.Text.UTF8Encoding]::new($false)
        [System.IO.File]::WriteAllText(
            $StandardOutputPath, $stdoutTask.GetAwaiter().GetResult(), $utf8
        )
        [System.IO.File]::WriteAllText(
            $StandardErrorPath, $stderrTask.GetAwaiter().GetResult(), $utf8
        )
        return $process.ExitCode
    }
    finally {
        $deadline.Stop()
        $process.Dispose()
    }
}

function Assert-PipelineFiles {
    $required = @(
        "docs\testing\max-restoration-test-contract.md",
        "docs\operations\2026-09-21-max-studio-rollback-live-result.md",
        "docs\superpowers\plans\2026-09-21-adaptive-rollback-production-readiness.md",
        ".codex\pipelines\max-restoration\01-test-author.md",
        ".codex\pipelines\max-restoration\02-test-review.md",
        ".codex\pipelines\max-restoration\03-implement.md",
        ".codex\pipelines\max-restoration\04-code-review.md",
        ".codex\pipelines\max-restoration\verdict.schema.json"
    )
    foreach ($relative in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $repoRoot $relative) -PathType Leaf)) {
            throw "Missing pipeline file: $relative"
        }
    }
    Get-Content -LiteralPath $verdictSchema -Raw | ConvertFrom-Json | Out-Null
    $null = Get-Command codex -ErrorAction Stop
    $null = Invoke-Native -FilePath "git" -Arguments @("-C", $repoRoot, "rev-parse", "--show-toplevel")
}

function Get-RepositoryChanges {
    param([Parameter(Mandatory)][string]$BaseSha)
    $tracked = @(& git -C $repoRoot diff --name-only $BaseSha --)
    if ($LASTEXITCODE -ne 0) { throw "Unable to list tracked changes" }
    $untracked = @(& git -C $repoRoot ls-files --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) { throw "Unable to list untracked changes" }
    return @(
        $tracked + $untracked |
            Where-Object { $_ -and (($_ -replace '\\', '/') -notlike '.artifacts/max-restoration-pipeline/*') } |
            Sort-Object -Unique
    )
}

function Test-IsContractPath {
    param([Parameter(Mandatory)][string]$Path)
    $normalized = $Path -replace '\\', '/'
    $allowed = @(
        '^\.codex/pipelines/max-restoration/',
        '^scripts/run-max-restoration-agent-pipeline\.ps1$',
        '^docs/operations/2026-09-21-max-studio-rollback-live-result\.md$',
        '^docs/superpowers/plans/2026-09-21-adaptive-rollback-production-readiness\.md$',
        '^apps/(api|orchestrator)/tests/',
        '^apps/(api|orchestrator)/pyproject\.toml$',
        '^apps/(api|orchestrator)/uv\.lock$',
        '^apps/web/src/.+\.test\.tsx?$',
        '^apps/web/(e2e|playwright)/',
        '^apps/web/(package\.json|pnpm-lock\.yaml|vitest\.config\.ts|playwright\.config\.ts)$',
        '^scripts/test-max-restoration\.ps1$',
        '^scripts/max_restoration_acceptance\.py$',
        '^docs/testing/',
        '^tools/restoration_contract/',
        '^\.github/workflows/',
        '(^|/)tests?/',
        '(^|/)(conftest|sitecustomize|usercustomize|pytest_plugins)\.py$',
        '(^|/)(pytest\.ini|tox\.ini|setup\.cfg|pyproject\.toml|\.coveragerc)$',
        '(^|/)(pytest|coverage|vitest|playwright|jest)\.config\.[^/]+$',
        '(^|/)(package\.json|uv\.lock|pnpm-lock\.yaml|package-lock\.json|yarn\.lock)$',
        '\.(spec|test)\.(py|tsx?|jsx?)$'
    )
    return [bool]($allowed | Where-Object { $normalized -match $_ })
}

function Assert-TestStageScope {
    param([Parameter(Mandatory)][string[]]$Paths)
    foreach ($path in $Paths) {
        $normalized = $path -replace '\\', '/'
        if (-not ($testAuthorAllowedPatterns | Where-Object { $normalized -match $_ })) {
            throw "Test-author stage changed forbidden path: $path"
        }
    }
}

function Get-FrozenContractPaths {
    $all = @(
        & git -C $repoRoot ls-files --cached --others --exclude-standard
    )
    if ($LASTEXITCODE -ne 0) { throw "Unable to enumerate the contract surface" }
    return @($all | Where-Object { $_ -and (Test-IsContractPath -Path $_) } | Sort-Object -Unique)
}

function Get-CandidateTreeDigest {
    $paths = @(
        & git -C $repoRoot ls-files --cached --others --exclude-standard |
            Where-Object { $_ -and (($_ -replace '\\', '/') -notlike '.artifacts/*') } |
            Sort-Object -Unique
    )
    if ($LASTEXITCODE -ne 0) { throw "Unable to enumerate candidate files" }
    $hash = [System.Security.Cryptography.IncrementalHash]::CreateHash(
        [System.Security.Cryptography.HashAlgorithmName]::SHA256
    )
    try {
        foreach ($relative in $paths) {
            $pathBytes = [System.Text.Encoding]::UTF8.GetBytes(($relative -replace '\\', '/') + "`0")
            $hash.AppendData($pathBytes)
            $full = Join-Path $repoRoot $relative
            if (Test-Path -LiteralPath $full -PathType Leaf) {
                $hash.AppendData([System.IO.File]::ReadAllBytes($full))
            }
            else {
                $hash.AppendData([System.Text.Encoding]::UTF8.GetBytes("<deleted>"))
            }
            $hash.AppendData([byte[]](0))
        }
        return [Convert]::ToHexString($hash.GetHashAndReset()).ToLowerInvariant()
    }
    finally {
        $hash.Dispose()
    }
}

function Read-GateSummary {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][ValidateSet("baseline", "full")][string]$ExpectedKind
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Gate did not produce required summary: $Path"
    }
    $summary = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    foreach ($property in @("status", "classification", "failed_case_ids", "critical_skip_count")) {
        if ($null -eq $summary.PSObject.Properties[$property]) {
            throw "Gate summary is missing '$property': $Path"
        }
    }
    if (
        $summary.critical_skip_count -isnot [int] -and
        $summary.critical_skip_count -isnot [long]
    ) {
        throw "Gate summary critical_skip_count must be an integer: $Path"
    }
    if ([long]$summary.critical_skip_count -lt 0) {
        throw "Gate summary critical_skip_count cannot be negative: $Path"
    }
    if ($null -eq $summary.failed_case_ids) {
        throw "Gate summary failed_case_ids must be an array, not null: $Path"
    }
    $failedCaseIds = @($summary.failed_case_ids)
    $contractText = Get-Content -LiteralPath (
        Join-Path $repoRoot "docs\testing\max-restoration-test-contract.md"
    ) -Raw
    foreach ($caseId in $failedCaseIds) {
        if ($caseId -isnot [string] -or -not $caseId.Trim()) {
            throw "Gate summary contains an invalid failed case ID: $Path"
        }
        if (-not $contractText.Contains("``$caseId``")) {
            throw "Gate summary names a case absent from the frozen contract: $caseId"
        }
    }
    if ([long]$summary.critical_skip_count -ne 0) {
        throw "Gate reported critical skipped tests: $Path"
    }
    if ($ExpectedKind -eq "baseline") {
        if ($summary.status -ne "failed" -or $summary.classification -ne "product_defect") {
            throw "Baseline RED must be a product_defect, not an infrastructure/test failure: $Path"
        }
        if ($failedCaseIds.Count -eq 0) {
            throw "Baseline RED contains no failed contract case IDs: $Path"
        }
    }
    elseif ($summary.status -eq "passed") {
        if ($summary.classification -ne "passed" -or $failedCaseIds.Count -ne 0) {
            throw "Passed full gate still lists failed contract IDs: $Path"
        }
    }
    elseif (
        $summary.status -ne "failed" -or
        $summary.classification -ne "product_defect" -or
        $failedCaseIds.Count -eq 0
    ) {
        throw "Full gate failure is not an actionable product defect: $Path"
    }
    return $summary
}

<#
    Test author may change only this surface. The implementation agent is checked
    against a larger frozen snapshot returned by Get-FrozenContractPaths.
#>
$testAuthorAllowedPatterns = @(
        '^apps/(api|orchestrator)/tests/',
        '^apps/web/src/.+\.test\.tsx?$',
        '^apps/web/(e2e|playwright)/',
        '^apps/(api|orchestrator)/pyproject\.toml$',
        '^apps/(api|orchestrator)/uv\.lock$',
        '^apps/web/(package\.json|pnpm-lock\.yaml|vitest\.config\.ts|playwright\.config\.ts)$',
        '^scripts/test-max-restoration\.ps1$',
        '^scripts/max_restoration_acceptance\.py$',
        '^docs/testing/',
        '^tools/restoration_contract/',
        '^\.github/workflows/(ci|restoration-contract)\.yml$'
)

function Save-FrozenManifest {
    param([Parameter(Mandatory)][string[]]$Paths, [Parameter(Mandatory)][string]$OutputPath)
    $entries = foreach ($relative in $Paths) {
        $full = Join-Path $repoRoot $relative
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
            throw "Cannot freeze missing test-contract file: $relative"
        }
        [ordered]@{
            path = ($relative -replace '\\', '/')
            sha256 = (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    @($entries) | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $OutputPath -Encoding utf8
    return @($entries)
}

function Assert-FrozenManifest {
    param([Parameter(Mandatory)][object[]]$Entries)
    if ($Entries.Count -eq 0) { throw "Frozen contract manifest cannot be empty" }
    foreach ($entry in $Entries) {
        $full = Join-Path $repoRoot ([string]$entry.path)
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
            throw "Implementation removed frozen contract file: $($entry.path)"
        }
        $actual = (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne [string]$entry.sha256) {
            throw "Implementation changed frozen contract file: $($entry.path)"
        }
    }
}

function Add-FrozenRegressionTests {
    param(
        [Parameter(Mandatory)][object[]]$Entries,
        [Parameter(Mandatory)][string]$ManifestPath
    )
    $known = @{}
    foreach ($entry in $Entries) { $known[[string]$entry.path] = $true }
    $currentContractPaths = @(Get-FrozenContractPaths)
    $newPaths = @(
        $currentContractPaths |
            Where-Object { -not $known.ContainsKey(($_ -replace '\\', '/')) }
    )
    $allowedNewTestPatterns = @(
        '^apps/(api|orchestrator)/tests/(e2e/)?test_[^/]+\.py$',
        '^apps/web/src/.+\.test\.tsx?$',
        '^apps/web/e2e/[^/]+\.(spec|test)\.tsx?$'
    )
    foreach ($relative in $newPaths) {
        $normalized = $relative -replace '\\', '/'
        if (-not ($allowedNewTestPatterns | Where-Object { $normalized -match $_ })) {
            throw "Implementation added an unapproved contract/config file: $relative"
        }
        $Entries += [ordered]@{
            path = $normalized
            sha256 = (Get-FileHash -LiteralPath (Join-Path $repoRoot $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    @($Entries) | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ManifestPath -Encoding utf8
    return @($Entries)
}

function Invoke-CodexStage {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Model,
        [Parameter(Mandatory)][string]$Effort,
        [Parameter(Mandatory)][string]$PromptPath,
        [Parameter(Mandatory)][ValidateSet("read-only", "workspace-write")][string]$Sandbox,
        [string]$SchemaPath
    )
    $stageDir = Join-Path $runDir $Name
    New-Item -ItemType Directory -Force -Path $stageDir | Out-Null
    $stdoutPath = Join-Path $stageDir "events.jsonl"
    $stderrPath = Join-Path $stageDir "stderr.log"
    $lastMessagePath = Join-Path $stageDir "last-message.json"
    $arguments = @(
        "exec", "-m", $Model,
        "-c", "model_reasoning_effort=$Effort",
        "--sandbox", $Sandbox,
        "--ignore-user-config",
        "--json",
        "--output-last-message", $lastMessagePath,
        "-C", $repoRoot
    )
    if ($SchemaPath) {
        $arguments += @("--output-schema", $SchemaPath)
    }
    $arguments += "-"
    $codexCommand = Get-Command codex -ErrorAction Stop
    if ($codexCommand.CommandType -eq [System.Management.Automation.CommandTypes]::ExternalScript) {
        $processFile = (Get-Command pwsh -ErrorAction Stop).Source
        $processArguments = @("-NoLogo", "-NoProfile", "-File", $codexCommand.Source) + $arguments
    }
    else {
        $processFile = $codexCommand.Source
        $processArguments = $arguments
    }
    try {
        $codexExitCode = Invoke-RedirectedProcess -FilePath $processFile `
            -Arguments $processArguments -InputPath $PromptPath `
            -StandardOutputPath $stdoutPath -StandardErrorPath $stderrPath `
            -TimeoutMilliseconds ($AgentTimeoutMinutes * 60 * 1000)
    }
    catch [System.TimeoutException] {
        $timeoutReceipt = [ordered]@{
            status = "blocked"
            classification = "timeout"
            stage = $Name
            timeout_minutes = $AgentTimeoutMinutes
        }
        $timeoutReceipt | ConvertTo-Json | Set-Content (Join-Path $stageDir "timeout.json") -Encoding utf8
        throw "Codex stage '$Name' exceeded $AgentTimeoutMinutes minutes."
    }
    if ($codexExitCode -ne 0) {
        throw "Codex stage '$Name' failed with exit code $codexExitCode. See $stderrPath"
    }
    if (-not (Test-Path -LiteralPath $lastMessagePath -PathType Leaf)) {
        throw "Codex stage '$Name' did not produce $lastMessagePath"
    }
    return $lastMessagePath
}

function New-CombinedPrompt {
    param(
        [Parameter(Mandatory)][string]$TemplatePath,
        [Parameter(Mandatory)][string]$OutputPath,
        [string[]]$ContextLines = @()
    )
    $body = Get-Content -LiteralPath $TemplatePath -Raw
    if ($ContextLines.Count -gt 0) {
        $body += "`n`n# Pipeline context`n`n" + ($ContextLines -join "`n") + "`n"
    }
    Set-Content -LiteralPath $OutputPath -Value $body -Encoding utf8
}

function Read-Verdict {
    param([Parameter(Mandatory)][string]$Path)
    $verdict = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    if ($verdict.status -notin @("approved", "changes_required", "blocked")) {
        throw "Invalid reviewer status in $Path"
    }
    $blockingFindings = @($verdict.findings | Where-Object { $_.priority -in @("P0", "P1", "P2") })
    if (
        $verdict.status -eq "approved" -and
        ($blockingFindings.Count -gt 0 -or @($verdict.failed_gates).Count -gt 0)
    ) {
        throw "Inconsistent approved verdict contains blocking findings or failed gates: $Path"
    }
    return $verdict
}

function Invoke-Gate {
    param(
        [Parameter(Mandatory)][string]$Profile,
        [Parameter(Mandatory)][string]$StageName,
        [Parameter(Mandatory)][ValidateSet("Local", "Release")][string]$AcceptanceMode,
        [Parameter(Mandatory)][string]$CandidateIdentity
    )
    $runner = Join-Path $repoRoot "scripts\test-max-restoration.ps1"
    if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
        throw "Astra did not create scripts/test-max-restoration.ps1"
    }
    $gateDir = Join-Path $runDir $StageName
    New-Item -ItemType Directory -Force -Path $gateDir | Out-Null
    $logPath = Join-Path $gateDir "gate.log"
    $errorLogPath = Join-Path $gateDir "gate.stderr.log"
    try {
        $gateExitCode = Invoke-RedirectedProcess `
            -FilePath (Get-Command pwsh -ErrorAction Stop).Source `
            -Arguments @(
                "-NoLogo", "-NoProfile", "-File", $runner,
                "-Profile", $Profile, "-ArtifactsDir", $gateDir,
                "-AcceptanceMode", $AcceptanceMode, "-CandidateIdentity", $CandidateIdentity
            ) -StandardOutputPath $logPath -StandardErrorPath $errorLogPath `
            -TimeoutMilliseconds ($GateTimeoutMinutes * 60 * 1000)
    }
    catch [System.TimeoutException] {
        $timeoutReceipt = [ordered]@{
            status = "blocked_environment"
            classification = "timeout"
            profile = $Profile
            timeout_minutes = $GateTimeoutMinutes
        }
        $timeoutReceipt | ConvertTo-Json | Set-Content (Join-Path $gateDir "timeout.json") -Encoding utf8
        throw "Gate '$StageName' exceeded $GateTimeoutMinutes minutes."
    }
    if (Test-Path -LiteralPath $logPath) { Get-Content -LiteralPath $logPath | Out-Host }
    if (Test-Path -LiteralPath $errorLogPath) { Get-Content -LiteralPath $errorLogPath | Out-Host }
    return [pscustomobject][ordered]@{
        exit_code = $gateExitCode
        directory = $gateDir
        summary = Join-Path $gateDir "gate-summary.json"
        fingerprint = Join-Path $gateDir "failure-fingerprint.txt"
        log = $logPath
        error_log = $errorLogPath
    }
}

Assert-PipelineFiles
if ($ValidateOnly) {
    Write-Host "MAX restoration pipeline files, JSON schema, Git, and Codex CLI are valid."
    exit 0
}

$dirtyTracked = @(& git -C $repoRoot status --porcelain --untracked-files=all)
if ($LASTEXITCODE -ne 0) { throw "Unable to read Git status" }
if ($dirtyTracked.Count -gt 0) {
    throw "Run the pipeline from a clean checkout. Tracked or untracked changes are present."
}

$baseSha = (& git -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Unable to resolve HEAD" }
$runId = Get-Date -Format "yyyyMMdd-HHmmss"
$runDir = Join-Path $artifactRoot $runId
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$branchName = "codex/max-restoration-$runId"
Invoke-Native -FilePath "git" -Arguments @("-C", $repoRoot, "switch", "-c", $branchName) | Out-Null

$testAuthorTemplate = Join-Path $pipelineRoot "01-test-author.md"
$testReviewTemplate = Join-Path $pipelineRoot "02-test-review.md"
$implementTemplate = Join-Path $pipelineRoot "03-implement.md"
$codeReviewTemplate = Join-Path $pipelineRoot "04-code-review.md"

$testVerdict = $null
for ($contractPass = 1; $contractPass -le $MaxContractPasses; $contractPass++) {
    $authorPrompt = Join-Path $runDir "test-author-$contractPass.md"
    $authorContext = @("Baseline SHA: $baseSha", "Contract pass: $contractPass of $MaxContractPasses")
    if ($testVerdict) {
        $previousVerdict = Join-Path $runDir "test-review-$($contractPass - 1)\last-message.json"
        $authorContext += "Correct every issue in the previous independent verdict: $previousVerdict"
    }
    New-CombinedPrompt -TemplatePath $testAuthorTemplate -OutputPath $authorPrompt -ContextLines $authorContext
    Invoke-CodexStage -Name "test-author-$contractPass" -Model $AstraModel -Effort "xhigh" `
        -PromptPath $authorPrompt -Sandbox "workspace-write" | Out-Null

    $testStagePaths = @(Get-RepositoryChanges -BaseSha $baseSha)
    Assert-TestStageScope -Paths $testStagePaths
    if ($testStagePaths.Count -eq 0) { throw "Astra produced no executable test changes" }

    $reviewPrompt = Join-Path $runDir "test-review-$contractPass.md"
    New-CombinedPrompt -TemplatePath $testReviewTemplate -OutputPath $reviewPrompt `
        -ContextLines @("Baseline SHA: $baseSha", "Review the current uncommitted test-contract diff.")
    $reviewMessage = Invoke-CodexStage -Name "test-review-$contractPass" -Model $AstraModel `
        -Effort "xhigh" -PromptPath $reviewPrompt -Sandbox "read-only" -SchemaPath $verdictSchema
    $testVerdict = Read-Verdict -Path $reviewMessage
    if ($testVerdict.status -eq "approved") { break }
    if ($testVerdict.status -eq "blocked") {
        throw "Astra marked the test contract blocked. See $reviewMessage"
    }
}
if (-not $testVerdict -or $testVerdict.status -ne "approved") {
    throw "The pre-implementation test contract was not approved within $MaxContractPasses passes."
}

$frozenPaths = @(Get-FrozenContractPaths)
$frozenManifest = Join-Path $runDir "frozen-test-contract.json"
$frozenEntries = @(Save-FrozenManifest -Paths $frozenPaths -OutputPath $frozenManifest)

$baselineTreeDigestBeforeGate = Get-CandidateTreeDigest
$baselineGate = Invoke-Gate -Profile "Contract" -StageName "baseline-red" `
    -AcceptanceMode "Local" -CandidateIdentity $baselineTreeDigestBeforeGate
Assert-FrozenManifest -Entries $frozenEntries
$baselineTreeDigestAfterGate = Get-CandidateTreeDigest
if ($baselineTreeDigestAfterGate -ne $baselineTreeDigestBeforeGate) {
    throw "The baseline tree changed while the deterministic gate was running."
}
if ($baselineGate.exit_code -eq 0) {
    throw "The baseline contract unexpectedly passed. Astra must add tests for the observed defects."
}
$null = Read-GateSummary -Path $baselineGate.summary -ExpectedKind "baseline"

$previousFingerprint = ""
$repeatCount = 0
$lastReviewPath = ""
for ($iteration = 1; $iteration -le $MaxIterations; $iteration++) {
    $implementationPrompt = Join-Path $runDir "implementation-$iteration.md"
    $context = @(
        "Iteration: $iteration of $MaxIterations",
        "Frozen manifest: $frozenManifest",
        "Latest deterministic gate summary: $($baselineGate.summary)",
        "Latest deterministic gate log: $($baselineGate.log)"
    )
    if ($lastReviewPath) { $context += "Latest independent Astra verdict: $lastReviewPath" }
    New-CombinedPrompt -TemplatePath $implementTemplate -OutputPath $implementationPrompt -ContextLines $context
    Invoke-CodexStage -Name "implementation-$iteration" -Model $SolModel -Effort "xhigh" `
        -PromptPath $implementationPrompt -Sandbox "workspace-write" | Out-Null
    $frozenEntries = @(Add-FrozenRegressionTests -Entries $frozenEntries -ManifestPath $frozenManifest)
    Assert-FrozenManifest -Entries $frozenEntries

    $candidateTreeDigestBeforeGate = Get-CandidateTreeDigest
    $fullGate = Invoke-Gate -Profile "Full" -StageName "full-gate-$iteration" `
        -AcceptanceMode "Local" -CandidateIdentity $candidateTreeDigestBeforeGate
    Assert-FrozenManifest -Entries $frozenEntries
    $candidateTreeDigestAfterGate = Get-CandidateTreeDigest
    if ($candidateTreeDigestAfterGate -ne $candidateTreeDigestBeforeGate) {
        throw "The candidate tree changed while the deterministic gate was running."
    }
    $fullGateSummary = Read-GateSummary -Path $fullGate.summary -ExpectedKind "full"
    if (($fullGate.exit_code -eq 0) -ne ($fullGateSummary.status -eq "passed")) {
        throw "Full gate exit code and structured status disagree: $($fullGate.summary)"
    }

    $reviewPrompt = Join-Path $runDir "code-review-$iteration.md"
    New-CombinedPrompt -TemplatePath $codeReviewTemplate -OutputPath $reviewPrompt -ContextLines @(
        "Acceptance mode: Local. Review the uncommitted candidate against its controller-owned tree digest. Do not require commit, deploy, or production live acceptance in this stage; list them only as later Release gates.",
        "Baseline SHA: $baseSha",
        "Local candidate tree digest: $candidateTreeDigestAfterGate",
        "Frozen manifest: $frozenManifest",
        "Deterministic gate summary: $($fullGate.summary)",
        "Deterministic gate log: $($fullGate.log)"
    )
    $lastReviewPath = Invoke-CodexStage -Name "code-review-$iteration" -Model $AstraModel `
        -Effort "xhigh" -PromptPath $reviewPrompt -Sandbox "read-only" -SchemaPath $verdictSchema
    $codeVerdict = Read-Verdict -Path $lastReviewPath
    if ($codeVerdict.status -eq "blocked") {
        $blockedResult = [ordered]@{
            status = "blocked"
            base_sha = $baseSha
            branch = $branchName
            iteration = $iteration
            gate_summary = $fullGate.summary
            astra_verdict = $lastReviewPath
        }
        $blockedResult | ConvertTo-Json -Depth 5 |
            Set-Content (Join-Path $runDir "pipeline-result.json") -Encoding utf8
        throw "Astra marked implementation blocked. See $lastReviewPath"
    }

    if ($fullGate.exit_code -eq 0 -and $codeVerdict.status -eq "approved") {
        $result = [ordered]@{
            status = "verified"
            base_sha = $baseSha
            candidate_tree_digest = $candidateTreeDigestAfterGate
            branch = $branchName
            iterations = $iteration
            frozen_manifest = $frozenManifest
            gate_summary = $fullGate.summary
            astra_verdict = $lastReviewPath
        }
        $result | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $runDir "pipeline-result.json") -Encoding utf8
        Write-Host "Verified local candidate produced on $branchName. Commit it, rerun the full gate on that exact SHA, then perform the authorized delivery gate."
        exit 0
    }

    $fingerprint = ""
    if (Test-Path -LiteralPath $fullGate.fingerprint -PathType Leaf) {
        $fingerprint = (Get-Content -LiteralPath $fullGate.fingerprint -Raw).Trim()
    }
    if (-not $fingerprint) { $fingerprint = [string]$codeVerdict.failure_fingerprint }
    if (-not $fingerprint) {
        $fingerprint = (Get-FileHash -LiteralPath $fullGate.log -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    if ($fingerprint -eq $previousFingerprint) { $repeatCount++ } else { $repeatCount = 1 }
    if ($repeatCount -ge 2) {
        throw "Stopped to prevent an infinite loop: failure fingerprint repeated twice: $fingerprint"
    }
    $previousFingerprint = $fingerprint
    $baselineGate = $fullGate
}

throw "The candidate did not pass all deterministic gates and Astra review in $MaxIterations iterations. See $runDir"
