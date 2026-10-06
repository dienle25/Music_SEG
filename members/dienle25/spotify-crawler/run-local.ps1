param([ValidateSet('run', 'test', 'audit', 'verify', 'evidence', 'publish')][string]$Action = 'run')
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$workspaceRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $projectRoot))
$portablePython = Join-Path $workspaceRoot 'work\python\python.exe'
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) { $projectPython = $venvPython }
elseif (Test-Path -LiteralPath $portablePython) { $projectPython = $portablePython }
else { $projectPython = (Get-Command python -ErrorAction Stop).Source }
Push-Location -LiteralPath $projectRoot
$bootstrap = 'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); module=sys.argv.pop(1); runpy.run_module(module,run_name="__main__",alter_sys=True)'
try {
    switch ($Action) {
        'run' { & $projectPython -X utf8 -c $bootstrap $projectRoot main --max-pages 3 --max-depth 1 }
        'test' { & $projectPython -X utf8 -c $bootstrap $projectRoot unittest discover -s tests -v }
        'audit' { & $projectPython -X utf8 -c $bootstrap $projectRoot main --audit-only }
        'verify' { & $projectPython -X utf8 -c $bootstrap $projectRoot main --verify-db }
        'evidence' { & $projectPython -X utf8 -c $bootstrap $projectRoot scripts.verify }
        'publish' { & $projectPython -X utf8 -c $bootstrap $projectRoot scripts.publish }
    }
    $resultCode = $LASTEXITCODE
} finally { Pop-Location }
exit $resultCode
