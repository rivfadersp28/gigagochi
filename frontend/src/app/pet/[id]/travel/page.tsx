import { InteractiveTravelScreen } from "@/components/InteractiveTravelScreen";

type InteractiveTravelPageProps = {
  params: Promise<{ id: string }>;
};

export default async function InteractiveTravelPage({ params }: InteractiveTravelPageProps) {
  const { id } = await params;
  return <InteractiveTravelScreen key={id} petId={id} />;
}
