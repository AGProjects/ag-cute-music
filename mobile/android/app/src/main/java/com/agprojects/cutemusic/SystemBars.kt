package com.agprojects.cutemusic

// Full screen as a video call's (src/Amp.tsx, landscape): the status bar and the navigation bar (the Android
// buttons) hidden, back for a moment with a swipe from the edge; shown again when asked.

import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.facebook.react.ReactPackage
import com.facebook.react.bridge.NativeModule
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.UiThreadUtil
import com.facebook.react.uimanager.ViewManager

class SystemBarsModule(context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {

    override fun getName() = "SystemBars"

    @ReactMethod
    fun setImmersive(on: Boolean) {
        val activity = reactApplicationContext.currentActivity ?: return
        UiThreadUtil.runOnUiThread {
            val window = activity.window ?: return@runOnUiThread
            val controller = WindowCompat.getInsetsController(window, window.decorView)
            if (on) {
                controller.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
                controller.hide(WindowInsetsCompat.Type.systemBars())
            } else {
                controller.show(WindowInsetsCompat.Type.systemBars())
            }
        }
    }
}

class SystemBarsPackage : ReactPackage {
    override fun createNativeModules(context: ReactApplicationContext): List<NativeModule> = listOf(SystemBarsModule(context))

    override fun createViewManagers(context: ReactApplicationContext): List<ViewManager<*, *>> = emptyList()
}
