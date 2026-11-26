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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewModelScope
import dev.aion.app.AppContainer
import dev.aion.app.data.AutopilotStatusDto
import dev.aion.app.data.AutopilotUpdateRequest
import dev.aion.app.data.Loadable
import dev.aion.app.ui.BaseViewModel
import dev.aion.app.ui.GlobalRefreshEffect
import dev.aion.app.ui.aionViewModel
import dev.aion.app.ui.components.AionCard
import dev.aion.app.ui.components.ConfirmDialog
import dev.aion.app.ui.components.ErrorBanner
import dev.aion.app.ui.components.KeyValueRow
import dev.aion.app.ui.components.Pill
import dev.aion.app.ui.components.SectionHeader
import dev.aion.app.ui.components.SkeletonBlock
import dev.aion.app.ui.fmtNum
import dev.aion.app.ui.fmtSignedUsd
import dev.aion.app.ui.formatTime
import dev.aion.app.ui.theme.AionColors
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

class AutopilotViewModel(container: AppContainer) : BaseViewModel(container) {

    val status = MutableStateFlow(Loadable<AutopilotStatusDto>())
    val saving = MutableStateFlow(false)
    val message = MutableStateFlow<String?>(null)

    init {
        refresh()
    }

    fun refresh() {
        load(status) { container.repository.autopilotStatus() }
    }

    fun submit(body: AutopilotUpdateRequest) {
        viewModelScope.launch {
            saving.value = true
            message.value = null
            when (val result = container.repository.updateAutopilot(body)) {
                is dev.aion.app.data.ApiResult.Success -> {
                    message.value = "已保存"
                    refresh()
                }
                is dev.aion.app.data.ApiResult.Failure ->
                    message.value = result.userMessage()
            }
            saving.value = false
        }
    }

    fun close(id: String) {
        viewModelScope.launch {
            message.value = null
            when (val result = container.repository.closeLivePosition(id)) {
                is dev.aion.app.data.ApiResult.Success -> {
                    message.value = "已提交平仓"
                    refresh()
                }
                is dev.aion.app.data.ApiResult.Failure -> message.value = result.userMessage()
            }
        }
    }
}

@Composable
fun AutopilotScreen(container: AppContainer) {
    val vm: AutopilotViewModel = aionViewModel(container) { AutopilotViewModel(it) }
    val state by vm.status.collectAsState()
    val saving by vm.saving.collectAsState()
    val message by vm.message.collectAsState()

    GlobalRefreshEffect(container) { vm.refresh() }

    val data = state.data
    var initialized by remember { mutableStateOf(false) }
    var enabled by remember { mutableStateOf(false) }
    var autoExec by remember { mutableStateOf(false) }
    var symbols by remember { mutableStateOf("") }
    var cycle by remember { mutableStateOf("600") }
    var maxTrades by remember { mutableStateOf("3") }
    var minConf by remember { mutableStateOf("0.65") }
    var intraday by remember { mutableStateOf(true) }
    var holdMinutes by remember { mutableStateOf("240") }
    var confirmExec by remember { mutableStateOf(false) }
    var mode by remember { mutableStateOf("PAPER") }
    var liveTotp by remember { mutableStateOf("") }

    if (data != null && !initialized) {
        enabled = data.config.enabled
        autoExec = data.config.autoExecute
        mode = data.config.executionMode
        symbols = data.config.symbols.joinToString(",")
        cycle = data.config.cycleSeconds.toString()
        maxTrades = data.config.maxTradesPerDay.toString()
        minConf = data.config.minConfidence
        intraday = data.config.intraday
        holdMinutes = data.config.maxHoldingMinutes.toString()
        initialized = true
    }

    if (confirmExec) {
        ConfirmDialog(
            title = "开启 AI 全权托管",
            message = when (mode) {
                "MAINNET" -> "主网使用真实资金：AI 将自动开仓、挂止盈止损并自动平仓。每笔订单仍需通过独立风控引擎，亏损由你承担。确认继续？"
                "TESTNET" -> "测试网使用 Binance 测试资金：AI 将自动开仓、挂止盈止损并自动平仓。确认继续？"
                else -> "模拟盘使用内部撮合，不碰真实资金：AI 将自动开仓、止盈止损并自动平仓，用于验证策略。确认开启？"
            },
            confirmLabel = "确认托管",
            destructive = mode == "MAINNET",
            onDismiss = { confirmExec = false },
            onConfirm = {
                confirmExec = false
                autoExec = true
            },
        )
    }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .background(AionColors.Bg),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            AionCard {
                SectionHeader(title = "AI 全权托管", subtitle = "授权后 AI 自动分析、开仓、止盈止损与平仓")
                Spacer(Modifier.height(12.dp))
                if (state.error != null && data == null) {
                    ErrorBanner(state.error ?: "", { vm.refresh() })
                    return@AionCard
                }
                Text("执行账户", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(6.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf("PAPER" to "模拟盘", "TESTNET" to "测试网", "MAINNET" to "主网").forEach { (value, label) ->
                        OutlinedButton(
                            onClick = {
                                if (mode != value) {
                                    if (autoExec && value != "PAPER") {
                                        autoExec = false
                                        confirmExec = true
                                    }
                                    liveTotp = ""
                                    mode = value
                                }
                            },
                            modifier = Modifier.weight(1f),
                            colors = ButtonDefaults.outlinedButtonColors(
                                containerColor = if (mode == value) AionColors.Primary.copy(alpha = 0.16f) else AionColors.Bg,
                                contentColor = if (mode == value) AionColors.Primary else AionColors.Text,
                            ),
                        ) { Text(label, style = MaterialTheme.typography.labelMedium) }
                    }
                }
                Spacer(Modifier.height(12.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("启用自动分析", style = MaterialTheme.typography.titleMedium)
                        Text("周期性复用全局市场研究并评估账户", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                    }
                    Switch(
                        checked = enabled,
                        onCheckedChange = { enabled = it },
                        colors = SwitchDefaults.colors(checkedTrackColor = AionColors.Primary),
                    )
                }
                Spacer(Modifier.height(8.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("自动开仓 / 止盈止损 / 平仓", style = MaterialTheme.typography.titleMedium)
                        Text("风控通过后自动成交，服务端持续保护", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                    }
                    Switch(
                        checked = autoExec,
                        onCheckedChange = { want ->
                            if (want) confirmExec = true else autoExec = false
                        },
                        colors = SwitchDefaults.colors(checkedTrackColor = AionColors.Green),
                    )
                }
                Spacer(Modifier.height(8.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("日内模式", style = MaterialTheme.typography.titleMedium)
                        Text("只做当天可平仓的机会，超时自动平仓", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                    }
                    Switch(
                        checked = intraday,
                        onCheckedChange = { intraday = it },
                        colors = SwitchDefaults.colors(checkedTrackColor = AionColors.Primary),
                    )
                }
                if (intraday) {
                    OutlinedTextField(
                        value = holdMinutes,
                        onValueChange = { holdMinutes = it.filter(Char::isDigit) },
                        label = { Text("最长持仓（分钟）") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    )
                }
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = symbols,
                    onValueChange = { symbols = it },
                    label = { Text("交易对（逗号分隔）") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(
                        value = cycle,
                        onValueChange = { cycle = it.filter(Char::isDigit) },
                        label = { Text("间隔秒") },
                        modifier = Modifier.weight(1f),
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    )
                    OutlinedTextField(
                        value = maxTrades,
                        onValueChange = { maxTrades = it.filter(Char::isDigit) },
                        label = { Text("每日上限（0 不限）") },
                        modifier = Modifier.weight(1f),
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    )
                    OutlinedTextField(
                        value = minConf,
                        onValueChange = { minConf = it },
                        label = { Text("置信度") },
                        modifier = Modifier.weight(1f),
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    )
                }
                Spacer(Modifier.height(12.dp))
                if (mode == "MAINNET" && enabled && autoExec &&
                    (data?.config?.liveAuthorizedAt == null || data.config.executionMode != mode)
                ) {
                    OutlinedTextField(
                        value = liveTotp,
                        onValueChange = { liveTotp = it.filter(Char::isDigit).take(8) },
                        label = { Text("主网授权验证码") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                        visualTransformation = PasswordVisualTransformation(),
                    )
                }
                Button(
                    onClick = {
                        val symbolList = symbols.split(",").map { it.trim().uppercase() }
                            .filter { it.isNotEmpty() }
                        vm.submit(
                            AutopilotUpdateRequest(
                                enabled = enabled,
                                autoExecute = autoExec,
                                confirmAutoExecute = autoExec,
                                executionMode = mode,
                                symbols = symbolList.ifEmpty { null },
                                intraday = intraday,
                                maxHoldingMinutes = holdMinutes.toIntOrNull(),
                                cycleSeconds = cycle.toIntOrNull(),
                                maxTradesPerDay = maxTrades.toIntOrNull(),
                                minConfidence = minConf.toDoubleOrNull(),
                                totpCode = liveTotp.takeIf { it.isNotBlank() && mode == "MAINNET" },
                            ),
                        )
                        liveTotp = ""
                    },
                    enabled = !saving,
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = AionColors.Primary),
                ) {
                    Text(if (saving) "保存中…" else "保存配置")
                }
                message?.let {
                    Spacer(Modifier.height(8.dp))
                    Text(it, style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                }
            }
        }

        if (data != null) {
            item {
                AionCard {
                    SectionHeader(title = "运行状态")
                    Spacer(Modifier.height(8.dp))
                    KeyValueRow("交易模式", data.config.tradingMode ?: "—")
                    KeyValueRow("上次运行", formatTime(data.config.lastRunAt), mono = false)
                    val cap = data.config.maxTradesPerDay
                    KeyValueRow("今日已开仓", if (cap == 0) "${data.config.tradesToday} · 不限次数" else "${data.config.tradesToday} / $cap")
                    if (data.config.executionMode != "PAPER") {
                        KeyValueRow("持续托管授权", if (data.config.liveAuthorizedAt == null) "未授权" else "已授权", mono = false)
                    }
                    KeyValueRow("今日已实现盈亏", fmtSignedUsd(data.todayRealizedPnlUsd))
                    KeyValueRow("累计已平仓", "${data.totalTrades} 笔")
                    data.config.lastError?.takeIf { it.isNotBlank() }?.let {
                        Spacer(Modifier.height(6.dp))
                        Text(it, style = MaterialTheme.typography.labelSmall, color = AionColors.Red)
                    }
                }
            }

            if (data.config.watchPlans.isNotEmpty()) {
                item {
                    AionCard {
                        SectionHeader(
                            title = "AI 等待区",
                            subtitle = "价格进入区间后立即重新分析，符合条件会自动下单",
                        )
                        Spacer(Modifier.height(8.dp))
                        data.config.watchPlans.forEach { plan ->
                            Column(modifier = Modifier.padding(vertical = 8.dp)) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text(
                                        "${plan.symbol} ${if (plan.side == "SELL") "做空" else "做多"} · ${plan.leverage}x",
                                        style = MaterialTheme.typography.titleMedium,
                                    )
                                    Spacer(Modifier.weight(1f))
                                    Pill(
                                        confidenceLabel(plan.confidence),
                                        AionColors.Primary,
                                    )
                                }
                                Spacer(Modifier.height(4.dp))
                                Text(
                                    "入场 ${fmtNum(plan.entryLow, 4)} – ${fmtNum(plan.entryHigh, 4)}",
                                    style = MaterialTheme.typography.bodyMedium,
                                )
                                Text(
                                    "止损 ${plan.stopLoss?.let { fmtNum(it, 4) } ?: "—"} · 止盈 ${plan.takeProfit?.let { fmtNum(it, 4) } ?: "—"}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = AionColors.Muted,
                                )
                                plan.reason.takeIf { it.isNotBlank() }?.let {
                                    Text(
                                        it,
                                        style = MaterialTheme.typography.labelSmall,
                                        color = AionColors.Muted,
                                    )
                                }
                                Text(
                                    "有效期至 ${formatTime(plan.expiresAt)}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = AionColors.Muted,
                                )
                            }
                        }
                    }
                }
            }

            item {
                AionCard {
                    SectionHeader(title = "当前模拟持仓", subtitle = "${data.openPositions.size} 个")
                    Spacer(Modifier.height(8.dp))
                    if (data.openPositions.isEmpty()) {
                        Text("暂无持仓", style = MaterialTheme.typography.bodySmall, color = AionColors.Muted)
                    } else {
                        data.openPositions.forEach { p ->
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(vertical = 6.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Column(modifier = Modifier.weight(1f)) {
                                    Text("${p.symbol} ${p.positionSide} ${p.leverage}x", style = MaterialTheme.typography.titleMedium)
                                    Text(
                                        "开仓 ${fmtNum(p.entryPrice, 4)} · 止损 ${p.stopLoss?.let { fmtNum(it, 4) } ?: "—"} · 止盈 ${p.takeProfit?.let { fmtNum(it, 4) } ?: "—"}",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = AionColors.Muted,
                                    )
                                }
                                Pill(fmtNum(p.quantity, 6), AionColors.Primary)
                            }
                        }
                    }
                }
            }

            item {
                AionCard {
                    SectionHeader(title = "真实持仓 · AI 托管中", subtitle = "${data.livePositions.size} 个")
                    Spacer(Modifier.height(8.dp))
                    if (data.livePositions.isEmpty()) {
                        Text(
                            "暂无真实持仓。AI 触发开仓后，会自动挂止盈止损并持续保护。",
                            style = MaterialTheme.typography.bodySmall,
                            color = AionColors.Muted,
                        )
                    } else {
                        data.livePositions.forEach { p ->
                            Column(modifier = Modifier.padding(vertical = 8.dp)) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text(
                                        "${p.symbol} ${p.positionSide} ${p.leverage}x",
                                        style = MaterialTheme.typography.titleMedium,
                                    )
                                    Spacer(Modifier.weight(1f))
                                    Pill(
                                        modeLabel(p.mode),
                                        if (p.mode == "MAINNET") AionColors.Red else AionColors.Primary,
                                    )
                                }
                                Spacer(Modifier.height(4.dp))
                                Text(
                                    "开仓 ${fmtNum(p.entryPrice, 4)} · 止损 ${p.stopLoss?.let { fmtNum(it, 4) } ?: "—"} · 止盈 ${p.takeProfit?.let { fmtNum(it, 4) } ?: "—"}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = AionColors.Muted,
                                )
                                Spacer(Modifier.height(2.dp))
                                Text(
                                    "保护单 ${protectionLabel(p.protectionStatus)} · 数量 ${fmtNum(p.quantity, 6)} · ${formatTime(p.openedAt)}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = if (p.protectionStatus == "PROTECTED") AionColors.Green else AionColors.Muted,
                                )
                                Spacer(Modifier.height(6.dp))
                                OutlinedButton(
                                    onClick = { p.id?.let { vm.close(it) } },
                                    modifier = Modifier.fillMaxWidth(),
                                    colors = ButtonDefaults.outlinedButtonColors(contentColor = AionColors.Red),
                                ) { Text("立即平仓") }
                            }
                        }
                    }
                }
            }

            item {
                AionCard {
                    SectionHeader(title = "最近自动分析")
                    Spacer(Modifier.height(8.dp))
                    if (data.recentRuns.isEmpty()) {
                        Text("暂无自动分析记录", style = MaterialTheme.typography.bodySmall, color = AionColors.Muted)
                    } else {
                        data.recentRuns.forEach { run ->
                            Column(modifier = Modifier.padding(vertical = 6.dp)) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text(run.symbol, style = MaterialTheme.typography.titleMedium)
                                    Spacer(Modifier.weight(1f))
                                    Pill(run.verdict ?: "—", if (run.verdict == "TRADE") AionColors.Green else AionColors.Muted)
                                }
                                Text(
                                    "${formatTime(run.startedAt)} · ${run.summary?.take(100) ?: ""}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = AionColors.Muted,
                                )
                            }
                        }
                    }
                }
            }
        } else if (state.error == null) {
            item {
                SkeletonBlock(140.dp)
                Spacer(Modifier.height(10.dp))
                SkeletonBlock(120.dp)
            }
        }
    }
}

private fun modeLabel(mode: String): String = when (mode) {
    "MAINNET" -> "主网"
    "TESTNET" -> "测试网"
    else -> "模拟盘"
}

private fun confidenceLabel(raw: String): String {
    val value = raw.toDoubleOrNull() ?: return raw
    return "${(value * 100).toInt()}%"
}

private fun protectionLabel(status: String): String = when (status) {
    "PROTECTED" -> "已挂止盈止损"
    "PARTIAL" -> "部分保护"
    "PENDING" -> "挂单中"
    "FAILED" -> "保护单异常"
    else -> status
}
