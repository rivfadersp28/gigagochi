import type { Metadata } from "next";

import { CreatePetForm } from "@/components/CreatePetForm";
import { APP_DESCRIPTION, APP_TITLE } from "@/lib/appMetadata";

export const metadata: Metadata = {
  title: `Создание персонажа — ${APP_TITLE}`,
  description: APP_DESCRIPTION,
  alternates: {
    canonical: "/create",
  },
};

export default function CreatePage() {
  return <CreatePetForm redirectExistingPet={false} />;
}
