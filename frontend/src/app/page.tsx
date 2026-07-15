import type { Metadata } from "next";

import { CreatePetForm } from "@/components/CreatePetForm";
import { APP_DESCRIPTION, APP_TITLE } from "@/lib/appMetadata";

export const metadata: Metadata = {
  alternates: {
    canonical: "/",
  },
  openGraph: {
    type: "website",
    url: "/",
    siteName: APP_TITLE,
    title: APP_TITLE,
    description: APP_DESCRIPTION,
    locale: "ru_RU",
  },
  twitter: {
    card: "summary",
    title: APP_TITLE,
    description: APP_DESCRIPTION,
  },
};

export default function Home() {
  return <CreatePetForm />;
}
