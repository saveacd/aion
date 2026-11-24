package dev.aion.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Dns
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import dev.aion.app.AppContainer
import dev.aion.app.data.ControlStateDto
import dev.aion.app.data.ExchangeStatusDto
import dev.aion.app.data.Loadable
import dev.aion.app.data.SystemStatusDto
import dev.aion.app.ui.BaseViewModel
import dev.aion.app.ui.GlobalRefreshEffect
import dev.aion.app.ui.aionViewModel
import dev.aion.app.ui.components.AionCard
import dev.aion.app.ui.components.EmptyState
import dev.aion.app.ui.components.ErrorBanner
import dev.aion.app.ui.components.InfoBanner
import dev.aion.app.ui.components.KeyValueRow
import dev.aion.app.ui.components.ModePill
import dev.aion.app.ui.components.Pill
import dev.aion.app.ui.components.SectionHeader
import dev.aion.app.ui.components.SkeletonBlock
import dev.aion.app.ui.formatTime
import dev.aion.app.ui.theme.AionColors
import kotlinx.coroutines.flow.MutableStateFlow

class SystemViewModel(container: AppContainer) : BaseViewModel(container) {

    val status = MutableStateFlow(Loadable<SystemStatusDto>())

    init {
        refresh()
    }

    fun refresh() {
        load(status) { container.repository.systemStatus() }
    }
}

@Composable
fun SystemScreen(container: AppContainer, onNavigate: (String) -> Unit) {
    val vm: SystemViewModel = aionViewModel(container) { SystemViewModel(it) }
    val state by vm.status.collectAsState()

    GlobalRefreshEffect(container) { vm.refresh() }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .background(AionColors.Bg),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            val data = state.data
            AionCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("交易模式", style = MaterialTheme.typography.labelMedium, color = AionColors.Muted)
                        Spacer(Modifier.height(4.dp))
                        Text(
                            data?.mode ?: "未知",
                            style = MaterialTheme.typography.headlineSmall.copy(
                                fontFamily = FontFamily.Monospace,
                            ),
                            color = when (data?.mode?.uppercase()) {
                                "MAINNET" -> AionColors.Red
                                "TESTNET" -> AionColors.Amber
                                "PAPER" -> AionColors.Primary
                                else -> AionColors.Text
                            },
                        )
                    }
                    ModePill(data?.mode)
                }
                if (data == null && state.error != null) {
                    Spacer(Modifier.height(10.dp))
                    ErrorBanner(state.error ?: "", { vm.refresh() })
                }
                if (data != null && data.mode?.uppercase() != "MAINNET") {
                    Spacer(Modifier.height(12.dp))
                    InfoBanner(
                        "当前不是 MAINNET：所有交易都停留在 ${data.mode ?: "未知"} 模式，界面中的 PAPER / TESTNET 标记必须保留。",
                        AionColors.Amber,
                    )
                }
                if (data?.mainnetOn == true) {
                    Spacer(Modifier.height(10.dp))
                    InfoBanner(
                        "主网通道已开启（mainnet_enabled=true）。下单会直接影响真实资金，请再次确认风控状态。",
                        AionColors.Red,
                    )
                }
            }
        }

        item {
            AionCard {
                SectionHeader(title = "服务健康", subtitle = "来源 /system/status")
                Spacer(Modifier.height(10.dp))
                when {
                    state.data == null && state.error != null ->
                        ErrorBanner(state.error ?: "", { vm.refresh() })
                    state.data == null -> {
                        SkeletonBlock(40.dp)
                        Spacer(Modifier.height(8.dp))
                        SkeletonBlock(40.dp)
                    }
                    else -> {
                        val data = state.data
                        KeyValueRow("应用", data?.appName ?: "AION", mono = false)
                        KeyValueRow("版本", data?.version ?: "—")
                        HorizontalDivider(
                            color = AionColors.Border.copy(alpha = 0.6f),
                            modifier = Modifier.padding(vertical = 6.dp),
                        )
                        HealthRow("AI 服务", "在线", true)
                        HealthRow("行情同步", if (data?.dbOk == true) "正常" else "同步中", data?.dbOk == true)
                        HealthRow(
                            "交易所连接",
                            if (data?.exchange?.any { it.credentialsConfigured } == true) "已连接" else "未连接",
                            data?.exchange?.any { it.credentialsConfigured } == true,
                        )
                        KeyValueRow("检查时间", formatTime(data?.checkedAt))
                    }
                }
            }
        }

        item {
            AionCard {
                SectionHeader(
                    title = "交易所账户",
                    subtitle = "交易所连接状态，密钥不会保存在手机中",
                )
                Spacer(Modifier.height(10.dp))
                val exchanges = state.data?.exchange.orEmpty()
                if (state.data == null) {
                    SkeletonBlock(56.dp)
                } else if (exchanges.isEmpty()) {
                    EmptyState(
                        title = "未返回交易所状态",
                        message = "暂时无法读取交易所连接状态，请稍后刷新。",
                        icon = Icons.Outlined.Dns,
                    )
                } else {
                    exchanges.forEachIndexed { index, exchange ->
                        if (index > 0) {
                            HorizontalDivider(
                                color = AionColors.Border.copy(alpha = 0.5f),
                                modifier = Modifier.padding(vertical = 4.dp),
                            )
                        }
                        ExchangeRow(exchange)
                    }
                }
            }
        }

        item {
            AionCard {
                SectionHeader(title = "交易控制", subtitle = "当前保护状态，修改请到风控页")
                Spacer(Modifier.height(8.dp))
                val controls: ControlStateDto? = state.data?.controlsValue
                ControlLine("停止新开仓", controls?.stopNewTrades == true)
                ControlLine("暂停 AI 决策", controls?.pauseAi == true)
                ControlLine("紧急停止", controls?.emergencyStop == true)
                if (controls?.emergencyReason != null) {
                    Spacer(Modifier.height(6.dp))
                    InfoBanner("紧急原因：${controls.emergencyReason}", AionColors.Red)
                }
                Spacer(Modifier.height(12.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Button(
                        onClick = { vm.refresh() },
                        colors = ButtonDefaults.buttonColors(containerColor = AionColors.CardHigh),
                        shape = androidx.compose.foundation.shape.RoundedCornerShape(12.dp),
                    ) {
                        Text("刷新", color = AionColors.Text)
                    }
                    Button(
                        onClick = { onNavigate("settings") },
                        colors = ButtonDefaults.buttonColors(containerColor = AionColors.Primary),
                        shape = androidx.compose.foundation.shape.RoundedCornerShape(12.dp),
                    ) {
                        Text("前往设置")
                    }
                }
            }
        }

        item {
            Text(
                "PAPER = 模拟撮合；TESTNET = 交易所测试网；MAINNET = 真实主网资金。",
                style = MaterialTheme.typography.labelSmall,
                color = AionColors.Muted,
                modifier = Modifier.padding(bottom = 8.dp),
            )
        }
    }
}

@Composable
private fun HealthRow(label: String, value: String, ok: Boolean) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label,
            style = MaterialTheme.typography.bodySmall,
            color = AionColors.Muted,
            modifier = Modifier.weight(1f),
        )
        Pill(value, if (ok) AionColors.Green else AionColors.Red, filled = true)
    }
}

@Composable
private fun ControlLine(label: String, enabled: Boolean) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label,
            style = MaterialTheme.typography.bodySmall,
            color = AionColors.Muted,
            modifier = Modifier.weight(1f),
        )
        Pill(if (enabled) "已启用" else "未启用", if (enabled) AionColors.Red else AionColors.Muted)
    }
}

@Composable
private fun ExchangeRow(exchange: ExchangeStatusDto) {
    val configured = exchange.credentialsConfigured || exchange.configured == true
    Row(modifier = Modifier.padding(vertical = 6.dp)) {
        Column(modifier = Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    exchange.mode ?: "UNKNOWN",
                    style = MaterialTheme.typography.titleMedium.copy(fontFamily = FontFamily.Monospace),
                )
                Spacer(Modifier.width(6.dp))
                Pill(if (configured) "已配置" else "未配置", if (configured) AionColors.Green else AionColors.Amber)
            }
            Text(
                "api_key ${if (exchange.hasApiKey || configured) "已注入" else "缺失"} · secret ${if (exchange.hasApiSecret || configured) "已注入" else "缺失"}",
                style = MaterialTheme.typography.labelSmall,
                color = AionColors.Muted,
                modifier = Modifier.padding(top = 2.dp),
            )
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(
                "账户行 ${if (exchange.accountRowActive) "有效" else "未激活"}",
                style = MaterialTheme.typography.labelSmall,
                color = AionColors.Muted,
            )
            Text(
                "权限 ${if (exchange.permissionsChecked) "已校验" else "未校验"}",
                style = MaterialTheme.typography.labelSmall,
                color = AionColors.Muted,
                modifier = Modifier.padding(top = 2.dp),
            )
        }
    }
}
