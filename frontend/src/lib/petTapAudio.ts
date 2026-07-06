const PET_TAP_SOUND_SRC = "/sounds/pet-tap.wav";

let petTapAudio: HTMLAudioElement | null = null;

export function playPetTapSound() {
  if (typeof Audio === "undefined") {
    return;
  }

  try {
    if (!petTapAudio) {
      petTapAudio = new Audio(PET_TAP_SOUND_SRC);
      petTapAudio.preload = "auto";
    }

    petTapAudio.currentTime = 0;
    void petTapAudio.play().catch(() => undefined);
  } catch {
    // Audio feedback is optional and can be blocked by the client.
  }
}
