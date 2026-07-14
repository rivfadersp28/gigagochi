const PET_TAP_SOUND_SRC = "/sounds/pet-tap.wav";
const PET_TAP_GAIN = 0.9;

type BrowserAudioWindow = Window &
  typeof globalThis & {
    webkitAudioContext?: typeof AudioContext;
  };

let petTapPreloadAudio: HTMLAudioElement | null = null;
let petTapAudioContext: AudioContext | null = null;
let petTapBuffer: AudioBuffer | null = null;
let petTapBufferPromise: Promise<AudioBuffer> | null = null;
const activePetTapSources = new Set<{
  source: AudioBufferSourceNode;
  gain: GainNode;
}>();
const activeFallbackPetTapAudios = new Set<HTMLAudioElement>();

function getPetTapAudioContext() {
  if (typeof window === "undefined") {
    return null;
  }

  if (petTapAudioContext) {
    return petTapAudioContext;
  }

  const audioWindow = window as BrowserAudioWindow;
  const AudioContextConstructor = audioWindow.AudioContext ?? audioWindow.webkitAudioContext;
  if (!AudioContextConstructor) {
    return null;
  }

  petTapAudioContext = new AudioContextConstructor();
  return petTapAudioContext;
}

function getFallbackAudioPreloader() {
  if (typeof Audio === "undefined") {
    return null;
  }

  if (!petTapPreloadAudio) {
    petTapPreloadAudio = new Audio(PET_TAP_SOUND_SRC);
    petTapPreloadAudio.preload = "auto";
  }

  return petTapPreloadAudio;
}

function createFallbackAudio() {
  if (typeof Audio === "undefined") {
    return null;
  }

  const audio = new Audio(PET_TAP_SOUND_SRC);
  audio.preload = "auto";
  return audio;
}

async function loadPetTapBuffer(context: AudioContext) {
  if (petTapBuffer) {
    return petTapBuffer;
  }

  if (!petTapBufferPromise) {
    petTapBufferPromise = fetch(PET_TAP_SOUND_SRC)
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Failed to load pet tap sound: ${response.status}`);
        }
        return response.arrayBuffer();
      })
      .then((audioData) => context.decodeAudioData(audioData))
      .then((buffer) => {
        petTapBuffer = buffer;
        return buffer;
      })
      .catch((error) => {
        petTapBufferPromise = null;
        throw error;
      });
  }

  return petTapBufferPromise;
}

function disconnectPetTapSource(source: AudioBufferSourceNode, gain: GainNode) {
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

function playPetTapBuffer(context: AudioContext, buffer: AudioBuffer) {
  const source = context.createBufferSource();
  const gain = context.createGain();
  const activeSource = { source, gain };
  let cleanedUp = false;

  const cleanup = () => {
    if (cleanedUp) {
      return;
    }

    cleanedUp = true;
    activePetTapSources.delete(activeSource);
    disconnectPetTapSource(source, gain);
  };

  source.buffer = buffer;
  gain.gain.value = PET_TAP_GAIN;
  source.connect(gain);
  gain.connect(context.destination);
  activePetTapSources.add(activeSource);

  source.onended = cleanup;
  try {
    source.start();
  } catch (error) {
    cleanup();
    throw error;
  }
}

async function playFallbackPetTapSound() {
  const audio = createFallbackAudio();
  if (!audio) {
    return false;
  }

  const cleanup = () => {
    activeFallbackPetTapAudios.delete(audio);
    audio.removeEventListener("ended", cleanup);
    audio.removeEventListener("error", cleanup);
  };

  activeFallbackPetTapAudios.add(audio);
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

async function resumePetTapAudioContext(context: AudioContext) {
  if (context.state === "suspended") {
    await context.resume();
  }

  return context.state === "running";
}

export function primePetTapSound() {
  try {
    getFallbackAudioPreloader()?.load();

    const context = getPetTapAudioContext();
    if (context) {
      void loadPetTapBuffer(context).catch(() => undefined);
    }
  } catch {
    // Audio can stay unavailable until a later user gesture.
  }
}

export async function playPetTapSound() {
  const context = getPetTapAudioContext();
  if (!context) {
    return playFallbackPetTapSound();
  }

  try {
    if (!(await resumePetTapAudioContext(context))) {
      return playFallbackPetTapSound();
    }

    if (!petTapBuffer) {
      void loadPetTapBuffer(context).catch(() => undefined);
      return playFallbackPetTapSound();
    }

    playPetTapBuffer(context, petTapBuffer);
    return true;
  } catch {
    // Audio feedback is optional and can be blocked by the client.
    return playFallbackPetTapSound();
  }
}

