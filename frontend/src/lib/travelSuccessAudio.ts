const TRAVEL_SUCCESS_SOUND_SRC = "/sounds/travel-success.wav";
const TRAVEL_SUCCESS_GAIN = 0.8;

type AudioWindow = Window & typeof globalThis & {
  webkitAudioContext?: typeof AudioContext;
};

let context: AudioContext | null = null;
let buffer: AudioBuffer | null = null;
let bufferPromise: Promise<AudioBuffer> | null = null;

function audioContext() {
  if (typeof window === "undefined") return null;
  if (context) return context;
  const audioWindow = window as AudioWindow;
  const Constructor = audioWindow.AudioContext ?? audioWindow.webkitAudioContext;
  context = Constructor ? new Constructor() : null;
  return context;
}

function loadBuffer(activeContext: AudioContext) {
  if (buffer) return Promise.resolve(buffer);
  if (!bufferPromise) {
    bufferPromise = fetch(TRAVEL_SUCCESS_SOUND_SRC)
      .then((response) => {
        if (!response.ok) throw new Error(`Success sound failed: ${response.status}`);
        return response.arrayBuffer();
      })
      .then((data) => activeContext.decodeAudioData(data))
      .then((decoded) => {
        buffer = decoded;
        return decoded;
      })
      .catch((error) => {
        bufferPromise = null;
        throw error;
      });
  }
  return bufferPromise;
}

export function primeInteractiveTravelSuccessSound() {
  const activeContext = audioContext();
  if (!activeContext) return;
  void activeContext.resume().catch(() => undefined);
  void loadBuffer(activeContext).catch(() => undefined);
}

export async function playInteractiveTravelSuccessSound() {
  const activeContext = audioContext();
  if (!activeContext) return false;
  try {
    await activeContext.resume();
    const decoded = await loadBuffer(activeContext);
    const source = activeContext.createBufferSource();
    const gain = activeContext.createGain();
    source.buffer = decoded;
    gain.gain.value = TRAVEL_SUCCESS_GAIN;
    source.connect(gain);
    gain.connect(activeContext.destination);
    source.start();
    return true;
  } catch {
    return false;
  }
}
