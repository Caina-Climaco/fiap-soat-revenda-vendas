# Cria o repositorio no GitHub, faz o commit inicial e protege a main.
# Uso (na raiz do repositorio):
#   powershell -ExecutionPolicy Bypass -File .\scripts\windows\02-criar-repositorio.ps1
#   (para atualizar os checks obrigatorios depois que o CI existir:)
#   powershell -ExecutionPolicy Bypass -File .\scripts\windows\02-criar-repositorio.ps1 -Checks qualidade,testes,contrato,sonarcloud,imagem,infra
# O commit e o push saem pela conta configurada no git e no gh desta maquina.
# Arquivo somente ASCII (compatibilidade com Windows PowerShell 5.1).
param(
    [string]$Dono = "Caina-Climaco",
    [string]$Nome = "fiap-soat-revenda-vendas",
    [string]$Descricao = "Revenda de veiculos - servico de venda (listagens, compras e saga) - Tech Challenge FIAP SOAT Fase 4",
    [string[]]$Checks = @()
)
$ErrorActionPreference = "Continue"
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $raiz
$log = Join-Path $raiz ".setup\relatorio-repositorio.txt"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
Start-Transcript -Path $log -Force | Out-Null
$repo = "$Dono/$Nome"

try {
    foreach ($ferramenta in @("git", "gh")) {
        if (-not (Get-Command $ferramenta -ErrorAction SilentlyContinue)) { throw "'$ferramenta' nao encontrado no PATH." }
    }
    gh auth status
    Write-Host ("Autor dos commits: " + (git config user.name) + " <" + (git config user.email) + ">")

    if (-not (Test-Path ".git")) { git init -b main | Out-Null }
    git add -A
    $pendente = git status --porcelain
    if ($pendente) { git commit -m "docs: desenho da solucao, ADRs e base do repositorio" | Out-Null }
    git log --oneline -n 3

    $existe = $true
    gh repo view $repo *> $null; if ($LASTEXITCODE -ne 0) { $existe = $false }
    if (-not $existe) {
        gh repo create $repo --public --description $Descricao --source . --remote origin --push
    } else {
        if (-not (git remote | Select-String -Quiet "^origin$")) { git remote add origin "https://github.com/$repo.git" }
        git push -u origin main
    }

    # So squash merge, apagar branch apos merge, auto-merge habilitado
    gh api -X PATCH "repos/$repo" -f allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false -F delete_branch_on_merge=true -F allow_auto_merge=true -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=PR_BODY | Out-Null

    # Protecao da main: PR obrigatorio, historico linear, sem force push, valendo para admins.
    # Os checks obrigatorios entram quando o CI existir (parametro -Checks).
    if ($Checks.Count -gt 0) {
        $listaChecks = ($Checks | ForEach-Object { '"' + $_ + '"' }) -join ", "
        $statusChecks = '{ "strict": true, "contexts": [' + $listaChecks + '] }'
    } else {
        $statusChecks = 'null'
    }
    $protecao = @"
{
  "required_status_checks": $statusChecks,
  "enforce_admins": true,
  "required_pull_request_reviews": { "required_approving_review_count": 0, "dismiss_stale_reviews": true },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
"@
    $tmp = New-TemporaryFile
    Set-Content -Path $tmp -Value $protecao -Encoding ascii
    gh api -X PUT "repos/$repo/branches/main/protection" --input $tmp | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "ERRO ao aplicar a protecao da main" }
    Remove-Item $tmp

    # Workflows de PRs vindos de forks so rodam com aprovacao (protege o runner self-hosted)
    gh api -X PUT "repos/$repo/actions/permissions/fork-pr-contributor-approval" -f approval_policy=all_external_contributors | Out-Null

    # Environment usado pelo CD
    gh api -X PUT "repos/$repo/environments/local" | Out-Null

    Write-Host "`n---- protecao aplicada"
    gh api "repos/$repo/branches/main/protection" --jq "{checks: .required_status_checks.contexts, admins: .enforce_admins.enabled, linear: .required_linear_history.enabled, pr: (.required_pull_request_reviews != null)}"
    Write-Host "`nRepositorio: https://github.com/$repo"
} catch {
    Write-Host "ERRO: $_"
} finally {
    Stop-Transcript | Out-Null
}
