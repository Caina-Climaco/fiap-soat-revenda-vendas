# Sobe a infraestrutura do servico de venda no cluster kind local: aplica o Terraform DESTE
# repositorio (namespace vendas, MongoDB 7, segredos e NetworkPolicies). A aplicacao e o
# CronJob de recuperacao sao implantados pelo CD (.github/workflows/cd.yml).
# PRE-REQUISITO: a plataforma do repositorio principal (fiap-soat-revenda-principal) ja no ar:
# cluster "revenda", Kong, observabilidade e o Secret de contrato dos tokens. Este script
# NAO cria o cluster; se ele faltar, rode antes o 04-subir-ambiente.ps1 de la.
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\scripts\windows\04-subir-ambiente.ps1 [-SemBancoExposto]
#     -SemBancoExposto  nao publica o MongoDB em localhost:27017
#
# State: %USERPROFILE%\.revenda\revenda-vendas.tfstate (TF_DATA_DIR em
# %USERPROFILE%\.revenda\terraform-data-vendas), o mesmo arquivo usado pelo CD (bind mount
# em /revenda-state no container do runner). Nunca dentro do repositorio.
# Log: .setup\relatorio-ambiente-subir.txt. Arquivo somente ASCII (Windows PowerShell 5.1).
param(
    [switch]$SemBancoExposto
)

$ErrorActionPreference = "Continue"
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tfDir = Join-Path $raiz "infra\terraform"
$log = Join-Path $raiz ".setup\relatorio-ambiente-subir.txt"
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

Write-Host "04-subir-ambiente.ps1 (servico de venda) - $(Get-Date -Format s)"
foreach ($f in @("docker", "kind", "kubectl", "terraform")) {
    if (-not (Get-Command $f -ErrorAction SilentlyContinue)) {
        Falhar "'$f' nao encontrado no PATH (rode scripts\windows\01-instalar-ferramentas.ps1)."
    }
}
if ((Invocar "docker" @("version", "--format", "docker {{.Server.Version}}")) -ne 0) {
    Falhar "Docker Desktop nao responde. Inicie o Docker Desktop e tente de novo."
}

# ------------------------------------------------------------------ caminhos (state compartilhado com o cd.yml)
$perfil = $env:USERPROFILE -replace '\\', '/'
$stateDir = "$perfil/.revenda"
$statePath = "$stateDir/revenda-vendas.tfstate"
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
$env:TF_DATA_DIR = "$stateDir/terraform-data-vendas"
$env:TF_IN_AUTOMATION = "1"
$env:TF_INPUT = "0"
$env:KUBECONFIG = "$perfil/.kube/config"
$env:TF_VAR_kubeconfig_path = "$perfil/.kube/config"
$env:TF_VAR_expor_banco = if ($SemBancoExposto) { "false" } else { "true" }
Write-Host "State: $statePath | TF_DATA_DIR: $env:TF_DATA_DIR | KUBECONFIG: $env:KUBECONFIG"

# ------------------------------------------------------------------ plataforma do principal (contrato)
if (-not ((ClustersKind) -contains "revenda")) {
    Falhar "cluster kind 'revenda' inexistente. Suba antes a plataforma pelo repositorio fiap-soat-revenda-principal (scripts\windows\04-subir-ambiente.ps1)."
}
if ((Invocar "kind" @("export", "kubeconfig", "--name", "revenda")) -ne 0) { Falhar "kind export kubeconfig falhou." }
& kubectl -n principal get secret contrato-tokens-servico *> $null
if ($LASTEXITCODE -ne 0) {
    Falhar "Secret principal/contrato-tokens-servico ausente. Rode o Terraform do repositorio principal antes."
}
Write-Host "Plataforma do principal OK (cluster e contrato de tokens)."

# ------------------------------------------------------------------ terraform
$codigo = Invocar "terraform" @("-chdir=$tfDir", "init", "-input=false", "-no-color", "-reconfigure", "-backend-config=path=$statePath")
if ($codigo -ne 0) { Falhar "terraform init falhou (codigo $codigo)." }
$codigo = Invocar "terraform" @("-chdir=$tfDir", "apply", "-input=false", "-no-color", "-auto-approve")
if ($codigo -ne 0) { Falhar "terraform apply falhou (codigo $codigo). Diagnostico: kubectl -n vendas get pods,events" }

# ------------------------------------------------------------------ verificacao
$null = Invocar "kubectl" @("-n", "vendas", "get", "pods,svc", "-o", "wide")
$null = Invocar "terraform" @("-chdir=$tfDir", "output", "-no-color", "urls")

Write-Host ""
Write-Host "==================== infraestrutura do servico de venda ===================="
Write-Host "Vitrine (depois do CD)  http://localhost:8080/api/v1/veiculos/a-venda"
Write-Host "Swagger (depois do CD)  http://localhost:8080/vendas/docs"
if (-not $SemBancoExposto) { Write-Host "MongoDB                 localhost:27017 (banco revenda_vendas; mongosh em terraform output comandos_uteis)" }
Write-Host ""
Write-Host "Aplicacao: merge na main ou  gh workflow run cd.yml  (CD no runner kind-local deste repositorio)."
Write-Host "Log: $log"
Stop-Transcript | Out-Null
exit 0
