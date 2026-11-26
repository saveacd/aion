package dev.aion.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import dev.aion.app.AppContainer
import dev.aion.app.ui.components.AionCard
import dev.aion.app.ui.theme.AionColors
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.JsonPrimitive

@Composable
fun ResearchLabScreen(container: AppContainer) {
    val scope = rememberCoroutineScope()
    var tasks by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var candidates by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var comparison by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var sessions by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var taskLogs by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var ensembles by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var approvals by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var reviewQueue by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var isAdmin by remember { mutableStateOf(false) }
    var approvalTarget by remember { mutableStateOf<JsonObject?>(null) }
    var factors by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var factorValidations by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var candidateBacktests by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var paperTrades by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var symbol by remember { mutableStateOf("BTCUSDT") }
    var interval by remember { mutableStateOf("15m") }
    var horizon by remember { mutableStateOf("8") }
    var selectedTask by remember { mutableStateOf<String?>(null) }
    var experiment by remember { mutableStateOf<JsonObject?>(null) }
    var experimentError by remember { mutableStateOf<String?>(null) }
    var replayMessage by remember { mutableStateOf<String?>(null) }
    var replaying by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    fun refresh() {
        scope.launch {
            loading = true
            error = null
            try {
                val api = container.api.api()
                val me = api.me()
                isAdmin = me.isAdmin
                tasks = api.researchTasks().mapNotNull { it as? JsonObject }
                candidates = api.researchCandidates().mapNotNull { it as? JsonObject }
                comparison = api.researchCompare().mapNotNull { it as? JsonObject }
                sessions = api.paperSessions().mapNotNull { it as? JsonObject }
                ensembles = api.researchEnsembles(symbol).mapNotNull { it as? JsonObject }
                approvals = api.researchApprovals().mapNotNull { it as? JsonObject }
                reviewQueue = if (isAdmin) {
                    api.strategyReviewQueue().items.filter { it.state == "SHADOW" && it.createdBy != me.username }.flatMap { strategy ->
                        val strategyId = strategy.id ?: return@flatMap emptyList()
                        api.strategyVersions(strategyId).mapNotNull { element ->
                            val version = element as? JsonObject ?: return@mapNotNull null
                            if (version["is_approved"]?.toString() == "true") return@mapNotNull null
                            JsonObject(mapOf(
                                "strategy_name" to JsonPrimitive(strategy.name),
                                "strategy_id" to JsonPrimitive(strategyId),
                                "version_id" to (version["id"] ?: JsonPrimitive("")),
                                "version" to (version["version"] ?: JsonPrimitive("?")),
                                "change_note" to (version["change_note"] ?: JsonPrimitive("无版本说明")),
                            ))
                        }
                    }
                } else emptyList()
                factors = api.factorLibrary(symbol).items.map { item ->
                    JsonObject(mapOf(
                        "id" to JsonPrimitive(item.id),
                        "factor_name" to JsonPrimitive(item.factorName),
                        "status" to JsonPrimitive(item.status),
                        "formula" to JsonPrimitive(item.formula),
                        "ic_mean" to (item.icMean?.let { JsonPrimitive(it) } ?: JsonPrimitive("—")),
                        "ic_ir" to (item.icIr?.let { JsonPrimitive(it) } ?: JsonPrimitive("—")),
                    ))
                }
                val latestTaskId = tasks.firstOrNull()?.get("id")?.toString()?.trim('"')
                taskLogs = if (!latestTaskId.isNullOrBlank()) {
                    api.researchTaskLogs(latestTaskId).mapNotNull { it as? JsonObject }
                } else emptyList()
                val firstFactorId = factors.firstOrNull()?.get("id")?.toString()?.trim('"')
                factorValidations = if (!firstFactorId.isNullOrBlank()) {
                    api.factorValidations(firstFactorId).mapNotNull { it as? JsonObject }
                } else emptyList()
                val firstCandidateId = candidates.firstOrNull()?.get("id")?.toString()?.trim('"')
                candidateBacktests = if (!firstCandidateId.isNullOrBlank()) {
                    api.candidateBacktests(firstCandidateId).mapNotNull { it as? JsonObject }
                } else emptyList()
                paperTrades = sessions.take(5).flatMap { session ->
                    val sessionId = session["id"]?.toString()?.trim('"')
                    if (sessionId.isNullOrBlank()) emptyList() else {
                        runCatching { api.paperTrades(sessionId) }.getOrDefault(emptyList())
                            .mapNotNull { it as? JsonObject }
                            .map { trade -> JsonObject(trade.toMutableMap().apply {
                                put("session_name", session["name"] ?: JsonPrimitive("PAPER"))
                            }) }
                    }
                }
            } catch (e: Exception) {
                error = e.message ?: "网络请求失败"
            } finally {
                loading = false
            }
        }
    }
    LaunchedEffect(Unit) { refresh() }

    LaunchedEffect(selectedTask) {
        experiment = null
        experimentError = null
        replayMessage = null
        val id = selectedTask ?: return@LaunchedEffect
        try {
            experiment = container.api.api().researchTask(id)
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            experimentError = e.message ?: "实验读取失败"
        }
    }

    approvalTarget?.let { target ->
        AlertDialog(
            onDismissRequest = { approvalTarget = null },
            title = { Text("确认独立审批") },
            text = { Text("确认审批 ${target["strategy_name"]} · v${target["version"]}？后端将再次检查策略所有者风险政策和验证证据。") },
            confirmButton = {
                TextButton(onClick = {
                    val selected = target
                    approvalTarget = null
                    scope.launch {
                        try {
                            val strategyId = selected["strategy_id"]?.toString()?.trim('"') ?: return@launch
                            val versionId = selected["version_id"]?.toString()?.trim('"') ?: return@launch
                            container.api.api().transitionStrategyState(strategyId, JsonObject(mapOf(
                                "state" to JsonPrimitive("APPROVED"),
                                "version_id" to JsonPrimitive(versionId),
                                "note" to JsonPrimitive("Independent administrator review via Android"),
                            )))
                            refresh()
                        } catch (e: Exception) { error = e.message ?: "审批未通过" }
                    }
                }) { Text("确认审批") }
            },
            dismissButton = { TextButton(onClick = { approvalTarget = null }) { Text("取消") } },
        )
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize().background(AionColors.Bg),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            AionCard {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("自主研究实验室", style = MaterialTheme.typography.titleLarge, color = AionColors.Text)
                    Text("后台研究任务 · 因子验证 · 策略候选 · PAPER 观察", color = AionColors.Muted)
                    OutlinedTextField(value = symbol, onValueChange = { symbol = it.uppercase() }, label = { Text("交易对") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
                    OutlinedTextField(value = interval, onValueChange = { interval = it }, label = { Text("K线周期") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
                    OutlinedTextField(value = horizon, onValueChange = { horizon = it.filter(Char::isDigit).take(3) },
                        label = { Text("预测长度（K 线根数，1–100）") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = {
                            scope.launch {
                                try {
                                    container.api.api().enqueueResearchTask(JsonObject(mapOf(
                                        "symbol" to JsonPrimitive(symbol),
                                        "interval" to JsonPrimitive(interval),
                                        "horizon_bars" to JsonPrimitive(horizon.toIntOrNull() ?: 8),
                                    )))
                                    refresh()
                                } catch (e: Exception) { error = e.message ?: "任务提交失败" }
                            }
                        }, enabled = !loading && symbol.length >= 3 && (horizon.toIntOrNull() ?: 0) in 1..100) { Text("提交研究任务") }
                        Button(onClick = { refresh() }, enabled = !loading) { Text("刷新") }
                    }
                    Text("研究模块仅生成研究记录，不具备交易所下单权限。", color = AionColors.Muted, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        if (loading) item { CircularProgressIndicator() }
        error?.let { message -> item { Text("请求失败：$message", color = AionColors.Red) } }
        item { Text("后台任务", style = MaterialTheme.typography.titleMedium, color = AionColors.Text) }
        if (tasks.isEmpty()) item { Text("暂无研究任务", color = AionColors.Muted) }
        items(tasks) { row ->
            AionCard {
                Column {
                    Text("${row["symbol"]?.toString()?.trim('"') ?: "—"} · ${row["interval"]?.toString()?.trim('"') ?: "—"}", color = AionColors.Text)
                    Text("状态：${row["status"]?.toString()?.trim('"') ?: "—"}", color = AionColors.Primary)
                    Text("创建：${row["created_at"]?.toString()?.trim('"') ?: "—"}", color = AionColors.Muted, style = MaterialTheme.typography.bodySmall)
                    val taskId = row.value("id")
                    if (taskId != "—") {
                        TextButton(onClick = {
                            selectedTask = if (selectedTask == taskId) null else taskId
                        }, enabled = !replaying) {
                            Text(if (selectedTask == taskId) "收起验证详情" else "查看验证详情")
                        }
                        if (selectedTask == taskId) {
                            experimentError?.let { Text(it, color = AionColors.Red) }
                            val result = experiment?.get("result") as? JsonObject
                            val discovery = result?.get("discovery") as? JsonObject
                            val data = result?.get("dataset") as? JsonObject
                            if (result != null && discovery != null && data != null) {
                                ExperimentDetails(discovery, data, result)
                                Button(onClick = {
                                    replaying = true
                                    scope.launch {
                                        try {
                                            val replay = container.api.api().replayResearch(taskId)
                                            replayMessage = if (replay.value("match") == "true")
                                                "与归档一致 · 已匹配 ${replay.value("matched_validation_runs")} 项验证"
                                            else "复算结果与归档不同"
                                        } catch (e: kotlinx.coroutines.CancellationException) {
                                            throw e
                                        } catch (e: Exception) {
                                            replayMessage = e.message ?: "实验复算失败"
                                        } finally {
                                            replaying = false
                                        }
                                    }
                                }, enabled = !replaying) { Text(if (replaying) "复算中…" else "复算归档实验") }
                                replayMessage?.let { Text(it, color = AionColors.Muted) }
                            } else if (experiment != null) {
                                Text("任务暂无归档的独立验证结果", color = AionColors.Muted)
                            } else if (experimentError == null) {
                                CircularProgressIndicator()
                            }
                        }
                    }
                    row["error"]?.let { Text(it.toString(), color = AionColors.Red) }
                }
            }
        }
        item { Text("研究日志（最近任务）", style = MaterialTheme.typography.titleMedium, color = AionColors.Text) }
        if (taskLogs.isEmpty()) item { Text("暂无任务日志", color = AionColors.Muted) }
        items(taskLogs) { row ->
            AionCard { Column {
                Text(row["event"]?.toString()?.trim('"') ?: "EVENT", color = AionColors.Primary)
                Text(row["message"]?.toString()?.trim('"') ?: "—", color = AionColors.Text)
                Text(row["created_at"]?.toString()?.trim('"') ?: "", color = AionColors.Muted, style = MaterialTheme.typography.bodySmall)
            } }
        }
        item { Text("因子智能中心", style = MaterialTheme.typography.titleMedium, color = AionColors.Text) }
        if (factors.isEmpty()) item { Text("暂无因子记录", color = AionColors.Muted) }
        items(factors) { row ->
            AionCard { Column {
                Text(row["factor_name"]?.toString()?.trim('"') ?: "Factor", color = AionColors.Text)
                Text("${row["status"] ?: "—"} · ${row["formula"] ?: "—"}", color = AionColors.Muted)
                Text("IC ${row["ic_mean"] ?: "—"} · ICIR ${row["ic_ir"] ?: "—"}", color = AionColors.Primary)
            } }
        }
        item { Text("因子验证报告（最近因子）", style = MaterialTheme.typography.titleMedium, color = AionColors.Text) }
        if (factorValidations.isEmpty()) item { Text("暂无独立验证记录", color = AionColors.Muted) }
        items(factorValidations) { row ->
            AionCard { Column {
                Text(if (row["passed"]?.toString() == "true") "验证通过" else "未通过 / 待复核", color = AionColors.Primary)
                Text("指标：${row["metrics"] ?: "—"}", color = AionColors.Text, style = MaterialTheme.typography.bodySmall)
                Text("警告：${row["warnings"] ?: "—"}", color = AionColors.Muted, style = MaterialTheme.typography.bodySmall)
            } }
        }
        item { Text("动态因子组合", style = MaterialTheme.typography.titleMedium, color = AionColors.Text) }
        if (ensembles.isEmpty()) item { Text("暂无组合快照", color = AionColors.Muted) }
        items(ensembles) { row ->
            AionCard { Column {
                Text("${row["symbol"] ?: "—"} · ${row["regime"] ?: "UNKNOWN"}", color = AionColors.Text)
                Text("权重：${row["weights"] ?: "—"}", color = AionColors.Muted)
            } }
        }
        item { Text("策略横向比较", style = MaterialTheme.typography.titleMedium, color = AionColors.Text) }
        if (comparison.isEmpty()) item { Text("暂无可比较策略", color = AionColors.Muted) }
        items(comparison) { row ->
            AionCard { Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("${row["name"]?.toString()?.trim('"')} · ${row["strategy_kind"] ?: "unknown"}", color = AionColors.Text)
                Text("${row["gate"] ?: "RESEARCH_ONLY"} · 评分 ${row["score"] ?: "—"}", color = AionColors.Primary)
                val oos = row["out_of_sample"] as? JsonObject
                Text("OOS 收益 ${oos?.get("total_return") ?: "—"} · 夏普 ${oos?.get("sharpe") ?: "—"} · 回撤 ${oos?.get("max_drawdown_pct") ?: "—"}% · 交易 ${oos?.get("trade_count") ?: "0"} 笔", color = AionColors.Muted, style = MaterialTheme.typography.bodySmall)
                val paper = row["paper"] as? JsonObject
                val paperMetrics = paper?.get("metrics") as? JsonObject
                Text(if (paper == null) "尚未进入 PAPER 观察" else "PAPER ${paper["status"]} · 权益 ${paperMetrics?.get("equity") ?: paper["current_cash"] ?: "—"} · 回撤 ${paperMetrics?.get("max_drawdown_pct") ?: "—"}%", color = AionColors.Muted, style = MaterialTheme.typography.bodySmall)
            } }
        }
        item { Text("策略候选", style = MaterialTheme.typography.titleMedium, color = AionColors.Text) }
        if (candidates.isEmpty()) item { Text("暂无候选策略", color = AionColors.Muted) }
        items(candidates) { row ->
            AionCard { Column {
                Text(row["name"]?.toString()?.trim('"') ?: "未命名候选", color = AionColors.Text)
                Text("状态：${row["status"]?.toString()?.trim('"') ?: "—"}", color = AionColors.Muted)
                Text("验证：${row["evaluation"]?.toString()?.take(240) ?: "待验证"}", color = AionColors.Primary, style = MaterialTheme.typography.bodySmall)
            } }
        }
        item { Text("候选回测记录（最近候选）", style = MaterialTheme.typography.titleMedium, color = AionColors.Text) }
        if (candidateBacktests.isEmpty()) item { Text("暂无已完成回测；被证据门槛阻止的任务会显示在候选评估中", color = AionColors.Muted) }
        items(candidateBacktests) { row ->
            AionCard { Column {
                Text("${row["sample"] ?: "—"} · ${row["status"] ?: "—"}", color = AionColors.Text)
                Text("指标：${row["metrics"] ?: "—"}", color = AionColors.Muted, style = MaterialTheme.typography.bodySmall)
                row["error"]?.let { Text(it.toString(), color = AionColors.Red) }
            } }
        }
        if (isAdmin) {
            item { Text("独立策略审批", style = MaterialTheme.typography.titleMedium, color = AionColors.Text) }
            if (reviewQueue.isEmpty()) item { Text("暂无其他用户待审批的 SHADOW 版本", color = AionColors.Muted) }
            items(reviewQueue) { row ->
                AionCard { Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("${row["strategy_name"]?.toString()?.trim('"')} · v${row["version"]}", color = AionColors.Text)
                    Text(row["change_note"]?.toString()?.trim('"') ?: "无版本说明", color = AionColors.Muted)
                    Button(onClick = { approvalTarget = row }) { Text("审核并审批") }
                } }
            }
        }
        item { Text("策略审批记录", style = MaterialTheme.typography.titleMedium, color = AionColors.Text) }
        if (approvals.isEmpty()) item { Text("暂无审批记录", color = AionColors.Muted) }
        items(approvals) { row ->
            AionCard { Column {
                Text("${row["decision"] ?: "—"} · ${row["reviewer"] ?: "—"}", color = AionColors.Text)
                Text(row["note"]?.toString()?.trim('"') ?: "无备注", color = AionColors.Muted)
            } }
        }
        item { Text("PAPER 模拟交易（最近会话）", style = MaterialTheme.typography.titleMedium, color = AionColors.Text) }
        if (paperTrades.isEmpty()) item { Text("暂无模拟交易记录", color = AionColors.Muted) }
        items(paperTrades) { row ->
            AionCard { Column {
                Text("${row["session_name"]?.toString()?.trim('"') ?: "PAPER"} · ${row["symbol"] ?: "—"} · ${row["side"] ?: "—"}", color = AionColors.Text)
                Text("数量 ${row["quantity"] ?: "—"} · 入场 ${row["entry_price"] ?: "—"}", color = AionColors.Muted)
                Text("${row["status"] ?: "—"} · PnL ${row["pnl"] ?: "未结算"}", color = AionColors.Primary)
            } }
        }
        item { Text("PAPER 观察账户", style = MaterialTheme.typography.titleMedium, color = AionColors.Text) }
        if (sessions.isEmpty()) item { Text("暂无 PAPER 会话", color = AionColors.Muted) }
        items(sessions) { row ->
            AionCard { Column {
                Text(row["name"]?.toString()?.trim('"') ?: "PAPER", color = AionColors.Text)
                Text("资金：${row["current_cash"] ?: "—"}", color = AionColors.Primary)
                val paperMetrics = row["metrics"] as? JsonObject
                Text("权益：${paperMetrics?.get("equity") ?: row["current_cash"] ?: "—"} · 未实现：${paperMetrics?.get("unrealized_pnl") ?: "—"}", color = AionColors.Text, style = MaterialTheme.typography.bodySmall)
                Text("最大回撤：${paperMetrics?.get("max_drawdown_pct") ?: "—"}% · 已平仓：${paperMetrics?.get("trades") ?: "0"} 笔", color = AionColors.Muted, style = MaterialTheme.typography.bodySmall)
                Text("状态：${row["status"] ?: "—"}", color = AionColors.Muted)
            } }
        }
        item { Spacer(Modifier.height(20.dp)) }
    }
}

private fun JsonObject.value(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull ?: "—"

@Composable
private fun ExperimentDetails(discovery: JsonObject, dataset: JsonObject, result: JsonObject) {
    Text("数据集 ${dataset.value("dataset_id").take(12)} · ${discovery.value("status")}", color = AionColors.Primary)
    val candidates = (discovery["candidates"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
    val quality = dataset["quality"] as? JsonObject
    val gaps = (quality?.get("gaps") as? JsonArray)?.size ?: 0
    Text("${candidates.size} 个表达式 · ${(dataset["bars"] as? JsonArray)?.size ?: 0} 根已收盘 K 线 · 断档 $gaps 处", color = AionColors.Muted)
    candidates.forEach { candidate ->
        val holdout = candidate["holdout"] as? JsonObject ?: return@forEach
        Text(candidate["expression"].toString(), color = AionColors.Text)
        Text("${candidate.value("status")} · IC ${holdout.value("ic")} · Rank IC ${holdout.value("rank_ic")}", color = AionColors.Muted)
        Text("ICIR ${holdout.value("icir")} · 校正 p 值 ${holdout.value("adjusted_p_value")}", color = AionColors.Muted)
    }
    (result["discovery_library"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }.forEach { event ->
        Text("${event.value("factor_name").take(12)} · ${event.value("previous_status")} → ${event.value("status")}", color = AionColors.Primary)
        val primary = event["primary"] as? JsonObject
        val portfolio = event["portfolio"] as? JsonObject
        val sources = event["assets"] as? JsonObject
        sources?.forEach { (asset, metrics) ->
            val scored = metrics as? JsonObject ?: return@forEach
            Text("$asset · ${scored.value("status")} · Rank IC ${scored.value("rank_ic")}", color = AionColors.Muted)
        }
        primary?.let {
            Text("净收益 ${it.value("net_return_per_interval")} · 换手 ${it.value("turnover_per_interval")}", color = AionColors.Muted)
            Text("容量 ${it.value("capacity_p10_usd")} USD · 平均滑点 ${it.value("average_round_trip_slippage_bps")} bps", color = AionColors.Muted)
            val regime = it["by_regime"] as? JsonObject
            val time = it["by_time"] as? JsonArray
            val regimes = regime.orEmpty().mapNotNull { (label, metrics) ->
                (metrics as? JsonObject)?.let { row -> "$label ${row.value("rank_ic")}" }
            }
            if (regimes.isNotEmpty()) Text("市场状态 Rank IC · " + regimes.joinToString(" / "), color = AionColors.Muted)
            val windows = time.orEmpty().mapNotNull { row -> (row as? JsonObject)?.value("rank_ic") }
            if (windows.isNotEmpty()) Text("时间分段 Rank IC · " + windows.joinToString(" / "), color = AionColors.Muted)
        }
        portfolio?.let { Text("组合增量 ${it.value("incremental_net")}", color = AionColors.Muted) }
    }
}
