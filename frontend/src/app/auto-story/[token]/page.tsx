import { AutomaticStoryScreen } from "@/components/AutomaticStoryScreen";

type AutomaticStoryPageProps = {
  params: Promise<{ token: string }>;
};

export default async function AutomaticStoryPage({ params }: AutomaticStoryPageProps) {
  const { token } = await params;
  return <AutomaticStoryScreen token={token} />;
}
