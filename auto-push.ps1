$ErrorActionPreference = "SilentlyContinue"
Set-Location "C:\Users\alunos 1.0\Desktop\caminhao-autonomo"

# Garante remoto correto
git remote set-url origin https://github.com/jeffpro013/caminhao-autonomo.git | Out-Null

# Faz commit e push somente se houver mudanças
git add .
if (-not (git diff --cached --quiet)) {
  $msg = "auto: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
  git commit -m $msg | Out-Null
}

# Se houver commits locais à frente, faz push
git push origin master | Out-Null

Write-Output "[auto-push] Finalizado em $(Get-Date)"


