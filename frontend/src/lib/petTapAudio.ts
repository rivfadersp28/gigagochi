const PET_TAP_SOUND_SRC = "/sounds/pet-tap.wav";
const PET_TAP_GAIN = 0.9;

type BrowserAudioWindow = Window &
  typeof globalThis & {
    webkitAudioContext?: typeof AudioContext;
  };

let petTapAudio: HTMLAudioElement | null = null;
let petTapAudioContext: AudioContext | null = null;
let petTapBuffer: AudioBuffer | null = null;
let petTapBufferPromise: Promise<AudioBuffer> | null = null;
let activePetTapSource: AudioBufferSourceNode | null = null;
let activePetTapGain: GainNode | null = null;

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

function getFallbackAudio() {
  if (typeof Audio === "undefined") {
    return null;
  }

  if (!petTapAudio) {
    petTapAudio = new Audio(PET_TAP_SOUND_SRC);
    petTapAudio.preload = "auto";
  }

  return petTapAudio;
}

async function loadPetTapBuffer(context: AudioContext) {
  if (petTapBuffer) {
    return petTapBuffer;
  }

  if (!petTapBufferPromise) {
    petTapBufferPromise = fetch(PET_TAP_SOUND_SRC)
      .then((response) => response.arrayBuffer())
      .then((audioData) => context.decodeAudioData(audioData))
      .then((buffer) => {
        petTapBuffer = buffer;
        return buffer;
      });
  }

  return petTapBufferPromise;
}

function stopActivePetTapSource() {
  try {
    activePetTapSource?.stop();
  } catch {
    // Source may have already ended.
  }

  try {
    activePetTapSource?.disconnect();
  } catch {
    // Source may have already disconnected.
  }

  try {
    activePetTapGain?.disconnect();
  } catch {
    // Gain may have already disconnected.
  }

  activePetTapSource = null;
  activePetTapGain = null;
}

function playPetTapBuffer(context: AudioContext, buffer: AudioBuffer) {
  stopActivePetTapSource();

  const source = context.createBufferSource();
  const gain = context.createGain();

  source.buffer = buffer;
  gain.gain.value = PET_TAP_GAIN;
  source.connect(gain);
  gain.connect(context.destination);
  source.start();

  activePetTapSource = source;
  activePetTapGain = gain;

  source.onended = () => {
    if (activePetTapSource === source) {
      stopActivePetTapSource();
    }
  };
}

function playFallbackPetTapSound() {
  try {
    const audio = getFallbackAudio();
    if (!audio) {
      return;
    }

    audio.currentTime = 0;
    void audio.play().catch(() => undefined);
  } catch {
    // Audio feedback is optional and can be blocked by the client.
  }
}

export function primePetTapSound() {
  try {
    getFallbackAudio()?.load();

    const context = getPetTapAudioContext();
    if (context) {
      void loadPetTapBuffer(context).catch(() => undefined);
    }
  } catch {
    // Audio can stay unavailable until a later user gesture.
  }
}

export function playPetTapSound() {
  const context = getPetTapAudioContext();
  if (!context) {
    playFallbackPetTapSound();
    return;
  }

  try {
    if (petTapBuffer) {
      if (context.state === "suspended") {
        void context.resume().catch(() => undefined);
      }
      playPetTapBuffer(context, petTapBuffer);
      return;
    }

    void loadPetTapBuffer(context).catch(() => undefined);
    playFallbackPetTapSound();
  } catch {
    // Audio feedback is optional and can be blocked by the client.
  }
}
