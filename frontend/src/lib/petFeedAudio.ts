const PET_FEED_SOUND_SRCS = [
  "/sounds/feed-bite-1.wav",
  "/sounds/feed-bite-2.wav",
  "/sounds/feed-bite-3.wav",
] as const;

const PET_FEED_GAIN = 0.78;

type BrowserAudioWindow = Window &
  typeof globalThis & {
    webkitAudioContext?: typeof AudioContext;
  };

let petFeedPreloadAudios: HTMLAudioElement[] | null = null;
let petFeedAudioContext: AudioContext | null = null;
let petFeedBuffers: AudioBuffer[] | null = null;
let petFeedBuffersPromise: Promise<AudioBuffer[]> | null = null;
let nextFeedSoundIndex = 0;
const activePetFeedSources = new Set<{
  source: AudioBufferSourceNode;
  gain: GainNode;
}>();
const activeFallbackPetFeedAudios = new Set<HTMLAudioElement>();

function getPetFeedAudioContext() {
  if (typeof window === "undefined") {
    return null;
  }

  if (petFeedAudioContext) {
    return petFeedAudioContext;
  }

  const audioWindow = window as BrowserAudioWindow;
  const AudioContextConstructor = audioWindow.AudioContext ?? audioWindow.webkitAudioContext;
  if (!AudioContextConstructor) {
    return null;
  }

  petFeedAudioContext = new AudioContextConstructor();
  return petFeedAudioContext;
}

function getFallbackAudioPreloaders() {
  if (typeof Audio === "undefined") {
    return [];
  }

  if (!petFeedPreloadAudios) {
    petFeedPreloadAudios = PET_FEED_SOUND_SRCS.map((src) => {
      const audio = new Audio(src);
      audio.preload = "auto";
      audio.volume = PET_FEED_GAIN;
      return audio;
    });
  }

  return petFeedPreloadAudios;
}

function createFallbackAudio(soundIndex: number) {
  if (typeof Audio === "undefined") {
    return null;
  }

  const audio = new Audio(PET_FEED_SOUND_SRCS[soundIndex]);
  audio.preload = "auto";
  audio.volume = PET_FEED_GAIN;
  return audio;
}

function getNextFeedSoundIndex() {
  const soundIndex = nextFeedSoundIndex;
  nextFeedSoundIndex = (nextFeedSoundIndex + 1) % PET_FEED_SOUND_SRCS.length;
  return soundIndex;
}

async function loadPetFeedBuffers(context: AudioContext) {
  if (petFeedBuffers) {
    return petFeedBuffers;
  }

  if (!petFeedBuffersPromise) {
    petFeedBuffersPromise = Promise.all(
      PET_FEED_SOUND_SRCS.map(async (src) => {
        const response = await fetch(src);
        if (!response.ok) {
          throw new Error(`Failed to load pet feed sound: ${response.status}`);
        }

        const audioData = await response.arrayBuffer();
        return context.decodeAudioData(audioData);
      }),
    )
      .then((buffers) => {
        petFeedBuffers = buffers;
        return buffers;
      })
      .catch((error) => {
        petFeedBuffersPromise = null;
        throw error;
      });
  }

  return petFeedBuffersPromise;
}

function disconnectPetFeedSource(source: AudioBufferSourceNode, gain: GainNode) {
  try {
    source.disconnect();
  } catch {
    // Source may have already disconnected.
  }

  try {
    gain.disconnect();
  } catch {
    // Gain may have already disconnected.
  }
}

function playPetFeedBuffer(context: AudioContext, buffer: AudioBuffer) {
  const source = context.createBufferSource();
  const gain = context.createGain();
  const activeSource = { source, gain };
  let cleanedUp = false;

  const cleanup = () => {
    if (cleanedUp) {
      return;
    }

    cleanedUp = true;
    activePetFeedSources.delete(activeSource);
    disconnectPetFeedSource(source, gain);
  };

  source.buffer = buffer;
  gain.gain.value = PET_FEED_GAIN;
  source.connect(gain);
  gain.connect(context.destination);
  activePetFeedSources.add(activeSource);

  source.onended = cleanup;
  try {
    source.start();
  } catch (error) {
    cleanup();
    throw error;
  }
}

async function playFallbackPetFeedSound(soundIndex: number) {
  const audio = createFallbackAudio(soundIndex);
  if (!audio) {
    return false;
  }

  const cleanup = () => {
    activeFallbackPetFeedAudios.delete(audio);
    audio.removeEventListener("ended", cleanup);
    audio.removeEventListener("error", cleanup);
  };

  activeFallbackPetFeedAudios.add(audio);
  audio.addEventListener("ended", cleanup, { once: true });
  audio.addEventListener("error", cleanup, { once: true });

  try {
    await audio.play();
    return true;
  } catch {
    // Audio feedback is optional and can be blocked by the client.
    cleanup();
    return false;
  }
}

async function resumePetFeedAudioContext(context: AudioContext) {
  if (context.state === "suspended") {
    await context.resume();
  }

  return context.state === "running";
}

export function primePetFeedSound() {
  try {
    getFallbackAudioPreloaders().forEach((audio) => audio.load());

    const context = getPetFeedAudioContext();
    if (context) {
      void loadPetFeedBuffers(context).catch(() => undefined);
    }
  } catch {
    // Audio can stay unavailable until a later user gesture.
  }
}

export async function playPetFeedSound() {
  const soundIndex = getNextFeedSoundIndex();
  const context = getPetFeedAudioContext();
  if (!context) {
    return playFallbackPetFeedSound(soundIndex);
  }

  try {
    if (!(await resumePetFeedAudioContext(context))) {
      return playFallbackPetFeedSound(soundIndex);
    }

    if (!petFeedBuffers) {
      void loadPetFeedBuffers(context).catch(() => undefined);
      return playFallbackPetFeedSound(soundIndex);
    }

    playPetFeedBuffer(context, petFeedBuffers[soundIndex]);
    return true;
  } catch {
    // Audio feedback is optional and can be blocked by the client.
    return playFallbackPetFeedSound(soundIndex);
  }
}
