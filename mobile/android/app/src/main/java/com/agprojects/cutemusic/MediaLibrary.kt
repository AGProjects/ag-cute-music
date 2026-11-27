package com.agprojects.cutemusic

// The phone's own audio for the JS side (src/local.ts): every audio file Android's media index (MediaStore) knows,
// with what Android says it is (music, recording, podcast...: the JS side makes sources of them), their covers (the album's thumbnail, else the picture in the file), their lyrics (a
// .lrc or .txt next to the file, when the app may read it) and their levels for the VU meters, worked out here
// (MediaCodec + an FFT) in the format AG CuteMusic's library serves (cutemusic/levels.py): the meters work
// without a library on the LAN. Covers and levels are kept in the app's cache.

import android.content.ContentUris
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.AudioFormat
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import android.util.Base64
import android.util.Size
import com.facebook.react.ReactPackage
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.NativeModule
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.uimanager.ViewManager
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.nio.ByteOrder
import java.util.concurrent.Executors
import kotlin.math.PI
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.ln
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt

class MediaLibraryModule(private val context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {

    override fun getName() = "MediaLibrary"

    // one song analysed at a time; the queries and covers beside it
    private val analyser = Executors.newSingleThreadExecutor()
    private val worker = Executors.newFixedThreadPool(2)

    private data class Row(
        val id: Long, val albumId: Long, val title: String, val artist: String, val albumArtist: String,
        val album: String, val year: Int, val track: Int, val duration: Double, val relative: String,
        val name: String, val data: String, val genre: String, val modified: Long, val kinds: List<String>,
    )

    // what Android says an audio file is (its flags): music, podcast, audiobook, recording, ringtone,
    // notification, alarm; none: some other audio
    private val kindColumns = buildList {
        add("music" to MediaStore.Audio.Media.IS_MUSIC)
        add("podcast" to MediaStore.Audio.Media.IS_PODCAST)
        add("ringtone" to MediaStore.Audio.Media.IS_RINGTONE)
        add("notification" to MediaStore.Audio.Media.IS_NOTIFICATION)
        add("alarm" to MediaStore.Audio.Media.IS_ALARM)
        if (Build.VERSION.SDK_INT >= 29) add("audiobook" to MediaStore.Audio.Media.IS_AUDIOBOOK)
        if (Build.VERSION.SDK_INT >= 31) add("recording" to MediaStore.Audio.Media.IS_RECORDING)
    }

    private fun uri(id: Long): Uri = ContentUris.withAppendedId(MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, id)

    // The folder of a song from the storage's root ("Music/Jazz/"): RELATIVE_PATH (Android 10+), else from _data
    private fun relativeOf(relative: String?, data: String): String {
        if (!relative.isNullOrEmpty()) return relative
        val match = Regex("^/storage/(?:emulated/\\d+|[^/]+)/(.*/)?[^/]*$").find(data) ?: return ""
        return match.groupValues[1]
    }

    private fun rows(): List<Row> {
        val columns = mutableListOf(
            MediaStore.Audio.Media._ID, MediaStore.Audio.Media.ALBUM_ID, MediaStore.Audio.Media.TITLE,
            MediaStore.Audio.Media.ARTIST, MediaStore.Audio.Media.ALBUM, MediaStore.Audio.Media.YEAR,
            MediaStore.Audio.Media.TRACK, MediaStore.Audio.Media.DURATION, MediaStore.Audio.Media.DISPLAY_NAME,
            MediaStore.Audio.Media.DATA, MediaStore.Audio.Media.DATE_MODIFIED, "album_artist",
        )
        if (Build.VERSION.SDK_INT >= 29) columns += MediaStore.Audio.Media.RELATIVE_PATH
        if (Build.VERSION.SDK_INT >= 30) columns += MediaStore.Audio.Media.GENRE
        columns += kindColumns.map { it.second }
        val found = mutableListOf<Row>()
        val cursor = (try {
            context.contentResolver.query(
                MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, columns.toTypedArray(),
                null, null, null,
            )
        } catch (e: IllegalArgumentException) {      // an older Android without album_artist
            columns.remove("album_artist")
            context.contentResolver.query(
                MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, columns.toTypedArray(),
                null, null, null,
            )
        }) ?: return found
        cursor.use { c ->
            fun text(name: String): String {
                val index = c.getColumnIndex(name)
                return if (index < 0 || c.isNull(index)) "" else c.getString(index) ?: ""
            }
            fun number(name: String): Long {
                val index = c.getColumnIndex(name)
                return if (index < 0 || c.isNull(index)) 0L else c.getLong(index)
            }
            while (c.moveToNext()) {
                val data = text(MediaStore.Audio.Media.DATA)
                val unknown = { value: String -> if (value == "<unknown>") "" else value }
                found += Row(
                    id = number(MediaStore.Audio.Media._ID),
                    albumId = number(MediaStore.Audio.Media.ALBUM_ID),
                    title = text(MediaStore.Audio.Media.TITLE),
                    artist = unknown(text(MediaStore.Audio.Media.ARTIST)),
                    albumArtist = unknown(text("album_artist")),
                    album = unknown(text(MediaStore.Audio.Media.ALBUM)),
                    year = number(MediaStore.Audio.Media.YEAR).toInt(),
                    track = number(MediaStore.Audio.Media.TRACK).toInt(),
                    duration = number(MediaStore.Audio.Media.DURATION) / 1000.0,
                    relative = relativeOf(if (Build.VERSION.SDK_INT >= 29) text(MediaStore.Audio.Media.RELATIVE_PATH) else null, data),
                    name = text(MediaStore.Audio.Media.DISPLAY_NAME).ifEmpty { data.substringAfterLast('/') },
                    data = data,
                    genre = if (Build.VERSION.SDK_INT >= 30) text(MediaStore.Audio.Media.GENRE) else "",
                    modified = number(MediaStore.Audio.Media.DATE_MODIFIED),
                    kinds = kindColumns.filter { number(it.second) != 0L }.map { it.first },
                )
            }
        }
        return found
    }

    // The folders with music in them, with how many songs each holds (its subfolders' too): [{path, count}]
    @ReactMethod
    fun folders(promise: Promise) {
        worker.execute {
            try {
                val counts = sortedMapOf<String, Int>(String.CASE_INSENSITIVE_ORDER)
                for (row in rows()) {
                    val parts = row.relative.split('/').filter { it.isNotEmpty() }
                    for (depth in 1..parts.size) {
                        val path = parts.take(depth).joinToString("/") + "/"
                        counts[path] = (counts[path] ?: 0) + 1
                    }
                }
                val result = Arguments.createArray()
                for ((path, count) in counts) {
                    val entry = Arguments.createMap()
                    entry.putString("path", path)
                    entry.putInt("count", count)
                    result.pushMap(entry)
                }
                promise.resolve(result)
            } catch (e: Exception) {
                promise.reject("folders", e.message, e)
            }
        }
    }

    // The songs in folder (and under it): their tags, path relative to folder, id ("" folder: all of them)
    @ReactMethod
    fun tracks(folder: String, promise: Promise) {
        worker.execute {
            try {
                val prefix = folder.trimStart('/')
                val result = Arguments.createArray()
                for (row in rows()) {
                    if (!row.relative.startsWith(prefix, ignoreCase = true)) continue
                    val entry = Arguments.createMap()
                    entry.putDouble("id", row.id.toDouble())
                    entry.putDouble("albumId", row.albumId.toDouble())
                    entry.putString("path", row.relative.substring(prefix.length) + row.name)
                    entry.putString("title", row.title)
                    entry.putString("artist", row.artist)
                    entry.putString("albumartist", row.albumArtist)
                    entry.putString("album", row.album)
                    entry.putString("genre", row.genre)
                    entry.putInt("year", row.year)
                    entry.putInt("track", row.track % 1000)        // MediaStore: disc x 1000 + track
                    entry.putInt("disc", row.track / 1000)
                    entry.putDouble("duration", row.duration)
                    entry.putDouble("modified", row.modified.toDouble())
                    entry.putArray("kinds", Arguments.fromList(row.kinds))
                    result.pushMap(entry)
                }
                promise.resolve(result)
            } catch (e: Exception) {
                promise.reject("tracks", e.message, e)
            }
        }
    }

    // A song's cover as a file:// URI (kept a size in the cache, an album's shared), or null
    @ReactMethod
    fun cover(id: Double, albumId: Double, size: Double, promise: Promise) {
        worker.execute {
            try {
                val side = max(64, min(1024, size.roundToInt()))
                val key = if (albumId > 0) "album-${albumId.toLong()}" else "song-${id.toLong()}"
                val file = File(context.cacheDir, "covers/$key-$side.jpg")
                if (file.exists()) {
                    promise.resolve(if (file.length() > 0) Uri.fromFile(file).toString() else null)
                    return@execute
                }
                file.parentFile?.mkdirs()
                var bitmap: Bitmap? = null
                if (Build.VERSION.SDK_INT >= 29) {
                    bitmap = try {
                        context.contentResolver.loadThumbnail(uri(id.toLong()), Size(side, side), null)
                    } catch (e: Exception) {
                        null
                    }
                }
                if (bitmap == null) {
                    val retriever = MediaMetadataRetriever()
                    try {
                        retriever.setDataSource(context, uri(id.toLong()))
                        retriever.embeddedPicture?.let { bytes ->
                            val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                            BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)
                            var sample = 1
                            while (max(options.outWidth, options.outHeight) / (sample * 2) >= side) sample *= 2
                            bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
                        }
                    } catch (e: Exception) {
                    } finally {
                        retriever.release()
                    }
                }
                val image = bitmap
                if (image == null) {
                    file.writeBytes(ByteArray(0))       // none: not looked for again
                    promise.resolve(null)
                    return@execute
                }
                FileOutputStream(file).use { image.compress(Bitmap.CompressFormat.JPEG, 88, it) }
                promise.resolve(Uri.fromFile(file).toString())
            } catch (e: Exception) {
                promise.resolve(null)
            }
        }
    }

    // A song's lyrics: <file>.lrc or <file>.txt next to it (when the app may read it), else null
    @ReactMethod
    fun lyrics(id: Double, promise: Promise) {
        worker.execute {
            try {
                val data = rows().firstOrNull { it.id == id.toLong() }?.data
                if (data.isNullOrEmpty()) {
                    promise.resolve(null)
                    return@execute
                }
                val base = data.substringBeforeLast('.')
                for (extension in listOf(".lrc", ".LRC", ".txt")) {
                    val file = File(base + extension)
                    try {
                        if (file.isFile) {
                            promise.resolve(file.readText())
                            return@execute
                        }
                    } catch (e: Exception) {
                    }
                }
                promise.resolve(null)
            } catch (e: Exception) {
                promise.resolve(null)
            }
        }
    }

    // A song's levels (JSON, as GET /levels): worked out once, then from the cache
    @ReactMethod
    fun levels(id: Double, modified: Double, promise: Promise) {
        analyser.execute {
            try {
                val file = File(context.cacheDir, "levels/${id.toLong()}-${modified.toLong()}.json")
                if (file.exists()) {
                    promise.resolve(file.readText())
                    return@execute
                }
                file.parentFile?.mkdirs()
                val json = Levels(context, uri(id.toLong())).analyse().toString()
                File(file.path + ".part").apply { writeText(json); renameTo(file) }
                promise.resolve(json)
            } catch (e: Exception) {
                promise.reject("levels", e.message, e)
            }
        }
    }

    override fun invalidate() {
        analyser.shutdownNow()
        worker.shutdownNow()
        super.invalidate()
    }
}

// The levels of a song, decoded with MediaCodec: per INTERVAL ms, each channel's RMS and peak, and the spectrum
// in BANDS bands equal on a log scale from LOW to HIGH Hz (the strongest FFT bin in each, or the one at its centre
// where a band is narrower than a bin), as dB bytes (-dB x 2)
private class Levels(private val context: ReactApplicationContext, private val uri: Uri) {
    companion object {
        const val INTERVAL = 50
        const val FFT = 1024
        const val BANDS = 32
        const val LOW = 30.0
        const val HIGH = 16000.0
        const val FLOOR = -60.0
        const val SPECTRUM_FLOOR = -90.0
    }

    private val rms = java.io.ByteArrayOutputStream()
    private val peak = java.io.ByteArrayOutputStream()
    private val spectrum = java.io.ByteArrayOutputStream()
    private var rate = 44100
    private var channels = 2
    private var perFrame = 0
    private val sums = DoubleArray(2)
    private val peaks = DoubleArray(2)
    private var counted = 0
    private val ring = DoubleArray(FFT)
    private var ringAt = 0
    private val window = DoubleArray(FFT) { 0.5 - 0.5 * cos(2 * PI * it / (FFT - 1)) }
    private val windowSum = window.sum()
    private val re = DoubleArray(FFT)
    private val im = DoubleArray(FFT)
    private var edges = DoubleArray(0)

    private fun byte(db: Double, floor: Double): Int =
        if (db.isNaN()) (-floor * 2).toInt() else (min(0.0, max(floor, db)) * -2).roundToInt()

    private fun configure(sampleRate: Int, channelCount: Int) {
        rate = sampleRate
        channels = max(1, channelCount)
        perFrame = max(1, rate * INTERVAL / 1000)
        edges = DoubleArray(BANDS + 1) { 2.0.pow(ln(LOW) / ln(2.0) + (ln(HIGH) / ln(2.0) - ln(LOW) / ln(2.0)) * it / BANDS) }
    }

    fun analyse(): JSONObject {
        val extractor = MediaExtractor()
        extractor.setDataSource(context, uri, null)
        var track = -1
        var format: MediaFormat? = null
        for (index in 0 until extractor.trackCount) {
            val candidate = extractor.getTrackFormat(index)
            if (candidate.getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true) {
                track = index
                format = candidate
                break
            }
        }
        if (track < 0 || format == null) throw IllegalStateException("no audio")
        extractor.selectTrack(track)
        configure(format.getInteger(MediaFormat.KEY_SAMPLE_RATE), format.getInteger(MediaFormat.KEY_CHANNEL_COUNT))
        val codec = MediaCodec.createDecoderByType(format.getString(MediaFormat.KEY_MIME)!!)
        codec.configure(format, null, null, 0)
        codec.start()
        var float = false
        val info = MediaCodec.BufferInfo()
        var inputDone = false
        var outputDone = false
        try {
            while (!outputDone) {
                if (Thread.currentThread().isInterrupted) throw InterruptedException()
                if (!inputDone) {
                    val input = codec.dequeueInputBuffer(10000)
                    if (input >= 0) {
                        val buffer = codec.getInputBuffer(input)!!
                        val size = extractor.readSampleData(buffer, 0)
                        if (size < 0) {
                            codec.queueInputBuffer(input, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                            inputDone = true
                        } else {
                            codec.queueInputBuffer(input, 0, size, extractor.sampleTime, 0)
                            extractor.advance()
                        }
                    }
                }
                val output = codec.dequeueOutputBuffer(info, 10000)
                if (output >= 0) {
                    val buffer = codec.getOutputBuffer(output)!!.order(ByteOrder.LITTLE_ENDIAN)
                    buffer.position(info.offset)
                    buffer.limit(info.offset + info.size)
                    if (float) {
                        val samples = buffer.asFloatBuffer()
                        while (samples.hasRemaining()) sample(samples.get().toDouble())
                    } else {
                        val samples = buffer.asShortBuffer()
                        while (samples.hasRemaining()) sample(samples.get() / 32768.0)
                    }
                    codec.releaseOutputBuffer(output, false)
                    if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) outputDone = true
                } else if (output == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    val changed = codec.outputFormat
                    configure(changed.getInteger(MediaFormat.KEY_SAMPLE_RATE), changed.getInteger(MediaFormat.KEY_CHANNEL_COUNT))
                    float = changed.containsKey(MediaFormat.KEY_PCM_ENCODING) &&
                        changed.getInteger(MediaFormat.KEY_PCM_ENCODING) == AudioFormat.ENCODING_PCM_FLOAT
                }
            }
        } finally {
            codec.stop()
            codec.release()
            extractor.release()
        }
        return JSONObject().apply {
            put("version", 1)
            put("interval", INTERVAL)
            put("frames", rms.size() / 2)
            put("rms", Base64.encodeToString(rms.toByteArray(), Base64.NO_WRAP))
            put("peak", Base64.encodeToString(peak.toByteArray(), Base64.NO_WRAP))
            put("bands", BANDS)
            put("low", LOW)
            put("high", HIGH)
            put("spectrum", Base64.encodeToString(spectrum.toByteArray(), Base64.NO_WRAP))
        }
    }

    private var channel = 0
    private var mono = 0.0

    // interleaved samples, one at a time
    private fun sample(value: Double) {
        val c = min(channel, 1)
        sums[c] += value * value
        peaks[c] = max(peaks[c], kotlin.math.abs(value))
        mono += value
        channel++
        if (channel < channels) return
        if (channels == 1) {         // mono: both meters the same
            sums[1] += value * value
            peaks[1] = max(peaks[1], kotlin.math.abs(value))
        }
        ring[ringAt] = mono / channels
        ringAt = (ringAt + 1) % FFT
        mono = 0.0
        channel = 0
        counted++
        if (counted >= perFrame) frame()
    }

    private fun frame() {
        for (c in 0..1) {
            val meanSquare = sums[c] / counted
            rms.write(byte(if (meanSquare > 0) 10 * log10(meanSquare) else Double.NaN, FLOOR))
            peak.write(byte(if (peaks[c] > 0) 20 * log10(peaks[c]) else Double.NaN, FLOOR))
            sums[c] = 0.0
            peaks[c] = 0.0
        }
        counted = 0
        // the last FFT samples, windowed
        for (i in 0 until FFT) {
            re[i] = ring[(ringAt + i) % FFT] * window[i]
            im[i] = 0.0
        }
        fft()
        val width = rate / 2.0 / (FFT / 2)
        val magnitudes = DoubleArray(FFT / 2) {
            val amplitude = 2 * sqrt(re[it] * re[it] + im[it] * im[it]) / windowSum
            if (amplitude > 0) 20 * log10(amplitude) else SPECTRUM_FLOOR
        }
        for (band in 0 until BANDS) {
            val i0 = edges[band] / width
            val i1 = edges[band + 1] / width
            val db = if (i1 - i0 < 1) {
                val centre = min(magnitudes.size - 1.001, max(0.0, (i0 + i1) / 2 - 0.5))
                val below = floor(centre).toInt()
                magnitudes[below] + (magnitudes[below + 1] - magnitudes[below]) * (centre - below)
            } else {
                var strongest = SPECTRUM_FLOOR
                for (i in i0.toInt() until min(magnitudes.size, ceil(i1).toInt())) strongest = max(strongest, magnitudes[i])
                strongest
            }
            spectrum.write(byte(db, SPECTRUM_FLOOR))
        }
    }

    // in place, radix 2
    private fun fft() {
        var j = 0
        for (i in 1 until FFT) {
            var bit = FFT shr 1
            while (j and bit != 0) {
                j = j xor bit
                bit = bit shr 1
            }
            j = j xor bit
            if (i < j) {
                var t = re[i]; re[i] = re[j]; re[j] = t
                t = im[i]; im[i] = im[j]; im[j] = t
            }
        }
        var length = 2
        while (length <= FFT) {
            val angle = -2 * PI / length
            val wr = cos(angle)
            val wi = sin(angle)
            var start = 0
            while (start < FFT) {
                var cr = 1.0
                var ci = 0.0
                for (k in 0 until length / 2) {
                    val a = start + k
                    val b = a + length / 2
                    val tr = re[b] * cr - im[b] * ci
                    val ti = re[b] * ci + im[b] * cr
                    re[b] = re[a] - tr
                    im[b] = im[a] - ti
                    re[a] += tr
                    im[a] += ti
                    val next = cr * wr - ci * wi
                    ci = cr * wi + ci * wr
                    cr = next
                }
                start += length
            }
            length = length shl 1
        }
    }
}

class MediaLibraryPackage : ReactPackage {
    override fun createNativeModules(context: ReactApplicationContext): List<NativeModule> = listOf(MediaLibraryModule(context))

    override fun createViewManagers(context: ReactApplicationContext): List<ViewManager<*, *>> = emptyList()
}
