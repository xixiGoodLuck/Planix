param(
    [string]$Url = "http://127.0.0.1:8003/api/health",
    [int]$TimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
$Deadline = (Get-Date).AddSeconds($TimeoutSeconds)

while ((Get-Date) -lt $Deadline) {
    try {
        $Response = Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 2
        if ($Response.status -ne "ok" -or $Response.app -ne "planix-api") {
            throw "Health endpoint is reachable but not a Planix API response."
        }
        Write-Host "API health check passed at $Url"
        $Response | ConvertTo-Json -Depth 5
        exit 0
    }
    catch {
        Start-Sleep -Seconds 1
    }
}

throw "API health check did not become ready within $TimeoutSeconds seconds: $Url"
