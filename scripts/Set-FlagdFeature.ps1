[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Name,

    [Parameter(Mandatory = $true)]
    [string]$Variant,

    [string]$BaseUrl = ""
)

function Resolve-FeatureBaseUrl {
    param(
        [string]$PreferredBaseUrl
    )

    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($PreferredBaseUrl)) {
        $candidates += $PreferredBaseUrl.TrimEnd("/")
    }
    $candidates += @(
        "http://127.0.0.1:8080/feature",
        "http://127.0.0.1:4000"
    )

    foreach ($candidate in $candidates | Select-Object -Unique) {
        try {
            Invoke-RestMethod -Uri "$candidate/api/read" -TimeoutSec 5 | Out-Null
            return $candidate
        } catch {
            continue
        }
    }

    throw "No reachable flagd-ui endpoint found. Tried: $($candidates -join ', ')"
}

$resolvedBaseUrl = Resolve-FeatureBaseUrl -PreferredBaseUrl $BaseUrl
$config = Invoke-RestMethod -Uri "$resolvedBaseUrl/api/read"
$flagProperty = $config.flags.PSObject.Properties[$Name]

if ($null -eq $flagProperty) {
    throw "Flag '$Name' does not exist."
}

$flag = $flagProperty.Value
$variants = @($flag.variants.PSObject.Properties.Name)

if ($Variant -notin $variants) {
    throw "Variant '$Variant' is invalid for '$Name'. Available variants: $($variants -join ', ')"
}

$flag.defaultVariant = $Variant

$body = @{
    data = $config
} | ConvertTo-Json -Depth 30

Invoke-RestMethod -Method Post -Uri "$resolvedBaseUrl/api/write" -ContentType "application/json" -Body $body | Out-Null

[pscustomobject]@{
    name = $Name
    defaultVariant = $Variant
    availableVariants = ($variants -join ", ")
    description = $flag.description
}
