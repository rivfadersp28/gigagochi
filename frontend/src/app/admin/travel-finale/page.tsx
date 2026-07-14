import { notFound } from "next/navigation";

import { TravelFinaleLab } from "@/components/admin/TravelFinaleLab";

export default function TravelFinalePage() {
  if (process.env.NODE_ENV === "production") {
    notFound();
  }
  return <TravelFinaleLab />;
}
