# Remove o servico de venda do cluster local: terraform destroy do state DESTE repositorio
# (namespace vendas, MongoDB, segredos e NetworkPolicies; a API e o CronJob somem junto com o
# namespace) e remove o state. Os dados do MongoDB sao PERDIDOS. A plataforma e o principal
# (outro repositorio) nao sao tocados.
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\scripts\windows\05-destruir-ambiente.ps1 [-Forcar]
# Log: .setup\relatorio-ambiente-destruir.txt. Arquivo somente ASCII (Windows PowerShell 5.1).
param(
    [switch]$Forcar
)

$ErrorActionPreference = "Continue"
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tfDir = Join-Path $raiz "infra\terraform"
$log = Join-Path $raiz ".setup\relatorio-ambiente-destruir.txt"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
Start-Transcript -Path $log -Force | Out-Null

function Invocar {
    param([string]$Exe, [string[]]$Argumentos)
    Write-Host ">> $Exe $($Argumentos -join ' ')"
    $global:LASTEXITCODE = 0
    & $Exe @Argumentos 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" }
    } | Out-Host
    return $LASTEXITCODE
}

function Falhar([string]$motivo) {
    Write-Host ""
    Write-Host "ERRO: $motivo"
    Write-Host "Log: $log"
    Stop-Transcript | Out-Null
    exit 1
}

function ClustersKind {
    $saida = @(& kind get clusters 2>$null)
    if ($LASTEXITCODE -ne 0) { return @() }
    return @($saida | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
}

Write-Host "05-destruir-ambiente.ps1 (servico de venda) - $(Get-Date -Format s)"
foreach ($f in @("docker", "kind", "terraform")) {
    if (-not (Get-Command $f -ErrorAction SilentlyContinue)) { Falhar "'$f' nao encontrado no PATH." }
}

if (-not $Forcar) {
    Write-Host "Isto apaga o namespace vendas (API, MongoDB com as compras e segredos)."
    $resposta = Read-Host "Digite SIM para continuar"
    if ($resposta -cne "SIM") {
        Write-Host "Cancelado."
        Stop-Transcript | Out-Null
        exit 0
    }
}

$perfil = $env:USERPROFILE -replace '\\', '/'
$stateDir = "$perfil/.revenda"
$statePath = "$stateDir/revenda-vendas.tfstate"
$env:TF_DATA_DIR = "$stateDir/terraform-data-vendas"
$env:TF_IN_AUTOMATION = "1"
$env:TF_INPUT = "0"
$env:KUBECONFIG = "$perfil/.kube/config"
$env:TF_VAR_kubeconfig_path = "$perfil/.kube/config"

if (((ClustersKind) -contains "revenda") -and (Test-Path $statePath)) {
    $codigo = Invocar "kind" @("export", "kubeconfig", "--name", "revenda")
    if ($codigo -eq 0) {
        $codigo = Invocar "terraform" @("-chdir=$tfDir", "init", "-input=false", "-no-color", "-reconfigure", "-backend-config=path=$statePath")
    }
    if ($codigo -eq 0) {
        $codigo = Invocar "terraform" @("-chdir=$tfDir", "destroy", "-input=false", "-no-color", "-auto-approve")
    }
    if ($codigo -ne 0) { Falhar "terraform destroy falhou (codigo $codigo); o state foi mantido em $statePath." }
} else {
    Write-Host "Sem cluster 'revenda' ou sem state em $statePath; nada a destruir pelo Terraform."
}

Remove-Item -Path $statePath, "$statePath.backup" -Force -ErrorAction SilentlyContinue
Write-Host "State removido: $statePath"
Write-Host "Servico de venda removido. Para recriar: scripts\windows\04-subir-ambiente.ps1 e depois o CD."
Write-Host "Log: $log"
Stop-Transcript | Out-Null
exit 0
