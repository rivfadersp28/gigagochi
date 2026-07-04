const PET_SPEECH_AUDIO_URLS = [
  "/speech/speech_1.wav",
  "/speech/speech_2.wav",
  "/speech/speech_3.wav",
  "/speech/speech_4.wav",
];

const PET_SPEECH_GAIN = 0.32;
const PET_SPEECH_STEP_SECONDS = 0.048;
const PET_SPEECH_START_OFFSET_SECONDS = 0.015;

type BrowserAudioWindow = Window &
  typeof globalThis & {
    webkitAudioContext?: typeof AudioContext;
  };

let speechAudioContext: AudioContext | null = null;
let speechBuffersPromise: Promise<AudioBuffer[]> | null = null;
let speechAudioPrimed = false;
let playbackVersion = 0;
let activeSources: AudioBufferSourceNode[] = [];
let activeGain: GainNode | null = null;

function getSpeechAudioContext() {
  if (typeof window === "undefined") {
    return null;
  }

  if (speechAudioContext) {
    return speechAudioContext;
  }

  const audioWindow = window as BrowserAudioWindow;
  const AudioContextConstructor = audioWindow.AudioContext ?? audioWindow.webkitAudioContext;
  if (!AudioContextConstructor) {
    return null;
  }

  speechAudioContext = new AudioContextConstructor();
  return speechAudioContext;
}

async function loadSpeechBuffers(context: AudioContext) {
  if (!speechBuffersPromise) {
    speechBuffersPromise = Promise.all(
      PET_SPEECH_AUDIO_URLS.map(async (url) => {
        const response = await fetch(url);
        const audioData = await response.arrayBuffer();
        return context.decodeAudioData(audioData);
      }),
    );
  }

  return speechBuffersPromise;
}

function stopSources(sources: AudioBufferSourceNode[]) {
  sources.forEach((source) => {
    try {
      source.stop();
    } catch {
      // Source may have already ended.
    }
    try {
      source.disconnect();
    } catch {
      // Source may have already disconnected.
    }
  });
}

function disconnectGain(gain: GainNode | null) {
  if (!gain) {
    return;
  }

  try {
    gain.disconnect();
  } catch {
    // Gain may have already disconnected.
  }
}

function pickRandomBuffer(buffers: AudioBuffer[], previousIndex: number) {
  if (buffers.length === 1) {
    return { buffer: buffers[0], index: 0 };
  }

  let index = Math.floor(Math.random() * buffers.length);
  while (index === previousIndex) {
    index = Math.floor(Math.random() * buffers.length);
  }

  return { buffer: buffers[index], index };
}

export async function primePetSpeechAudio() {
  const context = getSpeechAudioContext();
  if (!context) {
    return;
  }

  speechAudioPrimed = true;

  try {
    if (context.state === "suspended") {
      await context.resume();
    }
    void loadSpeechBuffers(context);
  } catch {
    // Audio can stay unavailable until a later user gesture.
  }
}

export function stopPetSpeechAudio() {
  playbackVersion += 1;
  stopSources(activeSources);
  activeSources = [];
  disconnectGain(activeGain);
  activeGain = null;
}

export function playPetSpeechAudioSequence(durationMs: number) {
  stopPetSpeechAudio();

  const version = playbackVersion + 1;
  playbackVersion = version;

  const localSources: AudioBufferSourceNode[] = [];
  let localGain: GainNode | null = null;
  let stopped = false;
  activeSources = localSources;

  function stop() {
    if (stopped) {
      return;
    }

    stopped = true;
    if (playbackVersion === version) {
      playbackVersion += 1;
    }
    stopSources(localSources);
    if (activeSources === localSources) {
      activeSources = [];
    }
    if (activeGain === localGain) {
      activeGain = null;
    }
    disconnectGain(localGain);
  }

  if (!speechAudioPrimed || durationMs <= 0) {
    return stop;
  }

  const context = getSpeechAudioContext();
  if (!context) {
    return stop;
  }

  void (async () => {
    try {
      if (context.state === "suspended") {
        await context.resume();
      }
      if (context.state !== "running") {
        return;
      }

      const buffers = await loadSpeechBuffers(context);
      if (stopped || playbackVersion !== version || buffers.length === 0) {
        return;
      }

      const startTime = context.currentTime + PET_SPEECH_START_OFFSET_SECONDS;
      const endTime = startTime + durationMs / 1000;
      const sequenceGain = context.createGain();
      localGain = sequenceGain;
      sequenceGain.gain.setValueAtTime(PET_SPEECH_GAIN, startTime);
      sequenceGain.connect(context.destination);
      activeGain = sequenceGain;

      let cursor = startTime;
      let previousIndex = -1;

      while (cursor < endTime) {
        const { buffer, index } = pickRandomBuffer(buffers, previousIndex);
        const source = context.createBufferSource();
        source.buffer = buffer;
        source.connect(sequenceGain);
        source.start(cursor);
        source.stop(Math.min(cursor + buffer.duration, endTime));
        localSources.push(source);
        previousIndex = index;
        cursor += PET_SPEECH_STEP_SECONDS + (Math.random() * 0.012 - 0.006);
      }

      window.setTimeout(stop, Math.ceil(durationMs) + 200);
    } catch {
      stop();
    }
  })();

  return stop;
}
