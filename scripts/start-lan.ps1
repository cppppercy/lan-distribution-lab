[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,

    [string]$ListenAddress,

    [ValidateSet("read", "read_write")]
    [string]$Scope = "read_write",

    [switch]$NoPairing,

    [switch]$EnableDiscovery,

    [ValidateRange(1, 65535)]
    [int]$DiscoveryPort = 47800,

    [switch]$PrintOnly
)

# Explicitly invoked convenience wrapper for Windows LAN tests.  It never uses
# 0.0.0.0 and deliberately does not create firewall, NAT, or port-forward rules.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-PrivateIPv4 {
    param([Parameter(Mandatory)][string]$Address)

    $octets = $Address.Split(".")
    if ($octets.Count -ne 4) {
        return $false
    }

    try {
        $values = @($octets | ForEach-Object { [int]$_ })
    }
    catch {
        return $false
    }
    if ($values | Where-Object { $_ -lt 0 -or $_ -gt 255 }) {
        return $false
    }

    return ($values[0] -eq 10) -or
        ($values[0] -eq 172 -and $values[1] -ge 16 -and $values[1] -le 31) -or
        ($values[0] -eq 192 -and $values[1] -eq 168)
}

function Get-PrivateLanCandidates {
    $candidates = @()
    foreach ($adapter in [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
        if ($adapter.OperationalStatus -ne [System.Net.NetworkInformation.OperationalStatus]::Up) {
            continue
        }
        if ($adapter.NetworkInterfaceType -in @(
                [System.Net.NetworkInformation.NetworkInterfaceType]::Loopback,
                [System.Net.NetworkInformation.NetworkInterfaceType]::Tunnel
            )) {
            continue
        }

        $properties = $adapter.GetIPProperties()
        $hasGateway = @(
            $properties.GatewayAddresses |
                Where-Object { $_.Address.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork }
        ).Count -gt 0
        foreach ($unicast in $properties.UnicastAddresses) {
            $address = $unicast.Address.ToString()
            if (Test-PrivateIPv4 $address) {
                $candidates += [PSCustomObject]@{
                    Address = $address
                    Interface = $adapter.Name
                    HasGateway = $hasGateway
                }
            }
        }
    }
    return @($candidates | Sort-Object Address -Unique)
}

function Select-ListenAddress {
    param([string]$RequestedAddress)

    if ($RequestedAddress) {
        if (-not (Test-PrivateIPv4 $RequestedAddress)) {
            throw "-ListenAddress must be a private IPv4 address, never 0.0.0.0 or a public address."
        }
        return $RequestedAddress
    }

    $candidates = @(Get-PrivateLanCandidates)
    $mobileHotspot = @($candidates | Where-Object { $_.Address -eq "192.168.137.1" })
    if ($mobileHotspot.Count -eq 1) {
        return $mobileHotspot[0].Address
    }

    $withGateway = @($candidates | Where-Object { $_.HasGateway })
    if ($withGateway.Count -eq 1) {
        return $withGateway[0].Address
    }
    if ($candidates.Count -eq 1) {
        return $candidates[0].Address
    }

    $display = ($candidates | Format-Table -AutoSize | Out-String).Trim()
    throw "Could not choose one safe LAN address. Candidates:`n$display`nRun again with -ListenAddress <one address shown above>."
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment is missing: $python. Run the README installation steps first."
}

$selectedAddress = Select-ListenAddress $ListenAddress
$url = "http://${selectedAddress}:$Port/"
Write-Host "Selected LAN address: $selectedAddress"
Write-Host "Open on a device connected to the same Wi-Fi/hotspot: $url"
Write-Host "This script does not change Windows Firewall, NAT, or router settings."
if ($EnableDiscovery) {
    Write-Host "UDP discovery: enabled on port $DiscoveryPort. Inbound UDP may need a manual firewall rule."
    Write-Host "Find this service from another Windows CLI: python -m lan_distribution_lab discover --port $DiscoveryPort"
}
else {
    Write-Host "UDP discovery is disabled. Pass -EnableDiscovery to opt in."
}

if ($PrintOnly) {
    return
}

if (-not $env:LOCALAPPDATA) {
    throw "LOCALAPPDATA is not available; provide a normal Windows user environment."
}
$stateBase = Join-Path $env:LOCALAPPDATA "LANDistributionLab"
$dataDir = Join-Path $stateBase "state"
$managedRoot = Join-Path $stateBase "managed"
New-Item -ItemType Directory -Force -Path $dataDir, $managedRoot | Out-Null

$webRoot = Join-Path $projectRoot "web"
$webDist = Join-Path $webRoot "dist\index.html"
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm is required to build the Web interface but was not found on PATH."
}
if (-not (Test-Path -LiteralPath (Join-Path $webRoot "node_modules"))) {
    & npm --prefix $webRoot ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed." }
}
& npm --prefix $webRoot run build
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $webDist)) {
    throw "Web production build failed."
}

if (-not $NoPairing) {
    Write-Host "Creating a one-time $Scope pairing code (valid for ten minutes)..."
    & $python -m lan_distribution_lab pair create --scope $Scope --data-dir $dataDir --managed-root $managedRoot
    if ($LASTEXITCODE -ne 0) { throw "Pairing-code creation failed." }
}

Write-Host "Starting LAN service. Keep this window open; press Ctrl+C to stop it."
$serveArguments = @(
    "-m", "lan_distribution_lab", "serve",
    "--host", $selectedAddress,
    "--port", "$Port",
    "--data-dir", $dataDir,
    "--managed-root", $managedRoot
)
if ($EnableDiscovery) {
    $serveArguments += @("--discover", "--discover-port", "$DiscoveryPort")
}
& $python @serveArguments
