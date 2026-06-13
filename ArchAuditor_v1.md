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
