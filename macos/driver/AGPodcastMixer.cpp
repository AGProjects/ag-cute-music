// AG Podcast Mixer: an AudioServerPlugIn (CoreAudio user-space driver), built on libASPL.
//
// python3-sipsimple docs/PodcastMixerLink.md, v2: two devices joined by two rings. AG CuteMusic plays its mix into
// "AG Podcast Mixer Feed" (opened by UID) and reads Blink's call from it; Blink uses "AG Podcast Mixer" as its sound
// device, in and out: the mix is its microphone, the call it plays goes to AG CuteMusic's BLINK strip. The driver only
// moves the data: no mixing, no conversion (48 kHz, stereo, float32 everywhere).
//
// - One clock for both devices (Clock: zero timestamps from one anchor, taken when coreaudiod loads the driver), so
//   there is no drift between AG CuteMusic writing and Blink reading: what the Feed is given for sample time T is
//   what the input gives for sample time T. The HAL writes an output ahead of its "now" and reads an input behind
//   it, so a frame is written before it is read; the latency is the two IO buffers.
// - One ring (Loop), indexed by sample time. The reader gets silence for any frame that was not written (the Feed
//   not running, a gap), never stale data.
// - "AG Podcast Mixer" is always there: Blink is set to it once and left so; while AG CuteMusic's mixer is off (the
//   Feed not playing) it reads silence, and the mix as soon as the mixer comes up, Blink started before or after.
// - A control channel between the Blinks and AG CuteMusic, as two custom properties (CFString) of "AG Podcast Mixer":
//   the only way a sandboxed Blink (Blink Pro, App Store) can reach another app, and the same for Blink classic.
//   'agbc' (Blink calls): each Blink sets "<pid> <json>" every 2 s (its calls, its redial target), "<pid>" alone as it
//   quits; read, it is one JSON object {"<pid>": <json>, ...}, an entry not set again for kBlinkStaleSeconds left
//   out (a Blink that crashed). 'agrq' (request): AG CuteMusic sets one JSON request ({"seq", "pid", "op": "hangup"
//   with "id", or "redial"}), read back as set; the Blink it names acts on a seq it has not seen. Both notify their
//   listeners when set.
// - Streaming (AG CuteMusic's RECORDER, STREAMING On): "AG Podcast Streaming", an input with what the recorder hears
//   (every strip mixed) for a streaming app (OBS...), and "AG Podcast Streaming Feed", the output AG CuteMusic plays
//   that mix into, joined by a ring of their own. Both there only while streaming: 'agst' ("1", "0") on the Feed
//   device adds or removes them. Not for Blink (it leaves them out of its lists).
// - Hidden from the system's device list (kAudioDevicePropertyIsHidden), so that other apps never offer them: "AG
//   Podcast Mixer" (Blink's SDK lists it by its UID: coreaudio_dev.m), the two Feeds (AG CuteMusic opens them by
//   UID: hal.py). Only "AG Podcast Streaming" is listed, for any app, while streaming. "AG Podcast Mixer" is listed
//   too while 'agvs' ("1", "0") on the Feed device says so: AG CuteMusic sets it while Sylk Desktop runs (Electron's
//   Chromium opens only listed devices), back to "0" when it quits or AG CuteMusic does.

#include <CoreFoundation/CoreFoundation.h>

#include <aspl/Driver.hpp>

#include <CoreAudio/AudioServerPlugIn.h>
#include <mach/mach_time.h>

#include <algorithm>
#include <atomic>
#include <cstring>
#include <functional>
#include <map>
#include <mutex>
#include <string>

namespace {

constexpr UInt32 kRate = 48000;
constexpr UInt32 kChannels = 2;
constexpr UInt32 kBytesPerFrame = kChannels * sizeof(Float32);
constexpr UInt32 kRingFrames = 16384;           // a power of two: 341 ms; also the zero timestamp period

constexpr const char* kInputName = "AG Podcast Mixer";
constexpr const char* kFeedName = "AG Podcast Mixer Feed";
constexpr const char* kInputUID = "com.ag-projects.podcast-mixer.input";
constexpr const char* kFeedUID = "com.ag-projects.podcast-mixer.feed";
constexpr const char* kModelUID = "com.ag-projects.podcast-mixer";
constexpr const char* kManufacturer = "AG Projects";
constexpr const char* kAppBundleID = "com.ag-projects.ag-cute-music";

constexpr const char* kStreamName = "AG Podcast Streaming";
constexpr const char* kStreamFeedName = "AG Podcast Streaming Feed";
constexpr const char* kStreamUID = "com.ag-projects.podcast-mixer.streaming";
constexpr const char* kStreamFeedUID = "com.ag-projects.podcast-mixer.streaming-feed";

constexpr AudioObjectPropertySelector kStreamingProperty = 'agst';
constexpr AudioObjectPropertySelector kBlinkCallsProperty = 'agbc';
constexpr AudioObjectPropertySelector kRequestProperty = 'agrq';
constexpr AudioObjectPropertySelector kVisibleProperty = 'agvs';
constexpr CFAbsoluteTime kBlinkStaleSeconds = 10.0;

std::string FromCFString(CFStringRef value)
{
    if (value == nullptr) {
        return std::string();
    }
    const CFIndex size = CFStringGetMaximumSizeForEncoding(CFStringGetLength(value), kCFStringEncodingUTF8) + 1;
    std::string text(size_t(size), '\0');
    if (!CFStringGetCString(value, &text[0], size, kCFStringEncodingUTF8)) {
        return std::string();
    }
    text.resize(strlen(text.c_str()));
    return text;
}

CFStringRef ToCFString(const std::string& text)
{
    CFStringRef value = CFStringCreateWithCString(kCFAllocatorDefault, text.c_str(), kCFStringEncodingUTF8);
    return value != nullptr ? value : CFStringCreateWithCString(kCFAllocatorDefault, "", kCFStringEncodingUTF8);
}

AudioStreamBasicDescription StreamFormat()
{
    AudioStreamBasicDescription format = {};
    format.mSampleRate = kRate;
    format.mFormatID = kAudioFormatLinearPCM;
    format.mFormatFlags = kAudioFormatFlagIsFloat | kAudioFormatFlagsNativeEndian | kAudioFormatFlagIsPacked;
    format.mBytesPerPacket = kBytesPerFrame;
    format.mFramesPerPacket = 1;
    format.mBytesPerFrame = kBytesPerFrame;
    format.mChannelsPerFrame = kChannels;
    format.mBitsPerChannel = 32;
    return format;
}

// The two devices' clock: a period of kRingFrames from one anchor, never reset.
class Clock
{
public:
    Clock()
    {
        mach_timebase_info_data_t timebase;
        mach_timebase_info(&timebase);
        const Float64 hostTicksPerSecond = Float64(timebase.denom) / Float64(timebase.numer) * 1000000000.0;
        ticksPerPeriod_ = hostTicksPerSecond / kRate * kRingFrames;
        anchor_ = mach_absolute_time();
    }

    void ZeroTimeStamp(Float64* sampleTime, UInt64* hostTime, UInt64* seed) const
    {
        const UInt64 now = mach_absolute_time();
        const UInt64 periods = UInt64(Float64(now - anchor_) / ticksPerPeriod_);
        *sampleTime = Float64(periods) * kRingFrames;
        *hostTime = anchor_ + UInt64(Float64(periods) * ticksPerPeriod_);
        *seed = 1;
    }

private:
    UInt64 anchor_ = 0;
    Float64 ticksPerPeriod_ = 0;
};

// The ring between the Feed (one writer: the HAL's mixed output) and the input (any number of readers), indexed by
// sample time. [begin, end) is what holds written frames; the rest reads as silence.
class Loop
{
public:
    void Write(Float64 sampleTime, const Float32* frames, UInt32 count)
    {
        const SInt64 start = SInt64(sampleTime);
        const SInt64 end = end_.load(std::memory_order_acquire);
        // a new run (the Feed started again, skipped ahead or went back): nothing before it is valid. Stored in the
        // order that keeps [begin, end) empty in between for a reader
        if (start > end) {
            begin_.store(start, std::memory_order_release);
            end_.store(start, std::memory_order_release);
        } else if (start < begin_.load(std::memory_order_relaxed)) {
            end_.store(start, std::memory_order_release);
            begin_.store(start, std::memory_order_release);
        }
        Copy(frames, start, count);
        if (start + SInt64(count) > end_.load(std::memory_order_relaxed)) {
            end_.store(start + count, std::memory_order_release);
        }
    }

    void Read(Float64 sampleTime, Float32* out, UInt32 count) const
    {
        const SInt64 start = SInt64(sampleTime);
        const SInt64 end = end_.load(std::memory_order_acquire);
        const SInt64 begin = std::max(begin_.load(std::memory_order_acquire), end - SInt64(kRingFrames));
        const SInt64 from = std::max(start, begin);
        const SInt64 to = std::min(start + SInt64(count), end);
        if (from >= to) {
            memset(out, 0, size_t(count) * kBytesPerFrame);
            return;
        }
        if (from > start) {
            memset(out, 0, size_t(from - start) * kBytesPerFrame);
        }
        Paste(out + (from - start) * kChannels, from, UInt32(to - from));
        if (to < start + SInt64(count)) {
            memset(out + (to - start) * kChannels, 0, size_t(start + count - to) * kBytesPerFrame);
        }
    }

    // The Feed stopped: everything reads as silence until it writes again.
    void Clear()
    {
        begin_.store(end_.load(std::memory_order_acquire), std::memory_order_release);
    }

private:
    void Copy(const Float32* frames, SInt64 start, UInt32 count)
    {
        UInt32 index = UInt32(start & (kRingFrames - 1));
        while (count > 0) {
            const UInt32 chunk = std::min(count, kRingFrames - index);
            memcpy(&ring_[index * kChannels], frames, size_t(chunk) * kBytesPerFrame);
            frames += chunk * kChannels;
            count -= chunk;
            index = 0;
        }
    }

    void Paste(Float32* out, SInt64 start, UInt32 count) const
    {
        UInt32 index = UInt32(start & (kRingFrames - 1));
        while (count > 0) {
            const UInt32 chunk = std::min(count, kRingFrames - index);
            memcpy(out, &ring_[index * kChannels], size_t(chunk) * kBytesPerFrame);
            out += chunk * kChannels;
            count -= chunk;
            index = 0;
        }
    }

    Float32 ring_[kRingFrames * kChannels] = {};
    std::atomic<SInt64> begin_ {0};
    std::atomic<SInt64> end_ {0};
};

// A device on the shared clock, at 48 kHz only.
class MixerDevice : public aspl::Device
{
public:
    MixerDevice(std::shared_ptr<const aspl::Context> context,
        const aspl::DeviceParameters& params,
        std::shared_ptr<const Clock> clock)
        : aspl::Device(std::move(context), params)
        , clock_(std::move(clock))
    {
    }

protected:
    OSStatus GetZeroTimeStampImpl(UInt32 clientID, Float64* outSampleTime, UInt64* outHostTime, UInt64* outSeed) override
    {
        clock_->ZeroTimeStamp(outSampleTime, outHostTime, outSeed);
        return kAudioHardwareNoError;
    }

    OSStatus SetNominalSampleRateImpl(Float64 rate) override
    {
        return rate == kRate ? aspl::Device::SetNominalSampleRateImpl(rate) : kAudioHardwareUnsupportedOperationError;
    }

private:
    std::shared_ptr<const Clock> clock_;
};

// "AG Podcast Mixer": Blink's device, which also carries the control channel ('agbc', 'agrq').
class BlinkDevice : public MixerDevice
{
public:
    BlinkDevice(std::shared_ptr<const aspl::Context> context,
        const aspl::DeviceParameters& params,
        std::shared_ptr<const Clock> clock)
        : MixerDevice(std::move(context), params, std::move(clock))
    {
        RegisterCustomProperty(kBlinkCallsProperty, *this, &BlinkDevice::GetBlinkCalls, &BlinkDevice::SetBlinkCalls);
        RegisterCustomProperty(kRequestProperty, *this, &BlinkDevice::GetRequest, &BlinkDevice::SetRequest);
    }

    CFStringRef GetBlinkCalls() const
    {
        const CFAbsoluteTime now = CFAbsoluteTimeGetCurrent();
        std::string text = "{";
        std::lock_guard<std::mutex> lock(mutex_);
        for (const auto& entry : blinks_) {
            if (now - entry.second.second > kBlinkStaleSeconds) {
                continue;
            }
            if (text.size() > 1) {
                text += ",";
            }
            text += "\"" + entry.first + "\":" + entry.second.first;
        }
        return ToCFString(text + "}");
    }

    void SetBlinkCalls(CFStringRef value)
    {
        // "<pid> <json>", or "<pid>" alone: that Blink gone
        const std::string text = FromCFString(value);
        const size_t space = text.find(' ');
        const std::string pid = text.substr(0, space);
        if (pid.empty() || pid.find_first_not_of("0123456789") != std::string::npos) {
            return;
        }
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (space == std::string::npos || text.find_first_not_of(' ', space) == std::string::npos) {
                blinks_.erase(pid);
            } else {
                blinks_[pid] = std::make_pair(text.substr(space + 1), CFAbsoluteTimeGetCurrent());
            }
        }
        NotifyPropertyChanged(kBlinkCallsProperty);
    }

    CFStringRef GetRequest() const
    {
        std::lock_guard<std::mutex> lock(mutex_);
        return ToCFString(request_);
    }

    void SetRequest(CFStringRef value)
    {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            request_ = FromCFString(value);
        }
        NotifyPropertyChanged(kRequestProperty);
    }

private:
    mutable std::mutex mutex_;
    std::map<std::string, std::pair<std::string, CFAbsoluteTime>> blinks_;     // pid: (json, when set)
    std::string request_;
};

// "AG Podcast Mixer Feed": AG CuteMusic's device, which also switches streaming ('agst': the two streaming devices
// added to the plug-in, or removed).
class FeedDevice : public MixerDevice
{
public:
    FeedDevice(std::shared_ptr<const aspl::Context> context,
        const aspl::DeviceParameters& params,
        std::shared_ptr<const Clock> clock,
        std::function<void(bool)> streaming,
        std::function<void(bool)> visible)
        : MixerDevice(std::move(context), params, std::move(clock))
        , streaming_(std::move(streaming))
        , visible_(std::move(visible))
    {
        RegisterCustomProperty(kStreamingProperty, *this, &FeedDevice::GetStreaming, &FeedDevice::SetStreaming);
        RegisterCustomProperty(kVisibleProperty, *this, &FeedDevice::GetVisible, &FeedDevice::SetVisible);
    }

    CFStringRef GetVisible() const
    {
        return ToCFString(shown_.load() ? "1" : "0");
    }

    void SetVisible(CFStringRef value)
    {
        const bool on = FromCFString(value) == "1";
        if (shown_.exchange(on) == on) {
            return;
        }
        visible_(on);
        NotifyPropertyChanged(kVisibleProperty);
    }

    CFStringRef GetStreaming() const
    {
        return ToCFString(on_.load() ? "1" : "0");
    }

    void SetStreaming(CFStringRef value)
    {
        const bool on = FromCFString(value) == "1";
        if (on_.exchange(on) == on) {
            return;
        }
        streaming_(on);
        NotifyPropertyChanged(kStreamingProperty);
    }

private:
    std::function<void(bool)> streaming_;
    std::function<void(bool)> visible_;
    std::atomic<bool> on_ {false};
    std::atomic<bool> shown_ {false};
};

// Each device's end of the two rings: it reads one (its input) and writes the other (its output). "AG Podcast Mixer"
// (Blink's) reads the mix and writes the call; "AG Podcast Mixer Feed" (AG CuteMusic's) writes the mix and reads the
// call. A side that stops: what it wrote reads as silence on the other.
class EndHandler : public aspl::ControlRequestHandler, public aspl::IORequestHandler
{
public:
    EndHandler(std::shared_ptr<Loop> reads, std::shared_ptr<Loop> writes)
        : reads_(std::move(reads))
        , writes_(std::move(writes))
    {
    }

    void OnStopIO() override
    {
        writes_->Clear();
    }

    void OnReadClientInput(const std::shared_ptr<aspl::Client>& client,
        const std::shared_ptr<aspl::Stream>& stream,
        Float64 zeroTimestamp,
        Float64 timestamp,
        void* bytes,
        UInt32 bytesCount) override
    {
        reads_->Read(timestamp, static_cast<Float32*>(bytes), bytesCount / kBytesPerFrame);
    }

    void OnWriteMixedOutput(const std::shared_ptr<aspl::Stream>& stream,
        Float64 zeroTimestamp,
        Float64 timestamp,
        const void* bytes,
        UInt32 bytesCount) override
    {
        writes_->Write(timestamp, static_cast<const Float32*>(bytes), bytesCount / kBytesPerFrame);
    }

private:
    std::shared_ptr<Loop> reads_;
    std::shared_ptr<Loop> writes_;
};

aspl::DeviceParameters Parameters(const char* name, const char* uid, bool canBeDefault)
{
    aspl::DeviceParameters params;
    params.Name = name;
    params.Manufacturer = kManufacturer;
    params.DeviceUID = uid;
    params.ModelUID = kModelUID;
    params.ConfigurationApplicationBundleID = kAppBundleID;
    params.CanBeDefault = canBeDefault;
    params.CanBeDefaultForSystemSounds = false;
    params.SampleRate = kRate;
    params.ChannelCount = kChannels;
    params.ZeroTimeStampPeriod = kRingFrames;
    params.EnableMixing = true;
    return params;
}

aspl::StreamParameters StreamParams(aspl::Direction direction)
{
    aspl::StreamParameters params;
    params.Direction = direction;
    params.Format = StreamFormat();
    return params;
}

std::shared_ptr<aspl::Driver> CreateDriver()
{
    // libASPL's tracing (every HAL call) to syslog only in a build made with build.sh --trace
#ifdef AGPM_TRACE
    auto tracer = std::make_shared<aspl::Tracer>(aspl::Tracer::Mode::Syslog);
#else
    auto tracer = std::make_shared<aspl::Tracer>(aspl::Tracer::Mode::Noop);
#endif
    auto context = std::make_shared<aspl::Context>(tracer);
    auto clock = std::make_shared<const Clock>();
    auto mix = std::make_shared<Loop>();        // AG CuteMusic's mix, to Blink
    auto call = std::make_shared<Loop>();       // Blink's call, to AG CuteMusic (its BLINK strip)

    auto plugin = std::make_shared<aspl::Plugin>(context);

    // Blink's device, in and out: always there (silence while the mixer is off; what Blink plays goes nowhere then).
    // Not a default device: chosen by hand, in Blink
    auto mixer = std::make_shared<BlinkDevice>(context, Parameters(kInputName, kInputUID, false), clock);
    mixer->AddStreamAsync(StreamParams(aspl::Direction::Input));
    mixer->AddStreamAsync(StreamParams(aspl::Direction::Output));
    auto mixerEnd = std::make_shared<EndHandler>(mix, call);
    mixer->SetControlHandler(mixerEnd);
    mixer->SetIOHandler(mixerEnd);

    // AG CuteMusic's, in and out. Listed, not hidden: GStreamer's osxaudio opens only devices of
    // kAudioHardwarePropertyDevices, which leaves hidden ones out ("CoreAudio device not found"); not a default device,
    // so the Sound settings do not offer it (Audio MIDI Setup shows it; Blink and AG CuteMusic leave it out)
    // Streaming: the input for a streaming app (it may be a default input: chosen in the Sound settings too) and
    // the output AG CuteMusic plays the recorder's mix into, made now, in the plug-in only while 'agst' is "1"
    auto streamed = std::make_shared<Loop>();       // the recorder's mix, to the streaming app
    auto unused = std::make_shared<Loop>();         // (the other direction of each: nothing)
    auto streamParams = Parameters(kStreamName, kStreamUID, true);
    auto stream = std::make_shared<MixerDevice>(context, streamParams, clock);
    stream->AddStreamAsync(StreamParams(aspl::Direction::Input));
    auto streamEnd = std::make_shared<EndHandler>(streamed, unused);
    stream->SetControlHandler(streamEnd);
    stream->SetIOHandler(streamEnd);
    auto streamFeed = std::make_shared<MixerDevice>(context, Parameters(kStreamFeedName, kStreamFeedUID, false), clock);
    streamFeed->AddStreamAsync(StreamParams(aspl::Direction::Output));
    auto streamFeedEnd = std::make_shared<EndHandler>(unused, streamed);
    streamFeed->SetControlHandler(streamFeedEnd);
    streamFeed->SetIOHandler(streamFeedEnd);
    std::weak_ptr<aspl::Plugin> weakPlugin = plugin;
    auto streaming = [weakPlugin, stream, streamFeed, streamed](bool on) {
        auto plugin = weakPlugin.lock();
        if (!plugin) {
            return;
        }
        if (on) {
            plugin->AddDevice(streamFeed);
            plugin->AddDevice(stream);
        } else {
            plugin->RemoveDevice(stream);
            plugin->RemoveDevice(streamFeed);
            streamed->Clear();
        }
    };

    // "AG Podcast Mixer" listed for every app ('agvs' "1": Sylk Desktop runs) or hidden again; the HAL told that the
    // device and the plug-in's list changed, so its clients list it anew (not removed and added: a Blink using it
    // goes on undisturbed)
    std::weak_ptr<BlinkDevice> weakMixer = mixer;
    auto visible = [weakPlugin, weakMixer](bool on) {
        auto plugin = weakPlugin.lock();
        auto mixer = weakMixer.lock();
        if (!plugin || !mixer) {
            return;
        }
        mixer->SetIsHidden(!on);
        mixer->NotifyPropertyChanged(kAudioDevicePropertyIsHidden);
        plugin->NotifyPropertiesChanged({kAudioPlugInPropertyDeviceList, kAudioObjectPropertyOwnedObjects});
    };

    auto feed = std::make_shared<FeedDevice>(context, Parameters(kFeedName, kFeedUID, false), clock, streaming, visible);
    feed->AddStreamAsync(StreamParams(aspl::Direction::Output));
    feed->AddStreamAsync(StreamParams(aspl::Direction::Input));
    auto feedEnd = std::make_shared<EndHandler>(call, mix);
    feed->SetControlHandler(feedEnd);
    feed->SetIOHandler(feedEnd);

    // hidden: only Blink (by UID) and AG CuteMusic (by UID) use them; "AG Podcast Streaming" alone stays listed
    mixer->SetIsHidden(true);
    feed->SetIsHidden(true);
    streamFeed->SetIsHidden(true);

    plugin->AddDevice(mixer);
    plugin->AddDevice(feed);

    return std::make_shared<aspl::Driver>(context, plugin);
}

} // namespace

extern "C" void* AGPodcastMixer_Create(CFAllocatorRef allocator, CFUUIDRef typeUUID)
{
    if (!CFEqual(typeUUID, kAudioServerPlugInTypeUUID)) {
        return nullptr;
    }
    static std::shared_ptr<aspl::Driver> driver = CreateDriver();
    return driver->GetReference();
}
