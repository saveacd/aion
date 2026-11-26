# AION Android 客户端

AION Android v2.16.0 使用 **Kotlin、Jetpack Compose 和 Material 3**。项目架构和后端部署说明见根目录 [`README.md`](../README.md)。

## 环境要求

- JDK 17
- Android SDK 35（`compileSdk 35`，`minSdk 26`）
- Gradle 8.7 或兼容版本
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

`10.0.2.2` 用于 Android 模拟器访问开发机。真机使用的构建应将对应构建类型中的 `AION_BASE_URL` 设置为实际 HTTPS 后端地址。数据库密码、模型密钥和交易所凭据保存在服务端。

## 客户端功能说明

客户端包含首页、交易、行情、信号和个人中心。进入「更多 → 研究实验室」可提交研究任务、设置预测长度、查看跨资产与样本外验证结果，以及复算已归档实验。策略候选和 PAPER 观察数据也通过已认证的 API 加载。

部分任务书规划的完整页面、审批证据视图和端到端联调仍未完成；具体边界以根目录 README 的“当前实现边界与已知未完成项”为准。

## AI 自动驾驶与因子进化

Android 与 Web 共用 `/api/v1/autopilot`。托管页面显示账户模式、授权状态和运行结果。默认账户模式为 PAPER。开启主网自动执行需要明确授权和首次 TOTP 验证，授权持续至关闭托管、关闭自动执行或切换模式。每日开仓次数上限设为 `0` 时不限次数，独立风控继续生效。
