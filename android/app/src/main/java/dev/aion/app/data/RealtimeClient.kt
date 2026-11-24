package dev.aion.app.data

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.atomic.AtomicBoolean

data class WsEvent(val type: String, val payload: JsonElement?)

/**
 * Realtime feed with exponential backoff. It only connects while a session
 * exists and stops as soon as the app logs out.
 */
class RealtimeClient(
    private val session: SessionStore,
    private val client: OkHttpClient,
    private val state: AppState,
    private val json: Json,
    private val scope: CoroutineScope,
) {

    val events = MutableSharedFlow<WsEvent>(extraBufferCapacity = 128)

    private val stopped = AtomicBoolean(true)
    private var job: Job? = null
    @Volatile private var socket: WebSocket? = null

    fun connect() {
        if (!session.hasSession) return
        if (job?.isActive == true) return
        stopped.set(false)
        job = scope.launch { runLoop() }
    }

    fun disconnect() {
        stopped.set(true)
        job?.cancel()
        job = null
        runCatching { socket?.close(1000, "client closed") }
        socket = null
        state.connection.value = ConnectionState.OFFLINE
    }

    private suspend fun runLoop() {
        var attempt = 0
        while (!stopped.get() && session.hasSession) {
            state.connection.value = if (attempt == 0) ConnectionState.CONNECTING else ConnectionState.RECONNECTING
            val url = session.wsUrl()
            if (url == null) {
                break
            }

            val closed = CompletableDeferred<Unit>()
            val listener = object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    attempt = 0
                    state.connection.value = ConnectionState.ONLINE
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    handleMessage(text)
                }

                override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                    webSocket.close(1000, null)
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    closed.complete(Unit)
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    closed.complete(Unit)
                }
            }

            val created = client.newWebSocket(Request.Builder().url(url).build(), listener)
            socket = created
            try {
                closed.await()
            } finally {
                socket = null
                created.cancel()
            }

            if (stopped.get() || !session.hasSession) break

            state.connection.value = ConnectionState.RECONNECTING
            val backoff = (1000L shl attempt.coerceAtMost(5)).coerceAtMost(30_000L)
            attempt += 1
            delay(backoff)
        }
        state.connection.value = ConnectionState.OFFLINE
    }

    private fun handleMessage(text: String) {
        if (text.isBlank()) return
        val trimmed = text.trim()
        if (trimmed == "ping" || trimmed == "pong") return
        val element = runCatching { json.parseToJsonElement(trimmed) }.getOrNull() ?: return
        val obj = element as? JsonObject ?: return
        val type = (obj["type"] as? JsonPrimitive)?.content ?: return
        // Server events wrap their body in "data"; older payloads used "payload".
        events.tryEmit(WsEvent(type, obj["data"] ?: obj["payload"]))
    }
}
