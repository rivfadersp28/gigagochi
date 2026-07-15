import { PetDashboard } from "@/components/PetDashboard";

type PetPageProps = {
  params: Promise<{ id: string }>;
};

export default async function PetPage({ params }: PetPageProps) {
  const { id } = await params;

  return <PetDashboard key={id} petId={id} />;
}
