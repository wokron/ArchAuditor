[CmdletBinding()]
param(
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

$metadata = @{
    adFailure = @{
        wired = $true
        problems = "2,5"
        code = "src/ad/.../AdService.java"
    }
    adHighCpu = @{
        wired = $true
        problems = "4,7"
        code = "src/ad/.../AdService.java"
    }
    adManualGc = @{
        wired = $true
        problems = "4,7"
        code = "src/ad/.../AdService.java"
    }
    addCircularDependency = @{
        wired = $true
        problems = "3"
        code = "src/recommendation/recommendation_server.py"
    }
    addErrorRate = @{
        wired = $true
        problems = "2"
        code = "src/recommendation/recommendation_server.py"
    }
    addLatency = @{
        wired = $true
        problems = "2,6"
        code = "src/product-catalog/main.go"
    }
    archCircular = @{
        wired = $false
        problems = ""
        code = ""
    }
    archCrash = @{
        wired = $false
        problems = ""
        code = ""
    }
    archLatency = @{
        wired = $false
        problems = ""
        code = ""
    }
    archNPlusOne = @{
        wired = $false
        problems = ""
        code = ""
    }
    archSpike = @{
        wired = $false
        problems = ""
        code = ""
    }
    cartFailure = @{
        wired = $true
        problems = "2,5"
        code = "src/cart/src/services/CartService.cs"
    }
    emailMemoryLeak = @{
        wired = $true
        problems = "4,7"
        code = "src/email/email_server.rb"
    }
    failedReadinessProbe = @{
        wired = $true
        problems = "5"
        code = "src/cart/src/services/HealthCheckService.cs"
    }
    highQPS = @{
        wired = $true
        problems = "2,4,6,7"
        code = "src/load-generator/locustfile.py; src/frontend/pages/api/data.ts"
    }
    imageSlowLoad = @{
        wired = $true
        problems = "6"
        code = "src/frontend/components/ProductCard/ProductCard.tsx"
    }
    kafkaQueueProblems = @{
        wired = $true
        problems = "2,6,7,9"
        code = "src/checkout/main.go; src/fraud-detection/.../main.kt"
    }
    llmInaccurateResponse = @{
        wired = $true
        problems = "business"
        code = "src/llm/app.py; src/product-reviews/product_reviews_server.py"
    }
    llmRateLimitError = @{
        wired = $true
        problems = "2"
        code = "src/product-reviews/product_reviews_server.py"
    }
    loadGeneratorFloodHomepage = @{
        wired = $true
        problems = "2,6,7"
        code = "src/load-generator/locustfile.py"
    }
    paymentFailure = @{
        wired = $true
        problems = "2,5"
        code = "src/payment/charge.js"
    }
    paymentUnreachable = @{
        wired = $true
        problems = "2,5"
        code = "src/checkout/main.go"
    }
    productCatalogFailure = @{
        wired = $true
        problems = "2"
        code = "src/product-catalog/main.go"
    }
    recommendationCacheFailure = @{
        wired = $true
        problems = "4,7"
        code = "src/recommendation/recommendation_server.py"
    }
}

$resolvedBaseUrl = Resolve-FeatureBaseUrl -PreferredBaseUrl $BaseUrl
$config = Invoke-RestMethod -Uri "$resolvedBaseUrl/api/read"

$config.flags.PSObject.Properties |
    Sort-Object Name |
    ForEach-Object {
        $name = $_.Name
        $flag = $_.Value
        $meta = $metadata[$name]
        [pscustomobject]@{
            name = $name
            defaultVariant = $flag.defaultVariant
            wired = if ($null -ne $meta) { [bool]$meta.wired } else { $false }
            architectureProblems = if ($null -ne $meta) { $meta.problems } else { "" }
            variants = ($flag.variants.PSObject.Properties.Name -join ", ")
            description = $flag.description
            codePath = if ($null -ne $meta) { $meta.code } else { "" }
        }
    }
