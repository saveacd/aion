package dev.aion.app.ui

import androidx.compose.ui.graphics.Color
import dev.aion.app.ui.theme.AionColors
import java.math.BigDecimal
import java.math.RoundingMode
import java.text.DecimalFormat
import java.text.DecimalFormatSymbols
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

fun toDecimal(raw: String?): BigDecimal? =
    raw?.trim()?.takeIf { it.isNotEmpty() }?.toBigDecimalOrNull()

fun fmtNum(raw: String?, maxDecimals: Int = 6): String {
    val value = toDecimal(raw) ?: return "—"
    return try {
        val rounded = value.abs().setScale(maxDecimals, RoundingMode.HALF_UP).stripTrailingZeros()
        val scale = rounded.scale().coerceAtLeast(0)
        val pattern = if (scale == 0) "#,##0" else "#,##0." + "0".repeat(scale)
        val df = DecimalFormat(pattern, DecimalFormatSymbols(Locale.US))
        val text = df.format(rounded)
        if (value.signum() < 0) "-$text" else text
    } catch (e: Exception) {
        raw ?: "—"
    }
}

fun fmtFixed(raw: String?, decimals: Int = 2): String {
    val value = toDecimal(raw) ?: return "—"
    return try {
        val pattern = if (decimals == 0) "#,##0" else "#,##0." + "0".repeat(decimals)
        DecimalFormat(pattern, DecimalFormatSymbols(Locale.US)).format(value)
    } catch (e: Exception) {
        raw ?: "—"
    }
}

fun fmtUsd(raw: String?, decimals: Int = 2): String {
    val value = toDecimal(raw) ?: return "—"
    val text = fmtFixed(value.abs().toPlainString(), decimals)
    return if (value.signum() < 0) "-\$$text" else "\$$text"
}

fun fmtSignedUsd(raw: String?): String {
    val value = toDecimal(raw) ?: return "—"
    val text = fmtFixed(value.abs().toPlainString(), 2)
    return when {
        value.signum() < 0 -> "-\$$text"
        value.signum() > 0 -> "+\$$text"
        else -> "\$$text"
    }
}

fun fmtPct(raw: String?, signed: Boolean = false): String {
    val value = toDecimal(raw) ?: return "—"
    val text = fmtFixed(value.abs().toPlainString(), 2)
    return when {
        value.signum() < 0 -> "-$text%"
        signed && value.signum() > 0 -> "+$text%"
        else -> "$text%"
    }
}

fun pnlColor(raw: String?): Color = when {
    raw == null -> AionColors.Muted
    (toDecimal(raw)?.signum() ?: 0) > 0 -> AionColors.Green
    (toDecimal(raw)?.signum() ?: 0) < 0 -> AionColors.Red
    else -> AionColors.Text
}

fun sideColor(side: String?): Color = when (side?.uppercase(Locale.US)) {
    "BUY", "LONG" -> AionColors.Green
    "SELL", "SHORT" -> AionColors.Red
    else -> AionColors.Muted
}

private val shortTimeFormatter = DateTimeFormatter.ofPattern("MM-dd HH:mm")
private val fullTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")

fun formatTime(raw: String?): String = formatTimeWith(raw, shortTimeFormatter)

fun formatTimeFull(raw: String?): String = formatTimeWith(raw, fullTimeFormatter)

private fun formatTimeWith(raw: String?, formatter: DateTimeFormatter): String {
    if (raw.isNullOrBlank()) return "—"
    val text = raw.trim()
    return try {
        val instant = OffsetDateTime.parse(text).toInstant()
        formatter.withZone(ZoneId.systemDefault()).format(instant)
    } catch (e: Exception) {
        try {
            formatter.withZone(ZoneId.systemDefault()).format(Instant.parse(text))
        } catch (e2: Exception) {
            text.replace('T', ' ').take(19)
        }
    }
}

fun shortId(raw: String?): String {
    if (raw.isNullOrBlank()) return "—"
    return if (raw.length <= 10) raw else raw.take(8) + "…"
}

fun statusColor(status: String?): Color = when (status?.uppercase(Locale.US)) {
    "APPROVED", "FILLED", "ACTIVE", "OK", "ONLINE", "ON", "TRUE", "PAPER" -> AionColors.Green
    "REJECTED", "FAILED", "ERROR", "EMERGENCY", "BLOCKED", "CANCELED", "CANCELLED" -> AionColors.Red
    "PENDING", "NEW", "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "CREATED",
    "CANCEL_PENDING", "SUSPENDED", "WARNING", "SHADOW" -> AionColors.Amber
    "TESTNET" -> AionColors.Amber
    else -> AionColors.Muted
}
