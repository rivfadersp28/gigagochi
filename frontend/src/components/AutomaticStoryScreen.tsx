"use client";

import { InteractiveTravelScreen } from "@/components/InteractiveTravelScreen";
import { useLocalPetState } from "@/lib/useLocalPetState";

export function AutomaticStoryScreen({ token }: { token: string }) {
  const localPet = useLocalPetState();

  if (localPet.status === "loading") {
    return null;
  }
  if (!localPet.pet) {
    return (
      <main style={{ minHeight: "100svh", background: "#000", color: "#fff" }}>
        Персонаж не найден.
      </main>
    );
  }

  return (
    <InteractiveTravelScreen
      key={`${localPet.pet.petId}:${token}`}
      petId={localPet.pet.petId}
      automaticStoryToken={token}
    />
  );
}
