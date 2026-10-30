param([int]$Port = 8010, [switch]$OcrTunnel)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    uv sync --frozen
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
}
$serveArguments = @('-m', 'shipping_agent.cli', 'serve', '--port', $Port)
if ($OcrTunnel) { $serveArguments += '--ocr-tunnel' }
& '.\.venv\Scripts\python.exe' @serveArguments
