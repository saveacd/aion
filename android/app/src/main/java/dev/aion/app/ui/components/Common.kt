package dev.aion.app.ui.components

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Inbox
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.WarningAmber
import androidx.compose.material.icons.outlined.WifiOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextFieldColors
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.aion.app.data.ConnectionState
import dev.aion.app.data.Loadable
import dev.aion.app.ui.theme.AionColors
import java.util.Locale

// ---------------------------------------------------------------------------
// Surfaces
// ---------------------------------------------------------------------------

@Composable
fun AionCard(
    modifier: Modifier = Modifier,
    onClick: (() -> Unit)? = null,
    padding: PaddingValues = PaddingValues(16.dp),
    content: @Composable ColumnScope.() -> Unit,
) {
    val shape = RoundedCornerShape(16.dp)
    val base = modifier
        .fillMaxWidth()
        .clip(shape)
        .background(AionColors.Card)
        .border(1.dp, AionColors.Border.copy(alpha = 0.75f), shape)
        .let { if (onClick != null) it.clickable { onClick() } else it }
    Column(modifier = base.padding(padding), content = content)
}

@Composable
fun SectionHeader(
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    action: (@Composable () -> Unit)? = null,
) {
    Row(
        modifier = modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium, color = AionColors.Text)
            if (subtitle != null) {
                Text(
                    subtitle,
                    style = MaterialTheme.typography.bodySmall,
                    color = AionColors.Muted,
                    modifier = Modifier.padding(top = 2.dp),
                )
            }
        }
        action?.invoke()
    }
}

@Composable
fun SectionCard(
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    trailing: (@Composable () -> Unit)? = null,
    padding: PaddingValues = PaddingValues(16.dp),
    content: @Composable ColumnScope.() -> Unit,
) {
    AionCard(modifier = modifier, padding = padding) {
        SectionHeader(title = title, subtitle = subtitle, action = trailing)
        Spacer(Modifier.height(12.dp))
        content()
    }
}

// ---------------------------------------------------------------------------
// Text
// ---------------------------------------------------------------------------

@Composable
fun Num(
    text: String,
    modifier: Modifier = Modifier,
    style: TextStyle = MaterialTheme.typography.bodyMedium,
    color: Color = Color.Unspecified,
    weight: FontWeight? = null,
    maxLines: Int = 1,
) {
    Text(
        text = text,
        modifier = modifier,
        style = style.copy(fontFamily = FontFamily.Monospace),
        color = color,
        fontWeight = weight,
        maxLines = maxLines,
        overflow = TextOverflow.Ellipsis,
    )
}

@Composable
fun MetricTile(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    valueColor: Color = AionColors.Text,
    hint: String? = null,
    mono: Boolean = true,
) {
    Column(modifier = modifier) {
        Text(
            label,
            style = MaterialTheme.typography.labelMedium,
            color = AionColors.Muted,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        Spacer(Modifier.height(6.dp))
        Text(
            text = value,
            style = MaterialTheme.typography.titleLarge.copy(
                fontFamily = if (mono) FontFamily.Monospace else FontFamily.Default,
                fontWeight = FontWeight.SemiBold,
            ),
            color = valueColor,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        if (hint != null) {
            Text(
                hint,
                style = MaterialTheme.typography.labelSmall,
                color = AionColors.Muted,
                modifier = Modifier.padding(top = 3.dp),
            )
        }
    }
}

@Composable
fun KeyValueRow(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    valueColor: Color = AionColors.Text,
    mono: Boolean = true,
) {
    Row(
        modifier = modifier
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
        Text(
            value,
            style = MaterialTheme.typography.bodyMedium,
            color = valueColor,
            fontFamily = if (mono) FontFamily.Monospace else null,
            textAlign = TextAlign.End,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(start = 12.dp),
        )
    }
}

@Composable
fun LabelText(text: String, modifier: Modifier = Modifier) {
    Text(
        text,
        modifier = modifier,
        style = MaterialTheme.typography.labelMedium,
        color = AionColors.Muted,
    )
}

// ---------------------------------------------------------------------------
// Badges / indicators
// ---------------------------------------------------------------------------

@Composable
fun Pill(
    text: String,
    color: Color,
    modifier: Modifier = Modifier,
    filled: Boolean = false,
) {
    val shape = RoundedCornerShape(50)
    Box(
        modifier = modifier
            .clip(shape)
            .background(color.copy(alpha = if (filled) 0.22f else 0.10f))
            .border(1.dp, color.copy(alpha = if (filled) 0.75f else 0.40f), shape)
            .padding(horizontal = 8.dp, vertical = 3.dp),
    ) {
        Text(
            text = text.uppercase(Locale.US),
            color = color,
            style = MaterialTheme.typography.labelSmall.copy(
                fontWeight = FontWeight.SemiBold,
                letterSpacing = 0.7.sp,
            ),
            maxLines = 1,
        )
    }
}

@Composable
fun ModePill(mode: String?, modifier: Modifier = Modifier) {
    if (mode.isNullOrBlank()) return
    val color = when (mode.uppercase(Locale.US)) {
        "MAINNET" -> AionColors.Red
        "TESTNET" -> AionColors.Amber
        "PAPER" -> AionColors.Primary
        else -> AionColors.Muted
    }
    Pill(text = mode, color = color, modifier = modifier, filled = true)
}

@Composable
fun StatusDot(
    state: ConnectionState,
    modifier: Modifier = Modifier,
    size: Dp = 9.dp,
) {
    val color = when (state) {
        ConnectionState.ONLINE -> AionColors.Green
        ConnectionState.CONNECTING, ConnectionState.RECONNECTING -> AionColors.Amber
        ConnectionState.OFFLINE -> AionColors.Red
    }
    Box(
        modifier = modifier
            .size(size)
            .clip(CircleShape)
            .background(color.copy(alpha = 0.22f))
            .padding(2.dp)
            .clip(CircleShape)
            .background(color),
    )
}

@Composable
fun ConnectionText(state: ConnectionState): String = when (state) {
    ConnectionState.ONLINE -> "实时连接正常"
    ConnectionState.CONNECTING -> "正在连接…"
    ConnectionState.RECONNECTING -> "连接中断，正在重连…"
    ConnectionState.OFFLINE -> "实时连接断开"
}

@Composable
fun InfoBanner(
    text: String,
    color: Color,
    modifier: Modifier = Modifier,
    icon: ImageVector = Icons.Outlined.WarningAmber,
) {
    val shape = RoundedCornerShape(14.dp)
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clip(shape)
            .background(color.copy(alpha = 0.10f))
            .border(1.dp, color.copy(alpha = 0.30f), shape)
            .padding(12.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Icon(icon, contentDescription = null, tint = color, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(10.dp))
        Text(
            text,
            color = AionColors.Text,
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.weight(1f),
        )
    }
}

@Composable
fun LabeledValue(
    label: String,
    value: String,
    valueColor: Color = AionColors.Text,
    mono: Boolean = true,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier) {
        LabelText(label)
        Spacer(Modifier.height(4.dp))
        Text(
            value,
            style = MaterialTheme.typography.bodyMedium,
            color = valueColor,
            fontFamily = if (mono) FontFamily.Monospace else null,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

// ---------------------------------------------------------------------------
// States
// ---------------------------------------------------------------------------

@Composable
fun SkeletonBlock(height: Dp, modifier: Modifier = Modifier) {
    val transition = rememberInfiniteTransition(label = "skeleton")
    val alpha by transition.animateFloat(
        initialValue = 0.28f,
        targetValue = 0.58f,
        animationSpec = infiniteRepeatable(tween(900), RepeatMode.Reverse),
        label = "skeleton-alpha",
    )
    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(height)
            .clip(RoundedCornerShape(12.dp))
            .background(AionColors.CardHigh.copy(alpha = alpha)),
    )
}

@Composable
fun SkeletonList(rows: Int = 4, modifier: Modifier = Modifier) {
    Column(modifier = modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        repeat(rows) { index ->
            SkeletonBlock(if (index == 0) 96.dp else 68.dp)
        }
    }
}

@Composable
fun EmptyState(
    title: String,
    message: String,
    modifier: Modifier = Modifier,
    icon: ImageVector = Icons.Outlined.Inbox,
    actionLabel: String? = null,
    onAction: (() -> Unit)? = null,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(vertical = 36.dp, horizontal = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(
            modifier = Modifier
                .size(52.dp)
                .clip(CircleShape)
                .background(AionColors.CardHigh),
            contentAlignment = Alignment.Center,
        ) {
            Icon(icon, contentDescription = null, tint = AionColors.Muted, modifier = Modifier.size(24.dp))
        }
        Spacer(Modifier.height(14.dp))
        Text(title, style = MaterialTheme.typography.titleMedium, color = AionColors.Text)
        Spacer(Modifier.height(6.dp))
        Text(
            message,
            style = MaterialTheme.typography.bodySmall,
            color = AionColors.Muted,
            textAlign = TextAlign.Center,
        )
        if (actionLabel != null && onAction != null) {
            Spacer(Modifier.height(14.dp))
            TextButton(onClick = onAction) { Text(actionLabel, color = AionColors.Primary) }
        }
    }
}

@Composable
fun ErrorState(
    message: String,
    requestId: String? = null,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(vertical = 28.dp, horizontal = 20.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(
            modifier = Modifier
                .size(52.dp)
                .clip(CircleShape)
                .background(AionColors.Red.copy(alpha = 0.12f)),
            contentAlignment = Alignment.Center,
        ) {
            Icon(Icons.Outlined.WifiOff, contentDescription = null, tint = AionColors.Red, modifier = Modifier.size(24.dp))
        }
        Spacer(Modifier.height(14.dp))
        Text("加载失败", style = MaterialTheme.typography.titleMedium, color = AionColors.Text)
        Spacer(Modifier.height(6.dp))
        Text(
            message,
            style = MaterialTheme.typography.bodySmall,
            color = AionColors.Muted,
            textAlign = TextAlign.Center,
        )
        if (!requestId.isNullOrBlank()) {
            Spacer(Modifier.height(6.dp))
            Num("request_id: $requestId", style = MaterialTheme.typography.labelSmall, color = AionColors.Muted)
        }
        Spacer(Modifier.height(16.dp))
        Button(
            onClick = onRetry,
            colors = ButtonDefaults.buttonColors(containerColor = AionColors.Primary),
            contentPadding = PaddingValues(horizontal = 22.dp, vertical = 10.dp),
        ) {
            Icon(Icons.Outlined.Refresh, contentDescription = null, modifier = Modifier.size(16.dp))
            Spacer(Modifier.width(8.dp))
            Text("重试", style = MaterialTheme.typography.labelLarge)
        }
    }
}

@Composable
fun ErrorBanner(message: String, onRetry: () -> Unit, modifier: Modifier = Modifier) {
    val shape = RoundedCornerShape(14.dp)
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clip(shape)
            .background(AionColors.Red.copy(alpha = 0.10f))
            .border(1.dp, AionColors.Red.copy(alpha = 0.28f), shape)
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(Icons.Outlined.WarningAmber, contentDescription = null, tint = AionColors.Red, modifier = Modifier.size(16.dp))
        Spacer(Modifier.width(8.dp))
        Text(
            message,
            style = MaterialTheme.typography.bodySmall,
            color = AionColors.Text,
            modifier = Modifier.weight(1f),
            maxLines = 3,
            overflow = TextOverflow.Ellipsis,
        )
        TextButton(onClick = onRetry, contentPadding = PaddingValues(horizontal = 8.dp)) {
            Text("重试", color = AionColors.Red, style = MaterialTheme.typography.labelMedium)
        }
    }
}

/**
 * One shared placeholder policy: skeleton while empty, error with retry,
 * explicit empty copy, and a thin error banner when stale data is retained.
 */
@Composable
fun <T> LoadableContent(
    state: Loadable<T>,
    onRetry: () -> Unit,
    isEmpty: (T) -> Boolean = { false },
    emptyTitle: String = "暂无数据",
    emptyMessage: String = "暂时没有可展示的数据。",
    emptyIcon: ImageVector = Icons.Outlined.Inbox,
    modifier: Modifier = Modifier,
    content: @Composable (T) -> Unit,
) {
    val data = state.data
    Column(modifier = modifier.fillMaxWidth()) {
        when {
            data == null && state.error != null -> ErrorState(
                message = state.error,
                requestId = state.requestId,
                onRetry = onRetry,
            )
            data == null -> SkeletonList()
            isEmpty(data) -> {
                if (state.error != null) {
                    ErrorBanner(state.error, onRetry)
                    Spacer(Modifier.height(10.dp))
                }
                EmptyState(title = emptyTitle, message = emptyMessage, icon = emptyIcon)
            }
            else -> {
                if (state.error != null) {
                    ErrorBanner(state.error, onRetry)
                    Spacer(Modifier.height(10.dp))
                }
                content(data)
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Form primitives
// ---------------------------------------------------------------------------

@Composable
fun aionFieldColors(): TextFieldColors = OutlinedTextFieldDefaults.colors(
    focusedContainerColor = AionColors.CardHigh,
    unfocusedContainerColor = AionColors.CardHigh,
    disabledContainerColor = AionColors.CardHigh.copy(alpha = 0.5f),
    errorContainerColor = AionColors.CardHigh,
    focusedBorderColor = AionColors.Primary,
    unfocusedBorderColor = AionColors.Border,
    disabledBorderColor = AionColors.Border,
    errorBorderColor = AionColors.Red,
    focusedLabelColor = AionColors.Primary,
    unfocusedLabelColor = AionColors.Muted,
    cursorColor = AionColors.Primary,
    focusedTextColor = AionColors.Text,
    unfocusedTextColor = AionColors.Text,
    errorTextColor = AionColors.Text,
)

@Composable
fun AionField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    placeholder: String? = null,
    keyboardType: KeyboardType = KeyboardType.Text,
    mono: Boolean = false,
    singleLine: Boolean = true,
    enabled: Boolean = true,
    isError: Boolean = false,
    supporting: String? = null,
    visualTransformation: VisualTransformation = VisualTransformation.None,
    keyboardActions: KeyboardActions = KeyboardActions.Default,
    trailing: (@Composable () -> Unit)? = null,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label) },
        modifier = modifier.fillMaxWidth(),
        singleLine = singleLine,
        enabled = enabled,
        isError = isError,
        visualTransformation = visualTransformation,
        keyboardActions = keyboardActions,
        placeholder = if (placeholder != null) {
            { Text(placeholder, color = AionColors.Muted) }
        } else {
            null
        },
        textStyle = if (mono) {
            MaterialTheme.typography.bodyLarge.copy(fontFamily = FontFamily.Monospace)
        } else {
            MaterialTheme.typography.bodyLarge
        },
        keyboardOptions = KeyboardOptions(keyboardType = keyboardType),
        colors = aionFieldColors(),
        shape = RoundedCornerShape(12.dp),
        supportingText = if (supporting != null) {
            { Text(supporting, style = MaterialTheme.typography.labelSmall) }
        } else {
            null
        },
        trailingIcon = trailing,
    )
}

@Composable
fun PillTabs(
    labels: List<String>,
    selected: Int,
    onSelect: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(AionColors.CardHigh)
            .padding(4.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        labels.forEachIndexed { index, label ->
            val active = index == selected
            Box(
                modifier = Modifier
                    .weight(1f)
                    .clip(RoundedCornerShape(9.dp))
                    .background(if (active) AionColors.Primary.copy(alpha = 0.18f) else Color.Transparent)
                    .clickable { onSelect(index) }
                    .padding(vertical = 9.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    label,
                    color = if (active) AionColors.Text else AionColors.Muted,
                    style = MaterialTheme.typography.labelLarge,
                    maxLines = 1,
                )
            }
        }
    }
}

@Composable
fun SelectableChip(
    label: String,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    color: Color = AionColors.Primary,
) {
    val shape = RoundedCornerShape(10.dp)
    Box(
        modifier = modifier
            .clip(shape)
            .background(if (selected) color.copy(alpha = 0.18f) else Color.Transparent)
            .border(1.dp, if (selected) color.copy(alpha = 0.65f) else AionColors.Border, shape)
            .clickable { onClick() }
            .padding(horizontal = 12.dp, vertical = 7.dp),
    ) {
        Text(
            label,
            color = if (selected) color else AionColors.Muted,
            style = MaterialTheme.typography.labelMedium,
            maxLines = 1,
        )
    }
}

// ---------------------------------------------------------------------------
// Dialogs
// ---------------------------------------------------------------------------

@Composable
fun ConfirmDialog(
    title: String,
    message: String,
    onDismiss: () -> Unit,
    confirmLabel: String = "确认",
    destructive: Boolean = false,
    reasonLabel: String? = null,
    onConfirm: (String?) -> Unit,
) {
    var reason by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = AionColors.CardHigh,
        titleContentColor = AionColors.Text,
        textContentColor = AionColors.Muted,
        shape = RoundedCornerShape(20.dp),
        title = { Text(title, style = MaterialTheme.typography.titleMedium) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(message, style = MaterialTheme.typography.bodyMedium, color = AionColors.Muted)
                if (reasonLabel != null) {
                    OutlinedTextField(
                        value = reason,
                        onValueChange = { reason = it },
                        label = { Text(reasonLabel) },
                        modifier = Modifier.fillMaxWidth(),
                        minLines = 2,
                        maxLines = 4,
                        colors = aionFieldColors(),
                        shape = RoundedCornerShape(12.dp),
                    )
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { onConfirm(reason.takeIf { it.isNotBlank() }) }) {
                Text(confirmLabel, color = if (destructive) AionColors.Red else AionColors.Primary)
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("取消", color = AionColors.Muted) }
        },
    )
}

// ---------------------------------------------------------------------------
// Chart
// ---------------------------------------------------------------------------

@Composable
fun PriceLineChart(
    values: List<Float>,
    modifier: Modifier = Modifier,
    color: Color = AionColors.Primary,
) {
    Canvas(modifier = modifier.fillMaxWidth()) {
        if (values.size < 2) return@Canvas
        val minValue = values.minOrNull() ?: return@Canvas
        val maxValue = values.maxOrNull() ?: return@Canvas
        val range = (maxValue - minValue).takeIf { it > 0f } ?: 1f
        val top = size.height * 0.08f
        val usable = size.height * 0.84f

        fun yOf(value: Float) = top + usable - ((value - minValue) / range) * usable

        for (i in 0..2) {
            val gy = top + usable * i / 2f
            drawLine(
                color = AionColors.Border.copy(alpha = 0.55f),
                start = Offset(0f, gy),
                end = Offset(size.width, gy),
                strokeWidth = 1f,
            )
        }

        val stepX = size.width / (values.size - 1).toFloat()
        val linePath = Path()
        values.forEachIndexed { index, value ->
            val x = index * stepX
            val y = yOf(value)
            if (index == 0) linePath.moveTo(x, y) else linePath.lineTo(x, y)
        }

        val fillPath = Path().apply {
            addPath(linePath)
            lineTo(size.width, size.height)
            lineTo(0f, size.height)
            close()
        }
        drawPath(
            path = fillPath,
            brush = Brush.verticalGradient(
                listOf(color.copy(alpha = 0.30f), Color.Transparent),
            ),
        )
        drawPath(
            path = linePath,
            color = color,
            style = Stroke(width = 2.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round),
        )
        drawCircle(
            color = color,
            radius = 3.5.dp.toPx(),
            center = Offset(size.width, yOf(values.last())),
        )
    }
}

@Composable
fun FullScreenLoading() {
    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        SkeletonList(rows = 3, modifier = Modifier.padding(20.dp))
    }
}
