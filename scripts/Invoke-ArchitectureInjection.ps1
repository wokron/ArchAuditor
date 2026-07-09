
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    <#
    Scenario 表示要注入哪一种问题。
    #>
    [Parameter(Mandatory = $true)]
    [ValidateSet(
        "ConfigDriftAnnotate",
        "CoDeployRestart",
        "PinToNode",
        "UnpinFromNode",
        "ScaleDeployment",
        "BreakK8sProbes",
        "BreakK8sRequests",
        "BreakK8sLimits",
        "InjectHostPath",
        "SinglePointAvailabilityRisk",
        "MarkRollback"
    )]
    [string]$Scenario,

    [string]$Namespace = "default",

    <#
    要操作的 Deployment 列表。
    支持 -Deployments frontend,checkout,payment
    也支持 -Deployments "frontend,checkout,payment"
    #>
    [string[]]$Deployments = @("frontend", "checkout", "payment"),

    <# PinToNode 使用：指定要固定到哪个 Kubernetes 节点。 #>
    [string]$NodeName,

    <# ScaleDeployment 使用：指定副本数。 #>
    [int]$Replicas = 1,

    <# 多容器 Pod 使用：指定要修改第几个 container，默认第 0 个。 #>
    [int]$ContainerIndex = 0
)

<#
把 -Deployments "frontend,checkout,payment" 拆成数组。
这样用户既可以传数组，也可以传逗号分隔字符串。
#>
$normalizedDeployments = @()
foreach ($item in $Deployments) {
    if ($null -eq $item) {
        continue
    }

    foreach ($part in ($item -split ",")) {
        $name = ($part | ForEach-Object { $_.Trim() })
        if (-not [string]::IsNullOrWhiteSpace($name)) {
            $normalizedDeployments += $name
        }
    }
}

if ($normalizedDeployments.Count -gt 0) {
    $Deployments = $normalizedDeployments
}

function Invoke-Kubectl {
    <#
    kubectl 包装函数。
    作用：
    1. 统一执行 kubectl。
    2. 如果 kubectl 失败，立刻抛错停止脚本。
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & kubectl @Arguments

    if ($LASTEXITCODE -ne 0) {
        throw "kubectl failed: kubectl $($Arguments -join ' ')"
    }
}

function Get-DeploymentObject {
    <#
    读取某个 Deployment 的完整 JSON，并转成 PowerShell 对象。
    后面的 probe/resource/hostPath 注入都需要先读取原始 Deployment。
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$DeploymentName
    )

    return kubectl get deployment $DeploymentName -n $Namespace -o json | ConvertFrom-Json
}

function Apply-DeploymentPatchFile {
    <#
    把 patch 内容写成临时 JSON 文件，再调用 kubectl patch。
    这样可以避免在命令行里手写 JSON 转义，稳定很多。
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$DeploymentName,

        [Parameter(Mandatory = $true)]
        [hashtable]$PatchBody
    )

    $patchPath = Join-Path $env:TEMP "archauditor-$DeploymentName-patch.json"
    ($PatchBody | ConvertTo-Json -Depth 50) | Set-Content -Path $patchPath -Encoding UTF8

    Invoke-Kubectl @(
        "patch",
        "deployment",
        $DeploymentName,
        "-n",
        $Namespace,
        "--type",
        "merge",
        "--patch-file",
        $patchPath
    )
}

switch ($Scenario) {
    "ConfigDriftAnnotate" {
        <#
        架构问题 8：配置漂移。
        注入方式：给 Deployment 加人工变更 annotation。
        审计接口：GET /api/config-drift。
        重点字段：is_manual_change、is_drifted、latest_changed_by、age_hours。
        #>
        $timestamp = (Get-Date).ToUniversalTime().ToString("o")

        foreach ($deployment in $Deployments) {
            if ($PSCmdlet.ShouldProcess("$Namespace/$deployment", "annotate deployment for config drift demo")) {
                Invoke-Kubectl @(
                    "annotate",
                    "deployment",
                    $deployment,
                    "-n",
                    $Namespace,
                    "archauditor.io/manual-change=$timestamp",
                    "kubernetes.io/change-cause=manual-drift-demo-$timestamp",
                    "--overwrite"
                )
            }
        }
    }

    "CoDeployRestart" {
        <#
        架构问题 9：丧失可维护性，协同部署。
        注入方式：同时 rollout restart 多个 Deployment。
        审计接口：GET /api/maintainability。
        重点字段：co_deployment_issues。
        #>
        foreach ($deployment in $Deployments) {
            if ($PSCmdlet.ShouldProcess("$Namespace/$deployment", "rollout restart deployment")) {
                Invoke-Kubectl @("rollout", "restart", "deployment/$deployment", "-n", $Namespace)
            }
        }
    }

    "PinToNode" {
        <#
        架构问题 10：缺少物理隔离。
        注入方式：给 Deployment 加 nodeSelector，把 Pod 固定到指定节点。
        审计接口：GET /api/isolation。
        重点字段：same_node_replica_services、single_zone_services、placements。
        #>
        if ([string]::IsNullOrWhiteSpace($NodeName)) {
            throw "PinToNode requires -NodeName."
        }

        foreach ($deployment in $Deployments) {
            if ($PSCmdlet.ShouldProcess("$Namespace/$deployment", "pin deployment to node $NodeName")) {
                Apply-DeploymentPatchFile -DeploymentName $deployment -PatchBody @{
                    spec = @{
                        template = @{
                            spec = @{
                                nodeSelector = @{
                                    "kubernetes.io/hostname" = $NodeName
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    "UnpinFromNode" {
        <#
        恢复问题 10 的注入。
        注入方式：删除 nodeSelector，让 Kubernetes 重新自由调度。
        审计接口：GET /api/isolation。
        #>
        $patch = '{"spec":{"template":{"spec":{"nodeSelector":null}}}}'

        foreach ($deployment in $Deployments) {
            if ($PSCmdlet.ShouldProcess("$Namespace/$deployment", "remove nodeSelector pin")) {
                Invoke-Kubectl @("patch", "deployment", $deployment, "-n", $Namespace, "--type", "merge", "-p", $patch)
            }
        }
    }

    "ScaleDeployment" {
        <#
        辅助问题 5 和问题 10。
        注入方式：修改 Deployment 副本数。
        审计接口：
        - GET /api/single-point
        - GET /api/isolation
        #>
        foreach ($deployment in $Deployments) {
            if ($PSCmdlet.ShouldProcess("$Namespace/$deployment", "scale deployment to $Replicas")) {
                Invoke-Kubectl @("scale", "deployment/$deployment", "-n", $Namespace, "--replicas=$Replicas")
            }
        }
    }

    "BreakK8sProbes" {
        <#
        架构问题 1：规范性问题，缺失探针。
        注入方式：删除 livenessProbe 和 readinessProbe。
        审计接口：GET /api/k8s-config-issues。
        重点类型：MISSING_LIVENESS_PROBE、MISSING_READINESS_PROBE。
        #>
        foreach ($deployment in $Deployments) {
            if ($PSCmdlet.ShouldProcess("$Namespace/$deployment", "remove liveness/readiness probes")) {
                $dep = Get-DeploymentObject -DeploymentName $deployment
                $containers = @($dep.spec.template.spec.containers)

                if ($ContainerIndex -ge $containers.Count) {
                    throw "Deployment '$deployment' does not have container index $ContainerIndex."
                }

                $target = $containers[$ContainerIndex]
                $target.PSObject.Properties.Remove("livenessProbe")
                $target.PSObject.Properties.Remove("readinessProbe")

                Apply-DeploymentPatchFile -DeploymentName $deployment -PatchBody @{
                    spec = @{
                        template = @{
                            spec = @{
                                containers = $containers
                            }
                        }
                    }
                }
            }
        }
    }

    "BreakK8sRequests" {
        <#
        架构问题 1：规范性问题，缺少 resource requests。
        也能辅助问题 5 和问题 7。
        注入方式：删除 container.resources.requests。
        审计接口：
        - GET /api/k8s-config-issues
        - GET /api/resource-utilization
        - GET /api/single-point
        #>
        foreach ($deployment in $Deployments) {
            if ($PSCmdlet.ShouldProcess("$Namespace/$deployment", "remove resource requests")) {
                $dep = Get-DeploymentObject -DeploymentName $deployment
                $containers = @($dep.spec.template.spec.containers)

                if ($ContainerIndex -ge $containers.Count) {
                    throw "Deployment '$deployment' does not have container index $ContainerIndex."
                }

                $target = $containers[$ContainerIndex]
                if ($null -eq $target.resources) {
                    $target | Add-Member -NotePropertyName resources -NotePropertyValue ([pscustomobject]@{}) -Force
                }
                $target.resources.PSObject.Properties.Remove("requests")

                Apply-DeploymentPatchFile -DeploymentName $deployment -PatchBody @{
                    spec = @{
                        template = @{
                            spec = @{
                                containers = $containers
                            }
                        }
                    }
                }
            }
        }
    }

    "BreakK8sLimits" {
        <#
        架构问题 1：规范性问题，缺少 resource limits。
        也能辅助问题 7。
        注入方式：删除 container.resources.limits。
        审计接口：GET /api/k8s-config-issues。
        重点类型：MISSING_RESOURCE_LIMITS。
        #>
        foreach ($deployment in $Deployments) {
            if ($PSCmdlet.ShouldProcess("$Namespace/$deployment", "remove resource limits")) {
                $dep = Get-DeploymentObject -DeploymentName $deployment
                $containers = @($dep.spec.template.spec.containers)

                if ($ContainerIndex -ge $containers.Count) {
                    throw "Deployment '$deployment' does not have container index $ContainerIndex."
                }

                $target = $containers[$ContainerIndex]
                if ($null -eq $target.resources) {
                    $target | Add-Member -NotePropertyName resources -NotePropertyValue ([pscustomobject]@{}) -Force
                }
                $target.resources.PSObject.Properties.Remove("limits")

                Apply-DeploymentPatchFile -DeploymentName $deployment -PatchBody @{
                    spec = @{
                        template = @{
                            spec = @{
                                containers = $containers
                            }
                        }
                    }
                }
            }
        }
    }

    "InjectHostPath" {
        <#
        架构问题 1：规范性问题，hostPath 挂载。
        注入方式：给 Pod 模板增加 hostPath volume，并挂到容器里。
        审计接口：GET /api/k8s-config-issues。
        重点类型：HOST_PATH_MOUNT。
        #>
        foreach ($deployment in $Deployments) {
            if ($PSCmdlet.ShouldProcess("$Namespace/$deployment", "inject hostPath mount")) {
                $dep = Get-DeploymentObject -DeploymentName $deployment
                $containers = @($dep.spec.template.spec.containers)

                if ($ContainerIndex -ge $containers.Count) {
                    throw "Deployment '$deployment' does not have container index $ContainerIndex."
                }

                $target = $containers[$ContainerIndex]
                $volumeName = "archauditor-hostpath-demo"
                $mountPath = "/tmp/archauditor-hostpath"

                $volumeMounts = @()
                if ($null -ne $target.volumeMounts) {
                    $volumeMounts = @($target.volumeMounts)
                }
                if (-not ($volumeMounts | Where-Object { $_.name -eq $volumeName })) {
                    $volumeMounts += [pscustomobject]@{
                        name = $volumeName
                        mountPath = $mountPath
                    }
                }
                if ($null -eq $target.volumeMounts) {
                    $target | Add-Member -NotePropertyName volumeMounts -NotePropertyValue $volumeMounts -Force
                } else {
                    $target.volumeMounts = $volumeMounts
                }

                $volumes = @()
                if ($null -ne $dep.spec.template.spec.volumes) {
                    $volumes = @($dep.spec.template.spec.volumes)
                }
                if (-not ($volumes | Where-Object { $_.name -eq $volumeName })) {
                    $volumes += [pscustomobject]@{
                        name = $volumeName
                        hostPath = [pscustomobject]@{
                            path = "/var/lib/archauditor-hostpath-demo"
                            type = "DirectoryOrCreate"
                        }
                    }
                }

                Apply-DeploymentPatchFile -DeploymentName $deployment -PatchBody @{
                    spec = @{
                        template = @{
                            spec = @{
                                containers = $containers
                                volumes = $volumes
                            }
                        }
                    }
                }
            }
        }
    }

    "SinglePointAvailabilityRisk" {
        <#
        架构问题 5：单点风险。
        注入方式：把服务缩成 1 副本，并删除 requests。
        审计接口：GET /api/single-point。
        重点字段：availability_risk_nodes。
        #>
        foreach ($deployment in $Deployments) {
            if ($PSCmdlet.ShouldProcess("$Namespace/$deployment", "scale to 1 and remove requests")) {
                Invoke-Kubectl @("scale", "deployment/$deployment", "-n", $Namespace, "--replicas=1")

                $dep = Get-DeploymentObject -DeploymentName $deployment
                $containers = @($dep.spec.template.spec.containers)

                if ($ContainerIndex -ge $containers.Count) {
                    throw "Deployment '$deployment' does not have container index $ContainerIndex."
                }

                $target = $containers[$ContainerIndex]
                if ($null -eq $target.resources) {
                    $target | Add-Member -NotePropertyName resources -NotePropertyValue ([pscustomobject]@{}) -Force
                }
                $target.resources.PSObject.Properties.Remove("requests")

                Apply-DeploymentPatchFile -DeploymentName $deployment -PatchBody @{
                    spec = @{
                        template = @{
                            spec = @{
                                containers = $containers
                            }
                        }
                    }
                }
            }
        }
    }

    "MarkRollback" {
        <#
        架构问题 9：丧失可维护性，回滚风险。
        注入方式：给 Deployment 打 rollback-demo 的 change-cause。
        审计接口：GET /api/maintainability。
        重点字段：rollback_issues。
        #>
        $timestamp = (Get-Date).ToUniversalTime().ToString("o")

        foreach ($deployment in $Deployments) {
            if ($PSCmdlet.ShouldProcess("$Namespace/$deployment", "mark rollback-like change")) {
                Invoke-Kubectl @(
                    "annotate",
                    "deployment",
                    $deployment,
                    "-n",
                    $Namespace,
                    "kubernetes.io/change-cause=rollback-demo-$timestamp",
                    "archauditor.io/manual-change=$timestamp",
                    "--overwrite"
                )
            }
        }
    }
}
