// Where the music plays on iOS (src/output.ts): Apple's route picker as a view (AGRoutePicker: the speaker,
// Bluetooth, AirPlay; Apple lets apps show it, not choose for the user), and the name of the route now.

#import <AVFoundation/AVFoundation.h>
#import <AVKit/AVKit.h>
#import <React/RCTBridgeModule.h>
#import <React/RCTConvert.h>
#import <React/RCTViewManager.h>

@interface AGAudioOutput : NSObject <RCTBridgeModule>
@end

@implementation AGAudioOutput

RCT_EXPORT_MODULE(AudioOutput)

+ (BOOL)requiresMainQueueSetup
{
    return NO;
}

RCT_EXPORT_METHOD(current:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
{
    AVAudioSessionPortDescription *port = AVAudioSession.sharedInstance.currentRoute.outputs.firstObject;
    if (port == nil) {
        resolve([NSNull null]);
    } else if ([port.portType isEqualToString:AVAudioSessionPortBuiltInSpeaker] || [port.portType isEqualToString:AVAudioSessionPortBuiltInReceiver]) {
        resolve(@"Speaker");
    } else {
        resolve(port.portName);
    }
}

RCT_EXPORT_METHOD(showPicker)
{
    // (on iOS the picker is the AGRoutePicker view itself: a tap on it)
}

@end

@interface AGRoutePickerManager : RCTViewManager
@end

@implementation AGRoutePickerManager

RCT_EXPORT_MODULE(AGRoutePicker)

- (UIView *)view
{
    AVRoutePickerView *picker = [AVRoutePickerView new];
    picker.prioritizesVideoDevices = NO;
    return picker;
}

RCT_CUSTOM_VIEW_PROPERTY(color, UIColor, AVRoutePickerView)
{
    view.tintColor = json ? [RCTConvert UIColor:json] : defaultView.tintColor;
}

RCT_CUSTOM_VIEW_PROPERTY(activeColor, UIColor, AVRoutePickerView)
{
    view.activeTintColor = json ? [RCTConvert UIColor:json] : defaultView.activeTintColor;
}

@end
