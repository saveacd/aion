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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewModelScope
import dev.aion.app.AppContainer
import dev.aion.app.data.ApiResult
import dev.aion.app.data.EvaluationDto
import dev.aion.app.data.FactorLibraryResponse
import dev.aion.app.data.FactorIntelligenceDto
import dev.aion.app.data.FactorLifecycleReportDto
import dev.aion.app.data.FactorProposalDto
import dev.aion.app.data.FactorResearchReport
import dev.aion.app.data.Loadable
import dev.aion.app.ui.BaseViewModel
import dev.aion.app.ui.GlobalRefreshEffect
import dev.aion.app.ui.aionViewModel
import dev.aion.app.ui.components.AionCard
import dev.aion.app.ui.components.AionField
import dev.aion.app.ui.components.ErrorBanner
import dev.aion.app.ui.components.KeyValueRow
import dev.aion.app.ui.components.Pill
import dev.aion.app.ui.components.PillTabs
import dev.aion.app.ui.components.SectionHeader
import dev.aion.app.ui.components.SkeletonBlock
import dev.aion.app.ui.fmtNum
import dev.aion.app.ui.formatTime
import dev.aion.app.ui.theme.AionColors
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

class FactorResearchViewModel(container: AppContainer) : BaseViewModel(container) {

    val symbol = MutableStateFlow("BTCUSDT")
    val intervalIndex = MutableStateFlow(1)
    val horizon = MutableStateFlow("8")
    val report = MutableStateFlow(Loadable<FactorResearchReport>())
    val library = MutableStateFlow(Loadable<FactorLibraryResponse>())
    val intelligence = MutableStateFlow(Loadable<FactorIntelligenceDto>())
    val lifecycleReport = MutableStateFlow<FactorLifecycleReportDto?>(null)
    val lifecycleRunning = MutableStateFlow(false)
    val lifecycleError = MutableStateFlow<String?>(null)
    val running = MutableStateFlow(false)
    val triggerError = MutableStateFlow<String?>(null)

    val intervals = listOf("5m", "15m", "1h", "4h")

    init {
        loadLibrary()
        loadIntelligence()
    }

    fun refresh() {
        loadLibrary()
        loadIntelligence()
    }

    fun updateSymbol(value: String) {
        symbol.value = value.uppercase()
    }

    fun updateInterval(index: Int) {
        intervalIndex.value = index
        loadIntelligence()
    }

    fun updateHorizon(value: String) {
        horizon.value = value
    }

    fun trigger() {
        if (running.value) return
        val target = symbol.value.trim().uppercase()
        if (target.length < 3) {
            triggerError.value = "请输入交易对，例如 BTCUSDT"
            return
        }
        val bars = horizon.value.trim().toIntOrNull()
        if (bars == null || bars <= 0) {
            triggerError.value = "预测窗口需为正整数（K 线根数）"
            return
        }
        triggerError.value = null
        running.value = true
        viewModelScope.launch {
            try {
                when (val result = container.repository.factorResearch(
                    target,
                    intervals[intervalIndex.value],
                    bars,
                )) {
                    is ApiResult.Failure -> triggerError.value = result.userMessage()
                    is ApiResult.Success -> {
                        report.value = Loadable(data = result.data)
                        loadLibrary()
                        loadIntelligence()
                    }
                }
            } finally {
                running.value = false
            }
        }
    }

    fun refreshLifecycle() {
        if (lifecycleRunning.value) return
        lifecycleRunning.value = true
        lifecycleError.value = null
        viewModelScope.launch {
            try {
                when (val result = container.repository.refreshFactorLifecycle(symbol.value.trim().uppercase())) {
                    is ApiResult.Success -> {
                        lifecycleReport.value = result.data
                        loadLibrary()
                        loadIntelligence()
                    }
                    is ApiResult.Failure -> lifecycleError.value = result.userMessage()
                }
            } finally {
                lifecycleRunning.value = false
            }
        }
    }

    private fun loadLibrary() {
        load(library) { container.repository.factorLibrary(symbol.value.trim().uppercase()) }
    }

    private fun loadIntelligence() {
        load(intelligence) {
            container.repository.factorIntelligence(
                symbol.value.trim().uppercase(), intervals[intervalIndex.value],
            )
        }
    }
}

@Composable
fun FactorResearchScreen(container: AppContainer) {
    val vm: FactorResearchViewModel = aionViewModel(container) { FactorResearchViewModel(it) }
    val symbol by vm.symbol.collectAsState()
    val intervalIndex by vm.intervalIndex.collectAsState()
    val horizon by vm.horizon.collectAsState()
    val report by vm.report.collectAsState()
    val library by vm.library.collectAsState()
    val intelligence by vm.intelligence.collectAsState()
    val lifecycleReport by vm.lifecycleReport.collectAsState()
    val lifecycleRunning by vm.lifecycleRunning.collectAsState()
    val lifecycleError by vm.lifecycleError.collectAsState()
    val running by vm.running.collectAsState()
    val triggerError by vm.triggerError.collectAsState()

    GlobalRefreshEffect(container) { vm.refresh() }

    val items = library.data?.items.orEmpty()

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .background(AionColors.Bg),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            AionCard {
                SectionHeader(
                    title = "因子研究",
                    subtitle = "多 Agent 提出候选因子并回测评估，通过的进入因子库",
                )
                Spacer(Modifier.height(10.dp))
                AionField(
                    value = symbol,
                    onValueChange = { vm.updateSymbol(it) },
                    label = "交易对",
                    placeholder = "BTCUSDT",
                    mono = true,
                    enabled = !running,
                )
                Spacer(Modifier.height(10.dp))
                PillTabs(
                    labels = vm.intervals,
                    selected = intervalIndex,
                    onSelect = { vm.updateInterval(it) },
                )
                Spacer(Modifier.height(10.dp))
                AionField(
                    value = horizon,
                    onValueChange = { vm.updateHorizon(it) },
                    label = "预测窗口（K 线根数）",
                    placeholder = "8",
                    keyboardType = KeyboardType.Number,
                    enabled = !running,
                )
                Spacer(Modifier.height(10.dp))
                if (triggerError != null) {
                    Text(
                        triggerError ?: "",
                        style = MaterialTheme.typography.bodySmall,
                        color = AionColors.Red,
                    )
                    Spacer(Modifier.height(8.dp))
                }
                Button(
                    onClick = { vm.trigger() },
                    enabled = !running,
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(48.dp),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = AionColors.Primary,
                        contentColor = AionColors.Bg,
                    ),
                ) {
                    if (running) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            color = AionColors.Bg,
                            strokeWidth = 2.dp,
                        )
                        Spacer(Modifier.width(8.dp))
                    }
                    Text(if (running) "研究运行中…" else "运行因子研究")
                }
            }
        }

        item {
            AionCard {
                SectionHeader(
                    title = "因子实时情报",
                    subtitle = "读取已收盘 K 线与当前用户 ACTIVE 因子 · ${symbol} / ${vm.intervals[intervalIndex]}",
                    action = { Button(onClick = { vm.refresh() }) { Text("刷新") } },
                )
                Spacer(Modifier.height(10.dp))
                when {
                    intelligence.data == null && intelligence.error != null ->
                        ErrorBanner(intelligence.error ?: "", { vm.refresh() })
                    intelligence.data == null -> SkeletonBlock(88.dp)
                    else -> {
                        val intel = intelligence.data!!
                        val signals = intel.factorSignals
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            AionCard(modifier = Modifier.weight(1f), padding = PaddingValues(10.dp)) {
                                Text("市场状态", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                                Text(regimeLabel(intel.marketRegime.label), style = MaterialTheme.typography.titleMedium, color = AionColors.Primary)
                                Text("置信度 ${fmtNum(intel.marketRegime.confidence.toString(), 2)}", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                            }
                            AionCard(modifier = Modifier.weight(1f), padding = PaddingValues(10.dp)) {
                                Text("组合信号", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                                Text(signals.compositeSignal?.let { fmtNum(it.toString(), 3) } ?: "—", style = MaterialTheme.typography.titleMedium, color = signalColor(signals.bias))
                                Text(biasLabel(signals.bias), style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                            }
                            AionCard(modifier = Modifier.weight(1f), padding = PaddingValues(10.dp)) {
                                Text("有效因子", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                                Text("${signals.factorCount}", style = MaterialTheme.typography.titleMedium, color = AionColors.Text)
                                Text("${intel.candlesUsed} 根 K线", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                            }
                        }
                        Spacer(Modifier.height(8.dp))
                        if (signals.factors.isEmpty()) {
                            Text("当前交易对和周期没有可计算的 ACTIVE 因子信号。先运行研究并通过确定性评估。", style = MaterialTheme.typography.bodySmall, color = AionColors.Muted)
                        } else {
                            Text("参与组合的因子", style = MaterialTheme.typography.titleSmall)
                            signals.factors.forEach { f ->
                                Column(Modifier.padding(vertical = 6.dp)) {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Text(f.factorName, style = MaterialTheme.typography.bodyMedium)
                                        Spacer(Modifier.weight(1f))
                                        Pill("信号 ${fmtNum(f.normalizedSignal?.toString(), 3)}", signalColor(if ((f.normalizedSignal ?: 0.0) > 0.08) "BULLISH" else if ((f.normalizedSignal ?: 0.0) < -0.08) "BEARISH" else "NEUTRAL"))
                                    }
                                    Text("${f.formula} · 权重 ${fmtNum(f.weight?.toString(), 3)} · Regime ×${fmtNum(f.regimeMultiplier?.toString(), 2)}", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                                }
                                HorizontalDivider(color = AionColors.Border.copy(alpha = 0.4f))
                            }
                        }
                        Text(signals.policy, style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                    }
                }
            }
        }

        item {
            AionCard {
                SectionHeader(title = "策略影子进化", subtitle = "按市场状态生成研究候选，不修改实盘策略")
                Spacer(Modifier.height(8.dp))
                val evolution = intelligence.data?.strategyEvolution
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Pill("SHADOW RESEARCH ONLY", AionColors.Amber)
                    Spacer(Modifier.weight(1f))
                    Text("自动改参数：否 · 自动下单：否", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                }
                Spacer(Modifier.height(8.dp))
                if (evolution == null) {
                    Text("等待因子情报数据。", style = MaterialTheme.typography.bodySmall, color = AionColors.Muted)
                } else {
                    evolution.suggestions.forEach { suggestion ->
                        Text(suggestion.type, style = MaterialTheme.typography.labelMedium, color = AionColors.Primary)
                        Text(suggestion.recommendation, style = MaterialTheme.typography.bodySmall, color = AionColors.Text, modifier = Modifier.padding(bottom = 8.dp))
                    }
                    evolution.candidateStrategies.forEach { candidate ->
                        Text(candidate.name, style = MaterialTheme.typography.titleSmall)
                        Text("入场确认：${candidate.entryConfirmation} · 必需评估：${candidate.evaluationRequired.joinToString()}", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
                    }
                    if (evolution.suggestions.isEmpty() && evolution.candidateStrategies.isEmpty()) {
                        Text("当前样本不足，暂无策略候选。", style = MaterialTheme.typography.bodySmall, color = AionColors.Muted)
                    }
                }
            }
        }

        item {
            AionCard {
                SectionHeader(title = "公式目录", subtitle = "11 种受限、可审计的确定性公式；LLM 只能从目录提出候选")
                val formulas = listOf(
                    "MOMENTUM" to "价格动量",
                    "MEAN_REVERSION_Z" to "均值回归 Z 分数",
                    "VOLATILITY_RATIO" to "波动率比值",
                    "VOLUME_Z" to "成交量 Z 分数",
                    "RSI_DEVIATION" to "RSI 偏离",
                    "EMA_SLOPE" to "EMA 斜率",
                    "RANGE_POSITION" to "区间位置",
                    "VOL_ADJUSTED_MOMENTUM" to "波动率调整动量",
                    "PRICE_VOLUME_CONFIRMATION" to "价量确认",
                    "TREND_EFFICIENCY" to "趋势效率",
                    "BREAKOUT_PRESSURE" to "突破压力",
                )
                formulas.forEach { (code, label) ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 5.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(label, style = MaterialTheme.typography.bodySmall, color = AionColors.Text, modifier = Modifier.weight(1f))
                        Text(code, style = MaterialTheme.typography.labelSmall, color = AionColors.Muted, fontFamily = FontFamily.Monospace)
                    }
                }
                Text("公式执行由后端确定性引擎完成，不接受 LLM 生成任意 Python / SQL。", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
            }
        }

        item {
            AionCard {
                SectionHeader(
                    title = "因子生命周期",
                    subtitle = "按近期样本外表现复核 ACTIVE / WATCH 状态",
                    action = {
                        Button(onClick = { vm.refreshLifecycle() }, enabled = !lifecycleRunning) {
                            if (lifecycleRunning) CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                            else Text("复核")
                        }
                    },
                )
                Text("复核只更新当前用户的因子状态，不触发下单。建议定期手动复核。", style = MaterialTheme.typography.bodySmall, color = AionColors.Muted)
                lifecycleError?.let { Text(it, color = AionColors.Red, style = MaterialTheme.typography.bodySmall) }
                lifecycleReport?.let { Text("最近一次复核：检查 ${it.checked} 项 · 状态变化 ${it.changed} 项", style = MaterialTheme.typography.labelMedium, color = AionColors.Primary) }
            }
        }

        item {
            AionCard {
                SectionHeader(title = "研究报告", subtitle = "来源 POST /factors/research")
                Spacer(Modifier.height(10.dp))
                when {
                    report.data == null && report.error != null ->
                        ErrorBanner(report.error ?: "", { vm.refresh() })
                    report.data == null ->
                        Text(
                            "尚未运行研究。填写参数后点击上方按钮，评估结果会显示在这里。",
                            style = MaterialTheme.typography.bodySmall,
                            color = AionColors.Muted,
                        )
                    else -> {
                        val r = report.data!!
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(r.symbol, style = MaterialTheme.typography.titleMedium)
                            Spacer(Modifier.width(6.dp))
                            Text(
                                "${r.interval}" + (r.horizonBars?.let { " · 视野 $it 根 K 线" } ?: ""),
                                style = MaterialTheme.typography.labelSmall,
                                color = AionColors.Muted,
                            )
                            Spacer(Modifier.weight(1f))
                            Pill("${r.acceptedCount} 通过", AionColors.Green)
                            Spacer(Modifier.width(6.dp))
                            Pill("${r.rejectedCount} 拒绝", AionColors.Muted)
                        }
                        Text(
                            "运行 ID ${r.runId}",
                            style = MaterialTheme.typography.labelSmall,
                            color = AionColors.Muted,
                            fontFamily = FontFamily.Monospace,
                        )
                        if (r.degraded) {
                            Spacer(Modifier.height(8.dp))
                            Text(
                                "降级运行：${r.reasons.joinToString("；").ifBlank { "数据不完整" }}",
                                style = MaterialTheme.typography.bodySmall,
                                color = AionColors.Amber,
                            )
                        }
                        Spacer(Modifier.height(8.dp))
                        HorizontalDivider(color = AionColors.Border.copy(alpha = 0.4f))
                        Text(
                            "候选因子假设（${r.proposals.size}）",
                            style = MaterialTheme.typography.titleSmall,
                            modifier = Modifier.padding(vertical = 8.dp),
                        )
                        r.proposals.forEach { p ->
                            ProposalRow(p)
                            HorizontalDivider(color = AionColors.Border.copy(alpha = 0.4f))
                        }
                        Text(
                            "回测评估（${r.evaluations.size}）",
                            style = MaterialTheme.typography.titleSmall,
                            modifier = Modifier.padding(vertical = 8.dp),
                        )
                        r.evaluations.forEach { e ->
                            EvaluationRow(e)
                            HorizontalDivider(color = AionColors.Border.copy(alpha = 0.4f))
                        }
                    }
                }
            }
        }

        item {
            AionCard {
                SectionHeader(title = "因子库", subtitle = "来源 /factors/library · 已入库因子一览")
                Spacer(Modifier.height(8.dp))
                when {
                    library.data == null && library.error != null ->
                        ErrorBanner(library.error ?: "", { vm.refresh() })
                    library.data == null -> SkeletonBlock(72.dp)
                    items.isEmpty() -> Text(
                        "因子库为空。运行一次因子研究，通过的因子会自动入库。",
                        style = MaterialTheme.typography.bodySmall,
                        color = AionColors.Muted,
                    )
                    else -> items.forEach { f ->
                        LibraryRow(f)
                        HorizontalDivider(color = AionColors.Border.copy(alpha = 0.4f))
                    }
                }
            }
        }

        item { Spacer(Modifier.height(4.dp)) }
    }
}

@Composable
private fun ProposalRow(p: FactorProposalDto) {
    Column(modifier = Modifier.padding(vertical = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(p.factorName, style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.width(6.dp))
            Pill(p.domain, AionColors.Primary)
            Spacer(Modifier.weight(1f))
            Pill(
                if (p.direction == "NEGATIVE") "反向" else "正向",
                if (p.direction == "NEGATIVE") AionColors.Red else AionColors.Green,
            )
        }
        Spacer(Modifier.height(4.dp))
        Text(p.hypothesis, style = MaterialTheme.typography.bodySmall, color = AionColors.Text)
        Text(
            "Agent ${p.agent} · 回看 ${p.lookbackBars} 根 K 线",
            style = MaterialTheme.typography.labelSmall,
            color = AionColors.Muted,
        )
        Spacer(Modifier.height(6.dp))
    }
}

@Composable
private fun EvaluationRow(e: EvaluationDto) {
    Column(modifier = Modifier.padding(vertical = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(e.factorName, style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.width(6.dp))
            Pill(e.domain, AionColors.Primary)
            Spacer(Modifier.weight(1f))
            Pill(
                if (e.accepted) "通过" else "拒绝",
                if (e.accepted) AionColors.Green else AionColors.Muted,
                filled = true,
            )
        }
        Spacer(Modifier.height(4.dp))
        Text(
            "IC ${fmtNum(e.icMean?.toString(), 4)} · ICIR ${fmtNum(e.icIr?.toString(), 3)} · " +
                "胜率 ${fmtNum(e.hitRate?.toString(), 2)} · 覆盖 ${fmtNum(e.coverage?.toString(), 2)}",
            style = MaterialTheme.typography.labelSmall,
            color = AionColors.Muted,
        )
        if (e.notes.isNotEmpty()) {
            Spacer(Modifier.height(2.dp))
            Text(
                e.notes.joinToString("；"),
                style = MaterialTheme.typography.bodySmall,
                color = AionColors.Muted,
            )
        }
        Spacer(Modifier.height(6.dp))
    }
}

@Composable
private fun LibraryRow(f: dev.aion.app.data.FactorLibraryItemDto) {
    Column(modifier = Modifier.padding(vertical = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(f.factorName, style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.width(6.dp))
            Pill(f.domain, AionColors.Primary)
            Spacer(Modifier.weight(1f))
            Pill(if (f.status == "ACTIVE") "ACTIVE" else f.status.ifBlank { "未知" }, if (f.status == "ACTIVE") AionColors.Green else AionColors.Amber)
        }
        Spacer(Modifier.height(4.dp))
        Text(
            "${f.symbol} · ${f.interval} · ${f.direction} · ${f.formula} · 回看 ${f.lookbackBars} 根 · Agent ${f.agent}",
            style = MaterialTheme.typography.labelSmall,
            color = AionColors.Muted,
        )
        Text(
            "IC ${fmtNum(f.icMean?.toString(), 4)} · ICIR ${fmtNum(f.icIr?.toString(), 3)} · " +
                "胜率 ${fmtNum(f.hitRate?.toString(), 2)} · ${formatTime(f.createdAt)}",
            style = MaterialTheme.typography.labelSmall,
            color = AionColors.Muted,
        )
        Spacer(Modifier.height(4.dp))
        KeyValueRow("状态", f.status)
        Spacer(Modifier.height(2.dp))
        Text(
            f.hypothesis,
            style = MaterialTheme.typography.bodySmall,
            color = AionColors.Text,
        )
        Spacer(Modifier.height(6.dp))
    }
}


private fun regimeLabel(value: String): String = when (value) {
    "TRENDING" -> "趋势"
    "RANGING" -> "震荡"
    "HIGH_VOLATILITY" -> "高波动"
    else -> "未知"
}

private fun biasLabel(value: String): String = when (value) {
    "BULLISH" -> "偏多"
    "BEARISH" -> "偏空"
    "NEUTRAL" -> "中性"
    else -> "无有效信号"
}

private fun signalColor(value: String) = when (value) {
    "BULLISH" -> AionColors.Green
    "BEARISH" -> AionColors.Red
    "NEUTRAL" -> AionColors.Amber
    else -> AionColors.Muted
}
