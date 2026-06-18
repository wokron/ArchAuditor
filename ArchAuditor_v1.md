# ArchAuditor - 架构问题识别说明

## 1. 文档目的

本文档用于说明 `ArchAuditor` 当前实现的七类架构问题的实现方式、验证入口、验证命令，以及预期验证结果。

覆盖的问题包括：

1. K8s 规范性问题
2. 违反依赖关系
3. 循环依赖
4. 单体服务
5. 单点
6. 过度拆分
7. 不合理资源利用

## 2. 运行启动

### 2.1 后端启动

```powershell
pip install -r requirements.txt
cd arch-auditor/
python -m arch_auditor.service --config ../k8s-audit.yaml
```

### 2.2 依赖服务

需要保证以下能力可访问：

- Kubernetes 集群中已部署业务服务
- Jaeger 可访问
- Prometheus 可访问

在功能测试阶段，`ArchAuditor`依赖项目`opentelemetry-demo`进行演示: https://github.com/open-telemetry/opentelemetry-demo

本地常用端口映射：

- `http://localhost:8000`：ArchAuditor 后端
- `http://localhost:16686`：Jaeger
- `http://localhost:9090`：Prometheus
- `http://localhost:8080`:  `opentelemetry-demo` 后端
- `http://localhost:8080/feature`: 服务异常注入开关

### 2.3 触发审计命令

触发一次审计：

```powershell
$base = "http://localhost:8000"
Invoke-RestMethod -Method Post "$base/api/audit" | Out-Null
```

或在 `localhost:8000/` 页面中点击`触发审计`按钮，控制面板展示全部审计日志：

![image-20260613111859255](C:\Users\张腾月\AppData\Roaming\Typora\typora-user-images\image-20260613111859255.png)

在 `/ControlCenterVisualization` 页面中同样可查看日志与问题情况。

![image-20260613112534682](C:\Users\张腾月\AppData\Roaming\Typora\typora-user-images\image-20260613112534682.png)

> 在开始通过接口验证架构问题前，可执行一次：
>
> ```powershell
> $base = "http://localhost:8000"
> 
> function Get-Api {
>     param([string]$Path)
>     return Invoke-RestMethod -Uri "$base$Path" -Method Get
> }
> ```
>
> 定义 `$base` 变量与 `Get-Api` 函数。下文命令均基于此。

## 3. 问题 1-K8s 规范性问题

### 3.1 实现方法

这一类问题基于 Kubernetes 资源配置进行静态检查。

当前已覆盖的检查项包括：

- 安全性配置未开启
- 缺失探针
- 缺失资源 `request / limit`
- 缺失 `ResourceQuota`
- 挂载 本地绝对路径

实现上分成两层：

- `K8sConfigSource`：从 K8s 中采集 deployment / pod / quota / volume / probe / security context
- `K8sConfigAnalyzer`：基于采集结果输出告警

### 3.2 验证入口

- 前端页面可视化：
  - `/ResourceAuditVisualization`
  
  ![image-20260613112221067](C:\Users\张腾月\AppData\Roaming\Typora\typora-user-images\image-20260613112221067.png)
  
  ​	可单独导出此类问题审计日志。
- 日志接口：
  - `/api/reports`

### 3.3 验证命令

```powershell
Invoke-RestMethod -Method Post "$base/api/audit" | Out-Null
(Get-Api "/api/reports") | Where-Object { $_ -like "*K8sConfigAnalyzer*" }
```

### 3.4 预期结果

可以看到类似日志：

```text
[WARNING] from K8sConfigAnalyzer: deployment/otel-demo/grafana: [MISSING_READINESS_PROBE] ...
[WARNING] from K8sConfigAnalyzer: deployment/otel-demo/payment: [MISSING_RESOURCE_REQUESTS] ...
[WARNING] from K8sConfigAnalyzer: namespace/otel-demo/otel-demo: [MISSING_RESOURCE_QUOTA] ...
```

说明系统已经能够识别：

- 哪个工作负载缺探针
- 哪个工作负载缺 `request / limit`
- 哪个命名空间缺 `ResourceQuota`
- ...

## 4. 问题 2-违反依赖关系

### 4.1 实现方法

该问题的实现流程如下：

1. 从 Jaeger 获取服务调用关系
2. 从 Prometheus 获取服务时序指标
3. 计算上下游服务之间的依赖相关性
4. 结合优先级配置判断是否违反约束

当前实现中：

- 依赖边来自真实 tracing 数据
- 相关性来自真实 Prometheus 时序数据
- 优先级来源当前为`用户手工配置`

### 4.2 验证入口

- 接口：
  - `/api/dependency-edges`
  - `/api/priorities`
  - `/api/reports`

### 4.3 验证命令

先查看依赖边：

```powershell
(Get-Api "/api/dependency-edges").edges |
  Where-Object { $_.dependency_correlation_status -eq "ok" } |
  Sort-Object call_count -Descending |
  Select-Object -First 10 source,target,dependency_type,call_count,dependency_correlation |
  ConvertTo-Json -Depth 6
```

如果需要演示违规告警，可以手工设置一组反向优先级：

```powershell
$strong = (Get-Api "/api/dependency-edges").edges |
  Where-Object { $_.dependency_type -eq "strong" -and $_.dependency_correlation_status -eq "ok" } |
  Sort-Object call_count -Descending |
  Select-Object -First 1

Invoke-RestMethod -Method Post "$base/api/priorities/$($strong.source)/0" | Out-Null
Invoke-RestMethod -Method Post "$base/api/priorities/$($strong.target)/3" | Out-Null
Invoke-RestMethod -Method Post "$base/api/audit" | Out-Null

(Get-Api "/api/reports") | Where-Object { $_ -like "*PriorityCheckAnalyzer*" }
```

### 4.4 预期结果

依赖边接口可看到返回如下形式：

```text
{
    source = load-generator
    target = frontend-proxy
    dependency_type = strong
    call_count = 1221
    dependency_correlation = 0.9712
    dependency_correlation_status = ok
}
...
```

这说明：

- 系统已经基于真实 tracing 识别出了服务调用边
- 已基于真实时序数据算出了依赖强弱

若配置了反向优先级，则日志中会出现：

```text
 [WARNING] from PriorityCheckAnalyzer: Priority violation: 'load-generator' (priority: 0) depends strongly on 'frontend-proxy' (priority: 3)
 ...
```

## 5. 问题 3-循环依赖

### 5.1 实现方法

基于服务调用图构建有向图，并使用图算法检测回环。

### 5.2 验证入口

- 前端页面：
  - `/vis/CircularDependencyAnalyzer`
  
    ![image-20260613113117482](C:\Users\张腾月\AppData\Roaming\Typora\typora-user-images\image-20260613113117482.png)
  
    展示服务依赖关系图，若存在循环依赖则高亮标注。
  
- 接口：
  - `/api/service-graph?view=circular`

### 5.3 验证命令

```powershell
Get-Api "/api/service-graph?view=circular" | ConvertTo-Json -Depth 6
```

### 5.4 预期结果

如果当前没有环，会返回：

```text
message = "No circular dependencies detected."
```

这说明循环依赖检测链路已经跑通，只是当前样例没有命中。

如果未来存在环，则会返回回环子图，并在日志中出现：

```text
[ERROR] from CircularDependencyAnalyzer: Circular dependency detected: ...
```

## 6. 问题 4：单体服务

### 6.1 实现方法

当前单体服务分析包含两部分：

1. 架构视角：
   - 使用入度 + 出度判断服务是否过于集中
2. 资源视角：
   - 基于 Prometheus 的真实 CPU / 内存利用率
   - 统计服务平均资源使用量
   - 与全局中位数比较，超过倍数阈值则视为候选单体

### 6.2 验证入口

- 接口：
  - `/api/monolithic-services`
  - `/api/reports`

### 6.3 验证命令

```powershell
(Get-Api "/api/monolithic-services").items |
  Where-Object { $_.has_architecture_issue -or $_.has_resource_monolith_issue } |
  Select-Object service,degree,avg_cpu_usage,avg_memory_usage_bytes,has_architecture_issue,has_resource_cpu_monolith_issue,has_resource_memory_monolith_issue |
  ConvertTo-Json -Depth 6

(Get-Api "/api/reports") | Where-Object { $_ -like "*MonolithicServiceAnalyzer*" }
```

### 6.4 预期结果

可以看到类似结果：

- `checkout` 被识别为 CPU 资源视角的单体候选
- `load-generator` 被识别为 CPU 资源视角的单体候选
- `kafka` 被识别为内存资源视角的单体候选

日志示例：

```text
[WARNING] from MonolithicServiceAnalyzer: Resource perspective: Service 'kafka' has average actual memory usage 519437219 bytes, which is > 5.0x the median service memory usage (84730601 bytes) over the observed time window.
...
```

说明系统已经能够基于真实 CPU / 内存利用率识别“过重服务”。

## 7. 问题 5：单点

### 7.1 实现方法

基于服务调用图识别关键单点服务，并利用支配树结合图上的关键性分值判断：

- 哪些服务一旦故障会显著影响整体核心路径

### 7.2 验证入口

- 前端页面：`/vis/SinglePointAnalyzer`
  
  ![image-20260613114942684](C:\Users\张腾月\AppData\Roaming\Typora\typora-user-images\image-20260613114942684.png)
  
  可以看到采用不同配色标注服务关键性，高亮关键故障节点。
- 接口：
  - `/api/reports`

### 7.3 验证命令

```powershell
(Get-Api "/api/reports") | Where-Object { $_ -like "*SinglePointAnalyzer*" }
```

### 7.4 预期结果

可以看到类似：

```text
[WARNING] from SinglePointAnalyzer: Service 'load-generator' is a critical single point of failure with criticality score 1701 (95.19% of root's criticality)
[WARNING] from SinglePointAnalyzer: Service 'frontend-proxy' is a critical single point of failure with criticality score 1701 (95.19% of root's criticality)
...
```

说明系统能够识别核心路径上的关键单点服务。

## 8. 问题 6：过度拆分

### 8.1 实现方法

当前实现从三个角度判断过度拆分：

1. 最长调用链是否过长
2. pipe service 比例是否过高
3. 是否存在大量总是一起部署的服务对

其中：

- 最长调用链来自服务图
- 累计平均时延来自 Prometheus `latency`
- 共同部署关系来自 `DeploymentHistorySource`

### 8.2 验证入口

- 接口：
  - `/api/over-decomposition`
  - `/api/reports`

### 8.3 验证命令

```powershell
(Get-Api "/api/over-decomposition").summary | ConvertTo-Json -Depth 8
(Get-Api "/api/reports") | Where-Object { $_ -like "*OverDecompositionAnalyzer*" }
```

### 8.4 预期结果

重点关注这些字段：

- `longest_path_services`
- `longest_path_service_count`
- `has_long_chain_issue`
- `pipe_service_ratio`
- `has_pipe_service_ratio_issue`
- `co_deployed_pairs`

可以看到如下形式结果：

```powershell
 "longest_path_services":  [
                                  "load-generator",
                                  "frontend-proxy",
                                  "frontend",
                                  "checkout",
                                  "payment",
                                  "flagd"
                              ],//长调用链路，即>5个服务
    "longest_path_service_count":  6,
    "path_service_threshold":  5,
    "longest_path_total_avg_latency":  37972.27451910925,
    "has_long_chain_issue":  true,
    "pipe_services":  [
                          {
                              "service":  "recommendation",
                              "upstream":  "frontend",
                              "downstream":  "flagd"
                          },
                          {
                              "service":  "shipping",
                              "upstream":  "checkout",
                              "downstream":  "quote"
                          },
                          {
                              "service":  "payment",
                              "upstream":  "checkout",
                              "downstream":  "flagd"
                          },
                         ...
                      ],//只有一个上游和一个下游的管道服务
    "pipe_service_ratio":  0.2777777777777778,
    "pipe_service_ratio_threshold":  0.3,
    "has_pipe_service_ratio_issue":  false,
    "co_deployed_pairs":  [
                              {
                                  "service_a":  "checkout",
                                  "service_b":  "frontend",
                                  "overlap_ratio":  1.0
                              },
                              {
                                  "service_a":  "cart",
                                  "service_b":  "frontend",
                                  "overlap_ratio":  1.0
                              },
                              {
                                  "service_a":  "currency",
                                  "service_b":  "frontend",
                                  "overlap_ratio":  1.0
                              },
                             ...
                          ],//与依赖的服务一起部署的紧密耦合服务
    "co_deploy_overlap_threshold":  0.8
}
```

这说明：

- 当前系统中存在超阈值的长调用链
- 中转型服务比例偏高，存在一起部署相互依赖的服务
- 拆分过细的问题已经能够被识别

## 9. 问题 7：不合理资源利用

### 9.1 实现方法

当前实现把两类数据联合起来分析：

1. K8s 配置数据
   - request / limit
2. Prometheus 真实利用率数据
   - CPU 平均利用率
   - CPU 短期平均利用率
   - 内存平均利用率
   - 内存短期平均利用率

在此基础上判断三类问题：

- 高优先级服务缺 request 设置
- 资源浪费，实际利用率 < 0.3 * request
- 利用率逼近极限，短期实际利用率 > 0.7 * limit

### 9.2 验证入口

- 接口：
  - `/api/resource-utilization`
  - `/api/metrics-timeseries`
  - `/api/reports`

### 9.3 验证命令

查看资源利用审计结果：

```powershell
(Get-Api "/api/resource-utilization").items |
  Where-Object { $_.has_missing_request_issue -or $_.has_waste_issue -or $_.has_limit_risk_issue } |
  Select-Object service,container,request_cpu,limit_cpu,request_memory_bytes,limit_memory_bytes,avg_cpu_usage,avg_memory_usage_bytes,short_term_avg_cpu_usage,short_term_avg_memory_usage_bytes,has_missing_request_issue,has_cpu_waste_issue,has_memory_waste_issue,has_cpu_limit_risk_issue,has_memory_limit_risk_issue |
  ConvertTo-Json -Depth 6
```

查看 memory 时序数据已经接入：

```powershell
(Get-Api "/api/metrics-timeseries").metrics.memory_usage |
  Select-Object -First 5 |
  ConvertTo-Json -Depth 6
```

查看日志：

```powershell
(Get-Api "/api/reports") | Where-Object { $_ -like "*ResourceUtilizationAnalyzer*" }
```

### 9.4 预期结果

可以验证出三类典型问题情况：

```powershell
 {
        "service":  "ad",
        "container":  "ad",
        "request_cpu":  0.0,
        "limit_cpu":  0.0,
        "request_memory_bytes":  0.0,
        "limit_memory_bytes":  314572800.0,
        "avg_cpu_usage":  0.02680863135961522,
        "avg_memory_usage_bytes":  228539497.93103448,
        "short_term_avg_cpu_usage":  0.0059492684061600715,
        "short_term_avg_memory_usage_bytes":  225508556.8,
        "has_missing_request_issue":  true, //高优先级缺少request设置
        "has_cpu_waste_issue":  false,
        "has_memory_waste_issue":  false,
        "has_cpu_limit_risk_issue":  false,
        "has_memory_limit_risk_issue":  true //内存资源利用率接近极限
    },
    {
        "service":  "cart",
        "container":  "cart",
        "request_cpu":  0.08,
        "limit_cpu":  0.2,
        "request_memory_bytes":  83886080.0,
        "limit_memory_bytes":  167772160.0,
        "avg_cpu_usage":  0.015060808376018144,
        "avg_memory_usage_bytes":  108952681.93103448,
        "short_term_avg_cpu_usage":  0.010513035571157714,
        "short_term_avg_memory_usage_bytes":  106804019.2,
        "has_missing_request_issue":  false,
        "has_cpu_waste_issue":  true, //cpu资源浪费
        "has_memory_waste_issue":  false,
        "has_cpu_limit_risk_issue":  false,
        "has_memory_limit_risk_issue":  false
    },
    {
        "service":  "checkout",
        "container":  "checkout",
        "request_cpu":  0.08,
        "limit_cpu":  0.2,
        "request_memory_bytes":  20971520.0,
        "limit_memory_bytes":  20971520.0,
        "avg_cpu_usage":  0.13160552374735457,
        "avg_memory_usage_bytes":  14762548.965517242,
        "short_term_avg_cpu_usage":  0.19404617242743466,
        "short_term_avg_memory_usage_bytes":  14931558.4,
        "has_missing_request_issue":  false,
        "has_cpu_waste_issue":  false,
        "has_memory_waste_issue":  false,
        "has_cpu_limit_risk_issue":  true, //cpu资源接近极限
        "has_memory_limit_risk_issue":  true //内存资源接近极限
    },
    ...
[2026-06-13 11:57:53] [WARNING] from ResourceUtilizationAnalyzer: Service 'cart' (container 'cart') is wasting CPU. Actual average utilization (0.015) < 30.0% of request (0.08).
[2026-06-13 11:57:53] [WARNING] from ResourceUtilizationAnalyzer: Service 'checkout' (container 'checkout') CPU stability risk. Short-term average utilization (0.194) > 70.0% of limit (0.2).
[2026-06-13 11:57:53] [WARNING] from ResourceUtilizationAnalyzer: Service 'checkout' (container 'checkout') memory stability risk. Short-term average utilization (14931558 bytes) > 70.0% of limit (20971520 bytes).
[2026-06-13 11:57:53] [ERROR] from ResourceUtilizationAnalyzer: Service 'currency' is a high-priority service (priority 0) but has no CPU request configured. This is unreasonable.
	...
```

## 10. 前端与接口的对应关系

### 10.1 已有明显前端展示的问题

- K8s 规范性问题
  - `/ResourceAuditVisualization`
- 循环依赖
  - `/ServiceAnalysisVisualization?issueType=circular`
- 单点
  - `/ServiceAnalysisVisualization?issueType=hierarchy`
  - `/ControlCenterVisualization`

### 10.2 主要依赖接口验证的问题

- 违反依赖关系
  - `/api/dependency-edges`
  - `/api/priorities`
- 单体服务
  - `/api/monolithic-services`
- 过度拆分
  - `/api/over-decomposition`
- 不合理资源利用
  - `/api/resource-utilization`

> 截至目前，前七类问题均已具备可运行的审计链路。
>
> - 问题 1、3、5 已具备较直观的前端展示
> - 问题 2、4、6、7 已具备结构化接口和审计日志验证方式



---6.18 工作---

## 配置漂移

#### 接口：

`/api/config-drift`

```
Get-Api "/api/config-drift" | ConvertTo-Json -Depth 8
```

#### 返回结构：

```
                                                {
                      "resource":  "configmap/default/flagd-config",////是哪个资源
                      "namespace":  "default",
                      "kind":  "Configmap",
                      "name":  "flagd-config",
                      "revision":  "99342",//版本修订号
                      "latest_changed_at":  "2026-06-17T18:14:00.245811+00:00",//最近变更时间
                      "latest_changed_by":  "user",//谁改的
                      "manager":  "minikube-user",//写这个对象的管理者是谁
                      "reason":  "manual-looking audit actor \u0027minikube-user\u0027",
                      "change_cause":  "",
                      "event_count":  1,
                      "age_hours":  8.6,//这次变更持续了多少小时
                      "drift_threshold_hours":  24,
                      "is_manual_change":  true,
                      "is_drifted":  false,
                      "verb":  "patch",
                      "username":  "minikube-user",
                      "user_agent":  "kubectl.exe/v1.32.2 (windows/amd64) kubernetes/67a30c0",
                      "source_ip":  "192.168.49.1",
                      "event_source":  "k8s_audit_log",
                      "selected_event_strategy":  "latest_manual_change",
                      "latest_observed_at":  "2026-06-17T18:14:00.245811+00:00",
                      "latest_observed_by":  "user",
                      "latest_observed_manager":  "minikube-user",
                      "latest_observed_reason":  "manual-looking audit actor \u0027minikube-user\u0027",
                      "latest_observed_verb":  "patch"
                  },
                  {
                      "resource":  "deployment/default/accounting",
                      "namespace":  "default",
                      "kind":  "Deployment.apps",
                      "name":  "accounting",
                      "revision":  "",
                      "latest_changed_at":  "2026-06-17T13:23:05.039849+00:00",
                      "latest_changed_by":  "user",
                      "manager":  "minikube-user",
                      "reason":  "manual-looking audit actor \u0027minikube-user\u0027",
                      "change_cause":  "",
                      "event_count":  1,
                      "age_hours":  13.45,
                      "drift_threshold_hours":  24,
                      "is_manual_change":  true,
                      "is_drifted":  false,
                      "verb":  "patch",
                      "username":  "minikube-user",
                      "user_agent":  "kubectl.exe/v1.32.2 (windows/amd64) kubernetes/67a30c0",
                      "source_ip":  "192.168.49.1",
                      "event_source":  "k8s_audit_log",
                      "selected_event_strategy":  "latest_manual_change",
                      "latest_observed_at":  "2026-06-17T13:23:05.039849+00:00",
                      "latest_observed_by":  "user",
                      "latest_observed_manager":  "minikube-user",
                      "latest_observed_reason":  "manual-looking audit actor \u0027minikube-user\u0027",
                      "latest_observed_verb":  "patch"
                  },
                  {
                      "resource":  "deployment/default/cart",
                      "namespace":  "default",
                      "kind":  "Deployment.apps",
                      "name":  "cart",
                      "revision":  "",
                      "latest_changed_at":  "2026-06-17T13:23:06.035563+00:00",
                      "latest_changed_by":  "user",
                      "manager":  "minikube-user",
                      "reason":  "manual-looking audit actor \u0027minikube-user\u0027",
                      "change_cause":  "",
                      "event_count":  1,
                      "age_hours":  13.45,
                      "drift_threshold_hours":  24,
                      "is_manual_change":  true,
                      "is_drifted":  false,
                      "verb":  "patch",
                      "username":  "minikube-user",
                      "user_agent":  "kubectl.exe/v1.32.2 (windows/amd64) kubernetes/67a30c0",
                      "source_ip":  "192.168.49.1",
                      "event_source":  "k8s_audit_log",
                      "selected_event_strategy":  "latest_manual_change",
                      "latest_observed_at":  "2026-06-17T13:23:06.035563+00:00",
                      "latest_observed_by":  "user",
                      "latest_observed_manager":  "minikube-user",
                      "latest_observed_reason":  "manual-looking audit actor \u0027minikube-user\u0027",
                      "latest_observed_verb":  "patch"
                  },
```

## 丧失可维护性

#### 接口：

`/api/maintainability`

```
Get-Api "/api/maintainability" | ConvertTo-Json -Depth 8
```

#### 返回结构：

startup_issues: 启动/扩容时间超过30s

```
 "startup_issues":  [
                      {
                                               "service":  "cart",
                                               "pod":  "cart-5756f5d76f-qmkf4",
                                               "namespace":  "default",
                                               "startup_seconds":  33.0,//创建到运行的启动时间
                                               "threshold_seconds":  30
                                           },
                                           {
                                               "service":  "checkout",
                                               "pod":  "checkout-7c7fc69749-ncmrd",
                                               "namespace":  "default",
                                               "startup_seconds":  109.0,
                                               "threshold_seconds":  30
                                           },
                                           {
                                               "service":  "flagd",
                                               "pod":  "flagd-c98989985-6ktr5",
                                               "namespace":  "default",
                                               "startup_seconds":  77.0,
                                               "threshold_seconds":  30
                                           },
                       ...
```

deployment_frequency_issues: 服务部署频率变低

```
                     ],
                    "deploy_frequency_issues":  [
                                                    {
                                                        "service":  "jaeger",
                                                        "deploy_count":  1,
                                                        "cluster_median_deploy_count":  3.0,
                                                        "low_deploy_frequency_ratio":  0.5,
                                                        "threshold_count":  1.5,
                                                        "first_deployed_at":  "2026-06-18T03:32:37+00:00",
                                                        "latest_deployed_at":  "2026-06-18T03:32:37+00:00"
                                                    },
                                                    {
                                                        "service":  "postgresql",
                                                        "deploy_count":  1,
                                                        "cluster_median_deploy_count":  3.0,
                                                        "low_deploy_frequency_ratio":  0.5,
                                                        "threshold_count":  1.5,
                                                        "first_deployed_at":  "2026-06-18T03:32:40+00:00",
                                                        "latest_deployed_at":  "2026-06-18T03:32:40+00:00"
                                                    },
                                                    {
                                                        "service":  "valkey-cart",
                                                        "deploy_count":  1,
                                                        "cluster_median_deploy_count":  3.0,
                                                        "low_deploy_frequency_ratio":  0.5,
                                                        "threshold_count":  1.5,
                                                        "first_deployed_at":  "2026-06-18T03:32:41+00:00",
                                                        "latest_deployed_at":  "2026-06-18T03:32:41+00:00"
                                                    }
                                                ],
```

rollback_issues：版本变更后异常回滚比例高，需要发生多次部署/回滚来触发

```
"rollback_issues":  [
                                            {
                                                "service":  "frontend-proxy",
                                                "deploy_count":  2,
                                                "rollback_count":  1,
                                                "total_events":  3,
                                                "rollback_ratio":  0.3333,
                                                "threshold":  0.25
                                            }
                                        ],
```

co_deployment_issues：协同部署，耦合程度高，也需要服务至少3次deploy才能判断，也就是三次以上协同部署。测试的时候采用多次滚动部署`frontend` `checkout` `payment`服务：

```
kubectl rollout restart deployment/frontend -n default
kubectl rollout restart deployment/checkout -n default
kubectl rollout restart deployment/payment -n default
```

结果如下：

```
"co_deployment_issues":  [
                                                 {
                                                     "service_a":  "checkout",
                                                     "service_b":  "frontend",
                                                     "deploy_events_a":  4,
                                                     "deploy_events_b":  3,
                                                     "overlap_ratio":  1.0,
                                                     "threshold":  0.8,
                                                     "min_events_per_service":  3
                                                 },
                                                 {
                                                     "service_a":  "checkout",
                                                     "service_b":  "payment",
                                                     "deploy_events_a":  4,
                                                     "deploy_events_b":  3,
                                                     "overlap_ratio":  1.0,
                                                     "threshold":  0.8,
                                                     "min_events_per_service":  3
                                                 },
                                                 {
                                                     "service_a":  "frontend",
                                                     "service_b":  "payment",
                                                     "deploy_events_a":  3,
                                                     "deploy_events_b":  3,
                                                     "overlap_ratio":  1.0,
                                                     "threshold":  0.8,
                                                     "min_events_per_service":  3
                                                 }
                                             ]
                }
```

## 缺少物理隔离

```
Get-Api "/api/isolation" | ConvertTo-Json -Depth 8
```

当前返回：

```
 "summary":  {
                    "critical_priority_threshold":  0,
                    "critical_services":  [
                                              "checkout",
                                              "frontend",
                                              "payment"   //手动配置的高优先级服务
                                          ],
                    "placements":  [//这个是展示这些高优先级服务配置的node和zone，分别在配置文件里面修改副本数为2 3 2
                                       {
                                           "service":  "checkout",
                                           "pod":  "checkout-7c7fc69749-ncmrd",
                                           "namespace":  "default",
                                           "node":  "arch-demo-m03",
                                           "zone":  "az-b",
                                           "labels":  {
                                                          "app.kubernetes.io/component":  "checkout",
                                                          "app.kubernetes.io/name":  "checkout",
                                                          "opentelemetry.io/name":  "checkout",
                                                          "pod-template-hash":  "7c7fc69749"
                                                      }
                                       },
                                       {
                                           "service":  "checkout",
                                           "pod":  "checkout-7c7fc69749-xrhbh",
                                           "namespace":  "default",
                                           "node":  "arch-demo-m02",
                                           "zone":  "az-a",
                                           "labels":  {
                                                          "app.kubernetes.io/component":  "checkout",
                                                          "app.kubernetes.io/name":  "checkout",
                                                          "opentelemetry.io/name":  "checkout",
                                                          "pod-template-hash":  "7c7fc69749",
                                                          "topology.kubernetes.io/zone":  "az-a"
                                                      }
                                       },
                                       {
                                           "service":  "frontend",
                                           "pod":  "frontend-74547fb9f8-g69v8",
                                           "namespace":  "default",
                                           "node":  "arch-demo-m03",
                                           "zone":  "az-b",
                                           "labels":  {
                                                          "app.kubernetes.io/component":  "frontend",
                                                          "app.kubernetes.io/name":  "frontend",
                                                          "opentelemetry.io/name":  "frontend",
                                                          "pod-template-hash":  "74547fb9f8",
                                                          "topology.kubernetes.io/zone":  "az-b"
                                                      }
                                       },
                                       {
                                           "service":  "frontend",
                                           "pod":  "frontend-74547fb9f8-s7m6b",
                                           "namespace":  "default",
                                           "node":  "arch-demo",
                                           "zone":  "az-a",
                                           "labels":  {
                                                          "app.kubernetes.io/component":  "frontend",
                                                          "app.kubernetes.io/name":  "frontend",
                                                          "opentelemetry.io/name":  "frontend",
                                                          "pod-template-hash":  "74547fb9f8"
                                                      }
                                       },
                                       {
                                           "service":  "frontend",
                                           "pod":  "frontend-74547fb9f8-xlj44",
                                           "namespace":  "default",
                                           "node":  "arch-demo-m05",
                                           "zone":  "az-c",
                                           "labels":  {
                                                          "app.kubernetes.io/component":  "frontend",
                                                          "app.kubernetes.io/name":  "frontend",
                                                          "opentelemetry.io/name":  "frontend",
                                                          "pod-template-hash":  "74547fb9f8",
                                                          "topology.kubernetes.io/zone":  "az-c"
                                                      }
                                       },
                                       {
                                           "service":  "payment",
                                           "pod":  "payment-68ccff6c8c-dpmgj",
                                           "namespace":  "default",
                                           "node":  "arch-demo-m05",
                                           "zone":  "az-c",
                                           "labels":  {
                                                          "app.kubernetes.io/component":  "payment",
                                                          "app.kubernetes.io/name":  "payment",
                                                          "opentelemetry.io/name":  "payment",
                                                          "pod-template-hash":  "68ccff6c8c"
                                                      }
                                       },
                                       {
                                           "service":  "payment",
                                           "pod":  "payment-68ccff6c8c-r9wrn",
                                           "namespace":  "default",
                                           "node":  "arch-demo-m02",
                                           "zone":  "az-a",
                                           "labels":  {
                                                          "app.kubernetes.io/component":  "payment",
                                                          "app.kubernetes.io/name":  "payment",
                                                          "opentelemetry.io/name":  "payment",
                                                          "pod-template-hash":  "68ccff6c8c",
                                                          "topology.kubernetes.io/zone":  "az-a"
                                                      }
                                       }
                                   ],
```

这一部分对应问题1。我先手动给服务`checkout`, `frontend`, `payment`配置为P0优先级，当前测试时部署在了5个Node上，但严格意义上都属于一台物理机。所以这里演示使用的是`MiniKube`里面的模拟多节点环境。

```
 "single_zone_services":  [

                                             ],
                    "service_zone_spread":  [
                                                {
                                                    "service":  "checkout",
                                                    "replica_nodes":  [
                                                                          "arch-demo-m02",
                                                                          "arch-demo-m03"
                                                                      ],
                                                    "replica_zones":  [
                                                                          "az-a",
                                                                          "az-b"
                                                                      ],
                                                    "replica_node_count":  2,
                                                    "replica_zone_count":  2
                                                },
                                                {
                                                    "service":  "frontend",
                                                    "replica_nodes":  [
                                                                          "arch-demo",
                                                                          "arch-demo-m03",
                                                                          "arch-demo-m05"
                                                                      ],
                                                    "replica_zones":  [
                                                                          "az-a",
                                                                          "az-b",
                                                                          "az-c"
                                                                      ],
                                                    "replica_node_count":  3,
                                                    "replica_zone_count":  3
                                                },
                                                {
                                                    "service":  "payment",
                                                    "replica_nodes":  [
                                                                          "arch-demo-m02",
                                                                          "arch-demo-m05"
                                                                      ],
                                                    "replica_zones":  [
                                                                          "az-a",
                                                                          "az-c"
                                                                      ],
                                                    "replica_node_count":  2,
                                                    "replica_zone_count":  2
                                                }
                                            ],
```

对应问题2，也就是关键服务副本缺少跨可用区配置。当前测试显然没有部署在 不同可用区 的 不同机器上，本地`Minikube`多节点也没有`AZ`概念，所以这里其实是手动给不同节点加了模拟AZ标签，配置如下：

```
arch-demo      az-a
arch-demo-m02  az-a
arch-demo-m03  az-b
arch-demo-m04  az-b
arch-demo-m05  az-c
```



当前涉及优先级的架构问题包括：违反依赖关系（高优先级不能强依赖低优先级），不合理资源利用（高优先级没有提前设定CPU/memory的request），缺少物理隔离（P0级别服务的pod在同一个物理节点）

- 违反依赖关系：可以改为一个服务被大量上游服务调用，入度很高，属于核心服务或者基础服务，但是却强依赖于一个被很少的服务调用的边缘服务，则出现了违反依赖关系
  - 可用`page rank`算法，也就是计算服务`pagerank`，一个服务被更多核心业务逻辑调用的话，这个值越高，但是如果他强依赖于一个`pagerank`很低的服务则可能有问题。结合高`in-degee`一起判断或许更好。
  - 查到一个图网络库`NetWork`
  
  目前已修改代码并验证，当前采用的判定逻辑+阈值是：
  
  ```
  1. 先判定一个调用边是不是strong，对每个 parent -> child 边
  - 调用量call_count>100
  - 计算皮尔逊相关系数，>0.7
  标记为strong
  
  再判断strong边是否违反依赖关系，改掉之前用优先级判断，先看source是不是核心服务：
  
  2. 计算pagerank和in_degree，
  - source_indegree >= min_core_indegree  //高入度
  - source_pagerank >= high_pagerank_threshold  //page_rank更高
  这里high_pagerank_threshold 不是固定数，是当前图的 70 分位数
  
  3. 再判断target是不是边缘服务：
  - target_indegree <= max_edge_indegree
  - target_pagerank <= low_pagerank_threshold
  这里low_pagerank_threshold 是当前图的 25 分位数
  
  最后还要判断，source_indgree>=target_indgree
  满足以上全部条件可以判定违反依赖关系
  ```
  
  测试返回：
  
  ```
  PS E:\Arch-project> (Get-Api "/api/reports") | Where-Object { $_ -like "*PriorityCheckAnalyzer*" }
  [2026-06-18 19:07:52] [WARNING] from PriorityCheckAnalyzer: Dependency hierarchy violation: core service 'checkout' (pagerank: 0.1328, indegree: 1) depends strongly on edge service 'currency' (pagerank: 0.0222, indegree: 1).
  [2026-06-18 19:07:52] [WARNING] from PriorityCheckAnalyzer: Dependency hierarchy violation: core service 'checkout' (pagerank: 0.1328, indegree: 1) depends strongly on edge service 'email' (pagerank: 0.0222, indegree: 1).
  ```
  
  
- 不合理资源利用：用高优先级/核心服务判断是否需要配置request不太合理
  - 区分不同的trace，两类，一类是同步调用链，也就是会影响用户的响应时间，在这一条可以被外部网关触发的同步调用树上的所有节点，都该配置request。
  - 而只出现在异步调用链，不涉及用户响应时间，完全后台消费型的服务，可以不配request；但是如果QPS很高，>>中位数，也需要配。
- 缺少物理隔离：这里的p0服务判定逻辑或许可以和违反依赖关系一样，判断这些是不是配置在了同一个node。
