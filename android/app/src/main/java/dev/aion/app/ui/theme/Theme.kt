package dev.aion.app.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

object AionColors {
    val Bg = Color(0xFF100D0B)
    val Card = Color(0xFF1A1511)
    val CardHigh = Color(0xFF241C15)
    val Border = Color(0xFF39291B)
    val Primary = Color(0xFFE9A34A)
    val Green = Color(0xFF49B987)
    val Red = Color(0xFFEA3943)
    val Amber = Color(0xFFFFC46B)
    val Text = Color(0xFFFFF4E6)
    val Muted = Color(0xFFB7A38D)
    val Purple = Color(0xFFD88A45)
}

private val AionDarkScheme = darkColorScheme(
    primary = AionColors.Primary,
    onPrimary = Color.White,
    primaryContainer = Color(0xFF3A2616),
    onPrimaryContainer = Color(0xFFFFE6C8),
    secondary = AionColors.Green,
    onSecondary = Color(0xFF04140D),
    tertiary = AionColors.Amber,
    onTertiary = Color(0xFF201800),
    background = AionColors.Bg,
    onBackground = AionColors.Text,
    surface = AionColors.Card,
    onSurface = AionColors.Text,
    surfaceVariant = AionColors.CardHigh,
    onSurfaceVariant = AionColors.Muted,
    surfaceContainer = AionColors.Card,
    surfaceContainerLow = AionColors.Bg,
    surfaceContainerHigh = AionColors.CardHigh,
    surfaceContainerHighest = Color(0xFF202839),
    inverseSurface = Color(0xFFFFF4E6),
    inverseOnSurface = Color(0xFF11151F),
    outline = Color(0xFF2C3547),
    outlineVariant = Color(0xFF1F2735),
    error = AionColors.Red,
    onError = Color.White,
    errorContainer = Color(0xFF3B1B12),
    onErrorContainer = Color(0xFFFFDCCB),
    scrim = Color.Black,
)

private val AionTypography = Typography(
    displaySmall = TextStyle(
        fontFamily = FontFamily.Monospace,
        fontWeight = FontWeight.SemiBold,
        fontSize = 32.sp,
        lineHeight = 38.sp,
        letterSpacing = (-0.5).sp,
    ),
    headlineSmall = TextStyle(
        fontWeight = FontWeight.SemiBold,
        fontSize = 22.sp,
        lineHeight = 28.sp,
    ),
    titleLarge = TextStyle(
        fontWeight = FontWeight.SemiBold,
        fontSize = 19.sp,
        lineHeight = 24.sp,
    ),
    titleMedium = TextStyle(
        fontWeight = FontWeight.SemiBold,
        fontSize = 16.sp,
        lineHeight = 22.sp,
    ),
    bodyLarge = TextStyle(fontSize = 15.sp, lineHeight = 21.sp),
    bodyMedium = TextStyle(fontSize = 13.5.sp, lineHeight = 19.sp),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 16.sp),
    labelLarge = TextStyle(fontWeight = FontWeight.Medium, fontSize = 13.sp),
    labelMedium = TextStyle(fontWeight = FontWeight.Medium, fontSize = 11.5.sp),
    labelSmall = TextStyle(fontWeight = FontWeight.Medium, fontSize = 10.5.sp, letterSpacing = 0.4.sp),
)

private val AionShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(10.dp),
    medium = RoundedCornerShape(16.dp),
    large = RoundedCornerShape(20.dp),
    extraLarge = RoundedCornerShape(24.dp),
)

@Composable
fun AionTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = AionDarkScheme,
        typography = AionTypography,
        shapes = AionShapes,
        content = content,
    )
}
