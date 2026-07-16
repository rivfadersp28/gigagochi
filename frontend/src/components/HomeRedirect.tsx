"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useLocalPetState } from "@/lib/useLocalPetState";

export function HomeRedirect() {
  const router = useRouter();
  const { pet, status } = useLocalPetState();

  useEffect(() => {
    if (status === "loading") {
      return;
    }
    router.replace(pet ? `/pet/${pet.petId}` : "/create");
  }, [pet, router, status]);

  return (
    <main
      className="tma-screen min-h-screen bg-black"
      aria-busy="true"
      aria-label="Открываем приложение"
    />
  );
}
