import { TravelVideoPrototypeScreen } from "@/components/TravelVideoPrototypeScreen";

type TravelVideoPrototypePageProps = {
  params: Promise<{ id: string }>;
};

export default async function TravelVideoPrototypePage({ params }: TravelVideoPrototypePageProps) {
  const { id } = await params;
  return <TravelVideoPrototypeScreen key={id} petId={id} />;
}
