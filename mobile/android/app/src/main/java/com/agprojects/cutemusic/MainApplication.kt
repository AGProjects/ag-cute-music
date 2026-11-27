package com.agprojects.cutemusic

import android.app.Application
import com.facebook.react.PackageList
import com.facebook.react.ReactApplication
import com.facebook.react.ReactHost
import com.facebook.react.ReactNativeApplicationEntryPoint.loadReactNative
import com.facebook.react.defaults.DefaultReactHost.getDefaultReactHost

class MainApplication : Application(), ReactApplication {

  override val reactHost: ReactHost by lazy {
    getDefaultReactHost(
      context = applicationContext,
      packageList =
        PackageList(this).packages.apply {
          add(MediaLibraryPackage())      // the phone's own music (MediaLibrary.kt)
          add(SystemBarsPackage())        // full screen in landscape (SystemBars.kt)
          add(AudioOutputPackage())       // where the music plays (AudioOutput.kt)
        },
    )
  }

  override fun onCreate() {
    super.onCreate()
    loadReactNative(this)
  }
}
