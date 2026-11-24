// feedio: the AG Podcast Mixer driver's hidden Feed devices, opened by their AudioDeviceID, I/O in C.
//
// AG CuteMusic (cutemusic/hal.py, through ctypes) plays its mix to Blink and to "AG Podcast Streaming" into a Feed's
// output, and reads what Blink plays from the Feed's input. GStreamer's osxaudio elements open only the devices the
// system lists, and the driver hides the Feeds (macos/driver/AGPodcastMixer.cpp), so the I/O is ours: a CoreAudio
// IOProc per device, entirely in C, on CoreAudio's real-time thread (no Python there, no lock, no allocation), and a
// lock-free single-producer single-consumer ring between it and Python's thread.
//
// - output: feedio_write() (Python's thread) puts S16LE stereo frames into the ring, as float32; the IOProc takes
//   them, silence when there are none. A write that would queue more than max_frames is refused (the frames
//   dropped): the latency never grows past it.
// - input: the IOProc puts what the device gives into the ring; feedio_read() (Python's thread) takes it out, as
//   S16LE (the link's format), or float32 (feedio_read_float).
//
// The driver's streams: 48 kHz, 2 channels, float32 interleaved, one buffer.

#include <CoreAudio/CoreAudio.h>

#include <stdatomic.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define CHANNELS 2

typedef struct {
    float *data;                    // capacity frames of CHANNELS floats
    uint32_t capacity;              // a power of two
    _Atomic uint64_t head;          // frames written (the producer's)
    _Atomic uint64_t tail;          // frames read (the consumer's)
} ring_t;

typedef struct {
    AudioObjectID device;
    AudioDeviceIOProcID proc;
    int output;
    uint32_t max_frames;            // output: the most queued
    ring_t ring;
    _Atomic uint64_t underruns;     // output: cycles with too little (silence filled in)
    _Atomic uint64_t overruns;      // input: frames lost (Python too slow)
} feedio_t;

static uint32_t ring_count(ring_t *ring)
{
    return (uint32_t)(atomic_load_explicit(&ring->head, memory_order_acquire) -
                      atomic_load_explicit(&ring->tail, memory_order_acquire));
}

static uint32_t ring_put(ring_t *ring, const float *frames, uint32_t count)
{
    uint64_t head = atomic_load_explicit(&ring->head, memory_order_relaxed);
    uint64_t tail = atomic_load_explicit(&ring->tail, memory_order_acquire);
    uint32_t room = ring->capacity - (uint32_t)(head - tail);
    if (count > room)
        count = room;
    for (uint32_t done = 0; done < count;) {
        uint32_t index = (uint32_t)((head + done) & (ring->capacity - 1));
        uint32_t chunk = count - done;
        if (chunk > ring->capacity - index)
            chunk = ring->capacity - index;
        memcpy(ring->data + (size_t)index * CHANNELS, frames + (size_t)done * CHANNELS, (size_t)chunk * CHANNELS * sizeof(float));
        done += chunk;
    }
    atomic_store_explicit(&ring->head, head + count, memory_order_release);
    return count;
}

static uint32_t ring_take(ring_t *ring, float *frames, uint32_t count)
{
    uint64_t tail = atomic_load_explicit(&ring->tail, memory_order_relaxed);
    uint64_t head = atomic_load_explicit(&ring->head, memory_order_acquire);
    uint32_t have = (uint32_t)(head - tail);
    if (count > have)
        count = have;
    for (uint32_t done = 0; done < count;) {
        uint32_t index = (uint32_t)((tail + done) & (ring->capacity - 1));
        uint32_t chunk = count - done;
        if (chunk > ring->capacity - index)
            chunk = ring->capacity - index;
        memcpy(frames + (size_t)done * CHANNELS, ring->data + (size_t)index * CHANNELS, (size_t)chunk * CHANNELS * sizeof(float));
        done += chunk;
    }
    atomic_store_explicit(&ring->tail, tail + count, memory_order_release);
    return count;
}

static OSStatus io_proc(AudioObjectID device, const AudioTimeStamp *now, const AudioBufferList *input,
                        const AudioTimeStamp *input_time, AudioBufferList *output, const AudioTimeStamp *output_time,
                        void *client)
{
    feedio_t *io = (feedio_t *)client;
    (void)device; (void)now; (void)input_time; (void)output_time;
    if (io->output && output != NULL) {
        for (UInt32 b = 0; b < output->mNumberBuffers; b++) {
            AudioBuffer *buffer = &output->mBuffers[b];
            uint32_t frames = buffer->mDataByteSize / (CHANNELS * sizeof(float));
            uint32_t got = b == 0 ? ring_take(&io->ring, (float *)buffer->mData, frames) : 0;
            if (got < frames) {
                memset((float *)buffer->mData + (size_t)got * CHANNELS, 0, (size_t)(frames - got) * CHANNELS * sizeof(float));
                if (b == 0 && got > 0)
                    atomic_fetch_add_explicit(&io->underruns, 1, memory_order_relaxed);
            }
        }
    } else if (!io->output && input != NULL && input->mNumberBuffers > 0) {
        const AudioBuffer *buffer = &input->mBuffers[0];
        uint32_t frames = buffer->mDataByteSize / (CHANNELS * sizeof(float));
        uint32_t put = ring_put(&io->ring, (const float *)buffer->mData, frames);
        if (put < frames)
            atomic_fetch_add_explicit(&io->overruns, frames - put, memory_order_relaxed);
    }
    return noErr;
}

static uint32_t power_of_two(uint32_t value)
{
    uint32_t result = 1;
    while (result < value)
        result <<= 1;
    return result;
}

// Opened and started: an IOProc on device, output (1) or input (0), its cycle io_frames (0: the device's), its ring
// ring_frames, at most max_frames queued for output (0: the ring); NULL and *status on failure.
feedio_t *feedio_open(uint32_t device, int output, uint32_t io_frames, uint32_t ring_frames, uint32_t max_frames,
                      int32_t *status)
{
    OSStatus result;
    feedio_t *io = calloc(1, sizeof(*io));
    if (io == NULL) {
        *status = -1;
        return NULL;
    }
    io->device = device;
    io->output = output;
    io->ring.capacity = power_of_two(ring_frames < 1024 ? 1024 : ring_frames);
    io->max_frames = max_frames && max_frames < io->ring.capacity ? max_frames : io->ring.capacity;
    io->ring.data = calloc((size_t)io->ring.capacity * CHANNELS, sizeof(float));
    if (io->ring.data == NULL) {
        free(io);
        *status = -1;
        return NULL;
    }
    if (io_frames) {
        AudioObjectPropertyAddress address = {kAudioDevicePropertyBufferFrameSize,
                                              output ? kAudioObjectPropertyScopeOutput : kAudioObjectPropertyScopeInput,
                                              kAudioObjectPropertyElementMain};
        UInt32 size = io_frames;
        AudioObjectSetPropertyData(device, &address, 0, NULL, sizeof(size), &size);
    }
    result = AudioDeviceCreateIOProcID(device, io_proc, io, &io->proc);
    if (result == noErr) {
        result = AudioDeviceStart(device, io->proc);
        if (result != noErr)
            AudioDeviceDestroyIOProcID(device, io->proc);
    }
    if (result != noErr) {
        free(io->ring.data);
        free(io);
        *status = result;
        return NULL;
    }
    *status = 0;
    return io;
}

void feedio_close(feedio_t *io)
{
    if (io == NULL)
        return;
    AudioDeviceStop(io->device, io->proc);
    AudioDeviceDestroyIOProcID(io->device, io->proc);
    free(io->ring.data);
    free(io);
}

// output: S16LE stereo frames queued, as float32; the number queued: all, or 0 when that would queue more than
// max_frames (dropped whole: the latency kept)
uint32_t feedio_write(feedio_t *io, const int16_t *samples, uint32_t frames)
{
    float converted[1024 * CHANNELS];
    uint32_t done = 0;
    if (ring_count(&io->ring) + frames > io->max_frames)
        return 0;
    while (done < frames) {
        uint32_t chunk = frames - done > 1024 ? 1024 : frames - done;
        for (uint32_t i = 0; i < chunk * CHANNELS; i++)
            converted[i] = samples[(size_t)done * CHANNELS + i] * (1.0f / 32768.0f);
        done += ring_put(&io->ring, converted, chunk);
    }
    return done;
}

// input: up to max frames taken out, as S16LE stereo; the number taken
uint32_t feedio_read(feedio_t *io, int16_t *samples, uint32_t max)
{
    float block[1024 * CHANNELS];
    uint32_t done = 0;
    while (done < max) {
        uint32_t chunk = max - done > 1024 ? 1024 : max - done;
        uint32_t got = ring_take(&io->ring, block, chunk);
        for (uint32_t i = 0; i < got * CHANNELS; i++) {
            float value = block[i] * 32768.0f;
            samples[(size_t)done * CHANNELS + i] = value >= 32767.0f ? 32767 : value <= -32768.0f ? -32768 : (int16_t)value;
        }
        done += got;
        if (got < chunk)
            break;
    }
    return done;
}

// frames queued: output, still to play; input, waiting to be read
uint32_t feedio_queued(feedio_t *io)
{
    return ring_count(&io->ring);
}

uint64_t feedio_underruns(feedio_t *io)
{
    return atomic_load_explicit(&io->underruns, memory_order_relaxed);
}

uint64_t feedio_overruns(feedio_t *io)
{
    return atomic_load_explicit(&io->overruns, memory_order_relaxed);
}

// dst += src, s16 saturated (the mixers' sum: link.py's _mix, without audioop on Python 3.13+)
void feedio_mix(int16_t *dst, const int16_t *src, uint32_t samples)
{
    for (uint32_t i = 0; i < samples; i++) {
        int32_t sum = (int32_t)dst[i] + (int32_t)src[i];
        dst[i] = (int16_t)(sum > 32767 ? 32767 : sum < -32768 ? -32768 : sum);
    }
}
