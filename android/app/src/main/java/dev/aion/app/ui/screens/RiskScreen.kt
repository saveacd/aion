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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Shield
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewModelScope
import dev.aion.app.AppContainer
import dev.aion.app.data.ApiResult
import dev.aion.app.data.ControlStateDto
import dev.aion.app.data.Loadable
import dev.aion.app.data.RiskDecisionsResponse
import dev.aion.app.data.RiskEventDto
import dev.aion.app.data.RiskEventsResponse
import dev.aion.app.data.RiskPolicyDto
import dev.aion.app.data.RiskPolicyResponse
import dev.aion.app.data.SystemStatusDto
import dev.aion.app.ui.BaseViewModel
import dev.aion.app.ui.GlobalRefreshEffect
import dev.aion.app.ui.aionViewModel
import dev.aion.app.ui.components.AionCard
import dev.aion.app.ui.components.AionField
import dev.aion.app.ui.components.ConfirmDialog
import dev.aion.app.ui.components.EmptyState
import dev.aion.app.ui.components.ErrorBanner
import dev.aion.app.ui.components.InfoBanner
import dev.aion.app.ui.components.KeyValueRow
import dev.aion.app.ui.components.LabelText
import dev.aion.app.ui.components.ModePill
import dev.aion.app.ui.components.Num
import dev.aion.app.ui.components.Pill
import dev.aion.app.ui.components.SectionHeader
import dev.aion.app.ui.components.SkeletonBlock
import dev.aion.app.ui.formatTime
import dev.aion.app.ui.fmtNum
import dev.aion.app.ui.fmtPct
import dev.aion.app.ui.statusColor
import dev.aion.app.ui.theme.AionColors
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive

class RiskViewModel(container: AppContainer) : BaseViewModel(container) {

    val policy = MutableStateFlow(Loadable<RiskPolicyResponse>())
    val events = MutableStateFlow(Loadable<RiskEventsResponse>())
    val decisions = MutableStateFlow(Loadable<RiskDecisionsResponse>())
    val system = MutableStateFlow(Loadable<SystemStatusDto>())

    val controlBusy = MutableStateFlow(false)
    val controlError = MutableStateFlow<String?>(null)

    val policySaving = MutableStateFlow(false)
    val policyError = MutableStateFlow<String?>(null)
    val policySaved = MutableStateFlow(false)

    init {
        refresh()
    }

    fun refresh() {
        load(policy) { container.repository.policy() }
        load(events) { container.repository.riskEvents(50) }
        load(decisions) { container.repository.decisions(30) }
        load(system) { container.repository.systemStatus() }
    }

    fun setControl(action: String, enabled: Boolean, reason: String?) {
        if (controlBusy.value) return
        controlBusy.value = true
        controlError.value = null
        viewModelScope.launch {
            when (val result = container.repository.setControl(action, enabled, reason)) {
                is ApiResult.Success -> {
                    load(system) { container.repository.systemStatus() }
                    load(events) { container.repository.riskEvents(50) }
                }
                is ApiResult.Failure -> {
                    controlError.value = buildString {
                        append(result.userMessage())
                        result.details?.let { append("\n").append(it) }
                        result.requestId?.let { append("\nrequest_id: ").append(it) }
                    }
                }
            }
            controlBusy.value = false
        }
    }

    fun savePolicy(fields: Map<String, JsonElement>) {
        if (policySaving.value) return
        if (fields.isEmpty()) {
            policyError.value = "请至少填写一项限值"
            return
        }
        policySaving.value = true
        policyError.value = null
        viewModelScope.launch {
            when (val result = container.repository.updatePolicy(fields)) {
                is ApiResult.Success -> {
                    policy.value = Loadable(data = result.data)
                    policySaved.value = true
                }
                is ApiResult.Failure -> {
                    policyError.value = buildString {
                        append(result.userMessage())
                        result.details?.let { append("\n").append(it) }
                    }
                }
            }
            policySaving.value = false
        }
    }

    fun consumePolicySaved() {
        policySaved.value = false
    }

    fun clearErrors() {
        controlError.value = null
        policyError.value = null
    }
}

private data class PendingControl(val action: String, val enabled: Boolean, val label: String)

@Composable
fun RiskScreen(container: AppContainer) {
    val vm: RiskViewModel = aionViewModel(container) { RiskViewModel(it) }
    val policyState by vm.policy.collectAsState()
    val eventsState by vm.events.collectAsState()
    val decisionsState by vm.decisions.collectAsState()
    val systemState by vm.system.collectAsState()
    val controlBusy by vm.controlBusy.collectAsState()
    val controlError by vm.controlError.collectAsState()
    val policySaving by vm.policySaving.collectAsState()
    val policyError by vm.policyError.collectAsState()
    val policySaved by vm.policySaved.collectAsState()

    var pendingControl by remember { mutableStateOf<PendingControl?>(null) }
    var editingPolicy by remember { mutableStateOf(false) }

    GlobalRefreshEffect(container) { vm.refresh() }

    LaunchedEffect(policySaved) {
        if (policySaved) {
            editingPolicy = false
            vm.consumePolicySaved()
        }
    }

    val policy = policyState.data?.policy
    val controls: ControlStateDto? = systemState.data?.controlsValue

    pendingControl?.let { pending ->
        ConfirmDialog(
            title = if (pending.enabled) "启用「${pending.label}」？" else "关闭「${pending.label}」？",
            message = when (pending.action) {
                "EMERGENCY_STOP" ->
                    "紧急停止会立即禁止新交易并暂停 AI，同时撤销未成交挂单。该操作影响真实账户，请确认。"
                "STOP_NEW_TRADES" ->
                    "停止新开仓后，新提交的交易意图会被风控拒绝；已有持仓与保护单不受影响。"
                else ->
                    "暂停 AI 后，Agent 不再产生新的交易意图；手动下单不受影响。"
            },
            confirmLabel = if (pending.enabled) "确认启用" else "确认关闭",
            destructive = pending.action == "EMERGENCY_STOP" && pending.enabled,
            reasonLabel = "操作原因（可选）",
            onDismiss = { pendingControl = null },
            onConfirm = { reason ->
                vm.setControl(pending.action, pending.enabled, reason)
                pendingControl = null
            },
        )
    }

    if (editingPolicy) {
        PolicyEditDialog(
            policy = policy,
            saving = policySaving,
            error = policyError,
            onDismiss = {
                editingPolicy = false
                vm.clearErrors()
            },
            onSave = { fields -> vm.savePolicy(fields) },
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
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("风控中心", style = MaterialTheme.typography.titleMedium)
                        Text(
                            "服务端硬上限优先于本地展示",
                            style = MaterialTheme.typography.labelSmall,
                            color = AionColors.Muted,
                        )
                    }
                    ModePill(systemState.data?.mode)
                    Spacer(Modifier.width(6.dp))
                    Pill(
                        if (controls?.emergencyStop == true) "EMERGENCY" else if (controls?.stopNewTrades == true) "RESTRICTED" else "NORMAL",
                        if (controls?.emergencyStop == true) AionColors.Red
                        else if (controls?.stopNewTrades == true) AionColors.Amber
                        else AionColors.Green,
                        filled = true,
                    )
                }
                if (controlError != null) {
                    Spacer(Modifier.height(10.dp))
                    ErrorBanner(controlError ?: "", { vm.clearErrors() })
                }
            }
        }

        item {
            AionCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("风控限值", style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.width(8.dp))
                    if (policy != null) {
                        Pill(if (policy.isActive) "生效中" else "未生效", if (policy.isActive) AionColors.Green else AionColors.Muted)
                    }
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = { editingPolicy = true }) {
                        Text("调整限值", color = AionColors.Primary, style = MaterialTheme.typography.labelMedium)
                    }
                }
                Spacer(Modifier.height(8.dp))
                val policyData = policyState.data
                val policyRow = policyData?.policy
                when {
                    policyData == null && policyState.error != null -> ErrorBanner(policyState.error ?: "", { vm.refresh() })
                    policyData == null -> {
                        SkeletonBlock(40.dp)
                        Spacer(Modifier.height(8.dp))
                        SkeletonBlock(40.dp)
                    }
                    policyRow == null -> EmptyState(
                        title = "使用默认风控",
                        message = "还没有自定义限值，当前使用 AION 默认风控。点右上角“调整限值”即可修改。",
                        icon = Icons.Outlined.Shield,
                    )
                    else -> {
                        val p = policyRow
                        KeyValueRow("策略名称", p.name.ifBlank { "—" }, mono = false)
                        KeyValueRow("最大杠杆", p.maxLeverage?.let { "${it}x" } ?: "—")
                        KeyValueRow("单笔名义上限", fmtNum(p.maxPositionNotionalUsd, 2))
                        KeyValueRow("总敞口上限", fmtNum(p.maxTotalExposureUsd, 2))
                        KeyValueRow("单笔最大亏损", fmtNum(p.maxSingleTradeLossUsd, 2))
                        KeyValueRow("日亏损上限", fmtNum(p.dailyLossLimitUsd, 2))
                        KeyValueRow("最大回撤", fmtPct(p.maxDrawdownPct))
                        KeyValueRow("连续亏损上限", p.maxConsecutiveLosses?.toString() ?: "—")
                        KeyValueRow("最大持仓数", p.maxOpenPositions?.toString() ?: "—")
                        KeyValueRow("方向集中度上限", fmtPct(p.maxDirectionalConcentrationPct))
                        KeyValueRow("最小保证金率", fmtPct(p.minMarginRatio))
                        KeyValueRow("最大点差", p.maxSpreadBps?.let { "$it bps" } ?: "—")
                        KeyValueRow("最小置信度", p.minConfidence ?: "—")
                        KeyValueRow(
                            "强制止损",
                            if (p.requiredStopLoss) "必须设置" else "可选",
                            valueColor = if (p.requiredStopLoss) AionColors.Amber else AionColors.Text,
                            mono = false,
                        )
                        KeyValueRow("更新时间", formatTime(p.updatedAt))

                        val limits = policyData.absoluteLimits
                        if (limits.isNotEmpty()) {
                            Spacer(Modifier.height(8.dp))
                            HorizontalDivider(color = AionColors.Border.copy(alpha = 0.6f))
                            Spacer(Modifier.height(8.dp))
                            LabelText("服务端绝对上限（不可由客户端突破）")
                            Spacer(Modifier.height(4.dp))
                            limits.forEach { (key, value) ->
                                KeyValueRow(key, jsonValueText(value))
                            }
                        }
                    }
                }
            }
        }

        item {
            AionCard {
                SectionHeader(
                    title = "紧急控制",
                    subtitle = "全部操作需要二次确认",
                )
                Spacer(Modifier.height(8.dp))
                ControlSwitch(
                    title = "停止新开仓",
                    description = "拦截所有新交易意图，保护现有仓位",
                    checked = controls?.stopNewTrades == true,
                    enabled = !controlBusy,
                    color = AionColors.Amber,
                    onChange = { pendingControl = PendingControl("STOP_NEW_TRADES", it, "停止新开仓") },
                )
                ControlSwitch(
                    title = "暂停 AI 决策",
                    description = "Agent 不再产生新的交易意图",
                    checked = controls?.pauseAi == true,
                    enabled = !controlBusy,
                    color = AionColors.Amber,
                    onChange = { pendingControl = PendingControl("PAUSE_AI", it, "暂停 AI 决策") },
                )
                ControlSwitch(
                    title = "紧急停止",
                    description = "最高级别熔断，立即冻结交易链路",
                    checked = controls?.emergencyStop == true,
                    enabled = !controlBusy,
                    color = AionColors.Red,
                    onChange = { pendingControl = PendingControl("EMERGENCY_STOP", it, "紧急停止") },
                )
                if (controls?.emergencyReason != null) {
                    Spacer(Modifier.height(8.dp))
                    InfoBanner("当前紧急原因：${controls.emergencyReason}", AionColors.Red)
                }
                Spacer(Modifier.height(8.dp))
                Text(
                    "紧急指令会立即生效，并写入安全审计记录。",
                    style = MaterialTheme.typography.labelSmall,
                    color = AionColors.Muted,
                )
            }
        }

        item {
            AionCard {
                SectionHeader(title = "风控事件", subtitle = "来源 /risk/events?limit=50")
                Spacer(Modifier.height(10.dp))
                when {
                    eventsState.data == null && eventsState.error != null -> ErrorBanner(eventsState.error ?: "", { vm.refresh() })
                    eventsState.data == null -> SkeletonBlock(56.dp)
                    eventsState.data?.items.isNullOrEmpty() -> EmptyState(
                        title = "暂无风控事件",
                        message = "暂时没有风控事件记录。",
                        icon = Icons.Outlined.Shield,
                    )
                    else -> eventsState.data?.items.orEmpty().forEachIndexed { index, event ->
                        if (index > 0) {
                            HorizontalDivider(
                                color = AionColors.Border.copy(alpha = 0.5f),
                                modifier = Modifier.padding(vertical = 2.dp),
                            )
                        }
                        RiskEventRow(event)
                    }
                }
            }
        }

        item {
            AionCard {
                SectionHeader(title = "最近风控决策", subtitle = "来源 /risk/decisions?limit=30")
                Spacer(Modifier.height(10.dp))
                when {
                    decisionsState.data == null && decisionsState.error != null ->
                        ErrorBanner(decisionsState.error ?: "", { vm.refresh() })
                    decisionsState.data == null -> SkeletonBlock(56.dp)
                    decisionsState.data?.items.isNullOrEmpty() -> EmptyState(
                        title = "暂无风控决策",
                        message = "还没有交易意图经过风控引擎。",
                        icon = Icons.Outlined.Shield,
                    )
                    else -> decisionsState.data?.items.orEmpty().forEachIndexed { index, decision ->
                        if (index > 0) {
                            HorizontalDivider(
                                color = AionColors.Border.copy(alpha = 0.5f),
                                modifier = Modifier.padding(vertical = 2.dp),
                            )
                        }
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(modifier = Modifier.weight(1f)) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text(
                                        decision.symbol.ifBlank { "—" },
                                        style = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace),
                                    )
                                    Spacer(Modifier.width(6.dp))
                                    Pill(
                                        if (decision.approved) "APPROVED" else "REJECTED",
                                        if (decision.approved) AionColors.Green else AionColors.Red,
                                    )
                                }
                                Text(
                                    decision.reasonCodes.joinToString(", ").ifBlank { "无原因码" },
                                    style = MaterialTheme.typography.labelSmall,
                                    color = AionColors.Muted,
                                    maxLines = 2,
                                )
                            }
                            Num(
                                formatTime(decision.decidedAt),
                                style = MaterialTheme.typography.labelSmall,
                                color = AionColors.Muted,
                            )
                        }
                    }
                }
            }
        }

        item { Spacer(Modifier.height(4.dp)) }
    }
}

private fun jsonValueText(value: JsonElement): String =
    if (value is JsonPrimitive) value.content else value.toString()

@Composable
private fun ControlSwitch(
    title: String,
    description: String,
    checked: Boolean,
    enabled: Boolean,
    color: androidx.compose.ui.graphics.Color,
    onChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(title, style = MaterialTheme.typography.bodyLarge, color = AionColors.Text)
                Spacer(Modifier.width(6.dp))
                if (checked) {
                    Pill("ON", color, filled = true)
                }
            }
            Text(
                description,
                style = MaterialTheme.typography.labelSmall,
                color = AionColors.Muted,
                modifier = Modifier.padding(top = 2.dp),
            )
        }
        Switch(
            checked = checked,
            onCheckedChange = onChange,
            enabled = enabled,
            colors = SwitchDefaults.colors(
                checkedThumbColor = color,
                checkedTrackColor = color.copy(alpha = 0.35f),
                uncheckedThumbColor = AionColors.Muted,
                uncheckedTrackColor = AionColors.CardHigh,
                uncheckedBorderColor = AionColors.Border,
            ),
        )
    }
}

@Composable
private fun RiskEventRow(event: RiskEventDto) {
    val color = when (event.severity.uppercase()) {
        "CRITICAL", "ERROR", "FATAL" -> AionColors.Red
        "WARNING", "WARN" -> AionColors.Amber
        else -> AionColors.Muted
    }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Pill(event.severity, color, filled = true)
                Spacer(Modifier.width(6.dp))
                Text(
                    event.code,
                    style = MaterialTheme.typography.labelMedium.copy(fontFamily = FontFamily.Monospace),
                    color = AionColors.Text,
                    maxLines = 1,
                )
                event.symbol?.let {
                    Spacer(Modifier.width(6.dp))
                    Pill(it, AionColors.Primary)
                }
            }
            Spacer(Modifier.height(4.dp))
            Text(event.message, style = MaterialTheme.typography.bodySmall, color = AionColors.Text)
        }
        Num(
            formatTime(event.createdAt),
            style = MaterialTheme.typography.labelSmall,
            color = AionColors.Muted,
            modifier = Modifier.padding(start = 8.dp),
        )
    }
}

@Composable
private fun PolicyEditDialog(
    policy: RiskPolicyDto?,
    saving: Boolean,
    error: String?,
    onDismiss: () -> Unit,
    onSave: (Map<String, JsonElement>) -> Unit,
) {
    var maxLeverage by remember { mutableStateOf(policy?.maxLeverage?.toString() ?: "") }
    var maxNotional by remember { mutableStateOf(policy?.maxPositionNotionalUsd ?: "") }
    var dailyLoss by remember { mutableStateOf(policy?.dailyLossLimitUsd ?: "") }
    var localError by remember { mutableStateOf<String?>(null) }

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = AionColors.CardHigh,
        shape = RoundedCornerShape(20.dp),
        title = { Text("调整风控限值", style = MaterialTheme.typography.titleMedium) },
        text = {
            Column(
                modifier = Modifier
                    .heightIn(max = 420.dp)
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text(
                    "只能收紧或调整允许范围内的限值；超出服务端硬上限会返回 422，错误信息将原样展示。",
                    style = MaterialTheme.typography.bodySmall,
                    color = AionColors.Muted,
                )
                AionField(
                    value = maxLeverage,
                    onValueChange = { maxLeverage = it.filter { ch -> ch.isDigit() } },
                    label = "最大杠杆（x）",
                    mono = true,
                    keyboardType = KeyboardType.Number,
                )
                AionField(
                    value = maxNotional,
                    onValueChange = { maxNotional = it.filter { ch -> ch.isDigit() || ch == '.' } },
                    label = "单笔名义上限（USD）",
                    mono = true,
                    keyboardType = KeyboardType.Decimal,
                )
                AionField(
                    value = dailyLoss,
                    onValueChange = { dailyLoss = it.filter { ch -> ch.isDigit() || ch == '.' || ch == '-' } },
                    label = "日亏损上限（USD）",
                    mono = true,
                    keyboardType = KeyboardType.Decimal,
                )
                val combinedError = localError ?: error
                if (combinedError != null) {
                    InfoBanner(combinedError, AionColors.Red)
                }
                Text(
                    "仅提交已填写的字段，其余保持服务端现值。",
                    style = MaterialTheme.typography.labelSmall,
                    color = AionColors.Muted,
                )
            }
        },
        confirmButton = {
            TextButton(
                enabled = !saving,
                onClick = {
                    val fields = buildMap<String, JsonElement> {
                        maxLeverage.toIntOrNull()?.let { put("max_leverage", JsonPrimitive(it)) }
                        maxNotional.toBigDecimalOrNull()?.let {
                            put("max_position_notional_usd", JsonPrimitive(it.toPlainString()))
                        }
                        dailyLoss.toBigDecimalOrNull()?.let {
                            put("daily_loss_limit_usd", JsonPrimitive(it.toPlainString()))
                        }
                    }
                    if (fields.isEmpty()) {
                        localError = "请至少填写一项限值"
                    } else {
                        localError = null
                        onSave(fields)
                    }
                },
            ) {
                Text(if (saving) "保存中…" else "保存", color = if (saving) AionColors.Muted else AionColors.Primary)
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("取消", color = AionColors.Muted) }
        },
    )
}
