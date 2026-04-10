Write-Host "Creating local Python virtual environments..."

python -m venv .venv
. .\.venv\Scripts\Activate.ps1

pip install --upgrade pip
pip install -r apps/api/requirements.txt
pip install -r apps/trading/requirements.txt
pip install -r apps/workers/requirements.txt

Write-Host "Bootstrap complete."
