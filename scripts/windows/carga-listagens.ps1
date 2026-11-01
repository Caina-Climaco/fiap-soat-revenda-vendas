# Teste de carga das listagens (RNF-10) com o k6 em container, na rede docker do kind
# (fala com o Kong pelo nome do no, como o runner do CD). Resultado no console e em
# .setup\relatorio-carga.txt; o k6 termina com erro se o p95 passar de 300 ms ou se mais
# de 1% das requisicoes falhar.
#
# Uso: powershell -ExecutionPolicy Bypass -File .\scripts\windows\carga-listagens.ps1
# Arquivo somente ASCII (Windows PowerShell 5.1).
$ErrorActionPreference = "Continue"
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$log = Join-Path $raiz ".setup\relatorio-carga.txt"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
$script = Join-Path $raiz "tests\carga"

& docker run --rm --network kind -v "${script}:/carga:ro" grafana/k6:2.2.0 run /carga/listagens.js 2>&1 |
    Tee-Object -FilePath $log
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERRO: limites do teste de carga nao atendidos (ou k6 falhou). Log: $log"
    exit 1
}
Write-Host "Teste de carga OK. Log: $log"
