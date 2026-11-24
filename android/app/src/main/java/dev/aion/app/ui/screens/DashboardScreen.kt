package dev.aion.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AccountBalanceWallet
import androidx.compose.material.icons.outlined.ArrowForward
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewModelScope
import dev.aion.app.AppContainer
import dev.aion.app.data.AccountOverviewDto
import dev.aion.app.data.AccountSnapshotDto
import dev.aion.app.data.ConnectionState
import dev.aion.app.data.ControlStateDto
import dev.aion.app.data.Loadable
import dev.aion.app.data.PositionDto
import dev.aion.app.data.SystemStatusDto
import dev.aion.app.ui.BaseViewModel
import dev.aion.app.ui.GlobalRefreshEffect
import dev.aion.app.ui.aionViewModel
import dev.aion.app.ui.components.AionCard
import dev.aion.app.ui.components.ConnectionText
import dev.aion.app.ui.components.EmptyState
import dev.aion.app.ui.components.ErrorBanner
import dev.aion.app.ui.components.KeyValueRow
import dev.aion.app.ui.components.LoadableContent
import dev.aion.app.ui.components.MetricTile
import dev.aion.app.ui.components.ModePill
import dev.aion.app.ui.components.Num
import dev.aion.app.ui.components.Pill
import dev.aion.app.ui.components.SkeletonBlock
import dev.aion.app.ui.components.StatusDot
import dev.aion.app.ui.theme.AionColors
import dev.aion.app.ui.fmtNum
import dev.aion.app.ui.fmtPct
import dev.aion.app.ui.fmtSignedUsd
import dev.aion.app.ui.fmtUsd
import dev.aion.app.ui.formatTime
import dev.aion.app.ui.pnlColor
import dev.aion.app.ui.sideColor
import dev.aion.app.ui.toDecimal
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

class DashboardViewModel(container: AppContainer) : BaseViewModel(container) {

    val overview = MutableStateFlow(Loadable<AccountOverviewDto>())
    val system = MutableStateFlow(Loadable<SystemStatusDto>())

    init {
        refresh()
        viewModelScope.launch {
            container.realtime.events.collect { event ->
                val type = event.type.lowercase()
                if (type.contains("account") || type.contains("position") ||
                    type.contains("order") || type.contains("control")
                ) {
                    load(overview) { container.repository.overview() }
                }
            }
        }
    }

    fun refresh() {
        load(overview) { container.repository.overview() }
        load(system) { container.repository.systemStatus() }
    }
}

@Composable
fun DashboardScreen(container: AppContainer, onNavigate: (String) -> Unit) {
    val vm: DashboardViewModel = aionViewModel(container) { DashboardViewModel(it) }
    val overview by vm.overview.collectAsState()
    val system by vm.system.collectAsState()
    val connection by container.appState.connection.collectAsState()

    GlobalRefreshEffect(container) { vm.refresh() }
    LaunchedEffect(Unit) {
        while (true) {
            delay(15_000)
            vm.refresh()
        }
    }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .background(AionColors.Bg),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            LoadableContent(
                state = overview,
                onRetry = { vm.refresh() },
                isEmpty = { it.snapshotValue == null },
                emptyTitle = "暂无账户数据",
                emptyMessage = "正在同步你的账户数据，稍后这里会显示权益与保证金。",
                emptyIcon = Icons.Outlined.AccountBalanceWallet,
            ) { data ->
                EquityCard(data)
            }
        }

        item {
            RiskAndControlCard(
                overview = overview,
                system = system,
                connection = connection,
                onRetry = { vm.refresh() },
            )
        }

        item {
            PositionsPreview(
                positions = overview.data?.positions.orEmpty(),
                loading = overview.data == null,
                onOpen = { onNavigate("positions") },
            )
        }

        item {
            SystemHealthCard(system = system, connection = connection, onRetry = { vm.refresh() })
        }

        item {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                QuickAction("去交易", Modifier.weight(1f)) { onNavigate("trading") }
                QuickAction("查看订单", Modifier.weight(1f)) { onNavigate("orders") }
                QuickAction("风控", Modifier.weight(1f)) { onNavigate("risk") }
            }
        }

        item {
            Text(
                "数据由 AION 云端实时同步 · 客户端不生成任何行情或账户数据",
                style = MaterialTheme.typography.labelSmall,
                color = AionColors.Muted.copy(alpha = 0.75f),
                modifier = Modifier.padding(top = 4.dp, bottom = 8.dp),
            )
        }
    }
}

@Composable
private fun QuickAction(label: String, modifier: Modifier, onClick: () -> Unit) {
    Box(
        modifier = modifier
            .clip(RoundedCornerShape(12.dp))
            .background(AionColors.CardHigh)
            .padding(horizontal = 10.dp, vertical = 12.dp),
        contentAlignment = Alignment.Center,
    ) {
        TextButton(onClick = onClick, contentPadding = PaddingValues(0.dp)) {
            Text(
                label,
                style = MaterialTheme.typography.labelLarge,
                color = AionColors.Primary,
                maxLines = 1,
            )
        }
    }
}

@Composable
private fun EquityCard(data: AccountOverviewDto) {
    val snapshot = data.snapshotValue
    val mode = data.mode ?: snapshot?.mode
    val stale = data.stale || snapshot?.stale == true

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(20.dp))
            .background(
                Brush.linearGradient(
                    listOf(Color(0xFF17264A), AionColors.Card),
                ),
            )
            .padding(20.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "账户总权益 · USD",
                style = MaterialTheme.typography.labelMedium,
                color = AionColors.Muted,
            )
            Spacer(Modifier.width(8.dp))
            ModePill(mode)
            if (stale) {
                Spacer(Modifier.width(6.dp))
                Pill("数据过期", AionColors.Amber)
            }
        }
        Spacer(Modifier.height(8.dp))
        Text(
            fmtUsd(snapshot?.equityValue),
            style = MaterialTheme.typography.displaySmall,
            color = AionColors.Text,
        )
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(18.dp)) {
            Column {
                Text("未实现盈亏", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                Num(
                    fmtSignedUsd(snapshot?.unrealizedValue),
                    style = MaterialTheme.typography.titleMedium,
                    color = pnlColor(snapshot?.unrealizedValue),
                )
            }
            Column {
                Text("日已实现", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                Num(
                    fmtSignedUsd(snapshot?.dailyRealizedPnl),
                    style = MaterialTheme.typography.titleMedium,
                    color = pnlColor(snapshot?.dailyRealizedPnl),
                )
            }
        }

        Spacer(Modifier.height(16.dp))
        HorizontalDivider(color = AionColors.Border.copy(alpha = 0.7f))
        Spacer(Modifier.height(14.dp))

        Row(modifier = Modifier.fillMaxWidth()) {
            MetricTile(
                label = "可用余额",
                value = fmtUsd(snapshot?.availableBalance),
                modifier = Modifier.weight(1f),
            )
            MetricTile(
                label = "保证金余额",
                value = fmtUsd(snapshot?.totalMarginBalance),
                modifier = Modifier.weight(1f),
            )
            MetricTile(
                label = "持仓数",
                value = snapshot?.openPositions?.toString() ?: "—",
                modifier = Modifier.weight(0.7f),
            )
        }

        Spacer(Modifier.height(12.dp))
        Num(
            "更新于 ${formatTime(snapshot?.capturedAt)}",
            style = MaterialTheme.typography.labelSmall,
            color = AionColors.Muted,
        )
    }
}

@Composable
private fun RiskAndControlCard(
    overview: Loadable<AccountOverviewDto>,
    system: Loadable<SystemStatusDto>,
    connection: ConnectionState,
    onRetry: () -> Unit,
) {
    val snapshot: AccountSnapshotDto? = overview.data?.snapshotValue
    val controls: ControlStateDto? = system.data?.controlsValue
    val marginRatio = toDecimal(snapshot?.marginRatio)
    val drawdown = toDecimal(snapshot?.drawdownPct)

    val (label, color) = when {
        controls?.emergencyStop == true -> "紧急停止" to AionColors.Red
        controls?.stopNewTrades == true -> "限制开仓" to AionColors.Amber
        controls?.pauseAi == true -> "AI 暂停" to AionColors.Amber
        overview.data?.stale == true -> "数据过期" to AionColors.Amber
        connection == ConnectionState.OFFLINE -> "连接断开" to AionColors.Amber
        marginRatio != null && marginRatio >= 0.8.toBigDecimal() -> "高风险" to AionColors.Red
        marginRatio != null && marginRatio >= 0.5.toBigDecimal() -> "关注" to AionColors.Amber
        drawdown != null && drawdown <= (-15).toBigDecimal() -> "回撤偏大" to AionColors.Amber
        else -> "正常" to AionColors.Green
    }

    AionCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("风险状态", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.width(10.dp))
            Pill(label, color, filled = true)
            Spacer(Modifier.weight(1f))
            TextButton(onClick = { onRetry() }) {
                Text("刷新", color = AionColors.Primary, style = MaterialTheme.typography.labelMedium)
            }
        }
        Spacer(Modifier.height(8.dp))
        if (overview.error != null && overview.data == null && snapshot == null) {
            ErrorBanner(overview.error, onRetry)
            return@AionCard
        }
        Row(modifier = Modifier.fillMaxWidth()) {
            MetricTile(
                label = "保证金率",
                value = fmtPct(snapshot?.marginRatio),
                modifier = Modifier.weight(1f),
            )
            MetricTile(
                label = "回撤",
                value = fmtPct(snapshot?.drawdownPct),
                modifier = Modifier.weight(1f),
                valueColor = if (drawdown != null && drawdown.signum() < 0) AionColors.Red else AionColors.Text,
            )
            MetricTile(
                label = "维持保证金",
                value = fmtUsd(snapshot?.totalMaintMargin),
                modifier = Modifier.weight(1.1f),
            )
        }
        Spacer(Modifier.height(14.dp))
        HorizontalDivider(color = AionColors.Border.copy(alpha = 0.7f))
        Spacer(Modifier.height(8.dp))
        ControlRow("停止新开仓", controls?.stopNewTrades)
        ControlRow("暂停 AI 决策", controls?.pauseAi)
        ControlRow("紧急停止", controls?.emergencyStop)
        if (controls?.emergencyReason != null) {
            Spacer(Modifier.height(6.dp))
            Text(
                "紧急原因：${controls.emergencyReason}",
                style = MaterialTheme.typography.bodySmall,
                color = AionColors.Red,
            )
        }
    }
}

@Composable
private fun ControlRow(label: String, value: Boolean?) {
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
        when (value) {
            true -> Pill("已启用", AionColors.Red, filled = true)
            false -> Pill("未启用", AionColors.Muted)
            null -> Text("—", style = MaterialTheme.typography.bodySmall, color = AionColors.Muted)
        }
    }
}

@Composable
private fun PositionsPreview(
    positions: List<PositionDto>,
    loading: Boolean,
    onOpen: () -> Unit,
) {
    AionCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("当前持仓", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.width(8.dp))
            if (positions.isNotEmpty()) {
                Pill("${positions.size}", AionColors.Primary)
            }
            Spacer(Modifier.weight(1f))
            TextButton(onClick = onOpen) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("全部", color = AionColors.Primary, style = MaterialTheme.typography.labelMedium)
                    Icon(
                        Icons.Outlined.ArrowForward,
                        contentDescription = null,
                        tint = AionColors.Primary,
                        modifier = Modifier
                            .padding(start = 2.dp)
                            .size(14.dp),
                    )
                }
            }
        }
        Spacer(Modifier.height(6.dp))
        when {
            loading -> {
                SkeletonBlock(48.dp)
                Spacer(Modifier.height(8.dp))
                SkeletonBlock(48.dp)
            }
            positions.isEmpty() -> {
                EmptyState(
                    title = "当前无持仓",
                    message = "当前没有持仓记录。",
                    icon = Icons.Outlined.AccountBalanceWallet,
                )
            }
            else -> {
                positions.take(3).forEachIndexed { index, position ->
                    if (index > 0) {
                        HorizontalDivider(
                            color = AionColors.Border.copy(alpha = 0.6f),
                            modifier = Modifier.padding(vertical = 4.dp),
                        )
                    }
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 7.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    position.symbol,
                                    style = MaterialTheme.typography.titleMedium.copy(
                                        fontFamily = FontFamily.Monospace,
                                    ),
                                    color = AionColors.Text,
                                )
                                Spacer(Modifier.width(6.dp))
                                Pill(position.positionSide, sideColor(position.positionSide))
                                Spacer(Modifier.width(6.dp))
                                Text(
                                    "${position.leverage}x",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = AionColors.Muted,
                                )
                            }
                            Num(
                                "开仓 ${fmtNum(position.entryPrice)} · 标记 ${fmtNum(position.markPrice)}",
                                style = MaterialTheme.typography.labelSmall,
                                color = AionColors.Muted,
                            )
                        }
                        Column(horizontalAlignment = Alignment.End) {
                            Num(
                                fmtSignedUsd(position.unrealizedPnl),
                                style = MaterialTheme.typography.titleMedium,
                                color = pnlColor(position.unrealizedPnl),
                                weight = FontWeight.SemiBold,
                            )
                            Num(
                                fmtPct(position.unrealizedPnlPct, signed = true),
                                style = MaterialTheme.typography.labelSmall,
                                color = pnlColor(position.unrealizedPnl),
                            )
                        }
                    }
                }
                if (positions.size > 3) {
                    Text(
                        "另有 ${positions.size - 3} 个持仓",
                        style = MaterialTheme.typography.labelSmall,
                        color = AionColors.Muted,
                        modifier = Modifier.padding(top = 4.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun SystemHealthCard(
    system: Loadable<SystemStatusDto>,
    connection: ConnectionState,
    onRetry: () -> Unit,
) {
    AionCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("系统连接", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.width(10.dp))
            StatusDot(connection)
            Spacer(Modifier.width(6.dp))
            Text(
                ConnectionText(connection),
                style = MaterialTheme.typography.labelSmall,
                color = AionColors.Muted,
            )
        }
        Spacer(Modifier.height(10.dp))
        val data = system.data
        when {
            data == null && system.error != null -> ErrorBanner(system.error, onRetry)
            data == null -> {
                SkeletonBlock(40.dp)
                Spacer(Modifier.height(8.dp))
                SkeletonBlock(40.dp)
            }
            else -> {
                KeyValueRow("AI 服务", "在线", valueColor = AionColors.Green)
                KeyValueRow(
                    "行情同步",
                    if (data.dbOk) "正常" else "同步中",
                    valueColor = if (data.dbOk) AionColors.Green else AionColors.Amber,
                )
                KeyValueRow(
                    "交易所连接",
                    if (data.exchange.any { it.credentialsConfigured }) "已连接" else "未连接",
                    valueColor = if (data.exchange.any { it.credentialsConfigured }) AionColors.Green else AionColors.Amber,
                )
                KeyValueRow("交易模式", data.mode ?: "—", valueColor = AionColors.Text)
                KeyValueRow("检查时间", formatTime(data.checkedAt))
            }
        }
    }
}
