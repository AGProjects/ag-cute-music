package com.agprojects.cutemusic

// Where the music plays (src/output.ts): the system's output switcher opened (the speaker, headphones, USB,
// Bluetooth, cast devices: Android chooses, for every app), and the name of the output media goes to now.

import android.content.Intent
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.media.MediaRouter2
import android.os.Build
import android.provider.Settings
import com.facebook.react.ReactPackage
import com.facebook.react.bridge.NativeModule
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.UiThreadUtil
import com.facebook.react.uimanager.ViewManager

class AudioOutputModule(context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {

    override fun getName() = "AudioOutput"

    @ReactMethod
    fun showPicker() {
        val context = reactApplicationContext
        UiThreadUtil.runOnUiThread {
            if (Build.VERSION.SDK_INT >= 34) {
                try {
                    if (MediaRouter2.getInstance(context).showSystemOutputSwitcher()) return@runOnUiThread
                } catch (e: Exception) {
                }
            }
            // Android 11-13: the media output panel; before: the Bluetooth settings
            val intent = if (Build.VERSION.SDK_INT >= 30) {
                Intent("android.settings.panel.action.MEDIA_OUTPUT")
                    .putExtra("com.android.settings.panel.extra.PACKAGE_NAME", context.packageName)
            } else {
                Intent(Settings.ACTION_BLUETOOTH_SETTINGS)
            }
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            try {
                (context.currentActivity ?: context).startActivity(intent)
            } catch (e: Exception) {
                try {
                    context.startActivity(Intent(Settings.ACTION_SOUND_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                } catch (e: Exception) {
                }
            }
        }
    }

    // The output media plays on now, by name ("Phone speaker", "Headphones", a Bluetooth device's own name)
    @ReactMethod
    fun current(promise: Promise) {
        try {
            val audio = reactApplicationContext.getSystemService(AudioManager::class.java)!!
            val device: AudioDeviceInfo? = if (Build.VERSION.SDK_INT >= 33) {
                val media = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).build()
                audio.getAudioDevicesForAttributes(media).firstOrNull()?.let { attributes ->
                    audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS).firstOrNull {
                        it.type == attributes.type && (attributes.address.isEmpty() || it.address == attributes.address)
                    }
                } ?: likeliest(audio)
            } else {
                likeliest(audio)
            }
            promise.resolve(device?.let { label(it) } ?: "Phone speaker")
        } catch (e: Exception) {
            promise.resolve(null)
        }
    }

    // (before Android 13: no asking where media goes) what Android routes to when it is connected
    private fun likeliest(audio: AudioManager): AudioDeviceInfo? {
        val rank = listOf(
            AudioDeviceInfo.TYPE_BLUETOOTH_A2DP, AudioDeviceInfo.TYPE_BLE_HEADSET, AudioDeviceInfo.TYPE_BLE_SPEAKER,
            AudioDeviceInfo.TYPE_USB_HEADSET, AudioDeviceInfo.TYPE_USB_DEVICE, AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
            AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_HDMI, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER,
        )
        return audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
            .filter { it.type in rank }
            .minByOrNull { rank.indexOf(it.type) }
    }

    private fun label(device: AudioDeviceInfo): String {
        val name = device.productName?.toString()?.takeIf { it.isNotBlank() && it != Build.MODEL }
        return when (device.type) {
            AudioDeviceInfo.TYPE_BUILTIN_SPEAKER, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE -> "Phone speaker"
            AudioDeviceInfo.TYPE_WIRED_HEADPHONES, AudioDeviceInfo.TYPE_WIRED_HEADSET -> "Headphones"
            AudioDeviceInfo.TYPE_USB_DEVICE, AudioDeviceInfo.TYPE_USB_HEADSET -> name ?: "USB audio"
            AudioDeviceInfo.TYPE_HDMI -> name ?: "HDMI"
            else -> name ?: "Bluetooth"
        }
    }
}

class AudioOutputPackage : ReactPackage {
    override fun createNativeModules(context: ReactApplicationContext): List<NativeModule> = listOf(AudioOutputModule(context))

    override fun createViewManagers(context: ReactApplicationContext): List<ViewManager<*, *>> = emptyList()
}
