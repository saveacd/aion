# AION Android 客户端

AION Android 客户端采用 **Kotlin + Jetpack Compose + Material 3** 原生开发，不使用 WebView 套壳。项目入口、整体架构、功能边界和后端部署说明请先阅读仓库根目录的 [`README.md`](../README.md)。

## 环境要求

- JDK 17
- Android SDK 35（`compileSdk 35`，`minSdk 26`）
- Gradle 8.14 或兼容版本
- Android Studio（推荐）

项目未内置 Gradle Wrapper。可使用 Android Studio 打开本目录并完成 Gradle Sync，或在本机安装 Gradle 后执行：

```bash
gradle assembleDebug
```

Debug APK 预期输出位置：

```text
app/build/outputs/apk/debug/app-debug.apk
```

只有命令实际构建成功后，才能认定 APK 已生成。

## 配置后端地址

后端地址通过 `app/build.gradle.kts` 中的 `AION_BASE_URL` 配置：

```kotlin
buildConfigField("String", "AION_BASE_URL", "\"http://10.0.2.2:8080\"")
```

`10.0.2.2` 是 Android 模拟器访问开发机的特殊地址。真机或部署环境请改为实际服务器地址，正式环境建议使用 HTTPS。不要把数据库密码、模型密钥或交易所 API 密钥写入 Android 工程。

## 客户端功能说明

客户端包含首页、交易、行情、信号及个人/更多功能入口，并提供因子智能与自主研究实验室相关页面。研究实验室通过认证 API 查询后台研究任务、策略候选和 PAPER 观察会话。模拟交易数据仅用于研究观察，不代表交易所真实成交。

部分任务书规划的完整页面、审批证据视图和端到端联调仍未完成；具体边界以根目录 README 的“当前实现边界与已知未完成项”为准。


## AI 自动驾驶与因子进化

Android 客户端与 Web 共用 `/api/v1/autopilot` 配置/状态接口，以及研究实验室 API。Autopilot 是交易决策巡航链路；因子研究实验室是独立的进化链路。ACTIVE 因子实时信号会进入后端六 AI 分析上下文，研究任务本身不直接提交订单。客户端应如实展示巡航启停状态、账户模式、最近运行时间和错误状态，不得仅凭页面开关推断后台 worker 已健康运行。默认账户模式为 PAPER；模拟结果不代表真实成交。
