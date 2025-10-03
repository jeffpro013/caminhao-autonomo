$ErrorActionPreference = "SilentlyContinue"
Set-Location "C:\Users\alunos 1.0\Desktop\caminhao-autonomo"

# Garante remoto correto
git remote set-url origin https://github.com/jeffpro013/caminhao-autonomo.git | Out-Null

# Descobre o branch atual (ex.: master/main)
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if (-not $branch) { $branch = "master" }

# Sincroniza com o remoto antes de comitar para evitar divergências
git fetch origin | Out-Null
git pull --rebase origin $branch | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Output "[auto-push] Conflitos detectados ao rebase. Resolva e execute novamente."
  exit 1
}

# Faz commit e push somente se houver mudanças
git add .
if (-not (git diff --cached --quiet)) {
  $msg = "auto: $branch $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
  git commit -m $msg | Out-Null
}

# Faz push para o branch atual
git push origin $branch | Out-Null

Write-Output "[auto-push] Finalizado em $(Get-Date)"


